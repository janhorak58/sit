import json
from pathlib import Path

from fastapi import APIRouter

from ..asr import diagnose, remote_status
from ..config import (
    AUDIO_SUBDIR,
    DATA_DIR,
    DIARIZE_MODEL,
    HF_TOKEN,
    INTERNAL_TOP_DIRS,
    LOCAL_ASR_DEVICE,
    LOCAL_ASR_MODEL,
    LOCAL_ASR_QUANTIZATION,
)
from ..library import meeting_json_path_for, read_text
from ..paths import rel_to_data, resolve_in_data, safe_target_dir, sanitize_component
from ..pipeline import remote_diarizer_status
from ..schemas import SuggestReq, SummaryReq
from ..summary import render_markdown, summarize

router = APIRouter()


@router.get("/asr/status")
def asr_status():
    return remote_status()


@router.get("/asr/diagnostics")
def asr_diagnostics():
    """Everything the settings panel needs to explain the engine choice."""
    return {
        "remote": diagnose(),
        "local": {
            "model": LOCAL_ASR_MODEL,
            "device": LOCAL_ASR_DEVICE,
            "compute_type": LOCAL_ASR_QUANTIZATION or "float32",
        },
        "diarization": {
            "remote": remote_diarizer_status(),
            "local_model": DIARIZE_MODEL,
            "hf_token": bool(HF_TOKEN),
        },
    }


@router.get("/projects")
def projects():
    folders = sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir()
        and p.name != AUDIO_SUBDIR
        and p.name not in INTERNAL_TOP_DIRS
        and not p.name.startswith(".")
    )
    return {"projects": folders}


@router.post("/projects/suggest-folder")
def suggest_folder(req: SuggestReq):
    project = sanitize_component(req.project, "")
    if not project:
        raise AppError("Select a project first.")
    # Preserve the user's existing convention: project is the top-level folder.
    return {"folder": project}


@router.post("/summaries")
def create_summary(req: SummaryReq):
    transcript = resolve_in_data(req.path)
    if transcript is None or not transcript.is_file() or transcript.suffix != ".txt":
        raise AppError("Transcript not found.")
    text = read_text(req.path)
    segments = None
    meeting_json = meeting_json_path_for(transcript)
    if meeting_json.is_file():
        try:
            segments = json.loads(meeting_json.read_text()).get("segments")
        except (json.JSONDecodeError, OSError):
            segments = None
    data = summarize(text, req.project, segments)
    markdown = render_markdown(data)
    summary_path = transcript.with_name(f"{transcript.stem}.summary.md")
    summary_path.write_text(markdown)
    transcript.with_name(f"{transcript.stem}.summary.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    return {"ok": True, "summary": markdown, "path": rel_to_data(summary_path)}
