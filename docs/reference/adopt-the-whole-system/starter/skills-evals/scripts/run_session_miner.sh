#!/usr/bin/env bash
# Daily session mining: real Claude Code transcripts -> lesson records and
# proposal files on agentskills-private's `persistent/lessons` branch.
#
# Runs ONLY on the owner's own computer, from WSL (ADR 0015). Never in CI or a
# cloud session: they have no real transcripts, and must never be given any.
#
# Steps, all inside one temp dir removed on exit:
#   1. shallow-clone skills-evals and run ITS copy of scripts/mine_sessions.py;
#   2. shallow-clone the private repo's persistent/lessons branch (created as
#      an orphan the first time) from the remote of the local clone;
#   3. mine the WSL root, and the Windows root plus the desktop app's data
#      when /mnt/c is reachable, into lessons/records/<date>.jsonl; route into
#      lessons/proposals/<date>/<subject>__<kind>.json;
#   4. commit "lessons: <date>" when anything changed, push, and verify with
#      `git merge-base --is-ancestor`;
#   5. only after that, move the new state file into place, so a failed push
#      leaves those sessions unread for the next run instead of lost;
#   6. with --file-issues, also file the day's proposals as issues.
#
# Usage: scripts/run_session_miner.sh [--dry-run] [--file-issues]
#   --dry-run      commit locally, push nothing, keep the old state, file nothing
#   --file-issues  file the day's proposals as `lesson-proposal` issues (off by
#                  default: proposal files are the primary sink)
#
# Requires PRIVACY_DENYLIST_FILE (one regex per line, kept outside every repo).
# Without it nothing is mined: exit 2.
#
# Settings (all optional; the defaults are the real thing on that machine):
#   LESSONS_CLONE      private repo clone (default ~/repos/agentskills-private)
#   LESSONS_REPO_URL   override that clone's origin URL (tests)
#   SKILLS_EVALS_URL   where to clone skills-evals from (default: GitHub)
#   GUIDANCE_MANIFEST  _agent-guidance agents-md/eval-coverage.yml
#                      (default ~/repos/_agent-guidance/agents-md/eval-coverage.yml)
#   MINER_REGISTRIES   space-separated registry checkouts whose skill names count
#                      (default ~/repos/agentskills and the private clone)
#   MINER_ROOTS        space-separated LABEL=PATH roots, replacing the defaults
#   WIN_HOME           Windows profile as a WSL path (default: asked of cmd.exe)
#   MINER_STATE        state file (default ~/.local/state/session-miner/state.json)
#   MINER_NOW          ISO-8601 instant handed to the miner as --now (tests)
#   PYTHON             interpreter (default python3)
set -euo pipefail

URL="${SKILLS_EVALS_URL:-https://github.com/example-adopter/skills-evals.git}"
BRANCH="persistent/lessons"
PY="${PYTHON:-python3}"
CLONE="${LESSONS_CLONE:-$HOME/repos/agentskills-private}"
STATE="${MINER_STATE:-$HOME/.local/state/session-miner/state.json}"
MANIFEST="${GUIDANCE_MANIFEST:-$HOME/repos/_agent-guidance/agents-md/eval-coverage.yml}"
DESKTOP_PACKAGE="AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
dry_run=0
file_issues=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --file-issues) file_issues=1 ;;
    *) echo "usage: $0 [--dry-run] [--file-issues]" >&2; exit 2 ;;
  esac
done

if [ -z "${PRIVACY_DENYLIST_FILE:-}" ] || [ ! -r "$PRIVACY_DENYLIST_FILE" ]; then
  echo "session miner: PRIVACY_DENYLIST_FILE is unset or unreadable; nothing mined" >&2
  exit 2
fi

remote="${LESSONS_REPO_URL:-}"
if [ -z "$remote" ]; then
  remote="$(git -C "$CLONE" remote get-url origin 2>/dev/null)" \
    || { echo "session miner: no private repo clone with an origin at LESSONS_CLONE" >&2; exit 1; }
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/session-miner-XXXXXXXX")"
trap 'rm -rf "$tmp"' EXIT
no_bg=(--config maintenance.auto=false --config gc.auto=0)
ident=()
if [ -z "$(git config user.email 2>/dev/null || true)" ]; then
  ident=(-c user.name=session-miner -c user.email=session-miner@users.noreply.github.com)
fi

git clone "${no_bg[@]}" --quiet --depth 1 "$URL" "$tmp/src" >/dev/null 2>&1 \
  || { echo "session miner: could not clone skills-evals" >&2; exit 1; }

if git ls-remote --exit-code --heads "$remote" "$BRANCH" >/dev/null 2>&1; then
  git clone "${no_bg[@]}" --quiet --depth 1 --single-branch --branch "$BRANCH" "$remote" "$tmp/lessons" >/dev/null 2>&1 \
    || { echo "session miner: could not clone $BRANCH" >&2; exit 1; }
else
  git init --quiet -b "$BRANCH" "$tmp/lessons"
  git -C "$tmp/lessons" remote add origin "$remote"
fi

roots=()
if [ -n "${MINER_ROOTS:-}" ]; then
  read -r -a roots <<<"$MINER_ROOTS"
else
  roots=("wsl=$HOME/.claude/projects")
  win="${WIN_HOME:-}"
  if [ -z "$win" ] && command -v cmd.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
    win="$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')" 2>/dev/null || true)"
  fi
  if [ -n "$win" ] && [ -d "$win/.claude/projects" ]; then roots+=("windows=$win/.claude/projects"); fi
fi
mine_args=(mine --lessons-dir "$tmp/lessons/lessons" --state "$STATE" --state-out "$tmp/state.json")
for root in "${roots[@]}"; do mine_args+=(--root "$root"); done
if [ -z "${MINER_ROOTS:-}" ] && [ -n "${win:-}" ] && [ -d "$win/$DESKTOP_PACKAGE" ]; then
  mine_args+=(--desktop "$win/$DESKTOP_PACKAGE")
fi
if [ -f "$MANIFEST" ]; then mine_args+=(--guidance-manifest "$MANIFEST"); fi
for reg in ${MINER_REGISTRIES-$HOME/repos/agentskills $CLONE}; do
  if [ -d "$reg/plugins" ]; then mine_args+=(--registry "$reg"); fi
done
now_args=()
if [ -n "${MINER_NOW:-}" ]; then now_args=(--now "$MINER_NOW"); fi

miner="$tmp/src/scripts/mine_sessions.py"
mined="$("$PY" "$miner" "${mine_args[@]}" "${now_args[@]+"${now_args[@]}"}")"
routed="$("$PY" "$miner" route --lessons-dir "$tmp/lessons/lessons" "${now_args[@]+"${now_args[@]}"}")"
day="$(ls -1 "$tmp/lessons/lessons/proposals" 2>/dev/null | sort | tail -n 1)"

git -C "$tmp/lessons" add -A lessons
unexpected="$(git -C "$tmp/lessons" diff --cached --name-only | grep -v '^lessons/' || true)"
if [ -n "$unexpected" ]; then
  echo "session miner: unexpected staged paths outside lessons/; refusing to commit" >&2
  exit 1
fi

summary="session miner: $mined; $routed"
if git -C "$tmp/lessons" diff --cached --quiet; then
  state="unchanged"
else
  git -C "$tmp/lessons" "${ident[@]+"${ident[@]}"}" commit --quiet -m "lessons: ${day:-records}" >/dev/null
  state="committed"
fi

if [ "$dry_run" = 1 ]; then
  echo "$summary; dry run ($state locally), pushed nothing, state kept"
  exit 0
fi

if [ "$state" = "committed" ]; then
  git -C "$tmp/lessons" push --quiet origin "HEAD:$BRANCH" >/dev/null 2>&1 \
    || { echo "session miner: push to $BRANCH failed; state kept, the next run re-reads" >&2; exit 1; }
  git -C "$tmp/lessons" fetch --quiet origin "$BRANCH" >/dev/null 2>&1
  if ! git -C "$tmp/lessons" merge-base --is-ancestor HEAD "origin/$BRANCH"; then
    echo "session miner: pushed, but the commit is not on origin/$BRANCH; state kept" >&2
    exit 1
  fi
fi
mkdir -p "$(dirname "$STATE")"
mv "$tmp/state.json" "$STATE"

if [ "$file_issues" = 1 ] && [ -n "$day" ]; then
  issued="$("$PY" "$miner" file-issues --apply --proposals "$tmp/lessons/lessons/proposals/$day")"
  summary="$summary; $issued"
fi
echo "$summary; $state"
