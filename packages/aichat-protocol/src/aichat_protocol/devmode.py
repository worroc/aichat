"""Dev mode: a local test hub that never accepts the real operator key.

``AICHAT_DEV=1`` (in the environment or in ``aichat.env``) switches the hub
and the ``aichat`` CLI to their dev files, so a local run cannot be opened with
the key of the real hub, and its data never mixes with real data:

- operator key: ``~/.config/aichat/operator_key.dev`` instead of ``operator_key``
- hub database: ``~/.config/aichat/aichat-dev.db`` instead of ``aichat.db``

An explicit ``AICHAT_OPERATOR_KEY`` / ``AICHAT_OPERATOR_KEY_FILE`` /
``AICHAT_DB_PATH`` still wins.
"""

import os

DEV_ENV = "AICHAT_DEV"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def is_dev() -> bool:
    return os.environ.get(DEV_ENV, "").strip().lower() in TRUE_VALUES


def operator_key_name(dev: bool) -> str:
    return "operator_key.dev" if dev else "operator_key"


def db_name(dev: bool) -> str:
    return "aichat-dev.db" if dev else "aichat.db"
