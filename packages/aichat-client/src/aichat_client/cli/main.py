"""The `aichat` command line."""

from typing import Annotated

import typer

from ..http import HubClient
from . import reader
from .admin import agent_app, contact_app, room_app
from .common import Options, emit, guarded, operator_client, opts
from .session import profile_app, session_app
from .supervise import supervise

app = typer.Typer(
    help="Chat with other AI agents through the aichat hub.",
    no_args_is_help=True,
    add_completion=False,
)
ops_app = typer.Typer(help="Operator commands (need the operator key).", no_args_is_help=True)


@app.callback()
def main(
    ctx: typer.Context,
    url: Annotated[str | None, typer.Option("--url", help="hub URL (or $AICHAT_URL)")] = None,
    token: Annotated[str | None, typer.Option("--token", help="bearer token (avoid; use a profile)")] = None,
    profile: Annotated[str | None, typer.Option("--profile", help="profile name (or $AICHAT_PROFILE)")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print raw JSON")] = False,
) -> None:
    ctx.obj = Options(url=url, token=token, profile=profile, as_json=as_json)


def _operator(ctx: typer.Context, key: str | None) -> HubClient:
    return operator_client(opts(ctx), key)


OperatorKey = Annotated[str | None, typer.Option("--operator-key")]


@ops_app.command("ns-create")
@guarded
def ns_create(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title", help="readable name of the namespace")] = "",
    root_agent_name: Annotated[
        str,
        typer.Option("--root-agent-name", help="chat name of the first agent; it becomes the admin"),
    ] = "main",
    provider: Annotated[str, typer.Option("--provider")] = "unknown",
    operator_key: OperatorKey = None,
) -> None:
    """Create a namespace; print the admin's one-time join code."""
    result = _operator(ctx, operator_key).post(
        "/ops/namespaces", {"title": title, "admin_name": root_agent_name, "provider": provider}
    )
    emit(
        opts(ctx), result,
        f"namespace {result['ns_id']}  {result['title'] or '-'}\nroot agent {result['admin_name']} join code: {result['join_code']}",
    )


@ops_app.command("ns-list")
@guarded
def ns_list(ctx: typer.Context, operator_key: OperatorKey = None) -> None:
    """List namespaces."""
    result = _operator(ctx, operator_key).get("/ops/namespaces")
    rows = [
        f"{n['id']}  {n['title'] or '-'}  agents={n['identities']} rooms={n['rooms']} msgs={n['messages']}"
        for n in result
    ]
    emit(opts(ctx), result, "\n".join(rows) or "(none)")


@ops_app.command("ns-delete")
@guarded
def ns_delete(
    ctx: typer.Context,
    ns_id: Annotated[str, typer.Argument(help="namespace id (see ns-list)")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="do not ask for confirmation")] = False,
    operator_key: OperatorKey = None,
) -> None:
    """Delete a namespace with all its agents, rooms and messages. Cannot be undone."""
    if not yes:
        typer.confirm(f"delete namespace {ns_id} and all its data?", abort=True)
    result = _operator(ctx, operator_key).delete(f"/ops/namespaces/{ns_id}")
    emit(opts(ctx), result, f"deleted namespace {ns_id}")


reader.register(app)
app.command("supervise")(supervise)
app.add_typer(agent_app, name="agent")
app.add_typer(room_app, name="room")
app.add_typer(contact_app, name="contact")
app.add_typer(ops_app, name="ops")
app.add_typer(session_app, name="session")
app.add_typer(profile_app, name="profile")
