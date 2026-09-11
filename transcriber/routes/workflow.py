import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter
from ..connections import (
    endpoints,
    get_connection,
    normalize_connections,
    public_connections,
    save_connections,
    test_ssh,
    tunnels,
)
from ..errors import AppError
from ..preferences import get_preferences, merge_preferences, save_preferences

from ..asr import diagnose, remote_status
from ..config import (
    AUDIO_SUBDIR,
    DATA_DIR,
    INTERNAL_TOP_DIRS,
    LOCAL_ASR_DEVICE,
    LOCAL_ASR_MODEL,
    LOCAL_ASR_QUANTIZATION,
)
from ..library import meeting_json_path_for, read_text
from ..paths import rel_to_data, resolve_in_data, safe_target_dir, sanitize_component
from ..pipeline import remote_diarizer_status
from ..schemas import ConnectionsReq, PreferencesReq, SuggestReq, SummaryReq
from ..summary import remote_status as analysis_remote_status, render_markdown, summarize
from ..progress import progress

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/preferences")
def preferences():
    return get_preferences()


@router.put("/preferences")
def update_preferences(req: PreferencesReq):
    return save_preferences(req.preferences)


@router.get("/connections")
def connections():
    return {
        "connections": public_connections(),
        "endpoints": endpoints(),
        "tunnels": tunnels.status(),
    }


@router.put("/connections")
def update_connections(req: ConnectionsReq):
    save_connections(req.connections)
    return {"connections": public_connections(), "endpoints": endpoints(), "tunnels": tunnels.restart()}


@router.post("/connections/ssh-test")
def connections_ssh_test(req: ConnectionsReq):
    """Check the SSH login for the values currently in the form."""
    return test_ssh(normalize_connections(req.connections))


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
            "local_model": get_connection("diarization")["local_model"],
            "hf_token": bool(get_connection("diarization")["hf_token"]),
        },
        "analysis": analysis_remote_status(),
        "ssh": tunnels.status(),
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


def _previous_context(transcript):
    """Small, recent comparison context; never includes the meeting being summarized."""
    summaries = sorted(
        (path for path in transcript.parent.glob("*.summary.json") if path.stem != f"{transcript.stem}.summary"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:3]
    items = []
    for path in summaries:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        summary = str(data.get("summary") or "").strip()
        if summary:
            items.append(f"{path.stem.removesuffix('.summary')}: {summary}")
    return "\n".join(items)


def _brief_instructions(preferences):
    brief = preferences["brief"]
    sections = ", ".join(name.replace("_", " ") for name, enabled in brief["sections"].items() if enabled)
    language = "the transcript's language" if brief["language"] == "same" else brief["language"]
    prompt = str(brief.get("user_prompt") or "").strip()
    return f"Detail: {brief['detail']}. Write in {language}. Include only: {sections}." + (f"\nUser instructions: {prompt}" if prompt else "")


def _filter_brief(data, preferences):
    for section, enabled in preferences["brief"]["sections"].items():
        if not enabled:
            data[section] = "" if section == "follow_up" else []
    if not preferences["brief"]["compare_previous"]:
        data["changes_since_last"] = []
    return data


def _create_summary(transcript, path, project, preferences):
    try:
        text = read_text(path)
        segments = None
        meeting_json = meeting_json_path_for(transcript)
        if meeting_json.is_file():
            try:
                segments = json.loads(meeting_json.read_text()).get("segments")
            except (json.JSONDecodeError, OSError):
                pass
        previous = _previous_context(transcript) if preferences["brief"]["compare_previous"] else ""
        data = _filter_brief(
            summarize(text, project, segments, previous, _brief_instructions(preferences)),
            preferences,
        )
        summary_path = transcript.with_name(f"{transcript.stem}.summary.md")
        summary_path.write_text(render_markdown(data))
        transcript.with_name(f"{transcript.stem}.summary.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        if meeting_json.is_file():
            meeting = json.loads(meeting_json.read_text())
            meeting["analysis_stale"] = False
            meeting_json.write_text(json.dumps(meeting, ensure_ascii=False, indent=2))
        progress.finish(stage="done", percent=100, message="AI analysis is ready.", saved_path=path)
    except AppError as exc:
        progress.finish(stage="error", message=str(exc), error=str(exc), saved_path=path)
    except Exception as exc:
        logger.warning("AI analysis failed for %s: %s", path, exc)
        message = "The AI analysis could not be generated. Check the Connections panel."
        progress.finish(stage="error", message=message, error=message, saved_path=path)


@router.post("/summaries")
def create_summary(req: SummaryReq):
    transcript = resolve_in_data(req.path)
    if transcript is None or not transcript.is_file() or transcript.suffix != ".txt":
        raise AppError("Transcript not found.")
    preferences = merge_preferences(req.preferences) if req.preferences else get_preferences()
    if not progress.begin(
        stage="analyzing", percent=5, message="Preparing AI analysis...",
        operation="summarize", source_path=req.path, saved_path=req.path,
    ):
        raise AppError("Another processing step is already running.")
    threading.Thread(
        target=_create_summary,
        args=(transcript, req.path, req.project, preferences),
        daemon=True,
    ).start()
    return {"ok": True}
