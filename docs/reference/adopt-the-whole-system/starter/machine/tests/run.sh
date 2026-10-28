#!/usr/bin/env bash
# tests/run.sh: every test for the per-machine files. Prints one line per
# suite and exits 1 if any failed. Needs git (2.54+ for the end-to-end hook
# part), python3 with PyYAML.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")/.."

failed=0
suite() {  # suite <name> <command...>
  local name=$1 out rc; shift
  out=$("$@" 2>&1); rc=$?
  if [ "$rc" = 0 ]; then
    echo "PASS $name: $(tail -n 1 <<<"$out")"
    grep '^SKIP' <<<"$out" | sed 's/^/    /'
  else
    echo "FAIL $name (exit $rc):"; printf '%s\n' "$out" | sed 's/^/    /'
    failed=1
  fi
}

suite "bash -n" bash -c 'bash -n install-agent-hooks.sh && bash -n tests/test-install-agent-hooks.sh && echo "all shell scripts parse"'
suite "user-settings.json parses" python3 -c 'import json; d=json.load(open("user-settings.json")); assert d["disableSkillShellExecution"] is True and d["hooks"]["Stop"]; print("1 assertion passed")'
suite "test-install-agent-hooks.sh" bash tests/test-install-agent-hooks.sh
suite "test_memory_home.py" python3 tests/test_memory_home.py
exit "$failed"
