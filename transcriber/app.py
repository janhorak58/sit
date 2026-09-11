"""Application factory."""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .config import STATIC_DIR, ensure_dirs
from .connections import tunnels
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
    # SSH auth against an unreachable host can take up to FORWARD_TIMEOUT +
    # SSH_TEST_TIMEOUT seconds; run it off the startup path so uvicorn binds
    # immediately. Shutdown joins this thread first so tunnels.stop() never
    # races a start() still building its process list under the same lock.
    tunnel_thread = threading.Thread(target=tunnels.start, daemon=True)
    tunnel_thread.start()
    try:
        yield
    finally:
        recorder.stop()
        tunnel_thread.join()
        tunnels.stop()


def create_app():
    ensure_dirs()
    app = FastAPI(title="SIT — Smart Interactive Transcriber", lifespan=lifespan)

    @app.exception_handler(AppError)
    def _app_error(request: Request, exc: AppError):
        # The UI reads `j.error` off a 200 body; keep that contract.
        return JSONResponse({"error": str(exc)})

    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
    for router in routers:
        app.include_router(router)
    return app


app = create_app()
