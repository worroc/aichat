"""In-memory store.

Every method runs without awaiting inside, so under asyncio each call is
atomic by itself. ``tx()`` serialises multi-step writes from the service
layer. There is no rollback: the service validates before it writes.
State lives only as long as the process.
"""

from __future__ import annotations

import asyncio
import bisect
import itertools
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from aichat_protocol.errors import ErrorCode

from ..domain import (
    AuditEntry,
    Channel,
    ChannelKind,
    Closed,
    Credential,
    DomainError,
    Event,
    IdempotencyRecord,
    Identity,
    Membership,
    Message,
    MessageFilter,
    Namespace,
    NewMessage,
    not_found,
)


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryNamespaces:
    def __init__(self) -> None:
        self._items: dict[str, Namespace] = {}

    async def add(self, namespace: Namespace) -> None:
        self._items[namespace.id] = namespace

    async def get(self, ns_id: str) -> Namespace | None:
        return self._items.get(ns_id)

    async def list(self) -> list[Namespace]:
        return sorted(self._items.values(), key=lambda n: n.created_at)

    def _drop(self, ns: str) -> None:
        self._items.pop(ns, None)


def _drop_keys(items: dict, ns: str) -> None:
    """Remove entries whose key is a tuple that starts with ``ns``."""
    for key in [k for k in items if k[0] == ns]:
        del items[key]


class MemoryIdentities:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Identity] = {}

    async def add(self, ns: str, identity: Identity) -> None:
        key = (ns, identity.name)
        if key in self._items:
            raise DomainError(ErrorCode.ALREADY_EXISTS, f"name {identity.name!r} is taken")
        self._items[key] = identity

    async def get(self, ns: str, name: str) -> Identity | None:
        return self._items.get((ns, name))

    async def list(self, ns: str) -> list[Identity]:
        return [i for (n, _), i in self._items.items() if n == ns]

    async def replace(self, ns: str, identity: Identity) -> None:
        if (ns, identity.name) not in self._items:
            raise not_found("identity")
        self._items[(ns, identity.name)] = identity

    def _drop(self, ns: str) -> None:
        _drop_keys(self._items, ns)


class MemoryCredentials:
    def __init__(self) -> None:
        self._items: dict[str, Credential] = {}

    async def add(self, ns: str, credential: Credential) -> None:
        self._items[credential.hash] = replace(credential, ns=ns)

    async def resolve(self, secret_hash: str) -> Credential | None:
        return self._items.get(secret_hash)

    async def replace(self, ns: str, credential: Credential) -> None:
        current = self._items.get(credential.hash)
        if current is None or current.ns != ns:
            raise not_found("credential")
        self._items[credential.hash] = credential

    async def list_for(self, ns: str, name: str) -> list[Credential]:
        return [c for c in self._items.values() if c.ns == ns and c.name == name]

    def _drop(self, ns: str) -> None:
        for key in [k for k, c in self._items.items() if c.ns == ns]:
            del self._items[key]


class MemoryContacts:
    def __init__(self) -> None:
        self._edges: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )

    async def add(self, ns: str, a: str, b: str) -> None:
        if a == b:
            return
        self._edges[ns][a].add(b)
        self._edges[ns][b].add(a)

    async def remove(self, ns: str, a: str, b: str) -> None:
        self._edges[ns][a].discard(b)
        self._edges[ns][b].discard(a)

    async def list(self, ns: str, name: str) -> set[str]:
        return set(self._edges[ns].get(name, ()))

    async def are_contacts(self, ns: str, a: str, b: str) -> bool:
        return b in self._edges[ns].get(a, ())

    def _drop(self, ns: str) -> None:
        self._edges.pop(ns, None)


class MemoryChannels:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Channel] = {}
        self._rooms: dict[tuple[str, str], str] = {}

    async def add(self, ns: str, channel: Channel) -> None:
        if (ns, channel.id) in self._items:
            raise DomainError(ErrorCode.ALREADY_EXISTS, "channel exists")
        if channel.kind is ChannelKind.ROOM:
            if (ns, channel.name) in self._rooms:
                raise DomainError(ErrorCode.ALREADY_EXISTS, f"room {channel.name!r} exists")
            self._rooms[(ns, channel.name)] = channel.id
        self._items[(ns, channel.id)] = channel

    async def get(self, ns: str, channel_id: str) -> Channel | None:
        return self._items.get((ns, channel_id))

    async def get_room(self, ns: str, name: str) -> Channel | None:
        channel_id = self._rooms.get((ns, name))
        return None if channel_id is None else self._items.get((ns, channel_id))

    async def list(self, ns: str, kind: ChannelKind | None = None) -> list[Channel]:
        return [
            c
            for (n, _), c in self._items.items()
            if n == ns and (kind is None or c.kind is kind)
        ]

    def _drop(self, ns: str) -> None:
        _drop_keys(self._items, ns)
        _drop_keys(self._rooms, ns)


class MemoryMemberships:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], Membership] = {}

    async def add(self, ns: str, membership: Membership) -> None:
        key = (ns, membership.channel_id, membership.name)
        self._items.setdefault(key, replace(membership, ns=ns))

    async def remove(self, ns: str, channel_id: str, name: str) -> None:
        self._items.pop((ns, channel_id, name), None)

    async def get(self, ns: str, channel_id: str, name: str) -> Membership | None:
        return self._items.get((ns, channel_id, name))

    async def list_for_channel(self, ns: str, channel_id: str) -> list[Membership]:
        return [m for (n, c, _), m in self._items.items() if n == ns and c == channel_id]

    async def list_for_member(self, ns: str, name: str) -> list[Membership]:
        return [m for (n, _, who), m in self._items.items() if n == ns and who == name]

    def _drop(self, ns: str) -> None:
        _drop_keys(self._items, ns)


class MemoryMessages:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Message] = {}
        self._by_channel: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        self._answers: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        self._threads: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        self._usage: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])

    async def add(self, ns: str, new: NewMessage) -> Message:
        return self._insert(ns, new)

    async def add_answer(self, ns: str, new: NewMessage) -> Message:
        question = self._items.get((ns, new.reply_to or ""))
        if question is None:
            raise not_found("question")
        if question.closed_reason is not None:
            raise DomainError(ErrorCode.QUESTION_CLOSED, "question is closed")
        answers = [self._items[(ns, i)] for i in self._answers[(ns, question.id)]]
        if any(a.sender == new.sender for a in answers):
            raise DomainError(ErrorCode.ALREADY_ANSWERED, "you already answered; use amend")
        message = self._insert(ns, new)
        self._answers[(ns, question.id)].append(message.id)
        if question.max_agents_answers and len(answers) + 1 >= question.max_agents_answers:
            self._items[(ns, question.id)] = replace(question, closed_reason=Closed.MAX_ANSWERS)
        return message

    def _insert(self, ns: str, new: NewMessage) -> Message:
        ids = self._by_channel[(ns, new.channel_id)]
        seq = len(ids) + 1
        message_id = str(uuid.uuid4())
        message = Message(
            ns=ns,
            id=message_id,
            channel_id=new.channel_id,
            seq=seq,
            time=_now(),
            sender=new.sender,
            sender_role=new.sender_role,
            kind=new.kind,
            text=new.text,
            reply_to=new.reply_to,
            thread_id=new.thread_id or message_id,
            to=new.to,
            max_agents_answers=new.max_agents_answers,
            expires_at=new.expires_at,
            refs=new.refs,
        )
        self._items[(ns, message_id)] = message
        ids.append(message_id)
        self._threads[(ns, message.thread_id)].append(message_id)
        usage = self._usage[ns]
        usage[0] += 1
        usage[1] += message.size
        return message

    async def replace(self, ns: str, message: Message) -> None:
        old = self._items.get((ns, message.id))
        if old is None:
            raise not_found("message")
        self._usage[ns][1] += message.size - old.size
        self._items[(ns, message.id)] = message

    async def close(self, ns: str, message_id: str, reason: Closed) -> Message:
        message = self._items.get((ns, message_id))
        if message is None:
            raise not_found("message")
        if message.closed_reason is None:
            message = replace(message, closed_reason=reason)
            self._items[(ns, message_id)] = message
        return message

    async def get(self, ns: str, message_id: str) -> Message | None:
        return self._items.get((ns, message_id))

    async def search(self, ns: str, flt: MessageFilter) -> list[Message]:
        candidates = self._candidates(ns, flt)
        matches = [m for m in candidates if _matches(m, flt)]
        if flt.newest:
            return matches[-flt.limit :] if flt.limit else []
        return matches[: flt.limit]

    def _candidates(self, ns: str, flt: MessageFilter) -> list[Message]:
        if flt.channel_id is not None:
            ids = self._by_channel.get((ns, flt.channel_id), [])
            lo = flt.after_seq or 0
            hi = len(ids) if flt.before_seq is None else max(flt.before_seq - 1, 0)
            return [self._items[(ns, i)] for i in ids[lo:hi]]
        if flt.thread_id is not None:
            ids = self._threads.get((ns, flt.thread_id), [])
            return [self._items[(ns, i)] for i in ids]
        found = [m for (n, _), m in self._items.items() if n == ns]
        return sorted(found, key=lambda m: (m.time, m.seq))

    async def answers_to(self, ns: str, question_id: str) -> list[Message]:
        return [self._items[(ns, i)] for i in self._answers.get((ns, question_id), [])]

    async def thread(self, ns: str, thread_id: str) -> list[Message]:
        found = [self._items[(ns, i)] for i in self._threads.get((ns, thread_id), [])]
        return sorted(found, key=lambda m: m.seq)

    async def last_seq(self, ns: str, channel_id: str) -> int:
        return len(self._by_channel.get((ns, channel_id), []))

    async def usage(self, ns: str) -> tuple[int, int]:
        count, size = self._usage.get(ns, [0, 0])
        return count, size

    def _drop(self, ns: str) -> None:
        for items in (self._items, self._by_channel, self._answers, self._threads):
            _drop_keys(items, ns)
        self._usage.pop(ns, None)


def _matches(m: Message, flt: MessageFilter) -> bool:
    checks = (
        flt.sender is None or m.sender == flt.sender,
        flt.exclude_sender is None or m.sender != flt.exclude_sender,
        flt.kind is None or m.kind is flt.kind,
        flt.thread_id is None or m.thread_id == flt.thread_id,
        flt.reply_to is None or m.reply_to == flt.reply_to,
        flt.after_seq is None or flt.channel_id is not None or m.seq > flt.after_seq,
        flt.before_seq is None or flt.channel_id is not None or m.seq < flt.before_seq,
    )
    return all(checks)


class MemoryPointers:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], int] = {}

    async def get(self, ns: str, reader: str, channel_id: str) -> int:
        return self._items.get((ns, reader, channel_id), 0)

    async def advance(self, ns: str, reader: str, channel_id: str, seq: int) -> int:
        key = (ns, reader, channel_id)
        value = max(self._items.get(key, 0), seq)
        self._items[key] = value
        return value

    async def list(self, ns: str) -> list[tuple[str, str, int]]:
        return [(r, c, s) for (n, r, c), s in self._items.items() if n == ns]

    def _drop(self, ns: str) -> None:
        _drop_keys(self._items, ns)


class MemoryIdempotency:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], IdempotencyRecord] = {}

    async def get(self, ns: str, actor: str, key: str) -> IdempotencyRecord | None:
        record = self._items.get((ns, actor, key))
        if record is not None and record.expires_at <= _now():
            return None
        return record

    async def put(self, ns: str, record: IdempotencyRecord) -> None:
        self._items[(ns, record.actor, record.key)] = record

    async def purge_expired(self, now: datetime) -> int:
        expired = {k for k, r in self._items.items() if r.expires_at <= now}
        for key in expired:
            del self._items[key]
        return len(expired)

    def _drop(self, ns: str) -> None:
        _drop_keys(self._items, ns)


class MemoryAudit:
    def __init__(self) -> None:
        self._items: defaultdict[str, list[AuditEntry]] = defaultdict(list)

    async def add(self, ns: str, entry: AuditEntry) -> None:
        self._items[ns].append(entry)

    async def list(self, ns: str, limit: int = 200) -> list[AuditEntry]:
        return self._items.get(ns, [])[-limit:]

    def _drop(self, ns: str) -> None:
        self._items.pop(ns, None)


class MemoryEvents:
    def __init__(self) -> None:
        self._items: list[Event] = []
        self._seqs: list[int] = []
        self._counter = itertools.count(1)

    async def add(self, ns: str | None, type_: str, data: dict[str, Any]) -> Event:
        event = Event(seq=next(self._counter), time=_now(), ns=ns, type=type_, data=data)
        self._items.append(event)
        self._seqs.append(event.seq)
        return event

    async def list(self, ns: str, after_seq: int, limit: int = 200) -> list[Event]:
        start = bisect.bisect_right(self._seqs, after_seq)
        return [e for e in self._items[start:] if e.ns == ns][:limit]

    async def list_all(self, after_seq: int, limit: int = 200) -> list[Event]:
        start = bisect.bisect_right(self._seqs, after_seq)
        return self._items[start : start + limit]

    def _drop(self, ns: str) -> None:
        self._items = [e for e in self._items if e.ns != ns]
        self._seqs = [e.seq for e in self._items]


class MemoryStore:
    def __init__(self) -> None:
        self.namespaces = MemoryNamespaces()
        self.identities = MemoryIdentities()
        self.credentials = MemoryCredentials()
        self.contacts = MemoryContacts()
        self.channels = MemoryChannels()
        self.memberships = MemoryMemberships()
        self.messages = MemoryMessages()
        self.pointers = MemoryPointers()
        self.idempotency = MemoryIdempotency()
        self.audit = MemoryAudit()
        self.events = MemoryEvents()
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def delete_namespace(self, ns: str) -> None:
        repos = (
            self.identities, self.credentials, self.contacts, self.channels, self.memberships,
            self.messages, self.pointers, self.idempotency, self.audit, self.events,
            self.namespaces,
        )
        for repo in repos:
            repo._drop(ns)

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[None]:
        async with self._lock:
            yield
