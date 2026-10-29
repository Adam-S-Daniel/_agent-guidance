#!/usr/bin/env bash
# Run the nightly audit against a stand-in gh in one named mode, and exit 0
# only when its exit code and output are right for that mode.
set -uo pipefail

case_name=${1:?usage: audit.sh CASE}

audit() {
    out=$(GH_STUB_MODE=$1 PATH="${PWD}/.probe/bin:${PATH}" bash scripts/audit-visibility.sh 2>&1)
    code=$?
}
has() { grep -qE -- "$1" <<<"$out"; }

case "$case_name" in
    gh-failure-fails)
        audit fail
        [[ "$code" -ne 0 ]] && ! has 'nothing to audit' ;;
    partial-listing-fails)
        audit partial
        [[ "$code" -ne 0 ]] ;;
    empty-org-passes)
        audit empty
        [[ "$code" -eq 0 ]] && ! has '^UNEXPECTED PUBLIC' ;;
    allowed-repos-pass)
        audit allowed
        [[ "$code" -eq 0 ]] && ! has '^UNEXPECTED PUBLIC' ;;
    unexpected-repo-flagged)
        audit unexpected
        [[ "$code" -ne 0 ]] \
            && has '^UNEXPECTED PUBLIC: example-org/internal-tools$' \
            && ! has '^UNEXPECTED PUBLIC: example-org/(docs-site|widgets)$' ;;
    *) echo "unknown case: ${case_name}" >&2; exit 2 ;;
esac
