"""Operator views across namespaces, and event streams."""

from aichat_protocol import models as wire

from ..domain import ChannelKind, Event, MessageFilter, MsgKind, not_found
from ..notify import OPS_TOPIC, events_topic
from ..security import Principal
from .access import label
from .context import Ctx
from .present import Presenter, ops_identity_out

WAKE_EVENT = "wake_needed"


def event_out(event: Event) -> wire.EventOut:
    return wire.EventOut(seq=event.seq, time=event.time, ns=event.ns, type=event.type, data=event.data)


class OpsService:
    def __init__(self, ctx: Ctx, presenter: Presenter) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.presenter = presenter

    async def _ns(self, ns_id: str) -> str:
        if await self.store.namespaces.get(ns_id) is None:
            raise not_found("namespace")
        return ns_id

    async def namespaces(self) -> list[wire.NamespaceOut]:
        out: list[wire.NamespaceOut] = []
        for ns in await self.store.namespaces.list():
            count, _ = await self.store.messages.usage(ns.id)
            out.append(
                wire.NamespaceOut(
                    id=ns.id,
                    title=ns.title,
                    created_at=ns.created_at,
                    identities=len(await self.store.identities.list(ns.id)),
                    rooms=len(await self.store.channels.list(ns.id, ChannelKind.ROOM)),
                    messages=count,
                )
            )
        return out

    async def identities(self, ns_id: str) -> list[wire.OpsIdentityOut]:
        ns = await self._ns(ns_id)
        items = await self.store.identities.list(ns)
        return [ops_identity_out(i) for i in sorted(items, key=lambda i: i.created_at)]

    async def channels(self, ns_id: str) -> list[wire.ChannelOut]:
        ns = await self._ns(ns_id)
        out: list[wire.ChannelOut] = []
        for channel in await self.store.channels.list(ns):
            members = await self.store.memberships.list_for_channel(ns, channel.id)
            last = await self.store.messages.last_seq(ns, channel.id)
            out.append(
                wire.ChannelOut(
                    id=channel.id,
                    kind=channel.kind.value,
                    name=channel.name,
                    members=sorted(m.name for m in members),
                    history=channel.history.value,
                    messages=last,
                    last_seq=last,
                )
            )
        return sorted(out, key=lambda c: (c.kind, c.name))

    async def messages(
        self,
        ns_id: str,
        *,
        channel_id: str | None,
        sender: str | None,
        kind: str | None,
        thread_id: str | None,
        before_seq: int | None,
        limit: int,
    ) -> wire.MessagesPage:
        ns = await self._ns(ns_id)
        limit = max(1, min(limit, self.ctx.settings.max_limit))
        flt = MessageFilter(
            channel_id=channel_id,
            sender=sender,
            kind=MsgKind(kind) if kind else None,
            thread_id=thread_id,
            before_seq=before_seq,
            limit=limit + 1,
            newest=True,
        )
        found = await self.store.messages.search(ns, flt)
        has_more = len(found) > limit
        shown = found[1:] if has_more else found
        return wire.MessagesPage(
            messages=await self.presenter.messages(shown, None), has_more=has_more
        )

    async def questions(self, ns_id: str, limit: int) -> list[wire.MessageOut]:
        ns = await self._ns(ns_id)
        flt = MessageFilter(kind=MsgKind.QUESTION, limit=limit, newest=True)
        return await self.presenter.messages(await self.store.messages.search(ns, flt), None)

    async def pointers(self, ns_id: str) -> list[wire.PointerOut]:
        ns = await self._ns(ns_id)
        out: list[wire.PointerOut] = []
        for reader, channel_id, seq in await self.store.pointers.list(ns):
            channel = await self.store.channels.get(ns, channel_id)
            name = label(channel, None) if channel else channel_id
            out.append(wire.PointerOut(reader=reader, channel=name, acked_seq=seq))
        return sorted(out, key=lambda x: (x.channel, x.reader))

    async def audit(self, ns_id: str, limit: int) -> list[wire.AuditOut]:
        ns = await self._ns(ns_id)
        entries = await self.store.audit.list(ns, limit)
        return [
            wire.AuditOut(
                ts=e.ts, actor=e.actor, action=e.action, target=e.target,
                msg_id=e.msg_id, detail=e.detail,
            )
            for e in entries
        ]

    # --- streams --------------------------------------------------------------------

    async def ops_events(self, after: int, limit: int = 200) -> list[wire.EventOut]:
        return [event_out(e) for e in await self.store.events.list_all(after, limit)]

    def ops_topic(self) -> str:
        return OPS_TOPIC

    async def wake_events(self, p: Principal, after: int, limit: int = 200) -> tuple[list[wire.EventOut], int]:
        """Wake events for the caller's children (all of them for the admin).

        Returns (events, last_seen_seq) so the stream can skip non-matching
        events without re-reading them.
        """
        await self.ctx.identity(p)
        events = await self.store.events.list(p.ns, after, limit)
        last = events[-1].seq if events else after
        mine = [
            event_out(e)
            for e in events
            if e.type == WAKE_EVENT and (p.is_admin or e.data.get("parent") == p.name)
        ]
        return mine, last

    def wake_topic(self, p: Principal) -> str:
        return events_topic(p.ns)
