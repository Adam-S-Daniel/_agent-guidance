#!/usr/bin/env bash
# Create or update every label in labels.txt on every repository in $ORG.
set -euo pipefail

: "${ORG:?ORG must name the organization}"
root="$(cd "$(dirname "$0")/.." && pwd)"

mapfile -t REPOS < <(gh repo list "$ORG" --limit 200 --jq '.[].nameWithOwner')
mapfile -t LABELS < "$root/labels.txt"

if [ "${#REPOS[@]}" -eq 0 ]; then
  echo "No repositories found in $ORG; nothing to sync."
  exit 0
fi

for repo in "${REPOS[@]}"; do
  for label in "${LABELS[@]}"; do
    gh label create "$label" --repo "$repo" --force
  done
  echo "Synced ${#LABELS[@]} labels to $repo"
done
