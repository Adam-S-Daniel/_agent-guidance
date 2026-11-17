#!/usr/bin/env bash
# check-actions-lock-helper.sh — run the REAL `gh actions-lock --no-fix` against
# what scripts/actions-lock-entry.sh produces, on vendored real lockfiles.
#
# The suite's own lk_verify models the tool's rule with the helper's own
# assumptions, so it cannot catch the helper and the model agreeing on a wrong
# rule (a composite action's transitive dependency being pruned was such a
# case). This script asks the tool itself.
#
# For each directory under test/fixtures/actions-lock-real/ (a copy of a real
# repo's .github/workflows as the sync found it, lock included):
#   1. baseline: the fixture as vendored is valid (proves the tool ran and
#      could reach GitHub, so a later failure is not an outage);
#   2. negative control: with the sync's workflow template added and the
#      lockfile NOT edited, the tool rejects it (not-pinned or stale);
#   3. with the helper's `apply` run on that lockfile, the tool accepts it.
# `gh actions-lock --no-fix` resolves every pin against GitHub, so it needs the
# network and a token (GH_TOKEN); it writes nothing to the checkout.
# Needs: gh with the actions-lock extension and jq.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
helper="$root/scripts/actions-lock-entry.sh"
template="$root/templates/dependabot-auto-merge/dependabot-auto-merge.yml"
fixtures="$root/test/fixtures/actions-lock-real"
wf=".github/workflows/dependabot-auto-merge.yml"
lock=".github/workflows/actions.lock"

gh actions-lock --version > /dev/null 2>&1 || { echo "::error::gh actions-lock is not installed"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# lock_json <dir>: the tool's read-only verdict as JSON; any other exit than a
# parsed verdict is an error of its own (network, auth), never a "finding".
lock_json() {
    local out
    out=$(cd "$1" && GH_ACTIONS_LOCK_REPO_ROOT="$1" gh actions-lock --no-fix --no-interactive --json=valid,findings 2>/dev/null) || true
    jq -e 'type == "object" and has("valid")' <<< "$out" > /dev/null 2>&1 || { echo "gh actions-lock gave no verdict in $1" >&2; return 2; }
    if jq -e 'any(.findings[]?; .category == "reachability-unknown" or .retryable == true)' <<< "$out" > /dev/null; then
        echo "gh actions-lock could not reach GitHub for $1" >&2; return 2
    fi
    printf '%s\n' "$out"
}

failed=0
n=0
for fx in "$fixtures"/*/; do
    name=$(basename "$fx")
    n=$((n + 1))
    d="$work/$name"
    mkdir -p "$d"
    cp -R "$fx". "$d"/

    out=$(lock_json "$d") || { echo "FAIL $name: baseline could not run"; failed=1; continue; }
    if [[ "$(jq -r .valid <<< "$out")" != true ]]; then
        echo "FAIL $name: the vendored fixture is not valid before the sync"; failed=1; continue
    fi

    cp "$template" "$d/$wf"
    out=$(lock_json "$d") || { echo "FAIL $name: negative control could not run"; failed=1; continue; }
    if [[ "$(jq -r .valid <<< "$out")" == true ]]; then
        echo "FAIL $name: negative control — the tool accepted the workflow without a lock edit, so this check proves nothing"
        failed=1; continue
    fi

    rc=0; res=$("$helper" apply "$d/$lock" "$wf") || rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "FAIL $name: the helper refused the real lockfile (exit $rc)"; failed=1; continue
    fi
    out=$(lock_json "$d") || { echo "FAIL $name: post-helper check could not run"; failed=1; continue; }
    if [[ "$(jq -r .valid <<< "$out")" == true ]]; then
        echo "PASS $name: helper said $res; gh actions-lock --no-fix accepts it (and rejected the unedited lockfile)"
    else
        echo "FAIL $name: gh actions-lock --no-fix rejects the helper's lockfile:"
        jq -r '.findings[]? | "  \(.category): \(.dependency // .workflow // "-"): \(.detail)"' <<< "$out"
        failed=1
    fi
done

[[ $n -gt 0 ]] || { echo "FAIL: no fixtures under $fixtures"; exit 1; }
echo "checked $n fixture(s), failed: $failed"
exit "$failed"
