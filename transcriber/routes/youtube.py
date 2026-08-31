from fastapi import APIRouter

from ..library import store_recording
from ..paths import rel_to_data
from ..schemas import YoutubeReq
from ..youtube import download_audio, fetch_title

router = APIRouter()


@router.post("/youtube")
def youtube(req: YoutubeReq):
    wav = download_audio(req.url)
    title = "" if req.filename else fetch_title(req.url)
    target = store_recording(wav, req.folder, req.filename or title)
    return {"ok": True, "path": rel_to_data(target), "title": title}
