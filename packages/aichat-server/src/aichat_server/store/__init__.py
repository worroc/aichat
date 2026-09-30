"""Store factory. Pick a backend from settings."""

from ..config import Settings
from .base import Store
from .memory import MemoryStore


def make_store(settings: Settings) -> Store:
    if settings.store == "memory":
        return MemoryStore()
    raise ValueError(f"unknown store backend: {settings.store}")
