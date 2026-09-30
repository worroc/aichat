"""Long-poll helper with per-reader limits.

A reader may hold at most N concurrent waits. A new wait on the same key
supersedes the older one, which then returns its current result.
"""

import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aichat_protocol.errors import ErrorCode

from ..domain import DomainError
from ..notify import Notifier


@dataclass
class WaitHandle:
    superseded: bool = False


class WaitRegistry:
    def __init__(self) -> None:
        self._per_reader: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._by_key: dict[tuple[str, str, str], WaitHandle] = {}

    @asynccontextmanager
    async def slot(
        self, reader: tuple[str, str], topic: str, limit: int
    ) -> AsyncIterator[tuple[WaitHandle, bool]]:
        """Yield (handle, superseded_other)."""
        key = (*reader, topic)
        previous = self._by_key.get(key)
        if previous is not None:
            previous.superseded = True
        elif self._per_reader[reader] >= limit:
            raise DomainError(ErrorCode.TOO_MANY_WAITS, f"at most {limit} concurrent waits")
        handle = WaitHandle()
        self._by_key[key] = handle
        self._per_reader[reader] += 1
        try:
            yield handle, previous is not None
        finally:
            self._per_reader[reader] -= 1
            if self._by_key.get(key) is handle:
                del self._by_key[key]


async def wait_until[T](
    notifier: Notifier,
    registry: WaitRegistry,
    reader: tuple[str, str],
    topics: tuple[str, ...],
    probe: Callable[[], Awaitable[tuple[T, bool]]],
    timeout: float,
    limit: int,
) -> tuple[T, bool]:
    """Re-run ``probe`` until it reports done or ``timeout`` passes.

    Returns (result, timed_out). Subscribes before the first probe, so a
    change between probe and wait is never missed.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    async with (
        registry.slot(reader, topics[0], limit) as (handle, superseded_other),
        notifier.subscribe(*topics) as sub,
    ):
        if superseded_other:
            await notifier.publish(topics[0])
        while True:
            result, done = await probe()
            remaining = deadline - time.monotonic()
            if done or handle.superseded:
                return result, False
            if remaining <= 0:
                return result, True
            await sub.wait(remaining)
