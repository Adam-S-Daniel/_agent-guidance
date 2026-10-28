#!/usr/bin/env bash
# run-all-tests.sh: run every tree's tests/run.sh and print one PASS/FAIL line
# per tree, then the totals. Exits 1 if any tree failed. Each tree's own output
# is kept in a log under a temp directory, shown in full for a failing tree.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")"

trees=(agent-guidance agent-skills agent-skills-private skills-evals repo-settings machine cloud)
logs=$(mktemp -d)
trap 'rm -rf "$logs"' EXIT
passed=0 failed=0
for t in "${trees[@]}"; do
  if [ ! -f "$t/tests/run.sh" ]; then
    echo "FAIL $t: no tests/run.sh"; failed=$((failed + 1)); continue
  fi
  bash "$t/tests/run.sh" > "$logs/$t.log" 2>&1
  rc=$?
  # The tree's summary lines: suite lines for the shell trees, pytest's count line.
  summary=$(grep -E '^(PASS|FAIL) |^[0-9]+ (passed|failed|skipped)' "$logs/$t.log" | tr '\n' ';' | sed 's/;$//; s/;/; /g')
  if [ "$rc" = 0 ]; then
    passed=$((passed + 1)); echo "PASS $t: $summary"
  else
    failed=$((failed + 1)); echo "FAIL $t (exit $rc):"; sed 's/^/    /' "$logs/$t.log"
  fi
done
echo "trees: $passed passed, $failed failed, ${#trees[@]} total"
[ "$failed" = 0 ]
