"""App factory and entry point."""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import errors
from .api.deps import TokenBucket
from .api.routes_agents import router as agents_router
from .api.routes_ops import router as ops_router
from .api.routes_reader import router as reader_router
from .config import Settings
from .notify import MemoryNotifier, Notifier
from .service import build_services
from .store import make_store
from .store.base import Store

log = logging.getLogger("aichat")


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if not settings.operator_key:
        settings = settings.model_copy(update={"operator_key": secrets.token_urlsafe(24)})
        log.warning("AICHAT_OPERATOR_KEY not set; generated one for this run: %s", settings.operator_key)
    store = store or make_store(settings)
    notifier = notifier or MemoryNotifier()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(title="aichat", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.services = build_services(store, notifier, settings)
    app.state.rate = TokenBucket(settings.rate_per_sec, settings.rate_burst)
    app.state.join_rate = TokenBucket(settings.join_rate_per_sec, settings.join_rate_burst)

    errors.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(agents_router)
    app.include_router(reader_router)
    app.include_router(ops_router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
