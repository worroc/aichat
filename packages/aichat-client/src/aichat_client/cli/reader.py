"""Commands every agent uses: join, read, send, ask, answer, presence."""

import hashlib
import os
import shlex
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import typer
from aichat_protocol.names import normalize

from .. import envelope
from ..http import HubClient
from ..profile import Profile, save_profile
from .common import (
    attach_refs,
    channel_path,
    emit,
    fail,
    guarded,
    opts,
    parse_ref,
    read_text,
    show_answers,
    split_names,
    wait_answers,
)

To = Annotated[str | None, typer.Option("--to", help="DM this contact")]
Room = Annotated[str | None, typer.Option("--room", help="post in this room")]
Text = Annotated[str | None, typer.Argument(help="message text, or '-' for stdin")]
Refs = Annotated[list[str] | None, typer.Option("--ref", help="/abs/path or file:///abs/path (local file), url:URL, or git:REPO@BRANCH:PATH")]
Key = Annotated[str | None, typer.Option("--key", help="idempotency key (safe re-run)")]
Attach = Annotated[
    list[str] | None,
    typer.Option("--attach", help="send this file through the hub; readers get it with `aichat fetch`"),
]


def register(app: typer.Typer) -> None:
    for command in (
        join, me, link, launch, send, ask, answer, inbox, ack, history, thread, wait, park, active, done,
        dir_, fetch,
    ):
        name = "dir" if command is dir_ else command.__name__
        app.command(name)(command)


@guarded
def join(
    ctx: typer.Context,
    code: Annotated[str | None, typer.Argument(help="join code (default: $AICHAT_JOIN)")] = None,
) -> None:
    """Trade a one-time join code for a token and save it as a profile."""
    o = opts(ctx)
    code = code or os.environ.get("AICHAT_JOIN")
    if not code:
        fail("no join code: pass it or set AICHAT_JOIN")
    url = o.credentials().url
    body = HubClient(url).join(code)
    name = o.profile or os.environ.get("AICHAT_PROFILE") or body["name"]
    path = save_profile(Profile(name=name, url=url, token=body["token"]))
    hint = "" if os.environ.get("AICHAT_PROFILE") == name else f"\nuse: export AICHAT_PROFILE={name}  (or --profile {name})"
    emit(o, {"name": body["name"], "profile": name, "path": str(path)},
         f"joined as {body['name']} ({body['kind']}); profile saved to {path}{hint}")


@guarded
def me(ctx: typer.Context) -> None:
    """Show who you are, your contacts and rooms."""
    o = opts(ctx)
    data = o.client().get("/me")
    contacts = ", ".join(f"{c['name']}[{c['state']}]" for c in data["contacts"]) or "(none)"
    rooms = ", ".join(f"#{r['name']}" for r in data["rooms"]) or "(none)"
    text = (
        f"{data['name']} ns={data['ns']} role={data['role']} kind={data['kind']} parent={data['parent']} "
        f"state={data['state']} machine={data.get('machine') or '?'}"
        f"\ncontacts: {contacts}\nrooms: {rooms}"
        + (f"\nchildren: {', '.join(data['children'])}" if data["children"] else "")
    )
    emit(o, data, text)


def ui_base(hub_url: str) -> str:
    """The operator UI: $AICHAT_UI_URL, else the hub's host on $AICHAT_UI_PORT (default 5173)."""
    if os.environ.get("AICHAT_UI_URL"):
        return os.environ["AICHAT_UI_URL"].rstrip("/")
    hub = urlsplit(hub_url)
    host = f"[{hub.hostname}]" if hub.hostname and ":" in hub.hostname else hub.hostname
    return f"{hub.scheme}://{host}:{os.environ.get('AICHAT_UI_PORT') or 5173}"


@guarded
def link(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Argument(help="the agent to chat with (default: you)")] = None,
) -> None:
    """Print the UI link that opens the human's DM with an agent (default: you)."""
    o = opts(ctx)
    client = o.client()
    data = client.get("/me")
    agent = normalize(name or data["name"])
    url = f"{ui_base(o.credentials().url)}/#/ns/{quote(data['ns'], safe='')}/chat/{quote(agent, safe='')}"
    emit(o, {"ns": data["ns"], "agent": agent, "url": url}, url)


@guarded
def launch(
    ctx: typer.Context,
    session: Annotated[str | None, typer.Option("--session", help="your session id")] = None,
    resume_template: Annotated[
        str | None, typer.Option("--resume-template", help="e.g. 'claude --resume {session}'")
    ] = None,
    cwd: Annotated[str | None, typer.Option("--cwd", help="your working dir ('.' = here)")] = None,
    command: Annotated[str | None, typer.Option("--command", help="the command you were started with")] = None,
) -> None:
    """Show, or set, how to reinstate your session (machine, dir, session, commands).

    `aichat spawn` sets this for its agents. Others (e.g. main) set it themselves:
    `aichat launch --session <id> --cwd . --resume-template 'claude --resume {session}'`.
    """
    o = opts(ctx)
    body = {
        "session_ref": session, "resume_template": resume_template, "command": command,
        "cwd": os.path.abspath(os.path.expanduser(cwd)) if cwd else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    client = o.client()
    data = client.post("/me/launch", body) if body else client.get("/me/launch")
    emit(o, data, launch_text(data))


def launch_text(data: dict) -> str:
    rows = {
        "machine": data.get("machine"), "dir": data.get("cwd"), "session": data.get("session_ref"),
        "resume": resume_line(data), "command": data.get("command"),
    }
    return "\n".join(f"{k:<8} {v or '?'}" for k, v in rows.items())


PRINT_FLAGS = {"-p", "--print"}


def resume_line(data: dict) -> str | None:
    """The resume template as a shell line a human can paste, run in the agent's dir.

    Split like `supervise` does, fill {session} and {name}, quote each token. A human
    resumes interactively, so `{prompt}` (and a `-p`/`--print` just before it) is dropped.
    """
    template = data.get("resume_template")
    if not template:
        return None
    values = {"{session}": data.get("session_ref") or "", "{name}": data.get("name") or ""}
    tokens: list[str] = []
    for token in shlex.split(template):
        if "{prompt}" in token:
            if tokens and tokens[-1] in PRINT_FLAGS:
                tokens.pop()
            continue
        for key, value in values.items():
            token = token.replace(key, value)
        tokens.append(token)
    line = shlex.join(tokens)
    return f"cd {shlex.quote(data['cwd'])} && {line}" if data.get("cwd") else line


def _refs(client: HubClient, refs: list[str] | None, attach: list[str] | None) -> list[dict]:
    parsed = [parse_ref(r) for r in refs or []]  # check every --ref before uploading anything
    return parsed + attach_refs(client, attach)


def _post_body(text: str, refs: list[dict], reply_to: str | None, **extra: Any) -> dict:
    body = {"text": text, "refs": refs, "reply_to": reply_to, **extra}
    return {k: v for k, v in body.items() if v is not None}


def _warn_unknown_machines(body: dict, result: dict) -> None:
    """The hub cannot check a file ref for readers on an old client; say so."""
    if not any((r.get("url") or "").startswith("file://") for r in body.get("refs", [])):
        return
    unknown = [r["name"] for r in result["recipients"] if not r.get("machine") and r.get("will_read", True)]
    if unknown:
        typer.echo(
            f"warning: machine of {', '.join(unknown)} is unknown; "
            "they may not be able to open your file ref",
            err=True,
        )


def _recipient(r: dict) -> str:
    note = ", wake queued" if r["wake"] == "queued" else ""
    if not r.get("will_read", True):
        note = ", will NOT read"
    return f"{r['name']}[{r['state']}{note}]"


def _delivery(result: dict) -> str:
    parts = [_recipient(r) for r in result["recipients"]]
    text = f"sent {result['message']['id']} to {', '.join(parts) or '(nobody)'}"
    unread = [r["name"] for r in result["recipients"] if not r.get("will_read", True)]
    if unread:
        text += f"\nwarning: {', '.join(unread)} finished (done); they will never read this"
    return text


@guarded
def send(
    ctx: typer.Context,
    text: Text = None,
    to: To = None,
    room: Room = None,
    reply_to: Annotated[str | None, typer.Option("--reply-to")] = None,
    ref: Refs = None,
    attach: Attach = None,
    key: Key = None,
    wake: Annotated[bool, typer.Option("--wake", help="room: also wake parked members")] = False,
) -> None:
    """Send information to a contact (--to) or a room (--room)."""
    o = opts(ctx)
    path = channel_path(to, room)
    message = read_text(text)
    client = o.client()
    body = _post_body(message, _refs(client, ref, attach), reply_to, kind="information", wake=wake or None)
    result = client.create(path if to else f"{path}/messages", body, key)
    _warn_unknown_machines(body, result)
    emit(o, result, _delivery(result))


@guarded
def ask(
    ctx: typer.Context,
    text: Text = None,
    to: To = None,
    room: Room = None,
    max_agents_answers: Annotated[int, typer.Option("--max-agents-answers", "-n")] = 1,
    only: Annotated[str | None, typer.Option("--only", help="room: only these may answer")] = None,
    wait: Annotated[float, typer.Option("--wait", help="seconds to wait for answers")] = 0,
    expires_in: Annotated[float | None, typer.Option("--expires-in")] = None,
    reply_to: Annotated[str | None, typer.Option("--reply-to")] = None,
    ref: Refs = None,
    attach: Attach = None,
    key: Key = None,
) -> None:
    """Ask a question. With --wait, block until it closes or the time runs out."""
    o = opts(ctx)
    path = channel_path(to, room)
    message = read_text(text)
    client = o.client()
    body = _post_body(
        message, _refs(client, ref, attach), reply_to, kind="question",
        max_agents_answers=max_agents_answers, to=split_names(only) or None, expires_in=expires_in,
    )
    result = client.create(path if to else f"{path}/messages", body, key)
    _warn_unknown_machines(body, result)
    if not wait:
        emit(o, result, _delivery(result) + f"\nwait with: aichat wait {result['message']['id']}")
        return
    if not o.as_json:
        typer.echo(_delivery(result), err=True)
    answers, timed_out = wait_answers(client, result["message"]["id"], wait)
    show_answers(o, answers, timed_out)


@guarded
def answer(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument()],
    text: Text = None,
    amend: Annotated[bool, typer.Option("--amend", help="replace your earlier answer")] = False,
    ref: Refs = None,
    attach: Attach = None,
    key: Key = None,
) -> None:
    """Answer a question (once per agent; --amend replaces it while open)."""
    o = opts(ctx)
    message = read_text(text)
    client = o.client()
    body = {"text": message, "refs": _refs(client, ref, attach)}
    if amend:
        result = client.put(f"/questions/{question_id}/answers/me", body)
        emit(o, result, f"amended answer {result['id']}")
        return
    result = client.create(f"/questions/{question_id}/answers", body, key)
    _warn_unknown_machines(body, result)
    emit(o, result, _delivery(result))


def _print_page(o, page: dict) -> None:
    messages = page["messages"]
    lines = [envelope.render_many(messages)] if messages else ["(no new messages)"]
    if page.get("cursor"):
        lines.append(f"cursor: {page['cursor']}")
    if page.get("has_more"):
        lines.append("(more messages waiting: ack, then read again)")
    emit(o, page, "\n".join(lines))


@guarded
def inbox(
    ctx: typer.Context,
    wait: Annotated[float, typer.Option("--wait", help="seconds to wait for something new")] = 0,
    limit: Annotated[int, typer.Option("--limit")] = 50,
    do_ack: Annotated[bool, typer.Option("--ack", help="mark shown messages as read")] = False,
    room: Room = None,
    to: Annotated[str | None, typer.Option("--dm", help="only this DM")] = None,
) -> None:
    """Show new messages from all rooms and DMs (not your own)."""
    o = opts(ctx)
    client = o.client()
    path = "/inbox" if not (room or to) else f"{channel_path(to, room)}/new"
    page = _poll(client, path, wait, limit)
    _print_page(o, page)
    if do_ack and page.get("cursor"):
        client.post("/inbox/ack", {"cursor": page["cursor"]})


def _poll(client: HubClient, path: str, wait: float, limit: int) -> dict:
    remaining = wait
    while True:
        chunk = min(remaining, 60.0)
        page = client.get(path, wait=chunk, limit=limit)
        remaining -= chunk
        if page["messages"] or remaining <= 0:
            return page


@guarded
def ack(ctx: typer.Context, cursor: Annotated[str, typer.Argument()]) -> None:
    """Mark messages up to CURSOR as read."""
    o = opts(ctx)
    result = o.client().post("/inbox/ack", {"cursor": cursor})
    emit(o, result, "acked")


@guarded
def history(
    ctx: typer.Context,
    to: To = None,
    room: Room = None,
    before: Annotated[int | None, typer.Option("--before", help="older than this seq")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    sender: Annotated[str | None, typer.Option("--sender")] = None,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
) -> None:
    """Show the last messages of a room or DM (newest last)."""
    o = opts(ctx)
    page = o.client().get(
        f"{channel_path(to, room)}/messages", before_seq=before, limit=limit, sender=sender, kind=kind
    )
    text = envelope.render_many(page["messages"]) or "(empty)"
    if page["has_more"] and page["messages"]:
        text += f"\n(older: --before {page['messages'][0]['seq']})"
    emit(o, page, text)


@guarded
def thread(ctx: typer.Context, message_id: Annotated[str, typer.Argument()]) -> None:
    """Show the whole thread of a message, in order."""
    o = opts(ctx)
    data = o.client().get(f"/messages/{message_id}/thread")
    emit(o, data, envelope.render_many(data["messages"]))


@guarded
def wait(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument()],
    timeout: Annotated[float, typer.Option("--timeout")] = 240,
) -> None:
    """Wait for answers to your question (exit 2 on timeout)."""
    o = opts(ctx)
    result, timed_out = wait_answers(o.client(), question_id, timeout)
    show_answers(o, result, timed_out)


def _set_state(ctx: typer.Context, state: str, session: str | None, template: str | None) -> None:
    o = opts(ctx)
    body = {"state": state, "session_ref": session, "resume_template": template}
    data = o.client().post("/me/state", {k: v for k, v in body.items() if v is not None})
    text = f"{data['name']} is now {data['state']}"
    if data.get("held_by"):
        text += (
            f" (still needed by: {', '.join(data['held_by'])}; "
            "stop now, the hub closes you when they are done)"
        )
    emit(o, data, text)


@guarded
def park(
    ctx: typer.Context,
    session: Annotated[str | None, typer.Option("--session", help="session id to resume")] = None,
    resume_template: Annotated[str | None, typer.Option("--resume-template")] = None,
) -> None:
    """Say you are idle and can be resumed; then stop working."""
    _set_state(ctx, "parked", session, resume_template)


@guarded
def active(ctx: typer.Context) -> None:
    """Say you are working again."""
    _set_state(ctx, "active", None, None)


@guarded
def done(ctx: typer.Context) -> None:
    """Say you have finished for good."""
    _set_state(ctx, "done", None, None)


@guarded
def dir_(
    ctx: typer.Context,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    state: Annotated[str | None, typer.Option("--state")] = None,
) -> None:
    """List the agents you can see."""
    o = opts(ctx)
    data = o.client().get("/directory", tag=tag, provider=provider, state=state)
    rows = [
        f"{i['name']:<20} {i['kind']:<9} {i['provider']:<8} {i['state']:<8} "
        f"machine={i.get('machine') or '?'} tags={','.join(i['tags'])}"
        + (f" depends_on={','.join(i['depends_on'])}" if i.get("depends_on") else "")
        for i in data["identities"]
    ]
    emit(o, data, "\n".join(rows) or "(nobody)")


def _message(client: HubClient, message_id: str) -> dict:
    found = client.get(f"/messages/{message_id}/thread")["messages"]
    match = [m for m in found if m["id"] == message_id]
    if not match:
        fail(f"message {message_id} not found")
    return match[0]


def _safe_name(name: str) -> str:
    """Only a plain file name: no folders, no drive letter (``C:x`` on Windows)."""
    plain = Path(name.replace("\\", "/")).name.replace(":", "_").strip()
    return plain if plain not in {"", ".", ".."} else "file"


def _save_blob(client: HubClient, message_id: str, ref: dict, out: Path, force: bool) -> Path:
    target = out / _safe_name(ref["name"])
    if target.exists() and not force:
        fail(f"{target} exists; use --force to overwrite or --out DIR")
    data = client.download(f"/messages/{message_id}/blobs/{ref['id']}")
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        fail(f"{ref['name']}: sha256 does not match; the download is damaged, try again")
    target.write_bytes(data)
    return target


@guarded
def fetch(
    ctx: typer.Context,
    message_id: Annotated[str, typer.Argument(help="the message with the attached files")],
    out: Annotated[Path, typer.Option("--out", help="folder to save into")] = Path("."),
    force: Annotated[bool, typer.Option("--force", help="overwrite files that exist")] = False,
) -> None:
    """Download the files attached to a message (sent with --attach)."""
    o = opts(ctx)
    client = o.client()
    blobs = [r for r in _message(client, message_id)["refs"] if r["type"] == "blob"]
    if not blobs:
        fail(f"message {message_id} has no attached files")
    out.mkdir(parents=True, exist_ok=True)
    saved = [str(_save_blob(client, message_id, r, out, force)) for r in blobs]
    emit(o, {"saved": saved}, "\n".join(f"saved {p}" for p in saved))
