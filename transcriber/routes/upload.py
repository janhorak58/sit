"""Upload an existing audio/video file and normalize it into the library."""

import logging
import subprocess

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ..config import SAMPLE_RATE, SCRATCH_DIR, WAV_PATH
from ..errors import AppError
from ..library import store_recording
from ..paths import rel_to_data, sanitize_component
from ..recorder import recorder

router = APIRouter()
logger = logging.getLogger(__name__)


def clear_scratch():
    for pattern in ("current.*", "upload.*"):
        for path in SCRATCH_DIR.glob(pattern):
            path.unlink()


@router.post("/upload")
async def upload(request: Request, folder: str = "", filename: str = "", ext: str = ""):
    """Raw file bytes in the body; ffmpeg does the container/rate conversion."""
    if recorder.is_recording:
        raise AppError("Stop the current recording before importing a file.")
    clear_scratch()
    suffix = sanitize_component(ext.lstrip(".").lower(), "bin")[:8]
    src = SCRATCH_DIR / f"upload.{suffix}"
    size = 0
    try:
        with src.open("wb") as fh:
            async for chunk in request.stream():
                size += fh.write(chunk)
    except BaseException:
        # Client disconnected (or any other failure) mid-upload: don't leave
        # a partial file behind in the scratch dir forever.
        src.unlink(missing_ok=True)
        raise
    if not size:
        src.unlink(missing_ok=True)
        raise AppError("Empty file.")
    # ffmpeg on a large video can take real time; running it on the event
    # loop thread would freeze the whole app for every other request.
    proc = await run_in_threadpool(
        subprocess.run,
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ar", SAMPLE_RATE, "-ac", "1", str(WAV_PATH)],
        capture_output=True, text=True,
    )
    src.unlink(missing_ok=True)
    if proc.returncode != 0 or not WAV_PATH.exists():
        logger.warning("ffmpeg conversion failed: %s", proc.stderr)
        raise AppError("Could not read this file as audio or video.")
    target = store_recording(WAV_PATH, folder, filename)
    return {"ok": True, "path": rel_to_data(target)}
