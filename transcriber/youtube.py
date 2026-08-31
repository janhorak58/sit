"""yt-dlp audio extraction into the scratch wav."""

import subprocess

from .config import SAMPLE_RATE, SCRATCH_DIR, WAV_PATH
from .errors import AppError
from .paths import sanitize_component


def clear_scratch():
    for p in SCRATCH_DIR.glob("current.*"):
        p.unlink()


def download_audio(url):
    """Fetch ``url`` as a mono 16 kHz wav at WAV_PATH."""
    clear_scratch()
    result = subprocess.run([
        "yt-dlp", "-x", "--audio-format", "wav",
        "--postprocessor-args", f"-ar {SAMPLE_RATE} -ac 1",
        "-o", str(SCRATCH_DIR / "current.%(ext)s"),
        url,
    ], capture_output=True, text=True)
    if result.returncode != 0 or not WAV_PATH.exists():
        raise AppError(result.stderr[-2000:] or "stažení selhalo")
    return WAV_PATH


def fetch_title(url):
    """Video title sanitized for use as a filename; empty string on failure."""
    result = subprocess.run(
        ["yt-dlp", "--print", "%(title)s", "--skip-download", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    return sanitize_component(result.stdout.strip(), "")
