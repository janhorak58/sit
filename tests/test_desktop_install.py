"""Exercise desktop integration with a short-lived stand-in for the GUI binary."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install-desktop.py"


def prepare_install(tmp_path):
    checkout = tmp_path / 'checkout SHIT \' "$`\\ %f'
    home = tmp_path / 'home SHIT \' "$`\\ %f'
    script = checkout / "scripts" / "install-desktop.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(INSTALLER, script)
    python = checkout / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    binary = checkout / "src-tauri" / "target" / "release" / "transcriber-desktop"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$TRANSCRIBER_PROJECT_DIR" '
        '"$TRANSCRIBER_PYTHON" > "$SIT_INSTALL_CAPTURE"\n', encoding="utf-8"
    )
    binary.chmod(0o755)
    icon = checkout / "src-tauri" / "icons" / "icon.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"test icon")
    capture = tmp_path / "captured-paths"
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / "data"), SIT_INSTALL_CAPTURE=str(capture))
    return checkout, home, capture, env, [sys.executable, str(script)]


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("gio"), reason="Needs Linux GIO desktop-entry support")
def test_desktop_entry_launches_with_literal_special_characters(tmp_path):
    checkout, home, capture, env, command = prepare_install(tmp_path)
    subprocess.run(command, env=env, cwd="/", check=True, capture_output=True)
    entry = home / "data" / "applications" / "shit.desktop"
    subprocess.run(["gio", "launch", str(entry)], env=env, cwd="/", check=True, capture_output=True)
    deadline = time.monotonic() + 5
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert capture.read_text().splitlines() == [str(checkout), str(checkout / ".venv/bin/python")]
    subprocess.run(command + ["--uninstall"], env=env, check=True, capture_output=True)
    assert not entry.exists()
    assert not (home / ".local/bin/shit").exists()
    assert not (home / "data/icons/shit.png").exists()
    assert checkout.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux installer")
def test_installer_refuses_to_overwrite_or_uninstall_unrelated_launcher(tmp_path):
    _, home, _, env, command = prepare_install(tmp_path)
    target = home / ".local/bin/shit"
    target.parent.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("user data")
    target.symlink_to(unrelated)
    result = subprocess.run(command, env=env, capture_output=True)
    assert result.returncode != 0
    assert target.is_symlink()
    assert unrelated.read_text() == "user data"
    assert not (home / "data/applications/shit.desktop").exists()
    result = subprocess.run(command + ["--uninstall"], env=env, capture_output=True)
    assert result.returncode != 0
    assert target.is_symlink()
    assert unrelated.read_text() == "user data"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux installer")
def test_installer_replaces_known_legacy_desktop_entry(tmp_path):
    _, home, _, env, command = prepare_install(tmp_path)
    legacy = home / "data" / "applications" / "transcriber.desktop"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ŠIT\n"
        f"Exec={home / '.local/lib/transcriber/transcriber-desktop'}\n"
        "Icon=transcriber\n",
        encoding="utf-8",
    )

    subprocess.run(command, env=env, cwd="/", check=True, capture_output=True)

    assert not legacy.exists()
    assert "Name=SHIT\n" in (home / "data" / "applications" / "shit.desktop").read_text()
