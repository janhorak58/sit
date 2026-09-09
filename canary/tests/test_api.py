import importlib.util
import io
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


MODULE_PATH = Path(__file__).parents[1] / "app.py"
spec = importlib.util.spec_from_file_location("canary_app", MODULE_PATH)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def transport():
    return httpx.ASGITransport(app=canary.create_app())


def wav_bytes(seconds=0.5, sample_rate=16000):
    """Smallest real WAV ffmpeg will decode, so the service path stays honest."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x01" * int(seconds * sample_rate))
    return buffer.getvalue()


@pytest.mark.anyio
async def test_health_and_model_discovery_do_not_load_model(monkeypatch, transport):
    monkeypatch.setattr(canary, "_model", None)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        models = await client.get("/v1/models")

    assert health.json() == {
        "ok": True,
        "model": canary.MODEL_ID,
        "device": canary.DEVICE,
        "loaded": False,
    }
    # The client probes /v1/models to decide whether the remote engine is usable.
    assert models.json() == {
        "object": "list",
        "data": [{"id": canary.MODEL_ID, "object": "model"}],
    }


@pytest.mark.anyio
async def test_transcription_returns_verbose_json_the_client_can_stitch(monkeypatch, transport):
    seen = {}

    class Model:
        def recognize(self, waveform, **kwargs):
            seen.update(samples=len(waveform), kwargs=kwargs)
            return iter([
                SimpleNamespace(start=0.1, end=1.2, text="Ahoj světe."),
                SimpleNamespace(start=1.5, end=2.0, text="   "),
                SimpleNamespace(start=2.0, end=2.4, text="Druhá věta."),
            ])

    monkeypatch.setattr(canary, "get_model", lambda: Model())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("chunk.wav", wav_bytes(), "audio/wav")},
            data={"model": "nemo-canary-1b-v2", "response_format": "verbose_json", "language": "cs"},
        )

    assert response.status_code == 200
    body = response.json()
    # Blank VAD windows must not become empty transcript segments.
    assert body["segments"] == [
        {"start": 0.1, "end": 1.2, "text": "Ahoj světe."},
        {"start": 2.0, "end": 2.4, "text": "Druhá věta."},
    ]
    assert body["text"] == "Ahoj světe. Druhá věta."
    # Duration comes from the decoded audio, not from the last segment's end.
    assert body["duration"] == 0.5
    assert body["model"] == canary.MODEL_ID
    assert seen["samples"] == 8000
    assert seen["kwargs"]["language"] == "cs"
    assert seen["kwargs"]["pnc"] is True
    assert seen["kwargs"]["sample_rate"] == 16000


@pytest.mark.anyio
async def test_missing_language_falls_back_to_the_configured_default(monkeypatch, transport):
    seen = {}

    class Model:
        def recognize(self, waveform, **kwargs):
            seen.update(kwargs)
            return iter([])

    monkeypatch.setattr(canary, "get_model", lambda: Model())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("chunk.wav", wav_bytes(), "audio/wav")},
        )

    assert response.status_code == 200
    assert seen["language"] == canary.DEFAULT_LANGUAGE
    assert response.json()["language"] == canary.DEFAULT_LANGUAGE


@pytest.mark.anyio
async def test_undecodable_audio_fails_before_the_model_is_loaded(monkeypatch, transport):
    monkeypatch.setattr(
        canary,
        "get_model",
        lambda: (_ for _ in ()).throw(AssertionError("model should not load")),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("broken.wav", b"not audio at all", "audio/wav")},
        )

    assert response.status_code == 500
    assert "Transcription failed" in response.json()["detail"]


@pytest.mark.anyio
async def test_empty_upload_is_rejected_without_loading_model(monkeypatch, transport):
    monkeypatch.setattr(
        canary,
        "get_model",
        lambda: (_ for _ in ()).throw(AssertionError("model should not load")),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Audio file is empty"}


def test_cuda_request_fails_loudly_when_onnxruntime_falls_back_to_cpu(monkeypatch):
    """A silent CPU fallback would only show up as 20x slower transcription."""
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
    fake = SimpleNamespace(
        load_model=lambda *a, **k: SimpleNamespace(asr=SimpleNamespace(encoder=session)),
        load_vad=lambda *a, **k: object(),
    )
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", fake)
    monkeypatch.setattr(canary, "_model", None)
    monkeypatch.setattr(canary, "DEVICE", "cuda")

    with pytest.raises(RuntimeError, match="fell back to CPU"):
        canary.get_model()
