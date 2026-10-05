"""`aichat-server-ui`: run the operator UI (Vite dev server) like `aichat-server`.

It starts in the background, reports when it already runs, and has --stop,
--restart and --foreground. Vite forwards API calls to the hub at $AICHAT_URL.
"""

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from . import lifecycle

# packages/aichat-server/src/aichat_server/ui_dev.py -> repo root -> ui/
DEFAULT_UI_DIR = Path(__file__).resolve().parents[4] / "ui"
UI_MARKER = b"<title>aichat operator</title>"
STOP_CHILD_TIMEOUT = 10.0

HELP = """settings (environment variables):
  AICHAT_UI_HOST, AICHAT_UI_PORT  where the UI listens (127.0.0.1, 5173)
  AICHAT_URL                      the hub the UI talks to (http://127.0.0.1:8765)
  AICHAT_UI_DIR                   the ui/ folder of the aichat repo (found next to this package)
  AICHAT_UI_PID_FILE              pid file (~/.config/aichat/aichat-server-ui-{port}.pid)
  AICHAT_LOG_DIR                  log folder (/var/log/aichat); log file: aichat-server-ui-{port}.log

examples:
  aichat-server-ui              # start in the background, or report that it already runs
  aichat-server-ui --stop       # graceful stop
  aichat-server-ui --restart    # graceful stop, then start
  aichat-server-ui --foreground # attached to this shell
"""


class UiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AICHAT_UI_")

    host: str = "127.0.0.1"
    port: int = 5173
    dir: str = str(DEFAULT_UI_DIR)
    pid_file: str = "~/.config/aichat/aichat-server-ui-{port}.pid"


def _ui_dir(settings: UiSettings) -> Path:
    path = Path(settings.dir).expanduser()
    if not (path / "package.json").is_file():
        raise lifecycle.LifecycleError(f"no UI found at {path}; set AICHAT_UI_DIR to the repo's ui/ folder")
    if shutil.which("npm") is None:
        raise lifecycle.LifecycleError("npm not found; install Node 20+")
    return path


def _install_deps(settings: UiSettings, log_path: Path) -> None:
    """Run `npm install` once, before detaching, so a fresh checkout just works."""
    path = _ui_dir(settings)
    if (path / "node_modules").is_dir():
        return
    print(f"installing UI dependencies (npm install) ...; output in {log_path}")
    with log_path.open("ab") as log:
        done = subprocess.run(["npm", "install"], cwd=path, stdout=log, stderr=log, check=False)
    if done.returncode != 0:
        raise lifecycle.LifecycleError(f"npm install failed; see {log_path}")


def _serve(settings: UiSettings) -> None:
    """Run Vite as a child process group; stop the whole group when we are stopped."""
    argv = ["npm", "run", "dev", "--", "--host", settings.host, "--port", str(settings.port), "--strictPort"]
    child = subprocess.Popen(argv, cwd=_ui_dir(settings), start_new_session=True)
    try:
        code = child.wait()
    finally:
        _stop_child(child)
    if code:
        raise SystemExit(code)


def _stop_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(STOP_CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"vite (pid {child.pid}) did not stop in {STOP_CHILD_TIMEOUT:.0f} s", file=sys.stderr)


def ui_service(settings: UiSettings) -> lifecycle.Service:
    return lifecycle.Service(
        name="aichat-server-ui",
        host=settings.host,
        port=settings.port,
        pid_file=lifecycle.pid_file(settings.pid_file, settings.port),
        probe_path="/",
        is_ours=lambda body: UI_MARKER in body,
        port_env="AICHAT_UI_PORT",
        relaunch=[sys.executable, "-m", "aichat_server.ui_dev", "--foreground"],
        start_timeout=60.0,
        before_start=lambda log_path: _install_deps(settings, log_path),
    )


def run(argv: list[str] | None = None) -> None:
    """Start the UI; safe to run again."""
    parser = lifecycle.command_parser(
        "aichat-server-ui",
        "Run the aichat operator UI (Vite). If it already runs on the host and port, say so and exit.",
        HELP,
    )
    args = parser.parse_args(argv)
    settings = UiSettings()
    lifecycle.run_service(ui_service(settings), args, lambda: _serve(settings))


if __name__ == "__main__":
    run()
