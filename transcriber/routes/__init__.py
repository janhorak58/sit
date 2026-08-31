"""HTTP surface, one router per concern."""

from . import library, pages, recording, transcription, workflow, youtube

routers = [
    pages.router,
    recording.router,
    youtube.router,
    transcription.router,
    library.router,
    workflow.router,
]

__all__ = ["routers"]
