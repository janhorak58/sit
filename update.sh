#!/usr/bin/env bash
# Update an existing SIT source install in place.
#
#   ./update.sh                 # pull, update Python deps, rebuild if needed
#   ./update.sh --no-pull       # only reinstall/rebuild from the current tree
#   ./update.sh --force-build   # rebuild the Tauri binary even if unchanged
#
# Keeps the checkout, .venv, .env and the data directory. Reinstalls the
# desktop entry only when it is already installed, and rebuilds the native
# binary only when Rust sources changed (or --force-build).
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

PULL=1
FORCE_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --force-build) FORCE_BUILD=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "update.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$1"; }

PY=.venv/bin/python
[ -x "$PY" ] || { echo "update.sh: $PY missing; run the install steps in README.md first" >&2; exit 1; }

# A running backend holds the ONNX models and the port; updating under it would
# leave a mismatched process alive. The installed background service is ours to
# stop and start again; anything else (an open window, a hand-started backend)
# still has to be closed by the user.
PORT=${TRANSCRIBER_PORT:-47831}
SERVICE_STOPPED=0
if systemctl --user is-active --quiet sit.service 2>/dev/null; then
  say "Stopping the background service"
  systemctl --user stop sit.service
  SERVICE_STOPPED=1
fi
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/asr/status" >/dev/null 2>&1; then
  [ "$SERVICE_STOPPED" = 1 ] && systemctl --user start sit.service
  echo "update.sh: SIT is running on 127.0.0.1:$PORT - close the window / stop the backend first" >&2
  exit 1
fi

RUST_BEFORE=""
if [ "$PULL" = 1 ]; then
  say "Fetching changes"
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "update.sh: local modifications present; commit/stash them or use --no-pull" >&2
    exit 1
  fi
  RUST_BEFORE=$(git rev-parse HEAD:src-tauri 2>/dev/null || echo none)
  git pull --ff-only
fi

say "Updating Python dependencies"
if command -v uv >/dev/null 2>&1; then
  INSTALL=(uv pip install --python "$PY")
else
  INSTALL=("$PY" -m pip install)
fi
"${INSTALL[@]}" -r requirements.txt
# Local speaker recognition is an opt-in extra; refresh it only if this install
# already has it. Keeping torch on the CPU index avoids silently pulling the
# ~4,5 GB CUDA closure into an install that never asked for it.
if "$PY" -c 'import pyannote.audio' >/dev/null 2>&1; then
  say "Updating the local diarization extra"
  if "$PY" -c 'import torch, sys; sys.exit(0 if "+cpu" in torch.__version__ else 1)'; then
    "${INSTALL[@]}" torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  fi
  "${INSTALL[@]}" -r requirements-diarization.txt
fi

BIN=src-tauri/target/release/transcriber-desktop
if [ -x "$BIN" ]; then
  RUST_AFTER=$(git rev-parse HEAD:src-tauri 2>/dev/null || echo none)
  if [ "$FORCE_BUILD" = 1 ] || [ "$RUST_BEFORE" != "$RUST_AFTER" ]; then
    say "Rebuilding the native window"
    cargo build --release --manifest-path src-tauri/Cargo.toml
  else
    say "Native window unchanged; skipping cargo build (use --force-build to force)"
  fi

  # The desktop launcher points at this binary, so refresh it only when a build
  # exists; install-desktop.py refuses to write an entry without one anyway.
  if [ -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/sit.desktop" ]; then
    say "Refreshing the desktop entry"
    "$PY" scripts/install-desktop.py
  fi
fi

if [ "$SERVICE_STOPPED" = 1 ]; then
  say "Starting the background service again"
  systemctl --user start sit.service
fi

say "Done. SIT runs in the background; open the window from the menu or ~/.local/bin/sit."
