"""PulseAudio + ffmpeg capture of microphone and system output into one wav."""

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


def microphones():
    """List physical PulseAudio/PipeWire input sources for user selection."""
    try:
        default = pactl("get-default-source")
        sources = json.loads(pactl("-f", "json", "list", "sources"))
    except (AppError, json.JSONDecodeError, TypeError):
        return []
    devices = []
    for source in sources:
        name = str(source.get("name") or "")
        properties = source.get("properties") or {}
        if not name or name.endswith(".monitor") or properties.get("device.class") == "monitor":
            continue
        devices.append({
            "id": name,
            "label": source.get("description") or properties.get("device.description") or name,
            "default": name == default,
        })
    return devices


class Recorder:
    """Owns the null sink, the two loopbacks and the ffmpeg process."""

    def __init__(self, wav_path=WAV_PATH):
        self.wav_path = wav_path
        self.proc = None
        self.started_at = None
        self.modules = {"sink": None, "loop1": None, "loop2": None}
        self._lock = threading.Lock()
        # Called under the capture lock before another recording can start.
        # The callback only signals live transcription; it must not wait for ASR.
        self.on_stop = None

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
                self.modules["sink"] = pactl(
                    "load-module", "module-null-sink", f"sink_name={SINK_NAME}",
                    "sink_properties=device.description=MeetingRec",
                )
                self.modules["loop1"] = pactl(
                    "load-module", "module-loopback", f"source={microphone or '@DEFAULT_SOURCE@'}", f"sink={SINK_NAME}"
                )
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
                    "ffmpeg", "-y", "-f", "pulse", "-i", f"{SINK_NAME}.monitor",
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
        if was_recording and self.on_stop is not None:
            self.on_stop()
        return self.wav_path.exists()


recorder = Recorder()
