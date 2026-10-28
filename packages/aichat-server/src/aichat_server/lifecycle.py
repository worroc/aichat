"""Start, detect, stop and report a local service: the hub or the UI.

The `aichat-server` command (`control.py`) drives these for each service:

* start: runs the service in the background, detached from the shell, with
  its output in ``$AICHAT_LOG_DIR/<name>-<port>.log`` (``/var/log/aichat``).
  If the service already answers on its host and port, it says so.
* stop: SIGTERM, then wait until it has finished. A service that does not
  stop is never killed.
* restart: stop, then start.
* status: up, down, or busy (another program holds the port).
"""

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT = 2.0
STOP_TIMEOUT = 30.0
POLL = 0.2
DEFAULT_LOG_DIR = "/var/log/aichat"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LifecycleError(Exception):
    """The service cannot be started or stopped; the message says why."""


@dataclass(frozen=True)
class Service:
    name: str  # command name, used in messages
    host: str
    port: int
    pid_file: Path
    probe_path: str  # GET this to recognise the service
    is_ours: Callable[[bytes], bool]  # does the response body come from this service?
    port_env: str  # the env var that changes the port, named in errors
    relaunch: Sequence[str]  # command that runs this service with --foreground
    start_timeout: float = 30.0
    before_start: Callable[[Path], None] | None = None  # runs before detaching; gets the log path

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def log_file(self) -> Path:
        return Path(os.environ.get("AICHAT_LOG_DIR") or DEFAULT_LOG_DIR) / f"{self.name}-{self.port}.log"


def hub_says_ok(body: bytes) -> bool:
    try:
        return json.loads(body) == {"ok": True}
    except ValueError:
        return False


def _probe_url(host: str, port: int, path: str) -> str:
    if host in {"", "0.0.0.0"}:
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    return f"http://{f'[{host}]' if ':' in host else host}:{port}{path}"


def probe(svc: Service) -> str:
    """"ours" if the service answers there, "free" if nothing listens, "busy" if another program does."""
    try:
        with _NO_PROXY.open(_probe_url(svc.host, svc.port, svc.probe_path), timeout=PROBE_TIMEOUT) as resp:
            if svc.is_ours(resp.read()):
                return "ours"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return "free"
    except OSError:
        pass
    return "busy"


def pid_file(template: str, port: int) -> Path:
    return Path(template.format(port=port)).expanduser()


def read_pid(path: Path) -> int | None:
    """The pid in the file, if that process still exists."""
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def write_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")


def remove_pid(path: Path) -> None:
    """Remove the pid file only if it is ours (a newer process may have replaced it)."""
    try:
        if int(path.read_text().strip()) == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def _reap(pid: int) -> None:
    """If the service is our own child (tests, scripts), collect it so it does not linger as a zombie."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _gone(pid: int) -> bool:
    _reap(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop(svc: Service, timeout: float = STOP_TIMEOUT) -> int:
    """Ask the running service to stop (SIGTERM) and wait until it is gone. Returns its pid."""
    pid = read_pid(svc.pid_file)
    if pid is None:
        raise LifecycleError(
            f"{svc.name} answers on {svc.host}:{svc.port} but {svc.pid_file} has no live pid; "
            "it was started another way. Stop it once by hand (kill PID)."
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _gone(pid) and probe(svc) == "free":
            return pid
        time.sleep(POLL)
    raise LifecycleError(f"{svc.name} (pid {pid}) did not stop within {timeout:.0f} s; it was left running")


def describe(svc: Service) -> str:
    pid = read_pid(svc.pid_file)
    return f"{svc.name} is already running on {svc.url}" + (f" (pid {pid})" if pid else "")


def exit_on_term(signum: int, frame: object) -> None:
    """SIGTERM handler: exit normally, so `finally` blocks (pid file cleanup) run."""
    raise SystemExit(0)


def _open_log(svc: Service):
    path = svc.log_file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab")
    except PermissionError:
        raise LifecycleError(
            f"cannot write {path}. Create the folder once: "
            f"sudo mkdir -p {path.parent} && sudo chown $USER: {path.parent}  "
            "(or set AICHAT_LOG_DIR)"
        ) from None


def _wait_started(svc: Service, child: subprocess.Popen) -> None:
    deadline = time.monotonic() + svc.start_timeout
    while time.monotonic() < deadline:
        if probe(svc) == "ours":
            return
        if child.poll() is not None:
            raise LifecycleError(f"{svc.name} exited with code {child.returncode}; see {svc.log_file}")
        time.sleep(POLL)
    raise LifecycleError(f"{svc.name} did not answer within {svc.start_timeout:.0f} s; see {svc.log_file}")


def _start_detached(svc: Service) -> None:
    """Start the service as its own session (no terminal), output appended to its log file."""
    with _open_log(svc) as log:
        if svc.before_start is not None:
            svc.before_start(svc.log_file)
        child = subprocess.Popen(
            list(svc.relaunch), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
        )
    _wait_started(svc, child)
    print(f"started {svc.name} on {svc.url} (pid {child.pid}); logs: {svc.log_file}")


def _run_foreground(svc: Service, serve: Callable[[], None]) -> None:
    write_pid(svc.pid_file)
    signal.signal(signal.SIGTERM, exit_on_term)
    try:
        serve()
    finally:
        remove_pid(svc.pid_file)


def _check_port(svc: Service) -> str:
    state = probe(svc)
    if state == "busy":
        raise LifecycleError(f"{svc.host}:{svc.port} is used by another program; set {svc.port_env}")
    return state


def start(svc: Service, serve: Callable[[], None], foreground: bool = False) -> None:
    """Start the service unless it already answers; detached unless `foreground`."""
    if _check_port(svc) == "ours":
        print(describe(svc))
    elif foreground:
        _run_foreground(svc, serve)
    else:
        _start_detached(svc)


def stop_service(svc: Service) -> None:
    """Stop the service gracefully, or say that it is not running."""
    if probe(svc) != "ours":
        print(f"{svc.name} is not running on {svc.url}")
        return
    print(f"stopped {svc.name} (pid {stop(svc)})")


def restart(svc: Service) -> None:
    """Stop the running service gracefully (if any), then start it detached."""
    if _check_port(svc) == "ours":
        print(f"stopping {svc.name} (pid {stop(svc)}) ...")
    _start_detached(svc)


@dataclass(frozen=True)
class Status:
    state: str  # "up", "down", or "busy" (another program holds the port)
    pid: int | None


def status(svc: Service) -> Status:
    state = {"ours": "up", "free": "down"}.get(probe(svc), "busy")
    return Status(state, read_pid(svc.pid_file) if state == "up" else None)
