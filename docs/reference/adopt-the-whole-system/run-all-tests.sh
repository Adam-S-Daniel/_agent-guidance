#!/usr/bin/env bash
# run-all-tests.sh: run the whole kit's tests and print one PASS/FAIL line per
# suite: starter/run-all-tests.sh (every starter folder),
# bootstrap/tests/test-bootstrap.sh, the export's own tests (export/tests:
# the leak scan, every kit file tracked by git, the suite runner), and
# `export/export.py --check` (each starter tree equals a fresh export of its
# pinned source, as git tracks it). Exits 1 if any failed; a failing suite's
# full output is shown. No network, no sleeps.
#
# `export.py --check` needs the source checkouts beside the repo that holds
# the kit (or KIT_EXPORT_SOURCES); a copy of the kit has none, so there it is
# reported as SKIP, never as a pass. The leak scan's private half reads
# PRIVACY_DENYLIST or PRIVACY_DENYLIST_FILE, and skips without one locally.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")"

logs=$(mktemp -d)
trap 'rm -rf "$logs"' EXIT
failed=0
suite() {  # suite <name> <command...>
  local name=$1 rc summary
  shift
  "$@" > "$logs/$name.log" 2>&1
  rc=$?
  if [ "$rc" = 0 ]; then
    summary=$(grep -E '^trees: |^[0-9]+ assertions passed|^[0-9]+ passed|^checked: ' "$logs/$name.log" | tail -n 1)
    echo "PASS $name: ${summary:-exit 0}"
  elif [ "$rc" = 3 ]; then
    echo "SKIP $name: $(tail -n 1 "$logs/$name.log")"
  else
    failed=1
    echo "FAIL $name (exit $rc):"; sed 's/^/    /' "$logs/$name.log"
  fi
}

# export.py --check, or exit 3 (SKIP) when the source checkouts are not there.
export_check() {
  local sources=${KIT_EXPORT_SOURCES:-$(cd ../../../.. 2>/dev/null && pwd)} missing
  missing=$(python3 -I -B - "$sources" <<'PY'
import pathlib, sys, yaml
sources = pathlib.Path(sys.argv[1])
names = set()
for p in sorted(pathlib.Path("export/manifests").glob("*.yml")):
    if p.name == "_common.yml":
        continue
    m = yaml.safe_load(p.read_text(encoding="utf-8"))
    names.add(m.get("source", m["repo"]))
    names.update(i["repo"] for i in m.get("imports") or [])
print(" ".join(sorted(n for n in names if not (sources / n / ".git").exists())))
PY
  ) || return 1
  if [ -n "$missing" ]; then
    echo "export.py --check not run: no source checkout of $missing under $sources (set KIT_EXPORT_SOURCES)"
    return 3
  fi
  local out
  out=$(python3 -B export/export.py --check 2>&1) || { printf '%s\n' "$out"; return 1; }
  printf '%s\n' "$out"
  echo "checked: $(grep -c '^OK ' <<<"$out") trees match a fresh export"
}

suite starter bash starter/run-all-tests.sh
suite bootstrap bash bootstrap/tests/test-bootstrap.sh
suite export-tests python3 -B -m pytest export/tests -q -p no:cacheprovider
suite export-check export_check
exit "$failed"
