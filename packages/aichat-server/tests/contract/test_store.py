"""Contract tests every store backend must pass."""

import asyncio
import inspect
from datetime import UTC, datetime

import pytest
from aichat_protocol.errors import ErrorCode
from aichat_server.domain import (
    Channel,
    ChannelKind,
    Closed,
    DomainError,
    History,
    MessageFilter,
    MsgKind,
    NewMessage,
    Role,
)
from aichat_server.store.base import NS_EXEMPT
from aichat_server.store.memory import MemoryStore
from hypothesis import given, settings
from hypothesis import strategies as st

BACKENDS = {"memory": MemoryStore}


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
    async def run():
        store = MemoryStore()
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

    asyncio.run(run())


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
