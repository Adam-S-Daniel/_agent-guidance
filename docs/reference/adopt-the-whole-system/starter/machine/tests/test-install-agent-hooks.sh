#!/usr/bin/env bash
# test-install-agent-hooks.sh: tests for install-agent-hooks.sh in a scratch
# HOME and GIT_CONFIG_GLOBAL (the real git config is never read or written).
# Remotes are local bare repos reached through url.insteadOf, so clone URLs
# read https://github.com/<owner>/... as they would on a real machine.
# No network, no sleeps, fixed commit times. The end-to-end part needs git
# 2.54+ (config-based hooks); an older git skips it and says so.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
OWNER=Acme-Example         # mixed case: the script also matches the lowercase form
LC=acme-example

pass=0 fail=0
check() {
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi
}

export HOME=$T/home GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_NOSYSTEM=1
unset XDG_CONFIG_HOME AGENT_CHECKS_REF
mkdir -p "$HOME"
git config --global user.name tester
git config --global user.email tester@example.com
git config --global init.defaultBranch main
git config --global "url.file://$T/remotes/.insteadOf" https://github.com/
export GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000"

sed "s|^owner=\"<you>\"|owner=\"$OWNER\"|" "$ROOT/install-agent-hooks.sh" > "$T/install.sh"
check "setup: the owner line was replaced exactly once" test "$(grep -c "^owner=\"$OWNER\"" "$T/install.sh")" = 1

OUT=$(bash "$T/install.sh"); RC=$?
INC=$HOME/.config/git/agent-hooks.inc
check "install: exits 0" test "$RC" = 0
check "install: writes the include file" test -f "$INC"
for ev in pre-commit pre-push; do
  check "install: $ev hook event" test "$(git config --file "$INC" "hook.agent-checks-$ev.event")" = "$ev"
  check "install: $ev hook runs the runner from origin/HEAD, then origin/main" \
    bash -c 'c=$(git config --file "$1" "hook.agent-checks-$2.command"); grep -qF "for r in origin/HEAD origin/main" <<<"$c" && grep -qF "agent-checks $2" <<<"$c"' _ "$INC" "$ev"
done
n_inc() { git config --global --get-regexp '^includeif\.' | wc -l | tr -d ' '; }
check "install: one includeIf per URL form and owner spelling (3 x 2)" test "$(n_inc)" = 6
check "install: the include names the owner's https URLs" \
  bash -c 'git config --global --get "includeIf.hasconfig:remote.*.url:https://github.com/$1/**.path"' _ "$OWNER"
bash "$T/install.sh" >/dev/null
check "install: running it again adds nothing" test "$(n_inc)" = 6
check "install: and leaves one hook command per event" \
  test "$(git config --file "$INC" --get-all hook.agent-checks-pre-commit.command | wc -l | tr -d ' ')" = 1

# ---- end to end: does a commit in each kind of clone run the runner?
if ! git version | awk '{split($3,v,"."); exit !(v[1]>2 || (v[1]==2 && v[2]>=54))}'; then
  echo "SKIP: $(git version) ignores config-based hooks; end-to-end part not run"
  echo "$pass assertions passed, $fail failed"
  [ "$fail" = 0 ]; exit
fi

# The runner on main refuses a staged file named BLOCKED.
mk_remote() {  # mk_remote <owner>: bare repo whose main holds the refusing runner
  local w=$T/seed-$1
  git init -q --bare "$T/remotes/$1/app.git"
  git init -q "$w"
  mkdir -p "$w/.githooks"
  cat > "$w/.githooks/agent-checks" <<'RUNNER'
#!/usr/bin/env bash
if [ "$1" = pre-commit ] && git diff --cached --name-only | grep -qx BLOCKED; then
  echo "agent-checks: BLOCKED is refused (ref $AGENT_CHECKS_REF)" >&2; exit 1
fi
exit 0
RUNNER
  git -C "$w" add -A && git -C "$w" commit -q -m runner
  git -C "$w" push -q "$T/remotes/$1/app.git" main
}
mk_remote "$OWNER"
mk_remote someone-else
# Setup failures stop the test: a missing clone would make "refused" pass.
git clone -q "https://github.com/$OWNER/app.git" "$T/mine" || { echo "FAIL: setup: clone"; exit 1; }
git clone -q https://github.com/someone-else/app.git "$T/theirs" || { echo "FAIL: setup: clone"; exit 1; }
git clone -q https://github.com/someone-else/app.git "$T/fork" || { echo "FAIL: setup: clone"; exit 1; }
# The fork's upstream names you in lowercase; it is never fetched.
git -C "$T/fork" remote add upstream "https://github.com/$LC/app.git"

try_commit() {  # try_commit <clone>: stage BLOCKED and commit; prints git's stderr
  printf 'x\n' > "$1/BLOCKED"; git -C "$1" add BLOCKED
  git -C "$1" commit -q -m try 2>&1
}
OUT=$(try_commit "$T/mine"); RC=$?
check "hook: in a clone of your repo, the runner refuses the commit" test "$RC" != 0
check "hook: the runner ran from origin's default branch" grep -qF "ref origin/HEAD" <<<"$OUT"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/mine/.githooks/agent-checks"   # a branch weakens the working tree copy
RC=0; try_commit "$T/mine" >/dev/null || RC=$?
check "hook: a weakened working-tree runner changes nothing" test "$RC" != 0
git -C "$T/mine" checkout -q -- .githooks/agent-checks
git -C "$T/mine" remote set-head origin --delete >/dev/null 2>&1
OUT=$(try_commit "$T/mine"); RC=$?
check "hook: with no origin/HEAD it falls back to origin/main" bash -c '[ "$1" != 0 ] && grep -qF "ref origin/main" <<<"$2"' _ "$RC" "$OUT"
git -C "$T/mine" reset -q

check "hook: someone else's clone does not include the hooks" \
  bash -c '! git -C "$1" config --get hook.agent-checks-pre-commit.event' _ "$T/theirs"
check "hook: a fork clone with your repo as upstream does include them" \
  test "$(git -C "$T/fork" config --get hook.agent-checks-pre-commit.event)" = pre-commit
RC=0; try_commit "$T/theirs" >/dev/null || RC=$?
check "hook: someone else's repo never runs it" test "$RC" = 0
RC=0; try_commit "$T/fork" >/dev/null || RC=$?
check "hook: a fork clone (origin is not yours) does not run it" test "$RC" = 0

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
