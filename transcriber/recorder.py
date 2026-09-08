"""PulseAudio + ffmpeg capture of microphone and system output into one wav."""

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
            "Chybí pactl. Nainstaluj pulseaudio-utils (Ubuntu/Debian) nebo "
            "libpulse (Arch). Hotový audiosoubor můžeš nahrát i bez něj."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AppError(
            "Zvukový server nepodporuje požadované nahrávání nebo není dostupný. "
            "Zkontroluj PulseAudio / PipeWire-Pulse a přístup k mikrofonu. "
            "WSLg nemusí podporovat loopback zvuku Windows; použij nahrání souboru. "
            f"Detail: {(exc.stderr or '').strip()}"
        ) from exc


class Recorder:
    """Owns the null sink, the two loopbacks and the ffmpeg process."""

    def __init__(self, wav_path=WAV_PATH):
        self.wav_path = wav_path
        self.proc = None
        self.started_at = None
        self.modules = {"sink": None, "loop1": None, "loop2": None}
        self._lock = threading.Lock()

    @property
    def is_recording(self):
        return self.proc is not None

    def start(self):
        with self._lock:
            if self.is_recording:
                raise AppError("Nahrávání už běží.")
            if not sys.platform.startswith("linux"):
                raise AppError(
                    "Přímé nahrávání vyžaduje Linux s PulseAudio / PipeWire-Pulse. "
                    "Na této platformě použij nahrání audiosouboru."
                )
            try:
                self.modules["sink"] = pactl(
                    "load-module", "module-null-sink", f"sink_name={SINK_NAME}",
                    "sink_properties=device.description=MeetingRec",
                )
                self.modules["loop1"] = pactl(
                    "load-module", "module-loopback", "source=@DEFAULT_SOURCE@", f"sink={SINK_NAME}"
                )
                self.modules["loop2"] = pactl(
                    "load-module", "module-loopback",
                    "source=@DEFAULT_SINK@.monitor", f"sink={SINK_NAME}",
                )
                self.proc = subprocess.Popen([
                    "ffmpeg", "-y", "-f", "pulse", "-i", f"{SINK_NAME}.monitor",
                    "-ac", "1", "-ar", SAMPLE_RATE, str(self.wav_path),
                ])
                self.started_at = time.time()
            except FileNotFoundError as exc:
                self._stop_locked()
                raise AppError(
                    "Chybí ffmpeg. Nainstaluj ffmpeg pro nahrávání a převod audia."
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
        """``stop()`` body; caller must hold ``self._lock``."""
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait()
            self.proc = None
            self.started_at = None
        for key in ("loop2", "loop1", "sink"):
            if self.modules[key]:
                subprocess.run(["pactl", "unload-module", self.modules[key]], check=False)
                self.modules[key] = None
        return self.wav_path.exists()


recorder = Recorder()
