#!/usr/bin/env bash
# .agent-checks/local.sh: this repo's own commit-time rules, run by
# .githooks/agent-checks after the fleet's rules (the sync never overwrites
# this file). The runner passes its arguments through: "pre-commit",
# "pre-push", "ci <base> <head>" or "tree [<rev>]".
#
# A TEMPLATE: as shipped its path loop refuses nothing. Add one case arm per
# rule below. Each refusal says what is wrong, why, and how to fix it. After
# the loop sits this repo's one rule, which is a no-op in a repo without
# templates/: its own copies of the agent-checks files equal their templates.
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

# agent-guidance only: its own live copies of the delivered agent-checks files
# (the sync skips this repo) must equal their templates, mode included, in
# what is being checked: the index for pre-commit, the head for ci, the rev
# for tree. scripts/update-own-copies.sh writes them.
own_copy() { # <spec for git rev-parse, or "" for the index> <path>: "<mode> <blob>"
  if [ -z "$1" ]; then git ls-files -s -- "$2" | awk '{print $1, $2}'
  else git ls-tree "$1" -- "$2" | awk '{print $1, $3}'; fi
}
case "${1:-}" in
  pre-commit) at="" ;;
  ci) at=$3 ;;
  tree) at=${2:-HEAD} ;;
esac
for pair in templates/agent-checks:.githooks/agent-checks templates/check-privacy.py:.githooks/check-privacy.py \
            templates/agent-checks.yml:.github/workflows/agent-checks.yml; do
  src=${pair%%:*} dest=${pair#*:}
  if [ "$(own_copy "$at" "$src")" != "$(own_copy "$at" "$dest")" ]; then
    echo "agent-checks: $dest is not byte for byte $src. This repo's own copy is what its required checks run. Run scripts/update-own-copies.sh and commit both." >&2
    fail=1
  fi
done
exit "$fail"
