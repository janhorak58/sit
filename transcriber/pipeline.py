"""Transcription pipeline: whisper -> optional diarization -> meeting.json + text file."""

import dataclasses
from datetime import datetime
import json
import logging
import httpx
import subprocess
import threading

from .asr import Segment, transcribe
from .config import (
    DIARIZE_MODEL,
    SPARK_DIARIZER_URL,
    SPARK_WHISPER_MODEL,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from .library import meeting_json_path_for
from .models import get_diarize_pipeline
from .paths import rel_to_data
from .progress import progress

log = logging.getLogger(__name__)
_last_remote_diarization_error = None


@dataclasses.dataclass(frozen=True)
class DiarizationResult:
    segments: list[Segment]
    backend: str
    model: str


def transcribe_segments(wav_path, language, cap):
    """Transcribe remotely when available, with transparent local fallback."""
    def on_segment(seg, duration):
        pct = min(cap - 1, int(seg.end / max(duration, 0.01) * cap))
        progress.update(percent=pct, message=f"Přepisuji audio... {pct}%")

    result = transcribe(wav_path, language, on_segment)
    progress.update(
        backend=result.backend,
        message="Přepisuji na pracovním Sparku..." if result.backend == "spark"
        else "Přepisuji lokálně...",
    )
    return result


def speaker_at(turns, t):
    """Speaker label whose turn covers instant ``t`` most."""
    best, best_overlap = None, 0.0
    for turn in turns:
        overlap = min(float(turn["end"]), t + 0.01) - max(float(turn["start"]), t)
        if overlap > best_overlap:
            best_overlap, best = overlap, turn["speaker"]
    return best or "SPEAKER_00"


DIARIZE_SAMPLE_RATE = 16000


def load_waveform(path):
    """Decode `path` into the in-memory waveform pyannote expects.

    pyannote 4 reads files through torchcodec, whose native libraries are
    easily mismatched with the host FFmpeg. ffmpeg(1) is already a hard
    dependency here, so decode with it and hand over samples directly.
    """
    import numpy as np
    import torch

    raw = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", str(DIARIZE_SAMPLE_RATE), "-f", "f32le", "-",
        ],
        check=True, capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    return {
        "waveform": torch.from_numpy(samples).unsqueeze(0),
        "sample_rate": DIARIZE_SAMPLE_RATE,
    }


def remote_diarizer_status():
    """Probe the configured Spark diarizer without loading the local model."""
    if not SPARK_DIARIZER_URL:
        return {"available": False, "backend": "local", "last_error": None}
    health_url = SPARK_DIARIZER_URL.removesuffix("/v1/audio/diarizations").rstrip("/") + "/health"
    try:
        response = httpx.get(health_url, timeout=2.0)
        response.raise_for_status()
        body = response.json()
        return {
            "available": bool(body.get("ok", True)),
            "backend": "spark",
            "model": body.get("model"),
            "device": body.get("device"),
            "last_error": _last_remote_diarization_error,
        }
    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        return {
            "available": False,
            "backend": "local",
            "last_error": str(exc)[:1000] or _last_remote_diarization_error,
        }


def _remote_speaker_turns(wav_path, num_speakers):
    data = {"num_speakers": str(num_speakers)} if num_speakers else {}
    with wav_path.open("rb") as audio:
        response = httpx.post(
            SPARK_DIARIZER_URL,
            data=data,
            files={"file": (wav_path.name, audio, "audio/wav")},
            timeout=3600.0,
        )
    response.raise_for_status()
    body = response.json()
    if body.get("error") or body.get("detail"):
        raise ValueError(str(body.get("error") or body.get("detail")))
    turns = body.get("segments")
    if not turns:
        raise ValueError("Spark diarizer nevrátil žádné segmenty.")
    return turns, body.get("model") or DIARIZE_MODEL


def _local_speaker_turns(wav_path, num_speakers):
    diar_kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    audio = load_waveform(wav_path)
    annotation = get_diarize_pipeline()(audio, **diar_kwargs).speaker_diarization
    return [
        {"start": float(turn.start), "end": float(turn.end), "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def label_speakers(wav_path, segments, num_speakers):
    """Attach labels using Spark first, with the local model as fallback."""
    global _last_remote_diarization_error
    turns, backend, model = None, "local", DIARIZE_MODEL
    if SPARK_DIARIZER_URL:
        progress.update(stage="diarizing", percent=75, message="Rozpoznávám mluvčí na Sparku...")
        try:
            turns, model = _remote_speaker_turns(wav_path, num_speakers)
            backend = "spark"
            _last_remote_diarization_error = None
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            _last_remote_diarization_error = f"{exc} {detail}".strip()[:1000]
            log.warning(
                "Spark diarization failed, falling back to local: %s",
                _last_remote_diarization_error,
            )
    if turns is None:
        progress.update(stage="diarizing", percent=75, message="Rozpoznávám mluvčí lokálně...")
        turns = _local_speaker_turns(wav_path, num_speakers)

    progress.update(stage="merging", percent=95, message="Skládám výstup...")
    labeled = [
        dataclasses.replace(seg, speaker=speaker_at(turns, (seg.start + seg.end) / 2))
        for seg in segments
    ]
    return DiarizationResult(labeled, backend, model)


def render_text(segments):
    """Flatten segments into the plain-text transcript (unchanged wire format)."""
    if not any(seg.speaker for seg in segments):
        return "\n".join(seg.text.strip() for seg in segments).strip()
    lines, last_speaker = [], None
    for seg in segments:
        if seg.speaker != last_speaker:
            lines.append(f"\n[{seg.speaker}]")
            last_speaker = seg.speaker
        lines.append(seg.text.strip())
    return "\n".join(lines).strip()


def rerun_diarization(wav_path, txt_path, num_speakers):
    """Re-label existing ASR segments without transcribing the audio again."""
    try:
        meeting_path = meeting_json_path_for(txt_path)
        data = json.loads(meeting_path.read_text())
        segments = [
            Segment(
                start=float(item["start"]),
                end=float(item["end"]),
                text=item.get("text", ""),
                speaker=item.get("speaker"),
            )
            for item in data.get("segments") or []
        ]
        diarization = label_speakers(wav_path, segments, num_speakers)
        segments = diarization.segments
        text = render_text(segments)
        txt_path.write_text(text)
        data["num_speakers"] = num_speakers
        data["diarization"] = {
            "backend": diarization.backend,
            "where": "Pracovní Spark" if diarization.backend == "spark" else "Lokálně",
            "model": diarization.model,
            "applied": True,
            "note": None,
        }
        data["segments"] = [dataclasses.asdict(segment) for segment in segments]
        meeting_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        progress.finish(
            stage="done",
            percent=100,
            message=(
                "Mluvčí byli rozpoznáni na Sparku."
                if diarization.backend == "spark"
                else "Mluvčí byli rozpoznáni lokálně."
            ),
            warning=None,
            text=text,
            saved_path=rel_to_data(txt_path),
            backend=(data.get("asr") or {}).get("backend") or data.get("backend"),
            diarization_backend=diarization.backend,
        )
    except Exception as exc:
        progress.finish(stage="error", message=str(exc), error=str(exc))


def start_diarization(wav_path, txt_path, num_speakers):
    """Start diarization of an existing transcript, returning False if busy."""
    if not progress.begin(
        stage="diarizing",
        percent=70,
        message="Rozpoznávám mluvčí...",
        source_path=rel_to_data(wav_path),
        operation="diarize",
        saved_path=rel_to_data(txt_path),
    ):
        return False
    threading.Thread(
        target=rerun_diarization,
        args=(wav_path, txt_path, num_speakers),
        daemon=True,
    ).start()
    return True


def run_pipeline(wav_path, txt_path, language, num_speakers):
    """Blocking end-to-end run; all state is reported through ``progress``."""
    try:
        single = num_speakers == 1
        result = transcribe_segments(wav_path, language, cap=40 if single else 70)
        warning = None
        diarization_backend = None
        diarization_model = None
        if single:
            segments = result.segments
        else:
            try:
                diarization = label_speakers(wav_path, result.segments, num_speakers)
                segments = diarization.segments
                diarization_backend = diarization.backend
                diarization_model = diarization.model
            except Exception as exc:
                log.warning("Diarization failed, continuing without speaker labels: %s", exc)
                warning = (
                    "Rozpoznání mluvčích se nezdařilo na Sparku ani lokálně — "
                    "přepis pokračuje bez rozlišení mluvčích."
                )
                segments = result.segments

        text = render_text(segments)
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(text)
        diarized = any(seg.speaker for seg in segments)
        meeting_json_path_for(txt_path).write_text(json.dumps({
            "version": 2,
            "language": language,
            "backend": result.backend,
            "num_speakers": num_speakers,
            # Provenance: which engine produced this transcript, and where.
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "duration": round(result.duration, 2),
            "asr": {
                "backend": result.backend,
                "where": "Pracovní Spark" if result.backend == "spark" else "Lokálně",
                "model": SPARK_WHISPER_MODEL if result.backend == "spark" else WHISPER_MODEL,
                "device": None if result.backend == "spark" else WHISPER_DEVICE,
            },
            "diarization": {
                "backend": diarization_backend,
                "where": (
                    "Pracovní Spark" if diarization_backend == "spark"
                    else "Lokálně" if diarization_backend == "local"
                    else None
                ),
                "model": diarization_model if diarized else None,
                "applied": diarized,
                "note": warning,
            },
            "segments": [dataclasses.asdict(seg) for seg in segments],
        }, ensure_ascii=False, indent=2))

        progress.finish(
            stage="done",
            percent=100,
            message="Hotovo." if not warning else warning,
            warning=warning,
            text=text,
            saved_path=rel_to_data(txt_path),
            diarization_backend=diarization_backend,
        )
    except Exception as e:  # surfaced to the UI, never crashes the worker thread
        progress.finish(stage="error", message=str(e), error=str(e))


def start_pipeline(wav_path, txt_path, language, num_speakers):
    """Start the single supported transcription job, returning whether it began."""
    if not progress.begin(
        message="Přepisuji audio...",
        operation="transcribe",
        source_path=rel_to_data(wav_path),
        saved_path=rel_to_data(txt_path),
    ):
        return False
    threading.Thread(
        target=run_pipeline,
        args=(wav_path, txt_path, language, num_speakers),
        daemon=True,
    ).start()
    return True
