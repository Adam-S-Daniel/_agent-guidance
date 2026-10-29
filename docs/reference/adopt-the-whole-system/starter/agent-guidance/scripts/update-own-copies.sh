#!/usr/bin/env bash
# update-own-copies.sh [--check]: agent-guidance's own live copies of the
# delivered agent-checks files (guide 4.5, 6.3).
#
# The sync skips this repo (SYNC_SELF_REPO), yet phase 4 makes agent-checks
# and privacy required here too, so the repo carries its own copies of:
#
#   templates/agent-checks      -> .githooks/agent-checks
#   templates/check-privacy.py  -> .githooks/check-privacy.py
#   templates/agent-checks.yml  -> .github/workflows/agent-checks.yml
#
# Edit templates/ (the guard allows it; it refuses edits under .githooks/),
# then run this script and commit both. With --check it changes nothing and
# exits 1 naming each copy that differs from its template. .agent-checks/
# local.sh refuses a commit whose copies differ, so they cannot drift.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PAIRS=(
  "templates/agent-checks:.githooks/agent-checks"
  "templates/check-privacy.py:.githooks/check-privacy.py"
  "templates/agent-checks.yml:.github/workflows/agent-checks.yml"
)
check=0
[ "${1:-}" = --check ] && check=1
[ $# -le 1 ] && { [ $# = 0 ] || [ "$check" = 1 ]; } || { echo "usage: update-own-copies.sh [--check]" >&2; exit 2; }
differ=0
for pair in "${PAIRS[@]}"; do
  src=${pair%%:*} dest=${pair#*:}
  if cmp -s "$src" "$dest" && { [ -x "$src" ] && [ -x "$dest" ] || { [ ! -x "$src" ] && [ ! -x "$dest" ]; }; }; then continue; fi
  if [ "$check" = 1 ]; then
    echo "update-own-copies: $dest differs from $src (run scripts/update-own-copies.sh)"; differ=1
  else
    mkdir -p "$(dirname "$dest")"
    cp -p "$src" "$dest"
    echo "update-own-copies: wrote $dest"
  fi
done
exit "$differ"
