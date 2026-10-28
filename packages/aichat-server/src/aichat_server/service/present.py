"""Convert domain objects into wire models. Never exposes the namespace id."""

from aichat_protocol import models as wire

from ..domain import Channel, Identity, Message, MsgKind, Ref
from ..store.base import Store
from .access import label


def ref_out(ref: Ref) -> wire.Ref:
    return wire.Ref(
        type=ref.type, repo=ref.repo, branch=ref.branch, commit=ref.commit, path=ref.path, url=ref.url
    )


def ref_in(ref: wire.Ref) -> Ref:
    return Ref(
        type=ref.type, repo=ref.repo, branch=ref.branch, commit=ref.commit, path=ref.path, url=ref.url
    )


def identity_out(ident: Identity, visible: set[str] | None = None) -> wire.IdentityOut:
    """``visible``: the viewer's visible names; hidden experts are left out (None: all)."""
    depends_on = ident.depends_on if visible is None else ident.depends_on & visible
    return wire.IdentityOut(
        name=ident.name,
        role=ident.role.value,
        kind=ident.kind.value,
        parent=ident.parent,
        state=ident.state.value,
        tags=sorted(ident.tags),
        provider=ident.provider,
        description=ident.description,
        last_seen=ident.last_seen,
        depends_on=sorted(depends_on),
    )


def ops_identity_out(ident: Identity) -> wire.OpsIdentityOut:
    base = identity_out(ident).model_dump()
    return wire.OpsIdentityOut(
        **base,
        depth=ident.depth,
        created_at=ident.created_at,
        session_ref=ident.session_ref,
        resume_template=ident.resume_template,
    )


class Presenter:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def message(self, msg: Message, channel: Channel, viewer: str | None) -> wire.MessageOut:
        answers = None
        if msg.kind is MsgKind.QUESTION:
            answers = len(await self.store.messages.answers_to(msg.ns, msg.id))
        is_question = msg.kind is MsgKind.QUESTION
        return wire.MessageOut(
            id=msg.id,
            channel=label(channel, viewer),
            seq=msg.seq,
            time=msg.time,
            sender=msg.sender,
            sender_role=msg.sender_role.value,
            kind=msg.kind.value,
            text=msg.text,
            reply_to=msg.reply_to,
            thread_id=msg.thread_id,
            to=sorted(msg.to),
            max_agents_answers=msg.max_agents_answers,
            answers_count=answers,
            closed=(msg.closed_reason is not None) if is_question else None,
            closed_reason=msg.closed_reason.value if msg.closed_reason else None,
            expires_at=msg.expires_at,
            refs=[ref_out(r) for r in msg.refs],
            amended_at=msg.amended_at,
        )

    async def messages(self, items: list[Message], viewer: str | None) -> list[wire.MessageOut]:
        out: list[wire.MessageOut] = []
        channels: dict[str, Channel] = {}
        for msg in items:
            channel = channels.get(msg.channel_id) or await self.store.channels.get(
                msg.ns, msg.channel_id
            )
            if channel is None:
                continue
            channels[msg.channel_id] = channel
            out.append(await self.message(msg, channel, viewer))
        return out
