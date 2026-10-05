"""`aichat session`: start a titled chat and join it as its admin. `aichat profile`: saved logins."""

import re
from typing import Annotated

import httpx
import typer

from ..http import HubClient, HubError
from ..profile import Profile, list_profiles, load_profile, save_profile
from .common import Options, emit, fail, guarded, operator_client, opts

session_app = typer.Typer(
    help="Start or reuse a chat session (a namespace with a readable title).", no_args_is_help=True
)
profile_app = typer.Typer(help="Saved logins (profiles).", no_args_is_help=True)

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
SLUG_MAX = 40


def slug(title: str) -> str:
    """Profile name made from a title: "Shop API: login!" -> "shop-api-login"."""
    return _NOT_SLUG.sub("-", title.lower()).strip("-")[:SLUG_MAX].strip("-")


def _alive(profile: Profile) -> bool:
    """True when the profile's token still works on its hub."""
    try:
        HubClient(profile.url, profile.token).get("/me")
    except HubError as exc:
        if exc.code == "unauthorized":
            return False
        raise
    return True


def _titled(ops: HubClient, title: str) -> list[dict]:
    return [n for n in ops.get("/ops/namespaces") if n["title"] == title]


def _create(o: Options, ops: HubClient, title: str, name: str, provider: str, profile_name: str) -> Profile:
    created = ops.post("/ops/namespaces", {"title": title, "admin_name": name, "provider": provider})
    url = o.credentials().url
    joined = HubClient(url).post("/join", {"code": created["join_code"]})
    profile = Profile(name=profile_name, url=url, token=joined["token"], title=title)
    save_profile(profile)
    return profile


def _report(o: Options, profile: Profile, reused: bool) -> None:
    me = HubClient(profile.url, profile.token).get("/me")
    state = "is already running" if reused else "started"
    text = (
        f'session "{profile.title}" {state}\n'
        f"  you are {me['name']} ({me['role']}), saved in profile {profile.name}\n"
        f"  run every command as:  aichat --profile {profile.name} <command>\n"
        f'  humans watch and talk in the UI: namespace "{profile.title}"\n'
        f"  saved profiles:        aichat profile list"
    )
    data = {"title": profile.title, "profile": profile.name, "name": me["name"], "reused": reused}
    emit(o, data, text)


@session_app.command("start")
@guarded
def start(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help='readable session title, e.g. "shop-api: fix login bug"')],
    name: Annotated[
        str, typer.Option("--name", help="your chat name in the session; you become its admin")
    ] = "main",
    provider: Annotated[str, typer.Option("--provider", help="claude | codex | agy | ...")] = "unknown",
) -> None:
    """Create the session and join it as admin; if it already runs for this profile, reuse it.

    The profile is named after the title, unless you pass the global --profile.
    """
    o = opts(ctx)
    title = title.strip()
    profile_name = o.profile or slug(title)
    if not profile_name:
        fail("the title needs at least one letter or digit")
    existing = load_profile(profile_name)
    if existing is not None and existing.title != title:
        fail(f"profile {profile_name!r} belongs to session {existing.title!r}; pass another --profile")
    if existing is not None and _alive(existing):
        _report(o, existing, reused=True)
        return
    ops = operator_client(o)
    if _titled(ops, title):
        fail(
            f'a session titled "{title}" exists, but profile {profile_name!r} cannot log in to it. '
            "Pick another title, or delete it: aichat ops ns-list / aichat ops ns-delete ID"
        )
    _report(o, _create(o, ops, title, name, provider, profile_name), reused=False)


def _status(profile: Profile) -> tuple[str, str]:
    """(agent name, status) of a profile, asking its hub."""
    try:
        me = HubClient(profile.url, profile.token).get("/me")
    except HubError as exc:
        return "-", exc.code
    except httpx.TransportError:
        return "-", "hub unreachable"
    return me["name"], "ok"


@profile_app.command("list")
def list_cmd(
    ctx: typer.Context,
    check: Annotated[bool, typer.Option("--check/--no-check", help="ask the hub if each login works")] = True,
) -> None:
    """List saved profiles: profile name, agent name, session title, status."""
    rows, data = [], []
    for p in list_profiles():
        agent, status = _status(p) if check else ("?", "not checked")
        data.append({"profile": p.name, "agent": agent, "title": p.title, "url": p.url, "status": status})
        rows.append(f"{p.name}  agent={agent}  session={p.title or '-'}  {status}")
    emit(opts(ctx), data, "\n".join(rows) or "(no profiles; run `aichat session start TITLE`)")
