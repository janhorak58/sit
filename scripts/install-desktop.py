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

On WSL2 the same entry is additionally installed to
/usr/share/applications/sit.desktop (via sudo), because WSLg only scans
system application directories when it generates Windows Start menu
shortcuts - a per-user entry alone never shows up in the Windows menu.

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
import subprocess
import sys
import tempfile
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

# WSLg builds the Windows Start menu from system application directories only
# (/usr/share/applications and friends); ~/.local/share/applications is never
# scanned. On WSL the user entry is therefore mirrored here.
SYSTEM_ENTRY_PATH = Path("/usr/share/applications") / f"{APP_ID}.desktop"

LEGACY_DESKTOP_ENTRY_PATH = XDG_DATA_HOME / "applications" / "transcriber.desktop"
LEGACY_DESKTOP_EXEC = Path.home() / ".local" / "lib" / "transcriber" / "transcriber-desktop"

LEGACY_SHIT_ID = "shit"
LEGACY_SHIT_LAUNCHER_PATH = Path.home() / ".local" / "bin" / LEGACY_SHIT_ID
LEGACY_SHIT_DESKTOP_ENTRY_PATH = XDG_DATA_HOME / "applications" / f"{LEGACY_SHIT_ID}.desktop"
LEGACY_SHIT_ICON_PATH = XDG_DATA_HOME / "icons" / f"{LEGACY_SHIT_ID}.png"

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
if not XDG_CONFIG_HOME.is_absolute():
    XDG_CONFIG_HOME = Path.home() / ".config"
LEGACY_SERVICE_PATH = XDG_CONFIG_HOME / "systemd" / "user" / "transcriber.service"

COMMENT = (
    "Local tool for meeting transcription and summaries with speaker recognition"
)


def is_wsl() -> bool:
    return "microsoft" in platform.uname().release.lower()


def check_supported_platform() -> None:
    if sys.platform != "linux":
        raise SystemExit(
            f"Unsupported platform: {sys.platform}. This installer only supports "
            "Linux (Arch, Ubuntu/Debian) and WSL2 with WSLg on Windows 11 - "
            "there is no native Windows or macOS build of this project."
        )
    if is_wsl():
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


def desktop_entry_text() -> str:
    return (
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


def write_desktop_entry() -> None:
    DESKTOP_ENTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = DESKTOP_ENTRY_PATH.exists()
    DESKTOP_ENTRY_PATH.write_text(desktop_entry_text(), encoding="utf-8")
    print(f"{'Overwrote' if existed else 'Created'} desktop entry: {DESKTOP_ENTRY_PATH}")
    update_db = shutil.which("update-desktop-database")
    if update_db:
        subprocess.run([update_db, str(DESKTOP_ENTRY_PATH.parent)], check=False)


def _root_command(argv) -> list[str]:
    """``argv`` run as root, prefixed with sudo unless already root."""
    return argv if os.geteuid() == 0 else ["sudo", *argv]


def _system_entry_is_ours() -> bool:
    """True when /usr/share/applications/sit.desktop is this installer's file.

    Read as the current user: the directory is world-readable, and refusing to
    escalate just to inspect a file keeps a foreign entry from being replaced
    silently.
    """
    try:
        return _is_managed_text(SYSTEM_ENTRY_PATH, "X-SIT-Managed=true")
    except OSError:
        return False


def write_system_entry() -> None:
    """Mirror the entry into /usr/share/applications so WSLg exports it.

    Only reached on WSL. Without this copy the app never appears in the
    Windows Start menu: WSLg's shortcut generator ignores per-user desktop
    directories entirely.
    """
    if SYSTEM_ENTRY_PATH.exists() and not _system_entry_is_ours():
        print(
            f"Not touching a foreign {SYSTEM_ENTRY_PATH}; the Windows Start "
            "menu entry was left as it is."
        )
        return
    handle, staged_name = tempfile.mkstemp(prefix=f"{APP_ID}-", suffix=".desktop")
    staged = Path(staged_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(desktop_entry_text())
        result = subprocess.run(
            _root_command(["install", "-D", "-m", "644", str(staged), str(SYSTEM_ENTRY_PATH)]),
            check=False,
        )
    finally:
        staged.unlink(missing_ok=True)
    if result.returncode != 0:
        print(
            "Could not write the system-wide entry needed by the Windows Start "
            "menu. Run this yourself, then restart WSL (`wsl --shutdown`):\n"
            f"  sudo cp {DESKTOP_ENTRY_PATH} {SYSTEM_ENTRY_PATH}\n"
            f"  sudo chmod 644 {SYSTEM_ENTRY_PATH}"
        )
        return
    # WSLg reads the icon straight from the path in the entry, so the per-user
    # icon and its parents must stay traversable for the exporting service.
    for path in (ICON_PATH.parent, ICON_PATH):
        subprocess.run(["chmod", "o+rX", str(path)], check=False)
    print(f"Installed the Windows Start menu entry: {SYSTEM_ENTRY_PATH}")
    print("It appears after WSLg refreshes; `wsl --shutdown` in PowerShell forces it.")


def remove_system_entry() -> None:
    if not SYSTEM_ENTRY_PATH.exists():
        return
    if not _system_entry_is_ours():
        print(f"Leaving a foreign entry unchanged: {SYSTEM_ENTRY_PATH}")
        return
    result = subprocess.run(_root_command(["rm", "-f", str(SYSTEM_ENTRY_PATH)]), check=False)
    if result.returncode == 0:
        print(f"Removed: {SYSTEM_ENTRY_PATH}")
    else:
        print(f"Could not remove it; run: sudo rm {SYSTEM_ENTRY_PATH}")


def _is_managed_text(path, marker) -> bool:
    return marker in path.read_text(encoding="utf-8", errors="replace").splitlines()


def check_managed_paths() -> None:
    """Never replace or remove an unrelated file or follow an output symlink."""
    # Our own desktop entry proves this install is ours, which is what lets a
    # stale icon be replaced: the shipped artwork changes between versions, so
    # byte equality alone would block every later reinstall.
    ours = DESKTOP_ENTRY_PATH.is_file() and not DESKTOP_ENTRY_PATH.is_symlink() and _is_managed_text(
        DESKTOP_ENTRY_PATH, "X-SIT-Managed=true"
    )
    for path in (LAUNCHER_PATH, DESKTOP_ENTRY_PATH, ICON_PATH):
        if path.is_symlink():
            raise SystemExit(f"Refusing to change a symbolic link: {path}")
        if not path.exists():
            continue
        if not path.is_file():
            raise SystemExit(f"Target is not a file: {path}")
        if path == ICON_PATH:
            managed = ours or (ICON_SOURCE.is_file() and path.read_bytes() == ICON_SOURCE.read_bytes())
        else:
            marker = (
                "# Generated by scripts/install-desktop.py - safe to regenerate, do not"
                if path == LAUNCHER_PATH else "X-SIT-Managed=true"
            )
            managed = _is_managed_text(path, marker)
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


def migrate_legacy_service() -> None:
    """Retire the old always-on user service.

    The desktop app reuses whatever already listens on the port, so a unit left
    over from the previous install keeps an old process — and, with its own
    TRANSCRIBER_DATA_DIR, a different library — in front of every launch. Only
    a unit that starts this project's backend is touched.
    """
    if not LEGACY_SERVICE_PATH.exists():
        return
    if LEGACY_SERVICE_PATH.is_symlink() or not LEGACY_SERVICE_PATH.is_file():
        raise SystemExit(f"Cannot migrate non-file legacy service: {LEGACY_SERVICE_PATH}")
    unit = LEGACY_SERVICE_PATH.read_text(encoding="utf-8", errors="replace")
    if "-m transcriber" not in unit:
        raise SystemExit(
            "A transcriber.service exists but does not start this backend; "
            f"leaving it unchanged: {LEGACY_SERVICE_PATH}"
        )
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "transcriber.service"],
        check=False, capture_output=True,
    )
    LEGACY_SERVICE_PATH.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    print(f"Stopped and removed the legacy background service: {LEGACY_SERVICE_PATH}")


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
    if is_wsl():
        write_system_entry()
    migrate_shit_installation()
    migrate_legacy_desktop_entry()
    migrate_legacy_service()
    print()
    print('Done. Find the app in your application menu as "SIT", or launch it directly:')
    print(f"  {LAUNCHER_PATH}")
    print()
    print("Uninstall: python3 scripts/install-desktop.py --uninstall")


def uninstall() -> None:
    check_supported_platform()
    check_managed_paths()
    remove_system_entry()
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
