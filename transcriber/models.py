"""Lazily loaded, process-wide ASR and diarization models.

Imports are deferred so the web app boots without paying onnxruntime's and
torch's import cost.
"""

import os
import threading

from .config import (
    DIARIZE_MODEL,
    HF_TOKEN,
    LOCAL_ASR_DEVICE,
    LOCAL_ASR_MODEL,
    LOCAL_ASR_PATH,
    LOCAL_ASR_QUANTIZATION,
    LOCAL_ASR_VAD_MODEL,
    LOCAL_ASR_WINDOW_SECONDS,
)

_cache = {"asr": None, "diarize": None}
# Guards lazy construction only; the batch pipeline and the live-draft worker
# can both race to instantiate the same model on first use, and loading it
# twice would waste memory/VRAM (and, on some backends, could corrupt shared
# state). Inference calls themselves run unlocked once the model exists.
_lock = threading.Lock()

_PROVIDERS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
}


def get_local_asr():
    """Local ASR (ONNX Runtime) wrapped in VAD, so long audio arrives segmented."""
    if _cache["asr"] is None:
        with _lock:
            if _cache["asr"] is None:
                import onnx_asr

                providers = _PROVIDERS.get(LOCAL_ASR_DEVICE.lower())
                if providers is None:
                    raise RuntimeError(
                        f"LOCAL_ASR_DEVICE={LOCAL_ASR_DEVICE!r} is not supported; "
                        f"use {' or '.join(sorted(_PROVIDERS))}."
                    )
                model = onnx_asr.load_model(
                    LOCAL_ASR_MODEL,
                    LOCAL_ASR_PATH or None,
                    quantization=LOCAL_ASR_QUANTIZATION or None,
                    providers=providers,
                )
                # VAD always runs on CPU: it is tiny, and keeping it off the GPU
                # avoids a second device context for a few milliseconds of work.
                vad = onnx_asr.load_vad(LOCAL_ASR_VAD_MODEL, providers=["CPUExecutionProvider"])
                _cache["asr"] = model.with_vad(
                    vad, max_speech_duration_s=LOCAL_ASR_WINDOW_SECONDS
                )
    return _cache["asr"]


def get_diarize_pipeline():
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN is not set; model pyannote/speaker-diarization-3.1 "
            "requires approved HuggingFace access."
        )
    if _cache["diarize"] is None:
        import torch

        torch.set_num_threads(os.cpu_count())
        from pyannote.audio import Pipeline

        _cache["diarize"] = Pipeline.from_pretrained(DIARIZE_MODEL, token=HF_TOKEN)
    return _cache["diarize"]
