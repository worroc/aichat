"""Shared CLI plumbing: global options, output, errors, helpers."""

import functools
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import typer
from aichat_protocol.errors import ExitCode, exit_code_for

from .. import envelope
from ..http import HubClient, HubError
from ..profile import Credentials, operator_key, resolve

WAIT_CHUNK = 60.0


@dataclass
class Options:
    url: str | None
    token: str | None
    profile: str | None
    as_json: bool

    def credentials(self) -> Credentials:
        return resolve(self.url, self.token, self.profile)

    def client(self, *, auth: bool = True) -> HubClient:
        creds = self.credentials()
        if auth and not creds.token:
            fail("no credentials: run `aichat join`, or set AICHAT_TOKEN / AICHAT_PROFILE", ExitCode.AUTH)
        return HubClient(creds.url, creds.token)


def operator_client(o: Options, key: str | None = None) -> HubClient:
    key = operator_key(key)
    if not key:
        fail("operator key required: --operator-key, $AICHAT_OPERATOR_KEY or ~/.config/aichat/operator_key")
    return HubClient(resolve(o.url, None, None).url, operator_key=key)


def opts(ctx: typer.Context) -> Options:
    return ctx.find_root().obj


def fail(message: str, code: int = ExitCode.ERROR) -> None:
    typer.echo(f"aichat: {message}", err=True)
    raise typer.Exit(code)


def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn HubError into a readable message and a stable exit code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HubError as exc:
            fail(f"{exc.code}: {exc.message}", exit_code_for(exc.code))
        except httpx.TransportError as exc:
            fail(f"cannot reach the hub: {exc}")

    return wrapper


def emit(o: Options, data: Any, text: str | None = None) -> None:
    if o.as_json or text is None:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        typer.echo(text)


def read_text(value: str | None) -> str:
    if value is None or value == "-":
        if sys.stdin.isatty() and value is None:
            fail("message text is required (pass it, or '-' to read stdin)")
        return sys.stdin.read()
    return value


def split_names(value: str | None) -> list[str]:
    return [n.strip() for n in (value or "").split(",") if n.strip()]


def parse_ref(value: str) -> dict[str, Any]:
    """``url:https://...`` or ``git:REPO[@BRANCH][:PATH][#COMMIT]``."""
    if value.startswith("url:"):
        return {"type": "url", "url": value.removeprefix("url:")}
    if not value.startswith("git:"):
        fail(f"bad --ref {value!r}: use git:REPO@BRANCH:PATH or url:URL")
    body, _, commit = value.removeprefix("git:").partition("#")
    repo, _, rest = body.partition("@")
    branch, _, path = rest.partition(":") if rest else ("", "", "")
    if not rest and ":" in repo:
        repo, _, path = repo.rpartition(":")
    return {
        "type": "git",
        "repo": repo or None,
        "branch": branch or None,
        "path": path or None,
        "commit": commit or None,
    }


def channel_path(to: str | None, room: str | None) -> str:
    if bool(to) == bool(room):
        fail("give exactly one of --to NAME or --room ROOM")
    return f"/dm/{to}" if to else f"/rooms/{room}"


def wait_answers(client: HubClient, qid: str, total: float) -> tuple[dict[str, Any], bool]:
    """Long-poll in chunks until the question closes or ``total`` seconds pass."""
    deadline = time.monotonic() + total
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        result = client.get(f"/questions/{qid}/answers", wait=min(WAIT_CHUNK, remaining))
        if result["closed"]:
            return result, False
        if deadline - time.monotonic() <= 0:
            return result, True


def show_answers(o: Options, result: dict[str, Any], timed_out: bool) -> None:
    lines = [
        f"question {result['question']['id']}: {len(result['answers'])}/"
        f"{result['question']['max_agents_answers']} answers, "
        f"closed={result['closed_reason'] or 'no'}"
        + (", timed out" if timed_out else "")
    ]
    if result.get("pending_wake"):
        lines.append(f"parked (wake queued): {', '.join(result['pending_wake'])}")
    if result["answers"]:
        lines.append(envelope.render_many(result["answers"]))
    emit(o, result, "\n".join(lines))
    if timed_out:
        raise typer.Exit(ExitCode.TIMEOUT)
