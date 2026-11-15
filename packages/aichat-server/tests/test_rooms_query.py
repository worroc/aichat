"""The default `common` room, its backfill, and the query API for rooms and agents."""

from datetime import UTC, datetime

from aichat_server.domain import Identity, Kind, Namespace, Role, State

from conftest import code_of


async def _room(actor, name="common") -> dict:
    return await actor.ok("get", f"/rooms/{name}")


def _members(room: dict) -> dict[str, str]:
    return {m["name"]: m["state"] for m in room["members"]}


# --- common room ----------------------------------------------------------------


async def test_namespace_starts_with_common_room_and_its_admin(hub):
    _, main = await hub.namespace()
    room = await _room(main)
    assert _members(room) == {"main": "active"}
    assert room["history"] == "since_join"
    assert room["last_message_at"] is None


async def test_every_new_agent_joins_common_at_any_depth(hub):
    _, main = await hub.namespace()
    lead = await hub.agent(main, "lead")
    body = await lead.ok("post", "/agents/children", {"name": "kid"})
    assert body["rooms"] == ["common"]
    kid = await hub.join(body["join_code"])
    assert set(_members(await _room(kid))) == {"main", "lead", "kid"}
    me = await kid.ok("get", "/me")
    assert [r["name"] for r in me["rooms"]] == ["common"]


async def test_late_agent_does_not_get_old_common_messages(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/rooms/common/messages", {"text": "old news"})
    late = await hub.agent(main, "late")
    await main.ok("post", "/rooms/common/messages", {"text": "new news"})
    texts = [m["text"] for m in (await late.ok("get", "/inbox"))["messages"]]
    assert texts == ["new news"]


async def test_pending_agent_is_already_in_common(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/agents", {"name": "later"})
    assert _members(await _room(main))["later"] == "pending"


async def test_common_members_see_each_other(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice")
    await hub.agent(main, "bob")
    names = {i["name"] for i in (await alice.ok("get", "/directory"))["identities"]}
    assert names == {"main", "bob"}
    await alice.ok("post", "/rooms/common/messages", {"text": "hi all"})
    room = await _room(main)
    assert room["last_message_at"] is not None


async def test_asking_for_common_again_does_not_duplicate(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "alice", rooms=["common"])
    await main.ok("post", "/admin/rooms/common/members/alice")
    await main.ok("post", "/admin/agents/alice/rejoin")
    room = await _room(main)
    assert [m["name"] for m in room["members"]] == ["alice", "main"]


async def test_revoked_agent_stays_listed_as_revoked(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "alice")
    await main.ok("delete", "/admin/agents/alice")
    assert _members(await _room(main))["alice"] == "revoked"


async def test_common_cannot_be_created_twice(hub):
    _, main = await hub.namespace()
    resp = await main.post("/admin/rooms", {"name": "common"})
    assert code_of(resp) == "already_exists"


# --- backfill ---------------------------------------------------------------------


async def _old_namespace(app) -> str:
    """A namespace as an older hub made it: agents, but no common room."""
    store = app.state.services.ctx.store
    stamp = datetime.now(UTC)
    await store.namespaces.add(Namespace(id="old-ns", created_at=stamp, title="old"))
    for name, role, state in (
        ("main", Role.ADMIN, State.ACTIVE),
        ("alice", Role.AGENT, State.PARKED),
        ("gone", Role.AGENT, State.REVOKED),
        ("human-x", Role.HUMAN, State.ACTIVE),
    ):
        kind = Kind.HUMAN if role is Role.HUMAN else Kind.PROCESS
        await store.identities.add("old-ns", Identity(
            ns="old-ns", uid=name, name=name, role=role, kind=kind, parent=None, depth=0,
            state=state, tags=frozenset(), provider="x", description="", session_ref=None,
            resume_template=None, created_at=stamp,
        ))
    return "old-ns"


async def test_backfill_creates_common_with_live_agents_only(hub, app):
    ns = await _old_namespace(app)
    resp = await hub.client.post("/ops/common/backfill", params={"ns": ns}, headers=hub.op)
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"ns": ns, "created": True, "added": ["alice", "main"]}]
    rooms = (await hub.client.get("/ops/rooms", params={"ns": ns}, headers=hub.op)).json()
    assert [(r["name"], [m["name"] for m in r["members"]]) for r in rooms] == [
        ("common", ["alice", "main"])
    ]


async def test_backfill_is_safe_to_run_again(hub, app):
    ns = await _old_namespace(app)
    await hub.client.post("/ops/common/backfill", headers=hub.op)
    again = await hub.client.post("/ops/common/backfill", params={"ns": ns}, headers=hub.op)
    assert again.json() == [{"ns": ns, "created": False, "added": []}]


async def test_backfill_adds_agents_missing_from_common(hub):
    ns, main = await hub.namespace()
    await hub.agent(main, "alice")
    await hub.leave_common(main, "alice")
    resp = await hub.client.post("/ops/common/backfill", params={"ns": ns}, headers=hub.op)
    assert resp.json() == [{"ns": ns, "created": False, "added": ["alice"]}]


async def test_backfill_needs_operator_and_known_namespace(hub):
    assert (await hub.client.post("/ops/common/backfill")).status_code == 401
    resp = await hub.client.post("/ops/common/backfill", params={"ns": "nope"}, headers=hub.op)
    assert code_of(resp) == "not_found"


# --- GET /rooms -------------------------------------------------------------------


async def test_agent_lists_only_rooms_it_is_in(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    alice = await hub.agent(main, "alice")
    assert [r["name"] for r in (await alice.ok("get", "/rooms"))["rooms"]] == ["common"]
    assert [r["name"] for r in (await main.ok("get", "/rooms"))["rooms"]] == ["common", "dev"]
    resp = await alice.get("/rooms/dev")
    assert resp.status_code == 404 and code_of(resp) == "not_found"
    assert code_of(await alice.get("/rooms/nope")) == "not_found"


async def test_admin_does_not_list_rooms_it_is_not_in(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    await main.ok("delete", "/admin/rooms/dev/members/main")
    assert [r["name"] for r in (await main.ok("get", "/rooms"))["rooms"]] == ["common"]


async def test_human_lists_every_room(hub):
    ns, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    await main.ok("delete", "/admin/rooms/dev/members/main")
    human = await hub.human(ns)
    assert [r["name"] for r in (await human.ok("get", "/rooms"))["rooms"]] == ["common", "dev"]


async def test_room_show_lists_member_details(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "alice", tags=["designer"])
    members = {m["name"]: m for m in (await _room(main))["members"]}
    assert members["alice"] == {
        "name": "alice", "role": "agent", "kind": "process", "state": "active", "tags": ["designer"],
    }


# --- GET /agents ------------------------------------------------------------------


async def test_agent_list_includes_self_and_visible_only(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice")
    await hub.agent(main, "hidden")
    await hub.leave_common(main, "alice", "hidden")
    names = [a["name"] for a in (await alice.ok("get", "/agents"))["agents"]]
    assert names == ["alice", "main"]


async def test_agent_list_shows_launch_only_for_self(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice")
    await hub.agent(main, "bob")
    await alice.ok("post", "/me/launch", {"cwd": "/w", "command": "run"})
    agents = {a["name"]: a for a in (await alice.ok("get", "/agents"))["agents"]}
    assert agents["alice"]["launch"]["cwd"] == "/w"
    assert agents["bob"]["launch"] is None and agents["main"]["launch"] is None


async def test_admin_agent_list_shows_launch_for_all(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice")
    await alice.ok("post", "/me/launch", {"cwd": "/w"})
    agents = {a["name"]: a for a in (await main.ok("get", "/agents"))["agents"]}
    assert agents["alice"]["launch"]["cwd"] == "/w"


async def test_agent_list_shows_only_rooms_the_caller_reads(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "secret"})
    alice = await hub.agent(main, "alice")
    agents = {a["name"]: a for a in (await alice.ok("get", "/agents"))["agents"]}
    assert agents["main"]["rooms"] == ["common"]
    agents = {a["name"]: a for a in (await main.ok("get", "/agents"))["agents"]}
    assert agents["main"]["rooms"] == ["common", "secret"]


async def test_agent_list_filters(hub):
    _, main = await hub.namespace()
    lead = await hub.agent(main, "lead", tags=["dev"])
    await hub.child(lead, "kid")
    await main.ok("post", "/admin/agents", {"name": "waiting"})

    async def names(**params) -> list[str]:
        return [a["name"] for a in (await main.ok("get", "/agents", params=params))["agents"]]

    assert await names(tag="dev") == ["lead"]
    assert await names(parent="lead") == ["kid"]
    assert await names(state="pending") == ["waiting"]
    kid = (await main.ok("get", "/agents", params={"parent": "lead"}))["agents"][0]
    assert kid["kind"] == "subagent" and kid["depth"] == 1


# --- operator variants --------------------------------------------------------------


async def test_ops_rooms_and_agents_across_namespaces(hub):
    ns1, main1 = await hub.namespace()
    ns2, _ = await hub.namespace()
    await hub.agent(main1, "alice")
    rooms = (await hub.client.get("/ops/rooms", headers=hub.op)).json()
    assert {(r["ns"], r["name"]) for r in rooms} >= {(ns1, "common"), (ns2, "common")}
    agents = (await hub.client.get("/ops/agents", params={"ns": ns1}, headers=hub.op)).json()
    assert [(a["ns"], a["name"], a["rooms"]) for a in agents] == [
        (ns1, "alice", ["common"]), (ns1, "main", ["common"])
    ]
    assert "cwd" in agents[0]


async def test_ops_query_needs_operator_key(hub, client):
    _, main = await hub.namespace()
    assert (await client.get("/ops/rooms")).status_code == 401
    assert (await main.get("/ops/agents")).status_code == 401
    resp = await client.get("/ops/agents", params={"ns": "nope"}, headers=hub.op)
    assert code_of(resp) == "not_found"
