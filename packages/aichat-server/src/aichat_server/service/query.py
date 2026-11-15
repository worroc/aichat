"""Query API: list rooms with members, and agents with their rooms.

Agents see only what ``Access`` lets them see: the rooms they may read and
the names they may see. The operator variants (``ops_*``) see everything.
"""

from dataclasses import dataclass
from datetime import datetime

from aichat_protocol import models as wire
from aichat_protocol.names import normalize

from ..domain import Channel, ChannelKind, Identity, MessageFilter, not_found
from ..security import Principal
from .access import Access
from .context import Ctx
from .present import agent_out, ops_identity_out, room_info_out


@dataclass(frozen=True, slots=True)
class AgentFilter:
    tag: str | None = None
    state: str | None = None
    parent: str | None = None
    provider: str | None = None

    def match(self, ident: Identity) -> bool:
        if self.tag and normalize(self.tag) not in ident.tags:
            return False
        if self.state and ident.state.value != self.state:
            return False
        if self.parent and ident.parent != normalize(self.parent):
            return False
        return not (self.provider and ident.provider != self.provider)


class QueryService:
    def __init__(self, ctx: Ctx, access: Access) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.access = access

    # --- agents' view -----------------------------------------------------------

    async def rooms(self, p: Principal) -> wire.RoomsResponse:
        await self.ctx.identity(p)
        rooms = await self._readable_rooms(p)
        return wire.RoomsResponse(rooms=[await self._room_info(r) for r in rooms])

    async def room(self, p: Principal, name: str) -> wire.RoomInfoOut:
        await self.ctx.identity(p)
        channel, _ = await self.access.room(p, normalize(name))
        return await self._room_info(channel)

    async def agents(self, p: Principal, flt: AgentFilter) -> wire.AgentsResponse:
        await self.ctx.identity(p)
        visible = await self.access.visible_names(p)
        shown = visible | {p.name}
        readable = {r.id: r.name for r in await self._readable_rooms(p)}
        found: list[wire.AgentOut] = []
        for ident in await self.store.identities.list(p.ns):
            if ident.name not in shown or not flt.match(ident):
                continue
            rooms = await self._rooms_of(p.ns, ident.name, readable)
            with_launch = ident.name == p.name or p.is_admin
            found.append(agent_out(ident, visible, rooms, with_launch))
        return wire.AgentsResponse(agents=sorted(found, key=lambda a: a.name))

    async def _readable_rooms(self, p: Principal) -> list[Channel]:
        rooms: list[Channel] = []
        for channel in await self.store.channels.list(p.ns, ChannelKind.ROOM):
            if await self.access.readable(p, channel) is not None:
                rooms.append(channel)
        return sorted(rooms, key=lambda r: r.name)

    # --- operator's view ------------------------------------------------------------

    async def ops_rooms(self, ns_id: str | None) -> list[wire.OpsRoomOut]:
        out: list[wire.OpsRoomOut] = []
        for ns in await self._namespaces(ns_id):
            for channel in await self.store.channels.list(ns, ChannelKind.ROOM):
                info = await self._room_info(channel)
                out.append(wire.OpsRoomOut(**info.model_dump(), ns=ns))
        return sorted(out, key=lambda r: (r.ns, r.name))

    async def ops_agents(self, ns_id: str | None, flt: AgentFilter) -> list[wire.OpsAgentOut]:
        out: list[wire.OpsAgentOut] = []
        for ns in await self._namespaces(ns_id):
            rooms = {r.id: r.name for r in await self.store.channels.list(ns, ChannelKind.ROOM)}
            for ident in await self.store.identities.list(ns):
                if not flt.match(ident):
                    continue
                base = ops_identity_out(ident).model_dump()
                names = sorted(await self._rooms_of(ns, ident.name, rooms))
                out.append(wire.OpsAgentOut(**base, ns=ns, rooms=names))
        return sorted(out, key=lambda a: (a.ns, a.name))

    async def _namespaces(self, ns_id: str | None) -> list[str]:
        if ns_id is None:
            return [n.id for n in await self.store.namespaces.list()]
        if await self.store.namespaces.get(ns_id) is None:
            raise not_found("namespace")
        return [ns_id]

    # --- shared -------------------------------------------------------------------------

    async def _rooms_of(self, ns: str, name: str, rooms: dict[str, str]) -> list[str]:
        """Names of the rooms (from ``rooms``: id -> name) the agent is a member of."""
        memberships = await self.store.memberships.list_for_member(ns, name)
        return [rooms[m.channel_id] for m in memberships if m.channel_id in rooms]

    async def _room_info(self, channel: Channel) -> wire.RoomInfoOut:
        memberships = await self.store.memberships.list_for_channel(channel.ns, channel.id)
        members: list[Identity] = []
        for membership in memberships:
            ident = await self.store.identities.get(channel.ns, membership.name)
            if ident is not None:
                members.append(ident)
        return room_info_out(channel, members, await self._last_message_at(channel))

    async def _last_message_at(self, channel: Channel) -> datetime | None:
        flt = MessageFilter(channel_id=channel.id, limit=1, newest=True)
        found = await self.store.messages.search(channel.ns, flt)
        return found[-1].time if found else None
