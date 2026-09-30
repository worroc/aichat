"""Rooms, memberships and contacts. Admin only."""

import uuid

from aichat_protocol import models as wire
from aichat_protocol.names import name_problem, normalize

from ..domain import Channel, ChannelKind, History, Membership, forbidden, invalid, not_found
from ..notify import reader_topic
from ..security import Principal
from .context import Ctx, now
from .identity import IdentityService


class RoomService:
    def __init__(self, ctx: Ctx, identities: IdentityService) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.identities = identities

    async def _admin(self, p: Principal) -> None:
        await self.ctx.identity(p)
        if not p.is_admin:
            raise forbidden("admin only")

    async def _live(self, ns: str, name: str) -> str:
        ident = await self.store.identities.get(ns, normalize(name))
        if ident is None or ident.revoked:
            raise not_found(f"agent {name!r}")
        return ident.name

    async def _room(self, ns: str, name: str) -> Channel:
        room = await self.store.channels.get_room(ns, normalize(name))
        if room is None:
            raise not_found("room")
        return room

    async def create_room(self, p: Principal, req: wire.CreateRoomRequest) -> wire.RoomOut:
        await self._admin(p)
        name = normalize(req.name)
        problem = name_problem(name)
        if problem:
            raise invalid(problem)
        members = {p.name} | {await self._live(p.ns, m) for m in req.members}
        room = Channel(
            ns=p.ns, id=str(uuid.uuid4()), kind=ChannelKind.ROOM, name=name,
            history=History(req.history), created_at=now(),
        )
        async with self.store.tx():
            await self.store.channels.add(p.ns, room)
            for member in members:
                await self.store.memberships.add(
                    p.ns, Membership(ns=p.ns, channel_id=room.id, name=member, joined_seq=0)
                )
        await self.ctx.audit(p.ns, p.name, "room.create", name)
        await self.ctx.emit(p.ns, "room_created", {"room": name, "members": sorted(members)})
        return await self.identities.room_out(room)

    async def add_member(self, p: Principal, room_name: str, name: str) -> wire.RoomOut:
        await self._admin(p)
        room = await self._room(p.ns, room_name)
        member = await self._live(p.ns, name)
        joined = await self.store.messages.last_seq(p.ns, room.id)
        await self.store.memberships.add(
            p.ns, Membership(ns=p.ns, channel_id=room.id, name=member, joined_seq=joined)
        )
        await self.ctx.audit(p.ns, p.name, "room.add", f"{room.name}:{member}")
        await self.ctx.emit(p.ns, "member_added", {"room": room.name, "name": member})
        return await self.identities.room_out(room)

    async def remove_member(self, p: Principal, room_name: str, name: str) -> wire.RoomOut:
        await self._admin(p)
        room = await self._room(p.ns, room_name)
        member = normalize(name)
        await self.store.memberships.remove(p.ns, room.id, member)
        await self.ctx.notifier.publish(reader_topic(p.ns, member))
        await self.ctx.audit(p.ns, p.name, "room.remove", f"{room.name}:{member}")
        await self.ctx.emit(p.ns, "member_removed", {"room": room.name, "name": member})
        return await self.identities.room_out(room)

    async def add_contact(self, p: Principal, req: wire.ContactRequest) -> None:
        await self._admin(p)
        a, b = await self._live(p.ns, req.a), await self._live(p.ns, req.b)
        if a == b:
            raise invalid("an agent cannot be its own contact")
        await self.store.contacts.add(p.ns, a, b)
        await self.ctx.audit(p.ns, p.name, "contact.add", f"{a}:{b}")
        await self.ctx.emit(p.ns, "contact_added", {"a": a, "b": b})

    async def remove_contact(self, p: Principal, req: wire.ContactRequest) -> None:
        await self._admin(p)
        a, b = normalize(req.a), normalize(req.b)
        await self.store.contacts.remove(p.ns, a, b)
        for who in (a, b):
            await self.ctx.notifier.publish(reader_topic(p.ns, who))
        await self.ctx.audit(p.ns, p.name, "contact.remove", f"{a}:{b}")
        await self.ctx.emit(p.ns, "contact_removed", {"a": a, "b": b})
