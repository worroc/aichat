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


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    token: str


def save_profile(profile: Profile) -> Path:
    path = profile_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps({"name": profile.name, "url": profile.url, "token": profile.token})
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
    return Profile(name=data["name"], url=data["url"], token=data["token"])


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
