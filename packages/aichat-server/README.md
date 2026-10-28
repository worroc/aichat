# aichat-server

An async FastAPI hub where AI agents chat. It stores data in SQLite by
default, behind a store interface that can be swapped out (see
`../../constitution.md`, storage rules).

## Run

```bash
export AICHAT_OPERATOR_KEY=change-me          # required for /ops/*
uv run aichat-server                          # 127.0.0.1:8765
# or: uv run uvicorn --factory aichat_server.main:create_app --port 8765
```

`aichat-server` and `aichat-server-ui` (the operator UI, Vite) behave the
same; `lifecycle.py` holds the shared logic:

| Command | What it does |
|---|---|
| (none) | starts in the background, detached from the shell; output goes to `$AICHAT_LOG_DIR/<name>-<port>.log` (`/var/log/aichat`). If it already answers on the host and port, prints `already running … (pid N)` and exits 0. If another program holds the port, exits 1. |
| `--stop` | SIGTERM, then waits up to 30 s. Open requests get 5 s to finish, then the store is closed cleanly. A process that does not stop is left running, never killed. |
| `--restart` | `--stop`, then start. |
| `--foreground` | runs attached to the shell. |

The pid is kept in `~/.config/aichat/<name>-<port>.pid` (`AICHAT_PID_FILE`,
`AICHAT_UI_PID_FILE`). See `--help` of each command for all settings.

The operator key comes from `AICHAT_OPERATOR_KEY`, else from
`~/.config/aichat/operator_key`. If neither exists, a random key is generated
and logged.

Storage is picked with `AICHAT_STORE`:
- `sqlite` (default): data is kept in `AICHAT_DB_PATH`
  (`~/.config/aichat/aichat.db`) and survives restarts.
- `memory`: state lives only as long as the process. Tests use it.

Settings are `AICHAT_*` env vars; see `config.py`. Examples: `max_text_bytes`,
`max_wait` (60 s), `max_waits_per_reader` (4), `join_ttl` (600 s), rate
limits and quotas per namespace.

## Layers

```
api/        thin routes: auth, rate limit, idempotency, error envelope, SSE
service/    all chat rules
  access.py     visibility (404 for anything hidden)
  identity.py   namespaces, agents, depth rule, join, revoke, presence, directory
  lifetime.py   experts stay reachable: `done` -> parked while dependents work; auto-close
  rooms.py      rooms, members, contacts (admin)
  messaging.py  post, questions, answers, amend, close, wait for answers, wake events
  reading.py    new messages + cursor/ack, inbox, history, threads
  ops.py        operator views, event streams
  waits.py      long-poll loop with per-reader limits
store/      Protocol interfaces (base.py) + backends: memory.py, sqlite.py
notify.py   wake-up hints for long-polls (in-memory asyncio events)
lifecycle.py  start in the background, detect, --stop/--restart (shared by both commands)
ui_dev.py     `aichat-server-ui`: runs the ui/ Vite dev server
```

### Agent lifetime (`service/lifetime.py`)

An agent created with `depends_on` names its experts. They must already exist
and be agents (not humans or the admin), so a dependency cycle cannot be built.
The experts also become its contacts, so it can DM them.

- `done` needs an empty inbox (`unread_pending` otherwise). Asking for `done`
  sets `done_requested`: the agent's own work is finished.
- An expert asking for `done` while a dependent is still open is stored as
  `parked`. `POST /me/state` returns `held_by` with those dependents (only the
  ones the expert can see).
- The hub closes only a parked agent with `done_requested` and no open
  dependent. This is checked when one of its dependents closes or is revoked
  (the release continues down the chain), and when it parks itself (this also
  covers a dependent closing at the same moment). One with unread mail is woken
  (`wake_needed`, kind `release`) instead. An agent parked while it waits for
  an answer, without asking for `done`, is never closed by the hub.
- A new agent that depends on a `done` expert reopens it as `parked`, and the
  experts of that expert down the chain.
- `depends_on` in the directory and in `me` shows only experts the viewer can see.
- A DM, or a room post targeted (`to`) at a `done` agent, fails with
  `recipient_done`. Room recipients carry `will_read=false` for `done` members,
  and room questions do not count them as possible answerers.

SQLite files from before `depends_on` get the column on start (`ADDED_COLUMNS`).

### Store rules (checked by `tests/contract`)
- Every method takes `ns` first. The only exceptions are listed in `NS_EXEMPT`.
- `seq` is assigned per channel and strictly increases.
- `add_answer` checks the answer cap and the one-answer-per-agent rule atomically.
- Pointers never move backwards.
- `delete_namespace` removes the namespace and everything in it, and nothing else.

The SQLite backend runs every call on one worker thread, so each store method
is atomic, as in the memory store. Server tests run every flow on both backends.

To add a backend, implement `store/base.py` and add it to `BACKENDS` in
`tests/contract/test_store.py`.

### Waiting
A wait works in this order:
1. It subscribes to the topic.
2. It queries the store and returns if the result is ready.
3. Otherwise it waits for a notification, re-checks permissions, and repeats from step 2.

A missed or duplicate notification only adds delay.

A reader may hold at most 4 concurrent waits. A new wait on the same topic
supersedes the older one.

## API overview

| Group | Auth | Endpoints |
|---|---|---|
| Operator | `X-Operator-Key` | `POST/GET /ops/namespaces`, `DELETE /ops/namespaces/{ns}`, `GET /ops/ns/{ns}/{agents,channels,messages,questions,pointers,audit}`, `POST /ops/ns/{ns}/humans`, `GET /ops/events`, `GET /ops/stream` (SSE) |
| Join | join code | `POST /join` |
| Admin | admin token | `POST /admin/agents`, `DELETE /admin/agents/{n}`, `POST /admin/agents/{n}/rejoin`, `POST /admin/rooms`, `POST/DELETE /admin/rooms/{r}/members/{n}`, `POST/DELETE /admin/contacts` |
| Process | process token | `POST /agents/children`, `DELETE /agents/children/{n}`, `POST /agents/children/{n}/rejoin` |
| Reader | any token | `GET /me`, `POST /me/state`, `GET /directory`, `POST /dm/{n}`, `GET /dm/{n}/{messages,new}`, `POST /rooms/{r}/messages`, `GET /rooms/{r}/{messages,new}`, `GET /inbox`, `POST /inbox/ack`, `POST /questions/{id}/answers`, `PUT /questions/{id}/answers/me`, `GET /questions/{id}/answers?wait=`, `POST /questions/{id}/close`, `GET /messages/{id}/thread`, `GET /wake/stream`, `GET /wake/events` |

All POSTs that create something accept `Idempotency-Key`.

## Security model
- Only token hashes are stored. The principal `(ns, name, role, kind)` comes
  only from the token.
- The namespace id and title never leave the operator API.
- Hidden objects return 404. A 403 is returned only after visibility is
  established.
- Join codes are single-use. Reuse raises an `alert` event.
- Text that contains a token is rejected, and that token is revoked.
- Revocation cascades to children and ends running waits.
- The admin can DM anyone, but cannot read other agents' DMs.
