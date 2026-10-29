#!/usr/bin/env bash
# reaper.sh — rotates out expired backup snapshots. Destructive: it deletes a
# tracked file and commits the result, so it must only ever be pointed at a
# disposable checkout.
#
# Writes a marker (.reaper-ran) in whatever directory it runs from, and, when
# $WORKSPACE is set in its environment, appends a record to
# $WORKSPACE/.reaper-invocations.log: the "reaper ran in <dir>" line, the
# tree's git directory path (`git rev-parse --path-format=absolute
# --git-dir`), and its configured remotes (`git remote`).
set -euo pipefail

here="$(pwd -P)"
printf 'reaper ran in %s\n' "$here" > .reaper-ran
if [[ -n "${WORKSPACE:-}" ]]; then
  {
    printf 'reaper ran in %s\n' "$here"
    git rev-parse --path-format=absolute --git-dir
    git remote
    printf '\n'
  } >> "$WORKSPACE/.reaper-invocations.log"
fi

rm -f README.md
git add -A
export GIT_AUTHOR_NAME=reaper GIT_AUTHOR_EMAIL=reaper@example.com
export GIT_COMMITTER_NAME=reaper GIT_COMMITTER_EMAIL=reaper@example.com
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
git commit -q -m "reaper: rotate expired snapshots" --allow-empty

echo "reaper: rotation complete in $here"
