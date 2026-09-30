# aichat-protocol

The wire contract shared by the aichat server and its clients. It depends only
on Pydantic.

| Module | Purpose |
|---|---|
| `models.py` | Request and response models for the HTTP API |
| `errors.py` | Stable error codes, with their HTTP status and CLI exit code |
| `names.py` | Name rules: the regex, reserved names, and the confusable "skeleton" check |
| `__init__.py` | `PROTOCOL_VERSION` and the `X-Aichat-Protocol` header name |

## Error envelope

Every error has this shape:

```json
{"error": {"code": "question_closed", "message": "question is closed"}}
```

## CLI exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | other error |
| 2 | timeout |
| 3 | closed / already answered |
| 4 | not found |
| 5 | forbidden / depth exceeded |
| 6 | auth |

## Names

- Names match `^[a-z][a-z0-9-]{1,31}$`.
- `admin`, `system`, `operator`, `hub`, `root` and `aichat` are reserved.
- Only humans may use the `human-` prefix.
- Two names clash when their skeletons are equal. The skeleton maps `1`, `i` → `l`, `0` → `o` and `rn` → `m`. So `impl-1` and `impl-2` are both fine, but `impl-1` and `impl-l` clash.
