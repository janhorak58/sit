"""Single source of truth for paths, models and tunables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("TRANSCRIBER_DATA_DIR", "/data"))
SCRATCH_DIR = DATA_DIR / "_scratch"
WAV_PATH = SCRATCH_DIR / "current.wav"

AUDIO_SUBDIR = "audio"
INTERNAL_TOP_DIRS = {"_scratch", "nltk_data", ".cache"}

HF_TOKEN = os.environ.get("HF_TOKEN", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
DIARIZE_MODEL = os.environ.get("DIARIZE_MODEL", "pyannote/speaker-diarization-3.1")
SPARK_DIARIZER_URL = os.environ.get(
    "SPARK_DIARIZER_URL", "http://127.0.0.1:8000/v1/audio/diarizations"
)
SPARK_WHISPER_URL = os.environ.get(
    "SPARK_WHISPER_URL", "http://127.0.0.1:8204/v1/audio/transcriptions"
)
SPARK_WHISPER_MODEL = os.environ.get("SPARK_WHISPER_MODEL", "large-v3")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128")
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "cc/claude-sonnet-5")
GPT_OSS_API_KEY = os.environ.get("GPT_OSS_API_KEY", "")
MAX_RECORDING_SECONDS = int(os.environ.get("MAX_RECORDING_SECONDS", str(3 * 3600)))

SINK_NAME = "meeting_rec"
SAMPLE_RATE = "16000"

HOST = os.environ.get("TRANSCRIBER_HOST", "0.0.0.0")
PORT = int(os.environ.get("TRANSCRIBER_PORT", "47831"))

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"
INDEX_HTML = WEB_DIR / "index.html"


def ensure_dirs():
    """Create the writable directories the app assumes exist."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
