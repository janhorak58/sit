"""Upload an existing audio/video file and normalize it into the library."""

import subprocess

from fastapi import APIRouter, Request

from ..config import SAMPLE_RATE, SCRATCH_DIR, WAV_PATH
from ..errors import AppError
from ..library import store_recording
from ..paths import rel_to_data, sanitize_component

router = APIRouter()


def clear_scratch():
    for path in SCRATCH_DIR.glob("current.*"):
        path.unlink()


@router.post("/upload")
async def upload(request: Request, folder: str = "", filename: str = "", ext: str = ""):
    """Raw file bytes in the body; ffmpeg does the container/rate conversion."""
    clear_scratch()
    suffix = sanitize_component(ext.lstrip(".").lower(), "bin")[:8]
    src = SCRATCH_DIR / f"upload.{suffix}"
    size = 0
    with src.open("wb") as fh:
        async for chunk in request.stream():
            size += fh.write(chunk)
    if not size:
        src.unlink(missing_ok=True)
        raise AppError("Empty file.")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ar", SAMPLE_RATE, "-ac", "1", str(WAV_PATH)],
        capture_output=True, text=True,
    )
    src.unlink(missing_ok=True)
    if proc.returncode != 0 or not WAV_PATH.exists():
        raise AppError((proc.stderr or "")[-2000:] or "conversion failed")
    target = store_recording(WAV_PATH, folder, filename)
    return {"ok": True, "path": rel_to_data(target)}
