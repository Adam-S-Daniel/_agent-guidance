#!/usr/bin/env bash
# Test-only seed, invented for the adoption kit. Builds, in $WORKSPACE:
#   prod.git   a bare repository holding repo-content/
#   checkout/  a clone of it, origin -> prod.git
# with a fixed identity, fixed dates and no ambient git config, so every
# sha is the same on every machine. prod.git is bare, so it has no nested
# .git marker: the workspace's own `git add -A` would walk its internals as
# plain files, so it is excluded there.
set -euo pipefail
root="${WORKSPACE:-$(pwd -P)}"
[ -d "$root/repo-content" ] || { echo "setup.sh: run me in the workspace" >&2; exit 1; }
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=ops-bot GIT_AUTHOR_EMAIL=ops-bot@example.com
export GIT_COMMITTER_NAME=ops-bot GIT_COMMITTER_EMAIL=ops-bot@example.com
export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
git() { command git -c commit.gpgsign=false -c core.fileMode=true -c core.autocrlf=false "$@"; }

staging="$root/.setup-staging"
rm -rf "$staging" && mkdir -p "$staging"
cp -R "$root/repo-content/." "$staging/"
chmod +x "$staging/scripts/reaper.sh"
git -C "$staging" init -q -b main
git -C "$staging" add -A
git -C "$staging" commit -q -m "initial import"
rm -rf "$root/prod.git"
git init -q --bare -b main "$root/prod.git"
git -C "$staging" push -q "$root/prod.git" main:main
rm -rf "$staging"
rm -rf "$root/checkout"
git clone -q "$root/prod.git" "$root/checkout"
if [ -d "$root/.git" ]; then
  mkdir -p "$root/.git/info"
  echo "/prod.git/" >> "$root/.git/info/exclude"
fi
rm -rf "$root/repo-content"
rm -f "$root/setup.sh"
