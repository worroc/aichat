"""Name rules shared by server and client.

Names are ASCII, lowercase, start with a letter. Confusable names
(``impl-1`` vs ``impl-l``) are detected by comparing their *skeletons*.
"""

import re

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
HUMAN_PREFIX = "human-"
RESERVED = frozenset({"admin", "system", "operator", "hub", "root", "aichat"})

_CONFUSABLE = str.maketrans({"1": "l", "i": "l", "0": "o"})


def normalize(name: str) -> str:
    return name.strip().lower()


def skeleton(name: str) -> str:
    return normalize(name).replace("rn", "m").translate(_CONFUSABLE)


def name_problem(name: str, *, human: bool = False) -> str | None:
    """Return why ``name`` is not allowed, or None when it is fine."""
    if not NAME_RE.fullmatch(name):
        return "name must match ^[a-z][a-z0-9-]{1,31}$"
    if name in RESERVED:
        return f"name {name!r} is reserved"
    has_prefix = name.startswith(HUMAN_PREFIX)
    if human and not has_prefix:
        return f"human names must start with {HUMAN_PREFIX!r}"
    if not human and has_prefix:
        return f"only humans may use the {HUMAN_PREFIX!r} prefix"
    return None


def confusable_with(name: str, existing: set[str]) -> str | None:
    """Return an existing name that looks like ``name`` (but is not equal)."""
    target = skeleton(name)
    for other in existing:
        if other != name and skeleton(other) == target:
            return other
    return None
