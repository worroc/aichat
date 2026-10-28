"""Commands every agent uses: join, read, send, ask, answer, presence."""

import os
from typing import Annotated, Any

import typer

from .. import envelope
from ..http import HubClient
from ..profile import Profile, save_profile
from .common import (
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
Refs = Annotated[list[str] | None, typer.Option("--ref", help="git:REPO@BRANCH:PATH or url:URL")]
Key = Annotated[str | None, typer.Option("--key", help="idempotency key (safe re-run)")]


def register(app: typer.Typer) -> None:
    for command in (
        join, me, send, ask, answer, inbox, ack, history, thread, wait, park, active, done, dir_,
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
    body = HubClient(url).post("/join", {"code": code})
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
        f"{data['name']} role={data['role']} kind={data['kind']} parent={data['parent']} "
        f"state={data['state']}\ncontacts: {contacts}\nrooms: {rooms}"
        + (f"\nchildren: {', '.join(data['children'])}" if data["children"] else "")
    )
    emit(o, data, text)


def _post_body(text: str, refs: list[str] | None, reply_to: str | None, **extra: Any) -> dict:
    body = {"text": text, "refs": [parse_ref(r) for r in refs or []], "reply_to": reply_to, **extra}
    return {k: v for k, v in body.items() if v is not None}


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
    key: Key = None,
    wake: Annotated[bool, typer.Option("--wake", help="room: also wake parked members")] = False,
) -> None:
    """Send information to a contact (--to) or a room (--room)."""
    o = opts(ctx)
    path = channel_path(to, room)
    body = _post_body(read_text(text), ref, reply_to, kind="information", wake=wake or None)
    result = o.client().create(path if to else f"{path}/messages", body, key)
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
    key: Key = None,
) -> None:
    """Ask a question. With --wait, block until it closes or the time runs out."""
    o = opts(ctx)
    path = channel_path(to, room)
    body = _post_body(
        read_text(text), ref, reply_to, kind="question", max_agents_answers=max_agents_answers,
        to=split_names(only) or None, expires_in=expires_in,
    )
    client = o.client()
    result = client.create(path if to else f"{path}/messages", body, key)
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
    key: Key = None,
) -> None:
    """Answer a question (once per agent; --amend replaces it while open)."""
    o = opts(ctx)
    body = {"text": read_text(text), "refs": [parse_ref(r) for r in ref or []]}
    client = o.client()
    if amend:
        result = client.put(f"/questions/{question_id}/answers/me", body)
        emit(o, result, f"amended answer {result['id']}")
        return
    result = client.create(f"/questions/{question_id}/answers", body, key)
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
        f"{i['name']:<20} {i['kind']:<9} {i['provider']:<8} {i['state']:<8} tags={','.join(i['tags'])}"
        + (f" depends_on={','.join(i['depends_on'])}" if i.get("depends_on") else "")
        for i in data["identities"]
    ]
    emit(o, data, "\n".join(rows) or "(nobody)")
