"""End-to-end flows through the HTTP API."""

import asyncio

from conftest import code_of


async def _setup_room(hub, *names):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    agents = [await hub.agent(main, n, rooms=["dev"]) for n in names]
    return main, agents


async def test_question_max_agents_answers(hub):
    main, (a, b, c) = await _setup_room(hub, "ann", "ben", "cat")
    q = await main.ok(
        "post", "/rooms/dev/messages",
        {"text": "A or B?", "kind": "question", "max_agents_answers": 2},
    )
    qid = q["message"]["id"]
    waiter = asyncio.create_task(main.get(f"/questions/{qid}/answers", params={"wait": 5}))
    await asyncio.sleep(0.05)
    await a.ok("post", f"/questions/{qid}/answers", {"text": "A"})
    assert code_of(await a.post(f"/questions/{qid}/answers", {"text": "A again"})) == "already_answered"
    await b.ok("post", f"/questions/{qid}/answers", {"text": "B"})
    assert code_of(await c.post(f"/questions/{qid}/answers", {"text": "late"})) == "question_closed"
    result = (await waiter).json()
    assert result["closed"] and result["closed_reason"] == "max_answers"
    assert [x["sender"] for x in result["answers"]] == ["ann", "ben"]
    assert not result["timed_out"]


async def test_cap_cannot_exceed_answerers(hub):
    main, _ = await _setup_room(hub, "ann")
    resp = await main.post(
        "/rooms/dev/messages", {"text": "q", "kind": "question", "max_agents_answers": 3}
    )
    assert code_of(resp) == "invalid"


async def test_wait_times_out_with_partial_answers(hub):
    main, (a, _) = await _setup_room(hub, "ann", "ben")
    q = await main.ok(
        "post", "/rooms/dev/messages", {"text": "q", "kind": "question", "max_agents_answers": 2}
    )
    qid = q["message"]["id"]
    await a.ok("post", f"/questions/{qid}/answers", {"text": "one"})
    result = await main.ok("get", f"/questions/{qid}/answers", params={"wait": 0.2})
    assert result["timed_out"] and not result["closed"] and len(result["answers"]) == 1


async def test_only_to_targets_can_answer(hub):
    main, (a, b) = await _setup_room(hub, "ann", "ben")
    q = await main.ok("post", "/rooms/dev/messages", {"text": "q", "kind": "question", "to": ["ann"]})
    qid = q["message"]["id"]
    assert code_of(await b.post(f"/questions/{qid}/answers", {"text": "me"})) == "forbidden"
    assert code_of(await main.post(f"/questions/{qid}/answers", {"text": "self"})) == "forbidden"
    await a.ok("post", f"/questions/{qid}/answers", {"text": "ok"})


async def test_information_is_not_answerable(hub):
    main, (a,) = await _setup_room(hub, "ann")
    info = await main.ok("post", "/rooms/dev/messages", {"text": "fyi"})
    resp = await a.post(f"/questions/{info['message']['id']}/answers", {"text": "x"})
    assert code_of(resp) == "not_answerable"


async def test_cursor_ack_redelivers_until_acked(hub):
    main, (a,) = await _setup_room(hub, "ann")
    await main.ok("post", "/rooms/dev/messages", {"text": "one"})
    await main.ok("post", "/rooms/dev/messages", {"text": "two"})
    first = await a.ok("get", "/rooms/dev/new")
    assert [m["text"] for m in first["messages"]] == ["one", "two"]
    again = await a.ok("get", "/rooms/dev/new")  # crash before ack: same messages again
    assert [m["text"] for m in again["messages"]] == ["one", "two"]
    await a.ok("post", "/rooms/dev/ack", {"cursor": again["cursor"]})
    assert (await a.ok("get", "/rooms/dev/new"))["messages"] == []


async def test_read_status_is_per_reader(hub):
    main, (a, b) = await _setup_room(hub, "ann", "ben")
    await main.ok("post", "/rooms/dev/messages", {"text": "hello"})
    page = await a.ok("get", "/inbox")
    await a.ok("post", "/inbox/ack", {"cursor": page["cursor"]})
    assert (await a.ok("get", "/inbox"))["messages"] == []
    assert [m["text"] for m in (await b.ok("get", "/inbox"))["messages"]] == ["hello"]


async def test_inbox_long_poll_wakes_on_dm(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    waiter = asyncio.create_task(bob.get("/inbox", params={"wait": 5}))
    await asyncio.sleep(0.05)
    await main.ok("post", "/dm/bob", {"text": "wake up"})
    page = (await waiter).json()
    assert [m["text"] for m in page["messages"]] == ["wake up"]
    assert page["messages"][0]["channel"] == "@main"


async def test_thread_q_a_q_a(hub):
    main, (a,) = await _setup_room(hub, "ann")
    q1 = (await main.ok("post", "/rooms/dev/messages", {"text": "q1", "kind": "question"}))["message"]
    a1 = (await a.ok("post", f"/questions/{q1['id']}/answers", {"text": "a1"}))["message"]
    q2 = (
        await main.ok(
            "post", "/rooms/dev/messages", {"text": "q2", "kind": "question", "reply_to": a1["id"]}
        )
    )["message"]
    await a.ok("post", f"/questions/{q2['id']}/answers", {"text": "a2"})
    thread = await a.ok("get", f"/messages/{q1['id']}/thread")
    assert [m["text"] for m in thread["messages"]] == ["q1", "a1", "q2", "a2"]
    assert {m["thread_id"] for m in thread["messages"]} == {q1["id"]}


async def test_history_paging_and_sender_filter(hub):
    main, (a,) = await _setup_room(hub, "ann")
    for i in range(5):
        await main.ok("post", "/rooms/dev/messages", {"text": f"m{i}"})
        await a.ok("post", "/rooms/dev/messages", {"text": f"a{i}"})
    last = await a.ok("get", "/rooms/dev/messages", params={"limit": 3})
    assert [m["text"] for m in last["messages"]] == ["a3", "m4", "a4"] and last["has_more"]
    older = await a.ok(
        "get", "/rooms/dev/messages", params={"limit": 3, "before_seq": last["messages"][0]["seq"]}
    )
    assert [m["text"] for m in older["messages"]] == ["m2", "a2", "m3"]
    mine = await a.ok("get", "/rooms/dev/messages", params={"sender": "main", "limit": 50})
    assert [m["text"] for m in mine["messages"]] == [f"m{i}" for i in range(5)]


async def test_since_join_hides_old_history(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev", "history": "since_join"})
    await main.ok("post", "/rooms/dev/messages", {"text": "before"})
    late = await hub.agent(main, "late", rooms=["dev"])
    await main.ok("post", "/rooms/dev/messages", {"text": "after"})
    texts = [m["text"] for m in (await late.ok("get", "/rooms/dev/messages"))["messages"]]
    assert texts == ["after"]


async def test_idempotent_retry(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "bob")
    headers = {"Idempotency-Key": "k1"}
    one = await main.ok("post", "/dm/bob", {"text": "once"}, headers=headers)
    two = await main.ok("post", "/dm/bob", {"text": "once"}, headers=headers)
    assert one["message"]["id"] == two["message"]["id"]
    conflict = await main.post("/dm/bob", {"text": "different"}, headers=headers)
    assert code_of(conflict) == "idempotency_conflict"
    history = await main.ok("get", "/dm/bob/messages")
    assert len(history["messages"]) == 1


async def test_human_posts_with_role(hub):
    ns, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    bob = await hub.agent(main, "bob", rooms=["dev"])
    human = await hub.human(ns)
    await human.ok("post", "/rooms/dev/messages", {"text": "hi from a person"})
    msg = (await bob.ok("get", "/rooms/dev/new"))["messages"][0]
    assert msg["sender"] == "human-alice" and msg["sender_role"] == "human"
    await human.ok("post", "/dm/bob", {"text": "direct"})
    reply = await bob.ok("post", "/dm/human-alice", {"text": "hello human"})
    assert reply["message"]["channel"] == "@human-alice"


async def test_wake_event_for_parked_agent(hub, client):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob", resume_template="echo {session} {prompt}")
    await bob.ok("post", "/me/state", {"state": "parked", "session_ref": "S1"})
    sent = await main.ok("post", "/dm/bob", {"text": "are you there?", "kind": "question"})
    assert sent["recipients"] == [{"name": "bob", "state": "parked", "wake": "queued"}]
    wake = await main.ok("get", "/wake/events")
    event = wake["events"][-1]
    assert event["type"] == "wake_needed"
    assert event["data"]["name"] == "bob" and event["data"]["session_ref"] == "S1"
    answers = await main.ok("get", f"/questions/{sent['message']['id']}/answers")
    assert answers["pending_wake"] == ["bob"]


async def test_amend_answer_counts_once(hub):
    main, (a, _) = await _setup_room(hub, "ann", "ben")
    q = await main.ok(
        "post", "/rooms/dev/messages", {"text": "q", "kind": "question", "max_agents_answers": 2}
    )
    qid = q["message"]["id"]
    await a.ok("post", f"/questions/{qid}/answers", {"text": "first"})
    amended = await a.ok("put", f"/questions/{qid}/answers/me", {"text": "better"})
    assert amended["text"] == "better" and amended["amended_at"]
    result = await main.ok("get", f"/questions/{qid}/answers")
    assert [x["text"] for x in result["answers"]] == ["better"] and not result["closed"]


async def test_refs_round_trip(hub):
    _, main = await hub.namespace()
    await hub.agent(main, "bob")
    ref = {"type": "git", "repo": "org/app", "branch": "sso", "path": "docs/design/sso.md"}
    sent = await main.ok("post", "/dm/bob", {"text": "design", "refs": [ref]})
    assert sent["message"]["refs"][0]["path"] == "docs/design/sso.md"


async def test_process_creates_subagents_that_talk(hub):
    _, main = await hub.namespace()
    await main.ok("post", "/admin/rooms", {"name": "research"})
    proc = await hub.agent(main, "res-web", rooms=["research"], provider="claude")
    sub_a = await hub.child(proc, "res-web-a", rooms=["research"])
    sub_b = await hub.child(proc, "res-web-b", rooms=["research"], contacts=["res-web-a"])
    await sub_b.ok("post", "/dm/res-web-a", {"text": "sibling hello"})
    got = await sub_a.ok("get", "/inbox")
    assert got["messages"][0]["sender"] == "res-web-b"
    me = await proc.ok("get", "/me")
    assert me["children"] == ["res-web-a", "res-web-b"]


async def test_revoke_ends_running_wait(hub):
    _, main = await hub.namespace()
    bob = await hub.agent(main, "bob")
    waiter = asyncio.create_task(bob.get("/inbox", params={"wait": 5}))
    await asyncio.sleep(0.05)
    await main.ok("delete", "/admin/agents/bob")
    assert (await waiter).status_code == 401


async def test_too_many_waits(hub):
    main, (a,) = await _setup_room(hub, "ann")
    qs = [
        (await main.ok("post", "/rooms/dev/messages", {"text": f"q{i}", "kind": "question"}))[
            "message"
        ]["id"]
        for i in range(5)
    ]
    tasks = [
        asyncio.create_task(main.get(f"/questions/{q}/answers", params={"wait": 1})) for q in qs[:4]
    ]
    await asyncio.sleep(0.05)
    fifth = await main.get(f"/questions/{qs[4]}/answers", params={"wait": 1})
    assert code_of(fifth) == "too_many_waits"
    for t in tasks:
        t.cancel()


async def test_namespace_title(hub, client):
    resp = await client.post(
        "/ops/namespaces", json={"title": "  rca clustering ", "admin_name": "main"}, headers=hub.op
    )
    assert resp.json()["title"] == "rca clustering"
    listed = (await client.get("/ops/namespaces", headers=hub.op)).json()
    assert [n["title"] for n in listed] == ["rca clustering"]
    too_long = await client.post("/ops/namespaces", json={"title": "x" * 201}, headers=hub.op)
    assert code_of(too_long) == "invalid"
