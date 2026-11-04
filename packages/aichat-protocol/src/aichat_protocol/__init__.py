"""Shared wire contract between the aichat server and its clients."""

PROTOCOL_VERSION = "1"
PROTOCOL_HEADER = "X-Aichat-Protocol"
# The host the caller runs on (`$AICHAT_MACHINE`, else the hostname). Sent on every request.
MACHINE_HEADER = "X-Aichat-Machine"
