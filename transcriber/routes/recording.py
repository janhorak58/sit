from fastapi import APIRouter

from ..config import MAX_RECORDING_SECONDS, WAV_PATH
from ..errors import AppError
from ..library import store_recording
from ..paths import rel_to_data
from ..recorder import recorder
from ..schemas import StopReq

router = APIRouter()


@router.post("/start")
def start():
    recorder.start()
    return {"ok": True}


@router.post("/recording/cancel")
def cancel():
    """Stop the in-progress recording and discard its audio."""
    recorder.stop()
    WAV_PATH.unlink(missing_ok=True)
    return {"ok": True}


@router.get("/recording/status")
def status():
    return {
        "recording": recorder.is_recording,
        "started_at": recorder.started_at * 1000 if recorder.started_at else None,
        "max_seconds": MAX_RECORDING_SECONDS,
    }


@router.post("/stop")
def stop(req: StopReq):
    if not recorder.stop():
        raise AppError("Žádná nahrávka nenalezena.")
    target = store_recording(WAV_PATH, req.folder, req.filename)
    return {"ok": True, "path": rel_to_data(target)}
