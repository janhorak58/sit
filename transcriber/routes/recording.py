import threading

from fastapi import APIRouter

from ..config import MAX_RECORDING_SECONDS, WAV_PATH
from ..errors import AppError
from ..library import store_recording
from ..live import live
from ..paths import rel_to_data
from ..recorder import capture_levels, microphones, recorder
from ..schemas import StartReq, StopReq

router = APIRouter()
_lifecycle_lock = threading.Lock()

# The watchdog auto-stop has no HTTP route to hang a callback off; wire it
# here so an auto-stopped capture still tells the live worker to stop
# scheduling new windows (drafts already produced remain available).
recorder.on_stop = live.stop_listening


@router.get("/recording/devices")
def devices():
    return {"microphones": microphones()}


@router.post("/start")
def start(req: StartReq | None = None):
    with _lifecycle_lock:
        recorder.start(req.microphone if req else "")
        live.start(
            recorder.wav_path, (req.language if req else "cs") or "",
            enabled=req.live if req else True,
        )
        return {"ok": True}


@router.post("/recording/cancel")
def cancel():
    """Stop the in-progress recording and discard its audio and any draft."""
    with _lifecycle_lock:
        recorder.stop()
        live.cancel()
        WAV_PATH.unlink(missing_ok=True)
        return {"ok": True}


@router.get("/recording/status")
def status():
    return {
        "recording": recorder.is_recording,
        "started_at": recorder.started_at * 1000 if recorder.started_at else None,
        "max_seconds": MAX_RECORDING_SECONDS,
        "available": not recorder.is_recording and WAV_PATH.exists(),
        "live": live.snapshot(),
        # Drives the waveform meter, so the user can see the microphone work.
        "levels": capture_levels(WAV_PATH) if recorder.is_recording else None,
    }


@router.post("/stop")
def stop(req: StopReq):
    with _lifecycle_lock:
        if not recorder.stop():
            raise AppError("No recording found.")
        target = store_recording(WAV_PATH, req.folder, req.filename)
        return {"ok": True, "path": rel_to_data(target)}
