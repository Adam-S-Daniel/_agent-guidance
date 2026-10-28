#!/usr/bin/env bash
# tests/run.sh: every test for repo-settings. Prints one line per suite and
# exits 1 if any failed. Needs jq and python3 with PyYAML.
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

python3 -c 'import yaml' 2>/dev/null || { echo "FAIL: PyYAML is missing (pip install PyYAML==6.0.3)"; exit 1; }
suite "bash -n" bash -c 'for f in bin/apply-rulesets.sh bin/lib/*.sh tests/run.sh; do bash -n "$f" || exit 1; done; echo "all shell scripts parse"'
suite "rulesets parse" bash -c 'n=0; for f in rulesets/*.json rulesets/phase4/*.json; do jq -e "type == \"object\"" "$f" >/dev/null || { echo "$f: not a JSON object"; exit 1; }; n=$((n + 1)); done; echo "$n files parse"'
suite "check-workflow.py" python3 tests/check-workflow.py
exit "$failed"
