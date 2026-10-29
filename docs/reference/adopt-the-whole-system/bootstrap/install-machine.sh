#!/usr/bin/env bash
# install-machine.sh [--config fleet.env] [--dry-run] [--machine-dir DIR] [--force] [--with-private]
#
# Sets up one durable machine, as the guide asks:
#
#   1. ~/.claude/settings.json (or $CLAUDE_CONFIG_DIR/settings.json): merges in
#      "disableSkillShellExecution": true (section 2) and the user-level Stop
#      hook "python3 ~/.claude/hooks/memory-home.py" (8.1), with a JSON parser,
#      keeping every other key and hook. The old file is backed up first to
#      settings.json.bak.N. Invalid JSON is refused, never overwritten.
#   2. Copies memory-home.py from the starter machine folder to
#      ~/.claude/hooks/ (a different existing copy is refused unless --force).
#   3. Runs the guide's git-hook installer (6.2), install-agent-hooks.sh from
#      the machine folder, with <you> filled in, when it would change anything.
#   4. Adds the plugin marketplace <OWNER>/agent-skills (named <owner>-skills,
#      lowercased, as copy-starter.sh writes the names) and installs PLUGINS
#      (default <owner>-coding-anywhere) with `claude plugin` (5.3).
#      --with-private also adds <OWNER>/agent-skills-private. The marketplace
#      reads the registry's default branch, so merge the agent-skills starter
#      pull request first; if the add fails, this warns, skips the plugins and
#      still finishes (the git hooks are already in place): rerun it after
#      the merge.
#
# DIR defaults to ../starter/machine next to this script. Idempotent: a second
# run says "Nothing to change". --dry-run prints the merged settings.json and
# the commands, and writes nothing. Needs: python3, jq, git; claude for step 3.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

MACHINE_DIR="$HERE/../starter/machine" FORCE=0 WITH_PRIVATE=0
parse_common_args "$@"
set -- "${REST_ARGS[@]}"
while [ $# -gt 0 ]; do
  case $1 in
    --machine-dir) [ $# -ge 2 ] || die "--machine-dir needs a path"; MACHINE_DIR=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    --with-private) WITH_PRIVATE=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
require_tools python3 jq git
load_config "$CONFIG"
O=$CFG_OWNER
OL=${O,,}
CLAUDE=${CLAUDE:-claude}
CDIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
SETTINGS="$CDIR/settings.json"

find_in_machine() { # filename
  [ -d "$MACHINE_DIR" ] || return 0
  find "$MACHINE_DIR" -type f -name "$1" -print 2>/dev/null | sort | head -n1
}

# ------------------------------------------------------------ 1. settings.json
merge_settings() { # current-file-or-empty -> merged JSON on stdout; exit 3 on invalid JSON
  python3 -I - "$1" <<'PY'
import json, sys
path = sys.argv[1]
cur = {}
if path:
    try:
        cur = json.load(open(path, encoding="utf-8"))
    except Exception:
        sys.exit(3)
    if not isinstance(cur, dict):
        sys.exit(3)
CMD = "python3 ~/.claude/hooks/memory-home.py"
cur["disableSkillShellExecution"] = True
hooks = cur.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
stop = hooks.get("Stop")
if not isinstance(stop, list):
    stop = []
def names_hook(entry):
    return isinstance(entry, dict) and any(
        isinstance(h, dict) and "memory-home.py" in str(h.get("command", ""))
        for h in entry.get("hooks", []) if isinstance(entry.get("hooks"), list))
if not any(names_hook(e) for e in stop):
    stop.append({"hooks": [{"type": "command", "timeout": 10, "command": CMD}]})
hooks["Stop"] = stop
cur["hooks"] = hooks
print(json.dumps(cur, indent=2))
PY
}

step_settings() {
  local cur="" merged rc=0 n=1
  [ -f "$SETTINGS" ] && cur=$SETTINGS
  merged=$(merge_settings "$cur") || rc=$?
  [ "$rc" = 0 ] || die "$SETTINGS is not a JSON object; fix it by hand, nothing was changed"
  if [ -n "$cur" ] && [ "$(jq -S . "$SETTINGS")" = "$(printf '%s' "$merged" | jq -S .)" ]; then
    skip "$SETTINGS already has disableSkillShellExecution and the memory-home Stop hook"; return 0
  fi
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY-RUN: would write $SETTINGS as:"
    printf '%s\n' "$merged"
    return 0
  fi
  mkdir -p "$CDIR"
  if [ -n "$cur" ]; then
    while [ -e "$SETTINGS.bak.$n" ]; do n=$((n + 1)); done
    cp -p "$SETTINGS" "$SETTINGS.bak.$n"
    did "backed up $SETTINGS to $SETTINGS.bak.$n"
  fi
  local tmp; tmp=$(mktemp "$CDIR/.settings.json.XXXXXX")
  printf '%s\n' "$merged" > "$tmp"
  [ -n "$cur" ] && chmod --reference="$SETTINGS" "$tmp" 2>/dev/null || chmod 600 "$tmp"
  mv "$tmp" "$SETTINGS"
  did "merged disableSkillShellExecution and the memory-home Stop hook into $SETTINGS"
}

# ------------------------------------------------------------ 2. memory-home.py
step_memory_hook() {
  local src dst="$CDIR/hooks/memory-home.py"
  src=$(find_in_machine memory-home.py)
  if [ -z "$src" ]; then
    warn "no memory-home.py in $MACHINE_DIR: save the guide's 8.1 script as $dst yourself"
    return 0
  fi
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then skip "$dst is current"; return 0; fi
  if [ -f "$dst" ] && [ "$FORCE" = 0 ]; then
    die "$dst exists and differs from $src; compare them, then re-run with --force to replace it"
  fi
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then _print_cmd install -m 644 "$src" "$dst"; return 0; fi
  mkdir -p "$CDIR/hooks"
  [ -f "$dst" ] && cp -p "$dst" "$dst.bak"
  install -m 644 "$src" "$dst"
  did "installed $dst"
  python3 -c 'import yaml' 2>/dev/null || warn "PyYAML is missing, so the memory-home hook does nothing: sudo apt install python3-yaml (or brew install pyyaml)"
}

# ------------------------------------------------------------ 3. plugins
step_plugins() {
  local markets=() m out list p id
  markets=("$O/agent-skills|$OL-skills")
  [ "$WITH_PRIVATE" = 1 ] && markets+=("$O/agent-skills-private|$OL-skills-private")
  if ! command -v "$CLAUDE" >/dev/null 2>&1; then
    [ "$DRY_RUN" = 1 ] || die "claude is not on PATH; install Claude Code first"
    for m in "${markets[@]}"; do _print_cmd "$CLAUDE" plugin marketplace add "${m%%|*}"; done
    for p in $CFG_PLUGINS; do _print_cmd "$CLAUDE" plugin install "$p@$OL-skills"; done
    return 0
  fi
  out=$("$CLAUDE" plugin marketplace list --json 2>/dev/null) || out='[]'
  for m in "${markets[@]}"; do
    if printf '%s' "$out" | jq -e --arg n "${m##*|}" '.. | objects | select(.name? == $n)' >/dev/null 2>&1; then
      skip "marketplace ${m##*|} is configured"
    else
      WRITES=$((WRITES + 1))
      if [ "$DRY_RUN" = 1 ]; then _print_cmd "$CLAUDE" plugin marketplace add "${m%%|*}"; continue; fi
      if ! "$CLAUDE" plugin marketplace add "${m%%|*}" >/dev/null 2>"$BOOT_WORK/err"; then
        warn "could not add the marketplace ${m%%|*}: merge the agent-skills starter pull request (copy-starter.sh opened it), then rerun install-machine.sh. Plugins were not installed; everything else is done."
        MARKET_FAILED=1
        return 0
      fi
      did "added marketplace ${m%%|*}"
    fi
  done
  list=$("$CLAUDE" plugin list 2>/dev/null) || list=""
  for p in $CFG_PLUGINS; do
    id=$p; [[ $id == *@* ]] || id="$p@$OL-skills"
    [[ $id =~ ^[a-z0-9._-]+@[a-z0-9._-]+$ ]] || die "invalid plugin id in PLUGINS: $id"
    if grep -qF -- "$id" <<<"$list"; then
      skip "plugin $id is installed"
    else
      run_write "$CLAUDE" plugin install "$id"
      did "installed plugin $id (it loads in the next session)"
    fi
  done
}

# ------------------------------------------------------------ 4. git hooks
step_git_hooks() {
  local src probe="$BOOT_WORK/probe" real_global inc have prefix
  src=$(find_in_machine install-agent-hooks.sh)
  if [ -z "$src" ]; then
    warn "no install-agent-hooks.sh in $MACHINE_DIR: run the guide's 6.2 script yourself"
    return 0
  fi
  python3 -I - "$src" "$BOOT_WORK/install-agent-hooks.sh" "$O" <<'PY'
import sys
src, dst, owner = sys.argv[1:4]
open(dst, "w", encoding="utf-8").write(open(src, encoding="utf-8").read().replace("<you>", owner))
PY
  # Probe: run it against a scratch HOME and global config, and compare what it
  # would write with what is there now.
  mkdir -p "$probe"
  HOME="$probe" GIT_CONFIG_GLOBAL="$probe/.gitconfig" bash "$BOOT_WORK/install-agent-hooks.sh" >/dev/null 2>&1 || true
  inc="$HOME/.config/git/agent-hooks.inc"
  real_global=${GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}
  local current=1
  if [ ! -f "$inc" ] || ! cmp -s "$inc" "$probe/.config/git/agent-hooks.inc"; then current=0; fi
  for prefix in https://github.com/ git@github.com: ssh://git@github.com/; do
    have=$(git config --file "$real_global" --get-all "includeIf.hasconfig:remote.*.url:${prefix}${O}/**.path" 2>/dev/null) || have=""
    grep -qxF -- "$inc" <<<"$have" || current=0
  done
  if [ "$current" = 1 ]; then skip "git hooks for $O are registered"; return 0; fi
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then _print_cmd bash "$src (with <you> = $O)"; return 0; fi
  bash "$BOOT_WORK/install-agent-hooks.sh"
  did "registered the agent-checks git hooks for clones of $O's repos"
}

log "Owner $O$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
step_settings
step_memory_hook
MARKET_FAILED=0
step_git_hooks
step_plugins
finish
[ "$MARKET_FAILED" = 0 ] || log "Not finished: rerun install-machine.sh once the agent-skills starter pull request is merged."
