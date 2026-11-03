"""The shared settings file, read by both `aichat-server` and the `aichat` CLI.

It lets a host be configured once (HTTPS files, listen address, hub URL), so
every later start and every new shell works without exporting variables.

File: ``$AICHAT_ENV_FILE``, else ``$AICHAT_HOME/aichat.env``, else
``~/.config/aichat/aichat.env`` (``$XDG_CONFIG_HOME`` is honoured). Format:
one ``AICHAT_X=value`` per line; ``#`` comments, blank lines, an ``export``
prefix and quotes around the value are allowed, so a shell can ``source`` it.

A variable already set in the environment wins over the file. Only
``AICHAT_*`` names are read, and never identities or secrets (`NOT_ALLOWED`):
those stay per agent and per human.
"""

import os
from pathlib import Path

NOT_ALLOWED = frozenset({"AICHAT_TOKEN", "AICHAT_PROFILE", "AICHAT_JOIN", "AICHAT_OPERATOR_KEY", "AICHAT_ENV_FILE"})


def env_file_path() -> Path:
    explicit = os.environ.get("AICHAT_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("AICHAT_HOME")
    if home:
        return Path(home) / "aichat.env"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "aichat" / "aichat.env"


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, value = line.removeprefix("export ").split("=", 1)
    name, value = name.strip(), value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not name.startswith("AICHAT_") or name in NOT_ALLOWED:
        return None
    return name, os.path.expanduser(value)


def parse(text: str) -> dict[str, str]:
    pairs = (_parse_line(line) for line in text.splitlines())
    return dict(pair for pair in pairs if pair is not None)


def load() -> dict[str, str]:
    """Copy the file's settings into ``os.environ`` where not set yet. Returns what was applied."""
    path = env_file_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    applied = {k: v for k, v in parse(text).items() if k not in os.environ}
    os.environ.update(applied)
    return applied
