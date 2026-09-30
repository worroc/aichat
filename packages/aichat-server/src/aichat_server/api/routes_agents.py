"""Join, admin and process endpoints (identity management)."""

from aichat_protocol import models as wire
from fastapi import APIRouter, Depends

from .deps import AdminDep, IdempotencyKey, PrincipalDep, ServicesDep, idempotent, join_limit

router = APIRouter()


@router.post("/join", response_model=wire.JoinResponse, dependencies=[Depends(join_limit)])
async def join(req: wire.JoinRequest, services: ServicesDep) -> wire.JoinResponse:
    return await services.identity.join(req.code)


# --- admin ------------------------------------------------------------------------


@router.post("/admin/agents", response_model=wire.CreateAgentResponse)
async def create_agent(
    req: wire.CreateAgentRequest, p: AdminDep, services: ServicesDep, key: IdempotencyKey = None
):
    return await idempotent(
        services, p, key, "admin.agents", req, lambda: services.identity.create_agent(p, req)
    )


@router.delete("/admin/agents/{name}")
async def revoke_agent(name: str, p: AdminDep, services: ServicesDep) -> dict:
    return {"revoked": await services.identity.revoke(p, name)}


@router.post("/admin/agents/{name}/rejoin", response_model=wire.RejoinResponse)
async def rejoin_agent(name: str, p: AdminDep, services: ServicesDep) -> wire.RejoinResponse:
    return await services.identity.rejoin(p, name)


@router.post("/admin/rooms", response_model=wire.RoomOut)
async def create_room(req: wire.CreateRoomRequest, p: AdminDep, services: ServicesDep):
    return await services.rooms.create_room(p, req)


@router.post("/admin/rooms/{room}/members/{name}", response_model=wire.RoomOut)
async def add_member(room: str, name: str, p: AdminDep, services: ServicesDep):
    return await services.rooms.add_member(p, room, name)


@router.delete("/admin/rooms/{room}/members/{name}", response_model=wire.RoomOut)
async def remove_member(room: str, name: str, p: AdminDep, services: ServicesDep):
    return await services.rooms.remove_member(p, room, name)


@router.post("/admin/contacts")
async def add_contact(req: wire.ContactRequest, p: AdminDep, services: ServicesDep) -> dict:
    await services.rooms.add_contact(p, req)
    return {"ok": True}


@router.delete("/admin/contacts")
async def remove_contact(req: wire.ContactRequest, p: AdminDep, services: ServicesDep) -> dict:
    await services.rooms.remove_contact(p, req)
    return {"ok": True}


# --- processes create their own subagents ------------------------------------------------


@router.post("/agents/children", response_model=wire.CreateAgentResponse)
async def create_child(
    req: wire.CreateAgentRequest, p: PrincipalDep, services: ServicesDep, key: IdempotencyKey = None
):
    child = req.model_copy(update={"kind": "subagent"})
    return await idempotent(
        services, p, key, "agents.children", child, lambda: services.identity.create_agent(p, child)
    )


@router.delete("/agents/children/{name}")
async def revoke_child(name: str, p: PrincipalDep, services: ServicesDep) -> dict:
    return {"revoked": await services.identity.revoke(p, name)}


@router.post("/agents/children/{name}/rejoin", response_model=wire.RejoinResponse)
async def rejoin_child(name: str, p: PrincipalDep, services: ServicesDep) -> wire.RejoinResponse:
    return await services.identity.rejoin(p, name)
