#!/usr/bin/env bash
# Install what .github/workflows/ci.yml's `test` job installs, so a fresh
# machine or cloud session can run this repo's tests. ci.yml is the source of
# truth: test/issues/test_issue_dev_setup.py fails when the two lists drift.
# The script never uses sudo and does not install bubblewrap (bwrap).
#
#   bash scripts/dev_setup.sh                  install everything
#   bash scripts/dev_setup.sh --print-pins     list the pip pins, install nothing
#   bash scripts/dev_setup.sh --print-npm-dirs list the npm dirs, install nothing
#   bash scripts/dev_setup.sh --print-bundler-dirs list the Gemfile dirs, install nothing
set -euo pipefail

PIP_PINS=(
  pyyaml==6.0.3
  markdown-it-py==4.2.0
  tree-sitter==0.26.0
  tree-sitter-bash==0.25.1
  pytest==9.1.1
  pytest-xdist==3.8.0
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The real-work fixtures' directories come from their own `deps:` entries,
# as ci.yml's install steps read them (scaffold_real_work.py deps-dirs, which
# needs PyYAML). Gemfile directories are installed frozen into vendor/bundle
# beside the Gemfile, as ci.yml's "Install real-work fixture gems" step does.
real_work_dirs() {
  local listed
  listed="$(python3 "$REPO_ROOT/scripts/scaffold_real_work.py" deps-dirs --manager "$1")" || return 1
  local dir
  for dir in $listed; do
    printf 'evals/real-work/%s\n' "$dir"
  done
}

USAGE="usage: dev_setup.sh [--print-pins | --print-npm-dirs | --print-bundler-dirs]"
if [ "$#" -gt 1 ]; then
  echo "$USAGE" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  case "$1" in
    --print-pins) printf '%s\n' "${PIP_PINS[@]}"; exit 0 ;;
    --print-npm-dirs) real_work_dirs npm; exit 0 ;;
    --print-bundler-dirs) real_work_dirs bundler; exit 0 ;;
    *) echo "$USAGE" >&2; exit 2 ;;
  esac
fi

# A PEP 668 "externally managed" Python refuses the first form.
if ! python3 -m pip install "${PIP_PINS[@]}"; then
  python3 -m pip install --break-system-packages "${PIP_PINS[@]}"
fi

npm_dirs="$(real_work_dirs npm)"
for dir in $npm_dirs; do
  (cd "$REPO_ROOT/$dir" && npm ci --ignore-scripts --no-audit --no-fund)
done

bundler_dirs="$(real_work_dirs bundler)"
for dir in $bundler_dirs; do
  (cd "$REPO_ROOT/$dir" && BUNDLE_PATH=vendor/bundle BUNDLE_FROZEN=true BUNDLE_DISABLE_SHARED_GEMS=true BUNDLE_APP_CONFIG="$(mktemp -d)" BUNDLE_SILENCE_ROOT_WARNING=true bundle install --quiet)
done

if ! command -v bwrap >/dev/null 2>&1; then
  echo "dev_setup: bubblewrap (bwrap) not found; tests of workspace-executing scorers fail closed without it. CI installs it; see .github/workflows/ci.yml." >&2
fi

echo "dev_setup: done."
echo "Run one module:  python3 -m pytest test/issues/<file>.py -q -n 2      Full suite:  python3 test/run_tests.py --jobs auto"
