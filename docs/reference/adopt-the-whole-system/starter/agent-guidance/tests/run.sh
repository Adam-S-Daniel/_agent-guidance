#!/usr/bin/env bash
# tests/run.sh: every test for agent-guidance. Prints one line per suite and
# exits 1 if any suite failed. Needs git and python3 with PyYAML.
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
    echo "FAIL $name (exit $rc):"; printf '%s\n' "$out" | grep -v '^ok ' | sed 's/^/    /'
    failed=1
  fi
}

python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is missing (pip install PyYAML==6.0.3)"; exit 1; }
suite "bash -n" bash -c 'for f in scripts/*.sh templates/*.sh templates/agent-checks .githooks/agent-checks tests/*.sh .agent-checks/local.sh; do bash -n "$f" || exit 1; done; echo "all shell scripts parse"'
suite "test-sync.sh" bash scripts/test-sync.sh
suite "test-drift-report.sh" bash tests/test-drift-report.sh
suite "test-kit.sh" bash tests/test-kit.sh
suite "test-skills-bootstrap.sh" bash tests/test-skills-bootstrap.sh
suite "guard cases" python3 tests/guard-test.py templates/agent-checks-guard.sh tests/guard-cases.json
suite "check-workflows.py" python3 tests/check-workflows.py
suite "check-coverage.py" python3 tests/check-coverage.py
exit "$failed"
