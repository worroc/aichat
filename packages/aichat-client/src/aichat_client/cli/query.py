"""Read-only queries: rooms with members, agents with rooms (agent and operator views)."""

from typing import Annotated, Any

import typer

from .common import emit, guarded, operator_client, opts

TagOpt = Annotated[str | None, typer.Option("--tag", help="only agents with this tag")]
StateOpt = Annotated[
    str | None, typer.Option("--state", help="pending | active | parked | done | revoked")
]
ParentOpt = Annotated[str | None, typer.Option("--parent", help="only children of this agent")]
ProviderOpt = Annotated[str | None, typer.Option("--provider", help="claude | codex | agy | ...")]
NsOpt = Annotated[str | None, typer.Option("--ns", help="one namespace id (default: all)")]
OperatorKey = Annotated[str | None, typer.Option("--operator-key")]


# --- text output ------------------------------------------------------------------------


def room_line(room: dict[str, Any]) -> str:
    ns = f"{room['ns']}  " if "ns" in room else ""
    last = room["last_message_at"] or "-"
    return f"{ns}#{room['name']:<20} members={len(room['members']):<3} last={last}"


def room_text(room: dict[str, Any]) -> str:
    lines = [f"#{room['name']}  history={room['history']}  last={room['last_message_at'] or '-'}"]
    lines += [
        f"  {m['name']:<20} {m['kind']:<9} {m['state']:<8} tags={','.join(m['tags'])}"
        for m in room["members"]
    ]
    return "\n".join(lines)


def agent_line(agent: dict[str, Any]) -> str:
    ns = f"{agent['ns']}  " if "ns" in agent else ""
    rooms = ",".join(f"#{r}" for r in agent["rooms"]) or "-"
    return (
        f"{ns}{agent['name']:<20} {agent['kind']:<9} {agent['provider']:<8} {agent['state']:<8} "
        f"parent={agent['parent'] or '-'} rooms={rooms} tags={','.join(agent['tags'])}"
    )


# --- agent view -----------------------------------------------------------------------------


@guarded
def room_list(ctx: typer.Context) -> None:
    """List the rooms you can read, with their member count and last message time."""
    o = opts(ctx)
    data = o.client().get("/rooms")
    emit(o, data, "\n".join(room_line(r) for r in data["rooms"]) or "(no rooms)")


@guarded
def room_show(ctx: typer.Context, room: Annotated[str, typer.Argument()]) -> None:
    """Show one room and its members (name, kind, state, tags)."""
    o = opts(ctx)
    data = o.client().get(f"/rooms/{room}")
    emit(o, data, room_text(data))


@guarded
def agent_list(
    ctx: typer.Context,
    tag: TagOpt = None,
    state: StateOpt = None,
    parent: ParentOpt = None,
    provider: ProviderOpt = None,
) -> None:
    """List the agents you can see (and you), with their rooms. The admin also gets launch info."""
    o = opts(ctx)
    data = o.client().get("/agents", tag=tag, state=state, parent=parent, provider=provider)
    emit(o, data, "\n".join(agent_line(a) for a in data["agents"]) or "(nobody)")


# --- operator view ----------------------------------------------------------------------------


@guarded
def ops_rooms(ctx: typer.Context, ns: NsOpt = None, operator_key: OperatorKey = None) -> None:
    """List rooms with members in every namespace (or one)."""
    o = opts(ctx)
    data = operator_client(o, operator_key).get("/ops/rooms", ns=ns)
    emit(o, data, "\n".join(room_line(r) for r in data) or "(no rooms)")


@guarded
def ops_agents(
    ctx: typer.Context,
    ns: NsOpt = None,
    tag: TagOpt = None,
    state: StateOpt = None,
    parent: ParentOpt = None,
    provider: ProviderOpt = None,
    operator_key: OperatorKey = None,
) -> None:
    """List agents with their rooms and launch info in every namespace (or one)."""
    o = opts(ctx)
    params = {"ns": ns, "tag": tag, "state": state, "parent": parent, "provider": provider}
    data = operator_client(o, operator_key).get("/ops/agents", **params)
    emit(o, data, "\n".join(agent_line(a) for a in data) or "(nobody)")


@guarded
def ops_common_backfill(ctx: typer.Context, ns: NsOpt = None, operator_key: OperatorKey = None) -> None:
    """Create the `common` room where it is missing and add every live agent. Safe to repeat."""
    o = opts(ctx)
    data = operator_client(o, operator_key).request("POST", "/ops/common/backfill", params={"ns": ns})
    rows = [
        f"{r['ns']}  {'created' if r['created'] else 'exists '}  added={','.join(r['added']) or '-'}"
        for r in data
    ]
    emit(o, data, "\n".join(rows) or "(no namespaces)")


def register(room_app: typer.Typer, agent_app: typer.Typer, ops_app: typer.Typer) -> None:
    room_app.command("list")(room_list)
    room_app.command("show")(room_show)
    room_app.command("members", hidden=True)(room_show)
    agent_app.command("list")(agent_list)
    ops_app.command("rooms")(ops_rooms)
    ops_app.command("agents")(ops_agents)
    ops_app.command("common-backfill")(ops_common_backfill)
