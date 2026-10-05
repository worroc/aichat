"""Server settings, read from ``AICHAT_*`` environment variables."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AICHAT_")

    operator_key: str = ""
    operator_key_file: str = "~/.config/aichat/operator_key"  # used when operator_key is empty
    host: str = "127.0.0.1"
    port: int = 8765
    pid_file: str = "~/.config/aichat/aichat-server-{port}.pid"
    store: Literal["memory", "sqlite"] = "sqlite"
    db_path: str = "~/.config/aichat/aichat.db"

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
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
