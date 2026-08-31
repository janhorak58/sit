"""Application factory."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, ensure_dirs
from .errors import AppError
from .routes import routers


def create_app():
    ensure_dirs()
    app = FastAPI(title="Transcriber")

    @app.exception_handler(AppError)
    def _app_error(request: Request, exc: AppError):
        # The UI reads `j.error` off a 200 body; keep that contract.
        return JSONResponse({"error": str(exc)})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in routers:
        app.include_router(router)
    return app


app = create_app()
