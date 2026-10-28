"""The start line a service writes to its log, and reading it back.

A service started by `aichat-server start` writes one line to its log when it
starts: ``aichat-start: pid=N version=V started=ISO``. `status` and `start`
read the line that matches the running pid, so they show which code runs and
since when.
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

MARKER = "aichat-start:"
GIT_TIMEOUT = 5.0
SOURCE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class StartInfo:
    pid: int
    version: str
    started: datetime


def _git_revision() -> str | None:
    """Short commit of the source checkout, with `-dirty` for local changes; None outside git."""
    try:
        done = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--abbrev=7"],
            cwd=SOURCE_DIR, capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def current_version() -> str:
    """Package version plus the git commit of the code on disk, e.g. ``0.1.0+d8c415a``."""
    version = metadata.version("aichat-server")
    revision = _git_revision()
    return f"{version}+{revision}" if revision else version


def start_line(pid: int, version: str, started: datetime) -> str:
    return f"{MARKER} pid={pid} version={version} started={started.isoformat(timespec='seconds')}"


def _parse(line: str) -> StartInfo | None:
    fields = dict(part.split("=", 1) for part in line[len(MARKER):].split() if "=" in part)
    try:
        return StartInfo(int(fields["pid"]), fields["version"], datetime.fromisoformat(fields["started"]))
    except (KeyError, ValueError):
        return None


def find_start(log: Path, pid: int) -> StartInfo | None:
    """The last start line for this pid in the log, if any."""
    found = None
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(MARKER) and (info := _parse(line)) and info.pid == pid:
                    found = info
    except OSError:
        return None
    return found
