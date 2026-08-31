"""Transcription pipeline: whisper -> optional diarization -> text file."""

import threading

from .models import get_diarize_pipeline, get_whisper_model
from .paths import rel_to_data
from .progress import progress


def speaker_at(diarization, t):
    """Speaker label whose turn covers instant ``t`` most."""
    best, best_overlap = None, 0.0
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        overlap = min(turn.end, t + 0.01) - max(turn.start, t)
        if overlap > best_overlap:
            best_overlap, best = overlap, speaker
    return best or "SPEAKER_00"


def transcribe_segments(wav_path, language, cap):
    """Run whisper, reporting progress up to ``cap`` percent."""
    model = get_whisper_model()
    kwargs = {"language": language} if language else {}
    segments_gen, info = model.transcribe(str(wav_path), **kwargs)
    segments = []
    for seg in segments_gen:
        segments.append(seg)
        pct = min(cap - 1, int(seg.end / max(info.duration, 0.01) * cap))
        progress.update(percent=pct, message=f"Přepisuji audio... {pct}%")
    return segments


def label_speakers(wav_path, segments, num_speakers):
    """Attach speaker headings to whisper segments via pyannote diarization."""
    progress.update(stage="diarizing", percent=75, message="Rozpoznávám mluvčí...")
    diar_kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    diarization = get_diarize_pipeline()(str(wav_path), **diar_kwargs).speaker_diarization

    progress.update(stage="merging", percent=95, message="Skládám výstup...")
    lines, last_speaker = [], None
    for seg in segments:
        speaker = speaker_at(diarization, (seg.start + seg.end) / 2)
        if speaker != last_speaker:
            lines.append(f"\n[{speaker}]")
            last_speaker = speaker
        lines.append(seg.text.strip())
    return "\n".join(lines).strip()


def run_pipeline(wav_path, txt_path, language, num_speakers):
    """Blocking end-to-end run; all state is reported through ``progress``."""
    try:
        progress.reset(stage="transcribing", message="Přepisuji audio...")
        single = num_speakers == 1
        segments = transcribe_segments(wav_path, language, cap=40 if single else 70)

        if single:
            text = "\n".join(seg.text.strip() for seg in segments).strip()
        else:
            text = label_speakers(wav_path, segments, num_speakers)

        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(text)
        progress.update(
            stage="done",
            percent=100,
            message="Hotovo.",
            text=text,
            saved_path=rel_to_data(txt_path),
        )
    except Exception as e:  # surfaced to the UI, never crashes the worker thread
        progress.update(stage="error", message=str(e), error=str(e))


def start_pipeline(wav_path, txt_path, language, num_speakers):
    """Kick off ``run_pipeline`` on a daemon thread and return immediately."""
    threading.Thread(
        target=run_pipeline,
        args=(wav_path, txt_path, language, num_speakers),
        daemon=True,
    ).start()
