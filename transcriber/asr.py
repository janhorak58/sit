"""Speech-to-text backend selection.

The work Spark endpoint is OpenAI-compatible. A fast probe decides whether to
send the audio there, as 15-minute 32 kbps mp3 chunks (the endpoint caps upload
size). Any probe or transcription failure falls back to the local
faster-whisper model; an unavailable work endpoint never blocks transcription.
"""

import dataclasses
from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import tempfile

import httpx

from .config import SPARK_WHISPER_MODEL, SPARK_WHISPER_URL
from .models import get_whisper_model

log = logging.getLogger(__name__)

# Why the remote engine was skipped on the last run, kept for the settings panel.
_last_remote_error = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class Transcript:
    segments: list[Segment]
    duration: float
    backend: str


def remote_status():
    """Return public backend state without leaking internal endpoint details."""
    if not SPARK_WHISPER_URL:
        return {"available": False, "backend": "local", "label": "Lokální model"}
    try:
        base = SPARK_WHISPER_URL.removesuffix("/v1/audio/transcriptions").rstrip("/")
        response = httpx.get(f"{base}/v1/models", timeout=2.0)
        if response.is_success:
            return {"available": True, "backend": "spark", "label": "Pracovní Spark"}
    except httpx.HTTPError:
        pass
    return {"available": False, "backend": "local", "label": "Lokální model"}


# 15 min of 32 kbps mono mp3 is ~3.5 MB, comfortably under the remote's
# per-file size limit whatever a meeting's real length turns out to be.
CHUNK_SECONDS = 900


def _mp3_chunks(wav_path, out_dir):
    """Split+compress the wav into upload-sized mp3 slices, in order."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wav_path), "-vn", "-ac", "1",
            "-c:a", "libmp3lame", "-b:a", "32k",
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1",
            str(Path(out_dir) / "part%04d.mp3"),
        ],
        check=True, capture_output=True, text=True,
    )
    return sorted(Path(out_dir).glob("part*.mp3"))


def _post_audio(path, language):
    """One chunk -> (segments, duration), both relative to the chunk's own start."""
    data = {"model": SPARK_WHISPER_MODEL, "response_format": "verbose_json"}
    if language:
        data["language"] = language
    with path.open("rb") as audio:
        response = httpx.post(
            SPARK_WHISPER_URL,
            data=data,
            files={"file": (path.name, audio, "audio/mpeg")},
            timeout=3600.0,
        )
    response.raise_for_status()
    body = response.json()
    # The endpoint reports some failures (missing audio support, bad model) as
    # an error body under HTTP 200; without this an empty transcript would win.
    if body.get("error"):
        raise ValueError(str(body["error"]))
    raw_segments = body.get("segments") or []
    if raw_segments:
        segments = [
            Segment(float(s.get("start", 0)), float(s.get("end", 0)), s.get("text", ""))
            for s in raw_segments
        ]
    else:
        segments = [Segment(0, float(body.get("duration", 0)), body.get("text", ""))]
    duration = float(body.get("duration") or max((s.end for s in segments), default=0))
    return segments, duration


def _remote_transcribe(wav_path, language, on_segment):
    """Chunked remote transcription; segment times are stitched back together."""
    segments, offset = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        chunks = _mp3_chunks(wav_path, tmp)
        total = len(chunks) * CHUNK_SECONDS  # only drives the progress bar
        for chunk in chunks:
            part, duration = _post_audio(chunk, language)
            for seg in part:
                shifted = dataclasses.replace(seg, start=seg.start + offset, end=seg.end + offset)
                segments.append(shifted)
                on_segment(shifted, total)
            offset += duration or CHUNK_SECONDS
    return Transcript(segments, offset, "spark")


def _local_transcribe(wav_path, language, on_segment):
    kwargs = {"language": language} if language else {}
    generated, info = get_whisper_model().transcribe(str(wav_path), **kwargs)
    segments = []
    for raw in generated:
        seg = Segment(raw.start, raw.end, raw.text)
        segments.append(seg)
        on_segment(seg, info.duration)
    return Transcript(segments, info.duration, "local")


def transcribe(wav_path, language, on_segment=lambda segment, duration: None):
    """Try Spark first when healthy; otherwise execute faster-whisper locally."""
    if remote_status()["available"]:
        try:
            return _remote_transcribe(wav_path, language, on_segment)
        except (httpx.HTTPError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
            global _last_remote_error
            detail = getattr(getattr(exc, "response", None), "text", "")
            _last_remote_error = f"{exc} {detail}".strip()[:1000]
            log.warning("Spark transcription failed, falling back to local: %s", _last_remote_error)
    return _local_transcribe(wav_path, language, on_segment)


def diagnose():
    """Why the remote engine is (not) working: probe, one real upload, last error."""
    report = {
        "url": SPARK_WHISPER_URL,
        "model": SPARK_WHISPER_MODEL,
        "probe": remote_status(),
        "last_error": _last_remote_error,
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k", str(probe)],
                check=True, capture_output=True,
            )
            segments, _ = _post_audio(probe, "cs")
        report["upload"] = {"ok": True, "detail": f"1s testovací audio přijato ({len(segments)} segmentů)"}
    except Exception as exc:  # any failure is the answer the user came for
        detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
        report["upload"] = {"ok": False, "detail": str(detail)[:1000]}
    return report
