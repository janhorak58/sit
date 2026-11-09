import json
import subprocess
import wave

import numpy as np
import pytest

RATE = 16000
FPS = 5


def _write_wav(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(np.zeros((seconds * RATE, 2), "<i2").tobytes())


def _frames(video):
    """Decoded frames as small grey images, one per 1/FPS second."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 120, 160)


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    from transcriber import library, paths, screen

    monkeypatch.setattr(library, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    scratch = tmp_path / "_scratch"
    scratch.mkdir()
    monkeypatch.setattr(screen, "SCRATCH_VIDEO", scratch / "current.screen.webm")
    monkeypatch.setattr(screen, "SCRATCH_OFFSET", scratch / "current.screen.offset")
    monkeypatch.setattr(screen, "SCRATCH_LOG", scratch / "current.screen.log")
    return tmp_path


def _record(data_dir, folder, name, audio_seconds, video_seconds, lead):
    """A saved recording whose screen video started ``lead`` seconds into the audio."""
    from transcriber import library, screen

    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", f"testsrc=size=160x120:rate={FPS}:duration={video_seconds}",
            "-c:v", "libvpx", "-g", str(screen.KEYFRAME_FRAMES), "-b:v", "300k",
            "-f", "webm", str(screen.SCRATCH_VIDEO),
        ],
        check=True,
    )
    screen.SCRATCH_OFFSET.write_text(json.dumps(lead))
    source = data_dir / "_scratch" / "current.wav"
    _write_wav(source, audio_seconds)
    return library.store_recording(source, folder, name)


def test_saved_video_stays_in_step_after_a_trim(data_dir):
    from transcriber import library, screen

    wav = _record(data_dir, "Team", "Weekly", audio_seconds=25, video_seconds=20, lead=3.0)
    video = screen.screen_path_for(wav)
    assert sorted(p.name for p in (data_dir / "_scratch").iterdir()) == []
    assert screen.describe(video)[0] == 3.0
    before = _frames(video)

    result = library.trim_recording("Team/audio/Weekly.wav", 10.0, 15.0)

    assert result["screen_kept"] is True
    lead, duration = screen.describe(video)
    # Audio second 10 was video second 7. The cut lands on the keyframe at 6,
    # so the video now starts one second before the trimmed audio does.
    assert lead == -1.0
    after = _frames(video)
    assert np.array_equal(after[0], before[6 * FPS])
    # It still reaches the trimmed audio's end: audio 15 s is video 12 s.
    assert len(after) == (12 - 6) * FPS
    assert duration == pytest.approx(6.0, abs=0.25)


def test_video_that_lies_only_in_the_removed_part_is_deleted(data_dir):
    from transcriber import library, screen

    wav = _record(data_dir, "Team", "Standup", audio_seconds=20, video_seconds=5, lead=3.0)

    result = library.trim_recording("Team/audio/Standup.wav", 10.0, 20.0)

    assert result["screen_kept"] is False
    assert not screen.screen_path_for(wav).exists()


def test_video_follows_its_recording_when_moved_and_deleted(data_dir):
    from transcriber import library, screen

    _record(data_dir, "Team/Weekly", "Weekly", audio_seconds=4, video_seconds=2, lead=0.5)

    library.move_item("Team/Weekly", "Weekly", "Archive", "Kickoff", "Team/Weekly/audio/Weekly.wav")

    moved = data_dir / "Archive/audio/Kickoff.wav"
    assert screen.describe(screen.screen_path_for(moved))[0] == 0.5
    assert not (data_dir / "Team/Weekly").exists()

    library.delete_file("Archive/audio/Kickoff.wav")

    assert not screen.screen_path_for(moved).exists()
