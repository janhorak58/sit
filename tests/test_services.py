from types import SimpleNamespace

import httpx

from transcriber import asr
from transcriber.errors import AppError
from transcriber.summary import summarize


def test_asr_falls_back_to_local_when_remote_transcription_fails(monkeypatch, tmp_path):
    wav = tmp_path / "meeting.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(asr, "remote_status", lambda: {"available": True})
    monkeypatch.setattr(asr, "_remote_transcribe", lambda *args: (_ for _ in ()).throw(httpx.ConnectError("down")))
    expected = asr.Transcript([asr.Segment(0, 1, "hello")], 1, "local")
    monkeypatch.setattr(asr, "_local_transcribe", lambda *args: expected)
    assert asr.transcribe(wav, "en").backend == "local"


def test_summary_uses_private_openai_compatible_endpoint(monkeypatch):
    seen = {}
    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": "# Summary\nDone"}}]},
        )
    monkeypatch.setattr("transcriber.summary.httpx.post", fake_post)
    assert summarize("Hello", "Acme") == "# Summary\nDone"
    assert seen["json"]["stream"] is False
    assert "Project: Acme" in seen["json"]["messages"][1]["content"]
