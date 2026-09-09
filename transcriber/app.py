"""Application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .config import STATIC_DIR, ensure_dirs
from .errors import AppError
from .recorder import recorder
from .routes import routers


class NoCacheStaticFiles(StaticFiles):
    """Always revalidate: a plain browser refresh must fetch the latest JS.

    Without this, missing Cache-Control lets Chrome skip revalidating
    subresources on normal reload, so a persistent app-mode profile can run
    stale JS indefinitely after a deploy.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        recorder.stop()


def create_app():
    ensure_dirs()
    app = FastAPI(title="SHIT — Super-helpful interactive transcriber", lifespan=lifespan)

    @app.exception_handler(AppError)
    def _app_error(request: Request, exc: AppError):
        # The UI reads `j.error` off a 200 body; keep that contract.
        return JSONResponse({"error": str(exc)})

    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
    for router in routers:
        app.include_router(router)
    return app


app = create_app()
