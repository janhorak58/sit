from fastapi import APIRouter

from ..errors import AppError
from ..library import read_meeting, transcript_path_for
from ..paths import rel_to_data, resolve_in_data
from ..pipeline import start_diarization, start_pipeline
from ..progress import progress
from ..schemas import DiarizeReq, TranscribeReq

router = APIRouter()


@router.post("/transcribe")
def transcribe(req: TranscribeReq):
    wav_path = resolve_in_data(req.path)
    if wav_path is None or not wav_path.is_file():
        raise AppError("Nahrávka nenalezena.")
    if not start_pipeline(
        wav_path, transcript_path_for(wav_path), req.language or None, req.num_speakers
    ):
        raise AppError("Přepis už běží.")
    return {"ok": True}


@router.post("/diarize")
def diarize(req: DiarizeReq):
    wav_path = resolve_in_data(req.path)
    if wav_path is None or not wav_path.is_file():
        raise AppError("Nahrávka nenalezena.")
    txt_path = transcript_path_for(wav_path)
    if not txt_path.is_file():
        raise AppError("Nejdřív vytvoř přepis.")
    meeting = read_meeting(rel_to_data(txt_path))
    if not meeting.get("segments"):
        raise AppError("Přepis nemá časované segmenty pro rozpoznání mluvčích.")
    if not start_diarization(wav_path, txt_path, req.num_speakers):
        raise AppError("Jiný krok zpracování už běží.")
    return {"ok": True}


@router.get("/progress")
def get_progress():
    return progress.snapshot()
