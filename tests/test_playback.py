import subprocess
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from transcriber import echo

RATE = echo.SAMPLE_RATE


def _write_wav(path, frames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(frames.shape[1])
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(frames.astype("<i2").tobytes())


def _decode(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, "<i2").astype(np.float64)


def _db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-9)


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.mark.skipif(not echo.available(), reason="speexdsp is not installed")
def test_listening_copy_plays_the_far_end_once_and_keeps_the_room(tmp_path):
    from transcriber import playback

    rng = np.random.default_rng(0)
    seconds = 30
    far = rng.normal(0, 3000, seconds * RATE)
    local = np.zeros_like(far)
    local[20 * RATE:25 * RATE] = rng.normal(0, 2000, 5 * RATE)
    delay = int(0.116 * RATE)
    room_echo = np.zeros_like(far)
    room_echo[delay:] = 0.5 * far[:-delay]
    wav = tmp_path / "audio" / "meeting.wav"
    _write_wav(wav, np.stack([local + room_echo, far], axis=1))

    copy = playback.build(wav)

    mixed = _decode(copy)
    assert len(mixed) == len(far)
    leftover = mixed - far
    far_only = slice(5 * RATE, 20 * RATE)
    # The echo the stereo file plays in the microphone ear is mostly gone.
    assert _db(leftover[far_only]) < _db(room_echo[far_only]) - 10
    # Speech in the room stays audible.
    talking = slice(21 * RATE, 25 * RATE)
    assert _db(leftover[talking]) > _db(local[talking]) - 3


def test_rewritten_recording_plays_the_original_until_its_copy_is_rebuilt(data_dir, monkeypatch):
    from transcriber import library, playback
    from transcriber.app import app

    wav = data_dir / "Team/Weekly/audio/Weekly.wav"
    _write_wav(wav, np.zeros((3 * RATE, 2)))
    playback.build(wav)
    client = TestClient(app)
    url = "/library/audio?path=Team/Weekly/audio/Weekly.wav"
    assert client.get(url).headers["content-type"] == "audio/flac"

    library.trim_recording("Team/Weekly/audio/Weekly.wav", 0.0, 2.0)
    built = []
    monkeypatch.setattr(playback, "schedule", built.append)

    response = client.get(url)

    assert response.headers["content-type"] == "audio/wav"
    assert built == [wav]


def test_listening_copy_follows_its_recording_when_moved_or_deleted(data_dir):
    from transcriber import library, playback

    wav = data_dir / "Team/Weekly/audio/Weekly.wav"
    _write_wav(wav, np.zeros((RATE, 2)))
    (data_dir / "Team/Weekly/Weekly.txt").write_text("transcript")
    playback.build(wav)

    library.move_item("Team/Weekly", "Weekly", "Archive", "Kickoff", "Team/Weekly/audio/Weekly.wav")
    moved = data_dir / "Archive/audio/Kickoff.wav"
    assert playback.fresh(moved) is not None

    library.delete_file("Archive/Kickoff.txt")
    assert playback.fresh(moved) is not None
    library.delete_file("Archive/audio/Kickoff.wav")
    assert not playback.playback_path_for(moved).exists()
