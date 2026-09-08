"""HTTP surface, one router per concern."""

from . import library, pages, recording, transcription, upload, workflow

routers = [
    pages.router,
    recording.router,
    upload.router,
    transcription.router,
    library.router,
    workflow.router,
]

__all__ = ["routers"]
