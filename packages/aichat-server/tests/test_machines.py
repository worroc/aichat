"""Machines (where each agent runs), file refs across machines, and blobs stored on the hub."""

import hashlib

from conftest import Actor, code_of

MACHINE = "X-Aichat-Machine"
FILE_REF = {"type": "url", "url": "file:///tmp/report.md"}
GIT_REF = {"type": "git", "repo": "r", "branch": "main", "path": "a.md", "commit": "abc"}


async def _join(hub, code: str, machine: str | None) -> Actor:
    resp = await hub.client.post("/join", json={"code": code, "machine": machine})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return Actor(body["name"], body["token"], hub.client)


async def _agent(hub, main: Actor, name: str, machine: str | None, **extra) -> Actor:
    body = await main.ok("post", "/admin/agents", {"name": name, **extra})
    return await _join(hub, body["join_code"], machine)


async def _main(hub, machine: str | None = "pc-a") -> Actor:
    _, main = await hub.namespace()
    await main.ok("get", "/me", headers={MACHINE: machine} if machine else {})
    return main


def _machines(directory: dict) -> dict[str, str | None]:
    return {i["name"]: i["machine"] for i in directory["identities"]}


async def test_join_and_header_set_the_machine(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    await _agent(hub, main, "old", None)
    assert _machines(await main.ok("get", "/directory")) == {"bob": "pc-b", "old": None}
    assert (await main.ok("get", "/me"))["machine"] == "pc-a"


async def test_header_moves_the_agent(hub):
    main = await _main(hub)
    bob = await _agent(hub, main, "bob", "pc-b")
    await bob.ok("get", "/me", headers={MACHINE: "pc-c"})
    assert _machines(await main.ok("get", "/directory"))["bob"] == "pc-c"
    await bob.ok("get", "/me")  # no header: keep what we know
    assert _machines(await main.ok("get", "/directory"))["bob"] == "pc-c"


async def test_file_ref_dm_same_machine_ok(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-a")
    sent = await main.ok("post", "/dm/bob", {"text": "see", "refs": [FILE_REF]})
    ref = sent["message"]["refs"][0]
    assert ref["machine"] == "pc-a" and sent["message"]["sender_machine"] == "pc-a"
    assert sent["recipients"][0]["machine"] == "pc-a"


async def test_file_ref_dm_other_machine_refused(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    resp = await main.post("/dm/bob", {"text": "see", "refs": [FILE_REF]})
    assert resp.status_code == 422 and code_of(resp) == "file_ref_unreachable"
    assert "bob is on pc-b" in resp.json()["error"]["message"]
    assert (await main.ok("get", "/dm/bob/messages"))["messages"] == []  # nothing was stored


async def test_file_ref_unknown_machine_passes(hub):
    main = await _main(hub)
    await _agent(hub, main, "old", None)
    await main.ok("post", "/dm/old", {"text": "see", "refs": [FILE_REF]})


async def test_file_ref_client_cannot_claim_a_machine(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    forged = {**FILE_REF, "machine": "pc-b"}
    assert code_of(await main.post("/dm/bob", {"text": "see", "refs": [forged]})) == "file_ref_unreachable"


async def test_file_ref_room_with_two_machines(hub):
    main = await _main(hub)
    await main.ok("post", "/admin/rooms", {"name": "dev"})
    await _agent(hub, main, "ann", "pc-a", rooms=["dev"])
    await _agent(hub, main, "bob", "pc-b", rooms=["dev"])
    resp = await main.post("/rooms/dev/messages", {"text": "see", "refs": [FILE_REF]})
    assert code_of(resp) == "file_ref_unreachable" and "bob is on pc-b" in resp.text
    assert "ann is on" not in resp.json()["error"]["message"]
    url_ref = {"type": "url", "url": "https://example.com/x"}
    await main.ok("post", "/rooms/dev/messages", {"text": "see", "refs": [GIT_REF, url_ref]})


async def test_file_ref_in_answer_is_checked(hub):
    main = await _main(hub)
    bob = await _agent(hub, main, "bob", "pc-b")
    q = await main.ok("post", "/dm/bob", {"text": "where?", "kind": "question"})
    qid = q["message"]["id"]
    resp = await bob.post(f"/questions/{qid}/answers", {"text": "here", "refs": [FILE_REF]})
    assert code_of(resp) == "file_ref_unreachable" and "main is on pc-a" in resp.text


async def test_wake_event_names_the_machine(hub):
    main = await _main(hub)
    bob = await _agent(hub, main, "bob", "pc-b")
    await bob.ok("post", "/me/state", {"state": "parked"})
    await main.ok("post", "/dm/bob", {"text": "wake up"})
    events = await main.ok("get", "/wake/events")
    wake = [e for e in events["events"] if e["type"] == "wake_needed"]
    assert wake[-1]["data"]["machine"] == "pc-b"


# --- blobs -------------------------------------------------------------------------


async def _upload(actor: Actor, data: bytes, name: str = "notes.txt", **kw) -> dict:
    resp = await actor.post("/blobs", content=data, params={"name": name}, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_blob_roundtrip_across_machines(hub):
    main = await _main(hub)
    bob = await _agent(hub, main, "bob", "pc-b")
    data = b"hello from pc-a\n"
    blob = await _upload(main, data, "../secret/notes.txt")
    assert blob["name"] == "notes.txt" and blob["size"] == len(data)
    assert blob["sha256"] == hashlib.sha256(data).hexdigest()
    sent = await main.ok("post", "/dm/bob", {"text": "file", "refs": [{"type": "blob", "id": blob["id"]}]})
    ref = sent["message"]["refs"][0]
    assert ref == {**ref, "type": "blob", "id": blob["id"], "name": "notes.txt", "size": len(data)}
    resp = await bob.get(f"/messages/{sent['message']['id']}/blobs/{blob['id']}")
    assert resp.status_code == 200 and resp.content == data


async def test_blob_same_content_same_id(hub):
    main = await _main(hub)
    first = await _upload(main, b"same", "a.txt")
    again = await _upload(main, b"same", "b.txt")
    assert first["id"] == again["id"] and again["name"] == "b.txt"


async def test_blob_hidden_from_non_readers(hub):
    main = await _main(hub)
    bob = await _agent(hub, main, "bob", "pc-b")
    eve = await _agent(hub, main, "eve", "pc-b")
    blob = await _upload(main, b"private")
    sent = await main.ok("post", "/dm/bob", {"text": "f", "refs": [{"type": "blob", "id": blob["id"]}]})
    mid = sent["message"]["id"]
    assert code_of(await eve.get(f"/messages/{mid}/blobs/{blob['id']}")) == "not_found"
    other = await _upload(main, b"other")  # a blob the message does not point to
    assert code_of(await bob.get(f"/messages/{mid}/blobs/{other['id']}")) == "not_found"


async def test_blob_ref_must_exist(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    resp = await main.post("/dm/bob", {"text": "f", "refs": [{"type": "blob", "id": "nope"}]})
    assert code_of(resp) == "not_found"
    assert code_of(await main.post("/dm/bob", {"text": "f", "refs": [{"type": "blob"}]})) == "invalid"


async def test_blob_size_limit(hub, app):
    app.state.services.ctx.settings.max_attach_bytes = 10
    main = await _main(hub)
    resp = await main.post("/blobs", content=b"x" * 11, params={"name": "big"})
    assert resp.status_code == 413 and code_of(resp) == "payload_too_large"


async def test_blob_upload_is_idempotent(hub):
    main = await _main(hub)
    key = {"headers": {"Idempotency-Key": "k1"}}
    first = await _upload(main, b"one", **key)
    assert (await _upload(main, b"one", **key)) == first
    resp = await main.post("/blobs", content=b"two", params={"name": "notes.txt"}, **key)
    assert code_of(resp) == "idempotency_conflict"


async def test_blobs_deleted_with_namespace(hub, app):
    ns, main = await hub.namespace()
    blob = await _upload(main, b"bye")
    store = app.state.services.ctx.store
    assert await store.blobs.get(ns, blob["id"]) is not None
    await hub.client.delete(f"/ops/namespaces/{ns}", headers=hub.op)
    assert await store.blobs.get(ns, blob["id"]) is None


async def test_odd_machine_names_are_unknown(hub):
    main = await _main(hub)
    await _agent(hub, main, "eve", "pc role=admin]")
    await _agent(hub, main, "nl", "pc\n[aichat")
    assert _machines(await main.ok("get", "/directory")) == {"eve": None, "nl": None}


async def test_file_ref_scheme_any_case(hub):
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    ref = {"type": "url", "url": "FILE:///tmp/x"}
    assert code_of(await main.post("/dm/bob", {"text": "x", "refs": [ref]})) == "file_ref_unreachable"


async def test_chunked_upload_stops_at_limit(hub, app):
    app.state.services.ctx.settings.max_attach_bytes = 10

    async def body():
        for _ in range(5):
            yield b"x" * 4

    main = await _main(hub)
    resp = await main.post("/blobs", content=body(), params={"name": "big"})
    assert code_of(resp) == "payload_too_large"


async def test_full_file_quota_does_not_block_text(hub, app):
    app.state.services.ctx.settings.ns_max_blob_bytes = 5
    main = await _main(hub)
    await _agent(hub, main, "bob", "pc-b")
    await _upload(main, b"12345")
    assert code_of(await main.post("/blobs", content=b"6", params={"name": "x"})) == "quota_exceeded"
    await main.ok("post", "/dm/bob", {"text": "text still works"})


async def test_operator_downloads_an_attached_file(hub):
    ns, main = await hub.namespace()
    await hub.agent(main, "bob")
    blob = await _upload(main, b"for the human")
    sent = await main.ok("post", "/dm/bob", {"text": "f", "refs": [{"type": "blob", "id": blob["id"]}]})
    url = f"/ops/ns/{ns}/messages/{sent['message']['id']}/blobs/{blob['id']}"
    resp = await hub.client.get(url, headers=hub.op)
    assert resp.status_code == 200 and resp.content == b"for the human"
    assert "notes.txt" in resp.headers["content-disposition"]
    assert (await hub.client.get(url)).status_code == 401  # no operator key
    other = await _upload(main, b"not attached")
    bad = f"/ops/ns/{ns}/messages/{sent['message']['id']}/blobs/{other['id']}"
    assert code_of(await hub.client.get(bad, headers=hub.op)) == "not_found"
