#!/usr/bin/env bash
# Rebuild templates/ and deploy/ for one named case, run the drift check the
# workflow runs, and exit 0 only when its exit code and output are right.
set -uo pipefail

case_name=${1:?usage: drift.sh CASE}

rm -rf templates deploy
mkdir templates deploy

template() {  # template NAME OWNER(or -) LINES...
    local name=$1 owner=$2
    shift 2
    {
        [[ "$owner" == "-" ]] || printf '# owner: %s\n' "$owner"
        printf '%s\n' "$@"
    } > "templates/${name}.conf"
}
in_sync() { cp "templates/$1.conf" "deploy/$1.conf"; }
drifted() { { cat "templates/$1.conf"; echo "hand_edit = true"; } > "deploy/$1.conf"; }

case "$case_name" in
    every-drifted-file-reported)
        template alpha web-team "listen = 8080"; drifted alpha
        template beta data-team "queue = jobs"; in_sync beta
        template gamma data-team "ttl_seconds = 300"; drifted gamma ;;
    unowned-template-not-fatal)
        template alpha web-team "listen = 8080"; in_sync alpha
        template beta - "queue = jobs"; in_sync beta
        template gamma data-team "ttl_seconds = 300"; in_sync gamma ;;
    clean-tree-passes)
        template alpha web-team "listen = 8080"; in_sync alpha
        template beta data-team "queue = jobs"; in_sync beta
        template gamma data-team "ttl_seconds = 300"; in_sync gamma ;;
    drift-fails-the-job)
        template alpha web-team "listen = 8080"; in_sync alpha
        template beta data-team "queue = jobs"; drifted beta ;;
    missing-file-fails-the-job)
        template alpha web-team "listen = 8080"; in_sync alpha
        template beta data-team "queue = jobs"
        template gamma data-team "ttl_seconds = 300"; in_sync gamma ;;
    *) echo "unknown case: ${case_name}" >&2; exit 2 ;;
esac

out=$(bash scripts/check-drift.sh 2>&1)
code=$?
has() { grep -qE -- "$1" <<<"$out"; }

case "$case_name" in
    every-drifted-file-reported)
        [[ "$code" -ne 0 ]] \
            && has '^DRIFT: deploy/alpha\.conf' \
            && has '^DRIFT: deploy/gamma\.conf' \
            && ! has '^DRIFT: deploy/beta\.conf' \
            && has '^drift check: 3 checked, 2 drifted' ;;
    unowned-template-not-fatal | clean-tree-passes)
        [[ "$code" -eq 0 ]] && has '^drift check: 3 checked, 0 drifted' ;;
    drift-fails-the-job)
        [[ "$code" -ne 0 ]] ;;
    missing-file-fails-the-job)
        [[ "$code" -ne 0 ]] && has '^MISSING: deploy/beta\.conf' ;;
esac
