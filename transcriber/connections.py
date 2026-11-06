"""User-owned remote service settings and managed SSH port forwards.

By default every service runs behind one shared LiteLLM gateway, so a fresh
install needs nothing but an API key. SIT then talks to the gateway directly:
no SSH, no ports.

With the gateway off, SIT reaches its own servers. One SSH identity (user,
host, port, key) serves every service. Each service carries the local port SIT
talks to, the port it listens on the remote machine, and the address it binds
there, all prefilled. A service can point at a different machine through its
own SSH host override. With a tunnel, SIT talks to
``http://127.0.0.1:<local port>``; without one it talks to the shared base
address (or a per-service host override).
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .config import (
    DATA_DIR,
    DIARIZE_MODEL,
    GPT_OSS_API_KEY,
    HF_TOKEN,
    OMNIROUTE_MODEL,
    OMNIROUTE_URL,
    SPARK_DIARIZER_URL,
    SPARK_WHISPER_MODEL,
    SPARK_WHISPER_URL,
)
from .errors import AppError

CONNECTIONS_PATH = DATA_DIR / ".connections.json"
SERVICES = ("transcription", "diarization", "llm")
LABELS = {
    "transcription": "Transcription",
    "diarization": "Pyannote",
    "llm": "LLM",
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Fallbacks so the panel is never blank: an unset or unparseable environment
# variable still yields a working port/path/model to edit.
FALLBACKS = {
    "transcription": (8208, "/v1/audio/transcriptions", "onnx-community/whisper-large-v3-turbo"),
    "diarization": (8299, "/v1/audio/diarizations", ""),
    "llm": (8100, "", "gpt-oss-120b"),
}
# Where each service lives behind the gateway. LiteLLM has no diarization API,
# so speakers go through a pass-through route whose path is configurable.
GATEWAY_PATHS = {"transcription": "/v1/audio/transcriptions", "llm": ""}


def _split_url(url, name):
    """(origin, port, path) of a configured default URL, with fallbacks."""
    port_fallback, path_fallback, _ = FALLBACKS[name]
    parsed = urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "", port_fallback, path_fallback
    port = parsed.port or (443 if parsed.scheme == "https" else port_fallback)
    return f"{parsed.scheme}://{parsed.hostname}", port, parsed.path.rstrip("/")


_asr_origin, _asr_port, _asr_path = _split_url(SPARK_WHISPER_URL, "transcription")
_diar_origin, _diar_port, _diar_path = _split_url(SPARK_DIARIZER_URL, "diarization")
_llm_origin, _llm_port, _llm_path = _split_url(OMNIROUTE_URL, "llm")
_base_url = _asr_origin or _diar_origin or _llm_origin or "http://127.0.0.1"

DEFAULTS = {
    "gateway": {
        "enabled": True,
        "url": "http://marlene.cxi.tul.cz:4000",
        # Never seeded from the environment, same as the service keys below.
        "api_key": "",
        "transcription_model": "whisper-large-v3-turbo",
        "llm_model": "spark/qwen3.6-a3b-35b-nvfp4",
        "diarization_path": "/diarizer/v1/audio/diarizations",
    },
    "ssh": {
        "enabled": False,
        "user": "",
        "host": "",
        "port": 22,
        "ssh_key": "",
    },
    # Used only for services without a tunnel; a tunnel always ends on
    # 127.0.0.1 at the service's local port.
    "base_url": _base_url,
    "transcription": {
        "local_port": _asr_port,
        "remote_port": _asr_port,
        "remote_host": "127.0.0.1",
        "path": _asr_path,
        "model": SPARK_WHISPER_MODEL or FALLBACKS["transcription"][2],
        "host": "" if _asr_origin in ("", _base_url) else _asr_origin,
        "ssh_host": "",
        # Bearer token for a gateway in front of the service (LiteLLM).
        # Never seeded from the environment, same as llm.api_key below.
        "api_key": "",
    },
    "diarization": {
        "local_port": _diar_port,
        "remote_port": _diar_port,
        "remote_host": "127.0.0.1",
        "path": _diar_path,
        "host": "" if _diar_origin in ("", _base_url) else _diar_origin,
        "ssh_host": "",
        # Local pyannote fallback only; gated models need approved HF access.
        "local_model": DIARIZE_MODEL,
        # Never HF_TOKEN here: this dict is the merge baseline for every
        # save, including unrelated ones, so seeding it from the environment
        # would silently clobber a stored token (or defeat forget_hf_token)
        # on the next save that doesn't mention hf_token. The env var is only
        # a first-run bootstrap value, applied once in get_connections().
        "hf_token": "",
        # Bearer token for a gateway in front of the remote diarizer.
        "api_key": "",
    },
    "llm": {
        "local_port": _llm_port,
        "remote_port": _llm_port,
        "remote_host": "127.0.0.1",
        "path": _llm_path,
        "model": OMNIROUTE_MODEL or FALLBACKS["llm"][2],
        "host": "" if _llm_origin in ("", _base_url) else _llm_origin,
        "ssh_host": "",
        # Bearer token for the OpenAI-compatible endpoint. Same reasoning as
        # diarization.hf_token: never seeded from the environment here, or an
        # unrelated save would clobber the stored key.
        "api_key": "",
    },
}

_lock = threading.RLock()
_cached = None


def _merge(defaults, values):
    merged = deepcopy(defaults)
    if not isinstance(values, dict):
        return merged
    for key, value in values.items():
        if key not in merged:
            continue
        if isinstance(merged[key], dict):
            if isinstance(value, dict):
                merged[key] = _merge(merged[key], value)
        elif isinstance(merged[key], bool):
            merged[key] = bool(value)
        elif not isinstance(value, (dict, list)) and value is not None:
            merged[key] = value
    return merged


def _clean_text(value, label, *, optional=False):
    value = str(value or "").strip()
    if not value and optional:
        return ""
    if not value:
        raise AppError(f"{label} is required.")
    if value.startswith("-") or any(char.isspace() for char in value):
        raise AppError(f"{label} is not valid.")
    return value


def _origin(value, label, *, optional=False):
    """Scheme + host only: the port always comes from the service itself."""
    value = str(value or "").strip().rstrip("/")
    if not value:
        if optional:
            return ""
        raise AppError(f"{label} is required.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError(f"{label} must be an http:// or https:// address.")
    if parsed.path or parsed.query or parsed.fragment:
        raise AppError(f"{label} must contain only scheme and host, e.g. http://127.0.0.1.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise AppError(f"{label} has an invalid port.") from exc
    if port is not None:
        raise AppError(f"{label} must not include a port; each service has its own port.")
    return f"{parsed.scheme}://{parsed.hostname}"


def _port(value, label, *, default=0):
    value = str(value if value is not None else "").strip()
    if not value:
        return default
    try:
        port = int(value)
    except ValueError as exc:
        raise AppError(f"{label} must be a number.") from exc
    if port == 0:
        return default
    if not 1 <= port <= 65535:
        raise AppError(f"{label} must be between 1 and 65535.")
    return port


def _path(value, label):
    value = str(value or "").strip()
    if not value:
        return ""
    if not value.startswith("/"):
        raise AppError(f"{label} API path must start with /.")
    return value.rstrip("/")


def _gateway_url(value):
    """Full base URL of the gateway: unlike a service origin it carries its port."""
    value = str(value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("LiteLLM address must be an http:// or https:// address.")
    if parsed.query or parsed.fragment:
        raise AppError("LiteLLM address must not contain a query or fragment.")
    try:
        parsed.port
    except ValueError as exc:
        raise AppError("LiteLLM address has an invalid port.") from exc
    return value


def normalize_connections(values):
    merged = _merge(DEFAULTS, values)
    gateway = merged["gateway"]
    gateway["url"] = _gateway_url(gateway["url"])
    gateway["api_key"] = _clean_text(gateway["api_key"], "LiteLLM API key", optional=True)
    gateway["transcription_model"] = str(gateway["transcription_model"] or "").strip()
    gateway["llm_model"] = str(gateway["llm_model"] or "").strip()
    gateway["diarization_path"] = _path(gateway["diarization_path"], "Speaker recognition")
    ssh = merged["ssh"]
    ssh["enabled"] = bool(ssh["enabled"])
    ssh["user"] = _clean_text(ssh["user"], "SSH user", optional=True)
    ssh["host"] = _clean_text(ssh["host"], "SSH host", optional=True)
    ssh["port"] = _port(ssh["port"], "SSH port", default=22)
    ssh["ssh_key"] = str(ssh["ssh_key"] or "").strip()
    merged["base_url"] = _origin(merged["base_url"], "Base address", optional=True)

    for name in SERVICES:
        service, label = merged[name], LABELS[name]
        service["local_port"] = _port(service["local_port"], f"{label} local port")
        service["remote_port"] = _port(
            service["remote_port"], f"{label} remote port", default=service["local_port"]
        )
        service["remote_host"] = _clean_text(
            service["remote_host"], f"{label} remote address", optional=True
        ) or "127.0.0.1"
        service["path"] = _path(service["path"], label)
        service["host"] = _origin(service["host"], f"{label} host", optional=True)
        service["ssh_host"] = _clean_text(service["ssh_host"], f"{label} SSH host", optional=True)

    merged["transcription"]["model"] = str(merged["transcription"]["model"] or "").strip()
    merged["llm"]["model"] = str(merged["llm"]["model"] or "").strip()
    merged["diarization"]["local_model"] = str(merged["diarization"]["local_model"] or "").strip()
    merged["diarization"]["hf_token"] = _clean_text(
        merged["diarization"]["hf_token"], "HuggingFace token", optional=True
    )
    for name in SERVICES:
        merged[name]["api_key"] = _clean_text(
            merged[name]["api_key"], f"{LABELS[name]} API key", optional=True
        )

    used = {}
    for name in SERVICES:
        port = merged[name]["local_port"]
        if not port:
            continue
        if port in used:
            raise AppError(
                f"{LABELS[name]} and {LABELS[used[port]]} both use local port {port}; "
                "give each service its own local port."
            )
        used[port] = name
    return merged


def ssh_host_for(config, name):
    """The machine a service is tunnelled from: its override, else the shared host."""
    return config[name]["ssh_host"] or config["ssh"]["host"]


def uses_tunnels(config):
    """SSH forwards only matter for own servers; the gateway is reached directly."""
    return config["ssh"]["enabled"] and not config["gateway"]["enabled"]


def is_tunnelled(config, name):
    service = config[name]
    return bool(
        uses_tunnels(config)
        and ssh_host_for(config, name)
        and service["local_port"]
        and service["remote_port"]
    )


def endpoint_for(config, name):
    """Full URL of one service, or "" when it has no remote endpoint."""
    gateway = config["gateway"]
    if gateway["enabled"]:
        if name == "diarization":
            path = gateway["diarization_path"]
            return gateway["url"] + path if path else ""
        return gateway["url"] + GATEWAY_PATHS[name]
    service = config[name]
    if not service["local_port"]:
        return ""
    if is_tunnelled(config, name):
        return f"http://127.0.0.1:{service['local_port']}{service['path']}"
    origin = service["host"] or config["base_url"]
    return f"{origin}:{service['local_port']}{service['path']}" if origin else ""


def get_connections():
    global _cached
    with _lock:
        if _cached is None:
            try:
                values = json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
                bootstrap = False
            except (OSError, json.JSONDecodeError):
                values = {}
                bootstrap = True
            _cached = normalize_connections(values)
            if bootstrap and HF_TOKEN and not _cached["diarization"]["hf_token"]:
                _cached["diarization"]["hf_token"] = HF_TOKEN
            if bootstrap and GPT_OSS_API_KEY and not _cached["llm"]["api_key"]:
                _cached["llm"]["api_key"] = GPT_OSS_API_KEY
        return deepcopy(_cached)


def endpoints():
    config = get_connections()
    return {name: endpoint_for(config, name) for name in SERVICES}


def get_connection(name):
    """One service's effective settings plus its resolved ``endpoint``. Behind
    the gateway, its key and model names replace the service's own."""
    config = get_connections()
    connection = {**config[name], "endpoint": endpoint_for(config, name)}
    gateway = config["gateway"]
    if gateway["enabled"]:
        connection["api_key"] = gateway["api_key"]
        if name in GATEWAY_PATHS:
            connection["model"] = gateway[f"{name}_model"]
    return connection


def bearer_headers(connection):
    """Authorization header from a service's stored key, or None without one."""
    key = connection.get("api_key")
    return {"Authorization": f"Bearer {key}"} if key else None


def public_connections():
    """Everything the browser may see: the stored secrets stay server-side."""
    config = get_connections()
    token = config["diarization"].pop("hf_token")
    config["diarization"]["hf_token_set"] = bool(token)
    for name in ("gateway", *SERVICES):
        api_key = config[name].pop("api_key")
        config[name]["api_key_set"] = bool(api_key)
    return config


def save_connections(values):
    """Persist settings. An empty ``hf_token`` / ``api_key`` keeps the stored
    one; the UI clears them explicitly with ``forget_hf_token`` and
    ``forget_<gateway|service>_api_key``, since the browser never receives the
    secret it would otherwise have to send back."""
    global _cached
    normalized = normalize_connections(values)
    asked = values if isinstance(values, dict) else {}
    stored = get_connections()
    if not normalized["diarization"]["hf_token"] and not asked.get("forget_hf_token"):
        normalized["diarization"]["hf_token"] = stored["diarization"]["hf_token"]
    for name in ("gateway", *SERVICES):
        if not normalized[name]["api_key"] and not asked.get(f"forget_{name}_api_key"):
            normalized[name]["api_key"] = stored[name]["api_key"]
    _validate_tunnel_config(normalized)
    with _lock:
        CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONNECTIONS_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(CONNECTIONS_PATH)
        _cached = normalized
    return deepcopy(normalized)


def _plan(config):
    """Per SSH host, the forwards a managed tunnel has to carry."""
    plan = {}
    for name in SERVICES:
        if not is_tunnelled(config, name):
            continue
        service = config[name]
        plan.setdefault(ssh_host_for(config, name), []).append(
            (
                name,
                service["local_port"],
                f"127.0.0.1:{service['local_port']}:{service['remote_host']}:{service['remote_port']}",
            )
        )
    return plan


def _validate_tunnel_config(config):
    if not uses_tunnels(config):
        return
    if not _plan(config):
        raise AppError(
            "Managed SSH needs an SSH host and at least one service with a local and remote port."
        )
    key = config["ssh"]["ssh_key"]
    if key and not Path(key).expanduser().is_file():
        raise AppError(f"SSH key was not found: {key}")


def _port_in_use(port):
    """A local port already bound elsewhere would make ssh -L fail on start."""
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


# ssh needs a moment to authenticate before it binds the forwards; probing a
# service before that lands as a bogus "connection refused".
FORWARD_TIMEOUT = 15.0
# A login test runs nothing on the remote machine: ssh -N -T opens no shell and
# no command. Success is proven by a throwaway -L forward binding locally,
# which only happens after authentication succeeds.
SSH_TEST_TIMEOUT = 20.0
# How often a supervised manager re-checks its tunnels. The backend can boot
# before the VPN is up (autostart at login), so a failed start is retried
# instead of leaving the services dead until someone opens Connections.
SUPERVISE_INTERVAL = 15.0


def _ssh_command(ssh):
    command = [
        "ssh", "-N", "-T",
        "-p", str(ssh["port"]),
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if ssh["ssh_key"]:
        command.extend(["-i", str(Path(ssh["ssh_key"]).expanduser())])
    return command


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_ssh(config):
    """Prove the SSH login works, before any service or real port is involved."""
    ssh = config["ssh"]
    host = ssh["host"] or next(
        (config[name]["ssh_host"] for name in SERVICES if config[name]["ssh_host"]), ""
    )
    if not host:
        raise AppError("Enter an SSH host first.")
    key = ssh["ssh_key"]
    if key and not Path(key).expanduser().is_file():
        raise AppError(f"SSH key was not found: {key}")

    target = f"{ssh['user']}@{host}" if ssh["user"] else host
    report = {"host": host, "target": target, "port": ssh["port"]}
    # Forwarded to a port nothing ever connects to; the local end is only used
    # as the "authenticated" signal.
    probe_port = _free_port()
    command = _ssh_command(ssh) + ["-L", f"127.0.0.1:{probe_port}:127.0.0.1:1", target]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return {**report, "ok": False, "detail": str(exc)}

    deadline = time.monotonic() + SSH_TEST_TIMEOUT
    try:
        while True:
            if _port_open(probe_port):
                return {**report, "ok": True, "detail": f"Signed in to {target} on port {ssh['port']}."}
            if process.poll() is not None:
                detail = process.stderr.read().strip() if process.stderr else ""
                return {
                    **report,
                    "ok": False,
                    "detail": (detail or f"ssh exited with status {process.returncode}")[:1000],
                }
            if time.monotonic() > deadline:
                return {
                    **report,
                    "ok": False,
                    "detail": f"No answer from {target} within {int(SSH_TEST_TIMEOUT)}s.",
                }
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class TunnelManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._processes = {}
        self._errors = {}
        self._services = {}
        # Set once the process is shutting down: the supervisor must not
        # resurrect tunnels that stop() just tore down.
        self._closing = threading.Event()

    def _reap(self):
        for host, process in list(self._processes.items()):
            returncode = process.poll()
            if returncode is None:
                continue
            detail = process.stderr.read().strip() if process.stderr else ""
            self._errors[host] = detail or f"ssh exited with status {returncode}"
            del self._processes[host]

    def start(self):
        config = get_connections()
        with self._lock:
            self.stop()
            self._errors = {}
            self._services = {}
            if not uses_tunnels(config):
                return self.status()
            try:
                _validate_tunnel_config(config)
                plan = _plan(config)
            except AppError as exc:
                self._services["configuration"] = []
                self._errors["configuration"] = str(exc)
                return self.status()

            ssh = config["ssh"]
            for host, forwards in plan.items():
                self._services[host] = [name for name, _, _ in forwards]
                busy = [str(port) for _, port, _ in forwards if _port_in_use(port)]
                if busy:
                    self._errors[host] = (
                        f"Local port {', '.join(busy)} is already in use. Close the process "
                        "holding it (often a hand-started ssh tunnel) and try again."
                    )
                    continue
                command = _ssh_command(ssh)
                for _, _, forward in forwards:
                    command.extend(["-L", forward])
                command.append(f"{ssh['user']}@{host}" if ssh["user"] else host)
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                except OSError as exc:
                    self._errors[host] = str(exc)
                    continue
                self._processes[host] = process

            self._await_forwards(plan)
            self._reap()
            return self.status()

    def _await_forwards(self, plan):
        """Block until every started forward accepts connections."""
        deadline = time.monotonic() + FORWARD_TIMEOUT
        for host, forwards in plan.items():
            process = self._processes.get(host)
            if process is None:
                continue
            for _, port, _ in forwards:
                while not _port_open(port):
                    if process.poll() is not None or time.monotonic() > deadline:
                        break
                    time.sleep(0.1)
                if _port_open(port) or process.poll() is not None:
                    continue
                self._errors[host] = (
                    f"The SSH tunnel did not open local port {port} within "
                    f"{int(FORWARD_TIMEOUT)}s. Check the VPN, the host, and your SSH key."
                )
                process.terminate()
                process.wait()
                del self._processes[host]
                break

    def stop(self):
        with self._lock:
            for process in self._processes.values():
                if process.poll() is None:
                    process.terminate()
            for process in self._processes.values():
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._processes.clear()

    def restart(self):
        return self.start()

    def _needs_start(self):
        """True when a configured host has no live ssh process behind it."""
        if self._closing.is_set():
            return False
        config = get_connections()
        if not uses_tunnels(config):
            return False
        with self._lock:
            self._reap()
            try:
                _validate_tunnel_config(config)
                plan = _plan(config)
            except AppError:
                # A broken configuration is not fixed by retrying; the user
                # corrects it in Connections, which calls restart() itself.
                return False
            return any(host not in self._processes for host in plan)

    def supervise(self):
        """Start the tunnels and keep restarting the ones that are down.

        Runs for the lifetime of the process. The backend now starts at login,
        long before the VPN is connected, so the first attempt usually fails;
        without this loop the remote engine would stay unreachable until
        someone pressed Reconnect.
        """
        self.start()
        while not self._closing.wait(SUPERVISE_INTERVAL):
            if self._needs_start():
                self.start()

    def close(self):
        """Stop supervising and tear the tunnels down."""
        self._closing.set()
        self.stop()

    def status(self):
        with self._lock:
            self._reap()
            config = get_connections()
            hosts = sorted(set(self._services) | set(self._errors))
            return {
                "enabled": uses_tunnels(config),
                "hosts": [
                    {
                        "host": host,
                        "services": self._services.get(host, []),
                        "state": "running" if host in self._processes else "error",
                        "error": self._errors.get(host),
                    }
                    for host in hosts
                ],
            }


tunnels = TunnelManager()
