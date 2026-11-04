"""Print messages for an LLM so text cannot fake a header.

Each message is wrapped as::

    [aichat id=... from=bob role=agent channel=#dev kind=question ...]
    ~~~
    <text>
    ~~~
    [/aichat]

The header comes only from server fields. Inside the text, lines that look
like a header or footer are escaped with a leading backslash, and the
fence is longer than any run of ``~`` in the text.
"""

import re
from typing import Any

from .machine import machine_id

_MARKER = re.compile(r"^(\s*)(\[/?aichat)", re.MULTILINE)


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"~+", text)), default=0)
    return "~" * max(3, longest + 1)


def _escape(text: str) -> str:
    return _MARKER.sub(r"\1\\\2", text)


def header(msg: dict[str, Any]) -> str:
    fields = [
        f"id={msg['id']}",
        f"from={msg['sender']}" + (f"@{msg['sender_machine']}" if msg.get("sender_machine") else ""),
        f"role={msg['sender_role']}",
        f"channel={msg['channel']}",
        f"kind={msg['kind']}",
        f"seq={msg['seq']}",
        f"time={msg['time']}",
    ]
    if msg.get("reply_to"):
        fields.append(f"reply_to={msg['reply_to']}")
    if msg.get("thread_id") and msg["thread_id"] != msg["id"]:
        fields.append(f"thread={msg['thread_id']}")
    if msg.get("to"):
        fields.append(f"to={','.join(msg['to'])}")
    if msg["kind"] == "question":
        fields.append(f"answers={msg.get('answers_count', 0)}/{msg.get('max_agents_answers')}")
        fields.append(f"closed={msg.get('closed_reason') or 'no'}")
    if msg.get("amended_at"):
        fields.append("amended=yes")
    return "[aichat " + " ".join(fields) + "]"


def _git_ref(ref: dict[str, Any]) -> str:
    text = "git:" + (ref.get("repo") or "")
    if ref.get("branch"):
        text += "@" + ref["branch"]
    if ref.get("path"):
        text += ":" + ref["path"]
    if ref.get("commit"):
        text += f" ({ref['commit']})"
    return text


def _url_ref(ref: dict[str, Any]) -> str:
    text = f"ref: {ref.get('url')}"
    machine = ref.get("machine")
    if not (ref.get("url") or "").startswith("file://") or not machine:
        return text
    text += f" (on {machine})"
    if machine != machine_id():
        text += " NOT on this machine: ask the sender to --attach it, or for a git: or url: ref"
    return text


def _blob_ref(ref: dict[str, Any], message_id: str) -> str:
    return f"ref: file {ref.get('name')} ({ref.get('size')} bytes), get it: aichat fetch {message_id}"


def _ref(ref: dict[str, Any], message_id: str) -> str:
    if ref["type"] == "blob":
        return _blob_ref(ref, message_id)
    if ref["type"] == "url":
        return _url_ref(ref)
    return f"ref: {_git_ref(ref)}"


def _refs(msg: dict[str, Any]) -> list[str]:
    return [_ref(ref, msg["id"]) for ref in msg.get("refs") or []]


def render(msg: dict[str, Any]) -> str:
    text = _escape(msg["text"])
    fence = _fence(text)
    return "\n".join([header(msg), *_refs(msg), fence, text, fence, "[/aichat]"])


def render_many(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(render(m) for m in messages)
