"""Agent lifetime: experts stay reachable until the agents that depend on them are done."""

import sqlite3

from aichat_server.store.sqlite import Db
from conftest import code_of


async def _team(hub, *experts, dependent="integrator"):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    team = [await hub.agent(main, n, rooms=["dev"]) for n in experts]
    lead = await hub.agent(main, dependent, rooms=["dev"], depends_on=list(experts))
    return main, team, lead


async def _states(main) -> dict[str, str]:
    found = (await main.ok("get", "/directory"))["identities"]
    return {i["name"]: i["state"] for i in found}


async def _done(actor) -> dict:
    return await actor.ok("post", "/me/state", {"state": "done"})


async def test_expert_done_is_parked_while_dependent_works(hub):
    main, (expert,), lead = await _team(hub, "r1")
    reply = await _done(expert)
    assert reply["state"] == "parked"
    assert reply["held_by"] == ["integrator"]
    assert (await _states(main))["integrator"] != "done"


async def test_dependent_done_closes_its_experts(hub):
    main, (r1, r2), lead = await _team(hub, "r1", "r2")
    await _done(r1)
    await _done(r2)
    assert (await _done(lead))["held_by"] == []
    states = await _states(main)
    assert [states[n] for n in ("r1", "r2", "integrator")] == ["done", "done", "done"]


async def test_active_expert_is_not_closed_under_it(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await _done(lead)
    assert (await _states(main))["r1"] == "active"
    assert (await _done(expert))["state"] == "done"


async def test_release_cascades_down_the_chain(hub):
    _, main = await hub.namespace()
    leaf = await hub.agent(main, "leaf")
    middle = await hub.agent(main, "middle", depends_on=["leaf"])
    top = await hub.agent(main, "top", depends_on=["middle"])
    await _done(leaf)
    assert (await _done(middle))["held_by"] == ["top"]
    await _done(top)
    states = await _states(main)
    assert [states[n] for n in ("leaf", "middle", "top")] == ["done", "done", "done"]


async def test_expert_waiting_for_an_answer_is_not_closed(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await expert.ok("post", "/dm/main", {"text": "which file?", "kind": "question"})
    await expert.ok("post", "/me/state", {"state": "parked"})
    await _done(lead)
    assert (await _states(main))["r1"] == "parked"
    resp = await main.post("/dm/r1", {"text": "this one"})
    assert resp.status_code == 200


async def test_woken_expert_that_parks_again_is_closed_when_free(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await _done(expert)  # held: parked, done_requested
    await expert.ok("post", "/me/state", {"state": "active"})  # woken for a question
    assert (await expert.ok("post", "/me/state", {"state": "parked"}))["state"] == "parked"
    await _done(lead)
    assert (await _states(main))["r1"] == "done"


async def test_plain_park_without_dependents_stays_parked(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    assert (await bob.ok("post", "/me/state", {"state": "parked"}))["state"] == "parked"


async def test_unread_mail_blocks_done(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    await main.ok("post", "/dm/bob", {"text": "one more thing"})
    resp = await bob.post("/me/state", {"state": "done"})
    assert resp.status_code == 409
    assert code_of(resp) == "unread_pending"
    page = await bob.ok("get", "/inbox")
    await bob.ok("post", "/inbox/ack", {"cursor": page["cursor"]})
    assert (await _done(bob))["state"] == "done"


async def test_released_expert_with_mail_is_woken_not_closed(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await expert.ok("post", "/me/state", {"state": "done", "session_ref": "S1"})
    await main.ok("post", "/rooms/dev/messages", {"text": "fyi, no wake"})
    page = await lead.ok("get", "/inbox")
    await lead.ok("post", "/inbox/ack", {"cursor": page["cursor"]})
    await _done(lead)
    assert (await _states(main))["r1"] == "parked"
    events = (await main.ok("get", "/wake/events"))["events"]
    woken = [e["data"] for e in events if e["type"] == "wake_needed"]
    assert [(w["name"], w["kind"]) for w in woken] == [("r1", "release")]


async def test_dm_to_done_agent_is_refused(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    await _done(bob)
    resp = await main.post("/dm/bob", {"text": "are you there?", "kind": "question"})
    assert resp.status_code == 409
    assert code_of(resp) == "recipient_done"


async def test_room_post_marks_done_members_unread(hub):
    main, (r1, r2), lead = await _team(hub, "r1", "r2")
    await _done(lead)
    await _done(r1)
    sent = await main.ok("post", "/rooms/dev/messages", {"text": "fyi"})
    will_read = {r["name"]: r["will_read"] for r in sent["recipients"]}
    assert will_read == {"r1": False, "r2": True, "integrator": False}


async def test_room_question_skips_done_members(hub):
    main, (r1, r2), lead = await _team(hub, "r1", "r2")
    await _done(lead)
    await _done(r1)
    targeted = await main.post("/rooms/dev/messages", {"text": "?", "kind": "question", "to": ["r1"]})
    assert code_of(targeted) == "recipient_done"
    too_many = await main.post(
        "/rooms/dev/messages", {"text": "?", "kind": "question", "max_agents_answers": 2}
    )
    assert code_of(too_many) == "invalid"


async def test_unknown_expert_is_not_found(hub):
    _, main = await hub.namespace()
    resp = await main.post("/admin/agents", {"name": "lead", "depends_on": ["ghost"]})
    assert resp.status_code == 404


async def test_new_dependent_reopens_a_done_expert(hub):
    _, main = await hub.namespace()
    expert = await hub.agent(main, "r1")
    await _done(expert)
    created = await main.ok("post", "/admin/agents", {"name": "late", "depends_on": ["r1"]})
    assert created["depends_on"] == ["r1"]
    states = await _states(main)
    assert states["r1"] == "parked"
    resp = await main.post("/dm/r1", {"text": "a flow question"})
    assert resp.status_code == 200


async def test_revoking_a_dependent_releases_its_experts(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await _done(expert)
    await main.ok("delete", "/admin/agents/integrator")
    assert (await _states(main))["r1"] == "done"


async def test_directory_shows_depends_on(hub):
    main, _, _ = await _team(hub, "r1")
    found = {i["name"]: i["depends_on"] for i in (await main.ok("get", "/directory"))["identities"]}
    assert found["integrator"] == ["r1"]


def test_old_sqlite_file_gets_depends_on_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE identities (ns TEXT NOT NULL, name TEXT NOT NULL, uid TEXT NOT NULL,"
        " role TEXT NOT NULL, kind TEXT NOT NULL, parent TEXT, depth INTEGER NOT NULL,"
        " state TEXT NOT NULL, tags TEXT NOT NULL, provider TEXT NOT NULL,"
        " description TEXT NOT NULL, session_ref TEXT, resume_template TEXT,"
        " created_at TEXT NOT NULL, last_seen TEXT, PRIMARY KEY (ns, name))"
    )
    conn.close()
    db = Db(str(path))
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(identities)")}
    db.close()
    assert {"depends_on", "done_requested"} <= columns


async def _read_all(actor) -> None:
    page = await actor.ok("get", "/inbox")
    await actor.ok("post", "/inbox/ack", {"cursor": page["cursor"]})


async def test_held_by_hides_dependents_the_expert_cannot_see(hub):
    _, main = await hub.namespace()
    expert = await hub.agent(main, "ee")
    await hub.agent(main, "dd", depends_on=["ee"])
    await main.ok("delete", "/admin/contacts", {"a": "dd", "b": "ee"})
    reply = await _done(expert)
    assert reply["state"] == "parked"
    assert reply["held_by"] == []


async def test_directory_hides_experts_the_viewer_cannot_see(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "ee")
    await hub.agent(main, "dd", depends_on=["ee"])
    viewer = await hub.agent(main, "xx", contacts=["dd"])
    found = {i["name"]: i["depends_on"] for i in (await viewer.ok("get", "/directory"))["identities"]}
    assert found["dd"] == []


async def test_experts_become_contacts_of_the_dependent(hub):
    main, _, lead = await _team(hub, "r1")
    me = await lead.ok("get", "/me")
    assert "r1" in {c["name"] for c in me["contacts"]}


async def test_parking_with_mail_after_release_wakes_the_expert(hub):
    _, main = await hub.namespace()
    expert = await hub.agent(main, "ee", resume_template="echo {session}")
    lead = await hub.agent(main, "dd", depends_on=["ee"])
    await _done(expert)
    await expert.ok("post", "/me/state", {"state": "active"})
    await main.ok("post", "/dm/ee", {"text": "fyi"})
    await _done(lead)
    await expert.ok("post", "/me/state", {"state": "parked"})
    events = (await main.ok("get", "/wake/events"))["events"]
    woken = [e["data"]["kind"] for e in events if e["type"] == "wake_needed" and e["data"]["name"] == "ee"]
    assert "release" in woken
    await _read_all(expert)
    assert (await _done(expert))["state"] == "done"


async def test_reopen_follows_the_chain(hub):
    _, main = await hub.namespace()
    leaf = await hub.agent(main, "leaf")
    middle = await hub.agent(main, "middle", depends_on=["leaf"])
    await _done(leaf)
    await _done(middle)
    await hub.agent(main, "late", depends_on=["middle"])
    states = await _states(main)
    assert (states["middle"], states["leaf"]) == ("parked", "parked")
    resp = await middle.post("/dm/leaf", {"text": "still there?"})
    assert resp.status_code == 200


async def test_room_question_with_only_done_members_says_so(hub):
    main, (r1,), lead = await _team(hub, "r1")
    await _done(lead)
    await _done(r1)
    resp = await main.post("/rooms/dev/messages", {"text": "?", "kind": "question"})
    assert code_of(resp) == "recipient_done"


async def test_only_agents_can_be_experts(hub):
    _, main = await hub.namespace()
    resp = await main.post("/admin/agents", {"name": "lead", "depends_on": ["main"]})
    assert resp.status_code == 404


async def test_revoking_an_expert_leaves_the_dependent_working(hub):
    main, (expert,), lead = await _team(hub, "r1")
    await main.ok("delete", "/admin/agents/r1")
    assert (await _done(lead))["state"] == "done"
