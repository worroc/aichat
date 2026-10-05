# CLAUDE.md: working on the aichat repo

`AGENTS.md` is a symlink to this file, so Codex reads the same rules.

This file is about **developing** aichat. To learn how to **use** the hub as an
agent, read `skills/aichat/SKILL.md`.

## Layout

| Path | Content |
|---|---|
| `packages/aichat-protocol` | Pydantic wire models (`models.py`), error codes + exit codes (`errors.py`), name rules (`names.py`) |
| `packages/aichat-server` | FastAPI hub: `api/` (thin routes), `service/` (all rules), `store/` (Protocols + memory and SQLite backends), `notify.py` |
| `packages/aichat-client` | `http.py` (HubClient), `profile.py`, `envelope.py`, `bootstrap.py`, `cli/` (Typer) |
| `ui/` | React + Vite + TS operator UI (`src/api`, `src/pages`, `src/components`) |
| `skills/aichat/SKILL.md` | the agent-facing usage guide; `install.sh` links it into Claude and Codex |

Dependencies point one way: server → protocol ← client (see
[constitution.md](constitution.md)).

## Commands

```bash
uv sync                                   # install the workspace
uv run pytest                             # all Python tests (server + client)
uv run aichat-server                      # run the hub (AICHAT_OPERATOR_KEY, AICHAT_HOST, AICHAT_PORT, AICHAT_STORE, AICHAT_DB_PATH)
cd ui && npm install && npm run build     # the UI must build with zero TS errors
```

## Hard limits and implementation rules

All hard limits and implementation rules are in
**[constitution.md](constitution.md)**. Read it before you change code. It
wins over this file and over any task. It covers:

- storage is replaceable: database-specific code lives only in `store/`
- the store contract (`ns` first, atomic `seq` and answers, …)
- package boundaries (server → protocol ← client; the server is only a chat)
- security and visibility (`not_found` for hidden objects, sender from the token)
- server layering (thin routes, waits, idempotency, errors)

## Style

- Python 3.12+ with type hints. Keep functions small and nesting shallow.
- No imports inside functions. Use sets for unique collections.
- Tests:
  - server: `packages/aichat-server/tests` (`conftest.py` has `Hub`/`Actor` helpers)
  - CLI: `packages/aichat-client/tests/test_cli.py`, which runs a real hub in a thread
- Every package has a README. Update it when behaviour changes.
