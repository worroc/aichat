"""Wake-up hints for long-polls.

A notification only says "something changed on this topic, re-check the
store". Waiters always re-query, so a missed or duplicate notification
costs latency, never correctness.
"""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol


class Subscription(Protocol):
    async def wait(self, timeout: float) -> bool: ...


class Notifier(Protocol):
    async def publish(self, topic: str) -> None: ...

    def subscribe(self, *topics: str) -> AbstractAsyncContextManager[Subscription]: ...


class MemorySubscription:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def set(self) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return False
        self._event.clear()
        return True


class MemoryNotifier:
    def __init__(self) -> None:
        self._subs: defaultdict[str, set[MemorySubscription]] = defaultdict(set)

    async def publish(self, topic: str) -> None:
        for sub in tuple(self._subs.get(topic, ())):
            sub.set()

    @asynccontextmanager
    async def subscribe(self, *topics: str) -> AsyncIterator[MemorySubscription]:
        sub = MemorySubscription()
        for topic in topics:
            self._subs[topic].add(sub)
        try:
            yield sub
        finally:
            for topic in topics:
                self._drop(topic, sub)

    def _drop(self, topic: str, sub: MemorySubscription) -> None:
        subs = self._subs.get(topic)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            del self._subs[topic]

    def topic_count(self) -> int:
        return len(self._subs)


def reader_topic(ns: str, name: str) -> str:
    return f"ns:{ns}:reader:{name}"


def question_topic(ns: str, qid: str) -> str:
    return f"ns:{ns}:q:{qid}"


def events_topic(ns: str) -> str:
    return f"ns:{ns}:events"


OPS_TOPIC = "ops"
