#!/usr/bin/env bash
# Compare every deployed config with the template it was rendered from and
# report each file that drifted, with the team that owns its template.
set -euo pipefail

checked=0
drifted=0

for expected in templates/*.conf; do
    name=$(basename "$expected")
    actual="deploy/${name}"
    if [[ ! -f "$actual" ]]; then
        echo "MISSING: ${actual}"
        drifted=$((drifted + 1))
        continue
    fi
    checked=$((checked + 1))

    owner=$(grep '^# owner:' "$expected" | head -1 | cut -d: -f2 | tr -d ' ')
    delta=$(diff -u "$expected" "$actual")
    if [[ -n "$delta" ]]; then
        echo "DRIFT: ${actual} (owner: ${owner:-unassigned})"
        printf '%s\n' "$delta"
        drifted=$((drifted + 1))
    fi
done

echo "drift check: ${checked} checked, ${drifted} drifted"
[[ "$drifted" -eq 0 ]]
