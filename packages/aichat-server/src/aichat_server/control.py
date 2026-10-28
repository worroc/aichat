"""`aichat-server`: start, stop, restart and show the status of the hub and the UI.

Like `cong`: each subcommand takes service names (`hub`, `ui`); no names means
all of them. The per-service logic lives in `lifecycle.py`.
"""

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from . import lifecycle, main, startlog, ui_dev
from .config import Settings

HELP = """services:
  hub   the chat hub (FastAPI), http://127.0.0.1:8765
  ui    the operator UI (Vite dev server), http://127.0.0.1:5173

settings (environment variables):
  AICHAT_HOST, AICHAT_PORT        where the hub listens (127.0.0.1, 8765)
  AICHAT_STORE                    sqlite (default, kept on disk) or memory (lost on stop)
  AICHAT_DB_PATH                  SQLite file (~/.config/aichat/aichat.db)
  AICHAT_OPERATOR_KEY             operator key; else read from ~/.config/aichat/operator_key
  AICHAT_PID_FILE                 hub pid file (~/.config/aichat/aichat-server-{port}.pid)
  AICHAT_UI_HOST, AICHAT_UI_PORT  where the UI listens (127.0.0.1, 5173)
  AICHAT_URL                      the hub the UI talks to (http://127.0.0.1:8765)
  AICHAT_UI_DIR                   the ui/ folder of the aichat repo (found next to this package)
  AICHAT_UI_PID_FILE              UI pid file (~/.config/aichat/aichat-server-ui-{port}.pid)
  AICHAT_LOG_DIR                  log folder (/var/log/aichat); files: <name>-<port>.log
  more hub limits: see aichat_server/config.py

examples:
  aichat-server start                  # hub and UI in the background; reports the ones already running
  aichat-server start hub              # only the hub
  aichat-server status                 # table: service, status, pid, version, started, url, log
  aichat-server stop ui                # graceful stop
  aichat-server restart                # graceful stop, then start
  aichat-server start hub --foreground # attached to this shell
"""
ORDER = ("hub", "ui")  # start order; stop runs in reverse
COLORS = {"up": "32", "down": "31", "busy": "33"}


@dataclass(frozen=True)
class Entry:
    service: lifecycle.Service
    serve: Callable[[], None]


def _entries() -> dict[str, Entry]:
    hub, ui = Settings(), ui_dev.UiSettings()
    return {
        "hub": Entry(main.hub_service(hub), lambda: main.serve(hub)),
        "ui": Entry(ui_dev.ui_service(ui), lambda: ui_dev.serve(ui)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aichat-server",
        description="Run the aichat hub and operator UI in the background.",
        epilog=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="action", metavar="{start,stop,restart,status}")
    for action, text in (
        ("start", "start services in the background; report the ones already running"),
        ("stop", "stop services gracefully"),
        ("restart", "stop services gracefully, then start them"),
        ("status", "show whether services run"),
    ):
        cmd = sub.add_parser(action, help=text, description=text)
        cmd.add_argument("names", nargs="*", metavar="SERVICE", help="hub, ui (default: all)")
        if action == "start":
            cmd.add_argument("--foreground", action="store_true", help="run one service attached to this shell")
    return parser


def _selected(names: list[str], action: str) -> list[str]:
    chosen = set(names) or set(ORDER)
    ordered = [name for name in ORDER if name in chosen]
    return ordered[::-1] if action == "stop" else ordered


def _paint(text: str) -> str:
    """Colour a padded status cell by its state when printing to a terminal."""
    state = text.strip().lower()
    if state not in COLORS or not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{COLORS[state]}m{text}\033[0m"


def _ago(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return f"{int(seconds)}s ago"


def _started(info: startlog.StartInfo | None) -> str:
    if info is None:
        return ""
    now = datetime.now().astimezone()
    when = info.started.strftime("%H:%M:%S" if info.started.date() == now.date() else "%Y-%m-%d %H:%M")
    return f"{when} ({_ago((now - info.started).total_seconds())})"


def _version(st: lifecycle.Status, current: str) -> str:
    """The running version; `(old)` when the code on disk has changed since it started."""
    if st.start is None:
        return "?" if st.state == "up" else ""
    return st.start.version + ("" if st.start.version == current else " (old)")


def _status_table(entries: dict[str, Entry], names: list[str]) -> None:
    current = startlog.current_version()
    rows = [("SERVICE", "STATUS", "PID", "VERSION", "STARTED", "URL", "LOG")]
    for name in names:
        svc = entries[name].service
        st = lifecycle.status(svc)
        row = (name, st.state.upper(), str(st.pid or ""), _version(st, current), _started(st.start))
        rows.append((*row, svc.url, str(svc.log_file)))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        cells[1] = _paint(cells[1])
        print("  ".join(cells).rstrip())


def _act(action: str, entry: Entry, foreground: bool) -> None:
    if action == "start":
        lifecycle.start(entry.service, entry.serve, foreground)
    elif action == "stop":
        lifecycle.stop_service(entry.service)
    else:
        lifecycle.restart(entry.service)


def _run_each(action: str, entries: dict[str, Entry], names: list[str], foreground: bool) -> int:
    """Run the action on each service; one failure does not stop the others. Returns the exit code."""
    code = 0
    for name in names:
        try:
            _act(action, entries[name], foreground)
        except lifecycle.LifecycleError as exc:
            print(f"{entries[name].service.name}: {exc}", file=sys.stderr)
            code = 1
    return code


def run(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.action is None:
        parser.print_help()
        return
    unknown = set(args.names) - set(ORDER)
    if unknown:
        parser.error(f"unknown service(s): {', '.join(sorted(unknown))}; known: {', '.join(ORDER)}")
    names = _selected(args.names, args.action)
    foreground = getattr(args, "foreground", False)
    if foreground and len(names) != 1:
        parser.error("--foreground runs exactly one service: name it (hub or ui)")
    entries = _entries()
    if args.action == "status":
        _status_table(entries, names)
        return
    raise SystemExit(_run_each(args.action, entries, names, foreground))


if __name__ == "__main__":
    run()
