import pytest


@pytest.fixture(autouse=True)
def no_ai_correction(monkeypatch):
    """Keep pipeline tests off the network: the AI correction finds nothing to fix."""
    from transcriber import pipeline

    monkeypatch.setattr(pipeline, "correct_cues", lambda *args, **kwargs: ({}, "test-llm"))
