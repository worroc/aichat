"""Visibility rules: who can see and use which channel, message and name.

Anything the caller may not see raises ``not_found`` - the same error as
for things that do not exist - so names and ids cannot be probed.
"""

from ..domain import (
    Channel,
    ChannelKind,
    History,
    Identity,
    Membership,
    Message,
    Role,
    not_found,
)
from ..security import Principal
from .context import Ctx, now

DM_PREFIX = "dm:"


def direct_id(a: str, b: str) -> str:
    return DM_PREFIX + "|".join(sorted((a, b)))


def label(channel: Channel, viewer: str | None) -> str:
    if channel.kind is ChannelKind.ROOM:
        return f"#{channel.name}"
    if viewer is None:
        return channel.name
    peers = [n for n in channel.name.split("|") if n != viewer]
    return f"@{peers[0] if peers else viewer}"


def seq_floor(channel: Channel, membership: Membership) -> int:
    """Messages with seq <= floor are hidden from this member."""
    return membership.joined_seq if channel.history is History.SINCE_JOIN else 0


class Access:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.store = ctx.store

    # --- rooms -------------------------------------------------------------

    async def room(
        self, p: Principal, name: str, *, join_human: bool = False
    ) -> tuple[Channel, Membership]:
        channel = await self.store.channels.get_room(p.ns, name)
        if channel is None:
            raise not_found("room")
        membership = await self.readable(p, channel)
        if membership is None:
            raise not_found("room")
        if join_human and p.is_human:
            await self.store.memberships.add(p.ns, membership)
        return channel, membership

    async def readable(self, p: Principal, channel: Channel) -> Membership | None:
        membership = await self.store.memberships.get(p.ns, channel.id, p.name)
        if membership is None and p.is_human and channel.kind is ChannelKind.ROOM:
            return Membership(ns=p.ns, channel_id=channel.id, name=p.name, joined_seq=0)
        return membership

    # --- direct messages -----------------------------------------------------

    async def peer(self, p: Principal, name: str) -> Identity:
        ident = await self.store.identities.get(p.ns, name)
        if ident is None or name == p.name:
            raise not_found("agent")
        return ident

    async def can_dm(self, p: Principal, peer: Identity, channel_exists: bool) -> bool:
        if p.sees_everyone:
            return True
        if await self.store.contacts.are_contacts(p.ns, p.name, peer.name):
            return True
        return channel_exists and peer.role in {Role.ADMIN, Role.HUMAN}

    async def direct_for_post(self, p: Principal, name: str) -> tuple[Channel, Identity]:
        peer = await self.peer(p, name)
        channel = await self.store.channels.get(p.ns, direct_id(p.name, name))
        if peer.revoked or not await self.can_dm(p, peer, channel is not None):
            raise not_found("agent")
        if channel is None:
            channel = await self._open_direct(p.ns, p.name, name)
        return channel, peer

    async def direct_for_read(self, p: Principal, name: str) -> tuple[Channel, Membership]:
        channel = await self.store.channels.get(p.ns, direct_id(p.name, name))
        membership = None if channel is None else await self.readable(p, channel)
        if channel is None or membership is None:
            raise not_found("agent")
        return channel, membership

    async def _open_direct(self, ns: str, a: str, b: str) -> Channel:
        channel = Channel(
            ns=ns,
            id=direct_id(a, b),
            kind=ChannelKind.DIRECT,
            name="|".join(sorted((a, b))),
            history=History.FULL,
            created_at=now(),
        )
        await self.store.channels.add(ns, channel)
        for who in (a, b):
            await self.store.memberships.add(
                ns, Membership(ns=ns, channel_id=channel.id, name=who, joined_seq=0)
            )
        return channel

    # --- channels by reference --------------------------------------------------

    async def channel_for_read(
        self, p: Principal, room: str | None, dm: str | None
    ) -> tuple[Channel, Membership]:
        if room is not None:
            return await self.room(p, room)
        if dm is not None:
            return await self.direct_for_read(p, dm)
        raise not_found("channel")

    # --- messages -------------------------------------------------------------

    async def message(self, p: Principal, message_id: str) -> tuple[Message, Channel, Membership]:
        message = await self.store.messages.get(p.ns, message_id)
        channel = None if message is None else await self.store.channels.get(p.ns, message.channel_id)
        membership = None if channel is None else await self.readable(p, channel)
        if message is None or channel is None or membership is None:
            raise not_found("message")
        if message.seq <= seq_floor(channel, membership):
            raise not_found("message")
        return message, channel, membership

    # --- directory ------------------------------------------------------------

    async def visible_names(self, p: Principal) -> set[str]:
        identities = await self.store.identities.list(p.ns)
        if p.sees_everyone:
            return {i.name for i in identities} - {p.name}
        names = await self.store.contacts.list(p.ns, p.name)
        names |= await self._room_peers(p)
        me = next((i for i in identities if i.name == p.name), None)
        names |= {i.name for i in identities if i.parent == p.name}
        if me is not None and me.parent:
            names.add(me.parent)
        names.discard(p.name)
        return names

    async def _room_peers(self, p: Principal) -> set[str]:
        peers: set[str] = set()
        for membership in await self.store.memberships.list_for_member(p.ns, p.name):
            channel = await self.store.channels.get(p.ns, membership.channel_id)
            if channel is None or channel.kind is not ChannelKind.ROOM:
                continue
            members = await self.store.memberships.list_for_channel(p.ns, channel.id)
            peers |= {m.name for m in members}
        return peers
