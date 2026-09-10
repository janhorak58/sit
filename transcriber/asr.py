"""Speech-to-text backend selection.

The work Spark endpoint is OpenAI-compatible. A fast probe decides whether to
send the audio there, as 15-minute 32 kbps mp3 chunks (the endpoint caps upload
size). Any probe or transcription failure falls back to the local Canary
model; an unavailable work endpoint never blocks transcription.
"""

import dataclasses
from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import tempfile
import wave

import httpx

from .config import LOCAL_ASR_DEFAULT_LANGUAGE
from .connections import get_connection
from .models import get_local_asr

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
    endpoint = get_connection("transcription")["endpoint"]
    if not endpoint:
        return {"available": False, "backend": "local", "label": "Local model"}
    try:
        base = endpoint.removesuffix("/v1/audio/transcriptions").rstrip("/")
        response = httpx.get(f"{base}/v1/models", timeout=2.0)
        if response.is_success:
            return {"available": True, "backend": "spark", "label": "Working Spark"}
    except (httpx.HTTPError, OSError):
        pass
    return {"available": False, "backend": "local", "label": "Local model"}


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


def _post_audio(path, language, timeout=3600.0, mime="audio/mpeg"):
    """One chunk -> (segments, duration), both relative to the chunk's own start.

    ``mime`` matches whatever bytes ``path`` actually holds: mp3 for batch
    chunks, wav for a live-draft window posted straight through with no
    ffmpeg transcode.
    """
    connection = get_connection("transcription")
    data = {"model": connection["model"], "response_format": "verbose_json"}
    if language:
        data["language"] = language
    with path.open("rb") as audio:
        response = httpx.post(
            connection["endpoint"],
            data=data,
            files={"file": (path.name, audio, mime)},
            timeout=timeout,
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


def _remote_transcribe(wav_path, language, on_segment, timeout=3600.0):
    """Chunked remote transcription; segment times are stitched back together."""
    segments, offset = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        chunks = _mp3_chunks(wav_path, tmp)
        total = len(chunks) * CHUNK_SECONDS  # only drives the progress bar
        for chunk in chunks:
            part, duration = _post_audio(chunk, language, timeout=timeout)
            for seg in part:
                shifted = dataclasses.replace(seg, start=seg.start + offset, end=seg.end + offset)
                segments.append(shifted)
                on_segment(shifted, total)
            offset += duration or CHUNK_SECONDS
    return Transcript(segments, offset, "spark")


def _wav_duration(wav_path):
    """Audio length straight from the PCM header; no decode, no ffprobe."""
    with wave.open(str(wav_path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _local_transcribe(wav_path, language, on_segment):
    """Canary over VAD-cut speech windows; silence never reaches the model.

    The language is always passed. Left to auto-detection, per-window language
    identification makes Czech recordings drift into Slovak and English.
    """
    duration = _wav_duration(wav_path)
    segments = []
    recognized = get_local_asr().recognize(
        str(wav_path),
        language=language or LOCAL_ASR_DEFAULT_LANGUAGE,
        pnc=True,
    )
    for raw in recognized:
        if not raw.text.strip():
            continue
        seg = Segment(raw.start, raw.end, raw.text)
        segments.append(seg)
        on_segment(seg, duration)
    return Transcript(segments, duration, "local")


def transcribe(wav_path, language, on_segment=lambda segment, duration: None, timeout=3600.0):
    """Try Spark first when healthy; otherwise execute Canary locally.

    ``timeout`` bounds only the remote HTTP call (default matches the batch
    pipeline's original unbounded-feeling budget); local inference has no
    network timeout to apply. Callers doing short live-draft passes pass a
    much smaller value so a stalled remote never blocks the next window.
    """
    if remote_status()["available"]:
        try:
            return _remote_transcribe(wav_path, language, on_segment, timeout=timeout)
        except (httpx.HTTPError, ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
            global _last_remote_error
            detail = getattr(getattr(exc, "response", None), "text", "")
            _last_remote_error = f"{exc} {detail}".strip()[:1000]
            log.warning("Spark transcription failed, falling back to local: %s", _last_remote_error)
    return _local_transcribe(wav_path, language, on_segment)


def transcribe_live(wav_path, language, timeout, remote_available):
    """Transcribe a short live WAV directly, using the session's backend probe."""
    if remote_available:
        try:
            segments, duration = _post_audio(wav_path, language, timeout=timeout, mime="audio/wav")
            return Transcript(segments, duration, "spark")
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            global _last_remote_error
            detail = getattr(getattr(exc, "response", None), "text", "")
            _last_remote_error = f"{exc} {detail}".strip()[:1000]
            log.warning("Live draft window remote transcription failed, falling back to local: %s", _last_remote_error)
    return _local_transcribe(wav_path, language, lambda segment, duration: None)


def diagnose():
    """Why the remote engine is (not) working: probe, one real upload, last error."""
    connection = get_connection("transcription")
    report = {
        "url": connection["endpoint"],
        "model": connection["model"],
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
        report["upload"] = {"ok": True, "detail": f"1s test audio accepted ({len(segments)} segments)"}
    except Exception as exc:  # any failure is the answer the user came for
        detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
        report["upload"] = {"ok": False, "detail": str(detail)[:1000]}
    return report
