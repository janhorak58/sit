"""Transcription pipeline: ASR -> optional diarization -> meeting.json + text file."""

import dataclasses
from datetime import datetime
import json
import logging
import httpx
import subprocess
import threading

from .asr import CHANNEL_LABELS, LOCAL_LABEL, REMOTE_LABEL, Segment, channel_wav, transcribe
from .config import LOCAL_ASR_DEVICE, LOCAL_ASR_MODEL
from .connections import get_connection
from .library import atomic_write_text, meeting_json_path_for
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
        progress.update(percent=pct, message=f"Transcribing audio... {pct}%")

    result = transcribe(wav_path, language, on_segment)
    progress.update(
        backend=result.backend,
        message="Transcribing on the remote engine..." if result.backend == "spark"
        else "Transcribing on this computer...",
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


# A speaker change inside one ASR segment only counts once the other voice
# holds the floor this long; below that it is diarization jitter around the
# boundary, not a real turn.
MIN_TURN_SECONDS = 0.5


def speaker_spans(turns, start, end):
    """Merged [start, end, speaker] spans covering ``start``..``end``, in order.

    Adjacent turns of the same speaker collapse into one span, and spans
    shorter than ``MIN_TURN_SECONDS`` are dropped so jitter cannot split a
    sentence.
    """
    clipped = []
    for turn in sorted(turns, key=lambda t: float(t["start"])):
        span_start, span_end = max(float(turn["start"]), start), min(float(turn["end"]), end)
        if span_end - span_start <= 0:
            continue
        if clipped and clipped[-1][2] == turn["speaker"]:
            clipped[-1][1] = max(clipped[-1][1], span_end)
        else:
            clipped.append([span_start, span_end, turn["speaker"]])
    return [span for span in clipped if span[1] - span[0] >= MIN_TURN_SECONDS] or clipped


def split_by_speaker(segment, turns):
    """One ASR segment -> one segment per speaker actually talking inside it.

    ASR boundaries follow pauses, diarization boundaries follow voices, so a
    single segment regularly spans a question and its answer. Labelling the
    whole thing by its midpoint gave the answer the questioner's name; here
    the text is cut at the turn boundaries instead, proportionally to how long
    each speaker held the floor.
    """
    spans = speaker_spans(turns, segment.start, segment.end)
    if len(spans) <= 1:
        speaker = spans[0][2] if spans else speaker_at(turns, (segment.start + segment.end) / 2)
        return [dataclasses.replace(segment, speaker=speaker)]
    words = segment.text.split()
    if len(words) < len(spans):  # too short to cut sensibly; keep the loudest voice
        widest = max(spans, key=lambda span: span[1] - span[0])
        return [dataclasses.replace(segment, speaker=widest[2])]
    total = sum(end - start for start, end, _ in spans)
    parts, taken = [], 0
    for index, (start, end, speaker) in enumerate(spans):
        remaining_spans = len(spans) - index - 1
        if remaining_spans:
            share = round(len(words) * (end - start) / total)
            share = max(1, min(share, len(words) - taken - remaining_spans))
        else:
            share = len(words) - taken
        text = " ".join(words[taken:taken + share])
        taken += share
        parts.append(dataclasses.replace(segment, start=start, end=end, text=text, speaker=speaker))
    return parts


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
    endpoint = get_connection("diarization")["endpoint"]
    if not endpoint:
        return {"available": False, "backend": "local", "off": True, "last_error": None}
    health_url = endpoint.removesuffix("/v1/audio/diarizations").rstrip("/") + "/health"
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
            get_connection("diarization")["endpoint"],
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
        raise ValueError("Spark diarizer returned no segments.")
    return turns, body.get("model") or get_connection("diarization")["local_model"]


def _local_speaker_turns(wav_path, num_speakers):
    diar_kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    # Resolve the model first: it raises the actionable "extra not installed"
    # error, while load_waveform would only fail on a bare `import torch`.
    pipeline = get_diarize_pipeline()
    annotation = pipeline(load_waveform(wav_path), **diar_kwargs).speaker_diarization
    return [
        {"start": float(turn.start), "end": float(turn.end), "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def _speaker_turns(wav_path, num_speakers):
    """Diarization turns plus the backend and model that produced them."""
    global _last_remote_diarization_error
    diarization = get_connection("diarization")
    if diarization["endpoint"]:
        progress.update(stage="diarizing", percent=75, message="Recognizing speakers on the remote engine...")
        try:
            turns, model = _remote_speaker_turns(wav_path, num_speakers)
            _last_remote_diarization_error = None
            return turns, "spark", model
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            _last_remote_diarization_error = f"{exc} {detail}".strip()[:1000]
            log.warning(
                "Spark diarization failed, falling back to local: %s",
                _last_remote_diarization_error,
            )
    progress.update(stage="diarizing", percent=75, message="Recognizing speakers on this computer...")
    return _local_speaker_turns(wav_path, num_speakers), "local", diarization["local_model"]


def label_speakers(wav_path, segments, num_speakers):
    """Attach labels using Spark first, with the local model as fallback.

    A two-track recording is diarized one track at a time. The tracks already
    separate the room from the machine perfectly, so mixing them down would
    only ask pyannote to rediscover that split - badly, since the same voice
    reaches both. Labels are namespaced per track and can never collide.
    """
    channels = sorted({seg.channel for seg in segments if seg.channel})
    if not channels:
        turns, backend, model = _speaker_turns(wav_path, num_speakers)
        progress.update(stage="merging", percent=95, message="Assembling output...")
        return DiarizationResult(
            [part for seg in segments for part in split_by_speaker(seg, turns)], backend, model
        )

    labeled, backends, model = [], [], get_connection("diarization")["local_model"]
    for label in channels:
        index = CHANNEL_LABELS.index(label)
        track_segments = [seg for seg in segments if seg.channel == label]
        with channel_wav(wav_path, index) as track:
            # num_speakers counts people in the room, not per track, so the
            # model is left to decide how many voices each track carries.
            turns, backend, model = _speaker_turns(track, None)
        backends.append(backend)
        prefix = label.upper()
        for seg in track_segments:
            for part in split_by_speaker(seg, turns):
                labeled.append(dataclasses.replace(part, speaker=f"{prefix}_{part.speaker}"))
    labeled.sort(key=lambda seg: seg.start)
    progress.update(stage="merging", percent=95, message="Assembling output...")
    return DiarizationResult(labeled, backends[0] if len(set(backends)) == 1 else "local", model)


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
                channel=item.get("channel"),
            )
            for item in data.get("segments") or []
        ]
        diarization = label_speakers(wav_path, segments, num_speakers)
        segments = diarization.segments
        text = render_text(segments)
        atomic_write_text(txt_path, text)
        data["num_speakers"] = num_speakers
        data["diarization"] = {
            "backend": diarization.backend,
            "where": REMOTE_LABEL if diarization.backend == "spark" else LOCAL_LABEL,
            "model": diarization.model,
            "applied": True,
            "note": None,
        }
        data["segments"] = [dataclasses.asdict(segment) for segment in segments]
        atomic_write_text(meeting_path, json.dumps(data, ensure_ascii=False, indent=2))
        progress.finish(
            stage="done",
            percent=100,
            message=(
                "Speakers were recognized on the remote engine."
                if diarization.backend == "spark"
                else "Speakers were recognized on this computer."
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
        message="Recognizing speakers...",
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
                    "Speaker recognition is unavailable right now — "
                    "the transcript continues without speaker separation."
                )
                segments = result.segments

        text = render_text(segments)
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(txt_path, text)
        diarized = any(seg.speaker for seg in segments)
        remote_asr_model = get_connection("transcription")["model"]
        atomic_write_text(meeting_json_path_for(txt_path), json.dumps({
            "version": 2,
            "language": language,
            "backend": result.backend,
            "num_speakers": num_speakers,
            # Provenance: which engine produced this transcript, and where.
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "duration": round(result.duration, 2),
            "asr": {
                "backend": result.backend,
                "where": REMOTE_LABEL if result.backend == "spark" else LOCAL_LABEL,
                "model": remote_asr_model if result.backend == "spark" else LOCAL_ASR_MODEL,
                "device": None if result.backend == "spark" else LOCAL_ASR_DEVICE,
            },
            "diarization": {
                "backend": diarization_backend,
                "where": (
                    REMOTE_LABEL if diarization_backend == "spark"
                    else LOCAL_LABEL if diarization_backend == "local"
                    else None
                ),
                "model": diarization_model if diarized else None,
                "applied": diarized,
                "note": warning,
            },
            "segments": [dataclasses.asdict(seg) for seg in segments],
            "analysis_stale": False,
        }, ensure_ascii=False, indent=2))

        progress.finish(
            stage="done",
            percent=100,
            message="Done." if not warning else warning,
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
        message="Transcribing audio...",
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
