"""Shared service context and small helpers used by every service module."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..domain import AuditEntry, Identity, unauthorized
from ..notify import OPS_TOPIC, Notifier, events_topic
from ..security import Principal
from ..store.base import Store
from .waits import WaitRegistry


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Ctx:
    store: Store
    notifier: Notifier
    settings: Settings
    waits: WaitRegistry = field(default_factory=WaitRegistry)

    async def identity(self, p: Principal) -> Identity:
        """Reload the caller; revoked or deleted callers are rejected."""
        ident = await self.store.identities.get(p.ns, p.name)
        if ident is None or ident.revoked:
            raise unauthorized()
        return ident

    async def audit(
        self,
        ns: str,
        actor: str,
        action: str,
        target: str = "",
        msg_id: str | None = None,
        detail: str = "",
    ) -> None:
        entry = AuditEntry(
            ts=now(), ns=ns, actor=actor, action=action, target=target, msg_id=msg_id, detail=detail
        )
        await self.store.audit.add(ns, entry)

    async def emit(self, ns: str | None, type_: str, data: dict[str, Any]) -> None:
        await self.store.events.add(ns, type_, data)
        await self.notifier.publish(OPS_TOPIC)
        if ns is not None:
            await self.notifier.publish(events_topic(ns))
