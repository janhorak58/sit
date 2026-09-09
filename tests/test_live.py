import threading
import time
import wave

import pytest

from transcriber import live as live_module
from transcriber.asr import Segment, Transcript


@pytest.fixture(autouse=True)
def local_live_engine(monkeypatch):
    monkeypatch.setattr(live_module, 'remote_status', lambda: {'available': False})


def audio(path, seconds):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0\0' * (16000 * seconds))


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), 'Live worker did not reach the expected state'


def text(session):
    return ' '.join(segment['text'].strip() for segment in session.snapshot()['segments'])


def test_overlapping_windows_preserve_sentence_prefix_without_repeating_overlap(monkeypatch, tmp_path):
    path = tmp_path / 'recording.wav'
    audio(path, 8)
    calls = []

    def transcribe(path, language, **kwargs):
        calls.append(language)
        if len(calls) == 1:
            return Transcript([Segment(0, 7, 'We will ship the release on Friday.')], 8, 'local')
        return Transcript([Segment(0, 4, 'on Friday. Documentation follows.')], 8, 'local')

    monkeypatch.setattr(live_module, 'POLL_INTERVAL', 0.01)
    monkeypatch.setattr(live_module, 'transcribe', transcribe)
    session = live_module.LiveTranscriber()
    try:
        session.start(path, 'en')
        eventually(lambda: session.snapshot()['processed_seconds'] >= 8)
        with path.open('ab') as output:
            output.write(b'\0\0' * (16000 * 2))
        eventually(lambda: session.snapshot()['processed_seconds'] >= 10)
        draft = text(session)
        assert 'We will ship the release' in draft
        assert draft.count('on Friday.') == 1
        assert 'Documentation follows.' in draft
        assert session.snapshot()['language'] == 'en'
    finally:
        session.cancel()


def test_cancelled_inference_cannot_publish_or_run_parallel_to_new_session(monkeypatch, tmp_path):
    path = tmp_path / 'recording.wav'
    audio(path, 8)
    entered = threading.Event()
    release = threading.Event()
    second = threading.Event()

    def transcribe(path, language, **kwargs):
        if language == 'en':
            entered.set()
            assert release.wait(3)
            return Transcript([Segment(0, 5, 'obsolete draft')], 8, 'local')
        second.set()
        return Transcript([Segment(0, 5, 'nová nahrávka')], 8, 'local')

    monkeypatch.setattr(live_module, 'POLL_INTERVAL', 0.01)
    monkeypatch.setattr(live_module, 'transcribe', transcribe)
    session = live_module.LiveTranscriber()
    try:
        session.start(path, 'en')
        assert entered.wait(3)
        with path.open('ab') as output:
            output.write(b'\0\0' * (16000 * 6))
        assert session.snapshot()['audio_seconds'] >= 14
        old_id = session.snapshot()['session_id']
        before = time.monotonic()
        session.cancel()
        assert session.snapshot()['segments'] == []
        session.start(path, 'cs')
        assert time.monotonic() - before < 0.5
        assert session.snapshot()['session_id'] != old_id
        assert not second.wait(0.1), 'Cancelled inference must not accumulate concurrent workers'
        release.set()
        eventually(lambda: 'nová nahrávka' in text(session))
        assert 'obsolete' not in text(session)
    finally:
        release.set()
        session.cancel()


def test_live_failure_leaves_audio_available_for_saving(monkeypatch, tmp_path):
    from transcriber import library, paths
    from transcriber.recorder import Recorder
    from transcriber.routes import recording
    from transcriber.schemas import StopReq

    path = tmp_path / 'current.wav'
    audio(path, 8)
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError('ASR unavailable')

    monkeypatch.setattr(live_module, 'POLL_INTERVAL', 0.01)
    monkeypatch.setattr(live_module, 'transcribe', fail)
    session = live_module.LiveTranscriber()
    monkeypatch.setattr(recording, 'live', session)
    monkeypatch.setattr(recording, 'recorder', Recorder(path))
    monkeypatch.setattr(recording, 'WAV_PATH', path)
    monkeypatch.setattr(library, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    try:
        session.start(path, 'en')
        eventually(lambda: session.snapshot()['error'] is not None)
        assert recording.status()['available'] is True
        result = recording.stop(StopReq(folder='project', filename='meeting'))
        assert (tmp_path / result['path']).read_bytes() == original
    finally:
        session.cancel()


def test_slow_inference_does_not_skip_unprocessed_audio(monkeypatch, tmp_path):
    import struct

    path = tmp_path / 'recording.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        for second in range(8):
            output.writeframesraw(struct.pack('<h', second) * 16000)
    entered = threading.Event()
    release = threading.Event()
    starts = []

    def transcribe(path, language, **kwargs):
        with wave.open(str(path), 'rb') as source:
            starts.append(struct.unpack('<h', source.readframes(1))[0])
        if len(starts) == 1:
            entered.set()
            assert release.wait(3)
        return Transcript([], 8, 'local')

    monkeypatch.setattr(live_module, 'POLL_INTERVAL', 0.01)
    monkeypatch.setattr(live_module, 'transcribe', transcribe)
    session = live_module.LiveTranscriber()
    try:
        session.start(path, 'en')
        assert entered.wait(3)
        with path.open('ab') as output:
            for second in range(8, 22):
                output.write(struct.pack('<h', second) * 16000)
        release.set()
        eventually(lambda: len(starts) >= 3)
        assert starts[:3] == [0, 2, 4]
    finally:
        release.set()
        session.cancel()


def test_draft_arrives_after_two_seconds_and_revises_with_context(monkeypatch, tmp_path):
    path = tmp_path / 'recording.wav'
    audio(path, 2)
    durations = []

    def transcribe(path, language, **kwargs):
        with wave.open(str(path), 'rb') as source:
            duration = source.getnframes() / source.getframerate()
        durations.append(duration)
        sentence = 'We can' if duration == 2 else 'We can ship tomorrow.'
        return Transcript([Segment(0, duration, sentence)], duration, 'local')

    monkeypatch.setattr(live_module, 'POLL_INTERVAL', 0.01)
    monkeypatch.setattr(live_module, 'transcribe', transcribe)
    session = live_module.LiveTranscriber()
    try:
        session.start(path, 'en')
        eventually(lambda: text(session) == 'We can')
        assert session.snapshot()['processed_seconds'] == 2
        with path.open('ab') as output:
            output.write(b'\0\0' * (16000 * 2))
        eventually(lambda: text(session) == 'We can ship tomorrow.')
        assert durations == [2, 4]
    finally:
        session.cancel()


def test_live_toggle_off_keeps_session_idle_without_running_worker(tmp_path):
    path = tmp_path / 'recording.wav'
    audio(path, 2)

    def unexpected_transcribe(*args, **kwargs):
        raise AssertionError('disabled live session must never call the ASR backend')

    live_module.transcribe = unexpected_transcribe
    session = live_module.LiveTranscriber()
    try:
        session.start(path, 'en', enabled=False)
        time.sleep(0.2)
        snapshot = session.snapshot()
        assert snapshot['session_id'] is None
        assert snapshot['state'] == 'idle'
        assert snapshot['segments'] == []
    finally:
        session.cancel()
