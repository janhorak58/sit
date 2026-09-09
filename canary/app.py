"""GPU-backed Canary speech-to-text HTTP service.

OpenAI-compatible on purpose: the transcriber client posts multipart audio to
`/v1/audio/transcriptions` and probes `/v1/models`, so this service can be
swapped in wherever a Whisper endpoint was configured. Recognition runs through
onnx-asr (ONNX Runtime), the same runtime the client uses locally for Parakeet,
so there is one inference stack to reason about instead of two.
"""

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL_ID = os.environ.get("CANARY_MODEL", "nemo-canary-1b-v2")
# Empty = full precision. int8 halves VRAM at a small accuracy cost.
QUANTIZATION = os.environ.get("CANARY_QUANTIZATION", "")
DEVICE = os.environ.get("CANARY_DEVICE", "cuda")
VAD_MODEL = os.environ.get("CANARY_VAD_MODEL", "silero")
# Canary is an attention encoder-decoder: long windows cost quadratic attention
# and drift, so VAD cuts speech into windows of at most this many seconds.
WINDOW_SECONDS = float(os.environ.get("CANARY_WINDOW_SECONDS", "30"))
# Windows recognized in one forward pass; raise it to trade VRAM for throughput.
BATCH_SIZE = int(os.environ.get("CANARY_BATCH_SIZE", "8"))
DEFAULT_LANGUAGE = os.environ.get("CANARY_DEFAULT_LANGUAGE", "cs")
PRELOAD_MODEL = os.environ.get("PRELOAD_MODEL", "1").lower() not in {"0", "false", "no"}
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(2 * 1024**3)))
SAMPLE_RATE = 16000

_PROVIDERS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
}

_model = None
_load_lock = threading.Lock()
# One GPU, one graph: concurrent sessions would multiply VRAM and thrash.
_inference_lock = threading.Lock()


def get_model():
    """Load the process-wide model once; concurrent requests share it."""
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is None:
            import onnx_asr

            providers = _PROVIDERS.get(DEVICE.lower())
            if providers is None:
                raise RuntimeError(
                    f"CANARY_DEVICE={DEVICE!r} is not supported; "
                    f"use {' or '.join(sorted(_PROVIDERS))}"
                )
            model = onnx_asr.load_model(
                MODEL_ID, quantization=QUANTIZATION or None, providers=providers
            )
            if DEVICE.lower().startswith("cuda"):
                actual = getattr(model, "asr", model)
                sessions = [
                    session
                    for session in vars(actual).values()
                    if hasattr(session, "get_providers")
                ]
                if sessions and not any(
                    "CUDAExecutionProvider" in session.get_providers() for session in sessions
                ):
                    raise RuntimeError(
                        "CANARY_DEVICE requests CUDA, but ONNX Runtime fell back to CPU; "
                        "install onnxruntime-gpu matching the container's CUDA/cuDNN"
                    )
            # VAD stays on CPU: milliseconds of work, not worth a second context.
            vad = onnx_asr.load_vad(VAD_MODEL, providers=["CPUExecutionProvider"])
            _model = model.with_vad(
                vad, max_speech_duration_s=WINDOW_SECONDS, batch_size=BATCH_SIZE
            )
    return _model


def load_waveform(path: Path):
    """Decode `path` to the mono float32 waveform the model expects."""
    import numpy as np

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
    return samples


def _run_transcription(path: Path, language: str | None):
    """Return (segments, duration); times are relative to this file's start."""
    waveform = load_waveform(path)
    duration = len(waveform) / SAMPLE_RATE
    model = get_model()
    with _inference_lock:
        results = list(
            model.recognize(
                waveform,
                sample_rate=SAMPLE_RATE,
                language=language or DEFAULT_LANGUAGE,
                pnc=True,
            )
        )
    segments = [
        {"start": round(float(r.start), 3), "end": round(float(r.end), 3), "text": r.text}
        for r in results
        if r.text.strip()
    ]
    return segments, duration


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
    app = FastAPI(title="Canary Transcription Service", version="1.0")

    @app.on_event("startup")
    def preload_model():
        if PRELOAD_MODEL:
            get_model()

    @app.get("/health")
    def health():
        return {"ok": True, "model": MODEL_ID, "device": DEVICE, "loaded": _model is not None}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}

    @app.post("/v1/audio/transcriptions")
    async def transcribe(
        file: UploadFile = File(...),
        model: str | None = Form(default=None),
        language: str | None = Form(default=None),
        # Accepted for OpenAI compatibility; the response is always verbose.
        response_format: str | None = Form(default=None),
    ):
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp_path = Path(temp.name)
            await _save_upload(file, temp_path)
            segments, duration = await to_thread.run_sync(
                _run_transcription, temp_path, language
            )
            return {
                "model": MODEL_ID,
                "language": language or DEFAULT_LANGUAGE,
                "duration": round(duration, 3),
                "text": " ".join(segment["text"] for segment in segments),
                "segments": segments,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
        finally:
            await file.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return app


app = create_app()
