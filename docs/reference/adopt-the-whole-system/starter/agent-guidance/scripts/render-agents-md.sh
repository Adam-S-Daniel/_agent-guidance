#!/usr/bin/env bash
# render-agents-md.sh <stub.md> <path/to/AGENTS.md>
# Rewrites the managed half of a repo's AGENTS.md from the stub and keeps
# everything from the "## Repo-specific additions" heading LINE down.
set -euo pipefail
stub=$1 target=$2
marker='## Repo-specific additions'
if grep -qxF -- "$marker" "$stub"; then
  echo "render-agents-md: the stub must not contain the marker line" >&2; exit 1
fi
tmp=$(mktemp)
cat "$stub" > "$tmp"
if [ -f "$target" ] && grep -qxF -- "$marker" "$target"; then
  echo >> "$tmp"
  awk -v m="$marker" 'found || $0 == m { found = 1; print }' "$target" >> "$tmp"
else
  printf '\n%s\n' "$marker" >> "$tmp"
  if [ -s "$target" ]; then echo >> "$tmp"; cat "$target" >> "$tmp"; fi
fi
mv "$tmp" "$target"
