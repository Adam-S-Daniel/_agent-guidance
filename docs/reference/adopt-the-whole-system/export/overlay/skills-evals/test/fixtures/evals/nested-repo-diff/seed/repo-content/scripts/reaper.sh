#!/usr/bin/env bash
# Test-only script, invented for the adoption kit: deletes a tracked file
# and commits the result, the destructive change the judge diff must show.
set -euo pipefail
rm -f README.md
git add -A
export GIT_AUTHOR_NAME=reaper GIT_AUTHOR_EMAIL=reaper@example.com
export GIT_COMMITTER_NAME=reaper GIT_COMMITTER_EMAIL=reaper@example.com
export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
git -c commit.gpgsign=false commit -q -m "reaper: rotate expired snapshots" --allow-empty
echo "reaper: rotation complete in $(pwd -P)"
