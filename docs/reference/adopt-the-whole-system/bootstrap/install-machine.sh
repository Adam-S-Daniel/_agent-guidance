#!/usr/bin/env bash
# install-machine.sh [--config fleet.env] [--dry-run] [--with-codex]
#
# Sets up one durable machine (a laptop, a workstation, a WSL distro) the way
# the owner's are, by running the exported repos' own installers. It writes
# nothing of its own:
#
#   1. clones OWNER/agentskills and OWNER/_agent-guidance into REPOS_DIR
#      (default ~/repos) when they are not there; an existing clone must have
#      origin at OWNER/<repo>, and is fast-forwarded when it is clean and on
#      its default branch (otherwise it is used as it is, with a warning);
#   2. `bash agentskills/setup.sh --owner-machine`: converges
#      ~/.claude/settings.json (your marketplaces agentskills and
#      agentskills-private, the plugins enabled, syncClaudeAiSkills false,
#      disableSkillShellExecution true) and removes links earlier versions
#      made. Claude Code installs the enabled plugins in its next session;
#   3. `bash agentskills/scripts/install-agent-hooks.sh`: the config-based git
#      hooks that run a repo's .githooks/agent-checks, only in clones whose
#      remote is one of your two owners (ADR 0020 there);
#   4. `bash _agent-guidance/scripts/register-memory-home-hook.sh --hook
#      <REPOS_DIR>/_agent-guidance/.claude/hooks/memory-home.sh`: the
#      user-level memory-home SessionStart nudge and Stop gate;
#   5. with --with-codex, and only when ~/.codex exists:
#      `bash _agent-guidance/scripts/register-codex-hook.sh`; then trust the
#      hook once in Codex with /hooks.
#
# Each installer is idempotent, so a second run changes nothing. --dry-run
# makes the clones' reads and prints the commands, running none of them.
# Merge the agentskills and _agent-guidance starter pull requests first: the
# installers come from their default branches. The wsl-automation scheduled
# jobs are a separate step (the quick start's "Schedule the machine jobs").
# Needs: git, bash, python3; claude for the plugins to load.

set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
. "$HERE/lib/common.sh"

WITH_CODEX=0
parse_common_args "$@"
for a in "${REST_ARGS[@]}"; do
  case $a in
    --with-codex) WITH_CODEX=1 ;;
    *) die "unknown argument: $a" ;;
  esac
done
require_tools git python3
load_config "$CONFIG"
O=$CFG_OWNER
DIR=$CFG_REPOS_DIR

# ensure_clone <repo> <file that must be on the default branch>: sets C.
ensure_clone() {
  local r=$1 must=$2 url have default
  C="$DIR/$r"
  url="$GIT_BASE/$O/$r.git"
  if [ ! -e "$C" ]; then
    if [ "$DRY_RUN" = 1 ]; then
      _print_cmd git clone "$url" "$C"; WRITES=$((WRITES + 1))
      C="$BOOT_WORK/$r"; clone_repo "$O/$r" "$C"
    else
      mkdir -p "$DIR"
      clone_repo "$O/$r" "$C"
      did "cloned $O/$r into $C"
    fi
  else
    [ -d "$C/.git" ] || die "$C exists and is not a git clone"
    have=$(git -C "$C" remote get-url origin 2>/dev/null || true); have=${have,,}; have=${have%.git}
    case $have in
      "${GIT_BASE,,}/${O,,}/${r,,}"|"git@github.com:${O,,}/${r,,}"|"ssh://git@github.com/${O,,}/${r,,}") ;;
      *) die "$C has origin ${have:-none}, not $O/$r: move it aside or set REPOS_DIR" ;;
    esac
    default=$(git -C "$C" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main); default=${default#origin/}
    if [ -n "$(git -C "$C" status --porcelain)" ] || [ "$(git -C "$C" rev-parse --abbrev-ref HEAD)" != "$default" ]; then
      warn "$C has local changes or is not on $default; using it as it is"
    elif [ "$DRY_RUN" = 1 ]; then
      # A fetch writes the clone's refs and FETCH_HEAD, so a dry run only
      # says what it would do.
      _print_cmd git -C "$C" fetch --quiet origin "$default"
      printf 'DRY-RUN: then git -C %q merge --ff-only FETCH_HEAD, if origin/%s is ahead\n' "$C" "$default"
      WRITES=$((WRITES + 1))
    elif git -C "$C" fetch --quiet origin "$default" 2>"$BOOT_WORK/err" \
        && [ "$(git -C "$C" rev-parse HEAD)" != "$(git -C "$C" rev-parse FETCH_HEAD)" ]; then
      git -C "$C" merge --quiet --ff-only FETCH_HEAD && did "fast-forwarded $C to origin/$default"
    else
      skip "$C is current"
    fi
  fi
  [ -f "$C/$must" ] || die "$O/$r has no $must: merge its starter pull request, then rerun"
}

# step <description> <cmd...>: run one installer, or print it in a dry run.
step() {
  local what=$1; shift
  WRITES=$((WRITES + 1))
  if [ "$DRY_RUN" = 1 ]; then _print_cmd "$@"; return 0; fi
  log "== $what"
  "$@" || die "$what failed (exit $?); see its output above"
}

log "Owner $O, clones in $DIR$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN')"
ensure_clone agentskills setup.sh; AS=$C
ensure_clone _agent-guidance scripts/register-memory-home-hook.sh; AG=$C
[ -f "$AS/scripts/install-agent-hooks.sh" ] || die "$AS has no scripts/install-agent-hooks.sh"
[ -f "$AG/.claude/hooks/memory-home.sh" ] || die "$AG has no .claude/hooks/memory-home.sh"

# In a dry run of a fresh machine the clones are temporary; name where they will be.
[ "$DRY_RUN" = 0 ] || { AS=$DIR/agentskills; AG=$DIR/_agent-guidance; }
step "agentskills setup.sh --owner-machine" bash "$AS/setup.sh" --owner-machine
step "agentskills install-agent-hooks.sh" bash "$AS/scripts/install-agent-hooks.sh"
step "_agent-guidance register-memory-home-hook.sh" bash "$AG/scripts/register-memory-home-hook.sh" --hook "$DIR/_agent-guidance/.claude/hooks/memory-home.sh"
if [ "$WITH_CODEX" = 1 ]; then
  if [ -d "$HOME/.codex" ]; then
    step "_agent-guidance register-codex-hook.sh" bash "$AG/scripts/register-codex-hook.sh"
    log "Then trust the hook once in Codex: run codex and type /hooks."
  else
    log "skip: --with-codex, but there is no ~/.codex on this machine"
  fi
fi
[ "$DRY_RUN" = 1 ] || log "Start a new Claude Code session: it installs the enabled plugins from your marketplaces."
finish
