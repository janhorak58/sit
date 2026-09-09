"""Single source of truth for paths, models and tunables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
if not _data_home.is_absolute():
    _data_home = Path.home() / ".local" / "share"
DATA_DIR = Path(
    os.environ.get("TRANSCRIBER_DATA_DIR") or _data_home / "shit"
).expanduser().resolve()
SCRATCH_DIR = DATA_DIR / "_scratch"
WAV_PATH = SCRATCH_DIR / "current.wav"

AUDIO_SUBDIR = "audio"
INTERNAL_TOP_DIRS = {"_scratch", "nltk_data", ".cache"}

HF_TOKEN = os.environ.get("HF_TOKEN", "")
# Canary, not Parakeet: Parakeet-TDT runs language identification per VAD window
# and ignores a requested language, so Czech recordings drift into Slovak and
# English mid-transcript. Canary takes the language as an input.
LOCAL_ASR_MODEL = os.environ.get("LOCAL_ASR_MODEL", "nemo-canary-1b-v2")
# Empty = resolve from HuggingFace cache; set to a directory for an offline copy.
LOCAL_ASR_PATH = os.environ.get("LOCAL_ASR_PATH", "")
# int8 halves RAM and roughly doubles CPU speed at a small accuracy cost;
# empty string selects the full-precision weights.
LOCAL_ASR_QUANTIZATION = os.environ.get("LOCAL_ASR_QUANTIZATION", "int8")
LOCAL_ASR_DEVICE = os.environ.get("LOCAL_ASR_DEVICE", "cpu")
LOCAL_ASR_VAD_MODEL = os.environ.get("LOCAL_ASR_VAD_MODEL", "silero")
# Attention decoding drifts on long windows, so VAD cuts speech into windows
# this long before recognition.
LOCAL_ASR_WINDOW_SECONDS = float(os.environ.get("LOCAL_ASR_WINDOW_SECONDS", "30"))
# Used when a recording carries no explicit language; never leave it unset,
# or the model falls back to English on Czech audio.
LOCAL_ASR_DEFAULT_LANGUAGE = os.environ.get("LOCAL_ASR_DEFAULT_LANGUAGE", "cs")
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

HOST = os.environ.get("TRANSCRIBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRANSCRIBER_PORT", "47831"))

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"
INDEX_HTML = WEB_DIR / "index.html"


def ensure_dirs():
    """Create the writable directories the app assumes exist."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
