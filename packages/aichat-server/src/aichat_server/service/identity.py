"""Namespaces, identities, join codes, revocation, presence and directory."""

import uuid
from dataclasses import replace
from datetime import timedelta

from aichat_protocol import models as wire
from aichat_protocol.errors import ErrorCode
from aichat_protocol.names import confusable_with, name_problem, normalize

from ..domain import (
    Channel,
    ChannelKind,
    CredKind,
    Credential,
    DomainError,
    Identity,
    Kind,
    Membership,
    Namespace,
    Role,
    State,
    forbidden,
    invalid,
    not_found,
    unauthorized,
)
from ..notify import reader_topic
from ..security import (
    HUMAN_PREFIX,
    JOIN_PREFIX,
    TOKEN_PREFIX,
    Principal,
    hash_secret,
    new_secret,
)
from .access import Access
from .context import Ctx, now
from .present import identity_out

TAG_PREFIX = "tag:"


class IdentityService:
    def __init__(self, ctx: Ctx, access: Access) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.access = access

    # --- authentication -------------------------------------------------------

    async def authenticate(self, token: str) -> Principal:
        cred = await self.store.credentials.resolve(hash_secret(token))
        if cred is None or cred.kind is not CredKind.TOKEN or cred.revoked:
            raise unauthorized()
        ident = await self.store.identities.get(cred.ns, cred.name)
        if ident is None or ident.revoked:
            raise unauthorized()
        await self._touch(ident)
        return Principal(ns=ident.ns, name=ident.name, role=ident.role, kind=ident.kind)

    async def _touch(self, ident: Identity) -> None:
        stamp = now()
        interval = timedelta(seconds=self.ctx.settings.last_seen_interval)
        if ident.last_seen is None or stamp - ident.last_seen >= interval:
            await self.store.identities.replace(ident.ns, replace(ident, last_seen=stamp))

    # --- namespaces -----------------------------------------------------------

    async def create_namespace(
        self, req: wire.CreateNamespaceRequest
    ) -> wire.CreateNamespaceResponse:
        admin_name = self._checked_name(req.admin_name, set())
        ns = Namespace(id=str(uuid.uuid4()), created_at=now(), title=req.title.strip())
        admin = self._new_identity(
            ns.id, admin_name, Role.ADMIN, Kind.PROCESS, None, req.provider, req.description
        )
        code = new_secret(JOIN_PREFIX)
        async with self.store.tx():
            await self.store.namespaces.add(ns)
            await self.store.identities.add(ns.id, admin)
            await self.store.credentials.add(ns.id, self._join_cred(ns.id, admin_name, code))
        await self.ctx.audit(ns.id, "operator", "namespace.create", admin_name)
        await self.ctx.emit(ns.id, "namespace_created", {"admin": admin_name, "title": ns.title})
        return wire.CreateNamespaceResponse(
            ns_id=ns.id, title=ns.title, admin_name=admin_name, join_code=code
        )

    # --- agents -----------------------------------------------------------------

    async def create_agent(
        self, p: Principal, req: wire.CreateAgentRequest
    ) -> wire.CreateAgentResponse:
        creator = await self.ctx.identity(p)
        kind = self._check_depth(creator, Kind(req.kind))
        identities = await self.store.identities.list(p.ns)
        name = self._pick_name(req, {i.name for i in identities})
        contacts = await self._resolve_contacts(p, req.contacts, identities)
        rooms = await self._resolve_rooms(p, req.rooms)
        ident = self._new_identity(
            p.ns, name, Role.AGENT, kind, creator.name, req.provider, req.description,
            tags=frozenset(normalize(t) for t in req.tags),
            session_ref=req.session_ref,
            resume_template=req.resume_template,
        )
        code = new_secret(JOIN_PREFIX)
        async with self.store.tx():
            await self.store.identities.add(p.ns, ident)
            await self.store.credentials.add(p.ns, self._join_cred(p.ns, name, code))
            await self._link(p.ns, name, {creator.name} | contacts, rooms)
        await self.ctx.audit(p.ns, p.name, "agent.create", name, detail=kind.value)
        await self.ctx.emit(p.ns, "agent_created", {"name": name, "parent": creator.name})
        return wire.CreateAgentResponse(
            name=name,
            join_code=code,
            kind=kind.value,
            parent=creator.name,
            contacts=sorted({creator.name} | contacts),
            rooms=sorted(r.name for r in rooms),
            tags=sorted(ident.tags),
        )

    def _check_depth(self, creator: Identity, wanted: Kind) -> Kind:
        if creator.kind is Kind.SUBAGENT:
            raise DomainError(ErrorCode.DEPTH_EXCEEDED, "subagents cannot create agents")
        if creator.role is Role.HUMAN:
            raise forbidden("humans cannot create agents")
        if wanted is Kind.PROCESS and creator.role is not Role.ADMIN:
            raise forbidden("only the admin creates processes; create a subagent instead")
        return wanted

    def _pick_name(self, req: wire.CreateAgentRequest, existing: set[str]) -> str:
        if req.name:
            return self._checked_name(req.name, existing)
        if not req.name_prefix:
            raise invalid("name or name_prefix is required")
        prefix = normalize(req.name_prefix)
        for number in range(1, 10_000):
            candidate = f"{prefix}-{number}"
            if candidate in existing or confusable_with(candidate, existing):
                continue
            return self._checked_name(candidate, existing)
        raise invalid("no free name for this prefix")

    def _checked_name(self, raw: str, existing: set[str], *, human: bool = False) -> str:
        name = normalize(raw)
        problem = name_problem(name, human=human)
        if problem:
            raise invalid(problem)
        if name in existing:
            raise DomainError(ErrorCode.ALREADY_EXISTS, f"name {name!r} is taken")
        similar = confusable_with(name, existing)
        if similar:
            raise invalid(f"name {name!r} is too similar to {similar!r}")
        return name

    async def _resolve_contacts(
        self, p: Principal, requested: list[str], identities: list[Identity]
    ) -> set[str]:
        live = {i.name: i for i in identities if not i.revoked}
        visible = await self.access.visible_names(p)
        allowed = set(live) if p.is_admin else visible & set(live)
        result: set[str] = set()
        for item in requested:
            result |= self._expand_contact(normalize(item), live, allowed)
        return result

    def _expand_contact(self, item: str, live: dict[str, Identity], allowed: set[str]) -> set[str]:
        if item.startswith(TAG_PREFIX):
            tag = item.removeprefix(TAG_PREFIX)
            return {n for n in allowed if tag in live[n].tags}
        if item not in allowed:
            raise not_found(f"contact {item!r}")
        return {item}

    async def _resolve_rooms(self, p: Principal, names: list[str]) -> list[Channel]:
        rooms: list[Channel] = []
        for raw in names:
            room = await self.store.channels.get_room(p.ns, normalize(raw))
            member = room and await self.store.memberships.get(p.ns, room.id, p.name)
            if room is None or (not p.is_admin and member is None):
                raise not_found(f"room {raw!r}")
            rooms.append(room)
        return rooms

    async def _link(self, ns: str, name: str, contacts: set[str], rooms: list[Channel]) -> None:
        for contact in contacts:
            await self.store.contacts.add(ns, name, contact)
        for room in rooms:
            joined = await self.store.messages.last_seq(ns, room.id)
            await self.store.memberships.add(
                ns, Membership(ns=ns, channel_id=room.id, name=name, joined_seq=joined)
            )

    def _new_identity(
        self,
        ns: str,
        name: str,
        role: Role,
        kind: Kind,
        parent: str | None,
        provider: str,
        description: str,
        *,
        tags: frozenset[str] = frozenset(),
        session_ref: str | None = None,
        resume_template: str | None = None,
        state: State = State.PENDING,
    ) -> Identity:
        return Identity(
            ns=ns,
            uid=str(uuid.uuid4()),
            name=name,
            role=role,
            kind=kind,
            parent=parent,
            depth=1 if kind is Kind.SUBAGENT else 0,
            state=state,
            tags=tags,
            provider=provider,
            description=description,
            session_ref=session_ref,
            resume_template=resume_template,
            created_at=now(),
        )

    def _join_cred(self, ns: str, name: str, code: str) -> Credential:
        return Credential(
            hash=hash_secret(code),
            ns=ns,
            name=name,
            kind=CredKind.JOIN,
            created_at=now(),
            expires_at=now() + timedelta(seconds=self.ctx.settings.join_ttl),
        )

    # --- join -------------------------------------------------------------------

    async def join(self, code: str) -> wire.JoinResponse:
        async with self.store.tx():
            cred = await self.store.credentials.resolve(hash_secret(code))
            if cred is None or cred.kind is not CredKind.JOIN:
                raise unauthorized("invalid join code")
            if cred.used_at is not None:
                await self._report_reuse(cred)
                raise unauthorized("join code already used")
            ident = await self.store.identities.get(cred.ns, cred.name)
            expired = cred.expires_at is not None and cred.expires_at <= now()
            if cred.revoked or expired or ident is None or ident.revoked:
                raise unauthorized("join code expired or revoked")
            token = new_secret(TOKEN_PREFIX)
            await self._consume_join(cred, ident, token)
        await self.ctx.audit(ident.ns, ident.name, "agent.join")
        await self.ctx.emit(ident.ns, "agent_joined", {"name": ident.name})
        return wire.JoinResponse(
            name=ident.name, token=token, role=ident.role.value, kind=ident.kind.value
        )

    async def _consume_join(self, cred: Credential, ident: Identity, token: str) -> None:
        await self.store.credentials.replace(cred.ns, replace(cred, used_at=now()))
        await self._revoke_tokens(ident.ns, ident.name)
        await self.store.credentials.add(ident.ns, self._token_cred(ident.ns, ident.name, token))
        state = State.ACTIVE if ident.state in {State.PENDING, State.PARKED} else ident.state
        await self.store.identities.replace(ident.ns, replace(ident, state=state, last_seen=now()))

    def _token_cred(self, ns: str, name: str, token: str) -> Credential:
        return Credential(
            hash=hash_secret(token), ns=ns, name=name, kind=CredKind.TOKEN,
            created_at=now(), expires_at=None,
        )

    async def _report_reuse(self, cred: Credential) -> None:
        await self.ctx.audit(cred.ns, "unknown", "join.reused", cred.name)
        await self.ctx.emit(cred.ns, "alert", {"alert": "join_code_reused", "name": cred.name})

    async def _revoke_tokens(self, ns: str, name: str) -> None:
        for cred in await self.store.credentials.list_for(ns, name):
            if cred.kind is CredKind.TOKEN and not cred.revoked:
                await self.store.credentials.replace(ns, replace(cred, revoked=True))

    # --- rejoin / revoke ----------------------------------------------------------

    async def rejoin(self, p: Principal, name: str) -> wire.RejoinResponse:
        target = await self._managed_target(p, name)
        code = new_secret(JOIN_PREFIX)
        await self.store.credentials.add(p.ns, self._join_cred(p.ns, target.name, code))
        await self.ctx.audit(p.ns, p.name, "agent.rejoin", target.name)
        return wire.RejoinResponse(name=target.name, join_code=code)

    async def revoke(self, p: Principal, name: str) -> list[str]:
        target = await self._managed_target(p, name)
        if target.name == p.name:
            raise forbidden("cannot revoke yourself")
        victims = await self._descendants(p.ns, target)
        async with self.store.tx():
            for ident in victims:
                await self._revoke_one(ident)
        names = [v.name for v in victims]
        for victim in names:
            await self.ctx.notifier.publish(reader_topic(p.ns, victim))
        await self.ctx.audit(p.ns, p.name, "agent.revoke", ",".join(names))
        await self.ctx.emit(p.ns, "agent_revoked", {"names": names})
        return names

    async def _managed_target(self, p: Principal, name: str) -> Identity:
        target = await self.store.identities.get(p.ns, normalize(name))
        visible = await self.access.visible_names(p) | {p.name}
        if target is None or target.name not in visible or target.revoked:
            raise not_found("agent")
        if not p.is_admin and target.parent != p.name:
            raise forbidden("you can manage only your own children")
        return target

    async def _descendants(self, ns: str, root: Identity) -> list[Identity]:
        everyone = await self.store.identities.list(ns)
        found = [root]
        frontier = {root.name}
        while frontier:
            children = [i for i in everyone if i.parent in frontier and not i.revoked]
            found.extend(children)
            frontier = {c.name for c in children}
        return found

    async def _revoke_one(self, ident: Identity) -> None:
        await self.store.identities.replace(ident.ns, replace(ident, state=State.REVOKED))
        for cred in await self.store.credentials.list_for(ident.ns, ident.name):
            if not cred.revoked:
                await self.store.credentials.replace(ident.ns, replace(cred, revoked=True))

    # --- humans ---------------------------------------------------------------------

    async def create_human(self, ns_id: str, name: str) -> wire.HumanResponse:
        if await self.store.namespaces.get(ns_id) is None:
            raise not_found("namespace")
        ident = await self._human_identity(ns_id, normalize(name))
        token = new_secret(HUMAN_PREFIX)
        await self.store.credentials.add(ns_id, self._token_cred(ns_id, ident.name, token))
        await self.ctx.audit(ns_id, "operator", "human.token", ident.name)
        return wire.HumanResponse(name=ident.name, token=token)

    async def _human_identity(self, ns: str, name: str) -> Identity:
        current = await self.store.identities.get(ns, name)
        if current is not None:
            if current.role is not Role.HUMAN or current.revoked:
                raise DomainError(ErrorCode.ALREADY_EXISTS, f"name {name!r} is taken")
            return current
        existing = {i.name for i in await self.store.identities.list(ns)}
        checked = self._checked_name(name, existing, human=True)
        ident = self._new_identity(
            ns, checked, Role.HUMAN, Kind.HUMAN, None, "human", "human operator",
            state=State.ACTIVE,
        )
        await self.store.identities.add(ns, ident)
        return ident

    # --- presence / directory ---------------------------------------------------------

    async def set_state(self, p: Principal, req: wire.StateRequest) -> wire.IdentityOut:
        ident = await self.ctx.identity(p)
        updated = replace(
            ident,
            state=State(req.state),
            session_ref=req.session_ref if req.session_ref is not None else ident.session_ref,
            resume_template=(
                req.resume_template if req.resume_template is not None else ident.resume_template
            ),
            last_seen=now(),
        )
        await self.store.identities.replace(p.ns, updated)
        await self.ctx.emit(p.ns, "presence", {"name": p.name, "state": req.state})
        return identity_out(updated)

    async def me(self, p: Principal) -> wire.MeResponse:
        ident = await self.ctx.identity(p)
        identities = {i.name: i for i in await self.store.identities.list(p.ns)}
        contacts = await self.store.contacts.list(p.ns, p.name)
        rooms = await self._rooms_of(p)
        children = sorted(n for n, i in identities.items() if i.parent == p.name and not i.revoked)
        return wire.MeResponse(
            name=ident.name,
            role=ident.role.value,
            kind=ident.kind.value,
            parent=ident.parent,
            depth=ident.depth,
            state=ident.state.value,
            tags=sorted(ident.tags),
            provider=ident.provider,
            contacts=[identity_out(identities[c]) for c in sorted(contacts) if c in identities],
            rooms=rooms,
            children=children,
        )

    async def _rooms_of(self, p: Principal) -> list[wire.RoomOut]:
        rooms: list[wire.RoomOut] = []
        for membership in await self.store.memberships.list_for_member(p.ns, p.name):
            channel = await self.store.channels.get(p.ns, membership.channel_id)
            if channel is None or channel.kind is not ChannelKind.ROOM:
                continue
            rooms.append(await self.room_out(channel))
        return sorted(rooms, key=lambda r: r.name)

    async def room_out(self, channel: Channel) -> wire.RoomOut:
        members = await self.store.memberships.list_for_channel(channel.ns, channel.id)
        return wire.RoomOut(
            name=channel.name,
            members=sorted(m.name for m in members),
            history=channel.history.value,
        )

    async def directory(
        self, p: Principal, tag: str | None, provider: str | None, state: str | None
    ) -> wire.DirectoryResponse:
        await self.ctx.identity(p)
        visible = await self.access.visible_names(p)
        found = []
        for ident in await self.store.identities.list(p.ns):
            if ident.name not in visible:
                continue
            if tag and tag not in ident.tags:
                continue
            if provider and ident.provider != provider:
                continue
            if state and ident.state.value != state:
                continue
            found.append(identity_out(ident))
        return wire.DirectoryResponse(identities=sorted(found, key=lambda i: i.name))
