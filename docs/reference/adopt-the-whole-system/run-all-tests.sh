#!/usr/bin/env bash
# run-all-tests.sh: run the whole kit's tests, starter/run-all-tests.sh (every
# starter folder) and bootstrap/tests/test-bootstrap.sh, and print one
# PASS/FAIL line per suite. Exits 1 if either failed; a failing suite's full
# output is shown. No network, no sleeps.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")"

logs=$(mktemp -d)
trap 'rm -rf "$logs"' EXIT
failed=0
suite() {  # suite <name> <script>
  local name=$1 script=$2 rc summary
  bash "$script" > "$logs/$name.log" 2>&1
  rc=$?
  if [ "$rc" = 0 ]; then
    summary=$(grep -E '^trees: |^[0-9]+ assertions passed' "$logs/$name.log" | tail -n 1)
    echo "PASS $name: ${summary:-exit 0}"
  else
    failed=1
    echo "FAIL $name (exit $rc):"; sed 's/^/    /' "$logs/$name.log"
  fi
}

suite starter starter/run-all-tests.sh
suite bootstrap bootstrap/tests/test-bootstrap.sh
exit "$failed"
