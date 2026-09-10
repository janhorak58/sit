<div align="center">

<img src="src-tauri/icons/icon.png" alt="SIT logo" width="120">

# SIT — Smart Interactive Transcriber

**Version 0.1.0 — alpha**

![status](https://img.shields.io/badge/status-alpha-orange)
![version](https://img.shields.io/badge/version-0.1.0-blue)
![platform](https://img.shields.io/badge/platform-Linux%20%7C%20WSL2-lightgrey)
![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)

Recordings, speaker-labeled transcripts, and summaries in one place.

</div>

> **Alpha software.** Interfaces, defaults and the on-disk layout can still
> change between commits, and only the Arch Linux path is verified end to end
> (see [Platforms](#platforms)). It is a source install, not a packaged
> release. Keep your own backups of the library directory.

SIT accepts audio/video files, and on a supported Linux audio server it records
the microphone together with system audio.

The FastAPI backend can be opened in a browser or in a native Tauri window.
Transcription uses a configured private ASR server, falling back to local
Canary (CPU, ONNX) when it is unavailable — that fallback is part of the
default install. Speaker recognition uses a remote diarizer; the local
pyannote fallback is an **optional extra** (the only component that needs
torch), see [Setup B](#setup-b--local-fallback-offline-capable). Summaries
require a separate OpenAI-compatible server; there is no built-in local
fallback for summaries. When using remote ASR/diarization, audio is sent to
that server; the transcript is sent to the summary server when generating a
summary. All of this is configured in the UI panel **Connections**; for purely
local ASR, clear the transcription and diarization local ports there.

## Live transcription while recording

Before recording, pick a language and optionally turn off the **Live
transcription while recording** toggle — turning it off saves compute (the
model does not run continuously); the final saved transcript after stopping is
unaffected either way. The live preview starts after roughly ten seconds of
captured audio plus the model's processing time; further updates arrive after
ten seconds of new audio, with four seconds of prior context for continuity.
First model load and slower hardware can extend the wait.
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
| Fedora 40 and newer | Documented install steps; running on these distros not yet verified |
| Ubuntu 24.04 / Debian 12 and newer | Documented install steps; running on these distros not yet verified |
| WSL2 + WSLg (Ubuntu) | Launcher does not require systemd; target environment not yet verified, audio limits below |
| Native Windows / macOS | Not yet supported by the desktop installer or the recorder |

This is a **source install**, not a standalone distribution package. The
desktop launcher needs a preserved checkout, a Python environment, and a built
binary. No system services or Arch-specific paths are required to run it.

## Installation

Three steps, in order:

1. **System dependencies** — pick your platform: [WSL2](#1-wsl2-with-wslg),
   [Fedora](#2-fedora), [Ubuntu / Debian](#3-ubuntu--debian),
   [Arch Linux](#4-arch-linux).
2. **Checkout and Python environment** — the same everywhere:
   [Common steps](#common-steps--checkout-and-python).
3. **Pick a setup** — [Remote setup](#setup-a--remote-recommended) (default,
   small) or [Local fallback setup](#setup-b--local-fallback-offline-capable).

The recommended Python is **3.12** (alternatively 3.11); the latest system
Python on a rolling-release distro may not have ML wheels available. Python
can be managed with `uv`. Installing from this private GitHub repository
requires access.

In each platform block, the first command is all the browser app needs. The
second block is only for the native Tauri window.

### 1. WSL2 with WSLg

In PowerShell on Windows 11, verify/update WSL first:

```powershell
wsl --version
wsl --update
```

Then, inside the Ubuntu distribution (WSL ships Ubuntu by default):

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv ffmpeg pulseaudio-utils

# Only for the native window; the browser needs no build dependencies.
sudo apt install -y build-essential pkg-config libwebkit2gtk-4.1-dev \
  libssl-dev libxdo-dev libayatana-appindicator3-dev librsvg2-dev
```

Check out in the Linux filesystem, e.g. `~/sit`, not `/mnt/c/...`, for
performance and permissions. WSLg provides the graphical environment and the
audio bridge; do not set `DISPLAY` or `PULSE_SERVER` manually if WSLg already
set them, and do not start an additional PulseAudio server on top of WSLg.
See [Linux GUI apps in WSL](https://learn.microsoft.com/windows/wsl/tutorials/gui-apps).

**Windows microphone and system audio are not the same thing.** The recorder
needs the PulseAudio modules `module-null-sink`, `module-loopback`, and an
output monitor. WSLg may not provide them; Windows system audio cannot be
guaranteed by this implementation. On error, use **Upload File**: record in
Windows and upload it through the UI. Upload uses `ffmpeg`, not PulseAudio.
WSLg support is not the same as native Windows build support.

### 2. Fedora

```bash
sudo dnf install -y git curl python3 python3-pip ffmpeg-free pulseaudio-utils

# Only for the native window:
sudo dnf install -y @development-tools pkgconf-pkg-config webkit2gtk4.1-devel \
  openssl-devel xdotool libappindicator-gtk3-devel librsvg2-devel rust cargo
```

`ffmpeg-free` comes from the Fedora repositories; if you already use the
RPM Fusion `ffmpeg` package, keep it instead. Fedora Workstation runs
PipeWire with PipeWire-Pulse, which is what the recorder needs;
`pulseaudio-utils` only ships the `pactl` client, not an audio server.

### 3. Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv ffmpeg pulseaudio-utils

# Only for the native window:
sudo apt install -y build-essential pkg-config libwebkit2gtk-4.1-dev \
  libssl-dev libxdo-dev libayatana-appindicator3-dev librsvg2-dev
```

A regular desktop needs PulseAudio or PipeWire with a compatible
PipeWire-Pulse service running. `pulseaudio-utils` ships the `pactl` client,
not an audio server. On Debian 12 the Tauri webkit package is
`libwebkit2gtk-4.1-dev` as well; older releases without it cannot build the
native window — use the browser instead.

### 4. Arch Linux

```bash
sudo pacman -S --needed git curl python uv ffmpeg libpulse

# Only for the native window:
sudo pacman -S --needed base-devel pkgconf webkit2gtk-4.1 openssl \
  libappindicator-gtk3 librsvg xdotool rustup
rustup default stable
```

Use your existing PipeWire-Pulse or PulseAudio; **do not replace your audio
server for this app**. `libpulse` provides `pactl` even when using PipeWire.

### Common steps — checkout and Python

If `uv` is not installed, install it following the
[official instructions](https://docs.astral.sh/uv/getting-started/installation/),
e.g. `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new terminal.

```bash
git clone https://github.com/janhorak58/sit.git
cd sit
uv venv --python 3.12 .venv
```

Without `uv`, using a system Python 3.11/3.12: `python3 -m venv .venv`, and
read `uv pip install --python .venv/bin/python` as
`.venv/bin/python -m pip install` in the commands below.

Now choose one of the two setups.

### Setup A — remote (recommended)

Transcription and speaker recognition run on your ASR/diarization servers
(configured under **Connections**), with local ONNX ASR as the fallback when
the remote endpoint is unreachable. **No torch, no CUDA libraries.**

```bash
uv pip install --python .venv/bin/python -r requirements.txt
```

- Install size: **~175 MB** for the whole `.venv` (measured, Python 3.11).
- Local ASR still works offline: `onnx-asr` + `onnxruntime` on the CPU, model
  `nemo-canary-1b-v2` (~1 GB downloaded on first local transcription).
- Speaker recognition requires a reachable diarization service (set under
  **Connections**). Without it, local speaker recognition reports that the
  optional extra is missing; the transcript is still produced, just without
  speaker labels.
- Summaries always need an OpenAI-compatible server (the LLM card under
  **Connections**); there is no local fallback for summaries in either setup.

### Setup B — local fallback (offline capable)

Everything except summaries can run on this machine, including speaker
recognition. This is the only configuration that needs **torch**, so it is a
separate, opt-in requirements file.

```bash
# 1. CPU torch first, from PyTorch's CPU-only index.
uv pip install --python .venv/bin/python torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
# 2. pyannote on top; torch is already satisfied and stays the CPU build.
uv pip install --python .venv/bin/python -r requirements-diarization.txt
```

- Install size: **~1,3 GB** for the whole `.venv` (measured; torch CPU alone is
  ~740 MB).
- **Do not skip step 1.** Installing pyannote first resolves the default CUDA
  torch and pulls `nvidia-*` and `triton` wheels: **~4,5 GB extra** that the
  CPU pipeline never uses. Install the CUDA wheels only if you deliberately
  want GPU diarization on this machine.
- Local speaker recognition needs a HuggingFace token (**Connections**) and
  accepted model terms for
  [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
  [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
  Models add ~100 MB to the cache.
- For a purely local run, clear the transcription and diarization local ports
  under **Connections**.
- pyannote 4 warns on import that `torchcodec` "is not installed correctly".
  Harmless here: SIT decodes audio with `ffmpeg` and hands pyannote an
  in-memory waveform, never using torchcodec's decoder.

Switching from A to B later is just running the two commands above; switching
back is `uv pip uninstall torch torchaudio pyannote.audio` (the remaining
pyannote dependencies are harmless but can be removed with a fresh `.venv`).

Installing the ML libraries and the first model download need a network
connection and free disk space. No GPU is required in either setup.

### Option A — app in the browser

```bash
.venv/bin/python -m transcriber
```

Open <http://127.0.0.1:47831>. The backend runs until the command is
terminated with Ctrl+C. Data is stored by default in `~/.local/share/sit`
(respects `XDG_DATA_HOME`).

### Option B — native desktop window

Install stable Rust via [rustup](https://rustup.rs/) (Arch and Fedora
commands above install it from the distribution). After the rustup script
install, load `source "$HOME/.cargo/env"`. System build dependencies are in
the platform blocks above;
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

## Updating

With SIT closed, from the checkout:

```bash
./update.sh
```

It pulls the repository, updates the Python dependencies, rebuilds the native
binary when `src-tauri/` changed, and refreshes the desktop entry if one is
installed. Your `.venv`, settings and data directory are left alone, and the
local diarization extra is refreshed (from the CPU wheel index) only when this
install already has it — so `update.sh` never turns a Setup A install into a
Setup B one.

| Flag | Effect |
| --- | --- |
| *(none)* | `git pull --ff-only`, dependencies, conditional rebuild, desktop entry |
| `--no-pull` | Reinstall/rebuild from the current working tree, no git operations |
| `--force-build` | Rebuild the Tauri binary even when `src-tauri/` did not change |

It refuses to run while the backend answers on `127.0.0.1:47831`
(`TRANSCRIBER_PORT` is honoured), and refuses to pull over uncommitted
changes — use `--no-pull` for a modified checkout.


## Data and configuration

**Everything you normally configure lives in the app, in the UI panel
"Connections".** Remote ASR, diarization and summary servers, their ports,
API paths, model names, SSH tunnels and the HuggingFace token are all set
there while SIT runs, and are saved to
`~/.local/share/sit/.connections.json` (mode `0600`) — no config file to edit
by hand, no restart, no shell environment. The only settings not in the panel
are the handful of process-level variables in
[Environment variables](#environment-variables) (library path, bind address,
local ASR tunables), which you rarely touch.

Managed SSH is the
normal way to reach them: tick *Reach the AI services through SSH tunnels*,
fill in the **SSH host** and **SSH user**, and press **Set up and test** —
either the button on a service card, which reports that service's result
inline, or the one at the bottom, which reports all three. SSH port `22`, the
local and remote ports, the API paths, and the model names are prefilled, so
nothing else is usually needed. SIT opens the forwards itself, waits until
each local port actually accepts connections, closes them when it quits, and
refuses to start when a local port is already taken (typically a
hand-started tunnel) instead of failing silently.

**Test SSH connection** in the SSH card checks only the login, before any
service is involved: SIT opens `ssh -N -T` with a throwaway forward — no shell
and no remote command — and reports success once that forward binds, or the
`ssh` error otherwise (`Permission denied (publickey)`, `Could not resolve
hostname`, `Connection timed out`). Every button shows a spinner while it
works and a ✓/✕ result when it finishes.

A service that lives on a different machine gets its own SSH host under
**Advanced**, together with the address it binds there (`127.0.0.1` by
default), its API path, and — for setups without tunnels — a direct address.
With a tunnel, SIT always talks to `http://127.0.0.1:<local port>`; without
one it uses the shared base address from *Without tunnels*. An empty local
port disables the service, which then falls back to local processing.

SIT stores all of this per user in `~/.local/share/sit/.connections.json` with
mode `0600`. Authentication uses your SSH key or agent; SIT never stores an
SSH password. Once that file exists it is the only source of truth: the
environment variables below are read on first run only, to prefill the panel.

The HuggingFace token for the optional local pyannote fallback and the local
pyannote model name live in the same panel; the token is only useful with the
extra from [Setup B](#setup-b--local-fallback-offline-capable) installed. The
token is write-only: it is stored server-side, never sent back to the browser,
and an empty field keeps the stored value — use **Forget the stored token** to
remove it.

### Environment variables

Process-level settings only, exported in the shell that starts the backend.
Everything else belongs in **Connections**.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIBER_DATA_DIR` | `${XDG_DATA_HOME:-~/.local/share}/sit` | Library root; an explicit value takes precedence, `~` is expanded |
| `TRANSCRIBER_HOST` / `TRANSCRIBER_PORT` | `127.0.0.1` / `47831` | Backend address for manual runs |
| `LOCAL_ASR_MODEL` | `nemo-canary-1b-v2` | Local ONNX ASR model (`onnx-asr`); `nemo-parakeet-tdt-0.6b-v3` is faster but ignores the language choice |
| `LOCAL_ASR_PATH` | empty | Path to an offline model copy; empty = HuggingFace cache |
| `LOCAL_ASR_QUANTIZATION` | `int8` | ONNX weight quantization; empty = full precision |
| `LOCAL_ASR_DEVICE` | `cpu` | `cpu` or `cuda` (requires `onnxruntime-gpu`) |
| `LOCAL_ASR_VAD_MODEL` | `silero` | VAD that cuts audio into windows before recognition |
| `LOCAL_ASR_WINDOW_SECONDS` | `30` | Max window length sent to the model |
| `LOCAL_ASR_DEFAULT_LANGUAGE` | `cs` | Language pinned when a recording carries no explicit one |
| `MAX_RECORDING_SECONDS` | `10800` | Maximum recording length in seconds |

First-run seeds for the **Connections** panel, ignored once
`.connections.json` exists — set them in the UI instead: `HF_TOKEN`,
`DIARIZE_MODEL`, `SPARK_WHISPER_URL`, `SPARK_WHISPER_MODEL`,
`SPARK_DIARIZER_URL`, `OMNIROUTE_URL`, `OMNIROUTE_MODEL`, `GPT_OSS_API_KEY`.
A `.env` at the checkout root is still read for these variables, but it is a
leftover, not a supported way to configure SIT.

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

## Docker (not recommended)

The container exists for a headless server, not for normal use. In a container
you lose the recorder (no PulseAudio/PipeWire access without wiring the host's
audio socket, permissions and `PULSE_SERVER` in yourself), you lose the native
window, and SSH tunnels to your model servers need the keys mounted. Use the
[source install](#installation) above; reach for Docker only when you
deliberately want an upload-only backend on a remote machine.

```bash
docker build -t transcriber .
docker run --rm -p 127.0.0.1:47831:47831 -v "$PWD/data:/data" transcriber
```

The image explicitly uses `/data` and binds `0.0.0.0` inside the container;
the example publishes the port locally only. It installs `requirements.txt`
only, i.e. the Setup A profile: no torch, **280 MB** built image (measured),
remote diarization or none. For local speaker recognition in a container, add
the Setup B extra to your own Dockerfile layer (CPU wheel index) or run the
separate GPU service in [diarizer/](diarizer/README.md). Everything else is
configured in **Connections** as usual, stored in `/data/.connections.json`.

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
