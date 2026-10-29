#!/usr/bin/env bash
# Stand-in for agentskills' .claude/hooks/skills-bootstrap.sh.
#
# It honours the same contract the arms assert on — the surface guard, the
# `skills: N/N from <repo>@<sha> — OK` verdict, the collision skip — so the
# hermetic tests exercise the arms' real code path instead of a mock. What it
# is FOR is the mutations: $FAKE_HOOK_MODE breaks exactly one clause of that
# contract at a time, which is how each of the hook arm's four assertions gets
# watched failing without needing the real registry or the real CLI.
#
#   honest                  the contract, kept
#   ignore-surface-guard    installs even on a durable session
#   wrong-count             emits "N-1/N — OK" (the verdict regex must reject it)
#   wrong-sentence          declines with a different sentence
#   skip-but-install        declines correctly, then installs anyway
#   collision-copies-anyway reports the collision skip and copies regardless
set -uo pipefail
cat >/dev/null || true

MODE="${FAKE_HOOK_MODE:-honest}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
DEST="$HOME/.claude/skills"
REPO="${AGENTSKILLS_REPO:?AGENTSKILLS_REPO is required}"
SRC="${REPO#file://}"

emit () {
  SKILLS_VERDICT="$1" python3 -I -c '
import json, os, sys
sys.stdout.write(json.dumps({
    "reloadSkills": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "reloadSkills": True,
        "additionalContext": os.environ["SKILLS_VERDICT"],
    },
}, ensure_ascii=True) + "\n")'
  exit 0
}

install_all () {
  mkdir -p "$DEST"
  ok=0; total=0; collisions=()
  while IFS= read -r key; do
    total=$((total + 1))
    bundle="${key%%/*}"; name="${key##*/}"
    if [ -f "$PROJECT_DIR/.claude/skills/$name/SKILL.md" ] \
       && [ "$MODE" != "collision-copies-anyway" ]; then
      collisions+=("$name"); continue
    fi
    if [ -f "$PROJECT_DIR/.claude/skills/$name/SKILL.md" ]; then
      collisions+=("$name")   # collision-copies-anyway: says one thing, does another
    fi
    rm -rf "${DEST:?}/$name"
    cp -R "$SRC/plugins/$bundle/skills/$name" "$DEST/$name" && ok=$((ok + 1))
  done < <(python3 -I -c '
import json, os, sys
lock = json.load(open(os.path.join(sys.argv[1], "skills.lock"), encoding="utf-8"))
sys.stdout.write("".join(k + "\n" for k in lock["skills"]))' "$PROJECT_DIR")

  from_ref="$REPO@0abcdef"
  [ "$MODE" = "wrong-count" ] && emit "skills: $((total - 1))/$total from $from_ref — OK"
  if [ "${#collisions[@]}" -gt 0 ]; then
    emit "skills: $ok/$total from $from_ref — DEGRADED: ${#collisions[@]} collision skipped, repo-owned wins (${collisions[*]})"
  fi
  emit "skills: $ok/$total from $from_ref — OK"
}

# --- surface guard: ephemeral sessions only ---------------------------------
# The "honest"/skip-but-install decline below deliberately emits the
# pre-agentskills-24977ed sentence, with no interpolated diagnostic clause.
# That is a live regression fixture, not staleness: arms.HOOK_SKIPPED_RE
# matches structurally now specifically so both the old sentence and the
# current enriched one pass, and this fixture is what keeps the old shape
# under test.
if [ -z "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ] && [ "${CLAUDE_CODE_ENTRYPOINT:-}" != "remote" ] \
   && [ -z "${SKILLS_BOOTSTRAP_FORCE:-}" ]; then
  case "$MODE" in
    ignore-surface-guard) install_all ;;
    wrong-sentence) emit "skills: nothing to do" ;;
    skip-but-install)
      mkdir -p "$DEST/leaked-by-a-broken-guard"
      touch "$DEST/leaked-by-a-broken-guard/SKILL.md"
      emit "skills: skipped — durable session, marketplace install is authoritative" ;;
    *) emit "skills: skipped — durable session, marketplace install is authoritative" ;;
  esac
fi

install_all
