"""Namespace jail, 404 hiding, impersonation, depth rule, name rules."""

from aichat_server.config import Settings
from aichat_server.main import create_app
from conftest import code_of


async def test_other_namespace_is_invisible(hub):
    ns_a, main_a = await hub.namespace()
    _, main_b = await hub.namespace()  # same admin name in another namespace
    await main_a.ok("post", "/admin/rooms", {"name": "dev"})
    await main_b.ok("post", "/admin/rooms", {"name": "dev"})
    bob_a = await hub.agent(main_a, "bob", rooms=["dev"])
    post = await bob_a.ok("post", "/rooms/dev/messages", {"text": "secret in A"})
    msg_id = post["message"]["id"]

    assert (await main_b.get(f"/messages/{msg_id}/thread")).status_code == 404
    history = await main_b.ok("get", "/rooms/dev/messages")
    assert history["messages"] == []
    assert (await main_b.post("/dm/bob", {"text": "hi"})).status_code == 404
    assert "ns" not in (await bob_a.ok("get", "/me"))
    assert ns_a not in (await bob_a.get("/me")).text


async def test_dm_only_to_contacts(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice")
    await hub.agent(main, "bob")
    resp = await alice.post("/dm/bob", {"text": "hi"})
    assert resp.status_code == 404
    assert (await alice.post("/dm/nobody", {"text": "hi"})).status_code == 404
    assert (await alice.post("/dm/main", {"text": "hi boss"})).status_code == 200


async def test_room_requires_membership(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    outsider = await hub.agent(main, "eve")
    assert (await outsider.post("/rooms/dev/messages", {"text": "x"})).status_code == 404
    assert (await outsider.get("/rooms/dev/messages")).status_code == 404
    assert (await outsider.get("/rooms/nope/messages")).status_code == 404


async def test_from_field_is_ignored(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    body = await bob.ok("post", "/dm/main", {"text": "hello", "from": "main", "sender": "main"})
    assert body["message"]["sender"] == "bob"


async def test_reply_to_other_channel_is_not_found(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "ra"})
    await main.ok("post", "/admin/rooms", {"name": "rb"})
    msg = await main.ok("post", "/rooms/ra/messages", {"text": "root"})
    resp = await main.post("/rooms/rb/messages", {"text": "x", "reply_to": msg["message"]["id"]})
    assert resp.status_code == 404


async def test_join_code_single_use(hub, client):
    _, main = await hub.namespace()
    created = await main.ok("post", "/admin/agents", {"name": "bob"})
    assert (await client.post("/join", json={"code": created["join_code"]})).status_code == 200
    again = await client.post("/join", json={"code": created["join_code"]})
    assert again.status_code == 401
    events = (await client.get("/ops/events", headers=hub.op)).json()
    assert any(e["data"].get("alert") == "join_code_reused" for e in events)


async def test_revoked_token_rejected(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    await main.ok("delete", "/admin/agents/bob")
    assert (await bob.get("/me")).status_code == 401


async def test_secret_in_text_is_rejected_and_revoked(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    resp = await bob.post("/dm/main", {"text": f"my token is {bob.token}"})
    assert code_of(resp) == "secret_in_text"
    assert (await bob.get("/me")).status_code == 401


async def test_non_admin_cannot_use_admin_endpoints(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    resp = await bob.post("/admin/rooms", {"name": "x"})
    assert resp.status_code == 403


async def test_name_rules(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "impl-1")
    assert (await main.post("/admin/agents", {"name": "impl-2"})).status_code == 200
    assert code_of(await main.post("/admin/agents", {"name": "impl-l"})) == "invalid"
    assert code_of(await main.post("/admin/agents", {"name": "admin"})) == "invalid"
    assert code_of(await main.post("/admin/agents", {"name": "human-x"})) == "invalid"
    assert code_of(await main.post("/admin/agents", {"name": "Bad Name"})) == "invalid"
    auto = await main.ok("post", "/admin/agents", {"name_prefix": "impl"})
    assert auto["name"] == "impl-3"


async def test_revoked_name_is_never_reused(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "bob")
    await main.ok("delete", "/admin/agents/bob")
    assert code_of(await main.post("/admin/agents", {"name": "bob"})) == "already_exists"


async def test_depth_rule(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    proc = await hub.agent(main, "proc", rooms=["dev"])
    sub = await hub.child(proc, "proc-sub", rooms=["dev"])
    me = await sub.ok("get", "/me")
    assert me["kind"] == "subagent" and me["parent"] == "proc" and me["depth"] == 1
    assert code_of(await sub.post("/agents/children", {"name": "deeper"})) == "depth_exceeded"
    # a process may not create processes, only the admin
    assert code_of(await proc.post("/admin/agents", {"name": "p2"})) == "forbidden"
    # a process cannot give rooms it is not in
    await main.ok("post", "/admin/rooms", {"name": "secret"})
    assert (await proc.post("/agents/children", {"name": "s2", "rooms": ["secret"]})).status_code == 404


async def test_no_member_limit(hub):
    _, main = await hub.namespace()
    for i in range(50):
        await main.ok("post", "/admin/agents", {"name": f"worker-{i}"})


async def test_directory_shows_only_visible(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    await hub.agent(main, "alice", rooms=["dev"], tags=["designer"])
    bob = await hub.agent(main, "bob", rooms=["dev"])
    await hub.agent(main, "hidden")
    names = {i["name"] for i in (await bob.ok("get", "/directory"))["identities"]}
    assert names == {"alice", "main"}
    tagged = await bob.ok("get", "/directory", params={"tag": "designer"})
    assert [i["name"] for i in tagged["identities"]] == ["alice"]


async def test_admin_cannot_read_others_dms(hub):
    _, main = await hub.namespace()
    alice = await hub.agent(main, "alice", contacts=[])
    await hub.agent(main, "bob")
    await main.ok("post", "/admin/contacts", {"a": "alice", "b": "bob"})
    await alice.ok("post", "/dm/bob", {"text": "private"})
    assert "private" not in (await main.get("/dm/alice/messages")).text
    assert "private" not in (await main.get("/dm/bob/messages")).text


def test_operator_key_read_from_file(tmp_path):
    key_file = tmp_path / "operator_key"
    key_file.write_text("from-file\n")
    app = create_app(Settings(operator_key="", operator_key_file=str(key_file), store="memory"))
    assert app.state.settings.operator_key == "from-file"
    env_wins = create_app(Settings(operator_key="env", operator_key_file=str(key_file), store="memory"))
    assert env_wins.state.settings.operator_key == "env"
