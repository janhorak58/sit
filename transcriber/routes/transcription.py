from fastapi import APIRouter

from ..errors import AppError
from ..library import transcript_path_for
from ..paths import resolve_in_data
from ..pipeline import start_pipeline
from ..progress import progress
from ..schemas import TranscribeReq

router = APIRouter()


@router.post("/transcribe")
def transcribe(req: TranscribeReq):
    wav_path = resolve_in_data(req.path)
    if wav_path is None or not wav_path.is_file():
        raise AppError("Nahrávka nenalezena.")
    start_pipeline(
        wav_path, transcript_path_for(wav_path), req.language or None, req.num_speakers
    )
    return {"ok": True}


@router.get("/progress")
def get_progress():
    return progress.snapshot()
