from pathlib import Path

from fastapi import APIRouter

from ..asr import remote_status
from ..config import DATA_DIR, INTERNAL_TOP_DIRS
from ..errors import AppError
from ..library import read_text
from ..paths import rel_to_data, resolve_in_data, safe_target_dir, sanitize_component
from ..schemas import SuggestReq, SummaryReq
from ..summary import summarize

router = APIRouter()


@router.get("/asr/status")
def asr_status():
    return remote_status()


@router.get("/projects")
def projects():
    folders = sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir() and p.name not in INTERNAL_TOP_DIRS and not p.name.startswith(".")
    )
    return {"projects": folders}


@router.post("/projects/suggest-folder")
def suggest_folder(req: SuggestReq):
    project = sanitize_component(req.project, "")
    if not project:
        raise AppError("Nejdřív vyber projekt.")
    # Preserve the user's existing convention: project is the top-level folder.
    return {"folder": project}


@router.post("/summaries")
def create_summary(req: SummaryReq):
    transcript = resolve_in_data(req.path)
    if transcript is None or not transcript.is_file() or transcript.suffix != ".txt":
        raise AppError("Přepis nenalezen.")
    text = read_text(req.path)
    result = summarize(text, req.project)
    summary_path = transcript.with_name(f"{transcript.stem}.summary.md")
    summary_path.write_text(result)
    return {"ok": True, "summary": result, "path": rel_to_data(summary_path)}
