"""App factory and entry point."""

import logging
import secrets
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import lifecycle
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


def _with_operator_key(settings: Settings) -> Settings:
    """Take the key from the env, else from the key file, else generate one for this run."""
    key = settings.operator_key
    path = Path(settings.operator_key_file).expanduser()
    if not key and settings.operator_key_file and path.is_file():
        key = path.read_text().strip()
    if not key:
        key = secrets.token_urlsafe(24)
        log.warning("AICHAT_OPERATOR_KEY not set and %s missing; generated one for this run: %s", path, key)
    return settings.model_copy(update={"operator_key": key})


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    settings = _with_operator_key(settings or Settings())
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


SETTINGS_HELP = """settings (environment variables):
  AICHAT_HOST, AICHAT_PORT   where to listen (127.0.0.1, 8765)
  AICHAT_STORE               sqlite (default, kept on disk) or memory (lost on stop)
  AICHAT_DB_PATH             SQLite file (~/.config/aichat/aichat.db)
  AICHAT_OPERATOR_KEY        operator key; else read from ~/.config/aichat/operator_key
  AICHAT_PID_FILE            pid file used by --stop/--restart (~/.config/aichat/aichat-server-{port}.pid)
  AICHAT_LOG_DIR             log folder (/var/log/aichat); log file: aichat-server-{port}.log
  more limits: see aichat_server/config.py

examples:
  aichat-server              # start in the background, or report that it already runs
  aichat-server --stop       # graceful stop
  aichat-server --restart    # graceful stop, then start
  aichat-server --foreground # attached to this shell
"""
GRACEFUL_SHUTDOWN = 5  # seconds open requests (long-polls, SSE) get before they are closed


def hub_service(settings: Settings) -> lifecycle.Service:
    return lifecycle.Service(
        name="aichat-server",
        host=settings.host,
        port=settings.port,
        pid_file=lifecycle.pid_file(settings.pid_file, settings.port),
        probe_path="/health",
        is_ours=lifecycle.hub_says_ok,
        port_env="AICHAT_PORT",
        relaunch=[sys.executable, "-m", "aichat_server.main", "--foreground"],
    )


def run(argv: list[str] | None = None) -> None:
    """Start the hub; safe to run again. ``--restart`` replaces a running hub."""
    parser = lifecycle.command_parser(
        "aichat-server",
        "Run the aichat hub. If it already runs on the host and port, say so and exit.",
        SETTINGS_HELP,
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = Settings()

    def serve() -> None:
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN,
        )

    lifecycle.run_service(hub_service(settings), args, serve)


if __name__ == "__main__":
    run()
