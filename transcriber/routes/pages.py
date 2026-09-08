from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import INDEX_HTML, STATIC_DIR

router = APIRouter()


@router.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC_DIR / "js" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/")
def index():
    return FileResponse(
        INDEX_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )
