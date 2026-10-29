#!/usr/bin/env bash
# .agent-checks/local.sh in agent-skills: the registry's own rules. The runner
# passes its arguments through: "pre-commit", "pre-push", "ci <base> <head>"
# or "tree [<rev>]".
set -euo pipefail
fail=0
case "${1:-}" in
  ci) diff=$(git diff --no-color "$2...$3" -- 'plugins/*/SKILL.md') ;;
  pre-commit) diff=$(git diff --no-color --cached -- 'plugins/*/SKILL.md') ;;
  *) exit 0 ;;
esac
# 1. No skill line that runs shell when the skill loads.
added=$(grep -E '^\+' <<<"$diff" | grep -vE '^\+\+\+ ' || true)
if grep -qF '!`' <<<"$added"; then
  echo 'agent-checks: a SKILL.md line contains !` and would run a shell command on load. Put the command in a code block for the model to run instead.' >&2
  fail=1
fi
# 2. In CI, a plugin whose files changed must raise its version.
if [ "$1" = ci ]; then
  changed=$(git diff --name-only "$2...$3" -- plugins | cut -d/ -f2 | sort -u)
  for p in $changed; do
    manifest="plugins/$p/.claude-plugin/plugin.json"
    git cat-file -e "$3:$manifest" 2>/dev/null || continue   # the plugin was removed
    if ! new=$(git show "$3:$manifest" | jq -er '.version'); then
      echo "agent-checks: $manifest is not valid JSON or has no version." >&2; fail=1; continue
    fi
    git cat-file -e "$2:$manifest" 2>/dev/null || continue   # a new plugin
    old=$(git show "$2:$manifest" | jq -r '.version // empty') || old=""
    highest=$(printf '%s\n%s\n' "$old" "$new" | sort -V | tail -n 1)
    if [ -n "$old" ] && { [ "$old" = "$new" ] || [ "$highest" != "$new" ]; }; then
      echo "agent-checks: plugin $p changed but its version went from $old to $new. Raise \"version\" in $manifest." >&2
      fail=1
    fi
  done
fi
exit "$fail"
