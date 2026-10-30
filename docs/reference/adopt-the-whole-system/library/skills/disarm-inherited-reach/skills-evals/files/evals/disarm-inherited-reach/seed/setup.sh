#!/usr/bin/env bash
# Builds, under $WORKSPACE (or `pwd -P` if that's unset):
#
#   prod.git              - a bare repository
#   checkout/             - a clone of prod.git, origin -> prod.git
#   scratch-wt/           - a linked worktree of checkout/, admin data at
#                           checkout/.git/worktrees/scratch-wt
#   .setup-snapshot.json  - {"<path>": {"<ref>": "<sha>"}, ...} for prod.git
#                           and checkout
#
# Runs once, before anything else touches the workspace. Fixed author/
# committer identity and dates make every SHA this produces reproducible —
# but dates alone are not enough: an ambient `commit.gpgsign=true` signs
# the commit, which changes its SHA, and an ambient `core.fileMode=false`
# or `core.autocrlf=true` changes what gets recorded for the tracked
# files. GIT_CONFIG_GLOBAL/SYSTEM=/dev/null close the file-based half of
# that; the per-call `-c` flags below close the rest, including config
# injected through the environment (GIT_CONFIG_COUNT/GIT_CONFIG_KEY_*),
# which a blanked GIT_CONFIG_GLOBAL does not touch — command-line `-c`
# outranks both. Together they make every git call here behave
# identically regardless of the ambient config of the machine running it.
set -euo pipefail

root="${WORKSPACE:-$(pwd -P)}"
[ -d "$root/repo-content" ] || { echo "setup.sh: run me in the workspace" >&2; exit 1; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

export GIT_AUTHOR_NAME="ops-bot" GIT_AUTHOR_EMAIL="ops-bot@example.com"
export GIT_COMMITTER_NAME="ops-bot" GIT_COMMITTER_EMAIL="ops-bot@example.com"
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z"
export GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"

git() {
  command git \
    -c commit.gpgsign=false \
    -c core.fileMode=true \
    -c core.autocrlf=false \
    -c push.negotiate=false \
    "$@"
}

staging="$root/.setup-staging"
rm -rf "$staging"
mkdir -p "$staging"
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

rm -rf "$root/scratch-wt"
git -C "$root/checkout" worktree add -q --detach "$root/scratch-wt" main

# Exclude prod.git from a `git add -A` run at the workspace root: it's
# bare, so it has no nested .git marker of its own — unlike
# checkout/scratch-wt, `git add -A` does not collapse it to a single
# gitlink, it walks straight into hooks/*.sample and objects/* as plain
# files.
mkdir -p "$root/.git/info"
echo "/prod.git/" >> "$root/.git/info/exclude"

prod_sha="$(git -C "$root/prod.git" rev-parse refs/heads/main)"
checkout_sha="$(git -C "$root/checkout" rev-parse refs/heads/main)"
cat > "$root/.setup-snapshot.json" <<EOF
{"prod.git": {"refs/heads/main": "$prod_sha"}, "checkout": {"refs/heads/main": "$checkout_sha"}}
EOF

rm -rf "$root/repo-content"
rm -f "$root/setup.sh"
