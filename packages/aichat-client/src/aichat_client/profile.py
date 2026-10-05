"""Where the CLI finds its identity.

Order: ``--token`` flag, ``AICHAT_TOKEN`` env, then a profile file chosen
by ``--profile`` or ``AICHAT_PROFILE``. With ``AICHAT_REQUIRE_PROFILE=1``
the env token is ignored (for in-process subagents that share an env).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8765"


def config_dir() -> Path:
    home = os.environ.get("AICHAT_HOME")
    if home:
        return Path(home)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "aichat"


def profile_path(name: str) -> Path:
    return config_dir() / "profiles" / f"{name}.json"


def operator_key_path() -> Path:
    return config_dir() / "operator_key"


def operator_key(explicit: str | None = None) -> str | None:
    """The flag, then ``$AICHAT_OPERATOR_KEY``, then the key file written by install.sh."""
    if explicit:
        return explicit
    if os.environ.get("AICHAT_OPERATOR_KEY"):
        return os.environ["AICHAT_OPERATOR_KEY"]
    path = operator_key_path()
    return path.read_text().strip() or None if path.exists() else None


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    token: str
    title: str = ""  # session (namespace) title, set by `aichat session start`


def save_profile(profile: Profile) -> Path:
    path = profile_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(
        {"name": profile.name, "url": profile.url, "token": profile.token, "title": profile.title}
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.chmod(path, 0o600)
    return path


def load_profile(name: str) -> Profile | None:
    path = profile_path(name)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Profile(
        name=data["name"], url=data["url"], token=data["token"], title=data.get("title", "")
    )


def list_profiles() -> list[Profile]:
    folder = config_dir() / "profiles"
    names = sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []
    return [p for p in (load_profile(n) for n in names) if p is not None]


@dataclass(frozen=True)
class Credentials:
    url: str
    token: str | None
    source: str


def resolve(url: str | None, token: str | None, profile: str | None) -> Credentials:
    env_url = url or os.environ.get("AICHAT_URL")
    if token:
        return Credentials(env_url or DEFAULT_URL, token, "--token")
    require_profile = os.environ.get("AICHAT_REQUIRE_PROFILE") == "1"
    env_token = os.environ.get("AICHAT_TOKEN")
    if env_token and not require_profile and not profile:
        return Credentials(env_url or DEFAULT_URL, env_token, "AICHAT_TOKEN")
    name = profile or os.environ.get("AICHAT_PROFILE")
    if name:
        found = load_profile(name)
        if found is not None:
            return Credentials(url or found.url, found.token, f"profile:{name}")
    return Credentials(env_url or DEFAULT_URL, None, "none")
