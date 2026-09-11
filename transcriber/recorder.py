"""PulseAudio + ffmpeg capture of microphone and system output into one wav."""

import array
import json
import logging
import signal
import subprocess
import sys
import threading
import time

from .errors import AppError

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, SINK_NAME, WAV_PATH

log = logging.getLogger(__name__)

# How long a stopping capture may take to flush and exit before it is killed.
STOP_TIMEOUT = 5.0


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


# A Bluetooth microphone forces the card into HFP, whose 8/16 kHz narrowband
# codec applies to playback as well. Both the recorded room and the loopback
# copy of system audio lose their high end, so the transcript gets worse for
# a convenience the user rarely intended.
BLUETOOTH_NOTE = (
    "Bluetooth headset microphone — switches the headset to narrowband HFP "
    "and lowers audio quality on both tracks. Prefer a built-in or USB microphone."
)
NEEDS_PROFILE_NOTE = (
    "No microphone in the card's current mode. Switch the device to headset "
    "mode in the system sound settings first."
)


def _is_bluetooth(name, properties):
    return name.startswith("bluez") or (properties.get("device.bus") or "") == "bluetooth"


def microphones():
    """List physical input sources, flagging the ones that record badly or not at all."""
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
        bluetooth = _is_bluetooth(name, properties)
        if not ready:
            note = NEEDS_PROFILE_NOTE if profile else "This device has no microphone in any profile."
        else:
            note = BLUETOOTH_NOTE if bluetooth else None
        devices.append({
            "id": name,
            "label": source.get("description") or properties.get("device.description") or name,
            "default": name == default,
            # Only a source that captures right now is usable: the recorder no
            # longer switches card profiles behind the user's back.
            "available": bool(ready),
            "bluetooth": bluetooth,
            "note": note,
        })
    return devices


def outputs():
    """Playback sinks whose monitor can be recorded as the system-audio track."""
    try:
        default = pactl("get-default-sink")
    except AppError:
        return []
    devices = []
    for sink in _pactl_list("list", "sinks"):
        name = str(sink.get("name") or "")
        if not name:
            continue
        properties = sink.get("properties") or {}
        devices.append({
            "id": name,
            "label": sink.get("description") or properties.get("device.description") or name,
            "default": name == default,
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
        # Set when the current capture stops, so its watchdog can retire.
        self._stopped = None
        self._lock = threading.Lock()
        # Called under the capture lock before another recording can start.
        # The callback only signals live transcription; it must not wait for ASR.
        self.on_stop = None

    def _prepare_source(self, microphone):
        """Validate the chosen input source and return it.

        Card profiles are never switched here. Forcing a Bluetooth headset
        into HFP to get a microphone also drops playback to narrowband, so the
        recording lost quality on both tracks for a mode the user never asked
        for. A device without a capture profile now reports what to change.
        """
        microphone = microphone or pactl("get-default-source")
        if not microphone:
            raise AppError("No microphone is selected. Connect one and try again.")
        device = next((mic for mic in microphones() if mic["id"] == microphone), None)
        if device is None:
            raise AppError(
                "The selected microphone is not available. Reconnect it or pick another one."
            )
        if not device["available"]:
            raise AppError(f"{device['label']}: {device['note']}")
        return microphone

    def _prepare_monitor(self, output):
        """Monitor source of the sink whose playback belongs on the system track."""
        if not output:
            return "@DEFAULT_SINK@.monitor"
        if not any(sink["id"] == output for sink in outputs()):
            raise AppError(
                "The selected output is not available. Reconnect it or pick another one."
            )
        return f"{output}.monitor"

    @property
    def is_recording(self):
        return self.proc is not None

    def start(self, microphone="", output=""):
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
                monitor = self._prepare_monitor(output)
                self.modules["sink"] = pactl(
                    "load-module", "module-null-sink", f"sink_name={SINK_NAME}",
                    "sink_properties=device.description=MeetingRec",
                )
                # The microphone goes directly to ffmpeg; only playback takes
                # the loopback detour, from whichever sink the user is
                # actually listening on rather than always the default one.
                self.modules["loop2"] = pactl(
                    "load-module", "module-loopback",
                    f"source={monitor}", f"sink={SINK_NAME}",
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
                    # Two tracks, never one sum: the loopback is a clean digital
                    # copy of whatever plays on this machine, while the mic adds
                    # room reverb and noise. Mixing them buried the clean signal
                    # under the room; kept apart, each stream is transcribed on
                    # its own and the channel already tells the speakers apart.
                    "-filter_complex",
                    "[0:a]highpass=f=70,lowpass=f=7600,"
                    "aformat=channel_layouts=mono,alimiter=limit=0.95[mic];"
                    "[1:a]aformat=channel_layouts=mono,alimiter=limit=0.95[sys];"
                    "[mic][sys]amerge=inputs=2,aresample=async=1",
                    "-ac", "2", "-ar", SAMPLE_RATE, "-c:a", "pcm_s16le",
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
        self._stopped = threading.Event()
        threading.Thread(
            target=self._watchdog, args=(self.started_at, self._stopped), daemon=True
        ).start()

    def _watchdog(self, started_at, stopped):
        """Auto-stop a recording that runs past MAX_RECORDING_SECONDS.

        Waits on the event rather than sleeping the full limit, so stopping a
        recording retires its watchdog instead of parking a thread for hours.
        """
        if stopped.wait(MAX_RECORDING_SECONDS):
            return
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
        if self._stopped is not None:
            self._stopped.set()
        if self.proc:
            # An input that stopped producing frames (headset unplugged,
            # pipewire restarted) leaves ffmpeg deaf to SIGINT; waiting for it
            # unbounded would wedge stop, start and cancel behind this lock.
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                log.warning("Capture ffmpeg ignored SIGINT after %ss; killing it", STOP_TIMEOUT)
                self.proc.kill()
                try:
                    self.proc.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    log.error("Capture ffmpeg survived SIGKILL; abandoning the process")
            self.proc = None
            self.started_at = None
        for key in ("loop2", "loop1", "sink"):
            if self.modules[key]:
                subprocess.run(["pactl", "unload-module", self.modules[key]], check=False)
                self.modules[key] = None
        if was_recording and self.on_stop is not None:
            self.on_stop()
        return self.wav_path.exists()


recorder = Recorder()
