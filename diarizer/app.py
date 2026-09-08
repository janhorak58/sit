"""GPU-backed pyannote speaker diarization HTTP service.

Audio is decoded with ffmpeg(1) and handed to pyannote as an in-memory waveform.
The pinned pyannote 3.x stack supports the NVIDIA image's NumPy 1.x ABI.
"""

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL_ID = os.environ.get(
    "DIARIZE_MODEL", "pyannote/speaker-diarization-3.1"
)
DEVICE = os.environ.get("DIARIZE_DEVICE", "cuda")
PRELOAD_MODEL = os.environ.get("PRELOAD_MODEL", "1").lower() not in {"0", "false", "no"}
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024**3)))
SAMPLE_RATE = 16000


_pipeline = None
_load_lock = threading.Lock()
_inference_lock = threading.Lock()


def get_pipeline():
    """Load the process-wide pipeline once; concurrent requests share it."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _load_lock:
        if _pipeline is None:
            import torch
            from pyannote.audio import Pipeline
            from pyannote.audio.core.task import Problem, Resolution, Specifications

            if DEVICE.startswith("cuda") and not torch.cuda.is_available():
                raise RuntimeError("DIARIZE_DEVICE requests CUDA, but CUDA is unavailable")
            token = os.environ.get("HF_TOKEN") or None
            # Legacy checkpoints contain these types. Keep weights-only loading
            # enabled and limit the allowlist to this model-loading operation.
            with torch.serialization.safe_globals([
                torch.torch_version.TorchVersion, Specifications, Problem, Resolution,
            ]):
                pipeline = Pipeline.from_pretrained(MODEL_ID, use_auth_token=token)
            pipeline.to(torch.device(DEVICE))
            _pipeline = pipeline
    return _pipeline


def load_waveform(path: Path):
    """Decode `path` into the in-memory waveform pyannote expects."""
    import numpy as np
    import torch

    raw = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
        ],
        check=True, capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32).copy()
    if samples.size == 0:
        raise ValueError("Audio file contains no decodable audio stream")
    return {
        "waveform": torch.from_numpy(samples).unsqueeze(0),
        "sample_rate": SAMPLE_RATE,
    }


def _run_diarization(path: Path, num_speakers: int | None):
    kwargs = {"num_speakers": num_speakers} if num_speakers is not None else {}
    audio = load_waveform(path)
    with _inference_lock:
        annotation = get_pipeline()(audio, **kwargs)
    return [
        {"start": float(turn.start), "end": float(turn.end), "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


async def _save_upload(upload: UploadFile, path: Path):
    size = 0
    with path.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Audio file is too large")
            target.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty")


def create_app():
    app = FastAPI(title="Pyannote Diarization Service", version="1.0")

    @app.on_event("startup")
    def preload_pipeline():
        if PRELOAD_MODEL:
            get_pipeline()

    @app.get("/health")
    def health():
        return {"ok": True, "model": MODEL_ID, "device": DEVICE, "loaded": _pipeline is not None}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}

    @app.post("/v1/audio/diarizations")
    async def diarize(
        file: UploadFile = File(...),
        num_speakers: int | None = Form(default=None, ge=1),
    ):
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp_path = Path(temp.name)
            await _save_upload(file, temp_path)
            segments = await to_thread.run_sync(_run_diarization, temp_path, num_speakers)
            return {"model": MODEL_ID, "segments": segments}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Diarization failed: {exc}") from exc
        finally:
            await file.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return app


app = create_app()
