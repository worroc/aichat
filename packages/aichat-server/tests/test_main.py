"""Idempotent start: probe what listens on the hub's port."""

import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import uvicorn
from aichat_server.config import Settings
from aichat_server.control import _selected
from aichat_server.lifecycle import probe, read_pid, stop
from aichat_server.main import create_app, hub_service


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NotAHub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def _hub(port: int, host: str = "127.0.0.1"):
    return hub_service(Settings(host=host, port=port, operator_key="k", store="memory"))


def test_probe_free_port():
    assert probe(_hub(_free_port())) == "free"


def test_probe_other_program():
    server = HTTPServer(("127.0.0.1", 0), _NotAHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert probe(_hub(server.server_port)) == "busy"
    finally:
        server.shutdown()


def test_probe_running_hub():
    port = _free_port()
    app = create_app(Settings(operator_key="k", store="memory"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert probe(_hub(port)) == "ours"
        assert probe(_hub(port, "0.0.0.0")) == "ours"
    finally:
        server.should_exit = True


HUB = [sys.executable, "-m", "aichat_server.control"]


def _hub_env(tmp_path, port: int) -> dict[str, str]:
    return {
        **os.environ,
        "AICHAT_PORT": str(port),
        "AICHAT_STORE": "memory",
        "AICHAT_OPERATOR_KEY": "k",
        "AICHAT_PID_FILE": str(tmp_path / "hub-{port}.pid"),
        "AICHAT_UI_PORT": str(_free_port()),
        "AICHAT_UI_PID_FILE": str(tmp_path / "ui-{port}.pid"),
        "AICHAT_LOG_DIR": str(tmp_path / "logs"),
        "NO_COLOR": "1",
    }


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*HUB, *args], env=env, capture_output=True, text=True, check=False, timeout=60)


def _wait_for(port: int, state: str) -> None:
    for _ in range(100):
        if probe(_hub(port)) == state:
            return
        time.sleep(0.1)
    raise AssertionError(f"port {port} never became {state}")


def test_second_start_reports_and_stop_is_graceful(tmp_path, monkeypatch):
    port = _free_port()
    env = _hub_env(tmp_path, port)
    hub = subprocess.Popen([*HUB, "start", "hub", "--foreground"], env=env)
    try:
        _wait_for(port, "ours")
        again = _cli(env, "start", "hub")
        assert again.returncode == 0
        assert f"already running on http://127.0.0.1:{port} (pid {hub.pid})" in again.stdout
        for key in ("AICHAT_PORT", "AICHAT_PID_FILE"):
            monkeypatch.setenv(key, env[key])
        svc = hub_service(Settings())
        assert stop(svc) == hub.pid
        assert hub.wait(5) == 0
        assert not svc.pid_file.exists()
    finally:
        hub.kill()


def test_detached_start_stop(tmp_path):
    port = _free_port()
    env = _hub_env(tmp_path, port)
    started = _cli(env, "start", "hub")
    try:
        assert started.returncode == 0, started.stderr
        assert f"started aichat-server on http://127.0.0.1:{port}" in started.stdout
        assert probe(_hub(port)) == "ours"
        assert (tmp_path / "logs" / f"aichat-server-{port}.log").read_text()  # uvicorn wrote to the log
        stopped = _cli(env, "stop", "hub")
        assert "stopped aichat-server (pid" in stopped.stdout
        assert probe(_hub(port)) == "free"
        assert "is not running" in _cli(env, "stop", "hub").stdout
    finally:
        _cli(env, "stop", "hub")


def test_log_dir_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    env = {**_hub_env(tmp_path, _free_port()), "AICHAT_LOG_DIR": str(locked / "aichat")}
    result = _cli(env, "start", "hub")
    assert result.returncode == 1
    assert "sudo mkdir -p" in result.stderr


def test_status_and_restart(tmp_path):
    port = _free_port()
    env = _hub_env(tmp_path, port)
    try:
        down = _cli(env, "status").stdout.splitlines()
        assert down[0].split() == ["SERVICE", "STATUS", "PID", "URL", "LOG"]
        assert down[1].split()[:2] == ["hub", "DOWN"] and down[2].split()[:2] == ["ui", "DOWN"]
        assert _cli(env, "restart", "hub").returncode == 0
        first = read_pid(tmp_path / f"hub-{port}.pid")
        up = _cli(env, "status", "hub").stdout.splitlines()
        assert len(up) == 2 and up[1].split()[:3] == ["hub", "UP", str(first)]
        restarted = _cli(env, "restart", "hub")
        assert f"stopping aichat-server (pid {first})" in restarted.stdout
        assert read_pid(tmp_path / f"hub-{port}.pid") not in {None, first}
    finally:
        _cli(env, "stop", "hub")


def test_cli_rejects_bad_names():
    assert _cli(dict(os.environ), "start", "db").returncode == 2
    assert "exactly one service" in _cli(dict(os.environ), "start", "--foreground").stderr
    bare = _cli(dict(os.environ))  # no subcommand: full help, nothing starts
    assert bare.returncode == 0 and "examples:" in bare.stdout


def test_stop_runs_in_reverse_order():
    assert _selected([], "start") == ["hub", "ui"]
    assert _selected(["ui", "hub"], "stop") == ["ui", "hub"]
    assert _selected(["ui"], "restart") == ["ui"]
