"""Live draft from sequential overlapping PCM windows, with one ASR worker.

Session IDs reject stale results after cancellation. Slow inference accumulates
visible lag, not queued jobs or missing audio; the saved recording is untouched.
"""

import dataclasses
import logging
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path
from uuid import uuid4

from .asr import Segment, remote_status, transcribe_live as transcribe

log = logging.getLogger(__name__)

# Rolling contextual window: at most WINDOW_SECONDS of audio sent per pass,
# with OVERLAP_SECONDS of trailing context carried into the next one so a
# word split across window boundaries still has surrounding audio on one
# side or the other. Growth during the first WINDOW_SECONDS is organic (the
# window's end is capped to whatever audio actually exists yet), so the
# first pass fires as soon as MIN_NEW_SECONDS is available instead of
# waiting for a full WINDOW_SECONDS window to fill.
WINDOW_SECONDS = 8.0
OVERLAP_SECONDS = 6.0
MIN_NEW_SECONDS = 2.0
# How often the worker checks the growing wav file for enough new audio to
# start a pass. Small: this is a stat()+maybe-header-parse, not a transcribe.
POLL_INTERVAL = 0.15
# Back off this long after a failed window so a persistently broken backend
# can't spin the loop hot; unrelated to how eagerly new audio is polled.
ERROR_BACKOFF = 1.0
# Finite bound on a single live window's remote call. Short so a stalled Spark
# endpoint can never wedge the worker for long; the batch pipeline's own
# timeout (asr.transcribe's default) is untouched by this.
LIVE_ASR_TIMEOUT = 20.0
_PUNCT = ".,!?;:\"'…"


@dataclasses.dataclass
class LiveState:
    session_id: str | None = None
    state: str = "idle"
    segments: list = dataclasses.field(default_factory=list)
    processed_seconds: float = 0.0
    error: str | None = None
    language: str = ""
    wav_path: Path | None = None
    # (data_offset, frame_rate, channels, sample_width) of wav_path, parsed
    # once per session off whatever is actually on disk.
    header: tuple | None = None
    # Decided once per session by the worker (never by ``start()``, which
    # must not block on an HTTP probe) and reused by every window in it, so
    # no live pass pays for a remote_status() probe on top of its own upload.
    remote_available: bool | None = None
    stop_event: threading.Event | None = None


def _wav_pcm_info(path):
    """(data_offset, frame_rate, channels, sample_width), read off the header
    actually on disk — never trusted from the (possibly still-streaming,
    placeholder-sized) RIFF/data chunk sizes."""
    with open(path, "rb") as f:
        riff = f.read(12)
        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise ValueError("not a wav file yet")
        frame_rate = channels = sample_width = None
        while True:
            header = f.read(8)
            if len(header) < 8:
                raise ValueError("wav header not fully written yet")
            chunk_id, chunk_size = struct.unpack("<4sI", header)
            if chunk_id == b"fmt ":
                fmt = f.read(chunk_size)
                if len(fmt) < 16:
                    raise ValueError("wav fmt chunk not fully written yet")
                _, channels, frame_rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                sample_width = bits // 8
            elif chunk_id == b"data":
                if frame_rate is None:
                    raise ValueError("wav data chunk before fmt chunk")
                return f.tell(), frame_rate, channels, sample_width
            else:
                f.seek(chunk_size, 1)
            if chunk_size % 2:
                f.seek(1, 1)


def _audio_seconds(wav_path, header):
    """Best-effort current duration of the growing file; 0.0 if unreadable."""
    if wav_path is None or header is None:
        return 0.0
    try:
        size = wav_path.stat().st_size
    except OSError:
        return 0.0
    data_offset, frame_rate, channels, sample_width = header
    bytes_per_sec = frame_rate * channels * sample_width
    if bytes_per_sec <= 0:
        return 0.0
    return max(size - data_offset, 0) / bytes_per_sec


def _read_window(path, data_offset, frame_rate, channels, sample_width, start_s, end_s):
    """Raw PCM bytes for ``[start_s, end_s)``, frame-aligned; ``b""`` if unreadable."""
    frame_bytes = channels * sample_width
    bytes_per_sec = frame_rate * frame_bytes
    start_off = data_offset + int(start_s * bytes_per_sec)
    length = int((end_s - start_s) * bytes_per_sec)
    length -= length % frame_bytes
    if length <= 0:
        return b""
    try:
        with open(path, "rb") as f:
            f.seek(start_off)
            return f.read(length)
    except OSError:
        return b""


def _write_temp_wav(pcm_bytes, frame_rate, channels, sample_width):
    fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    fd.close()
    with wave.open(fd.name, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(frame_rate)
        wf.writeframes(pcm_bytes)
    return Path(fd.name)


def _normalize_words(words):
    return [w.strip(_PUNCT).lower() for w in words]


def _dedupe_overlap(prev_text, next_text):
    """Trim ``next_text``'s longest word-prefix that duplicates ``prev_text``'s
    suffix — the two overlapping windows' shared audio re-transcribed the same
    words, and only the genuinely new tail of ``next_text`` should survive."""
    prev_words = prev_text.split()
    next_words = next_text.split()
    limit = min(len(prev_words), len(next_words))
    for n in range(limit, 0, -1):
        if _normalize_words(prev_words[-n:]) == _normalize_words(next_words[:n]):
            return " ".join(next_words[n:])
    return next_text


def _stitch(kept, new_segments):
    """Append ``new_segments`` after ``kept``, deduplicating the overlap text
    against the tail of the last kept segment instead of dropping either side
    wholesale — preserves the committed sentence prefix and never repeats the
    words both windows independently transcribed for the shared audio."""
    if not kept or not new_segments:
        return kept + list(new_segments)
    prev_text = kept[-1].text
    rest = list(new_segments)
    while rest:
        trimmed = _dedupe_overlap(prev_text, rest[0].text)
        if trimmed.strip():
            rest[0] = dataclasses.replace(rest[0], text=trimmed)
            break
        rest.pop(0)
    return kept + rest


class LiveTranscriber:
    """Owns the single live-draft session and its one persistent worker thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = LiveState()
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def snapshot(self):
        with self._lock:
            s = self._state
            audio_seconds = _audio_seconds(s.wav_path, s.header)
            return {
                "session_id": s.session_id,
                "state": s.state,
                "segments": [
                    {"start": seg.start, "end": seg.end, "text": seg.text}
                    for seg in s.segments
                ],
                "audio_seconds": audio_seconds,
                "processed_seconds": s.processed_seconds,
                "error": s.error,
                "language": s.language,
            }

    def start(self, wav_path, language, enabled=True):
        """Reset for a fresh session. When ``enabled`` is false, the session
        stays idle and no worker/ASR work ever runs for it — the toggle is a
        real resource switch, not just a UI hint. Never blocks: the persistent
        worker only picks up an enabled session on its next loop tick."""
        with self._lock:
            if not enabled:
                self._state = LiveState()
                return
            self._state = LiveState(
                session_id=uuid4().hex, state="listening", language=language or "",
                wav_path=wav_path, stop_event=threading.Event(),
            )
            self._wake.set()

    def stop_listening(self):
        """Capture ended: let any in-flight window finish, flush a trailing
        partial window, then settle into 'stopped'. Never blocks."""
        with self._lock:
            event = self._state.stop_event
        if event is not None:
            event.set()

    def cancel(self):
        """Drop the session immediately (start/cancel reset). Never blocks on
        the worker thread; a stale in-flight pass's later publish attempts
        are discarded because the session_id no longer matches."""
        with self._lock:
            event = self._state.stop_event
            self._state = LiveState()
            self._wake.set()
        if event is not None:
            event.set()

    # -- publishing helpers, all session-guarded under the lock ------------

    def _set_header(self, session_id, header):
        with self._lock:
            if self._state.session_id != session_id:
                return False
            self._state.header = header
            return True

    def _set_remote(self, session_id, remote_available):
        with self._lock:
            if self._state.session_id != session_id:
                return False
            self._state.remote_available = remote_available
            return True

    def _publish_transcribing(self, session_id):
        with self._lock:
            if self._state.session_id != session_id:
                return False
            self._state.state = "transcribing"
            return True

    def _publish_window(self, session_id, window_start, window_end, new_segments):
        with self._lock:
            if self._state.session_id != session_id:
                return False
            kept = [s for s in self._state.segments if s.start < window_start]
            self._state.segments = _stitch(kept, new_segments)
            self._state.processed_seconds = window_end
            self._state.state = "listening"
            self._state.error = None
            return True

    def _publish_error(self, session_id, message):
        with self._lock:
            if self._state.session_id != session_id:
                return False
            self._state.error = message
            self._state.state = "error"
            return True

    def _finalize(self, session_id):
        with self._lock:
            if self._state.session_id != session_id:
                return
            self._state.state = "stopped"

    # -- the one persistent worker ------------------------------------

    def _loop(self):
        while True:
            with self._lock:
                s = self._state
                session_id, wav_path, language = s.session_id, s.wav_path, s.language
                processed, header, stop_event = s.processed_seconds, s.header, s.stop_event
                remote_available = s.remote_available
                waiting = session_id is None or s.state == "stopped"
                if waiting:
                    self._wake.clear()

            if waiting:
                self._wake.wait()
                continue
            stopping = stop_event.is_set() if stop_event is not None else True

            try:
                size = wav_path.stat().st_size
            except OSError:
                if stopping:
                    self._finalize(session_id)
                time.sleep(POLL_INTERVAL)
                continue

            if header is None:
                try:
                    header = _wav_pcm_info(wav_path)
                except (ValueError, OSError):
                    if stopping:
                        self._finalize(session_id)
                    time.sleep(POLL_INTERVAL)
                    continue
                if not self._set_header(session_id, header):
                    continue

            if remote_available is None:
                # Decided once per session, here in the worker thread (never
                # in start(), which must never block) and cached so no later
                # window pays for another remote_status() probe.
                remote_available = remote_status()["available"]
                if not self._set_remote(session_id, remote_available):
                    continue

            data_offset, frame_rate, channels, sample_width = header
            bytes_per_sec = frame_rate * channels * sample_width
            if bytes_per_sec <= 0:
                self._finalize(session_id)
                time.sleep(POLL_INTERVAL)
                continue
            audio_seconds = max(size - data_offset, 0) / bytes_per_sec

            # Rolling contextual window: start OVERLAP_SECONDS behind the
            # last processed point (never before 0) and extend up to
            # WINDOW_SECONDS, capped to whatever audio actually exists yet —
            # so the very first passes grow organically (0..2, 0..4, 0..6,
            # 0..8) instead of waiting for a full WINDOW_SECONDS window
            # before transcribing anything. A slow backend falls behind
            # (audio_seconds keeps growing past processed) but never skips a
            # stretch of speech: processed only ever advances to window_end,
            # and the next window_start still starts from there.
            window_start = max(0.0, processed - OVERLAP_SECONDS)
            window_end = min(audio_seconds, window_start + WINDOW_SECONDS)
            if window_end - processed < MIN_NEW_SECONDS:
                if not stopping:
                    time.sleep(POLL_INTERVAL)
                    continue
                if audio_seconds <= processed:
                    self._finalize(session_id)
                    time.sleep(POLL_INTERVAL)
                    continue
                window_end = audio_seconds  # final, shorter-than-usual flush

            pcm = _read_window(
                wav_path, data_offset, frame_rate, channels, sample_width,
                window_start, window_end,
            )
            if not pcm:
                if stopping:
                    self._finalize(session_id)
                time.sleep(POLL_INTERVAL)
                continue

            if not self._publish_transcribing(session_id):
                continue

            tmp_path = None
            errored = False
            try:
                tmp_path = _write_temp_wav(pcm, frame_rate, channels, sample_width)
                result = transcribe(
                    tmp_path, language or None,
                    timeout=LIVE_ASR_TIMEOUT, remote_available=remote_available,
                )
                shifted = [
                    Segment(seg.start + window_start, seg.end + window_start, seg.text)
                    for seg in result.segments
                ]
                if not self._publish_window(session_id, window_start, window_end, shifted):
                    continue
            except Exception as exc:  # slow/broken ASR must never break capture
                errored = True
                log.warning("Live draft window failed, will retry: %s", exc)
                if not self._publish_error(session_id, str(exc)[:500]):
                    continue
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)

            if stopping and window_end >= audio_seconds:
                self._finalize(session_id)
            if errored:
                # Back off before retrying the same window so a persistently
                # broken backend can't spin this loop hot.
                time.sleep(ERROR_BACKOFF)


live = LiveTranscriber()
