from fastapi import APIRouter

from ..config import WAV_PATH
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


@router.post("/stop")
def stop(req: StopReq):
    if not recorder.stop():
        raise AppError("Žádná nahrávka nenalezena.")
    target = store_recording(WAV_PATH, req.folder, req.filename)
    return {"ok": True, "path": rel_to_data(target)}
