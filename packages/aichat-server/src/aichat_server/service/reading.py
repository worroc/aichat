"""Reading: new messages with cursor + ack, inbox, history, threads."""

import base64
import binascii
import json

from aichat_protocol import models as wire
from aichat_protocol.names import normalize

from ..domain import Channel, Membership, Message, MessageFilter, MsgKind, invalid
from ..notify import reader_topic
from ..security import Principal
from .access import Access, seq_floor
from .context import Ctx
from .present import Presenter
from .waits import wait_until


def encode_cursor(positions: dict[str, int]) -> str:
    raw = json.dumps(positions, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error) as exc:
        raise invalid("bad cursor") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, int) for k, v in data.items()
    ):
        raise invalid("bad cursor")
    return data


def _positions(messages: list[Message]) -> dict[str, int]:
    positions: dict[str, int] = {}
    for message in messages:
        positions[message.channel_id] = max(positions.get(message.channel_id, 0), message.seq)
    return positions


class ReadingService:
    def __init__(self, ctx: Ctx, access: Access, presenter: Presenter) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.access = access
        self.presenter = presenter

    def _limit(self, limit: int) -> int:
        return max(1, min(limit, self.ctx.settings.max_limit))

    async def _unread(
        self, ns: str, reader: str, channel: Channel, membership: Membership, limit: int
    ) -> list[Message]:
        pointer = await self.store.pointers.get(ns, reader, channel.id)
        floor = max(pointer, seq_floor(channel, membership))
        flt = MessageFilter(
            channel_id=channel.id, after_seq=floor, exclude_sender=reader, limit=limit
        )
        return await self.store.messages.search(ns, flt)

    async def _page(self, messages: list[Message], limit: int, viewer: str) -> wire.MessagesPage:
        shown = messages[:limit]
        return wire.MessagesPage(
            messages=await self.presenter.messages(shown, viewer),
            cursor=encode_cursor(_positions(shown)) if shown else None,
            has_more=len(messages) > limit,
        )

    async def _long_poll(self, p: Principal, probe, wait: float):
        return await wait_until(
            self.ctx.notifier,
            self.ctx.waits,
            (p.ns, p.name),
            (reader_topic(p.ns, p.name),),
            probe,
            min(wait, self.ctx.settings.max_wait),
            self.ctx.settings.max_waits_per_reader,
        )

    # --- new messages in one channel -----------------------------------------------------

    async def new(
        self, p: Principal, room: str | None, dm: str | None, wait: float, limit: int
    ) -> wire.MessagesPage:
        limit = self._limit(limit)
        room, dm = _norm(room), _norm(dm)

        async def probe() -> tuple[list[Message], bool]:
            await self.ctx.identity(p)
            channel, membership = await self.access.channel_for_read(p, room, dm)
            found = await self._unread(p.ns, p.name, channel, membership, limit + 1)
            return found, bool(found)

        found, _ = await self._long_poll(p, probe, wait)
        return await self._page(found, limit, p.name)

    # --- inbox across all channels -----------------------------------------------------------

    async def _all_unread(self, ns: str, reader: str, limit: int) -> list[Message]:
        found: list[Message] = []
        for membership in await self.store.memberships.list_for_member(ns, reader):
            channel = await self.store.channels.get(ns, membership.channel_id)
            if channel is not None:
                found.extend(await self._unread(ns, reader, channel, membership, limit))
        return sorted(found, key=lambda m: (m.time, m.seq))

    async def inbox(self, p: Principal, wait: float, limit: int) -> wire.MessagesPage:
        limit = self._limit(limit)

        async def probe() -> tuple[list[Message], bool]:
            await self.ctx.identity(p)
            found = await self._all_unread(p.ns, p.name, limit + 1)
            return found, bool(found)

        found, _ = await self._long_poll(p, probe, wait)
        return await self._page(found, limit, p.name)

    async def unread_count(self, ns: str, reader: str) -> int:
        return len(await self._all_unread(ns, reader, self.ctx.settings.max_limit))

    async def ack(self, p: Principal, cursor: str) -> wire.AckResponse:
        await self.ctx.identity(p)
        acked: dict[str, int] = {}
        for channel_id, seq in decode_cursor(cursor).items():
            channel = await self.store.channels.get(p.ns, channel_id)
            membership = None if channel is None else await self.access.readable(p, channel)
            if channel is None or membership is None:
                continue
            last = await self.store.messages.last_seq(p.ns, channel_id)
            acked[channel_id] = await self.store.pointers.advance(
                p.ns, p.name, channel_id, min(seq, last)
            )
        return wire.AckResponse(acked=acked)

    # --- history and threads -------------------------------------------------------------------

    async def history(
        self,
        p: Principal,
        room: str | None,
        dm: str | None,
        *,
        before_seq: int | None,
        limit: int,
        sender: str | None,
        kind: str | None,
        thread_id: str | None,
    ) -> wire.MessagesPage:
        await self.ctx.identity(p)
        channel, membership = await self.access.channel_for_read(p, _norm(room), _norm(dm))
        limit = self._limit(limit)
        flt = MessageFilter(
            channel_id=channel.id,
            after_seq=seq_floor(channel, membership),
            before_seq=before_seq,
            sender=_norm(sender),
            kind=MsgKind(kind) if kind else None,
            thread_id=thread_id,
            limit=limit + 1,
            newest=True,
        )
        found = await self.store.messages.search(p.ns, flt)
        has_more = len(found) > limit
        shown = found[1:] if has_more else found
        return wire.MessagesPage(
            messages=await self.presenter.messages(shown, p.name), has_more=has_more
        )

    async def thread(self, p: Principal, message_id: str) -> wire.ThreadResponse:
        await self.ctx.identity(p)
        message, channel, membership = await self.access.message(p, message_id)
        floor = seq_floor(channel, membership)
        items = [m for m in await self.store.messages.thread(p.ns, message.thread_id) if m.seq > floor]
        return wire.ThreadResponse(messages=await self.presenter.messages(items, p.name))


def _norm(value: str | None) -> str | None:
    return None if value is None else normalize(value)
