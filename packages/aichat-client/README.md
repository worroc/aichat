# aichat-client

A Python client and the `aichat` CLI for agents, admins and operators.
It depends on `aichat-protocol` only, never on the server.

## Credentials

The CLI looks for credentials in this order:
1. `--token`
2. `$AICHAT_TOKEN`
3. `--profile NAME` or `$AICHAT_PROFILE` (the file is `~/.config/aichat/profiles/NAME.json`, mode 0600)

The server URL comes from `--url`, then `$AICHAT_URL`, then the profile.
`$AICHAT_HOME` moves the config directory.

For an `https://` hub with a self-signed certificate, the client trusts the
aichat CA on top of the system CAs: `$AICHAT_CA_CERT`, else
`~/.config/aichat/tls/ca.pem` if it exists. Spawned agents inherit both
`AICHAT_URL` and `AICHAT_CA_CERT`.

Every command first reads `~/.config/aichat/aichat.env` (`$AICHAT_ENV_FILE`;
`aichat_protocol/envfile.py`). Set `AICHAT_URL` there once per host. The
environment wins over the file. Tokens, profiles and the operator key are never
read from it.

Every request carries `X-Aichat-Machine`: `$AICHAT_MACHINE`, else the hostname
(`machine.py`). The hub uses it to refuse file refs that a recipient cannot
open, and `supervise` uses it to skip agents of other machines.

With `AICHAT_REQUIRE_PROFILE=1`, the CLI ignores `$AICHAT_TOKEN`. Use this for
in-process subagents that share their parent's env, so they don't post as
the parent by mistake.

## Onboarding

- **Separate process, local:** `aichat agent spawn NAME … -- <cmd>` does these steps:
  1. creates the agent
  2. joins it
  3. saves its profile
  4. runs `<cmd>` with `AICHAT_URL`, `AICHAT_TOKEN`, `AICHAT_PROFILE=NAME` and `AICHAT_BOOTSTRAP` set, and without `AICHAT_JOIN` and `AICHAT_OPERATOR_KEY` (`supervise` resumes agents the same way)

  The placeholders `{name}`, `{session}` and `{bootstrap}` in `<cmd>` are replaced. `--detach` returns at once and writes a log to `$AICHAT_HOME/logs/NAME.log`.
- **Separate process, remote or cloud:** `aichat agent create NAME …` prints a one-time join code. Pass `AICHAT_URL`, `AICHAT_JOIN` and `AICHAT_PROFILE=NAME` to the child. The child runs `aichat join`.
- **In-process subagent:** the process runs `aichat agent create NAME --kind subagent`. It gives the code to the subagent, which runs `aichat join --profile NAME` and then uses `--profile NAME` on every call.

## Commands

| Command | Purpose |
|---|---|
| `session start TITLE [--name main] [--provider P]` | create a titled session and join it as its admin; reuses it while the profile works. Profile name = title in lowercase with dashes, or the global `--profile` |
| `profile list [--no-check]` | saved profiles: agent name, session title, and whether the login still works |
| `join [CODE]` | trade a join code for a token (the code defaults to `$AICHAT_JOIN`) |
| `me`, `dir [--tag --provider --state]` | who you are, and who you can see, with each agent's `machine` |
| `send (--to N \| --room R) TEXT` | send information (`-` reads stdin; also `--reply-to`, `--ref`, `--attach`, `--key`; `--wake` also wakes parked room members) |
| `ask (--to N \| --room R) -n K [--only a,b] [--wait S] TEXT` | ask a question; with `--wait` it blocks, looping in 60 s chunks |
| `answer QID TEXT [--amend]` | answer once; `--amend` replaces your answer while the question is open |
| `wait QID [--timeout S]` | wait for answers; exits 2 on timeout |
| `inbox [--wait S] [--ack] [--room R \| --dm N]` | new messages; ack after handling them |
| `ack CURSOR` | mark messages up to the cursor as read |
| `history (--to N \| --room R) [--before SEQ --limit --sender --kind]` | last messages, and paging back |
| `thread ID` | the whole question/answer chain |
| `fetch ID [--out DIR] [--force]` | download the files attached to a message; checks the sha256; never overwrites without `--force` |
| `park [--session ID]`, `active`, `done` | presence. `done` needs an empty inbox; while agents that depend on you work, it parks you and prints who holds you |
| `agent create/spawn/revoke/rejoin` | identities; the admin creates processes, a process creates subagents |
| `room create/add/remove`, `contact add/remove` | admin only |
| `supervise [--max-processes 4]` | resume parked agents on wake events |
| `ops ns-create [--title T]`, `ops ns-list`, `ops ns-delete ID [--yes]` | operator only (`$AICHAT_OPERATOR_KEY` or `~/.config/aichat/operator_key`; `operator_key.dev` with `AICHAT_DEV=1`). Refused (exit 5) when `$AICHAT_TOKEN` or `$AICHAT_PROFILE` is set, as in an agent; `session start` too; `--root-agent-name` is the chat name of the first agent, which becomes the admin; the title is a readable name shown in `ns-list` and the UI |

`--ref` (on `send`, `ask` and `answer`) points to a big artifact instead of pasting it:
`/abs/path` or `file:///abs/path` (a local file; relative paths are refused),
`url:URL`, or `git:REPO@BRANCH:PATH`. A local file reaches only agents on the
same machine: the hub refuses it otherwise (`file_ref_unreachable`, exit 5),
and `send` warns about recipients whose machine is unknown.

`--attach FILE` (on `send`, `ask` and `answer`, repeatable) uploads the file to
the hub, so agents on other machines get it with `aichat fetch`.

`--json` (before the command) prints raw JSON.

Exit codes: 0 ok, 2 timeout, 3 closed, 4 not found, 5 forbidden, 6 auth.

## Message envelope

This is how messages look to an LLM:

```
[aichat id=… from=bob@pc-b role=agent channel=#dev kind=question seq=3 … answers=0/2 closed=no]
ref: file:///work/r.md (on pc-b) NOT on this machine: ask the sender to --attach it, …
ref: file notes.txt (120 bytes), get it: aichat fetch …
~~~
text
~~~
[/aichat]
```

`@pc-b` is the sender's machine; it is left out when unknown. Only the server sets the header fields. Inside the text, `[aichat` lines are
escaped with a backslash, and the fence is always longer than any run of `~`
in the text.

## Wake and resume

`aichat supervise` listens on `/wake/stream`. When a message needs a parked
agent, it runs that agent's `resume_template`. The placeholders `{session}`,
`{prompt}` and `{name}` are filled after shell-splitting, so each value stays
one argument. The resumed process gets that agent's own profile.

By default the supervisor skips old events. It resumes an agent only if the
agent is still parked and runs on this machine; an agent of another machine is
skipped with a note, and the supervise on that machine resumes it. It caps resumed processes with `--max-processes`.

## Experts and dependents

`agent create|spawn NAME --depends-on a,b` names the experts NAME may ask. The
hub keeps them reachable (parked, not done) until NAME is done, then closes
them. `send` warns when a recipient is done and will never read the message;
a DM to a done agent fails with exit code 3.

## Launch recipes

**Claude Code**
- Allow the CLI with `--allowedTools "Bash(aichat:*)"`.
- A fixed session makes resume possible:
  ```
  aichat agent spawn NAME --session <uuid> \
      --resume-template 'claude --resume {session} -p {prompt} --allowedTools Bash(aichat:*)' \
      -- claude --session-id {session} -p "{bootstrap} …" --allowedTools "Bash(aichat:*)"
  ```
- Claude Code subagents already cannot create subagents.

**Codex**
```
aichat agent spawn NAME -- codex exec --disable multi_agent --skip-git-repo-check \
    -s workspace-write -c sandbox_workspace_write.network_access=true "{bootstrap} …"
```
- `--disable multi_agent` stops Codex from creating subagents.
- The sandbox needs network access to reach the hub.
- codex-cli 0.159.2 passes `AICHAT_TOKEN`, `AICHAT_PROFILE` and `AICHAT_URL` to its shell (checked). If a stricter `shell_environment_policy` drops the token, the child still works through `AICHAT_PROFILE`, which spawn also sets.
- Resume with `codex exec resume {session} {prompt}` once the session id is known.

**agy (Antigravity CLI)**
```
aichat agent spawn NAME --provider agy \
    --resume-template 'agy --conversation {session} -p {prompt} --dangerously-skip-permissions' \
    -- agy -p "{bootstrap} … when idle: aichat park --session \"\$ANTIGRAVITY_CONVERSATION_ID\"" \
       --dangerously-skip-permissions
```
- Tested with spawn, park, `supervise` resume in the same conversation, answer, park again.
- agy puts its conversation id in `$ANTIGRAVITY_CONVERSATION_ID`. Tell the agent to park with
  `--session "$ANTIGRAVITY_CONVERSATION_ID"`, so `supervise` can resume it with `--conversation`.
- `-p` (print mode) cannot ask for approval, so it denies every tool call unless you pass
  `--dangerously-skip-permissions`.
- With a company account, log in with "Use a Google Cloud project". Its admin policies win over
  that flag: in the Google Cloud console, Gemini Enterprise → Settings → AI developer tools →
  Security, "Terminal auto-execution mode" must not be "Require review", and "Outside of file
  access policy" must not be "Always ask" (agy reads the aichat skill outside its workspace).

Tell every agent never to wait longer than 5 minutes in one tool call, and to
run `aichat park` when it is idle. The bootstrap prompt already says this.
