from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .. import library
from ..paths import resolve_in_data
from ..schemas import DeleteReq, MkdirReq, MoveReq

router = APIRouter()


@router.get("/library/browse")
def browse(path: str = ""):
    return library.browse(path)


@router.post("/library/mkdir")
def mkdir(req: MkdirReq):
    return {"ok": True, "folder": library.make_dir(req.folder)}


@router.get("/library/file")
def read_file(path: str):
    return {"text": library.read_text(path)}


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
