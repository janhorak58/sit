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
        raise AppError("Recording not found.")
    if not start_pipeline(
        wav_path, transcript_path_for(wav_path), req.language or None, req.num_speakers
    ):
        raise AppError("Transcription is already running.")
    return {"ok": True}


@router.post("/diarize")
def diarize(req: DiarizeReq):
    wav_path = resolve_in_data(req.path)
    if wav_path is None or not wav_path.is_file():
        raise AppError("Recording not found.")
    txt_path = transcript_path_for(wav_path)
    if not txt_path.is_file():
        raise AppError("Create a transcript first.")
    meeting = read_meeting(rel_to_data(txt_path))
    if not meeting.get("segments"):
        raise AppError("Transcript has no timed segments for speaker recognition.")
    if not start_diarization(wav_path, txt_path, req.num_speakers):
        raise AppError("Another processing step is already running.")
    return {"ok": True}


@router.get("/progress")
def get_progress():
    return progress.snapshot()
