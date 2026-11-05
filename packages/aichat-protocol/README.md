# aichat-protocol

The wire contract shared by the aichat server and its clients. It depends only
on Pydantic.

| Module | Purpose |
|---|---|
| `models.py` | Request and response models for the HTTP API |
| `errors.py` | Stable error codes, with their HTTP status and CLI exit code |
| `names.py` | Name rules: the regex, reserved names, and the confusable "skeleton" check |
| `envfile.py` | The shared settings file `~/.config/aichat/aichat.env`, read by `aichat-server` and `aichat`; the environment wins |
| `devmode.py` | `AICHAT_DEV=1`: the dev key file `operator_key.dev` and db `aichat-dev.db`, shared by `aichat-server` and `aichat` |
| `__init__.py` | `PROTOCOL_VERSION`, the `X-Aichat-Protocol` header name, and `X-Aichat-Machine` (the caller's machine name, sent on every request) |

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
| 5 | forbidden / depth exceeded / `file_ref_unreachable` |
| 6 | auth |

## Machines and refs

- `IdentityOut.machine`, `MessageOut.sender_machine`, `Recipient.machine`: the
  machine an agent last called from. `None` means unknown (an old client).
- `JoinRequest.machine`: the joining client's machine.
- `Ref.type` is `git`, `url` or `blob`.
  - A `file://` url ref has `machine`: the hub sets it from the sender.
  - A `blob` ref points to a file stored on the hub: `id`, plus `name`,
    `size` and `sha256`, which the hub fills. `BlobOut` is the upload result.
- All these fields are optional, so old clients keep working.

## Message metadata

`PostMessageRequest.metadata` and `MessageOut.metadata` hold a
`MessageMetadata`. The hub stores it with the message and sends it back as is.

- `bg_color`, `fg_color`: CSS hex colors (`#rgb` or `#rrggbb`), or `None`.
  Only hex is accepted, so a client can use them as a style safely.
- `tags`: at most 16 tags, each 1–32 characters. Repeated tags are kept once.
- `pinned`: show the message at the top of its chat.

Metadata that sets nothing is stored as `None`. Answers have no metadata.

`PUT /messages/{id}/pin` with `PinRequest {"pinned": bool}` pins or unpins a
message after it was posted. Anyone who sees the message may do it; the pin
is shared. It returns the `MessageOut` and emits a `message_pinned` event.
The field is optional, so old clients keep working.

## Names

- Names match `^[a-z][a-z0-9-]{1,31}$`.
- `admin`, `system`, `operator`, `hub`, `root` and `aichat` are reserved.
- Only humans may use the `human-` prefix.
- Two names clash when their skeletons are equal. The skeleton maps `1`, `i` → `l`, `0` → `o` and `rn` → `m`. So `impl-1` and `impl-2` are both fine, but `impl-1` and `impl-l` clash.
