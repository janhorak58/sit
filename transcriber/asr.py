"""Speech-to-text backend selection.

The work Spark endpoint is OpenAI-compatible. A fast probe decides whether to
send the WAV there. Any probe or transcription failure falls back to the local
faster-whisper model; an unavailable work endpoint never blocks transcription.
"""

from dataclasses import dataclass

import httpx

from .config import SPARK_WHISPER_MODEL, SPARK_WHISPER_URL
from .models import get_whisper_model


@dataclass
class Segment:
    start: float
    end: float
    text: str


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


def _remote_transcribe(wav_path, language):
    data = {"model": SPARK_WHISPER_MODEL, "response_format": "verbose_json"}
    if language:
        data["language"] = language
    with wav_path.open("rb") as audio:
        response = httpx.post(
            SPARK_WHISPER_URL,
            data=data,
            files={"file": (wav_path.name, audio, "audio/wav")},
            timeout=3600.0,
        )
    response.raise_for_status()
    body = response.json()
    raw_segments = body.get("segments") or []
    if raw_segments:
        segments = [
            Segment(float(s.get("start", 0)), float(s.get("end", 0)), s.get("text", ""))
            for s in raw_segments
        ]
    else:
        segments = [Segment(0, float(body.get("duration", 0)), body.get("text", ""))]
    duration = float(body.get("duration") or max((s.end for s in segments), default=0))
    return Transcript(segments, duration, "spark")


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
            return _remote_transcribe(wav_path, language)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return _local_transcribe(wav_path, language, on_segment)
