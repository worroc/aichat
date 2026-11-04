"""Server settings, read from ``AICHAT_*`` environment variables."""

from typing import Literal, Self

from aichat_protocol import devmode
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = "~/.config/aichat"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AICHAT_")

    dev: bool = False  # AICHAT_DEV=1: a local test hub with its own key and db (aichat_protocol.devmode)
    operator_key: str = ""
    operator_key_file: str = ""  # used when operator_key is empty; default operator_key[.dev]
    host: str = "127.0.0.1"
    port: int = 8765
    pid_file: str = "~/.config/aichat/aichat-server-{port}.pid"
    store: Literal["memory", "sqlite"] = "sqlite"
    db_path: str = ""  # default aichat.db, or aichat-dev.db in dev mode
    tls_cert: str = ""  # HTTPS when both are set; made by `aichat-server cert`
    tls_key: str = ""

    max_text_bytes: int = 32 * 1024
    max_limit: int = 200
    max_wait: float = 60.0
    max_waits_per_reader: int = 4
    join_ttl: float = 600.0
    idempotency_ttl: float = 24 * 3600.0

    rate_per_sec: float = 20.0
    rate_burst: int = 100
    join_rate_per_sec: float = 1.0
    join_rate_burst: int = 10

    ns_max_messages: int = 100_000
    ns_max_bytes: int = 200 * 1024 * 1024

    last_seen_interval: float = 5.0
    cors_origins: list[str] = [
        "http://localhost:5173", "http://127.0.0.1:5173", "https://localhost:5173", "https://127.0.0.1:5173",
    ]

    @model_validator(mode="after")
    def _dev_defaults(self) -> Self:
        """Fill the key file and db path that depend on dev mode, unless set explicitly."""
        if not self.operator_key_file:
            self.operator_key_file = f"{CONFIG_DIR}/{devmode.operator_key_name(self.dev)}"
        if not self.db_path:
            self.db_path = f"{CONFIG_DIR}/{devmode.db_name(self.dev)}"
        return self
