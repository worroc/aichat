"""Operator endpoints (``X-Operator-Key``). Used by the web UI."""

from typing import Annotated

from aichat_protocol import models as wire
from fastapi import APIRouter, Query, Request

from .deps import OperatorDep, ServicesDep
from .sse import sse_response

router = APIRouter(prefix="/ops", dependencies=[OperatorDep])

Limit = Annotated[int, Query(ge=1)]


@router.post("/namespaces", response_model=wire.CreateNamespaceResponse)
async def create_namespace(req: wire.CreateNamespaceRequest, services: ServicesDep):
    return await services.identity.create_namespace(req)


@router.get("/namespaces", response_model=list[wire.NamespaceOut])
async def namespaces(services: ServicesDep):
    return await services.ops.namespaces()


@router.delete("/namespaces/{ns}")
async def delete_namespace(ns: str, services: ServicesDep) -> dict:
    await services.ops.delete_namespace(ns)
    return {"deleted": ns}


@router.get("/ns/{ns}/agents", response_model=list[wire.OpsIdentityOut])
async def agents(ns: str, services: ServicesDep):
    return await services.ops.identities(ns)


@router.get("/ns/{ns}/channels", response_model=list[wire.ChannelOut])
async def channels(ns: str, services: ServicesDep):
    return await services.ops.channels(ns)


@router.get("/ns/{ns}/messages", response_model=wire.MessagesPage)
async def messages(
    ns: str,
    services: ServicesDep,
    channel_id: str | None = None,
    sender: str | None = None,
    kind: str | None = None,
    thread_id: str | None = None,
    before_seq: int | None = None,
    limit: Limit = 100,
):
    return await services.ops.messages(
        ns, channel_id=channel_id, sender=sender, kind=kind, thread_id=thread_id,
        before_seq=before_seq, limit=limit,
    )


@router.get("/ns/{ns}/questions", response_model=list[wire.MessageOut])
async def questions(ns: str, services: ServicesDep, limit: Limit = 100):
    return await services.ops.questions(ns, limit)


@router.get("/ns/{ns}/pointers", response_model=list[wire.PointerOut])
async def pointers(ns: str, services: ServicesDep):
    return await services.ops.pointers(ns)


@router.get("/ns/{ns}/audit", response_model=list[wire.AuditOut])
async def audit(ns: str, services: ServicesDep, limit: Limit = 200):
    return await services.ops.audit(ns, limit)


@router.post("/ns/{ns}/humans", response_model=wire.HumanResponse)
async def human(ns: str, req: wire.HumanRequest, services: ServicesDep):
    return await services.identity.create_human(ns, req.name)


@router.get("/events", response_model=list[wire.EventOut])
async def events(services: ServicesDep, after: int = 0, limit: Limit = 200):
    return await services.ops.ops_events(after, limit)


@router.get("/stream")
async def stream(request: Request, services: ServicesDep, after: int = 0):
    async def fetch(cursor: int):
        found = await services.ops.ops_events(cursor)
        return found, (found[-1].seq if found else cursor)

    return sse_response(request, services.ctx.notifier, services.ops.ops_topic(), fetch, after)
