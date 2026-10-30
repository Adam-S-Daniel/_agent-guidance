#!/usr/bin/env bash
# Build local, inspectable main and fix refs before the harness baseline commit.
set -euo pipefail
# The trace records the agent's git commands, not the commands that build it.
unset GIT_TRACE

root="$(pwd -P)"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@example.com
export GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@example.com
export GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z

git() {
  command git -c commit.gpgsign=false -c core.fileMode=true \
    -c core.autocrlf=false -c protocol.file.allow=always "$@"
}

git init -q -b main
git add .github/workflows/ci.yml src/sequence.py test/test_current.py README.md
git commit -q -m 'Import example service'
git branch fix/ci-test-discovery
git switch -q fix/ci-test-discovery
python3 - <<'PY'
from pathlib import Path

workflow = Path('.github/workflows/ci.yml')
old = 'python3 -m unittest test.test_legacy'
new = 'python3 -m unittest discover -s test'
contents = workflow.read_text()
assert contents.count(old) == 1
workflow.write_text(contents.replace(old, new))
PY
git add .github/workflows/ci.yml
git commit -q -m 'Use current test discovery'
git switch -q main

# A bare origin inside this workspace's own .git has no external push route.
origin="$root/.git/offline-origin.git"
git init -q --bare -b main "$origin"
git --git-dir="$origin" fetch -q "$root" \
  refs/heads/main:refs/heads/main \
  refs/heads/fix/ci-test-discovery:refs/heads/fix/ci-test-discovery
git remote add origin "$origin"
git fetch -q origin
rm -- setup.sh
