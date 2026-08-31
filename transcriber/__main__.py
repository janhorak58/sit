"""`python -m transcriber` — dev entrypoint."""

import uvicorn

from .config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("transcriber.app:app", host=HOST, port=PORT)
