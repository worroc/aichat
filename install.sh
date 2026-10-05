#!/usr/bin/env bash
# Install the aichat CLI + server and teach Claude Code and Codex how to use them.
#
#   ./install.sh                 # everything
#   ./install.sh --no-claude     # skip Claude Code setup
#   ./install.sh --no-codex      # skip Codex setup
#   ./install.sh --no-permission # do not add Bash(aichat:*) to Claude's allow list
#   ./install.sh --uninstall     # remove tools, skill links and the permission
#
# Safe to run again. It never overwrites a skill that is not ours, and it
# backs up ~/.claude/settings.json before changing it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$REPO/skills/aichat"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PERMISSION='Bash(aichat:*)'

do_claude=1 do_codex=1 do_permission=1 uninstall=0
for arg in "$@"; do
  case "$arg" in
    --no-claude) do_claude=0 ;;
    --no-codex) do_codex=0 ;;
    --no-permission) do_permission=0 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

need_uv() {
  command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
}

install_tools() {
  need_uv
  say "installing the aichat CLI (editable, from $REPO)"
  uv tool install --quiet --force --editable "$REPO/packages/aichat-client" \
    --with-editable "$REPO/packages/aichat-protocol"
  say "installing aichat-server"
  uv tool install --quiet --force --editable "$REPO/packages/aichat-server" \
    --with-editable "$REPO/packages/aichat-protocol"
  command -v aichat >/dev/null || say "note: add $(uv tool dir --bin) to your PATH"
}

# make_operator_key: one key shared by aichat-server, `aichat ops` and `aichat session`.
make_operator_key() {
  local dir="$HOME/.config/aichat" key="$HOME/.config/aichat/operator_key"
  if [ -s "$key" ]; then
    say "operator key exists: $key"
    return
  fi
  mkdir -p "$dir" && chmod 700 "$dir"
  (umask 077 && python3 -c 'import secrets; print(secrets.token_hex(16))' > "$key")
  say "operator key created: $key"
}

# make_log_dir: the folder where aichat-server and aichat-server-ui write their logs.
# /var/log is owned by root, so this asks for the sudo password once.
make_log_dir() {
  local dir="${AICHAT_LOG_DIR:-/var/log/aichat}"
  if [ -d "$dir" ] && [ -w "$dir" ]; then
    say "log folder ok: $dir"
    return
  fi
  if mkdir -p "$dir" 2>/dev/null && [ -w "$dir" ]; then
    say "log folder created: $dir"
    return
  fi
  say "creating log folder $dir (needs sudo)"
  sudo mkdir -p "$dir" && sudo chown "$(id -un):$(id -gn)" "$dir"
  say "log folder created: $dir"
}

# link_skill DIR: symlink DIR/aichat -> repo skill, unless something else is there.
link_skill() {
  local dir="$1" target="$1/aichat"
  mkdir -p "$dir"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfn "$SKILL_SRC" "$target"
    say "skill linked: $target -> $SKILL_SRC"
  else
    say "skip: $target exists and is not a link (leave it alone)"
  fi
}

unlink_skill() {
  local target="$1/aichat"
  if [ -L "$target" ] && [ "$(readlink "$target")" = "$SKILL_SRC" ]; then
    rm "$target" && say "removed $target"
  fi
}

# edit_permission add|remove: change Bash(aichat:*) in Claude's settings.json.
edit_permission() {
  local action="$1" settings="$CLAUDE_HOME/settings.json"
  mkdir -p "$CLAUDE_HOME"
  [ -f "$settings" ] || echo '{}' > "$settings"
  cp "$settings" "$settings.bak.aichat"
  python3 - "$settings" "$PERMISSION" "$action" <<'PY'
import json, sys
path, rule, action = sys.argv[1:]
with open(path) as fh:
    data = json.load(fh)
allow = data.setdefault("permissions", {}).setdefault("allow", [])
if action == "add" and rule not in allow:
    allow.append(rule)
if action == "remove" and rule in allow:
    allow.remove(rule)
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
  say "claude permission $action: $PERMISSION (backup: $settings.bak.aichat)"
}

if [ "$uninstall" = 1 ]; then
  command -v uv >/dev/null && { uv tool uninstall aichat-client 2>/dev/null || true; uv tool uninstall aichat-server 2>/dev/null || true; }
  unlink_skill "$CLAUDE_HOME/skills"
  unlink_skill "$CODEX_HOME/skills"
  [ "$do_permission" = 1 ] && [ -f "$CLAUDE_HOME/settings.json" ] && edit_permission remove
  say "uninstalled"
  exit 0
fi

install_tools
make_operator_key
make_log_dir
if [ "$do_claude" = 1 ]; then
  link_skill "$CLAUDE_HOME/skills"
  [ "$do_permission" = 1 ] && edit_permission add
fi
[ "$do_codex" = 1 ] && link_skill "$CODEX_HOME/skills"

cat <<EOF

Done. Next steps:
  aichat-server                                   # hub in the background, http://127.0.0.1:8765
  aichat-server-ui                                # operator UI, http://127.0.0.1:5173
  aichat session start "my task"                  # create the session, join it as admin (profile: my-task)
  aichat --profile my-task me
  aichat profile list                             # all saved logins

Claude and Codex now load the "aichat" skill when a task mentions aichat.
Codex also needs network in its sandbox:
  codex exec -s workspace-write -c sandbox_workspace_write.network_access=true ...
EOF
