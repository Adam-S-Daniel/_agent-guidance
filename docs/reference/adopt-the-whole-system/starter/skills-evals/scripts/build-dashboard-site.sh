#!/usr/bin/env bash
# build-dashboard-site.sh <results-checkout>: refresh the dashboard's datasets
# into dashboard/data/ and say how to serve the page.
#   <results-checkout>  a local checkout of persistent/eval-results (guide 12.1)
# Environment:
#   DASHBOARD_OUT  where the datasets go (default: dashboard/data in this repo)
# The weekly cap comes from evals/budget.yml, validated by harness/budget.py.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
results=${1:-}
if [ -z "$results" ] || [ ! -d "$results" ]; then
  echo "usage: build-dashboard-site.sh <checkout of persistent/eval-results>" >&2
  exit 2
fi
out=${DASHBOARD_OUT:-$root/dashboard/data}

cap=$(cd "$root" && python3 -c 'import sys; from harness.budget import load_budget; print(load_budget(sys.argv[1])["weekly_cap"])' evals/budget.yml)
args=(--results "$results" --out "$out" --weekly-cap "$cap")
if [ -f "$out/manifest.json" ]; then
  args+=(--previous-manifest "$out/manifest.json")
fi
python3 "$root/scripts/dashboard-refresh.py" "${args[@]}"

echo "Datasets written to $out."
if [ "$out" != "$root/dashboard/data" ]; then
  echo "The page reads data/ beside index.html: serve a directory holding dashboard/index.html and this one as data/."
  exit 0
fi
cat <<MSG
Serve the page (fetch() does not work from a file:// URL):
  python3 -m http.server 8000 --bind 127.0.0.1 --directory "$root/dashboard"
then open http://127.0.0.1:8000/
MSG
