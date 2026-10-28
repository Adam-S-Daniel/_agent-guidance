#!/usr/bin/env bash
# list_fires.sh <window start>: print, as JSON, every dispatch of
# routine-eval-fire.yml since <window start> (YYYY-MM-DDTHH:MM:SSZ), and
# whether it got as far as the "Fire the routine" step (a run that validation
# or the guard refused did not fire and is not charged). This is the
# --fires file allowance_guard.py reads:
#   [{"name": "fire eval <run id>", "created_at": "...", "fired": true}, ...]
# Needs gh (GH_TOKEN with actions: read) and jq; GITHUB_REPOSITORY names the repo.
set -euo pipefail
start=${1:-}
[[ $start =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
  || { echo "list_fires: the window start must be YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2; }
[[ ${GITHUB_REPOSITORY:-} =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] \
  || { echo "list_fires: GITHUB_REPOSITORY is not owner/repo" >&2; exit 2; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
gh api -X GET --paginate "repos/$GITHUB_REPOSITORY/actions/workflows/routine-eval-fire.yml/runs" \
  -f created=">=$start" -f per_page=100 \
  --jq '.workflow_runs[] | [.id, .display_title, .created_at] | @tsv' > "$tmp/runs.tsv"
: > "$tmp/fires.ndjson"
while IFS=$'\t' read -r id name created; do
  [[ $id =~ ^[0-9]+$ ]] || { echo "list_fires: a run id is not a number" >&2; exit 2; }
  fired=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$id/jobs" \
    --jq '[.jobs[].steps[]? | select(.name == "Fire the routine") | .conclusion]
          | any(. != null and . != "skipped")')
  jq -n --arg n "$name" --arg c "$created" --argjson f "$fired" \
    '{name: $n, created_at: $c, fired: $f}' >> "$tmp/fires.ndjson"
done < "$tmp/runs.tsv"
jq -s . "$tmp/fires.ndjson"
