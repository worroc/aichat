"""The default ``common`` room of every namespace.

The hub creates it with the namespace and adds every agent when it is
created, at any depth. It is a normal room: its members see each other.
Every call is idempotent, so a backfill or a repeated add is safe.
"""

import uuid

from ..domain import Channel, ChannelKind, History, Membership
from .context import Ctx, now

COMMON_ROOM = "common"


class CommonRoom:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.store = ctx.store

    async def ensure(self, ns: str) -> tuple[Channel, bool]:
        """The room, and True when this call created it.

        Call it before the other writes of a request: a race with another
        request that creates the room fails here, before anything is written.
        """
        room = await self.store.channels.get_room(ns, COMMON_ROOM)
        if room is not None:
            return room, False
        room = Channel(
            ns=ns, id=str(uuid.uuid4()), kind=ChannelKind.ROOM, name=COMMON_ROOM,
            history=History.SINCE_JOIN, created_at=now(),
        )
        await self.store.channels.add(ns, room)
        return room, True

    async def add(self, room: Channel, names: set[str]) -> set[str]:
        """Add members that are not in the room yet. Returns the names added."""
        ns = room.ns
        joined = await self.store.messages.last_seq(ns, room.id)
        added: set[str] = set()
        for name in names:
            if await self.store.memberships.get(ns, room.id, name) is not None:
                continue
            membership = Membership(ns=ns, channel_id=room.id, name=name, joined_seq=joined)
            await self.store.memberships.add(ns, membership)
            added.add(name)
        return added

    async def announce(self, ns: str, created: bool, added: set[str]) -> None:
        """Events for the UI. Call after the writes are done."""
        if created:
            await self.ctx.emit(ns, "room_created", {"room": COMMON_ROOM, "members": sorted(added)})
            return
        for name in sorted(added):
            await self.ctx.emit(ns, "member_added", {"room": COMMON_ROOM, "name": name})
