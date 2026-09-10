"""PulseAudio + ffmpeg capture of microphone and system output into one wav."""

import array
import json
import signal
import subprocess
import sys
import threading
import time

from .errors import AppError

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, SINK_NAME, WAV_PATH


def pactl(*args):
    try:
        return subprocess.run(
            ["pactl", *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except FileNotFoundError as exc:
        raise AppError(
            "Missing pactl. Install pulseaudio-utils (Ubuntu/Debian) or "
            "libpulse (Arch). You can upload a finished audio file without it."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AppError(
            "The audio server does not support the requested recording, or is not available. "
            "Check PulseAudio / PipeWire-Pulse and microphone access. "
            "WSLg may not support Windows audio loopback; use file upload instead. "
            f"Detail: {(exc.stderr or '').strip()}"
        ) from exc


def _pactl_list(*args):
    """A pactl JSON listing, or [] when the payload is not a list at all."""
    try:
        payload = json.loads(pactl("-f", "json", *args))
    except (AppError, json.JSONDecodeError, TypeError):
        return []
    return payload if isinstance(payload, list) else []


def _cards():
    """Sound cards as pactl reports them, or [] when the server is unusable."""
    return _pactl_list("list", "cards")


def _input_profile(card):
    """An available profile of ``card`` that exposes a capture device.

    A Bluetooth headset in A2DP still publishes a ``bluez_input`` source, but
    that profile carries no microphone and the source returns digital silence.
    The headset (HFP/HSP) profiles are the ones that actually capture.
    """
    profiles = card.get("profiles") or {}
    usable = [
        name for name, profile in profiles.items()
        if profile.get("available", True) and (profile.get("sources") or 0) > 0
    ]
    if not usable:
        return None
    # Prefer the profile that keeps playback working too, then the shortest
    # name, which is the plain variant rather than a codec-specific one.
    return min(usable, key=lambda name: (-(profiles[name].get("sinks") or 0), len(name), name))


def _capture_state(cards):
    """Per card name: (has a capture device now, profile that would provide one)."""
    state = {}
    for card in cards:
        profiles = card.get("profiles") or {}
        active = profiles.get(card.get("active_profile") or "") or {}
        state[card.get("name")] = (
            (active.get("sources") or 0) > 0,
            _input_profile(card),
        )
    return state


def _sources():
    return _pactl_list("list", "sources")


def _source_card(name):
    """Name of the card owning input source ``name``, or "" when unknown."""
    for source in _sources():
        if source.get("name") == name:
            return (source.get("properties") or {}).get("device.name") or ""
    return ""


def microphones():
    """List physical input sources, flagging the ones that cannot capture."""
    try:
        default = pactl("get-default-source")
    except AppError:
        return []
    sources = _sources()
    capture = _capture_state(_cards())
    devices = []
    for source in sources:
        name = str(source.get("name") or "")
        properties = source.get("properties") or {}
        if not name or name.endswith(".monitor") or properties.get("device.class") == "monitor":
            continue
        card = properties.get("device.name") or ""
        # Cards pactl does not report at all are assumed to work; only a known
        # card with a known input-less active profile is flagged.
        ready, profile = capture.get(card, (True, None))
        devices.append({
            "id": name,
            "label": source.get("description") or properties.get("device.description") or name,
            "default": name == default,
            "available": bool(ready or profile),
            "needs_profile": None if ready else profile,
            "note": None if ready or profile else "This device has no microphone in any profile.",
        })
    return devices



# Enough tail to look like a waveform at the 250 ms status poll, cheap enough
# to read on every one: 0.75 s of 16 kHz mono s16le is 24 kB.
LEVEL_WINDOW_SECONDS = 0.75
# ffmpeg's pcm_s16le wav header; skipping it keeps the tail read frame-aligned.
WAV_HEADER_BYTES = 44
LEVEL_BARS = 28


def capture_levels(path, bars=LEVEL_BARS):
    """Per-bar peak (0..1) over the tail of a growing capture, newest last."""
    empty = {"bars": [0.0] * bars, "peak": 0.0}
    frame_rate = int(SAMPLE_RATE)
    window = int(frame_rate * LEVEL_WINDOW_SECONDS) * 2
    try:
        size = path.stat().st_size
        if size <= WAV_HEADER_BYTES:
            return empty
        with path.open("rb") as handle:
            start = max(WAV_HEADER_BYTES, size - window)
            handle.seek(start - (start - WAV_HEADER_BYTES) % 2)
            data = handle.read(window)
    except OSError:
        return empty
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if not samples:
        return empty
    step = max(1, len(samples) // bars)
    peaks = []
    for index in range(bars):
        chunk = samples[index * step:(index + 1) * step]
        if not chunk:
            peaks.append(0.0)
            continue
        # Some laptop codecs carry a large fixed DC bias. It sounds like
        # silence after a high-pass filter but otherwise paints a flat meter.
        center = sum(chunk) / len(chunk)
        peaks.append(max(abs(sample - center) for sample in chunk) / 32768)
    # Older audio first so the bars scroll left to right like a waveform.
    return {"bars": [round(peak, 3) for peak in peaks], "peak": round(max(peaks), 3)}


class Recorder:
    """Owns a direct microphone capture, system-audio loopback and ffmpeg."""
    def __init__(self, wav_path=WAV_PATH):
        self.wav_path = wav_path
        self.proc = None
        self.started_at = None
        self.modules = {"sink": None, "loop1": None, "loop2": None}
        # (card, profile) to put back after a capture-only profile switch.
        self.restore_profile = None
        self._lock = threading.Lock()
        # Called under the capture lock before another recording can start.
        # The callback only signals live transcription; it must not wait for ASR.
        self.on_stop = None

    def _prepare_source(self, microphone):
        """Return a concrete input source, switching Bluetooth to HFP if needed.

        PipeWire keeps a Bluetooth headset in headset mode only while a real
        client captures its source.  A PulseAudio loopback is insufficient and
        WirePlumber switches it back to A2DP after a couple of seconds.
        """
        microphone = microphone or pactl("get-default-source")
        if not microphone:
            raise AppError("No microphone is selected. Connect one and try again.")
        device = next((mic for mic in microphones() if mic["id"] == microphone), None)
        if device is None:
            raise AppError(
                "The selected microphone is not available. Reconnect it or pick another one."
            )
        if device["needs_profile"] is None:
            if not device["available"]:
                raise AppError(f"{device['label']} has no microphone to record from.")
            return microphone
        card_name = _source_card(microphone)
        card = next((card for card in _cards() if card.get("name") == card_name), None)
        if card is None:
            raise AppError(f"{device['label']} has no microphone in its current mode.")
        previous = card.get("active_profile")
        pactl("set-card-profile", card["name"], device["needs_profile"])
        self.restore_profile = (card["name"], previous)
        # The capture device appears asynchronously after the switch.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any(mic["id"] == microphone and mic["needs_profile"] is None for mic in microphones()):
                return microphone
            time.sleep(0.2)
        raise AppError(
            f"{device['label']} did not provide a microphone after switching to "
            f"{device['needs_profile']}. Pick another microphone."
        )

    @property
    def is_recording(self):
        return self.proc is not None

    def start(self, microphone=""):
        with self._lock:
            if self.is_recording:
                raise AppError("Recording is already running.")
            if not sys.platform.startswith("linux"):
                raise AppError(
                    "Direct recording requires Linux with PulseAudio / PipeWire-Pulse. "
                    "On this platform, upload an audio file instead."
                )
            try:
                microphone = self._prepare_source(microphone)
                self.modules["sink"] = pactl(
                    "load-module", "module-null-sink", f"sink_name={SINK_NAME}",
                    "sink_properties=device.description=MeetingRec",
                )
                # The microphone goes directly to ffmpeg: PipeWire then keeps
                # a Bluetooth headset in its capture-capable HFP profile.
                self.modules["loop2"] = pactl(
                    "load-module", "module-loopback",
                    "source=@DEFAULT_SINK@.monitor", f"sink={SINK_NAME}",
                )
                # ffmpeg opens (and, via -y, truncates) the output file only
                # once its own startup completes, which is asynchronous with
                # this call returning. Remove any stale wav synchronously so a
                # live-draft worker started right after this never reads a
                # previous session's leftover audio before ffmpeg gets to it.
                self.wav_path.unlink(missing_ok=True)
                self.proc = subprocess.Popen([
                    "ffmpeg", "-y",
                    "-f", "pulse", "-i", microphone,
                    "-f", "pulse", "-i", f"{SINK_NAME}.monitor",
                    "-filter_complex",
                    "[0:a]highpass=f=70,lowpass=f=7600[mic];"
                    "[mic][1:a]amix=inputs=2:duration=longest:normalize=0,"
                    "alimiter=limit=0.95,aresample=async=1",
                    "-ac", "1", "-ar", SAMPLE_RATE, "-c:a", "pcm_s16le",
                    "-flush_packets", "1", str(self.wav_path),
                ])
                self.started_at = time.time()
            except FileNotFoundError as exc:
                self._stop_locked()
                raise AppError(
                    "Missing ffmpeg. Install ffmpeg for recording and audio conversion."
                ) from exc
            except Exception:
                self._stop_locked()
                raise
        threading.Thread(target=self._watchdog, args=(self.started_at,), daemon=True).start()

    def _watchdog(self, started_at):
        """Auto-stop a recording that runs past MAX_RECORDING_SECONDS."""
        time.sleep(MAX_RECORDING_SECONDS)
        with self._lock:
            if self.started_at == started_at:
                self._stop_locked()

    def stop(self):
        """Flush ffmpeg, tear down pulse modules, return True if a wav exists."""
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self):
        """Caller holds the capture lock; notification cannot race a new capture."""
        was_recording = self.proc is not None
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait()
            self.proc = None
            self.started_at = None
        for key in ("loop2", "loop1", "sink"):
            if self.modules[key]:
                subprocess.run(["pactl", "unload-module", self.modules[key]], check=False)
                self.modules[key] = None
        if self.restore_profile:
            card, profile = self.restore_profile
            self.restore_profile = None
            if profile:
                subprocess.run(["pactl", "set-card-profile", card, profile], check=False)
        if was_recording and self.on_stop is not None:
            self.on_stop()
        return self.wav_path.exists()


recorder = Recorder()
