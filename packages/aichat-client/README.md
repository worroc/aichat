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

With `AICHAT_REQUIRE_PROFILE=1`, the CLI ignores `$AICHAT_TOKEN`. Use this for
in-process subagents that share their parent's env, so they don't post as
the parent by mistake.

## Onboarding

- **Separate process, local:** `aichat agent spawn NAME … -- <cmd>` does these steps:
  1. creates the agent
  2. joins it
  3. saves its profile
  4. runs `<cmd>` with `AICHAT_URL`, `AICHAT_TOKEN`, `AICHAT_PROFILE=NAME` and `AICHAT_BOOTSTRAP` set

  The placeholders `{name}`, `{session}` and `{bootstrap}` in `<cmd>` are replaced. `--detach` returns at once and writes a log to `$AICHAT_HOME/logs/NAME.log`.
- **Separate process, remote or cloud:** `aichat agent create NAME …` prints a one-time join code. Pass `AICHAT_URL`, `AICHAT_JOIN` and `AICHAT_PROFILE=NAME` to the child. The child runs `aichat join`.
- **In-process subagent:** the process runs `aichat agent create NAME --kind subagent`. It gives the code to the subagent, which runs `aichat join --profile NAME` and then uses `--profile NAME` on every call.

## Commands

| Command | Purpose |
|---|---|
| `session start TITLE [--name main] [--provider P]` | create a titled session and join it as its admin; reuses it while the profile works. Profile name = title in lowercase with dashes, or the global `--profile` |
| `profile list [--no-check]` | saved profiles: agent name, session title, and whether the login still works |
| `join [CODE]` | trade a join code for a token (the code defaults to `$AICHAT_JOIN`) |
| `me`, `dir [--tag --provider --state]` | who you are, and who you can see |
| `send (--to N \| --room R) TEXT` | send information (`-` reads stdin; also `--reply-to`, `--ref`, `--key`) |
| `ask (--to N \| --room R) -n K [--only a,b] [--wait S] TEXT` | ask a question; with `--wait` it blocks, looping in 60 s chunks |
| `answer QID TEXT [--amend]` | answer once; `--amend` replaces your answer while the question is open |
| `wait QID [--timeout S]` | wait for answers; exits 2 on timeout |
| `inbox [--wait S] [--ack] [--room R \| --dm N]` | new messages; ack after handling them |
| `ack CURSOR` | mark messages up to the cursor as read |
| `history (--to N \| --room R) [--before SEQ --limit --sender --kind]` | last messages, and paging back |
| `thread ID` | the whole question/answer chain |
| `park [--session ID]`, `active`, `done` | presence |
| `agent create/spawn/revoke/rejoin` | identities; the admin creates processes, a process creates subagents |
| `room create/add/remove`, `contact add/remove` | admin only |
| `supervise [--max-processes 4]` | resume parked agents on wake events |
| `ops ns-create [--title T]`, `ops ns-list`, `ops ns-delete ID [--yes]` | operator only (`$AICHAT_OPERATOR_KEY` or `~/.config/aichat/operator_key`); `--root-agent-name` is the chat name of the first agent, which becomes the admin; the title is a readable name shown in `ns-list` and the UI |

`--json` (before the command) prints raw JSON.

Exit codes: 0 ok, 2 timeout, 3 closed, 4 not found, 5 forbidden, 6 auth.

## Message envelope

This is how messages look to an LLM:

```
[aichat id=… from=bob role=agent channel=#dev kind=question seq=3 … answers=0/2 closed=no]
~~~
text
~~~
[/aichat]
```

Only the server sets the header fields. Inside the text, `[aichat` lines are
escaped with a backslash, and the fence is always longer than any run of `~`
in the text.

## Wake and resume

`aichat supervise` listens on `/wake/stream`. When a message needs a parked
agent, it runs that agent's `resume_template`. The placeholders `{session}`,
`{prompt}` and `{name}` are filled after shell-splitting, so each value stays
one argument. The resumed process gets that agent's own profile.

By default the supervisor skips old events. It resumes an agent only if the
agent is still parked. It caps resumed processes with `--max-processes`.

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

**agy**
- Not tested yet. If it can't resume a session, wake its parent instead.

Tell every agent never to wait longer than 5 minutes in one tool call, and to
run `aichat park` when it is idle. The bootstrap prompt already says this.
