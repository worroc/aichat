"""`aichat-server`: start, stop, restart and show the status of the hub and the UI;
`aichat-server cert` makes the HTTPS certificates (`tls.py`).

Like `cong`: each subcommand takes service names (`hub`, `ui`); no names means
all of them. The per-service logic lives in `lifecycle.py`.
"""

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from aichat_protocol import envfile

from . import lifecycle, main, startlog, tls, ui_dev
from .config import Settings

HELP = """services:
  hub   the chat hub (FastAPI), http://127.0.0.1:8765 (https:// with TLS)
  ui    the operator UI (Vite dev server), http://127.0.0.1:5173 (https:// with TLS)

settings (environment variables):
  AICHAT_HOST, AICHAT_PORT        where the hub listens (127.0.0.1, 8765)
  AICHAT_STORE                    sqlite (default, kept on disk) or memory (lost on stop)
  AICHAT_DEV                      1 = local test hub: uses operator_key.dev and aichat-dev.db
  AICHAT_DB_PATH                  SQLite file (~/.config/aichat/aichat.db; aichat-dev.db in dev)
  AICHAT_OPERATOR_KEY             operator key; else read from AICHAT_OPERATOR_KEY_FILE
  AICHAT_OPERATOR_KEY_FILE        (~/.config/aichat/operator_key; operator_key.dev in dev)
  AICHAT_PID_FILE                 hub pid file (~/.config/aichat/aichat-server-{port}.pid)
  AICHAT_UI_HOST, AICHAT_UI_PORT  where the UI listens (127.0.0.1, 5173)
  AICHAT_URL                      the hub the UI talks to (http://127.0.0.1:8765)
  AICHAT_UI_DIR                   the ui/ folder of the aichat repo (found next to this package)
  AICHAT_UI_PID_FILE              UI pid file (~/.config/aichat/aichat-server-ui-{port}.pid)
  AICHAT_ENV_FILE                 settings file read first (~/.config/aichat/aichat.env); env wins
  AICHAT_LOG_DIR                  log folder (/var/log/aichat); files: <name>-<port>.log
  AICHAT_TLS_CERT, AICHAT_TLS_KEY HTTPS for hub and UI when both are set (made by `aichat-server cert`)
  AICHAT_CA_CERT                  the CA the UI trusts for an https:// hub (~/.config/aichat/tls/ca.pem)
  AICHAT_TLS_DIR                  where `cert` writes its files (~/.config/aichat/tls)
  AICHAT_TLS_SANS                 extra names/IPs for `cert`, comma-separated
  more hub limits: see aichat_server/config.py

examples:
  aichat-server start                  # hub and UI in the background; reports the ones already running
  aichat-server start hub              # only the hub
  aichat-server status                 # table: service, status, pid, version, started, url, log
  aichat-server stop ui                # graceful stop
  aichat-server restart                # graceful stop, then start
  aichat-server start hub --foreground # attached to this shell
  AICHAT_DEV=1 aichat-server start     # local test hub: dev key and dev db, never the real key
  aichat-server cert                   # make the CA and server cert once; prints how to use them
  aichat-server cert --force --san 203.0.113.7   # reissue the server cert (new IP); the CA is kept
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
    sub = parser.add_subparsers(dest="action", metavar="{start,stop,restart,status,cert}")
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
    _cert_parser(sub)
    return parser


def _cert_parser(sub) -> None:
    text = "make the HTTPS CA and server certificate (kept if they exist); print how to use them"
    cmd = sub.add_parser("cert", help=text, description=text)
    cmd.add_argument("--force", action="store_true", help="reissue the server cert (new IPs); keeps the CA")
    cmd.add_argument("--new-ca", action="store_true", help="also replace the CA; every client needs the new ca.pem")
    cmd.add_argument("--san", action="append", default=[], metavar="NAME_OR_IP",
                     help="extra name or IP for the cert, e.g. a NAT address (repeatable; also $AICHAT_TLS_SANS)")


def _cert(args: argparse.Namespace) -> None:
    folder = tls.tls_dir()
    try:
        made = tls.make(folder, [*tls.extra_sans_from_env(), *args.san], force=args.force, new_ca=args.new_ca)
    except (OSError, tls.TlsError) as exc:
        print(f"aichat-server cert: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print(tls.report(folder, made, Settings().port, ui_dev.UiSettings().port))


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
    envfile.load()  # ~/.config/aichat/aichat.env: settings made once for every start
    parser = _parser()
    args = parser.parse_args(argv)
    if args.action is None:
        parser.print_help()
        return
    if args.action == "cert":
        _cert(args)
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
