#!/usr/bin/env bash
# skills-evals' own agent-checks, run by .githooks/agent-checks after the
# fleet checks, with the changed paths NUL-separated on stdin.
#
# Refuses a test file that names a third-party action by the exact tag
# .github/workflows/actions.lock locks. Dependabot moves that tag, so the test
# fails the bump and the sweep never merges it (#397 and #475 sat red on
# five such asserts).
set -u

rev=${AGENT_CHECKS_REV:-}
show() { git show "$rev:$1" 2>/dev/null; }

mapfile -t pins < <(show .github/workflows/actions.lock \
  | sed -n "s/^ *- '\([^']*@[^']*\)'$/\1/p" | sort -u)
[ "${#pins[@]}" -gt 0 ] || exit 0

status=0
while IFS= read -r -d '' path; do
  case "$path" in test/*.py) ;; *) continue ;; esac
  content=$(show "$path") || continue
  for pin in "${pins[@]}"; do
    if grep -qiF -- "$pin" <<<"$content"; then
      echo "agent-checks (local): $path names $pin, the exact tag actions.lock locks. Dependabot moves that tag, so this test would fail its next bump. Assert the action prefix and that lock_problem(uses, workflow) is None (test/lockfile_pins.py), or read the tag from the workflow under test." >&2
      status=1
      break
    fi
  done
done
exit $status
