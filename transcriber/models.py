"""Lazily loaded, process-wide ASR and diarization models.

Imports are deferred so the web app boots without paying torch's import cost.
"""

import os

from .config import (
    DIARIZE_MODEL,
    HF_TOKEN,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)

_cache = {"whisper": None, "diarize": None}


def get_whisper_model():
    if _cache["whisper"] is None:
        from faster_whisper import WhisperModel

        _cache["whisper"] = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
        )
    return _cache["whisper"]


def get_diarize_pipeline():
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN není nastaven; model pyannote/speaker-diarization-3.1 "
            "vyžaduje schválený HuggingFace přístup."
        )
    if _cache["diarize"] is None:
        import torch

        torch.set_num_threads(os.cpu_count())
        from pyannote.audio import Pipeline

        _cache["diarize"] = Pipeline.from_pretrained(DIARIZE_MODEL, token=HF_TOKEN)
    return _cache["diarize"]
