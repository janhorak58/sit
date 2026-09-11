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
the microphone together with system audio — as two separate channels, not one
mix. Each track is transcribed on its own pass, so room reverb from the
microphone never degrades the clean loopback copy of what plays on the machine,
and speaker labels are namespaced per track (`MIC_SPEAKER_00`,
`SYSTEM_SPEAKER_00`). A track that stayed silent is skipped, not transcribed.

Both ends of that pair are chosen in the UI: the microphone, and the output
whose monitor becomes the system track (defaulting to the current default
sink). Card profiles are never switched automatically — a Bluetooth headset
has to be put into headset mode in the system sound settings first, because
doing it silently would drop *both* tracks to narrowband HFP. Bluetooth
microphones are labelled with that warning in the device list.

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

## Video guides

<p>
  <a href="https://www.youtube.com/playlist?list=PLDh-GSR7gPYs">
    <strong>Watch the complete SIT tutorial playlist on YouTube →</strong>
  </a>
</p>

<table>
  <tr>
    <td width="50%">
      <a href="https://youtu.be/SBpx8ou_by0"><img src="https://img.youtube.com/vi/SBpx8ou_by0/hqdefault.jpg" alt="Play: Install and launch SIT" width="100%"></a><br>
      <strong>1. Install and launch</strong><br>
      Set up the browser or desktop app on a supported Linux system.
    </td>
    <td width="50%">
      <a href="https://youtu.be/_Rqnt7yBPgA"><img src="https://img.youtube.com/vi/_Rqnt7yBPgA/hqdefault.jpg" alt="Play: Configure SIT connections" width="100%"></a><br>
      <strong>2. Connections</strong><br>
      Configure managed SSH tunnels and remote AI services.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="https://youtu.be/k8j4wIk-aoE"><img src="https://img.youtube.com/vi/k8j4wIk-aoE/hqdefault.jpg" alt="Play: Record a meeting with SIT" width="100%"></a><br>
      <strong>3. Recording</strong><br>
      Capture microphone and selected system audio with a live preview.
    </td>
    <td width="50%">
      <a href="https://youtu.be/OgSvlKAtHwU"><img src="https://img.youtube.com/vi/OgSvlKAtHwU/hqdefault.jpg" alt="Play: Upload, transcribe, and analyze with SIT" width="100%"></a><br>
      <strong>4. Upload, transcript, and analysis</strong><br>
      Import a file, label speakers, edit the transcript, and create a brief.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="https://youtu.be/7XGKbOyHMsg"><img src="https://img.youtube.com/vi/7XGKbOyHMsg/hqdefault.jpg" alt="Play: Manage SIT library, privacy, and updates" width="100%"></a><br>
      <strong>5. Library, privacy, and updates</strong><br>
      Manage projects, adjust defaults, and keep the local library maintainable.
    </td>
    <td width="50%">
      <a href="https://www.youtube.com/playlist?list=PLDh-GSR7gPYs"><img src="src-tauri/icons/icon.png" alt="SIT tutorial playlist" width="120"></a><br>
      <strong>Complete playlist</strong><br>
      Watch the guides in order on YouTube.
    </td>
  </tr>
</table>


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

A level meter under the recording toolbar shows what is actually being
captured, and says so when nothing is: *No sound is reaching the recording —
check the selected microphone.*

A Bluetooth headset only exposes a usable microphone in its headset (HFP/HSP)
profile. In A2DP the system may still offer a `bluez_input` source that records
silence. SIT labels that condition in the device list; switch the headset to a
capture-capable profile in the system sound settings before starting a recording.

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
uses PulseAudio modules `module-null-sink`, `module-loopback`, and an output
monitor to capture system audio. WSLg may reject those modules, so Windows
system audio cannot be guaranteed. If that happens, SIT continues with the
selected WSL microphone as a single track and marks the running recording as
microphone-only; use **Upload File** for a recording that must include Windows
audio. WSLg support is not the same as native Windows build support.

**Windows Start menu.** WSLg builds Start menu shortcuts only from system
application directories, never from `~/.local/share/applications`, so
`scripts/install-desktop.py` additionally writes
`/usr/share/applications/sit.desktop` on WSL (it asks for `sudo`). The
shortcut shows up once WSLg refreshes; `wsl --shutdown` in PowerShell forces
it immediately. If `sudo` is unavailable the installer prints the two
commands to run by hand.

**Autostart in WSL** needs systemd, which WSL does not enable by default. Put

```ini
[boot]
systemd=true
```

in `/etc/wsl.conf`, run `wsl --shutdown` in PowerShell, and then (re)run
`python3 scripts/install-desktop.py`. The backend then starts with the
distribution, so typing `sit` after a reboot opens the window immediately; the
SSH tunnels connect by themselves once you are on the VPN.

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
(respects `XDG_DATA_HOME`). A library left behind by the pre-SIT name at
`~/.local/share/transcriber` is used as-is instead, until `sit/` holds
meetings of its own; `TRANSCRIBER_DATA_DIR` overrides both.

### Option B — native desktop window

The desktop shell is a Rust/Tauri binary. Build it only after completing the
common Python setup above: it starts `.venv/bin/python -m transcriber` when no
backend is already listening, so a Cargo build alone is not a complete SIT
install.
Install stable Rust with [rustup](https://rustup.rs/) (the Arch and Fedora
platform commands above install it from the distribution). Select the stable
toolchain before using Cargo — a distribution `rustup` install can exist
without one:

```bash
rustup default stable
# Only needed after using the rustup installer; harmlessly skip it otherwise.
test ! -f "$HOME/.cargo/env" || source "$HOME/.cargo/env"
cargo --version
rustc --version
```

The native-only Linux packages from your platform section — compiler toolchain,
`pkg-config`, WebKitGTK 4.1, OpenSSL, xdotool, appindicator, and librsvg — must
be installed before building. From the repository root, create the optimized
release binary and verify the installer can use it:

```bash
cargo build --release --manifest-path src-tauri/Cargo.toml
test -x src-tauri/target/release/transcriber-desktop
python3 scripts/install-desktop.py
```

Cargo downloads and compiles the pinned Tauri dependencies on the first build;
later unchanged builds reuse `src-tauri/target/`. The installer must run from
this checkout because it writes a launcher pointing to this exact release
binary and this checkout's `.venv`. It makes no system-wide changes and never
needs `sudo`.

For a development build without creating a menu entry, run the shell directly:

```bash
cargo run --manifest-path src-tauri/Cargo.toml
```

It uses Cargo's debug binary and starts (or reuses) the backend exactly as the
installed launcher does. Press Ctrl+C in the terminal or close the window to
exit; the latter stops only the backend the window started itself.

**SIT** will appear in your application menu. You can also launch it from a
terminal by absolute path:

```bash
~/.local/bin/sit
```

The installer does not require sudo (except the WSL Start menu entry). It creates:

- `~/.local/bin/sit` — a launcher with an absolute path to the checkout and `.venv/bin/python`;
- `${XDG_DATA_HOME:-~/.local/share}/applications/sit.desktop` — a menu entry;
- `${XDG_DATA_HOME:-~/.local/share}/icons/sit.png` — an icon;
- `~/.config/systemd/user/sit.service` — the backend as a background service,
  when a systemd user manager is available.

Reinstalling updates its own files; it refuses to change a foreign file or
symlink at any of the target locations. After moving the checkout, run the
installer again. Updating the Rust code requires a fresh
`cargo build --release`; the launcher uses the binary directly from the
checkout. Web/Python changes take effect after a backend restart.

### Background service and autostart

`sit.service` runs `.venv/bin/python -m transcriber` on `127.0.0.1:47831`. The
installer enables it, starts it, and turns on user lingering
(`loginctl enable-linger`), so the backend comes up with the machine — after a
reboot, `sit` only has to open a window against a backend that is already warm.
Jobs keep running when the window is closed.

```bash
systemctl --user status sit.service       # state
journalctl --user -u sit.service -f       # logs
systemctl --user restart sit.service      # after changing Python code
systemctl --user disable --now sit.service  # opt out of autostart
```

The SSH tunnels are supervised by the backend itself: a service that started
before the VPN was connected retries every 15 s, so the remote engine becomes
available on its own once the VPN is up — no Reconnect click needed.

**systemd is not required.** Without a user manager (a WSL distribution
without `systemd=true` in `/etc/wsl.conf`, a container) the installer says so
and the app starts the backend on demand, exactly as before. The installer
also retires the legacy `transcriber.service` so an old process cannot shadow
the current checkout or point SIT at a different library. Before opening the
window the launcher waits for the API to respond; a broken install is reported
on stderr. If something goes wrong, run `~/.local/bin/sit` in a terminal.

**Closing the window stops only a backend the window itself started.** With
the service installed, closing the window leaves the backend running. Without
it, finish saving a recording and processing first; an in-progress
transcription is interrupted on shutdown. An active recorder is stopped on a
clean backend shutdown, and the temporary WAV stays in `_scratch` (this does
not replace the button for saving a recording).

Remove the desktop integration (does not delete data, the checkout, or `.venv`):

```bash
python3 scripts/install-desktop.py --uninstall
```

## Updating

With the window closed, from the checkout:

```bash
./update.sh
```

An installed `sit.service` is stopped for the update and started again
afterwards; anything else holding the port still has to be closed by hand.

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

The LLM **API key** is a field in Connections next to the model
(`Authorization: Bearer …`); a stored key is never sent back to the browser,
an empty field keeps it, and "Forget the stored API key" clears it. When the
server answers 401/403, Service diagnostics says the key was rejected instead
of reporting the endpoint as running.

Local pyannote needs you to accept the model terms at
[speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
[segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
The remote model servers are not part of the running desktop app; the
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

**Open on Disk** explains where the SIT library is stored; it does not require
or contact a separate helper. Open that data directory directly in your file
manager when you need to inspect the files.

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
