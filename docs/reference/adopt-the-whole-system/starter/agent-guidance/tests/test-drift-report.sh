#!/usr/bin/env bash
# test-drift-report.sh: scripts/drift-report.sh against local bare repos, the
# way scripts/test-sync.sh tests sync.sh: remotes are file:// repos reached
# through url.insteadOf in a scratch GIT_CONFIG_GLOBAL, no network, no clock.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
OWNER=acme-example
pass=0 fail=0
check() { local d=$1; shift; if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi; }
has() { grep -qF -- "$2" <<<"$1"; }

export GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_NOSYSTEM=1
git config --global user.name tester
git config --global user.email tester@example.com
git config --global init.defaultBranch main
git config --global "url.file://$T/remotes/.insteadOf" https://github.com/
mkdir -p "$T/bin"
printf '#!/usr/bin/env bash\necho "gh stub: no call expected: $*" >&2; exit 1\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"

# The guidance repo, with the real sync.sh and a real template set.
G=$T/guidance
mkdir -p "$G/agents-md" "$G/templates" "$G/scripts"
printf '# Stub\nManaged.\n' > "$G/agents-md/stub.md"
printf '# Base\nRule.\n' > "$G/agents-md/base.md"
printf '#!/usr/bin/env bash\necho fleet-memory\n' > "$G/templates/fleet-memory.sh"
cp "$ROOT/scripts/sync.sh" "$ROOT/scripts/render-agents-md.sh" "$ROOT/scripts/drift-report.sh" "$G/scripts/"
git -C "$G" init -q && git -C "$G" add -A
GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000" git -C "$G" commit -q -m v1

mk_remote() {
  git init -q --bare "$T/remotes/$OWNER/$1.git"
  git clone -q "$T/remotes/$OWNER/$1.git" "$T/w-$1" 2>/dev/null
  printf '# %s\n' "$1" > "$T/w-$1/README.md"
  git -C "$T/w-$1" add -A && git -C "$T/w-$1" commit -q -m first && git -C "$T/w-$1" push -q origin main
}
for r in agent-guidance app-new app-synced; do mk_remote "$r"; done
# app-synced: bring it up to date with a real (non-dry) sync, then merge the branch.
printf 'repos:\n  - app-synced\n' > "$T/one.yml"
printf '#!/usr/bin/env bash\ncase "$1 $2" in "pr list") exit 0;; "pr create") echo https://github.com/x/y/pull/1;; esac\nexit 0\n' > "$T/bin/gh-sync"; chmod +x "$T/bin/gh-sync"
mkdir -p "$T/bin-sync"; cp "$T/bin/gh-sync" "$T/bin-sync/gh"
PATH="$T/bin-sync:$PATH" SYNC_OWNER=$OWNER SYNC_SRC=$G SYNC_REPOS_FILE=$T/one.yml bash "$G/scripts/sync.sh" >/dev/null 2>&1
git --git-dir="$T/remotes/$OWNER/app-synced.git" update-ref refs/heads/main refs/heads/guidance-sync/update
printf 'repos:\n  - agent-guidance\n  - app-new\n  - app-synced\n  - app-missing\n' > "$T/repos.yml"

before=$(for r in app-new app-synced; do git --git-dir="$T/remotes/$OWNER/$r.git" for-each-ref; done)
run() { OUT=$(PATH="$T/bin:$PATH" SYNC_OWNER=$OWNER SYNC_SRC=$G SYNC_REPOS_FILE=$T/repos.yml \
              GH_TOKEN="ghs_""FAKE0000000000000000000000000000000000" bash "$G/scripts/drift-report.sh" "$@" 2>&1); RC=$?; }

run "$T/out1"
check "report: exits 0 with drift present" test "$RC" = 0
R=$T/out1/report.json
check "report: names each repo's state" python3 -I - "$R" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
states = {x["repo"]: x["state"] for x in r["repos"]}
assert states == {"acme-example/agent-guidance": "skipped", "acme-example/app-new": "drift",
                  "acme-example/app-synced": "current", "acme-example/app-missing": "failed"}, states
new = next(x for x in r["repos"] if x["repo"] == "acme-example/app-new")
assert "AGENTS.md" in new["files"] and ".claude/hooks/fleet-memory.sh" in new["files"], new
assert r["counts"] == {"current": 1, "drift": 1, "failed": 1, "refused": 0, "skipped": 1}
assert len(r["guidance_commit"]) == 40
PY
check "report: the markdown table has a row per repo" test "$(grep -c '^| acme-example/' "$T/out1/report.md")" = 4
check "report: no sync.sh free text in the report" bash -c '! grep -q "check the App" "$1"/report.*' _ "$T/out1"
check "report: nothing changed in any fleet repo" test "$before" = "$(for r in app-new app-synced; do git --git-dir="$T/remotes/$OWNER/$r.git" for-each-ref; done)"
check "report: the token is never printed" bash -c '! grep -q ghs_FAKE <<<"$1"' _ "$OUT"
check "report: no push without --push" test -z "$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" for-each-ref refs/heads/persistent)"

run "$T/out2"
check "report: same inputs, same bytes" cmp -s "$T/out1/report.json" "$T/out2/report.json"

run --push "$T/out3"
check "push: exits 0" test "$RC" = 0
DB=refs/heads/persistent/drift-report
sha1=$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse -q --verify "$DB")
check "push: created persistent/drift-report" test -n "$sha1"
check "push: the branch holds the report" cmp -s "$T/out1/report.json" <(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" show "$DB:drift-report/report.json")
check "push: main is untouched" test "$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-list --count main)" = 1
run --push "$T/out4"
check "push again, nothing new: no commit" test "$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse "$DB")" = "$sha1"
check "push again: says unchanged" has "$OUT" "unchanged"
printf '# Base\nRule two.\n' > "$G/agents-md/base.md"; git -C "$G" commit -qam v2
run --push "$T/out5"
sha2=$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse "$DB")
check "push after a guidance change: a fast-forward commit" git --git-dir="$T/remotes/$OWNER/agent-guidance.git" merge-base --is-ancestor "$sha1" "$sha2"
check "push after a guidance change: the synced repo drifted" bash -c 'jq -e ".repos[] | select(.repo == \"acme-example/app-synced\") | .state == \"drift\"" "$1" >/dev/null' _ "$T/out5/report.json"
# Someone else's commit on the report branch is kept: the next push builds on it.
git clone -q --branch persistent/drift-report "$T/remotes/$OWNER/agent-guidance.git" "$T/w-report" 2>/dev/null
(cd "$T/w-report" && printf 'note\n' > NOTE.md && git add NOTE.md && git commit -q -m note && git push -q origin HEAD:persistent/drift-report)
sha3=$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse "$DB")
printf '# Base\nRule three.\n' > "$G/agents-md/base.md"; git -C "$G" commit -qam v3
run --push "$T/out6"
check "push: exits 0 on top of another commit" test "$RC" = 0
check "push: that commit is kept (fast-forward only)" git --git-dir="$T/remotes/$OWNER/agent-guidance.git" merge-base --is-ancestor "$sha3" "$DB"
check "push: and its file too" git --git-dir="$T/remotes/$OWNER/agent-guidance.git" cat-file -e "$DB:NOTE.md"
# A push that reports success but leaves the commit off the branch (here a
# post-receive hook rewinds it) is a failure: the check after the push sees it.
sha4=$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse "$DB")
HOOK="$T/remotes/$OWNER/agent-guidance.git/hooks/post-receive"
printf '#!/bin/sh\nwhile read old new ref; do git update-ref "$ref" "$old"; done\n' > "$HOOK"; chmod +x "$HOOK"
printf '# Base\nRule four.\n' > "$G/agents-md/base.md"; git -C "$G" commit -qam v4
run --push "$T/out7"
rm -f "$HOOK"
check "push that does not land: exit 1" test "$RC" = 1
check "push that does not land: says the commit is not on the branch" has "$OUT" "is not on persistent/drift-report after the push"
check "push that does not land: the branch is where it was" test "$(git --git-dir="$T/remotes/$OWNER/agent-guidance.git" rev-parse "$DB")" = "$sha4"

run
check "usage: no output dir is exit 2" test "$RC" = 2
OUT=$(PATH="$T/bin:$PATH" SYNC_SRC=$G bash "$G/scripts/drift-report.sh" "$T/x" 2>&1); RC=$?
check "usage: no SYNC_OWNER is exit 2" test "$RC" = 2

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
