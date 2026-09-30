"""Secrets, hashing and the authenticated Principal."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from .domain import Kind, Role

TOKEN_PREFIX = "aic_ag_"
JOIN_PREFIX = "aic_join_"
HUMAN_PREFIX = "aic_hu_"

SECRET_RE = re.compile(r"aic_(?:ag|join|hu)_[A-Za-z0-9_-]{20,}")


def new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def find_secrets(text: str) -> set[str]:
    return set(SECRET_RE.findall(text))


def operator_key_ok(given: str | None, expected: str) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. Built only from a verified credential."""

    ns: str
    name: str
    role: Role
    kind: Kind

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_human(self) -> bool:
        return self.role is Role.HUMAN

    @property
    def sees_everyone(self) -> bool:
        return self.role in {Role.ADMIN, Role.HUMAN}
