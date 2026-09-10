import io
import socket
import stat

import pytest

from transcriber import connections
from transcriber.errors import AppError


class Process:
    """Stand-in for a live ssh port-forward."""

    started = []
    instances = []

    def __init__(self, command, **kwargs):
        Process.started.append(command)
        Process.instances.append(self)
        self.returncode = None
        self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def minimal(key):
    """What a new user actually fills in: SSH host, user, key. Nothing else."""
    return {
        "ssh": {"enabled": True, "user": "horak1", "host": "gpu.example.test", "ssh_key": str(key)},
    }


def ready_tunnels(monkeypatch):
    monkeypatch.setattr(connections.subprocess, "Popen", Process)
    monkeypatch.setattr(connections.time, "sleep", lambda _: None)
    monkeypatch.setattr(connections, "_port_in_use", lambda port: False)
    monkeypatch.setattr(connections, "_port_open", lambda port: True)
    Process.started = []
    Process.instances = []


def test_ssh_login_test_reports_success_without_running_a_remote_command(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    ready_tunnels(monkeypatch)
    config = connections.normalize_connections(minimal(key))

    report = connections.test_ssh(config)

    assert report == {
        "host": "gpu.example.test",
        "target": "horak1@gpu.example.test",
        "port": 22,
        "ok": True,
        "detail": "Signed in to horak1@gpu.example.test on port 22.",
    }
    command, = Process.started
    # -N -T: no shell, no remote command. Success is the throwaway forward
    # binding locally, which only happens once ssh is authenticated.
    assert command[:3] == ["ssh", "-N", "-T"]
    assert command[-1] == "horak1@gpu.example.test"
    assert command[-2].endswith(":127.0.0.1:1") and command[-3] == "-L"
    assert Process.instances[0].returncode == 0  # probe connection is torn down


def test_ssh_login_test_fails_while_authentication_never_completes(monkeypatch, tmp_path):
    """A server that keeps the connection open without authenticating must not
    count as success — only a bound forward proves the login."""
    ready_tunnels(monkeypatch)
    monkeypatch.setattr(connections, "_port_open", lambda port: False)
    monkeypatch.setattr(connections, "SSH_TEST_TIMEOUT", 0.0)
    config = connections.normalize_connections(
        {"ssh": {"enabled": True, "host": "gpu.example.test", "user": "horak1"}}
    )

    report = connections.test_ssh(config)

    assert report["ok"] is False
    assert "No answer from horak1@gpu.example.test" in report["detail"]
    assert Process.instances[0].returncode == 0


def test_ssh_login_test_returns_the_ssh_error(monkeypatch, tmp_path):
    class Failing:
        def __init__(self, command, **kwargs):
            self.returncode = 255
            self.stderr = io.StringIO("Permission denied (publickey).")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(connections.subprocess, "Popen", Failing)
    config = connections.normalize_connections(
        {"ssh": {"enabled": True, "host": "gpu.example.test", "user": "horak1"}}
    )

    report = connections.test_ssh(config)

    assert report["ok"] is False
    assert report["detail"] == "Permission denied (publickey)."


def test_ssh_login_test_needs_a_host(tmp_path):
    with pytest.raises(AppError, match="Enter an SSH host"):
        connections.test_ssh(connections.normalize_connections({}))


def test_ssh_host_and_user_alone_tunnel_every_prefilled_service(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    ready_tunnels(monkeypatch)

    saved = connections.save_connections(minimal(key))
    status = connections.TunnelManager().start()

    assert saved["ssh"]["port"] == 22
    assert saved["transcription"]["local_port"] == saved["transcription"]["remote_port"] == 8208
    assert saved["diarization"]["remote_host"] == "127.0.0.1"
    # Every endpoint resolves to the tunnel's local end, whatever the base address.
    assert connections.endpoints() == {
        "transcription": "http://127.0.0.1:8208/v1/audio/transcriptions",
        "diarization": "http://127.0.0.1:8299/v1/audio/diarizations",
        "llm": "http://127.0.0.1:8100",
    }
    assert status["hosts"] == [{
        "host": "gpu.example.test",
        "services": ["transcription", "diarization", "llm"],
        "state": "running",
        "error": None,
    }]
    command, = Process.started
    assert command[-1] == "horak1@gpu.example.test"
    assert ["-p", "22"] == command[command.index("-p"):command.index("-p") + 2]
    forwards = [command[i + 1] for i, arg in enumerate(command) if arg == "-L"]
    assert forwards == [
        "127.0.0.1:8208:127.0.0.1:8208",
        "127.0.0.1:8299:127.0.0.1:8299",
        "127.0.0.1:8100:127.0.0.1:8100",
    ]
    assert stat.S_IMODE((tmp_path / "connections.json").stat().st_mode) == 0o600


def test_service_override_tunnels_from_a_second_machine(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    values = minimal(key)
    values["llm"] = {"ssh_host": "llm.example.test", "local_port": "18100", "remote_port": "8100"}
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    ready_tunnels(monkeypatch)

    connections.save_connections(values)
    status = connections.TunnelManager().start()

    assert connections.endpoints()["llm"] == "http://127.0.0.1:18100"
    assert [(host["host"], host["services"]) for host in status["hosts"]] == [
        ("gpu.example.test", ["transcription", "diarization"]),
        ("llm.example.test", ["llm"]),
    ]
    llm_command = next(c for c in Process.started if c[-1].endswith("@llm.example.test"))
    assert "127.0.0.1:18100:127.0.0.1:8100" in llm_command


def test_remote_bind_address_reaches_a_service_behind_the_ssh_host(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    values = minimal(key)
    values["transcription"] = {"remote_host": "10.0.0.5", "remote_port": "9000"}
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    ready_tunnels(monkeypatch)

    connections.save_connections(values)
    connections.TunnelManager().start()

    command, = Process.started
    assert "127.0.0.1:8208:10.0.0.5:9000" in command


def test_busy_local_port_is_reported_instead_of_a_silent_ssh_failure(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    monkeypatch.setattr(connections, "_port_in_use", lambda port: port == 8299)
    monkeypatch.setattr(
        connections.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ssh started")),
    )
    connections.save_connections(minimal(key))

    status = connections.TunnelManager().start()

    assert status["hosts"][0]["state"] == "error"
    assert "8299" in status["hosts"][0]["error"]
    assert "already in use" in status["hosts"][0]["error"]


def test_port_probe_sees_a_real_listener(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert connections._port_in_use(port) is True
    assert connections._port_in_use(port) is False


def test_forward_that_never_opens_is_reported_and_the_process_is_killed(monkeypatch, tmp_path):
    """Probing a service before ssh binds the forward would look like a
    bogus "connection refused"; start() must wait and then say what happened."""
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    ready_tunnels(monkeypatch)
    monkeypatch.setattr(connections, "_port_open", lambda port: False)
    monkeypatch.setattr(connections, "FORWARD_TIMEOUT", 0.0)
    connections.save_connections(minimal(key))

    manager = connections.TunnelManager()
    status = manager.start()

    assert status["hosts"][0]["state"] == "error"
    assert "did not open local port 8208" in status["hosts"][0]["error"]
    assert [p.returncode for p in Process.instances] == [0]


def test_forward_readiness_waits_for_the_port_to_accept(monkeypatch, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)
    ready_tunnels(monkeypatch)
    attempts = {8208: 3, 8299: 1, 8100: 1}

    def opening(port):
        attempts[port] -= 1
        return attempts[port] <= 0

    monkeypatch.setattr(connections, "_port_open", opening)
    connections.save_connections(minimal(key))

    status = connections.TunnelManager().start()

    assert status["hosts"][0]["state"] == "running"
    assert status["hosts"][0]["error"] is None


def test_two_services_cannot_share_one_local_port(tmp_path):
    values = minimal(tmp_path / "id_ed25519")
    values["llm"] = {"local_port": "8208"}

    with pytest.raises(AppError, match="own local port"):
        connections.normalize_connections(values)


def test_without_tunnels_endpoints_use_the_base_address(monkeypatch, tmp_path):
    values = {
        "ssh": {"enabled": False},
        "base_url": "http://gpu.example.test",
        "llm": {"host": "http://llm.example.test"},
        "diarization": {"local_port": ""},
    }
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)

    connections.save_connections(values)

    assert connections.endpoints() == {
        "transcription": "http://gpu.example.test:8208/v1/audio/transcriptions",
        "diarization": "",
        "llm": "http://llm.example.test:8100",
    }


def test_missing_ssh_host_is_reported_before_any_ssh_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)

    with pytest.raises(AppError, match="needs an SSH host"):
        connections.save_connections({"ssh": {"enabled": True, "user": "horak1"}})


def test_missing_saved_key_reports_error_without_starting_ssh(monkeypatch, tmp_path):
    values = connections.normalize_connections(minimal(tmp_path / "removed-key"))
    monkeypatch.setattr(connections, "get_connections", lambda: values)
    monkeypatch.setattr(
        connections.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ssh started")),
    )

    status = connections.TunnelManager().start()

    assert status["hosts"] == [{
        "host": "configuration",
        "services": [],
        "state": "error",
        "error": f"SSH key was not found: {tmp_path / 'removed-key'}",
    }]


def test_hf_token_is_write_only_kept_on_save_and_cleared_on_request(monkeypatch, tmp_path):
    values = {"ssh": {"enabled": False}, "diarization": {"hf_token": "hf_secret"}}
    monkeypatch.setattr(connections, "CONNECTIONS_PATH", tmp_path / "connections.json")
    monkeypatch.setattr(connections, "_cached", None)

    connections.save_connections(values)
    assert connections.get_connection("diarization")["hf_token"] == "hf_secret"

    public = connections.public_connections()
    assert "hf_token" not in public["diarization"]
    assert public["diarization"]["hf_token_set"] is True

    # A form round-trip carries an empty field back; the token must survive it.
    public["diarization"]["hf_token"] = ""
    connections.save_connections(public)
    assert connections.get_connection("diarization")["hf_token"] == "hf_secret"

    connections.save_connections({**public, "forget_hf_token": True})
    assert connections.get_connection("diarization")["hf_token"] == ""
    assert connections.public_connections()["diarization"]["hf_token_set"] is False
