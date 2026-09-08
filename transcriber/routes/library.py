from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse

from .. import library
from ..errors import AppError
from ..paths import resolve_in_data
from ..schemas import DeleteReq, FolderRenameReq, MkdirReq, MoveReq, RenameSpeakerReq

router = APIRouter()


@router.get("/library/browse")
def browse(path: str = ""):
    return library.browse(path)


@router.post("/library/mkdir")
def mkdir(req: MkdirReq):
    return {"ok": True, "folder": library.make_dir(req.folder)}


@router.post("/library/folder/rename")
def rename_folder(req: FolderRenameReq):
    return {"ok": True, "folder": library.rename_folder(req.path, req.name)}


@router.post("/library/folder/delete")
def delete_folder(req: DeleteReq):
    library.delete_folder(req.path)
    return {"ok": True}


@router.get("/library/file")
def read_file(path: str):
    return {"text": library.read_text(path)}


@router.get("/library/meeting")
def meeting(path: str):
    return library.read_meeting(path)


@router.get("/library/summary-json")
def summary_json(path: str):
    return library.read_summary_json(path) or {}


@router.post("/library/rename-speaker")
def rename_speaker(req: RenameSpeakerReq):
    return library.rename_speaker(req.path, req.old, req.new)


@router.get("/library/audio")
def audio(path: str):
    target = resolve_in_data(path)
    if target is None or not target.is_file():
        raise AppError("Nahrávka nenalezena.")
    return FileResponse(target, media_type="audio/wav")


@router.post("/library/move")
def move(req: MoveReq):
    library.move_item(req.folder, req.name, req.to_folder, req.to_name, req.wav_path)
    return {"ok": True}


@router.post("/library/delete")
def delete(req: DeleteReq):
    library.delete_file(req.path)
    return {"ok": True}


@router.get("/download")
def download(path: str | None = None):
    target = resolve_in_data(path) if path else None
    if target is None or not target.is_file():
        return PlainTextResponse("")
    return PlainTextResponse(
        target.read_text(),
        headers={"Content-Disposition": f"attachment; filename={target.name}"},
    )
