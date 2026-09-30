"""Endpoints for every reader: agents, the admin and humans."""

from typing import Annotated

from aichat_protocol import models as wire
from fastapi import APIRouter, Query, Request

from .deps import IdempotencyKey, PrincipalDep, ServicesDep, idempotent
from .sse import sse_response

router = APIRouter()

Wait = Annotated[float, Query(ge=0)]
Limit = Annotated[int, Query(ge=1)]


# --- identity -------------------------------------------------------------------------


@router.get("/me", response_model=wire.MeResponse)
async def me(p: PrincipalDep, services: ServicesDep) -> wire.MeResponse:
    return await services.identity.me(p)


@router.post("/me/state", response_model=wire.IdentityOut)
async def set_state(req: wire.StateRequest, p: PrincipalDep, services: ServicesDep):
    return await services.identity.set_state(p, req)


@router.get("/directory", response_model=wire.DirectoryResponse)
async def directory(
    p: PrincipalDep,
    services: ServicesDep,
    tag: str | None = None,
    provider: str | None = None,
    state: str | None = None,
) -> wire.DirectoryResponse:
    return await services.identity.directory(p, tag, provider, state)


# --- direct messages --------------------------------------------------------------------


@router.post("/dm/{name}", response_model=wire.PostResponse)
async def post_dm(
    name: str,
    req: wire.PostMessageRequest,
    p: PrincipalDep,
    services: ServicesDep,
    key: IdempotencyKey = None,
):
    return await idempotent(
        services, p, key, f"dm:{name}", req, lambda: services.messaging.post_dm(p, name, req)
    )


@router.get("/dm/{name}/messages", response_model=wire.MessagesPage)
async def dm_history(
    name: str,
    p: PrincipalDep,
    services: ServicesDep,
    before_seq: int | None = None,
    limit: Limit = 50,
    sender: str | None = None,
    kind: str | None = None,
    thread_id: str | None = None,
) -> wire.MessagesPage:
    return await services.reading.history(
        p, None, name, before_seq=before_seq, limit=limit, sender=sender, kind=kind,
        thread_id=thread_id,
    )


@router.get("/dm/{name}/new", response_model=wire.MessagesPage)
async def dm_new(
    name: str, p: PrincipalDep, services: ServicesDep, wait: Wait = 0, limit: Limit = 50
) -> wire.MessagesPage:
    return await services.reading.new(p, None, name, wait, limit)


# --- rooms ---------------------------------------------------------------------------------


@router.post("/rooms/{room}/messages", response_model=wire.PostResponse)
async def post_room(
    room: str,
    req: wire.PostMessageRequest,
    p: PrincipalDep,
    services: ServicesDep,
    key: IdempotencyKey = None,
):
    return await idempotent(
        services, p, key, f"room:{room}", req, lambda: services.messaging.post_room(p, room, req)
    )


@router.get("/rooms/{room}/messages", response_model=wire.MessagesPage)
async def room_history(
    room: str,
    p: PrincipalDep,
    services: ServicesDep,
    before_seq: int | None = None,
    limit: Limit = 50,
    sender: str | None = None,
    kind: str | None = None,
    thread_id: str | None = None,
) -> wire.MessagesPage:
    return await services.reading.history(
        p, room, None, before_seq=before_seq, limit=limit, sender=sender, kind=kind,
        thread_id=thread_id,
    )


@router.get("/rooms/{room}/new", response_model=wire.MessagesPage)
async def room_new(
    room: str, p: PrincipalDep, services: ServicesDep, wait: Wait = 0, limit: Limit = 50
) -> wire.MessagesPage:
    return await services.reading.new(p, room, None, wait, limit)


# --- inbox and ack ----------------------------------------------------------------------------


@router.get("/inbox", response_model=wire.MessagesPage)
async def inbox(
    p: PrincipalDep, services: ServicesDep, wait: Wait = 0, limit: Limit = 50
) -> wire.MessagesPage:
    return await services.reading.inbox(p, wait, limit)


@router.post("/inbox/ack", response_model=wire.AckResponse)
@router.post("/rooms/{room}/ack", response_model=wire.AckResponse)
@router.post("/dm/{name}/ack", response_model=wire.AckResponse)
async def ack(req: wire.AckRequest, p: PrincipalDep, services: ServicesDep) -> wire.AckResponse:
    return await services.reading.ack(p, req.cursor)


# --- questions ----------------------------------------------------------------------------------


@router.post("/questions/{qid}/answers", response_model=wire.PostResponse)
async def answer(
    qid: str,
    req: wire.AnswerRequest,
    p: PrincipalDep,
    services: ServicesDep,
    key: IdempotencyKey = None,
):
    return await idempotent(
        services, p, key, f"answer:{qid}", req, lambda: services.messaging.answer(p, qid, req)
    )


@router.put("/questions/{qid}/answers/me", response_model=wire.MessageOut)
async def amend(qid: str, req: wire.AnswerRequest, p: PrincipalDep, services: ServicesDep):
    return await services.messaging.amend(p, qid, req)


@router.get("/questions/{qid}/answers", response_model=wire.AnswersResponse)
async def answers(
    qid: str, p: PrincipalDep, services: ServicesDep, wait: Wait = 0
) -> wire.AnswersResponse:
    return await services.messaging.wait_answers(p, qid, wait)


@router.post("/questions/{qid}/close", response_model=wire.MessageOut)
async def close(qid: str, p: PrincipalDep, services: ServicesDep) -> wire.MessageOut:
    return await services.messaging.close(p, qid)


@router.get("/messages/{message_id}/thread", response_model=wire.ThreadResponse)
async def thread(message_id: str, p: PrincipalDep, services: ServicesDep) -> wire.ThreadResponse:
    return await services.reading.thread(p, message_id)


# --- wake events for supervisors ------------------------------------------------------------------


@router.get("/wake/stream")
async def wake_stream(request: Request, p: PrincipalDep, services: ServicesDep, after: int = 0):
    async def fetch(cursor: int):
        return await services.ops.wake_events(p, cursor)

    return sse_response(request, services.ctx.notifier, services.ops.wake_topic(p), fetch, after)


@router.get("/wake/events")
async def wake_events(p: PrincipalDep, services: ServicesDep, after: int = 0) -> dict:
    events, last = await services.ops.wake_events(p, after)
    return {"events": [e.model_dump(mode="json") for e in events], "last": last}
