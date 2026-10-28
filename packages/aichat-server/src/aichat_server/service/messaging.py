"""Posting, questions, answers and waiting for answers."""

from dataclasses import replace
from datetime import timedelta

from aichat_protocol import models as wire
from aichat_protocol.errors import ErrorCode
from aichat_protocol.names import normalize

from ..domain import (
    Channel,
    ChannelKind,
    Closed,
    DomainError,
    Identity,
    Message,
    MsgKind,
    NewMessage,
    State,
    forbidden,
    invalid,
)
from ..notify import question_topic, reader_topic
from ..security import Principal, find_secrets, hash_secret
from .access import Access, label
from .context import Ctx, now
from .present import Presenter, ref_in
from .reading import ReadingService
from .waits import wait_until


class MessagingService:
    def __init__(
        self, ctx: Ctx, access: Access, presenter: Presenter, reading: ReadingService
    ) -> None:
        self.ctx = ctx
        self.store = ctx.store
        self.access = access
        self.presenter = presenter
        self.reading = reading

    # --- posting ------------------------------------------------------------------

    async def post_room(
        self, p: Principal, room: str, req: wire.PostMessageRequest
    ) -> wire.PostResponse:
        await self.ctx.identity(p)
        channel, _ = await self.access.room(p, normalize(room), join_human=True)
        others = await self._live_members(p.ns, channel) - {p.name}
        to = {normalize(n) for n in req.to}
        if to - others:
            raise invalid(f"not room members: {sorted(to - others)}")
        closed = await self._closed(p.ns, others)
        if to & closed:
            raise recipient_done(sorted(to & closed))
        if req.kind == MsgKind.QUESTION.value and closed and not to and not others - closed:
            raise recipient_done(sorted(closed))
        answerers = to or others - closed
        new = await self._build(p, channel, req, frozenset(to), answerers)
        message = await self._store(p, new)
        wake = to or (others if message.kind is MsgKind.QUESTION or req.wake else set())
        return await self._deliver(p, channel, message, others, wake)

    async def post_dm(
        self, p: Principal, peer_name: str, req: wire.PostMessageRequest
    ) -> wire.PostResponse:
        await self.ctx.identity(p)
        channel, peer = await self.access.direct_for_post(p, normalize(peer_name))
        if peer.closed:
            raise recipient_done([peer.name])
        if req.to and {normalize(n) for n in req.to} != {peer.name}:
            raise invalid("in a DM, 'to' can only be the peer")
        new = await self._build(p, channel, req, frozenset({peer.name}), {peer.name})
        message = await self._store(p, new)
        return await self._deliver(p, channel, message, {peer.name}, {peer.name})

    async def _live_members(self, ns: str, channel: Channel) -> set[str]:
        names: set[str] = set()
        for membership in await self.store.memberships.list_for_channel(ns, channel.id):
            ident = await self.store.identities.get(ns, membership.name)
            if ident is not None and not ident.revoked:
                names.add(ident.name)
        return names

    async def _closed(self, ns: str, names: set[str]) -> set[str]:
        """Members that are done: a message to them is never read."""
        found: set[str] = set()
        for name in names:
            ident = await self.store.identities.get(ns, name)
            if ident is not None and ident.closed:
                found.add(name)
        return found

    async def _build(
        self,
        p: Principal,
        channel: Channel,
        req: wire.PostMessageRequest,
        to: frozenset[str],
        answerers: set[str],
    ) -> NewMessage:
        await self._check_text(p, req.text)
        kind = MsgKind(req.kind)
        thread_id = await self._thread_of(p, channel, req.reply_to)
        cap = self._answer_cap(kind, req.max_agents_answers, answerers)
        expires = now() + timedelta(seconds=req.expires_in) if req.expires_in else None
        return NewMessage(
            channel_id=channel.id,
            sender=p.name,
            sender_role=p.role,
            kind=kind,
            text=req.text,
            reply_to=req.reply_to,
            thread_id=thread_id,
            to=to,
            max_agents_answers=cap,
            expires_at=expires if kind is MsgKind.QUESTION else None,
            refs=tuple(ref_in(r) for r in req.refs),
        )

    def _answer_cap(self, kind: MsgKind, requested: int | None, answerers: set[str]) -> int | None:
        if kind is not MsgKind.QUESTION:
            if requested is not None:
                raise invalid("max_agents_answers is only for questions")
            return None
        cap = requested or 1
        if cap > len(answerers):
            raise invalid(f"max_agents_answers={cap} but only {len(answerers)} can answer")
        return cap

    async def _thread_of(self, p: Principal, channel: Channel, reply_to: str | None) -> str | None:
        if reply_to is None:
            return None
        parent, parent_channel, _ = await self.access.message(p, reply_to)
        if parent_channel.id != channel.id:
            raise DomainError(ErrorCode.NOT_FOUND, "message not found")
        return parent.thread_id

    async def _check_text(self, p: Principal, text: str) -> None:
        if not text.strip():
            raise invalid("text is empty")
        if len(text.encode()) > self.ctx.settings.max_text_bytes:
            raise DomainError(ErrorCode.PAYLOAD_TOO_LARGE, "text is too large")
        leaked = find_secrets(text)
        if leaked:
            await self._burn_leaked(p, leaked)
            raise DomainError(ErrorCode.SECRET_IN_TEXT, "text contains a credential; it was rejected")

    async def _burn_leaked(self, p: Principal, leaked: set[str]) -> None:
        for secret in leaked:
            cred = await self.store.credentials.resolve(hash_secret(secret))
            if cred is None or cred.revoked:
                continue
            await self.store.credentials.replace(cred.ns, replace(cred, revoked=True))
            await self.ctx.audit(cred.ns, p.name, "credential.leak_revoked", cred.name)
            await self.ctx.emit(cred.ns, "alert", {"alert": "credential_leaked", "name": cred.name})

    async def _check_quota(self, ns: str, size: int) -> None:
        count, used = await self.store.messages.usage(ns)
        settings = self.ctx.settings
        if count >= settings.ns_max_messages or used + size > settings.ns_max_bytes:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "namespace storage quota exceeded")

    async def _store(self, p: Principal, new: NewMessage) -> Message:
        await self._check_quota(p.ns, len(new.text.encode()))
        return await self.store.messages.add(p.ns, new)

    # --- delivery -------------------------------------------------------------------

    async def _deliver(
        self,
        p: Principal,
        channel: Channel,
        message: Message,
        readers: set[str],
        wake_targets: set[str],
    ) -> wire.PostResponse:
        recipients: list[wire.Recipient] = []
        for name in sorted(readers):
            await self.ctx.notifier.publish(reader_topic(p.ns, name))
            ident = await self.store.identities.get(p.ns, name)
            if ident is None:
                continue
            woken = name in wake_targets and ident.state is State.PARKED
            if woken:
                await self._emit_wake(ident, message)
            recipients.append(
                wire.Recipient(
                    name=name,
                    state=ident.state.value,
                    wake="queued" if woken else "none",
                    will_read=not ident.closed,
                )
            )
        await self.ctx.emit(p.ns, "message", self._event_data(channel, message))
        await self.ctx.audit(p.ns, p.name, f"post.{message.kind.value}", label(channel, None), message.id)
        return wire.PostResponse(
            message=await self.presenter.message(message, channel, p.name), recipients=recipients
        )

    def _event_data(self, channel: Channel, message: Message) -> dict:
        return {
            "id": message.id,
            "channel_id": channel.id,
            "channel": label(channel, None),
            "channel_kind": channel.kind.value,
            "sender": message.sender,
            "kind": message.kind.value,
            "seq": message.seq,
        }

    async def _emit_wake(self, ident: Identity, message: Message) -> None:
        unread = await self.reading.unread_count(ident.ns, ident.name)
        await self.ctx.emit(
            ident.ns,
            "wake_needed",
            {
                "name": ident.name,
                "parent": ident.parent,
                "provider": ident.provider,
                "session_ref": ident.session_ref,
                "resume_template": ident.resume_template,
                "message_id": message.id,
                "from": message.sender,
                "kind": message.kind.value,
                "unread": unread,
            },
        )

    # --- answers ----------------------------------------------------------------------

    async def _question(self, p: Principal, qid: str) -> tuple[Message, Channel]:
        await self.ctx.identity(p)
        question, channel, _ = await self.access.message(p, qid)
        if question.kind is not MsgKind.QUESTION:
            raise DomainError(ErrorCode.NOT_ANSWERABLE, "only questions can be answered")
        return await self._expire(question), channel

    async def _expire(self, question: Message) -> Message:
        expired = question.expires_at is not None and question.expires_at <= now()
        if expired and question.closed_reason is None:
            question = await self.store.messages.close(question.ns, question.id, Closed.EXPIRED)
            await self.ctx.notifier.publish(question_topic(question.ns, question.id))
        return question

    async def answer(self, p: Principal, qid: str, req: wire.AnswerRequest) -> wire.PostResponse:
        question, channel = await self._question(p, qid)
        if question.sender == p.name:
            raise forbidden("you cannot answer your own question")
        if question.to and p.name not in question.to:
            raise forbidden("this question is addressed to other agents")
        await self._check_text(p, req.text)
        await self._check_quota(p.ns, len(req.text.encode()))
        new = NewMessage(
            channel_id=channel.id,
            sender=p.name,
            sender_role=p.role,
            kind=MsgKind.ANSWER,
            text=req.text,
            reply_to=question.id,
            thread_id=question.thread_id,
            to=frozenset({question.sender}),
            refs=tuple(ref_in(r) for r in req.refs),
        )
        message = await self.store.messages.add_answer(p.ns, new)
        await self._after_answer(p, question)
        readers = await self._live_members(p.ns, channel) - {p.name}
        return await self._deliver(p, channel, message, readers, {question.sender})

    async def _after_answer(self, p: Principal, question: Message) -> None:
        await self.ctx.notifier.publish(question_topic(p.ns, question.id))
        current = await self.store.messages.get(p.ns, question.id)
        if current is not None and current.closed_reason is not None:
            await self.ctx.emit(
                p.ns, "question_closed", {"id": question.id, "reason": current.closed_reason.value}
            )

    async def amend(self, p: Principal, qid: str, req: wire.AnswerRequest) -> wire.MessageOut:
        question, channel = await self._question(p, qid)
        if question.closed_reason in {Closed.CLOSED, Closed.EXPIRED}:
            raise DomainError(ErrorCode.QUESTION_CLOSED, "question is closed")
        answers = await self.store.messages.answers_to(p.ns, question.id)
        mine = next((a for a in answers if a.sender == p.name), None)
        if mine is None:
            raise DomainError(ErrorCode.NOT_FOUND, "answer not found")
        await self._check_text(p, req.text)
        updated = replace(
            mine, text=req.text, refs=tuple(ref_in(r) for r in req.refs), amended_at=now()
        )
        await self.store.messages.replace(p.ns, updated)
        await self.ctx.notifier.publish(question_topic(p.ns, question.id))
        await self.ctx.notifier.publish(reader_topic(p.ns, question.sender))
        await self.ctx.emit(p.ns, "answer_amended", {"id": mine.id, "question": question.id})
        await self.ctx.audit(p.ns, p.name, "answer.amend", label(channel, None), mine.id)
        return await self.presenter.message(updated, channel, p.name)

    async def close(self, p: Principal, qid: str) -> wire.MessageOut:
        question, channel = await self._question(p, qid)
        if question.sender != p.name and not p.is_admin:
            raise forbidden("only the asker or the admin can close a question")
        closed = await self.store.messages.close(p.ns, question.id, Closed.CLOSED)
        await self.ctx.notifier.publish(question_topic(p.ns, question.id))
        await self.ctx.emit(p.ns, "question_closed", {"id": question.id, "reason": "closed"})
        await self.ctx.audit(p.ns, p.name, "question.close", label(channel, None), question.id)
        return await self.presenter.message(closed, channel, p.name)

    # --- waiting ---------------------------------------------------------------------------

    async def wait_answers(self, p: Principal, qid: str, wait: float) -> wire.AnswersResponse:
        question, _ = await self._question(p, qid)
        if question.sender != p.name and not p.is_admin:
            raise forbidden("only the asker or the admin can wait on answers")

        async def probe() -> tuple[wire.AnswersResponse, bool]:
            current, channel = await self._question(p, qid)
            result = await self._answers_result(current, channel, p.name, timed_out=False)
            return result, result.closed

        timeout = min(wait, self.ctx.settings.max_wait)
        result, timed_out = await wait_until(
            self.ctx.notifier,
            self.ctx.waits,
            (p.ns, p.name),
            (question_topic(p.ns, qid), reader_topic(p.ns, p.name)),
            probe,
            timeout,
            self.ctx.settings.max_waits_per_reader,
        )
        return result.model_copy(update={"timed_out": timed_out and not result.closed})

    async def _answers_result(
        self, question: Message, channel: Channel, viewer: str, *, timed_out: bool
    ) -> wire.AnswersResponse:
        answers = await self.store.messages.answers_to(question.ns, question.id)
        return wire.AnswersResponse(
            question=await self.presenter.message(question, channel, viewer),
            answers=await self.presenter.messages(answers, viewer),
            closed=question.closed_reason is not None,
            closed_reason=question.closed_reason.value if question.closed_reason else None,
            pending_wake=await self._pending_wake(question, channel, {a.sender for a in answers}),
            timed_out=timed_out,
        )

    async def _pending_wake(self, question: Message, channel: Channel, answered: set[str]) -> list[str]:
        if question.closed_reason is not None:
            return []
        candidates = set(question.to)
        if not candidates and channel.kind is ChannelKind.ROOM:
            candidates = await self._live_members(question.ns, channel) - {question.sender}
        parked: list[str] = []
        for name in sorted(candidates - answered):
            ident = await self.store.identities.get(question.ns, name)
            if ident is not None and ident.state is State.PARKED:
                parked.append(name)
        return parked


def recipient_done(names: list[str]) -> DomainError:
    return DomainError(
        ErrorCode.RECIPIENT_DONE,
        f"{', '.join(names)} finished for good (done) and will never read this; ask main instead",
    )
