# aichat constitution

These are the hard limits and implementation rules of aichat. They hold for
every change, for humans and agents alike. A change that breaks one of them
is wrong, even if the tests pass. To change a rule, change this file first,
on purpose, in its own commit.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Storage is replaceable

The database can be swapped at any time: memory, SQLite today, another
engine tomorrow. Only the storage layer may know which one is in use.

- All database-specific code MUST live in the storage layer:
  `packages/aichat-server/src/aichat_server/store/<backend>.py`. This means
  SQL, drivers, connections, transactions, locking, schema, migrations, and
  converting rows to and from domain objects.
- The storage layer's only contract is the set of Protocols in
  `store/base.py`. They take and return domain objects from `domain.py`.
- Application code (`service/`, `api/`, `domain.py`, `notify.py`, `main.py`)
  MUST NOT:
  - import a backend module, a database driver, or any type from them;
  - build SQL or any other query language;
  - catch backend-specific errors. A backend turns them into `DomainError`;
  - depend on behaviour of one backend that the Protocols do not promise.
    Examples: row order not stated in the contract, auto-increment ids,
    cascades, or triggers.
- Only the factory `store/__init__.py:make_store` picks a backend, and only
  from `Settings`. Backend settings (`store`, `db_path`, …) are read there and
  passed to the backend. Nothing else reads them.
- A behaviour the application needs from storage MUST first be added to a
  Protocol in `store/base.py`. Then it is implemented in **every** backend.
  Example: `Store.delete_namespace`.
- Every backend MUST pass `tests/contract/test_store.py`. Add it to
  `BACKENDS`. Server flow tests SHOULD run on every backend
  (`tests/conftest.py:settings`).
- Services MUST validate first and write last. A backend is not required to
  roll back a multi-step write made through `Store.tx()`.

## 2. Store contract

- **Every store method takes `ns` first.** The only exceptions are in
  `store/base.py:NS_EXEMPT`. `test_every_store_method_takes_ns_first`
  enforces this.
- `messages.add` assigns `id`, `seq` (per channel, strictly increasing, no
  gaps) and `time` atomically.
- `messages.add_answer` checks the answer cap and the one-answer-per-author
  rule atomically, and closes the question when the cap is reached.
- `pointers.advance` never moves a pointer backwards.
- `delete_namespace` removes the namespace and everything in it, and nothing
  else.

## 3. Package boundaries

- Dependencies point one way: server → protocol ← client.
- The client MUST NOT import the server, except in tests.
- The server is **only a chat**. It never spawns, limits or kills processes.
  Process control belongs in the client (`agent spawn`, `supervise`).

## 4. Security and visibility

- **Anything the caller may not see raises `not_found`.** Use the same error
  as for objects that do not exist. Use `forbidden` only after the caller has
  been shown to see the object. Visibility rules live in `service/access.py`.
- **`sender` and `role` come only from the token's `Principal`.** Never add a
  `from` field to a request model.
- The namespace id and title never leave the operator API. They are never
  sent to agents.
- Only hashes of tokens and join codes are stored.

## 5. Layering in the server

- **Routes stay thin.** Business rules go in `service/`. Wire models are
  built in `service/present.py`.
- **Waits** use `service/waits.py:wait_until`: subscribe, probe, then wait.
  The probe re-checks auth and visibility every time it runs.
- **New writes that create something** accept `Idempotency-Key` through
  `api/deps.py:idempotent`.
- **Errors:** raise `DomainError(ErrorCode.X, msg)`. New codes go in
  `aichat_protocol/errors.py`, with their HTTP status and CLI exit code.
- Domain objects are frozen dataclasses (`domain.py`). Wire objects are
  Pydantic models (protocol package). Don't mix the two.
