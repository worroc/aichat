"""`aichat supervise`: resume parked agents when someone needs them.

Runs on the machine of whoever spawned the agents. It listens to the
hub's wake events (for your children; the admin gets all) and runs each
agent's ``resume_template``. It also caps how many resumed processes run
at the same time - the hub itself never limits processes.
"""

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Annotated, Any

import typer

from ..http import HubClient
from ..profile import load_profile
from .admin import child_env, log_path
from .common import fail, guarded, opts

WAKE_PROMPT = (
    "You were resumed by aichat: you have {unread} unread message(s). "
    "Run `aichat active`, then `aichat inbox --ack`, handle them (answer questions with `aichat answer`), "
    "then run `aichat park` and stop."
)


def build_argv(template: str, **values: str) -> list[str]:
    """Split first, then fill placeholders, so each value stays one argument."""
    return [part.format(**values) for part in shlex.split(template)]


def latest_seq(client: HubClient) -> int:
    """Skip the backlog: find the newest event seq visible to us."""
    last = 0
    while True:
        page = client.get("/wake/events", after=last)
        if page["last"] == last:
            return last
        last = page["last"]


def still_parked(client: HubClient, name: str) -> bool:
    found = client.get("/directory", state="parked")["identities"]
    return any(i["name"] == name for i in found)


@dataclass
class Supervisor:
    client: HubClient
    max_processes: int
    dry_run: bool
    running: dict[str, subprocess.Popen] = field(default_factory=dict)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)

    def reap(self) -> None:
        for name, proc in list(self.running.items()):
            if proc.poll() is not None:
                typer.echo(f"supervise: {name} exited with {proc.returncode}")
                del self.running[name]

    def wake(self, event: dict[str, Any]) -> None:
        name = event["name"]
        if name in self.running or not still_parked(self.client, name):
            return
        if len(self.running) >= self.max_processes:
            self.pending[name] = event
            typer.echo(f"supervise: {name} queued ({len(self.running)} running)")
            return
        self._start(event)

    def drain(self) -> None:
        self.reap()
        while self.pending and len(self.running) < self.max_processes:
            _, event = self.pending.popitem()
            self._start(event)

    def _start(self, event: dict[str, Any]) -> None:
        name = event["name"]
        template = event.get("resume_template")
        profile = load_profile(name)
        if not template or profile is None:
            typer.echo(f"supervise: cannot resume {name} (no resume template or no local profile)")
            return
        prompt = WAKE_PROMPT.format(unread=event.get("unread", "some"))
        argv = build_argv(template, session=event.get("session_ref") or "", prompt=prompt, name=name)
        if self.dry_run:
            typer.echo(f"supervise: would run {argv}")
            return
        # Mark it active first, so new messages do not queue more wake events.
        HubClient(profile.url, profile.token).post("/me/state", {"state": "active"})
        with open(log_path(name), "ab") as fh:
            self.running[name] = subprocess.Popen(
                argv, env=child_env(name, profile.url, profile.token), stdout=fh,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
            )
        typer.echo(f"supervise: resumed {name} pid={self.running[name].pid}")


@guarded
def supervise(
    ctx: typer.Context,
    max_processes: Annotated[int, typer.Option("--max-processes", help="cap on resumed processes")] = 4,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    after: Annotated[
        int | None, typer.Option("--after", help="replay events after this seq (default: only new)")
    ] = None,
) -> None:
    """Resume parked agents on wake events (runs until interrupted)."""
    o = opts(ctx)
    client = o.client()
    start = latest_seq(client) if after is None else after
    sup = Supervisor(client=client, max_processes=max_processes, dry_run=dry_run)
    typer.echo(f"supervise: listening for wake events after seq {start}")
    for event, data in client.stream("/wake/stream", after=start):
        if event == "error":
            fail(f"stream ended: {data}")
        sup.drain()
        if event == "wake_needed":
            sup.wake(json.loads(data)["data"])
