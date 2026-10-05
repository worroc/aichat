"""Store factory. Pick a backend from settings."""

from ..config import Settings
from .base import Store
from .memory import MemoryStore
from .sqlite import SqliteStore


def make_store(settings: Settings) -> Store:
    if settings.store == "memory":
        return MemoryStore()
    if settings.store == "sqlite":
        return SqliteStore(settings.db_path)
    raise ValueError(f"unknown store backend: {settings.store}")
