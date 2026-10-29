#!/usr/bin/env bash
# tests/run.sh: the cloud setup script's tests. Prints one line and exits 1 on
# failure. Reads ../machine/install-agent-hooks.sh unless INSTALL_AGENT_HOOKS
# names the installer (do that once this folder lives in another repo).
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")/.."

out=$(bash tests/test-setup-script.sh 2>&1); rc=$?
if [ "$rc" = 0 ]; then
  echo "PASS test-setup-script.sh: $(tail -n 1 <<<"$out")"
else
  echo "FAIL test-setup-script.sh (exit $rc):"; printf '%s\n' "$out" | sed 's/^/    /'
fi
exit "$rc"
