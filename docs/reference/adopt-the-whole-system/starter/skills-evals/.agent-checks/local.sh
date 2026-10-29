#!/usr/bin/env bash
# .agent-checks/local.sh: this repo's own commit-time rules, run by
# .githooks/agent-checks after the fleet's rules (the sync never overwrites
# this file). The runner passes its arguments through: "pre-commit",
# "pre-push", "ci <base> <head>" or "tree [<rev>]".
#
# A TEMPLATE: as shipped it lists the paths and refuses nothing. Add one case
# arm per rule below. Each refusal says what is wrong, why, and how to fix it.
# A rule about code SHAPE parses the file with a real parser; a case on the
# path is for path rules only.
set -uo pipefail
fail=0
case "${1:-}" in
  pre-commit) lister=(git diff --cached -z --name-only --diff-filter=ACMR) ;;
  ci) lister=(git diff -z --name-only --diff-filter=ACMR "$2...$3") ;;
  tree) lister=(git ls-tree -z -r --name-only "${2:-HEAD}") ;;
  *) exit 0 ;;  # pre-push: the runner has read the ref lines; CI covers the push
esac
# NUL-separated (-z) so any file name survives; captured first, so a failed git
# command fails the run instead of reading as "no paths".
list=$(mktemp) || exit 1
trap 'rm -f "$list"' EXIT
"${lister[@]}" > "$list" || { echo "agent-checks: local.sh could not list paths" >&2; exit 1; }
while IFS= read -r -d '' p; do
  case "$p" in
    # EXAMPLE (uncomment the next line to refuse merge leftovers):
    # *.orig|*.rej) echo "agent-checks: $(printf '%q' "$p") is a merge or patch leftover. It is never source. Delete it (git rm --cached -- $(printf '%q' "$p"))." >&2; fail=1 ;;  # local-example: enable
    *) ;;
  esac
done < "$list"
exit "$fail"
