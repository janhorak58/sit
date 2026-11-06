import wave

import numpy as np
import pytest

from transcriber import asr, echo

pytestmark = pytest.mark.skipif(not echo.available(), reason="speexdsp is not installed")

RATE = echo.SAMPLE_RATE


def _write_stereo(path, mic, system):
    frames = np.stack([mic, system], axis=1).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(frames.tobytes())


def _read_mono(path):
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), np.int16).astype(np.float64)


def _db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-9)


def test_microphone_track_loses_speaker_echo_but_keeps_the_room(tmp_path):
    rng = np.random.default_rng(0)
    seconds = 30
    far = rng.normal(0, 3000, seconds * RATE)
    far[20 * RATE:25 * RATE] = 0  # far end silent: only the room talks
    local = np.zeros(seconds * RATE)
    local[20 * RATE:25 * RATE] = rng.normal(0, 2000, 5 * RATE)
    delay = int(0.116 * RATE)  # what a laptop's speakers measured
    room_echo = np.zeros_like(far)
    room_echo[delay:] = 0.5 * far[:-delay] + 0.2 * np.r_[np.zeros(40), far[:-delay - 40]]
    mic = local + room_echo
    wav = tmp_path / "meeting.wav"
    _write_stereo(wav, mic, far)

    with asr.channel_wav(wav, asr.CHANNEL_LABELS.index("mic")) as track:
        cleaned = _read_mono(track)

    assert len(cleaned) == len(mic)
    echo_only = slice(10 * RATE, 20 * RATE)  # after the filter converged
    assert _db(mic[echo_only]) - _db(cleaned[echo_only]) > 15
    room_only = slice(21 * RATE, 25 * RATE)
    assert abs(_db(mic[room_only]) - _db(cleaned[room_only])) < 1


def test_headphone_recording_keeps_the_microphone_track_as_recorded(tmp_path):
    rng = np.random.default_rng(1)
    mic = rng.normal(0, 2000, 10 * RATE)
    system = rng.normal(0, 3000, 10 * RATE)
    wav = tmp_path / "meeting.wav"
    _write_stereo(wav, mic, system)

    with asr.channel_wav(wav, asr.CHANNEL_LABELS.index("mic")) as track:
        kept = _read_mono(track)

    assert np.array_equal(kept, mic.astype(np.int16))
