# ŠIT — ŠIKOVNÝ INTERAKTIVNÍ TRANSKRIPTOR

Local meeting/audio transcriber. Records microphone + system audio or accepts
uploaded audio/video, tries an OpenAI-compatible work Spark ASR service, and
falls back automatically to local faster-whisper. Speaker recognition tries the
private Spark pyannote service first and falls back to local pyannote. Markdown
summaries use a private omniroute endpoint. Audio leaves the machine only for
these configured private services; internal endpoint URLs stay server-side.

## Layout

```
transcriber/
  app.py             FastAPI factory: static mount, routers, error handler
  __main__.py        `python -m transcriber` dev entrypoint
  config.py          paths, model names, env-tunable settings
  paths.py           sanitization; every browser-supplied path stays in DATA_DIR
  errors.py          AppError -> {"error": ...} response
  schemas.py         pydantic request bodies
  asr.py             remote-first ASR with local faster-whisper fallback
  summary.py         private omniroute summary client
  models.py          lazy faster-whisper / pyannote loaders
  progress.py        thread-safe state of the in-flight job
  pipeline.py        ASR -> diarization -> transcript file
  recorder.py        PulseAudio null sink + loopbacks + ffmpeg capture
  library.py         browse / store / move / delete under DATA_DIR
  routes/            pages, workflow, recording, transcription, library
  web/               index.html + static/styles.css + static/js/*.js
```

Data layout in `DATA_DIR`: transcripts at `<folder>/<name>.txt`, recordings at
`<folder>/audio/<name>.wav`, scratch capture in `_scratch/`.
Summaries are stored as `<folder>/<name>.summary.md`. Legacy recordings at
`<folder>/<name>.wav` remain readable.


## Run

Docker (expects `/data` mounted and a PulseAudio socket for recording):

```
docker build -t transcriber .
docker run --rm -p 47831:47831 -v "$PWD/data:/data" -e HF_TOKEN=... transcriber
```

Locally:

```
pip install -r requirements.txt
TRANSCRIBER_DATA_DIR=./data HF_TOKEN=... python -m transcriber
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBER_DATA_DIR` | `/data` | Library root |
| `TRANSCRIBER_HOST` / `TRANSCRIBER_PORT` | `0.0.0.0` / `47831` | Dev server bind |
| `HF_TOKEN` | — | Hugging Face token for the pyannote pipeline |
| `WHISPER_MODEL` | `small` | faster-whisper model size |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` | Inference backend |
| `DIARIZE_MODEL` | `pyannote/speaker-diarization-3.1` | Local fallback diarization model |
| `SPARK_WHISPER_URL` | `http://127.0.0.1:8204/v1/audio/transcriptions` | OpenAI-compatible work ASR endpoint; empty or unavailable uses local ASR |
| `SPARK_WHISPER_MODEL` | `large-v3` | Remote ASR model |
| `SPARK_DIARIZER_URL` | `http://127.0.0.1:8000/v1/audio/diarizations` | Private Spark pyannote endpoint; empty or unavailable uses local pyannote |
| `OMNIROUTE_URL` | `http://127.0.0.1:20128` | Private OpenAI-compatible summary endpoint |
| `OMNIROUTE_MODEL` | `cc/claude-sonnet-5` | Summary model |
| `GPT_OSS_API_KEY` | — | Bearer token for an authenticated OpenAI-compatible summary endpoint |
| `MAX_RECORDING_SECONDS` | `10800` (3 h) | Auto-stop a recording after this many seconds |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | UI |
| `POST` | `/start`, `/stop` | Recording lifecycle; stop files the wav; auto-stops after `MAX_RECORDING_SECONDS` |
| `POST` | `/recording/cancel` | Stop the in-progress recording and discard its audio |
| `GET` | `/recording/status` | Poll whether a recording is in progress, its start time, and the configured limit |
| `POST` | `/transcribe`, `/diarize` | Start a full transcription or rerun speaker recognition on stored segments |
| `GET` | `/progress` | Poll the running job |
| `GET` | `/asr/status` | Public ASR availability and label; never exposes an endpoint URL |
| `GET` | `/projects` | Top-level project folders |
| `POST` | `/projects/suggest-folder` | Suggest a sanitized folder for a project |
| `POST` | `/summaries` | Generate and store a Markdown summary beside a transcript |
| `GET` | `/library/browse`, `/library/file` | Listing and transcript text |
| `POST` | `/library/mkdir`, `/library/move`, `/library/delete` | Recording and file mutations |
| `POST` | `/library/folder/rename`, `/library/folder/delete` | Rename or recursively delete a library folder |
| `GET` | `/download` | Transcript as an attachment |

Recoverable failures return HTTP 200 with `{"error": "..."}` — the UI checks
that field.
