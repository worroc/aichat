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
from aichat_server.lifecycle import probe, stop
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


HUB = [sys.executable, "-m", "aichat_server.main"]


def _hub_env(tmp_path, port: int) -> dict[str, str]:
    return {
        **os.environ,
        "AICHAT_PORT": str(port),
        "AICHAT_STORE": "memory",
        "AICHAT_OPERATOR_KEY": "k",
        "AICHAT_PID_FILE": str(tmp_path / "hub-{port}.pid"),
        "AICHAT_LOG_DIR": str(tmp_path / "logs"),
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
    hub = subprocess.Popen([*HUB, "--foreground"], env=env)
    try:
        _wait_for(port, "ours")
        again = _cli(env)
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
    started = _cli(env)
    try:
        assert started.returncode == 0, started.stderr
        assert f"started aichat-server on http://127.0.0.1:{port}" in started.stdout
        assert probe(_hub(port)) == "ours"
        assert (tmp_path / "logs" / f"aichat-server-{port}.log").read_text()  # uvicorn wrote to the log
        stopped = _cli(env, "--stop")
        assert "stopped aichat-server (pid" in stopped.stdout
        assert probe(_hub(port)) == "free"
        assert "is not running" in _cli(env, "--stop").stdout
    finally:
        _cli(env, "--stop")


def test_log_dir_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    env = {**_hub_env(tmp_path, _free_port()), "AICHAT_LOG_DIR": str(locked / "aichat")}
    result = _cli(env)
    assert result.returncode == 1
    assert "sudo mkdir -p" in result.stderr
