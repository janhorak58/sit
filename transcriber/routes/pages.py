from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..config import INDEX_HTML

router = APIRouter()


@router.get("/")
def index():
    return FileResponse(INDEX_HTML, media_type="text/html")
