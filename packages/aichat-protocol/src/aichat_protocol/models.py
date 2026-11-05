"""Pydantic request/response models: the wire contract of the HTTP API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "agent", "human"]
Kind = Literal["process", "subagent", "human"]
State = Literal["pending", "active", "parked", "done", "revoked"]
SelfState = Literal["active", "parked", "done"]
MessageKind = Literal["question", "information", "answer"]
PostKind = Literal["question", "information"]
History = Literal["full", "since_join"]
ClosedReason = Literal["max_answers", "closed", "expired"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


# --- references -----------------------------------------------------------


class Ref(BaseModel):
    type: Literal["git", "url", "blob"]
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    path: str | None = None
    url: str | None = None
    # A file:// url ref: the machine the file is on. The hub sets it from the sender.
    machine: str | None = None
    # A blob ref (a file stored on the hub): its id. The hub fills name, size and sha256.
    id: str | None = None
    name: str | None = None
    size: int | None = None
    sha256: str | None = None


# --- message metadata -------------------------------------------------------

COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"  # CSS hex only: safe to use as a style
MAX_TAGS = 16
Tag = Annotated[str, Field(min_length=1, max_length=32)]


class MessageMetadata(BaseModel):
    """How a message looks and is grouped. The hub stores it and sends it back as is."""

    bg_color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    fg_color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    tags: list[Tag] = Field(default_factory=list, max_length=MAX_TAGS)
    pinned: bool = False


class PinRequest(BaseModel):
    pinned: bool


class BlobOut(BaseModel):
    id: str
    name: str
    size: int
    sha256: str


# --- identities -----------------------------------------------------------


class IdentityOut(BaseModel):
    name: str
    role: Role
    kind: Kind
    parent: str | None
    state: State
    tags: list[str]
    provider: str
    description: str
    last_seen: datetime | None
    depends_on: list[str] = Field(default_factory=list)
    machine: str | None = None  # the host the agent last called from (None: unknown, old client)


class RoomOut(BaseModel):
    name: str
    members: list[str]
    history: History


class MeResponse(BaseModel):
    name: str
    role: Role
    kind: Kind
    parent: str | None
    depth: int
    state: State
    tags: list[str]
    provider: str
    machine: str | None = None
    contacts: list[IdentityOut]
    rooms: list[RoomOut]
    children: list[str]


class StateRequest(BaseModel):
    state: SelfState
    session_ref: str | None = None
    resume_template: str | None = None


class StateResponse(IdentityOut):
    # Agents that still depend on this one. Non-empty when `done` was asked for
    # and the hub parked the agent instead; the hub closes it when they finish.
    held_by: list[str] = Field(default_factory=list)


# --- onboarding / admin ---------------------------------------------------


class CreateNamespaceRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    admin_name: str = "main"
    provider: str = "unknown"
    description: str = ""


class CreateNamespaceResponse(BaseModel):
    ns_id: str
    title: str
    admin_name: str
    join_code: str


class JoinRequest(BaseModel):
    code: str
    machine: str | None = Field(default=None, max_length=128)


class JoinResponse(BaseModel):
    name: str
    token: str
    role: Role
    kind: Kind


class CreateAgentRequest(BaseModel):
    name: str | None = None
    name_prefix: str | None = None
    kind: Literal["process", "subagent"] = "process"
    contacts: list[str] = Field(default_factory=list)
    rooms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provider: str = "unknown"
    description: str = ""
    session_ref: str | None = None
    resume_template: str | None = None
    # Experts this agent may ask. They stay reachable (parked, not done) until it is done.
    depends_on: list[str] = Field(default_factory=list)


class CreateAgentResponse(BaseModel):
    name: str
    join_code: str
    kind: Kind
    parent: str
    contacts: list[str]
    rooms: list[str]
    tags: list[str]
    depends_on: list[str] = Field(default_factory=list)


class RejoinResponse(BaseModel):
    name: str
    join_code: str


class CreateRoomRequest(BaseModel):
    name: str
    members: list[str] = Field(default_factory=list)
    history: History = "full"


class ContactRequest(BaseModel):
    a: str
    b: str


class HumanRequest(BaseModel):
    name: str


class HumanResponse(BaseModel):
    name: str
    token: str


# --- messages -------------------------------------------------------------


class PostMessageRequest(BaseModel):
    text: str
    kind: PostKind = "information"
    max_agents_answers: int | None = Field(default=None, ge=1)
    to: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    refs: list[Ref] = Field(default_factory=list)
    expires_in: float | None = Field(default=None, gt=0)
    wake: bool = False  # room information: also wake parked members (DMs and questions always do)
    metadata: MessageMetadata | None = None


class AnswerRequest(BaseModel):
    text: str
    refs: list[Ref] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: str
    channel: str
    seq: int
    time: datetime
    sender: str
    sender_role: Role
    kind: MessageKind
    text: str
    reply_to: str | None
    thread_id: str
    to: list[str]
    max_agents_answers: int | None
    answers_count: int | None
    closed: bool | None
    closed_reason: ClosedReason | None
    expires_at: datetime | None
    refs: list[Ref]
    amended_at: datetime | None
    sender_machine: str | None = None
    metadata: MessageMetadata | None = None


class Recipient(BaseModel):
    name: str
    state: State
    wake: Literal["queued", "none"]
    will_read: bool = True  # False when the recipient is done: nobody will read it
    machine: str | None = None


class PostResponse(BaseModel):
    message: MessageOut
    recipients: list[Recipient]


class MessagesPage(BaseModel):
    messages: list[MessageOut]
    cursor: str | None = None
    has_more: bool = False


class AckRequest(BaseModel):
    cursor: str


class AckResponse(BaseModel):
    acked: dict[str, int]


class AnswersResponse(BaseModel):
    question: MessageOut
    answers: list[MessageOut]
    closed: bool
    closed_reason: ClosedReason | None
    pending_wake: list[str]
    timed_out: bool


class ThreadResponse(BaseModel):
    messages: list[MessageOut]


class DirectoryResponse(BaseModel):
    identities: list[IdentityOut]


# --- events / operator ----------------------------------------------------


class EventOut(BaseModel):
    seq: int
    time: datetime
    ns: str | None
    type: str
    data: dict


class NamespaceOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    identities: int
    agents: int = 0  # agents that are not revoked; humans not counted
    active_agents: int = 0  # of those, the ones in state "active"
    rooms: int
    messages: int


class OpsIdentityOut(IdentityOut):
    depth: int
    created_at: datetime
    session_ref: str | None
    resume_template: str | None


class ChannelOut(BaseModel):
    id: str
    kind: Literal["room", "direct"]
    name: str
    members: list[str]
    history: History
    messages: int
    last_seq: int


class PointerOut(BaseModel):
    reader: str
    channel: str
    acked_seq: int


class AuditOut(BaseModel):
    ts: datetime
    actor: str
    action: str
    target: str
    msg_id: str | None
    detail: str
