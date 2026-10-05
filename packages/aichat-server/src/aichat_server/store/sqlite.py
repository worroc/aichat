"""SQLite store.

All database work runs on one worker thread. Each repo method is one
synchronous function sent to that thread, so a method is atomic relative
to other coroutines, the same as in the memory store. Writes that touch
several rows run inside ``BEGIN IMMEDIATE`` and roll back on error.
``tx()`` serialises multi-step writes from the service layer, as in the
memory store.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aichat_protocol.errors import ErrorCode

from ..domain import (
    AuditEntry,
    Channel,
    ChannelKind,
    Closed,
    Credential,
    CredKind,
    DomainError,
    Event,
    History,
    IdempotencyRecord,
    Identity,
    Kind,
    Membership,
    Message,
    MessageFilter,
    MsgKind,
    Namespace,
    NewMessage,
    Ref,
    Role,
    State,
    not_found,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS namespaces (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS identities (
    ns TEXT NOT NULL, name TEXT NOT NULL, uid TEXT NOT NULL, role TEXT NOT NULL,
    kind TEXT NOT NULL, parent TEXT, depth INTEGER NOT NULL, state TEXT NOT NULL,
    tags TEXT NOT NULL, provider TEXT NOT NULL, description TEXT NOT NULL,
    session_ref TEXT, resume_template TEXT, created_at TEXT NOT NULL, last_seen TEXT,
    PRIMARY KEY (ns, name)
);
CREATE TABLE IF NOT EXISTS credentials (
    hash TEXT PRIMARY KEY, ns TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT, used_at TEXT, revoked INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS credentials_owner ON credentials (ns, name);
CREATE TABLE IF NOT EXISTS contacts (
    ns TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, PRIMARY KEY (ns, a, b)
);
CREATE TABLE IF NOT EXISTS channels (
    ns TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
    history TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (ns, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS channels_room ON channels (ns, name) WHERE kind = 'room';
CREATE TABLE IF NOT EXISTS memberships (
    ns TEXT NOT NULL, channel_id TEXT NOT NULL, name TEXT NOT NULL,
    joined_seq INTEGER NOT NULL, PRIMARY KEY (ns, channel_id, name)
);
CREATE TABLE IF NOT EXISTS messages (
    rid INTEGER PRIMARY KEY, ns TEXT NOT NULL, id TEXT NOT NULL, channel_id TEXT NOT NULL,
    seq INTEGER NOT NULL, time TEXT NOT NULL, sender TEXT NOT NULL, sender_role TEXT NOT NULL,
    kind TEXT NOT NULL, text TEXT NOT NULL, reply_to TEXT, thread_id TEXT NOT NULL,
    recipients TEXT NOT NULL, max_agents_answers INTEGER, expires_at TEXT, refs TEXT NOT NULL,
    closed_reason TEXT, amended_at TEXT, size INTEGER NOT NULL, answer_to TEXT,
    UNIQUE (ns, id), UNIQUE (ns, channel_id, seq)
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages (ns, thread_id);
CREATE INDEX IF NOT EXISTS messages_answers ON messages (ns, answer_to);
CREATE TABLE IF NOT EXISTS pointers (
    ns TEXT NOT NULL, reader TEXT NOT NULL, channel_id TEXT NOT NULL, seq INTEGER NOT NULL,
    PRIMARY KEY (ns, reader, channel_id)
);
CREATE TABLE IF NOT EXISTS idempotency (
    ns TEXT NOT NULL, actor TEXT NOT NULL, key TEXT NOT NULL, body_hash TEXT NOT NULL,
    result TEXT NOT NULL, expires_at TEXT NOT NULL, PRIMARY KEY (ns, actor, key)
);
CREATE TABLE IF NOT EXISTS audit (
    rid INTEGER PRIMARY KEY, ns TEXT NOT NULL, ts TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT NOT NULL, msg_id TEXT, request_id TEXT, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ns ON audit (ns, rid);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, ns TEXT,
    type TEXT NOT NULL, data TEXT NOT NULL
);
"""

# Tables cleared by Store.delete_namespace (column that holds the namespace id).
NS_TABLES = (
    ("identities", "ns"),
    ("credentials", "ns"),
    ("contacts", "ns"),
    ("channels", "ns"),
    ("memberships", "ns"),
    ("messages", "ns"),
    ("pointers", "ns"),
    ("idempotency", "ns"),
    ("audit", "ns"),
    ("events", "ns"),
    ("namespaces", "id"),
)

MESSAGE_COLS = (
    "ns, id, channel_id, seq, time, sender, sender_role, kind, text, reply_to, thread_id, "
    "recipients, max_agents_answers, expires_at, refs, closed_reason, amended_at"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="microseconds")


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class Db:
    """One connection, used only from one worker thread."""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = str(Path(path).expanduser())
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aichat-sqlite")

    async def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def run(self, sql: str, *args: Any) -> int:
        return self.conn.execute(sql, args).rowcount

    def close(self) -> None:
        self._pool.shutdown(wait=True)
        self.conn.close()


class SqliteNamespaces:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, namespace: Namespace) -> None:
        await self._db.call(self._add, namespace)

    def _add(self, n: Namespace) -> None:
        self._db.run(
            "INSERT OR REPLACE INTO namespaces VALUES (?, ?, ?)", n.id, _ts(n.created_at), n.title
        )

    async def get(self, ns_id: str) -> Namespace | None:
        row = await self._db.call(self._db.one, "SELECT * FROM namespaces WHERE id = ?", ns_id)
        return None if row is None else _namespace(row)

    async def list(self) -> list[Namespace]:
        rows = await self._db.call(self._db.all, "SELECT * FROM namespaces ORDER BY created_at")
        return [_namespace(r) for r in rows]


def _namespace(row: sqlite3.Row) -> Namespace:
    return Namespace(id=row["id"], created_at=_dt(row["created_at"]), title=row["title"])


class SqliteIdentities:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, identity: Identity) -> None:
        await self._db.call(self._add, ns, identity)

    def _add(self, ns: str, i: Identity) -> None:
        try:
            self._db.run(
                "INSERT INTO identities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                *_identity_row(ns, i),
            )
        except sqlite3.IntegrityError:
            raise DomainError(ErrorCode.ALREADY_EXISTS, f"name {i.name!r} is taken") from None

    async def get(self, ns: str, name: str) -> Identity | None:
        row = await self._db.call(
            self._db.one, "SELECT * FROM identities WHERE ns = ? AND name = ?", ns, name
        )
        return None if row is None else _identity(row)

    async def list(self, ns: str) -> list[Identity]:
        rows = await self._db.call(
            self._db.all, "SELECT * FROM identities WHERE ns = ? ORDER BY rowid", ns
        )
        return [_identity(r) for r in rows]

    async def replace(self, ns: str, identity: Identity) -> None:
        await self._db.call(self._replace, ns, identity)

    def _replace(self, ns: str, i: Identity) -> None:
        row = _identity_row(ns, i)
        changed = self._db.run(
            "UPDATE identities SET uid = ?, role = ?, kind = ?, parent = ?, depth = ?, state = ?,"
            " tags = ?, provider = ?, description = ?, session_ref = ?, resume_template = ?,"
            " created_at = ?, last_seen = ? WHERE ns = ? AND name = ?",
            *row[2:], ns, i.name,
        )
        if not changed:
            raise not_found("identity")


def _identity_row(ns: str, i: Identity) -> tuple[Any, ...]:
    return (
        ns, i.name, i.uid, i.role.value, i.kind.value, i.parent, i.depth, i.state.value,
        json.dumps(sorted(i.tags)), i.provider, i.description, i.session_ref,
        i.resume_template, _ts(i.created_at), _ts(i.last_seen),
    )


def _identity(row: sqlite3.Row) -> Identity:
    return Identity(
        ns=row["ns"],
        uid=row["uid"],
        name=row["name"],
        role=Role(row["role"]),
        kind=Kind(row["kind"]),
        parent=row["parent"],
        depth=row["depth"],
        state=State(row["state"]),
        tags=frozenset(json.loads(row["tags"])),
        provider=row["provider"],
        description=row["description"],
        session_ref=row["session_ref"],
        resume_template=row["resume_template"],
        created_at=_dt(row["created_at"]),
        last_seen=_dt(row["last_seen"]),
    )


class SqliteCredentials:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, credential: Credential) -> None:
        await self._db.call(self._write, "INSERT OR REPLACE", ns, credential)

    def _write(self, verb: str, ns: str, c: Credential) -> None:
        self._db.run(
            f"{verb} INTO credentials VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            c.hash, ns, c.name, c.kind.value, _ts(c.created_at), _ts(c.expires_at),
            _ts(c.used_at), int(c.revoked),
        )

    async def resolve(self, secret_hash: str) -> Credential | None:
        row = await self._db.call(
            self._db.one, "SELECT * FROM credentials WHERE hash = ?", secret_hash
        )
        return None if row is None else _credential(row)

    async def replace(self, ns: str, credential: Credential) -> None:
        await self._db.call(self._replace, ns, credential)

    def _replace(self, ns: str, c: Credential) -> None:
        with self._db.write():
            current = self._db.one("SELECT ns FROM credentials WHERE hash = ?", c.hash)
            if current is None or current["ns"] != ns:
                raise not_found("credential")
            self._write("REPLACE", c.ns, c)

    async def list_for(self, ns: str, name: str) -> list[Credential]:
        rows = await self._db.call(
            self._db.all,
            "SELECT * FROM credentials WHERE ns = ? AND name = ? ORDER BY rowid", ns, name,
        )
        return [_credential(r) for r in rows]


def _credential(row: sqlite3.Row) -> Credential:
    return Credential(
        hash=row["hash"],
        ns=row["ns"],
        name=row["name"],
        kind=CredKind(row["kind"]),
        created_at=_dt(row["created_at"]),
        expires_at=_dt(row["expires_at"]),
        used_at=_dt(row["used_at"]),
        revoked=bool(row["revoked"]),
    )


class SqliteContacts:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, a: str, b: str) -> None:
        if a != b:
            await self._db.call(self._edit, "INSERT OR IGNORE INTO contacts VALUES (?, ?, ?)", ns, a, b)

    async def remove(self, ns: str, a: str, b: str) -> None:
        await self._db.call(
            self._edit, "DELETE FROM contacts WHERE ns = ? AND a = ? AND b = ?", ns, a, b
        )

    def _edit(self, sql: str, ns: str, a: str, b: str) -> None:
        with self._db.write():
            self._db.run(sql, ns, a, b)
            self._db.run(sql, ns, b, a)

    async def list(self, ns: str, name: str) -> set[str]:
        rows = await self._db.call(
            self._db.all, "SELECT b FROM contacts WHERE ns = ? AND a = ?", ns, name
        )
        return {r["b"] for r in rows}

    async def are_contacts(self, ns: str, a: str, b: str) -> bool:
        row = await self._db.call(
            self._db.one, "SELECT 1 FROM contacts WHERE ns = ? AND a = ? AND b = ?", ns, a, b
        )
        return row is not None


class SqliteChannels:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, channel: Channel) -> None:
        await self._db.call(self._add, ns, channel)

    def _add(self, ns: str, c: Channel) -> None:
        with self._db.write():
            if self._db.one("SELECT 1 FROM channels WHERE ns = ? AND id = ?", ns, c.id):
                raise DomainError(ErrorCode.ALREADY_EXISTS, "channel exists")
            if c.kind is ChannelKind.ROOM and self._room_row(ns, c.name):
                raise DomainError(ErrorCode.ALREADY_EXISTS, f"room {c.name!r} exists")
            self._db.run(
                "INSERT INTO channels VALUES (?, ?, ?, ?, ?, ?)",
                ns, c.id, c.kind.value, c.name, c.history.value, _ts(c.created_at),
            )

    def _room_row(self, ns: str, name: str) -> sqlite3.Row | None:
        return self._db.one(
            "SELECT * FROM channels WHERE ns = ? AND name = ? AND kind = 'room'", ns, name
        )

    async def get(self, ns: str, channel_id: str) -> Channel | None:
        row = await self._db.call(
            self._db.one, "SELECT * FROM channels WHERE ns = ? AND id = ?", ns, channel_id
        )
        return None if row is None else _channel(row)

    async def get_room(self, ns: str, name: str) -> Channel | None:
        row = await self._db.call(self._room_row, ns, name)
        return None if row is None else _channel(row)

    async def list(self, ns: str, kind: ChannelKind | None = None) -> list[Channel]:
        if kind is None:
            rows = await self._db.call(
                self._db.all, "SELECT * FROM channels WHERE ns = ? ORDER BY rowid", ns
            )
        else:
            rows = await self._db.call(
                self._db.all,
                "SELECT * FROM channels WHERE ns = ? AND kind = ? ORDER BY rowid", ns, kind.value,
            )
        return [_channel(r) for r in rows]


def _channel(row: sqlite3.Row) -> Channel:
    return Channel(
        ns=row["ns"],
        id=row["id"],
        kind=ChannelKind(row["kind"]),
        name=row["name"],
        history=History(row["history"]),
        created_at=_dt(row["created_at"]),
    )


class SqliteMemberships:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, membership: Membership) -> None:
        m = membership
        await self._db.call(
            self._db.run,
            "INSERT OR IGNORE INTO memberships VALUES (?, ?, ?, ?)",
            ns, m.channel_id, m.name, m.joined_seq,
        )

    async def remove(self, ns: str, channel_id: str, name: str) -> None:
        await self._db.call(
            self._db.run,
            "DELETE FROM memberships WHERE ns = ? AND channel_id = ? AND name = ?",
            ns, channel_id, name,
        )

    async def get(self, ns: str, channel_id: str, name: str) -> Membership | None:
        row = await self._db.call(
            self._db.one,
            "SELECT * FROM memberships WHERE ns = ? AND channel_id = ? AND name = ?",
            ns, channel_id, name,
        )
        return None if row is None else _membership(row)

    async def list_for_channel(self, ns: str, channel_id: str) -> list[Membership]:
        rows = await self._db.call(
            self._db.all,
            "SELECT * FROM memberships WHERE ns = ? AND channel_id = ? ORDER BY rowid",
            ns, channel_id,
        )
        return [_membership(r) for r in rows]

    async def list_for_member(self, ns: str, name: str) -> list[Membership]:
        rows = await self._db.call(
            self._db.all,
            "SELECT * FROM memberships WHERE ns = ? AND name = ? ORDER BY rowid", ns, name,
        )
        return [_membership(r) for r in rows]


def _membership(row: sqlite3.Row) -> Membership:
    return Membership(
        ns=row["ns"], channel_id=row["channel_id"], name=row["name"], joined_seq=row["joined_seq"]
    )


class SqliteMessages:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, new: NewMessage) -> Message:
        return await self._db.call(self._add, ns, new)

    def _add(self, ns: str, new: NewMessage) -> Message:
        with self._db.write():
            return self._insert(ns, new, None)

    async def add_answer(self, ns: str, new: NewMessage) -> Message:
        return await self._db.call(self._add_answer, ns, new)

    def _add_answer(self, ns: str, new: NewMessage) -> Message:
        with self._db.write():
            question = self._get(ns, new.reply_to or "")
            if question is None:
                raise not_found("question")
            if question.closed_reason is not None:
                raise DomainError(ErrorCode.QUESTION_CLOSED, "question is closed")
            senders = {a.sender for a in self._answers(ns, question.id)}
            if new.sender in senders:
                raise DomainError(ErrorCode.ALREADY_ANSWERED, "you already answered; use amend")
            message = self._insert(ns, new, question.id)
            cap = question.max_agents_answers
            if cap and len(senders) + 1 >= cap:
                self._set_closed(ns, question.id, Closed.MAX_ANSWERS)
            return message

    def _insert(self, ns: str, new: NewMessage, answer_to: str | None) -> Message:
        seq = self._last_seq(ns, new.channel_id) + 1
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
        self._db.run(
            f"INSERT INTO messages ({MESSAGE_COLS}, size, answer_to)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            *_message_row(message), message.size, answer_to,
        )
        return message

    async def replace(self, ns: str, message: Message) -> None:
        await self._db.call(self._replace, ns, message)

    def _replace(self, ns: str, m: Message) -> None:
        row = _message_row(m)
        changed = self._db.run(
            "UPDATE messages SET channel_id = ?, seq = ?, time = ?, sender = ?, sender_role = ?,"
            " kind = ?, text = ?, reply_to = ?, thread_id = ?, recipients = ?,"
            " max_agents_answers = ?, expires_at = ?, refs = ?, closed_reason = ?,"
            " amended_at = ?, size = ? WHERE ns = ? AND id = ?",
            *row[2:], m.size, ns, m.id,
        )
        if not changed:
            raise not_found("message")

    async def close(self, ns: str, message_id: str, reason: Closed) -> Message:
        return await self._db.call(self._close, ns, message_id, reason)

    def _close(self, ns: str, message_id: str, reason: Closed) -> Message:
        with self._db.write():
            message = self._get(ns, message_id)
            if message is None:
                raise not_found("message")
            if message.closed_reason is None:
                self._set_closed(ns, message_id, reason)
            return self._get(ns, message_id)

    def _set_closed(self, ns: str, message_id: str, reason: Closed) -> None:
        self._db.run(
            "UPDATE messages SET closed_reason = ? WHERE ns = ? AND id = ?",
            reason.value, ns, message_id,
        )

    async def get(self, ns: str, message_id: str) -> Message | None:
        return await self._db.call(self._get, ns, message_id)

    def _get(self, ns: str, message_id: str) -> Message | None:
        row = self._db.one("SELECT * FROM messages WHERE ns = ? AND id = ?", ns, message_id)
        return None if row is None else _message(row)

    async def search(self, ns: str, flt: MessageFilter) -> list[Message]:
        return await self._db.call(self._search, ns, flt)

    def _search(self, ns: str, flt: MessageFilter) -> list[Message]:
        where, args, order = _search_sql(ns, flt)
        direction = " DESC" if flt.newest else ""
        sql = (
            f"SELECT * FROM messages WHERE {' AND '.join(where)}"
            f" ORDER BY {', '.join(c + direction for c in order)} LIMIT ?"
        )
        rows = self._db.all(sql, *args, flt.limit)
        found = [_message(r) for r in rows]
        return found[::-1] if flt.newest else found

    async def answers_to(self, ns: str, question_id: str) -> list[Message]:
        return await self._db.call(self._answers, ns, question_id)

    def _answers(self, ns: str, question_id: str) -> list[Message]:
        rows = self._db.all(
            "SELECT * FROM messages WHERE ns = ? AND answer_to = ? ORDER BY rid", ns, question_id
        )
        return [_message(r) for r in rows]

    async def thread(self, ns: str, thread_id: str) -> list[Message]:
        rows = await self._db.call(
            self._db.all,
            "SELECT * FROM messages WHERE ns = ? AND thread_id = ? ORDER BY seq, rid",
            ns, thread_id,
        )
        return [_message(r) for r in rows]

    async def last_seq(self, ns: str, channel_id: str) -> int:
        return await self._db.call(self._last_seq, ns, channel_id)

    def _last_seq(self, ns: str, channel_id: str) -> int:
        row = self._db.one(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM messages WHERE ns = ? AND channel_id = ?",
            ns, channel_id,
        )
        return row["seq"]

    async def usage(self, ns: str) -> tuple[int, int]:
        row = await self._db.call(
            self._db.one,
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM messages WHERE ns = ?",
            ns,
        )
        return row["n"], row["bytes"]


def _search_sql(ns: str, flt: MessageFilter) -> tuple[list[str], list[Any], list[str]]:
    """WHERE parts, their arguments and the ORDER BY columns; same rules as the memory store."""
    where, args = ["ns = ?"], [ns]
    checks = (
        ("channel_id = ?", flt.channel_id),
        ("thread_id = ?", flt.thread_id),
        ("sender = ?", flt.sender),
        ("sender != ?", flt.exclude_sender),
        ("kind = ?", flt.kind.value if flt.kind else None),
        ("reply_to = ?", flt.reply_to),
        ("seq > ?", flt.after_seq),
        ("seq < ?", flt.before_seq),
    )
    for clause, value in checks:
        if value is not None:
            where.append(clause)
            args.append(value)
    if flt.channel_id is not None:
        order = ["seq"]
    elif flt.thread_id is not None:
        order = ["rid"]
    else:
        order = ["time", "seq"]
    return where, args, order


def _message_row(m: Message) -> tuple[Any, ...]:
    return (
        m.ns, m.id, m.channel_id, m.seq, _ts(m.time), m.sender, m.sender_role.value,
        m.kind.value, m.text, m.reply_to, m.thread_id, json.dumps(sorted(m.to)),
        m.max_agents_answers, _ts(m.expires_at), json.dumps([asdict(r) for r in m.refs]),
        m.closed_reason.value if m.closed_reason else None, _ts(m.amended_at),
    )


def _message(row: sqlite3.Row) -> Message:
    closed = row["closed_reason"]
    return Message(
        ns=row["ns"],
        id=row["id"],
        channel_id=row["channel_id"],
        seq=row["seq"],
        time=_dt(row["time"]),
        sender=row["sender"],
        sender_role=Role(row["sender_role"]),
        kind=MsgKind(row["kind"]),
        text=row["text"],
        reply_to=row["reply_to"],
        thread_id=row["thread_id"],
        to=frozenset(json.loads(row["recipients"])),
        max_agents_answers=row["max_agents_answers"],
        expires_at=_dt(row["expires_at"]),
        refs=tuple(Ref(**r) for r in json.loads(row["refs"])),
        closed_reason=Closed(closed) if closed else None,
        amended_at=_dt(row["amended_at"]),
    )


class SqlitePointers:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def get(self, ns: str, reader: str, channel_id: str) -> int:
        row = await self._db.call(
            self._db.one,
            "SELECT seq FROM pointers WHERE ns = ? AND reader = ? AND channel_id = ?",
            ns, reader, channel_id,
        )
        return 0 if row is None else row["seq"]

    async def advance(self, ns: str, reader: str, channel_id: str, seq: int) -> int:
        return await self._db.call(self._advance, ns, reader, channel_id, seq)

    def _advance(self, ns: str, reader: str, channel_id: str, seq: int) -> int:
        row = self._db.one(
            "INSERT INTO pointers VALUES (?, ?, ?, ?)"
            " ON CONFLICT (ns, reader, channel_id) DO UPDATE SET seq = MAX(seq, excluded.seq)"
            " RETURNING seq",
            ns, reader, channel_id, seq,
        )
        return row["seq"]

    async def list(self, ns: str) -> list[tuple[str, str, int]]:
        rows = await self._db.call(
            self._db.all, "SELECT * FROM pointers WHERE ns = ? ORDER BY rowid", ns
        )
        return [(r["reader"], r["channel_id"], r["seq"]) for r in rows]


class SqliteIdempotency:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def get(self, ns: str, actor: str, key: str) -> IdempotencyRecord | None:
        row = await self._db.call(
            self._db.one,
            "SELECT * FROM idempotency WHERE ns = ? AND actor = ? AND key = ?", ns, actor, key,
        )
        if row is None:
            return None
        record = _idempotency(row)
        return None if record.expires_at <= _now() else record

    async def put(self, ns: str, record: IdempotencyRecord) -> None:
        r = record
        await self._db.call(
            self._db.run,
            "INSERT OR REPLACE INTO idempotency VALUES (?, ?, ?, ?, ?, ?)",
            ns, r.actor, r.key, r.body_hash, json.dumps(r.result), _ts(r.expires_at),
        )

    async def purge_expired(self, now: datetime) -> int:
        return await self._db.call(self._purge, now)

    def _purge(self, now: datetime) -> int:
        with self._db.write():
            rows = self._db.all("SELECT rowid, expires_at FROM idempotency")
            expired = [r["rowid"] for r in rows if _dt(r["expires_at"]) <= now]
            for rowid in expired:
                self._db.run("DELETE FROM idempotency WHERE rowid = ?", rowid)
            return len(expired)


def _idempotency(row: sqlite3.Row) -> IdempotencyRecord:
    return IdempotencyRecord(
        ns=row["ns"],
        actor=row["actor"],
        key=row["key"],
        body_hash=row["body_hash"],
        result=json.loads(row["result"]),
        expires_at=_dt(row["expires_at"]),
    )


class SqliteAudit:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str, entry: AuditEntry) -> None:
        e = entry
        await self._db.call(
            self._db.run,
            "INSERT INTO audit (ns, ts, actor, action, target, msg_id, request_id, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ns, _ts(e.ts), e.actor, e.action, e.target, e.msg_id, e.request_id, e.detail,
        )

    async def list(self, ns: str, limit: int = 200) -> list[AuditEntry]:
        rows = await self._db.call(
            self._db.all, "SELECT * FROM audit WHERE ns = ? ORDER BY rid DESC LIMIT ?", ns, limit
        )
        return [_audit(r) for r in reversed(rows)]


def _audit(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        ts=_dt(row["ts"]),
        ns=row["ns"],
        actor=row["actor"],
        action=row["action"],
        target=row["target"],
        msg_id=row["msg_id"],
        request_id=row["request_id"],
        detail=row["detail"],
    )


class SqliteEvents:
    def __init__(self, db: Db) -> None:
        self._db = db

    async def add(self, ns: str | None, type_: str, data: dict[str, Any]) -> Event:
        return await self._db.call(self._add, ns, type_, data)

    def _add(self, ns: str | None, type_: str, data: dict[str, Any]) -> Event:
        row = self._db.one(
            "INSERT INTO events (time, ns, type, data) VALUES (?, ?, ?, ?) RETURNING *",
            _ts(_now()), ns, type_, json.dumps(data),
        )
        return _event(row)

    async def list(self, ns: str, after_seq: int, limit: int = 200) -> list[Event]:
        rows = await self._db.call(
            self._db.all,
            "SELECT * FROM events WHERE ns = ? AND seq > ? ORDER BY seq LIMIT ?",
            ns, after_seq, limit,
        )
        return [_event(r) for r in rows]

    async def list_all(self, after_seq: int, limit: int = 200) -> list[Event]:
        rows = await self._db.call(
            self._db.all, "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
            after_seq, limit,
        )
        return [_event(r) for r in rows]


def _event(row: sqlite3.Row) -> Event:
    return Event(
        seq=row["seq"], time=_dt(row["time"]), ns=row["ns"], type=row["type"],
        data=json.loads(row["data"]),
    )


class SqliteStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = Db(path)
        self.namespaces = SqliteNamespaces(self._db)
        self.identities = SqliteIdentities(self._db)
        self.credentials = SqliteCredentials(self._db)
        self.contacts = SqliteContacts(self._db)
        self.channels = SqliteChannels(self._db)
        self.memberships = SqliteMemberships(self._db)
        self.messages = SqliteMessages(self._db)
        self.pointers = SqlitePointers(self._db)
        self.idempotency = SqliteIdempotency(self._db)
        self.audit = SqliteAudit(self._db)
        self.events = SqliteEvents(self._db)
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        self._db.close()

    async def delete_namespace(self, ns: str) -> None:
        await self._db.call(self._delete_namespace, ns)

    def _delete_namespace(self, ns: str) -> None:
        with self._db.write():
            for table, column in NS_TABLES:
                self._db.run(f"DELETE FROM {table} WHERE {column} = ?", ns)

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[None]:
        async with self._lock:
            yield
