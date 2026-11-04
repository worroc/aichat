"""Contract tests every store backend must pass."""

import asyncio
import inspect
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from aichat_protocol.errors import ErrorCode
from aichat_server.domain import (
    Blob,
    Channel,
    ChannelKind,
    Closed,
    DomainError,
    History,
    Identity,
    Kind,
    MessageFilter,
    MsgKind,
    Namespace,
    NewMessage,
    Ref,
    Role,
    State,
)
from aichat_server.store.base import NS_EXEMPT
from aichat_server.store.memory import MemoryStore
from aichat_server.store.sqlite import SCHEMA, SqliteStore
from hypothesis import given, settings
from hypothesis import strategies as st

BACKENDS = {"memory": MemoryStore, "sqlite": SqliteStore}


@pytest.fixture(params=sorted(BACKENDS))
def make_store(request):
    return BACKENDS[request.param]


async def _room(store, ns="ns1", cid="c1") -> Channel:
    channel = Channel(
        ns=ns, id=cid, kind=ChannelKind.ROOM, name=cid, history=History.FULL,
        created_at=datetime.now(UTC),
    )
    await store.channels.add(ns, channel)
    return channel


def _msg(cid="c1", sender="a", kind=MsgKind.INFORMATION, **kw) -> NewMessage:
    return NewMessage(
        channel_id=cid, sender=sender, sender_role=Role.AGENT, kind=kind, text="hi", **kw
    )


async def test_seq_strictly_increasing_under_concurrency(make_store):
    store = make_store()
    await _room(store)
    results = await asyncio.gather(*(store.messages.add("ns1", _msg()) for _ in range(50)))
    assert sorted(m.seq for m in results) == list(range(1, 51))


async def test_seq_is_per_channel(make_store):
    store = make_store()
    await _room(store, cid="c1")
    await _room(store, cid="c2")
    a = await store.messages.add("ns1", _msg("c1"))
    b = await store.messages.add("ns1", _msg("c2"))
    assert a.seq == b.seq == 1


async def test_namespaces_are_isolated(make_store):
    store = make_store()
    await _room(store, ns="ns1")
    msg = await store.messages.add("ns1", _msg())
    assert await store.messages.get("ns2", msg.id) is None
    assert await store.channels.get("ns2", "c1") is None


@settings(max_examples=30, deadline=None)
@given(total=st.integers(0, 40), page=st.integers(1, 10))
def test_paging_backwards_never_skips(total, page):
    async def run(store):
        await _room(store)
        for _ in range(total):
            await store.messages.add("ns1", _msg())
        seen: list[int] = []
        before = None
        while True:
            flt = MessageFilter(channel_id="c1", before_seq=before, limit=page, newest=True)
            chunk = await store.messages.search("ns1", flt)
            if not chunk:
                break
            seen = [m.seq for m in chunk] + seen
            before = chunk[0].seq
        assert seen == list(range(1, total + 1))

    for backend in BACKENDS.values():
        asyncio.run(run(backend()))


async def test_pointer_only_moves_forward(make_store):
    store = make_store()
    assert await store.pointers.advance("ns1", "a", "c1", 5) == 5
    assert await store.pointers.advance("ns1", "a", "c1", 3) == 5
    assert await store.pointers.get("ns1", "a", "c1") == 5


async def test_answer_cap_and_uniqueness_under_concurrency(make_store):
    store = make_store()
    await _room(store)
    question = await store.messages.add(
        "ns1", _msg(kind=MsgKind.QUESTION, max_agents_answers=3)
    )

    async def answer(who: str):
        try:
            return await store.messages.add_answer(
                "ns1", _msg(sender=who, kind=MsgKind.ANSWER, reply_to=question.id)
            )
        except DomainError as exc:
            return exc.code

    senders = [f"s{i % 5}" for i in range(20)]  # 5 distinct senders, each 4 times
    results = await asyncio.gather(*(answer(s) for s in senders))
    accepted = [r for r in results if not isinstance(r, ErrorCode)]
    assert len(accepted) == 3
    assert len({a.sender for a in accepted}) == 3
    closed = await store.messages.get("ns1", question.id)
    assert closed.closed_reason is Closed.MAX_ANSWERS


def test_every_store_method_takes_ns_first():
    store = MemoryStore()
    for repo_name, repo in vars(store).items():
        if repo_name.startswith("_"):
            continue
        for method_name, method in inspect.getmembers(repo, inspect.iscoroutinefunction):
            if method_name.startswith("_") or (repo_name, method_name) in NS_EXEMPT:
                continue
            params = list(inspect.signature(method).parameters)
            assert params and params[0] == "ns", f"{repo_name}.{method_name} must take ns first"


async def test_delete_namespace_removes_only_that_namespace(make_store):
    store = make_store()
    for ns in ("ns1", "ns2"):
        await store.namespaces.add(Namespace(id=ns, created_at=datetime.now(UTC)))
        await _room(store, ns=ns)
        await store.messages.add(ns, _msg())
        await store.pointers.advance(ns, "a", "c1", 1)
        await store.events.add(ns, "test", {})
    await store.delete_namespace("ns1")
    assert await store.namespaces.get("ns1") is None
    assert await store.channels.list("ns1") == []
    assert await store.messages.usage("ns1") == (0, 0)
    assert await store.pointers.list("ns1") == []
    assert await store.events.list("ns1", 0) == []
    assert await store.channels.get("ns2", "c1") is not None
    assert await store.messages.usage("ns2") == (1, 2)


async def test_identity_lifetime_fields_round_trip(make_store):
    store = make_store()
    ident = Identity(
        ns="ns1", uid="u1", name="lead", role=Role.AGENT, kind=Kind.PROCESS, parent="main",
        depth=0, state=State.ACTIVE, tags=frozenset(), provider="claude", description="",
        session_ref=None, resume_template=None, created_at=datetime.now(UTC),
        depends_on=frozenset({"r1", "r2"}),
    )
    await store.identities.add("ns1", ident)
    assert await store.identities.get("ns1", "lead") == ident
    changed = replace(ident, state=State.PARKED, done_requested=True, machine="pc-a")
    await store.identities.replace("ns1", changed)
    assert await store.identities.get("ns1", "lead") == changed


async def test_sqlite_keeps_data_after_reopen(tmp_path):
    path = str(tmp_path / "hub.db")
    store = SqliteStore(path)
    await store.namespaces.add(Namespace(id="ns1", created_at=datetime.now(UTC), title="t"))
    await _room(store)
    sent = await store.messages.add("ns1", _msg(refs=()))
    await store.close()
    again = SqliteStore(path)
    assert (await again.namespaces.get("ns1")).title == "t"
    assert await again.messages.get("ns1", sent.id) == sent
    await again.close()


def _blob(data: bytes, blob_id: str = "b1", name: str = "a.txt") -> Blob:
    return Blob(
        ns="", id=blob_id, name=name, size=len(data), sha256=f"sha-{data.hex()}",
        uploader="a", created_at=datetime.now(UTC), data=data,
    )


async def test_blobs_store_dedup_and_isolation(make_store):
    store = make_store()
    stored = await store.blobs.add("ns1", _blob(b"hello"))
    assert stored.id == "b1" and stored.ns == "ns1" and stored.data == b""
    again = await store.blobs.add("ns1", _blob(b"hello", "b2", "other.txt"))
    assert again.id == "b1" and again.name == "a.txt"
    assert (await store.blobs.get("ns1", "b1")).data == b""
    assert (await store.blobs.read("ns1", "b1")).data == b"hello"
    assert await store.blobs.read("ns2", "b1") is None
    await store.blobs.add("ns2", _blob(b"hello", "b3"))
    assert (await store.blobs.get("ns2", "b3")).ns == "ns2"
    assert await store.blobs.usage("ns1") == 5
    await store.delete_namespace("ns1")
    assert await store.blobs.get("ns1", "b1") is None and await store.blobs.usage("ns1") == 0
    assert await store.blobs.usage("ns2") == 5


async def test_message_machine_and_refs_round_trip(make_store):
    store = make_store()
    await _room(store)
    refs = (
        Ref(type="url", url="file:///tmp/x", machine="pc-a"),
        Ref(type="blob", id="b1", name="x.txt", size=3, sha256="abc"),
    )
    sent = await store.messages.add("ns1", _msg(refs=refs, sender_machine="pc-a"))
    got = await store.messages.get("ns1", sent.id)
    assert got == sent and got.refs == refs and got.sender_machine == "pc-a"
    await store.messages.replace("ns1", replace(got, sender_machine="pc-b"))
    assert (await store.messages.get("ns1", sent.id)).sender_machine == "pc-b"


async def test_sqlite_adds_machine_columns_to_an_old_file(tmp_path):
    path = tmp_path / "old.db"
    old = SCHEMA.replace("    machine TEXT, PRIMARY KEY", "    PRIMARY KEY").replace(
        "    sender_machine TEXT, UNIQUE", "    UNIQUE"
    )
    assert old != SCHEMA
    conn = sqlite3.connect(path)
    conn.executescript(old)
    conn.close()
    store = SqliteStore(str(path))
    ident = Identity(
        ns="ns1", uid="u1", name="lead", role=Role.AGENT, kind=Kind.PROCESS, parent=None,
        depth=0, state=State.ACTIVE, tags=frozenset(), provider="p", description="",
        session_ref=None, resume_template=None, created_at=datetime.now(UTC), machine="pc-a",
    )
    await store.identities.add("ns1", ident)
    assert (await store.identities.get("ns1", "lead")).machine == "pc-a"
    await _room(store)
    sent = await store.messages.add("ns1", _msg(sender_machine="pc-a"))
    assert (await store.messages.get("ns1", sent.id)).sender_machine == "pc-a"
    await store.close()
