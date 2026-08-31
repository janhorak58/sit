"""HTTP surface, one router per concern."""

from . import library, pages, recording, transcription, youtube

routers = [
    pages.router,
    recording.router,
    youtube.router,
    transcription.router,
    library.router,
]

__all__ = ["routers"]
