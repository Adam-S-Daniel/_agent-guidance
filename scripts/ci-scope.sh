#!/usr/bin/env bash
set -euo pipefail
#
# ci-scope.sh — Decide how much of CI a change needs.
#
# ci.yml's `test` is the one required check, so its jobs cannot use
# `on.paths` (a check that never reports blocks the merge). Instead each
# `suite` shard runs this first and gates its later steps on the answer, so a
# shard with nothing to test still reports success. `checks` does not use it:
# its gates are cheap and other tests pin them as always-on.
#
# Prints one line, `scope=<value>`:
#   full      run everything. Any file outside docs/, any docs file a
#             non-docs file names, the adoption kit, an unreadable range,
#             an empty diff, or any event but pull_request/push.
#   none      every changed file is a doc nothing outside docs/ reads.
#   <groups>  space-separated test groups. Every changed file is either an
#             unread doc or a doc listed in DATA_FILES below, whose readers
#             are all known and are tested by those groups.
#
# A docs file counts as read when its BASENAME appears in any tracked file
# outside docs/. Matching the basename, not the path, errs toward running:
# a reader that builds the path in pieces still names the file.
#
# With MATRIX_GROUPS set (a suite shard's own groups), it also prints
# `run=true|false`: true when scope is full, or names a group in MATRIX_GROUPS.
# Without it, `run` is true only for full scope.
#
# Usage: ci-scope.sh [<repo dir>]   (default: the current directory)
# Reads GITHUB_EVENT_NAME and GITHUB_EVENT_PATH; shas come from the event
# file, never from an interpolated expression.

repo="${1:-.}"

# A doc that a script reads as data, the files allowed to name it, and the
# test groups that cover those files. If any OTHER non-docs file names it,
# the change is full scope again, so this list cannot go stale silently.
#   <path>|<allowed readers, space-separated>|<groups>
DATA_FILES=(
    # This script and test/run-tests.sh name the ledger too, as table and
    # fixture; neither reads it.
    "docs/agent-checks-lessons.md|scripts/agent_check_lane.py test/test_agent_check_lane.py scripts/ci-scope.sh test/run-tests.sh|agent_checks"
)

emit() {
    local scope="$1" run=false g
    if [[ "$scope" == full ]]; then
        run=true
    elif [[ -n "${MATRIX_GROUPS:-}" && "$scope" != none ]]; then
        for g in $scope; do
            if [[ " $MATRIX_GROUPS " == *" $g "* ]]; then run=true; fi
        done
    fi
    echo "scope=$scope"
    echo "run=$run"
    exit 0
}

case "${GITHUB_EVENT_NAME:-}" in
    pull_request)
        base=$(jq -r '.pull_request.base.sha // empty' "${GITHUB_EVENT_PATH:-/dev/null}" 2>/dev/null) || base=""
        head=$(jq -r '.pull_request.head.sha // empty' "${GITHUB_EVENT_PATH:-/dev/null}" 2>/dev/null) || head="" ;;
    push)
        base=$(jq -r '.before // empty' "${GITHUB_EVENT_PATH:-/dev/null}" 2>/dev/null) || base=""
        head=$(jq -r '.after // empty' "${GITHUB_EVENT_PATH:-/dev/null}" 2>/dev/null) || head="" ;;
    *) emit full ;;
esac

zero=0000000000000000000000000000000000000000
if ! [[ "$base" =~ ^[0-9a-f]{40}$ && "$head" =~ ^[0-9a-f]{40}$ && "$base" != "$zero" ]]; then
    emit full
fi
from=$(git -C "$repo" merge-base "$base" "$head" 2>/dev/null) || emit full
changed=$(git -C "$repo" diff --no-renames --name-only "$from" "$head" 2>/dev/null) || emit full
[[ -n "$changed" ]] || emit full

groups=""
while IFS= read -r f; do
    [[ "$f" == docs/* ]] || emit full
    [[ "$f" == docs/reference/adopt-the-whole-system/* ]] && emit full
    name="${f##*/}"
    # Tracked files at the head commit, outside docs/, that name this file.
    readers=$(git -C "$repo" grep -lF -e "$name" "$head" -- . ':(exclude)docs/' 2>/dev/null \
        | sed "s|^$head:||") || readers=""
    [[ -z "$readers" ]] && continue
    match=""
    for entry in "${DATA_FILES[@]}"; do
        IFS='|' read -r path allowed covers <<<"$entry"
        [[ "$path" == "$f" ]] || continue
        match=1
        while IFS= read -r r; do
            [[ " $allowed " == *" $r "* ]] || emit full
        done <<<"$readers"
        groups="$groups $covers"
    done
    [[ -n "$match" ]] || emit full
done <<<"$changed"

groups=$(tr ' ' '\n' <<<"$groups" | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ $//')
emit "${groups:-none}"
