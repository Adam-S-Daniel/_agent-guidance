#!/usr/bin/env bash
# run-all-tests.sh: run every export's own test suite and print one PASS/FAIL
# line per tree, then the totals. Exits 1 if any tree failed. Each tree runs
# through ../export/run_suites.py, which copies it to a temporary directory and
# runs the source repo's own setup and test command (all trees at once) (its manifest's suite:
# block) in a PID namespace. Failures listed in known-failures.txt, by exact
# id, are excused only when they account for the whole failure; anything else
# fails the tree. A failing tree's log is shown.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")"

trees=(agent-guidance repo-settings agent-skills agent-skills-private skills-evals wsl-automation)
logs=$(mktemp -d)
trap 'rm -rf "$logs"' EXIT
passed=0 failed=0
# Start every tree at once, then report in a fixed order. Each tree runs in its
# own temporary copy and PID namespace, so they do not share state.
for t in "${trees[@]}"; do
  if ! grep -qxE "kit_name: $t" ../export/manifests/*.yml 2>/dev/null; then
    echo 2 > "$logs/$t.rc"; echo "no manifest names it" > "$logs/$t.log"; continue
  fi
  ( python3 ../export/run_suites.py --kit-name "$t" --known-failures known-failures.txt > "$logs/$t.log" 2>&1
    echo $? > "$logs/$t.rc" ) &
done
wait
for t in "${trees[@]}"; do
  rc=$(cat "$logs/$t.rc" 2>/dev/null || echo 1)
  if [ "$rc" = 0 ]; then
    passed=$((passed + 1)); sed -n "s/^PASS $t: /PASS $t: /p; /^    known failure/p; /^    listed as known/p" "$logs/$t.log"
  else
    failed=$((failed + 1)); echo "FAIL $t (exit $rc):"; sed 's/^/    /' "$logs/$t.log"
  fi
done
echo "trees: $passed passed, $failed failed, ${#trees[@]} total"
[ "$failed" = 0 ]
