import json
import wave

import numpy as np
import pytest

from transcriber.errors import AppError

RATE = 16000


def _write_wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(samples.shape[1])
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(samples.astype("<i2").tobytes())


def _read_wav(path):
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        return np.frombuffer(frames, dtype="<i2").reshape(-1, handle.getnchannels())


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    from transcriber import library, paths

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    return tmp_path


def _ramp(seconds):
    """Stereo audio whose samples identify their own position."""
    index = np.arange(seconds * RATE) % 30000
    return np.stack([index, -index], axis=1)


def test_trim_keeps_exact_range_and_moves_transcript_with_it(data_dir):
    from transcriber import library

    audio = _ramp(10)
    _write_wav(data_dir / "Team/Weekly/audio/Weekly.wav", audio)
    (data_dir / "Team/Weekly/Weekly.txt").write_text("old")
    (data_dir / "Team/Weekly/Weekly.meeting.json").write_text(json.dumps({
        "version": 2,
        "duration": 10.0,
        "analysis_stale": False,
        "segments": [
            {"start": 0.5, "end": 1.5, "text": "Before the meeting.", "speaker": "Jan"},
            {"start": 1.5, "end": 2.6, "text": "Hello.", "speaker": "Jan"},
            {"start": 4.0, "end": 5.0, "text": "Agenda.", "speaker": "Eva"},
            {"start": 7.5, "end": 9.0, "text": "After hanging up.", "speaker": "Jan"},
        ],
    }))

    result = library.trim_recording("Team/Weekly/audio/Weekly.wav", 2.0, 8.0)

    assert result == {"duration": 6.0, "transcript_shifted": True, "screen_kept": None}
    assert np.array_equal(_read_wav(data_dir / "Team/Weekly/audio/Weekly.wav"), audio[2 * RATE:8 * RATE])
    meeting = json.loads((data_dir / "Team/Weekly/Weekly.meeting.json").read_text())
    assert [(s["start"], s["end"], s["text"]) for s in meeting["segments"]] == [
        (0.0, 0.6, "Hello."),
        (2.0, 3.0, "Agenda."),
    ]
    assert meeting["duration"] == 6.0
    assert meeting["analysis_stale"] is True
    assert (data_dir / "Team/Weekly/Weekly.txt").read_text() == "[Jan]\nHello.\n\n[Eva]\nAgenda."


def test_trim_without_transcript_only_cuts_audio(data_dir):
    from transcriber import library

    audio = _ramp(5)
    _write_wav(data_dir / "Solo/audio/Solo.wav", audio)

    result = library.trim_recording("Solo/audio/Solo.wav", 0, 3.25)

    assert result == {"duration": 3.25, "transcript_shifted": False, "screen_kept": None}
    assert np.array_equal(_read_wav(data_dir / "Solo/audio/Solo.wav"), audio[:52000])
    assert sorted(p.name for p in (data_dir / "Solo/audio").iterdir()) == ["Solo.wav"]


@pytest.mark.parametrize(("start", "end", "message"), [
    (0, 5, "Nothing to trim"),
    (2.0, 2.5, "at least one second"),
])
def test_trim_rejects_ranges_that_change_nothing_or_keep_too_little(data_dir, start, end, message):
    from transcriber import library

    audio = _ramp(5)
    _write_wav(data_dir / "Solo/audio/Solo.wav", audio)

    with pytest.raises(AppError, match=message):
        library.trim_recording("Solo/audio/Solo.wav", start, end)
    assert np.array_equal(_read_wav(data_dir / "Solo/audio/Solo.wav"), audio)


def test_trim_refuses_transcript_without_timings_and_keeps_audio(data_dir):
    from transcriber import library

    audio = _ramp(5)
    _write_wav(data_dir / "Old/audio/Old.wav", audio)
    (data_dir / "Old/Old.txt").write_text("legacy transcript")

    with pytest.raises(AppError, match="no timings"):
        library.trim_recording("Old/audio/Old.wav", 1, 4)
    assert np.array_equal(_read_wav(data_dir / "Old/audio/Old.wav"), audio)


def test_trim_waits_for_running_processing(data_dir):
    from transcriber.progress import progress
    from transcriber.routes import library as routes
    from transcriber.schemas import TrimReq

    audio = _ramp(5)
    _write_wav(data_dir / "Busy/audio/Busy.wav", audio)
    assert progress.begin(source_path="Busy/audio/Busy.wav")
    try:
        with pytest.raises(AppError, match="Wait until"):
            routes.trim(TrimReq(path="Busy/audio/Busy.wav", start=1, end=4))
    finally:
        progress.cancel()
    assert np.array_equal(_read_wav(data_dir / "Busy/audio/Busy.wav"), audio)


def test_levels_follow_the_louder_channel(data_dir):
    from transcriber import library

    quiet = np.zeros((4 * RATE, 2))
    remote_voice = np.zeros((4 * RATE, 2))
    remote_voice[:, 1] = 16000 * np.sin(np.arange(4 * RATE) / 10)
    _write_wav(data_dir / "Call/audio/Call.wav", np.concatenate([quiet, remote_voice]))

    result = library.recording_levels("Call/audio/Call.wav")

    levels = result["levels"]
    assert result["duration"] == 8.0
    assert max(levels[: len(levels) // 2]) == 0
    assert min(levels[len(levels) // 2:]) > 0.8
