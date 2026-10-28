"""Agent lifetime: experts stay reachable while agents that depend on them work.

An agent names its experts at creation (``depends_on``). An expert asking for
``done`` is parked instead while any of its dependents is still open, so a late
question can still wake it. When the last dependent closes, the hub closes the
expert itself, and that may in turn release the experts it depends on.

The hub closes only agents that asked for ``done`` once (``done_requested``).
An agent parked while it waits for an answer is never closed under it.
"""

from dataclasses import replace

from aichat_protocol.errors import ErrorCode

from ..domain import DomainError, Identity, State
from .context import Ctx, now
from .reading import ReadingService

RELEASE_KIND = "release"


class Lifetime:
    def __init__(self, ctx: Ctx, reading: ReadingService) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.reading = reading

    async def open_dependents(self, ns: str, name: str) -> list[str]:
        everyone = await self.store.identities.list(ns)
        return sorted(i.name for i in everyone if name in i.depends_on and not i.closed)

    async def done_state(self, ident: Identity) -> tuple[State, list[str]]:
        """The state a ``done`` request really gets, and who holds the agent open."""
        unread = await self.reading.unread_count(ident.ns, ident.name)
        if unread:
            raise DomainError(
                ErrorCode.UNREAD_PENDING,
                f"{unread} unread message(s): read them (inbox --ack) and handle them first",
            )
        held_by = await self.open_dependents(ident.ns, ident.name)
        return (State.PARKED if held_by else State.DONE), held_by

    async def after_state(self, ident: Identity) -> None:
        """Run the lifetime rules after an agent stored its own new state."""
        if ident.state is State.DONE:
            await self.release(ident)
        elif ident.state is State.PARKED:
            # Re-checked after the write: a dependent may have closed meanwhile.
            await self._settle(ident)

    async def release(self, closed: Identity) -> None:
        """``closed`` is finished: close each expert nobody needs any more."""
        frontier = [closed]
        while frontier:
            current = frontier.pop()
            for name in sorted(current.depends_on):
                expert = await self.store.identities.get(current.ns, name)
                if expert is not None and await self._settle(expert):
                    frontier.append(expert)

    async def reopen(self, ns: str, experts: set[str]) -> None:
        """A new dependent may ask these experts: closed-by-done ones are parked again,
        and so are their own done experts, down the chain."""
        frontier = sorted(experts)
        seen: set[str] = set()
        while frontier:
            name = frontier.pop()
            expert = await self.store.identities.get(ns, name)
            if name in seen or expert is None or expert.state is not State.DONE:
                continue
            seen.add(name)
            await self._set(expert, State.PARKED)
            frontier.extend(sorted(expert.depends_on))

    async def _settle(self, expert: Identity) -> bool:
        """Close a parked agent that finished its work and that nobody needs.
        With unread mail it is woken to read first. Return True when closed."""
        if expert.state is not State.PARKED or not expert.done_requested:
            return False
        if await self.open_dependents(expert.ns, expert.name):
            return False
        unread = await self.reading.unread_count(expert.ns, expert.name)
        if unread:
            await self._emit_wake(expert, unread)
            return False
        await self._set(expert, State.DONE)
        return True

    async def _set(self, ident: Identity, state: State) -> None:
        await self.store.identities.replace(ident.ns, replace(ident, state=state, last_seen=now()))
        await self.ctx.emit(ident.ns, "presence", {"name": ident.name, "state": state.value})
        await self.ctx.audit(ident.ns, "hub", f"agent.{state.value}", ident.name, detail="lifetime")

    async def _emit_wake(self, ident: Identity, unread: int) -> None:
        await self.ctx.emit(
            ident.ns,
            "wake_needed",
            {
                "name": ident.name,
                "parent": ident.parent,
                "provider": ident.provider,
                "session_ref": ident.session_ref,
                "resume_template": ident.resume_template,
                "message_id": None,
                "from": "hub",
                "kind": RELEASE_KIND,
                "unread": unread,
            },
        )
