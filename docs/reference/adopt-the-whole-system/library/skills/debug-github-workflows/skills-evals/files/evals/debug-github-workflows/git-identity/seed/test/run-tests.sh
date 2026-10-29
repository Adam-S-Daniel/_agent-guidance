#!/usr/bin/env bash
# Build a scratch repository with a known history, then run the unit tests
# against it.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
echo "Testing changelog at $(git -C "$root" describe --tags --always)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
repo="$work/history"

echo "Building a scratch repository for the changelog tests"
git init --quiet "$repo"
commit() {
  git -C "$repo" commit --quiet --allow-empty -m "$1" > /dev/null 2>&1
}
commit "feat: add the export command"
commit "chore: tidy the build"
commit "fix: keep empty sections out"

cd "$root"
PYTHONDONTWRITEBYTECODE=1 CHANGELOG_REPO="$repo" python3 -m unittest discover -s test
