"""Listening copy of a two-track recording: one mono mix without speaker echo.

The stored wav keeps the microphone and the system audio on separate
channels. Played as it is, the far end is heard twice: from the system track
in one ear and, a tenth of a second later, as loudspeaker echo on the
microphone track in the other. The player gets a mix of the echo-free
microphone and the system track instead. Transcription, trimming and
downloads keep using the original wav.
"""

import logging
import os
import subprocess
import threading

from . import asr

log = logging.getLogger(__name__)

_building = set()
_building_lock = threading.Lock()


def playback_path_for(wav_path):
    """Hidden next to the recording, so the folder on disk shows only the wav."""
    return wav_path.with_name(f".{wav_path.stem}.playback.flac")


def fresh(wav_path):
    """The listening copy of ``wav_path``, or None while it is missing or outdated.

    A finished copy carries the recording's mtime. Any rewrite of the
    recording (a trim) changes that, including one that lands while the copy
    is still being built from the old audio.
    """
    copy = playback_path_for(wav_path)
    try:
        if copy.stat().st_mtime_ns == wav_path.stat().st_mtime_ns:
            return copy
    except FileNotFoundError:
        pass
    return None


def build(wav_path):
    """Write the listening copy. None for single-track audio or on failure."""
    if asr.wav_channels(wav_path) < 2:
        return None
    copy = playback_path_for(wav_path)
    tmp = copy.with_name(f"{copy.name}.tmp")
    try:
        source = wav_path.stat()
        with asr.channel_wav(wav_path, 0) as mic:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(mic), "-i", str(wav_path),
                    # Plain sum, saturated on conversion to 16-bit. A limiter
                    # would delay the audio against the transcript times.
                    "-filter_complex",
                    "[1:a]pan=mono|c0=c1[sys];"
                    "[0:a][sys]amix=inputs=2:duration=longest:normalize=0",
                    "-c:a", "flac", "-f", "flac", str(tmp),
                ],
                check=True, capture_output=True, text=True,
            )
        os.utime(tmp, ns=(source.st_atime_ns, source.st_mtime_ns))
        os.replace(tmp, copy)
        return copy
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("Could not prepare the listening copy of %s: %s", wav_path.name, exc)
        return None
    finally:
        tmp.unlink(missing_ok=True)


def schedule(wav_path):
    """Build the listening copy in the background, once per recording at a time."""
    if asr.wav_channels(wav_path) < 2 or fresh(wav_path):
        return
    key = str(wav_path)
    with _building_lock:
        if key in _building:
            return
        _building.add(key)

    def run():
        try:
            build(wav_path)
        finally:
            with _building_lock:
                _building.discard(key)

    threading.Thread(target=run, name="playback-copy", daemon=True).start()
