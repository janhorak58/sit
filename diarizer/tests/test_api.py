import importlib.util
import io
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


MODULE_PATH = Path(__file__).parents[1] / "app.py"
spec = importlib.util.spec_from_file_location("diarizer_app", MODULE_PATH)
diarizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diarizer)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def transport():
    return httpx.ASGITransport(app=diarizer.create_app())


@pytest.mark.anyio
async def test_health_and_model_discovery_do_not_load_model(monkeypatch, transport):
    monkeypatch.setattr(diarizer, "_pipeline", None)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        models = await client.get("/v1/models")

    assert health.json() == {
        "ok": True,
        "model": diarizer.MODEL_ID,
        "device": diarizer.DEVICE,
        "loaded": False,
    }
    assert models.json() == {
        "object": "list",
        "data": [{"id": diarizer.MODEL_ID, "object": "model"}],
    }


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
async def test_diarization_returns_raw_speaker_turns(monkeypatch, transport):
    turn = SimpleNamespace(start=1.25, end=3.5)
    annotation = SimpleNamespace(
        itertracks=lambda yield_label: iter([(turn, None, "SPEAKER_01")])
    )
    seen = {}

    class Pipeline:
        def __call__(self, audio, **kwargs):
            seen.update(audio=audio, kwargs=kwargs)
            return annotation

    monkeypatch.setattr(diarizer, "get_pipeline", lambda: Pipeline())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/diarizations",
            files={"file": ("meeting.wav", wav_bytes(), "audio/wav")},
            data={"num_speakers": "2"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "model": diarizer.MODEL_ID,
        "segments": [{"start": 1.25, "end": 3.5, "speaker": "SPEAKER_01"}],
    }
    assert seen["kwargs"] == {"num_speakers": 2}
    # Uploaded audio is normalized to mono 16 kHz before inference.
    assert seen["audio"]["sample_rate"] == 16000
    assert seen["audio"]["waveform"].shape == (1, 8000)


@pytest.mark.anyio
async def test_undecodable_audio_fails_before_the_model_is_loaded(monkeypatch, transport):
    monkeypatch.setattr(
        diarizer,
        "get_pipeline",
        lambda: (_ for _ in ()).throw(AssertionError("model should not load")),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/diarizations",
            files={"file": ("broken.wav", b"not audio at all", "audio/wav")},
        )

    assert response.status_code == 500
    assert "Diarization failed" in response.json()["detail"]


@pytest.mark.anyio
async def test_empty_upload_is_rejected_without_loading_model(monkeypatch, transport):
    monkeypatch.setattr(
        diarizer,
        "get_pipeline",
        lambda: (_ for _ in ()).throw(AssertionError("model should not load")),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/audio/diarizations",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Audio file is empty"}
