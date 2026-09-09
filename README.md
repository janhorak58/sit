# SIT — Smart Interactive Transcriber

Recordings, speaker-labeled transcripts, and summaries in one place. SIT accepts
audio/video files, and on a supported Linux audio server it records the
microphone together with system audio.

The FastAPI backend can be opened in a browser or in a native Tauri window.
Transcription uses a configured private ASR server, falling back to local
Canary when it is unavailable. Speaker recognition similarly uses remote or
local pyannote. Summaries require a separate OpenAI-compatible server; there
is no built-in local fallback for summaries. When using remote ASR/diarization,
audio is sent to that server; the transcript is sent to the summary server when
generating a summary. For purely local ASR, set `SPARK_WHISPER_URL=` and
`SPARK_DIARIZER_URL=`.

## Live transcription while recording

Before recording, pick a language and optionally turn off the **Live
transcription while recording** toggle — turning it off saves compute (the
model does not run continuously); the final saved transcript after stopping is
unaffected either way. The live preview starts after roughly two seconds of
captured audio plus the model's processing time; further updates arrive every
two seconds of new audio. It uses up to eight seconds of context for speech
continuity. First model load and slower hardware can extend the wait.
While recording, the input cards and toggle are hidden and replaced by a large
timed transcript with recording controls (if live transcription is on). The
newest segment is highlighted; reading older text does not auto-scroll.
Lag over five seconds shows the transcription status. No segments are skipped
due to slow processing.

This is a running preview that may change, without speaker recognition yet.
**Stop & Save** saves the audio and automatically starts a full transcription
including speaker recognition (unless the speaker count is set to 1). A
preview error does not end the recording. **Cancel** discards both the audio
and the preview. After an automatic stop at the time limit, the recording
remains ready to save.

Reloading the page restores the preview and language of a running recording.
The preview is not a separately saved transcript: it is lost on a backend
restart, while saved audio and finished transcripts remain. It uses the same
remote/local ASR settings as the full transcription.

## Platforms

| Environment | Status |
| --- | --- |
| Arch Linux + PipeWire-Pulse / PulseAudio | Development environment; locally verified launch and backend |
| Ubuntu 24.04 / Debian 12 and newer | Documented install steps; running on these distros not yet verified |
| WSL2 + WSLg | Launcher does not require systemd; target environment not yet verified, audio limits below |
| Native Windows / macOS | Not yet supported by the desktop installer or the recorder |

This is a **source install**, not a standalone distribution package. The
desktop launcher needs a preserved checkout, a Python environment, and a built
binary. No system services or Arch-specific paths are required to run it.

## Installation

The recommended Python is **3.12** (alternatively 3.11); the latest system
Python on a rolling-release distro may not have ML wheels available. Python
can be managed with `uv`. Installing from this private GitHub repository
requires access.

### Ubuntu / Debian — system dependencies

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv ffmpeg pulseaudio-utils

# Only for the native window; the browser needs no build dependencies.
sudo apt install -y build-essential pkg-config libwebkit2gtk-4.1-dev \
  libssl-dev libxdo-dev libayatana-appindicator3-dev librsvg2-dev
```

A regular desktop needs PulseAudio or PipeWire with a compatible
PipeWire-Pulse service running. `pulseaudio-utils` ships the `pactl` client,
not an audio server.

### Arch Linux — system dependencies

```bash
sudo pacman -S --needed git curl python uv ffmpeg libpulse

# Only for the native window:
sudo pacman -S --needed base-devel pkgconf webkit2gtk-4.1 openssl \
  libappindicator-gtk3 librsvg xdotool rustup
rustup default stable
```

Use your existing PipeWire-Pulse or PulseAudio; **do not replace your audio
server for this app**. `libpulse` provides `pactl` even when using PipeWire.

### Common steps — Python and checkout

If `uv` is not installed, install it following the
[official instructions](https://docs.astral.sh/uv/getting-started/installation/),
e.g. `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new terminal.

```bash
git clone https://github.com/janhorak58/sit.git
cd sit
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Alternative with a system Python 3.11/3.12:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Installing the ML libraries and the first model download need a network
connection and free disk space. No GPU is required; the default local ASR
uses the CPU. `HF_TOKEN` is needed for gated speaker-recognition models, not
for simply opening the app or transcribing.

### Option A — app in the browser

```bash
.venv/bin/python -m transcriber
```

Open <http://127.0.0.1:47831>. The backend runs until the command is
terminated with Ctrl+C. Data is stored by default in `~/.local/share/sit`
(respects `XDG_DATA_HOME`).

### Option B — native desktop window

Install stable Rust via [rustup](https://rustup.rs/) (see above for Arch).
After the rustup script install, load `source "$HOME/.cargo/env"`.
System build dependencies are listed above;
[official Tauri prerequisites](https://v2.tauri.app/start/prerequisites/).

```bash
cargo build --release --manifest-path src-tauri/Cargo.toml
python3 scripts/install-desktop.py
```

**SIT** will appear in your application menu. You can also launch it from a
terminal by absolute path:

```bash
~/.local/bin/sit
```

The installer does not require sudo. It creates:

- `~/.local/bin/sit` — a launcher with an absolute path to the checkout and `.venv/bin/python`;
- `${XDG_DATA_HOME:-~/.local/share}/applications/sit.desktop` — a menu entry;
- `${XDG_DATA_HOME:-~/.local/share}/icons/sit.png` — an icon.

Reinstalling updates its own files; it refuses to change a foreign file or
symlink at any of the target locations. After moving the checkout, run the
installer again. Updating the Rust code requires a fresh
`cargo build --release`; the launcher uses the binary directly from the
checkout. Web/Python changes take effect after a backend restart.

The launcher reuses a backend already running on `127.0.0.1:47831`. On Linux
it may also try to start an already-installed `transcriber.service`. If the
service is unavailable, it starts Python directly — **systemd is not
required**, not even under WSL.
Before opening the window it waits for the API to respond; a broken install is
reported on stderr. If something goes wrong, run `~/.local/bin/sit` in a
terminal.

**Closing the window stops the backend the window itself started.** Finish
saving a recording and processing first; an in-progress transcription is
interrupted on shutdown. An active recorder is stopped on a clean backend
shutdown, and the temporary WAV stays in `_scratch` (this does not replace the
button for saving a recording).
A manually started or systemd-managed backend is not stopped by the window.
If jobs need to keep running after the window closes, start the backend ahead
of time using Option A.

Remove the desktop integration (does not delete data, the checkout, or `.venv`):

```bash
python3 scripts/install-desktop.py --uninstall
```

### WSL2 with WSLg

In PowerShell on Windows 11, verify/update WSL:

```powershell
wsl --version
wsl --update
```

Inside Ubuntu under WSL, follow the Linux installation above. We recommend
checking out in the Linux filesystem, e.g. `~/sit`, not `/mnt/c/...`, for
performance and permissions. WSLg provides the graphical environment and
audio bridge; do not manually set `DISPLAY` or `PULSE_SERVER` if WSLg already
set them. Do not start an additional PulseAudio server on top of WSLg for
this app. See [Linux GUI apps in WSL](https://learn.microsoft.com/windows/wsl/tutorials/gui-apps).

**Windows microphone and audio are not the same thing.** The recorder needs
the PulseAudio modules `module-null-sink`, `module-loopback`, and an output
monitor. WSLg may not provide them; Windows system audio cannot be guaranteed
by this implementation. On error, use **Upload File**: record in Windows and
upload it through the UI. Upload uses `ffmpeg`, not PulseAudio. WSLg support
is not the same as native Windows build support.

## Data and configuration

An optional `.env` at the checkout root is git-ignored; never commit tokens to
the repository. For an existing library, set for example
`TRANSCRIBER_DATA_DIR=/absolute/path/to/library`.
**An existing `/data` is not moved automatically:** if you already use it
outside Docker, keep `TRANSCRIBER_DATA_DIR=/data` in the environment or `.env`.

### Migrating from SHIT

The renamed app uses `~/.local/share/sit` by default. Existing recordings
remain in `~/.local/share/shit`; either move that directory to the new location
or set `TRANSCRIBER_DATA_DIR=~/.local/share/shit` before starting SIT.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBER_DATA_DIR` | `${XDG_DATA_HOME:-~/.local/share}/sit`; Docker `/data` | Library root; an explicit value takes precedence, `~` is expanded |
| `TRANSCRIBER_HOST` / `TRANSCRIBER_PORT` | `127.0.0.1` / `47831` | Backend address for manual runs |
| `HF_TOKEN` | empty | Access to gated pyannote models |
| `LOCAL_ASR_MODEL` | `nemo-canary-1b-v2` | Local ONNX ASR model (`onnx-asr`); `nemo-parakeet-tdt-0.6b-v3` is faster but ignores the language choice |
| `LOCAL_ASR_PATH` | empty | Path to an offline model copy; empty = HuggingFace cache |
| `LOCAL_ASR_QUANTIZATION` | `int8` | ONNX weight quantization; empty = full precision |
| `LOCAL_ASR_DEVICE` | `cpu` | `cpu` or `cuda` (requires `onnxruntime-gpu`) |
| `LOCAL_ASR_VAD_MODEL` | `silero` | VAD that cuts audio into windows before recognition |
| `LOCAL_ASR_WINDOW_SECONDS` | `30` | Max window length sent to the model |
| `LOCAL_ASR_DEFAULT_LANGUAGE` | `cs` | Language pinned when a recording carries no explicit one |
| `DIARIZE_MODEL` | `pyannote/speaker-diarization-3.1` | Local diarization |
| `SPARK_WHISPER_URL` | `http://127.0.0.1:8204/v1/audio/transcriptions` | Remote ASR; empty = local. For the `canary/` service, port 8208 |
| `SPARK_WHISPER_MODEL` | `large-v3` | Remote model; for the `canary/` service, `nemo-canary-1b-v2` |
| `SPARK_DIARIZER_URL` | `http://127.0.0.1:8000/v1/audio/diarizations` | Remote diarization; empty = local |
| `OMNIROUTE_URL` / `OMNIROUTE_MODEL` | `http://127.0.0.1:20128` / `cc/claude-sonnet-5` | Server and model for summaries; must be set to your own server |
| `GPT_OSS_API_KEY` | empty | Authorization for the summary server |
| `MAX_RECORDING_SECONDS` | `10800` | Maximum recording length in seconds |

Local pyannote needs you to accept the model terms at
[speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
[segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
The remote model servers are not part of the running desktop app; the Spark
diarization service is documented at [diarizer/README.md](diarizer/README.md),
and the Canary ASR service at [canary/README.md](canary/README.md).

The desktop launcher sets `TRANSCRIBER_PROJECT_DIR` and `TRANSCRIBER_PYTHON`
to absolute paths. When running the binary yourself, set them in the
environment. Without them it looks for the checkout at the build location and
Python at its `.venv/bin/python`. The desktop always uses
`127.0.0.1:47831`; open a different backend port in the browser instead.
A relative `XDG_DATA_HOME` is ignored per the XDG spec.

Transcripts: `<folder>/<name>.txt`; structured data: `<name>.meeting.json`;
recordings: `<folder>/audio/<name>.wav`; summaries: `<name>.summary.md` /
`.summary.json`. Older audio directly in a folder remains readable. Working
files live in `_scratch/`.

### Recording and the optional file-manager integration

`pactl info` must see the audio server for the user the backend runs as.
Missing `pactl`, `ffmpeg`, or rejected PulseAudio modules return an error in
the UI; a missing audio server does not block library browsing or file
upload.

The **Open on Disk** button currently uses a separate helper,
`transcriber-opener`, on `127.0.0.1:47833`. This helper is not part of the
repository or the desktop installer; without it, the button reports an error.
No other feature needs it — you can open the library manually in its data
directory.

## Docker

```bash
docker build -t transcriber .
docker run --rm -p 127.0.0.1:47831:47831 -v "$PWD/data:/data" transcriber
```

The image explicitly uses `/data` and binds `0.0.0.0` inside the container.
The example publishes the port locally only. Upload does not need an audio
socket; direct recording does (the host's PulseAudio socket, permissions, and
`PULSE_SERVER`). The desktop installer does not install a container.
Configuration can be passed via `--env-file .env`.

**The API has no authentication.** Do not expose it publicly. For LAN access
you must deliberately change the bind/port mapping and secure access; the
default local bind is intentional.

## Development and API

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest tests
node tests/group_segments.mjs
```

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | UI |
| POST | `/start`, `/stop`, `/recording/cancel` | Recording lifecycle |
| GET | `/recording/status`, `/progress` | Recording / processing status |
| POST | `/upload` | Raw audio/video upload and conversion via ffmpeg |
| POST | `/transcribe`, `/diarize` | Transcription or a fresh speaker-recognition pass |
| POST | `/summaries` | Summary |
| GET | `/asr/status`, `/asr/diagnostics` | Model availability and diagnostics |
| GET | `/projects`, `/library/browse` | Library browsing |
| POST | `/projects/suggest-folder` | Folder suggestion |
| GET | `/library/file`, `/library/meeting`, `/library/summary-json`, `/library/audio` | Outputs and audio |
| POST | `/library/mkdir`, `/library/move`, `/library/delete` | Library edits |
| POST | `/library/folder/rename`, `/library/folder/delete`, `/library/rename-speaker` | Renames and deletes |
| GET | `/download` | Text output download |

`POST /start` accepts an optional `{"language":"cs","live":true}` (`"en"` for
English, `""` for automatic detection; `"live":false` starts recording but
runs no live transcription at all — useful to save compute).
`/recording/status` also returns `available` for an unsaved stopped
recording, and `live`: a session id (`null` if live transcription is off),
state, language, timed preview segments, audio length, processed length, and
any error.

Expected application errors return HTTP 200 with `{"error": "..."}`; the UI
checks this field. API validation errors may return other HTTP statuses.
