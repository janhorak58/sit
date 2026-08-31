"""PulseAudio + ffmpeg capture of microphone and system output into one wav."""

import signal
import subprocess

from .config import SAMPLE_RATE, SINK_NAME, WAV_PATH


def pactl(*args):
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class Recorder:
    """Owns the null sink, the two loopbacks and the ffmpeg process."""

    def __init__(self, wav_path=WAV_PATH):
        self.wav_path = wav_path
        self.proc = None
        self.modules = {"sink": None, "loop1": None, "loop2": None}

    @property
    def is_recording(self):
        return self.proc is not None

    def start(self):
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

    def stop(self):
        """Flush ffmpeg, tear down pulse modules, return True if a wav exists."""
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait()
            self.proc = None
        for key in ("loop2", "loop1", "sink"):
            if self.modules[key]:
                subprocess.run(["pactl", "unload-module", self.modules[key]], check=False)
                self.modules[key] = None
        return self.wav_path.exists()


recorder = Recorder()
