#!/usr/bin/env python3
"""Per-user desktop integration for the SIT (transcriber) desktop app.

Installs, for the current user only (no sudo, no package manager calls):

  - a launcher script at   ~/.local/bin/sit
  - a desktop entry at     ~/.local/share/applications/sit.desktop
  - an app icon at         ~/.local/share/icons/sit.png

The launcher does not copy the Tauri binary anywhere: it points straight at
the prebuilt binary inside this source checkout
(src-tauri/target/release/transcriber-desktop) and exports the two env vars
the binary needs to find its Python backend (TRANSCRIBER_PROJECT_DIR,
TRANSCRIBER_PYTHON). That keeps the installer boring - reinstalling after a
`cargo build --release` or `git pull` just works, nothing to re-copy.

Supported: Arch Linux, Ubuntu/Debian, and WSL2 with WSLg (also Linux under
the hood). Any other platform (native Windows, macOS) is rejected outright -
this project has no supported build for those.

Prerequisites (see README.md for the full setup):
  - a `.venv` at the repo root with the Python dependencies installed
  - a release build of the desktop shell: `cd src-tauri && cargo build --release`

Usage:
  python3 scripts/install-desktop.py            install/reinstall
  python3 scripts/install-desktop.py --uninstall remove the files listed above
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
DESKTOP_BINARY = REPO_ROOT / "src-tauri" / "target" / "release" / "transcriber-desktop"
ICON_SOURCE = REPO_ROOT / "src-tauri" / "icons" / "icon.png"

APP_ID = "sit"
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
if not XDG_DATA_HOME.is_absolute():
    XDG_DATA_HOME = Path.home() / ".local" / "share"

LAUNCHER_PATH = Path.home() / ".local" / "bin" / APP_ID
DESKTOP_ENTRY_PATH = XDG_DATA_HOME / "applications" / f"{APP_ID}.desktop"
ICON_PATH = XDG_DATA_HOME / "icons" / f"{APP_ID}.png"

LEGACY_DESKTOP_ENTRY_PATH = XDG_DATA_HOME / "applications" / "transcriber.desktop"
LEGACY_DESKTOP_EXEC = Path.home() / ".local" / "lib" / "transcriber" / "transcriber-desktop"

LEGACY_SHIT_ID = "shit"
LEGACY_SHIT_LAUNCHER_PATH = Path.home() / ".local" / "bin" / LEGACY_SHIT_ID
LEGACY_SHIT_DESKTOP_ENTRY_PATH = XDG_DATA_HOME / "applications" / f"{LEGACY_SHIT_ID}.desktop"
LEGACY_SHIT_ICON_PATH = XDG_DATA_HOME / "icons" / f"{LEGACY_SHIT_ID}.png"

COMMENT = (
    "Local tool for meeting transcription and summaries with speaker recognition"
)


def check_supported_platform() -> None:
    if sys.platform != "linux":
        raise SystemExit(
            f"Unsupported platform: {sys.platform}. This installer only supports "
            "Linux (Arch, Ubuntu/Debian) and WSL2 with WSLg on Windows 11 - "
            "there is no native Windows or macOS build of this project."
        )
    release = platform.uname().release.lower()
    if "microsoft" in release:
        print(
            "Detected WSL2. Showing the window and playing audio needs WSLg "
            "(bundled with current Windows 11 + `wsl --update`). Capturing "
            "Windows system audio through WSLg is not guaranteed - see README."
        )


def sh_single_quote(value: str) -> str:
    """POSIX sh single-quote escaping: safe for spaces, unicode, `$`, backticks."""
    return "'" + value.replace("'", "'\\''") + "'"


def desktop_value(value: str) -> str:
    """Escape the Desktop Entry string layer (before Exec argument parsing)."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def desktop_exec_quote(value: str) -> str:
    """Quote an Exec argument, including literal percent field-code escaping."""
    quoted = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return desktop_value(f'"{quoted}"')


def check_prerequisites() -> None:
    problems = []
    if not (VENV_PYTHON.is_file() and os.access(VENV_PYTHON, os.X_OK)):
        problems.append(
            f"Missing virtual environment: {VENV_PYTHON}\n"
            "  Create it with (see README.md):\n"
            f"    cd {REPO_ROOT}\n"
            "    python3 -m venv .venv\n"
            "    .venv/bin/pip install -r requirements.txt"
        )
    if not (DESKTOP_BINARY.is_file() and os.access(DESKTOP_BINARY, os.X_OK)):
        problems.append(
            f"Missing built desktop shell: {DESKTOP_BINARY}\n"
            "  Build it with (see README.md):\n"
            f"    cd {REPO_ROOT / 'src-tauri'}\n"
            "    cargo build --release"
        )
    if not ICON_SOURCE.exists():
        problems.append(f"Missing icon: {ICON_SOURCE}")
    if problems:
        raise SystemExit(
            "Cannot finish installation, missing prerequisites:\n\n"
            + "\n\n".join(problems)
        )


def write_launcher() -> None:
    LAUNCHER_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = LAUNCHER_PATH.exists()
    script = (
        "#!/bin/sh\n"
        "# Generated by scripts/install-desktop.py - safe to regenerate, do not\n"
        "# hand-edit (a reinstall overwrites this file).\n"
        f"export TRANSCRIBER_PROJECT_DIR={sh_single_quote(str(REPO_ROOT))}\n"
        f"export TRANSCRIBER_PYTHON={sh_single_quote(str(VENV_PYTHON))}\n"
        f"exec {sh_single_quote(str(DESKTOP_BINARY))} \"$@\"\n"
    )
    LAUNCHER_PATH.write_text(script, encoding="utf-8")
    LAUNCHER_PATH.chmod(0o755)
    print(f"{'Overwrote' if existed else 'Created'} launcher: {LAUNCHER_PATH}")


def write_icon() -> None:
    ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = ICON_PATH.exists()
    shutil.copyfile(ICON_SOURCE, ICON_PATH)
    print(f"{'Overwrote' if existed else 'Installed'} icon: {ICON_PATH}")


def write_desktop_entry() -> None:
    DESKTOP_ENTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = DESKTOP_ENTRY_PATH.exists()
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=SIT\n"
        "GenericName=Meeting Transcriber\n"
        f"Comment={COMMENT}\n"
        f"Exec=/bin/sh {desktop_exec_quote(str(LAUNCHER_PATH))}\n"
        f"Icon={desktop_value(str(ICON_PATH))}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
        "X-SIT-Managed=true\n"
    )
    DESKTOP_ENTRY_PATH.write_text(entry, encoding="utf-8")
    print(f"{'Overwrote' if existed else 'Created'} desktop entry: {DESKTOP_ENTRY_PATH}")
    update_db = shutil.which("update-desktop-database")
    if update_db:
        import subprocess

        subprocess.run(
            [update_db, str(DESKTOP_ENTRY_PATH.parent)], check=False
        )


def check_managed_paths() -> None:
    """Never replace or remove an unrelated file or follow an output symlink."""
    for path in (LAUNCHER_PATH, DESKTOP_ENTRY_PATH, ICON_PATH):
        if path.is_symlink():
            raise SystemExit(f"Refusing to change a symbolic link: {path}")
        if not path.exists():
            continue
        if not path.is_file():
            raise SystemExit(f"Target is not a file: {path}")
        if path == ICON_PATH:
            managed = ICON_SOURCE.is_file() and path.read_bytes() == ICON_SOURCE.read_bytes()
        else:
            marker = (
                "# Generated by scripts/install-desktop.py - safe to regenerate, do not"
                if path == LAUNCHER_PATH else "X-SIT-Managed=true"
            )
            managed = marker in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not managed:
            raise SystemExit(f"Target does not belong to the SIT installer; leaving unchanged: {path}")


def migrate_legacy_desktop_entry() -> None:
    """Remove only the known ŠIT desktop entry so menu search has one app."""
    if not LEGACY_DESKTOP_ENTRY_PATH.exists():
        return
    if LEGACY_DESKTOP_ENTRY_PATH.is_symlink() or not LEGACY_DESKTOP_ENTRY_PATH.is_file():
        raise SystemExit(
            f"Cannot migrate non-file legacy desktop entry: {LEGACY_DESKTOP_ENTRY_PATH}"
        )
    legacy = LEGACY_DESKTOP_ENTRY_PATH.read_text(encoding="utf-8", errors="replace")
    known_entry = (
        "Name=ŠIT\n" in legacy
        and f"Exec={LEGACY_DESKTOP_EXEC}\n" in legacy
        and "Icon=transcriber\n" in legacy
    )
    if not known_entry:
        raise SystemExit(
            "A legacy transcriber.desktop entry exists but is not the known ŠIT "
            f"entry; leaving it unchanged: {LEGACY_DESKTOP_ENTRY_PATH}"
        )
    LEGACY_DESKTOP_ENTRY_PATH.unlink()
    print(f"Removed legacy desktop entry: {LEGACY_DESKTOP_ENTRY_PATH}")


def migrate_shit_installation() -> None:
    """Remove only the prior installer-owned SHIT files after SIT is written."""
    paths = (
        (LEGACY_SHIT_LAUNCHER_PATH, "# Generated by scripts/install-desktop.py - safe to regenerate, do not"),
        (LEGACY_SHIT_DESKTOP_ENTRY_PATH, "X-SHIT-Managed=true"),
        (LEGACY_SHIT_ICON_PATH, None),
    )
    for path, marker in paths:
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Cannot migrate non-file SHIT installation path: {path}")
        if marker is None:
            managed = ICON_SOURCE.is_file() and path.read_bytes() == ICON_SOURCE.read_bytes()
        else:
            managed = marker in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not managed:
            raise SystemExit(
                f"A prior SHIT installation path is not installer-managed; leaving unchanged: {path}"
            )
        path.unlink()
        print(f"Removed prior SHIT installation path: {path}")


def install() -> None:
    check_supported_platform()
    check_prerequisites()
    check_managed_paths()
    write_launcher()
    write_icon()
    write_desktop_entry()
    migrate_shit_installation()
    migrate_legacy_desktop_entry()
    print()
    print('Done. Find the app in your application menu as "SIT", or launch it directly:')
    print(f"  {LAUNCHER_PATH}")
    print()
    print("Uninstall: python3 scripts/install-desktop.py --uninstall")


def uninstall() -> None:
    check_supported_platform()
    check_managed_paths()
    removed = []
    for path in (LAUNCHER_PATH, DESKTOP_ENTRY_PATH, ICON_PATH):
        if path.exists():
            path.unlink()
            removed.append(path)
    if removed:
        print("Removed:")
        for path in removed:
            print(f"  {path}")
    else:
        print("Nothing to remove - none of the installed files exist.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Per-user desktop integration install for SIT (no sudo, no "
            "system directory changes)."
        )
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help=f"remove {LAUNCHER_PATH}, {DESKTOP_ENTRY_PATH}, and {ICON_PATH}",
    )
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
