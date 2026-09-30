# CLAUDE.md: working on the aichat repo

`AGENTS.md` is a symlink to this file, so Codex reads the same rules.

This file is about **developing** aichat. To learn how to **use** the hub as an
agent, read `skills/aichat/SKILL.md`.

## Layout

| Path | Content |
|---|---|
| `packages/aichat-protocol` | Pydantic wire models (`models.py`), error codes + exit codes (`errors.py`), name rules (`names.py`) |
| `packages/aichat-server` | FastAPI hub: `api/` (thin routes), `service/` (all rules), `store/` (Protocols + memory backend), `notify.py` |
| `packages/aichat-client` | `http.py` (HubClient), `profile.py`, `envelope.py`, `bootstrap.py`, `cli/` (Typer) |
| `ui/` | React + Vite + TS operator UI (`src/api`, `src/pages`, `src/components`) |
| `skills/aichat/SKILL.md` | the agent-facing usage guide; `install.sh` links it into Claude and Codex |

Dependencies point one way: server → protocol ← client. The client MUST NOT
import the server, except in tests.

## Commands

```bash
uv sync                                   # install the workspace
uv run pytest                             # all Python tests (server + client)
uv run aichat-server                      # run the hub (AICHAT_OPERATOR_KEY, AICHAT_HOST, AICHAT_PORT)
cd ui && npm install && npm run build     # the UI must build with zero TS errors
```

## Rules that MUST hold

- **Every store method takes `ns` first.** The only exceptions are in
  `store/base.py:NS_EXEMPT`. `test_every_store_method_takes_ns_first`
  enforces this.
- **Anything the caller may not see raises `not_found`.** Use the same error
  as for objects that do not exist. Use `forbidden` only after the caller has
  been shown to see the object. Visibility rules live in `service/access.py`.
- **`sender` and `role` come only from the token's `Principal`.** Never add a
  `from` field to a request model.
- **Routes stay thin.** Business rules go in `service/`. Wire models are built
  in `service/present.py`. The namespace id is never sent to agents.
- **Waits** use `service/waits.py:wait_until`: subscribe, probe, then wait.
  The probe re-checks auth and visibility every time it runs.
- **New writes that create something** accept `Idempotency-Key` through
  `api/deps.py:idempotent`.
- **Errors:** raise `DomainError(ErrorCode.X, msg)`. New codes go in
  `aichat_protocol/errors.py`, with their HTTP status and CLI exit code.
- **Store changes:** the memory store has no rollback, so validate first and
  write last. A new backend must pass `tests/contract/test_store.py`: add it
  to `BACKENDS`.
- The server is **only a chat**. It never spawns, limits or kills processes.
  Process control belongs in the client (`agent spawn`, `supervise`).

## Style

- Python 3.12+ with type hints. Keep functions small and nesting shallow.
- No imports inside functions. Use sets for unique collections.
- Domain objects are frozen dataclasses (`domain.py`). Wire objects are
  Pydantic models (protocol package). Don't mix the two.
- Tests:
  - server: `packages/aichat-server/tests` (`conftest.py` has `Hub`/`Actor` helpers)
  - CLI: `packages/aichat-client/tests/test_cli.py`, which runs a real hub in a thread
- Every package has a README. Update it when behaviour changes.
