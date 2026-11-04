"""Which machine this client runs on.

``$AICHAT_MACHINE`` when set, else the hostname. Agents on the same machine
share a disk, so they can open each other's ``file://`` refs. Set
``AICHAT_MACHINE`` to the same value on hosts that share their folders
(for example a container that mounts the host's home), or to tell apart
two hosts with the same hostname. It can live in ``aichat.env``.
"""

import os
import socket


def machine_id() -> str:
    return os.environ.get("AICHAT_MACHINE", "").strip() or socket.gethostname()
