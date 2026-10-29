#!/usr/bin/env bash
# tests/run.sh: every test for agent-skills' own tooling. Prints one line per
# suite and exits 1 if any failed. Needs git, jq and python3 with PyYAML.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")/.."

failed=0
suite() {  # suite <name> <command...>
  local name=$1 out rc; shift
  out=$("$@" 2>&1); rc=$?
  if [ "$rc" = 0 ]; then
    echo "PASS $name: $(tail -n 1 <<<"$out")"
  else
    echo "FAIL $name (exit $rc):"; printf '%s\n' "$out" | sed 's/^/    /'
    failed=1
  fi
}

suite "bash -n" bash -c 'bash -n .agent-checks/local.sh && bash -n tests/test-local.sh && echo "all shell scripts parse"'
suite "test-local.sh" bash tests/test-local.sh
suite "test-layout.py" python3 tests/test-layout.py
suite "check-workflows.py" python3 tests/check-workflows.py
suite "test-runtime-detect.py" python3 tests/test-runtime-detect.py
exit "$failed"
