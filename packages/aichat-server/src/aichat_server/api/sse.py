"""Server-sent events over the notifier + event log."""

from collections.abc import AsyncIterator, Awaitable, Callable

from aichat_protocol import models as wire
from fastapi import Request
from fastapi.responses import StreamingResponse

from ..domain import DomainError
from ..notify import Notifier

Fetch = Callable[[int], Awaitable[tuple[list[wire.EventOut], int]]]

HEARTBEAT_SECONDS = 15.0
BATCH = 200


def _frame(event: wire.EventOut) -> str:
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def _events(
    request: Request, notifier: Notifier, topic: str, fetch: Fetch, after: int
) -> AsyncIterator[str]:
    cursor = after
    async with notifier.subscribe(topic) as sub:
        yield ": connected\n\n"
        while not await request.is_disconnected():
            try:
                events, cursor = await fetch(cursor)
            except DomainError as exc:
                yield f"event: error\ndata: {exc.code.value}\n\n"
                return
            for event in events:
                yield _frame(event)
            if len(events) >= BATCH:
                continue
            if not await sub.wait(HEARTBEAT_SECONDS):
                yield ": ping\n\n"


def sse_response(
    request: Request, notifier: Notifier, topic: str, fetch: Fetch, after: int
) -> StreamingResponse:
    return StreamingResponse(
        _events(request, notifier, topic, fetch, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
