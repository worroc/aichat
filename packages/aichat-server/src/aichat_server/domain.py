"""Domain objects. Frozen dataclasses, independent of the HTTP schemas."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from aichat_protocol.errors import ErrorCode


class Role(StrEnum):
    ADMIN = "admin"
    AGENT = "agent"
    HUMAN = "human"


class Kind(StrEnum):
    PROCESS = "process"
    SUBAGENT = "subagent"
    HUMAN = "human"


class State(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    PARKED = "parked"
    DONE = "done"
    REVOKED = "revoked"


class ChannelKind(StrEnum):
    ROOM = "room"
    DIRECT = "direct"


class History(StrEnum):
    FULL = "full"
    SINCE_JOIN = "since_join"


class MsgKind(StrEnum):
    QUESTION = "question"
    INFORMATION = "information"
    ANSWER = "answer"


class CredKind(StrEnum):
    TOKEN = "token"
    JOIN = "join"


class Closed(StrEnum):
    MAX_ANSWERS = "max_answers"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Namespace:
    id: str
    created_at: datetime
    title: str = ""


@dataclass(frozen=True, slots=True)
class Identity:
    ns: str
    uid: str
    name: str
    role: Role
    kind: Kind
    parent: str | None
    depth: int
    state: State
    tags: frozenset[str]
    provider: str
    description: str
    session_ref: str | None
    resume_template: str | None
    created_at: datetime
    last_seen: datetime | None = None
    depends_on: frozenset[str] = frozenset()  # experts this agent may ask
    # It asked for `done` once: its own work is finished. Only such an agent is
    # closed by the hub; one parked while waiting for an answer is left alone.
    done_requested: bool = False

    @property
    def revoked(self) -> bool:
        return self.state is State.REVOKED

    @property
    def closed(self) -> bool:
        """Finished for good: it will never read or answer again."""
        return self.state in {State.DONE, State.REVOKED}


@dataclass(frozen=True, slots=True)
class Credential:
    hash: str
    ns: str
    name: str
    kind: CredKind
    created_at: datetime
    expires_at: datetime | None
    used_at: datetime | None = None
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class Channel:
    ns: str
    id: str
    kind: ChannelKind
    name: str
    history: History
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Membership:
    ns: str
    channel_id: str
    name: str
    joined_seq: int


@dataclass(frozen=True, slots=True)
class Ref:
    type: str
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    path: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class NewMessage:
    channel_id: str
    sender: str
    sender_role: Role
    kind: MsgKind
    text: str
    reply_to: str | None = None
    thread_id: str | None = None  # None: the message starts its own thread
    to: frozenset[str] = frozenset()
    max_agents_answers: int | None = None
    expires_at: datetime | None = None
    refs: tuple[Ref, ...] = ()


@dataclass(frozen=True, slots=True)
class Message:
    ns: str
    id: str
    channel_id: str
    seq: int
    time: datetime
    sender: str
    sender_role: Role
    kind: MsgKind
    text: str
    reply_to: str | None
    thread_id: str
    to: frozenset[str]
    max_agents_answers: int | None
    expires_at: datetime | None
    refs: tuple[Ref, ...]
    closed_reason: Closed | None = None
    amended_at: datetime | None = None

    @property
    def size(self) -> int:
        return len(self.text.encode())


@dataclass(frozen=True, slots=True)
class MessageFilter:
    channel_id: str | None = None
    sender: str | None = None
    exclude_sender: str | None = None
    kind: MsgKind | None = None
    thread_id: str | None = None
    reply_to: str | None = None
    after_seq: int | None = None
    before_seq: int | None = None
    limit: int = 100
    newest: bool = False  # take the newest `limit` matches (for history paging)


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    ns: str
    actor: str
    key: str
    body_hash: str
    result: dict[str, Any]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEntry:
    ts: datetime
    ns: str
    actor: str
    action: str
    target: str = ""
    msg_id: str | None = None
    request_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    time: datetime
    ns: str | None
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class DomainError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def not_found(what: str) -> DomainError:
    return DomainError(ErrorCode.NOT_FOUND, f"{what} not found")


def forbidden(message: str) -> DomainError:
    return DomainError(ErrorCode.FORBIDDEN, message)


def invalid(message: str) -> DomainError:
    return DomainError(ErrorCode.INVALID, message)


def unauthorized(message: str = "invalid or revoked credentials") -> DomainError:
    return DomainError(ErrorCode.UNAUTHORIZED, message)
