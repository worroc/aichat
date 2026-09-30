"""FastAPI dependencies: services, auth, rate limits, idempotency."""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated, Any

from aichat_protocol.errors import ErrorCode
from fastapi import Depends, Header, Request
from pydantic import BaseModel

from ..domain import DomainError, IdempotencyRecord, forbidden, unauthorized
from ..security import Principal, operator_key_ok
from ..service import Services
from ..service.context import now


class TokenBucket:
    """Per-key token bucket. Memory grows with active keys only."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        self._state: dict[str, tuple[float, float]] = {}

    def take(self, key: str) -> bool:
        stamp = time.monotonic()
        tokens, last = self._state.get(key, (float(self.burst), stamp))
        tokens = min(self.burst, tokens + (stamp - last) * self.rate)
        if tokens < 1:
            self._state[key] = (tokens, stamp)
            return False
        self._state[key] = (tokens - 1, stamp)
        return True


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def _limit(request: Request, bucket_name: str, key: str) -> None:
    bucket: TokenBucket = getattr(request.app.state, bucket_name)
    if not bucket.take(key):
        raise DomainError(ErrorCode.RATE_LIMITED, "too many requests")


async def get_principal(
    request: Request,
    services: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized("missing bearer token")
    principal = await services.identity.authenticate(authorization.removeprefix("Bearer ").strip())
    _limit(request, "rate", f"{principal.ns}:{principal.name}")
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_admin(p: PrincipalDep) -> Principal:
    if not p.is_admin:
        raise forbidden("admin only")
    return p


AdminDep = Annotated[Principal, Depends(get_admin)]


async def require_operator(
    request: Request,
    x_operator_key: Annotated[str | None, Header()] = None,
) -> None:
    if not operator_key_ok(x_operator_key, request.app.state.settings.operator_key):
        raise unauthorized("operator key required")


OperatorDep = Depends(require_operator)


def join_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    _limit(request, "join_rate", client)


IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _body_hash(route: str, body: BaseModel) -> str:
    payload = json.dumps({"route": route, "body": body.model_dump(mode="json")}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


async def idempotent(
    services: Services,
    p: Principal,
    key: str | None,
    route: str,
    body: BaseModel,
    call: Callable[[], Awaitable[BaseModel]],
) -> Any:
    """Run ``call`` once per (caller, key). Replays return the first result."""
    if not key:
        return await call()
    store = services.ctx.store
    body_hash = _body_hash(route, body)
    record = await store.idempotency.get(p.ns, p.name, key)
    if record is not None:
        if record.body_hash != body_hash:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "key reused with a different body")
        return record.result
    result = (await call()).model_dump(mode="json")
    ttl = timedelta(seconds=services.ctx.settings.idempotency_ttl)
    await store.idempotency.put(
        p.ns,
        IdempotencyRecord(
            ns=p.ns, actor=p.name, key=key, body_hash=body_hash, result=result,
            expires_at=now() + ttl,
        ),
    )
    return result
