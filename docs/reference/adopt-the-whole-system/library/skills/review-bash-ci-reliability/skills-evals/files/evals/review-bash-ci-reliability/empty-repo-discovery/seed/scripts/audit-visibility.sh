#!/usr/bin/env bash
# Flag every public example-org repository that is not on the allowlist.
# An organization with no public repositories has nothing to audit.
set -euo pipefail

ORG="example-org"
ALLOWLIST="config/public-allowlist.txt"

mapfile -t REPOS < <(gh repo list "$ORG" --visibility public --json name --jq '.[].name' --limit 500)

if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "No public repositories in ${ORG}; nothing to audit."
    exit 0
fi

unexpected=0
for repo in "${REPOS[@]}"; do
    if ! grep -qxF -- "$repo" "$ALLOWLIST"; then
        echo "UNEXPECTED PUBLIC: ${ORG}/${repo}"
        unexpected=$((unexpected + 1))
    fi
done

echo "audited ${#REPOS[@]} public repositories, ${unexpected} unexpected"
[[ "$unexpected" -eq 0 ]]
