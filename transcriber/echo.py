"""Offline removal of loudspeaker echo from the microphone track.

Without headphones the microphone re-records whatever the machine plays. The
system track holds the exact signal that went to the speakers, so an adaptive
echo canceller (speexdsp, loaded through ctypes) can subtract it from the
microphone after the fact. On real meetings this took 13-17 dB of far-end
speech off the microphone and kept the local speaker intact, where matching
transcripts by text had left most of the duplicate far-end speech in place.

The library is optional: without it the microphone track stays as recorded.
"""
import ctypes
import ctypes.util
import logging
import wave

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# 16 ms frames, the size speexdsp is tuned for.
FRAME = 256
# Room reverb after the direct path. The bulk loopback delay is removed before
# the filter sees the signal, so the filter only has to model the room.
TAIL = 4096
# The filter window opens this far before the measured delay, so an early
# reflection or a few ms of capture jitter still falls inside it.
LAG_MARGIN = 640
# Loopback plus capture buffering measured 87-127 ms. A second is ample.
MAX_LAG = SAMPLE_RATE
PROBE_WINDOWS = 8
PROBE_SECONDS = 20
# Headphone recordings correlate at about 0.05, speaker echo at 0.2-0.7.
MIN_CORRELATION = 0.15
# Frames processed per write: 10 s keeps memory flat on hour-long meetings.
BLOCK_FRAMES = 625

_SET_SAMPLING_RATE = 24


def _load():
    name = ctypes.util.find_library("speexdsp")
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.speex_echo_state_init.restype = ctypes.c_void_p
    lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
    lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p] * 4
    return lib


_lib = _load()


def available():
    return _lib is not None


def estimate_delay(mic, ref):
    """Samples by which the speakers' echo trails ``ref`` in ``mic``.

    None when no probe window shows the echo, which is what a recording made
    on headphones looks like. Probes spread over the whole recording because a
    meeting often starts or ends with nobody talking on the far end.
    """
    window = min(PROBE_SECONDS * SAMPLE_RATE, len(mic), len(ref))
    if window < SAMPLE_RATE:
        return None
    size = 1 << (2 * window - 1).bit_length()
    lags = []
    for start in np.unique(np.linspace(0, len(mic) - window, PROBE_WINDOWS).astype(int)):
        a = mic[start:start + window].astype(np.float64)
        b = ref[start:start + window].astype(np.float64)
        energy = np.sqrt(np.dot(a, a) * np.dot(b, b))
        if not energy:
            continue
        # corr[k] = sum(a[n + k] * b[n]): a positive k means mic lags ref.
        corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
        corr = corr[:MAX_LAG + 1]
        lag = int(np.argmax(np.abs(corr)))
        if abs(corr[lag]) / energy >= MIN_CORRELATION:
            lags.append(lag)
    return int(np.median(lags)) if lags else None


def _delayed(ref, start, stop, shift):
    """``ref[start - shift:stop - shift]``, zero where that runs before the start."""
    out = np.zeros(stop - start, np.int16)
    lo, hi = max(start - shift, 0), max(stop - shift, 0)
    if hi > lo:
        out[lo + shift - start:hi + shift - start] = ref[lo:hi]
    return out


def cancel(mic_path, ref_path, out_path):
    """Write the microphone minus the speakers' echo as a mono 16 kHz wav.

    Both inputs are raw mono s16le at ``SAMPLE_RATE`` and of equal length.
    Returns False, writing nothing, when the microphone holds no echo.
    """
    mic = np.memmap(mic_path, dtype="<i2", mode="r")
    ref = np.memmap(ref_path, dtype="<i2", mode="r")
    lag = estimate_delay(mic, ref)
    if lag is None:
        log.info("No loudspeaker echo on the microphone track; keeping it as recorded")
        return False
    log.info("Cancelling loudspeaker echo (delay %.0f ms)", lag * 1000 / SAMPLE_RATE)
    shift = max(lag - LAG_MARGIN, 0)
    usable = len(mic) // FRAME * FRAME
    block = BLOCK_FRAMES * FRAME
    cleaned = np.empty(block, np.int16)
    state = _lib.speex_echo_state_init(FRAME, TAIL)
    try:
        rate = ctypes.c_int(SAMPLE_RATE)
        _lib.speex_echo_ctl(state, _SET_SAMPLING_RATE, ctypes.byref(rate))
        with wave.open(str(out_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            for start in range(0, usable, block):
                stop = min(start + block, usable)
                near = np.ascontiguousarray(mic[start:stop])
                far = _delayed(ref, start, stop, shift)
                for offset in range(0, stop - start, FRAME):
                    _lib.speex_echo_cancellation(
                        state,
                        near.ctypes.data + 2 * offset,
                        far.ctypes.data + 2 * offset,
                        cleaned.ctypes.data + 2 * offset,
                    )
                out.writeframes(cleaned[:stop - start].tobytes())
            # A partial last frame (under 16 ms) passes through untouched.
            out.writeframes(mic[usable:].tobytes())
    finally:
        _lib.speex_echo_state_destroy(state)
    return True
