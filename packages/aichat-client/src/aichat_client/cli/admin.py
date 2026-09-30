"""Creating agents (admin: processes, processes: subagents), rooms, contacts."""

import os
import subprocess
from typing import Annotated, Any

import typer

from .. import bootstrap
from ..http import HubClient
from ..profile import Profile, config_dir, save_profile
from .common import Options, emit, fail, guarded, opts, split_names

agent_app = typer.Typer(help="Create, spawn, revoke agents.", no_args_is_help=True)
room_app = typer.Typer(help="Rooms (admin only).", no_args_is_help=True)
contact_app = typer.Typer(help="Contacts (admin only).", no_args_is_help=True)

Name = Annotated[str | None, typer.Argument(help="agent name (or use --name-prefix)")]
Prefix = Annotated[str | None, typer.Option("--name-prefix", help="auto-number: PREFIX-N")]
KindOpt = Annotated[str | None, typer.Option("--kind", help="process (admin only) or subagent")]
Contacts = Annotated[str | None, typer.Option("--contacts", help="a,b,tag:designer")]
Rooms = Annotated[str | None, typer.Option("--rooms", help="r1,r2")]
Tags = Annotated[str | None, typer.Option("--tags", help="t1,t2")]
Provider = Annotated[str, typer.Option("--provider", help="claude | codex | agy | ...")]
Description = Annotated[str, typer.Option("--description")]
Template = Annotated[
    str | None,
    typer.Option("--resume-template", help="e.g. 'codex exec resume {session} {prompt}'"),
]
Session = Annotated[str | None, typer.Option("--session", help="session id for resume")]


def _create(
    o: Options,
    name: str | None,
    prefix: str | None,
    kind: str | None,
    fields: dict[str, Any],
) -> dict:
    client = o.client()
    me = client.get("/me")
    wanted = kind or ("process" if me["role"] == "admin" else "subagent")
    body = {"name": name, "name_prefix": prefix, "kind": wanted, **fields}
    body = {k: v for k, v in body.items() if v is not None}
    path = "/admin/agents" if wanted == "process" else "/agents/children"
    return client.create(path, body)


def _fields(contacts, rooms, tags, provider, description, template, session) -> dict[str, Any]:
    return {
        "contacts": split_names(contacts),
        "rooms": split_names(rooms),
        "tags": split_names(tags),
        "provider": provider,
        "description": description,
        "resume_template": template,
        "session_ref": session,
    }


@agent_app.command("create")
@guarded
def create(
    ctx: typer.Context,
    name: Name = None,
    name_prefix: Prefix = None,
    kind: KindOpt = None,
    contacts: Contacts = None,
    rooms: Rooms = None,
    tags: Tags = None,
    provider: Provider = "unknown",
    description: Description = "",
    resume_template: Template = None,
    session: Session = None,
) -> None:
    """Create an agent; print its one-time join code and bootstrap prompt."""
    o = opts(ctx)
    fields = _fields(contacts, rooms, tags, provider, description, resume_template, session)
    created = _create(o, name, name_prefix, kind, fields)
    prompt = bootstrap.render(created, provider)
    text = (
        f"created {created['name']} ({created['kind']}, parent {created['parent']})\n"
        f"join code (single use): {created['join_code']}\n"
        f"child runs: AICHAT_JOIN=<code> AICHAT_PROFILE={created['name']} aichat join\n\n"
        f"--- bootstrap prompt ---\n{prompt}"
    )
    emit(o, {**created, "bootstrap": prompt}, text)


@agent_app.command(
    "spawn", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@guarded
def spawn(
    ctx: typer.Context,
    name: Name = None,
    name_prefix: Prefix = None,
    kind: KindOpt = None,
    contacts: Contacts = None,
    rooms: Rooms = None,
    tags: Tags = None,
    provider: Provider = "unknown",
    description: Description = "",
    resume_template: Template = None,
    session: Session = None,
    detach: Annotated[bool, typer.Option("--detach", help="return at once, log to a file")] = False,
) -> None:
    """Create + join an agent, then run the command after `--` with its token in env.

    In the command, {name}, {session} and {bootstrap} are replaced. The child gets
    AICHAT_URL, AICHAT_TOKEN, AICHAT_PROFILE and AICHAT_BOOTSTRAP.
    """
    o = opts(ctx)
    command = list(ctx.args)
    if not command:
        fail("give the command to run after --, e.g. -- codex exec '{bootstrap}'")
    fields = _fields(contacts, rooms, tags, provider, description, resume_template, session)
    created = _create(o, name, name_prefix, kind, fields)
    prompt = bootstrap.render(created, provider)
    url = o.credentials().url
    joined = HubClient(url).post("/join", {"code": created["join_code"]})
    save_profile(Profile(name=created["name"], url=url, token=joined["token"]))
    env = child_env(created["name"], url, joined["token"], prompt)
    values = {"{name}": created["name"], "{session}": session or "", "{bootstrap}": prompt}
    argv = [_fill(part, values) for part in command]
    _run(o, created["name"], argv, env, detach)


def _fill(part: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        part = part.replace(placeholder, value)
    return part


def child_env(name: str, url: str, token: str | None, prompt: str | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"AICHAT_JOIN", "AICHAT_TOKEN"}}
    env.update({"AICHAT_URL": url, "AICHAT_PROFILE": name})
    if token:
        env["AICHAT_TOKEN"] = token
    if prompt:
        env["AICHAT_BOOTSTRAP"] = prompt
    return env


def log_path(name: str):
    path = config_dir() / "logs" / f"{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run(o: Options, name: str, argv: list[str], env: dict[str, str], detach: bool) -> None:
    if not detach:
        raise typer.Exit(subprocess.run(argv, env=env, check=False).returncode)
    log = log_path(name)
    with open(log, "ab") as fh:
        proc = subprocess.Popen(
            argv, env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    emit(o, {"name": name, "pid": proc.pid, "log": str(log)}, f"spawned {name} pid={proc.pid} log={log}")


@agent_app.command("revoke")
@guarded
def revoke(ctx: typer.Context, name: Annotated[str, typer.Argument()]) -> None:
    """Revoke an agent and all its children (admin: anyone; process: own children)."""
    o = opts(ctx)
    client = o.client()
    path = "/admin/agents" if client.get("/me")["role"] == "admin" else "/agents/children"
    result = client.delete(f"{path}/{name}")
    emit(o, result, f"revoked: {', '.join(result['revoked'])}")


@agent_app.command("rejoin")
@guarded
def rejoin(ctx: typer.Context, name: Annotated[str, typer.Argument()]) -> None:
    """Issue a new join code for an agent (its old token dies on join)."""
    o = opts(ctx)
    client = o.client()
    path = "/admin/agents" if client.get("/me")["role"] == "admin" else "/agents/children"
    result = client.post(f"{path}/{name}/rejoin")
    emit(o, result, f"join code for {result['name']}: {result['join_code']}")


@room_app.command("create")
@guarded
def room_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    members: Annotated[str | None, typer.Option("--members")] = None,
    history: Annotated[str, typer.Option("--history", help="full | since_join")] = "full",
) -> None:
    """Create a room (you become a member)."""
    o = opts(ctx)
    body = {"name": name, "members": split_names(members), "history": history}
    result = o.client().post("/admin/rooms", body)
    emit(o, result, f"room #{result['name']}: {', '.join(result['members'])}")


@room_app.command("add")
@guarded
def room_add(ctx: typer.Context, room: str, name: str) -> None:
    """Add an agent to a room."""
    o = opts(ctx)
    result = o.client().post(f"/admin/rooms/{room}/members/{name}")
    emit(o, result, f"room #{result['name']}: {', '.join(result['members'])}")


@room_app.command("remove")
@guarded
def room_remove(ctx: typer.Context, room: str, name: str) -> None:
    """Remove an agent from a room."""
    o = opts(ctx)
    result = o.client().delete(f"/admin/rooms/{room}/members/{name}")
    emit(o, result, f"room #{result['name']}: {', '.join(result['members'])}")


@contact_app.command("add")
@guarded
def contact_add(ctx: typer.Context, a: str, b: str) -> None:
    """Make A and B contacts of each other."""
    o = opts(ctx)
    emit(o, o.client().post("/admin/contacts", {"a": a, "b": b}), f"{a} <-> {b}")


@contact_app.command("remove")
@guarded
def contact_remove(ctx: typer.Context, a: str, b: str) -> None:
    """Remove the contact between A and B."""
    o = opts(ctx)
    emit(o, o.client().delete("/admin/contacts", {"a": a, "b": b}), f"{a} x {b}")
