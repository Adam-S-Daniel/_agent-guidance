#!/usr/bin/env bash
# test-kit.sh: tests for scripts/render-agents-md.sh, templates/fleet-memory.sh,
# templates/check-privacy.py and templates/agent-checks.
# No network, no sleeps, no wall-clock: every repo is a scratch git repo with a
# fixed commit time, HOME and the git config are scratch too.
# Exit 0 and "N assertions passed" mean it worked; any failed assertion prints
# FAIL and the exit code is 1.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

pass=0 fail=0
check() {  # check <description> <command...>: passes when the command exits 0
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi
}
has() { grep -qF -- "$2" <<<"$1"; }          # has <text> <needle>
lacks() { ! grep -qF -- "$2" <<<"$1"; }
same() { cmp -s "$1" "$2"; }

export HOME=$T/home GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME"
unset FLEET_GUIDANCE_SKIP CLAUDE_CONFIG_DIR PRIVACY_DENYLIST_FILE AGENT_CHECKS_REF
git config --global user.name tester
git config --global user.email tester@example.com
git config --global init.defaultBranch main
export GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000"
MARKER='## Repo-specific additions'

# ============================================================ render-agents-md.sh
R=$ROOT/scripts/render-agents-md.sh
mkdir -p "$T/r"
printf '# Stub v1\nManaged text.\n' > "$T/r/stub1.md"
printf '# Stub v2\nNew managed text.\n' > "$T/r/stub2.md"

bash "$R" "$T/r/stub1.md" "$T/r/new.md"
check "render: a missing AGENTS.md becomes stub, blank line, marker" \
  test "$(cat "$T/r/new.md")" = "$(printf '# Stub v1\nManaged text.\n\n%s' "$MARKER")"

printf 'Our own notes.\n' > "$T/r/plain.md"
bash "$R" "$T/r/stub1.md" "$T/r/plain.md"
check "render: an AGENTS.md without the marker keeps its text below the marker" \
  test "$(cat "$T/r/plain.md")" = "$(printf '# Stub v1\nManaged text.\n\n%s\n\nOur own notes.' "$MARKER")"

printf '# Old managed\nstale\n\n%s\n\nKeep me.\n  and me\n' "$MARKER" > "$T/r/repo.md"
bash "$R" "$T/r/stub2.md" "$T/r/repo.md"
OUT=$(cat "$T/r/repo.md")
check "render: the managed half is replaced" lacks "$OUT" "stale"
check "render: the new stub is on top" test "$(head -1 "$T/r/repo.md")" = "# Stub v2"
check "render: the repo-specific half is kept verbatim" \
  test "$(sed -n "/^$MARKER\$/,\$p" "$T/r/repo.md")" = "$(printf '%s\n\nKeep me.\n  and me' "$MARKER")"
cp "$T/r/repo.md" "$T/r/repo.before"
bash "$R" "$T/r/stub2.md" "$T/r/repo.md"
check "render: a second run is byte for byte the same" same "$T/r/repo.md" "$T/r/repo.before"

# The real stub renders: it is the managed half verbatim, and a repo's own
# additions survive below the marker.
printf '# Old\n\n%s\n\nOur repo notes.\n' "$MARKER" > "$T/r/real.md"
check "render: the real stub.md renders" bash "$R" "$ROOT/agents-md/stub.md" "$T/r/real.md"
check "render: the real stub is the managed half, byte for byte" \
  cmp -s <(head -c "$(wc -c < "$ROOT/agents-md/stub.md")" "$T/r/real.md") "$ROOT/agents-md/stub.md"
check "render: the real stub keeps the repo's additions" \
  test "$(sed -n "/^$MARKER\$/,\$p" "$T/r/real.md")" = "$(printf '%s\n\nOur repo notes.' "$MARKER")"

# The marker quoted inside a line is not the marker: split on the whole line only.
printf '<!-- DO NOT EDIT ABOVE "%s" -->\nold managed\n%s\nmine\n' "$MARKER" "$MARKER" > "$T/r/quoted.md"
bash "$R" "$T/r/stub1.md" "$T/r/quoted.md"
check "render: a line quoting the marker does not anchor the split" \
  test "$(grep -c -- "$MARKER" "$T/r/quoted.md")" = 1
check "render: and the old managed text is gone" lacks "$(cat "$T/r/quoted.md")" "old managed"

printf 'bad\n%s\n' "$MARKER" > "$T/r/badstub.md"
cp "$T/r/repo.md" "$T/r/repo.keep"
RC=0; bash "$R" "$T/r/badstub.md" "$T/r/repo.md" 2>/dev/null || RC=$?
check "render: a stub holding the marker line is refused" test "$RC" = 1
check "render: and the target is untouched" same "$T/r/repo.md" "$T/r/repo.keep"

# ============================================================ fleet-memory.sh
mkdir -p "$T/fm/v100" "$T/fm/v50" "$T/fm/v200" "$T/fm/bad"
for v in v100 v50 v200 bad; do cp "$ROOT/templates/fleet-memory.sh" "$T/fm/$v/"; done
printf '<!-- guidance-version: 100 -->\n# Base\nRule one.\n' > "$T/fm/v100/fleet-guidance.md"
printf '<!-- guidance-version: 50 -->\n# Base\nOld rule.\n' > "$T/fm/v50/fleet-guidance.md"
printf '<!-- guidance-version: 200 -->\n# Base\nRule two.' > "$T/fm/v200/fleet-guidance.md"   # no final newline
CFG=$T/fm/config
hook() { CLAUDE_CONFIG_DIR=$CFG bash "$T/fm/$1/fleet-memory.sh"; }
BEGIN='<!-- BEGIN FLEET GUIDANCE (managed) — DO NOT EDIT -->'
END='<!-- END FLEET GUIDANCE -->'

OUT=$(hook v100); RC=$?
check "memory: first run installs" has "$OUT" "fleet-guidance: installed (v100, $(wc -c < "$T/fm/v100/fleet-guidance.md" | tr -d ' ') bytes)"
check "memory: exits 0" test "$RC" = 0
check "memory: the block holds the payload between the markers" \
  test "$(cat "$CFG/CLAUDE.md")" = "$(printf '%s\n' "$BEGIN"; cat "$T/fm/v100/fleet-guidance.md"; printf '%s' "$END")"
check "memory: a second run is current" has "$(hook v100)" "fleet-guidance: current (v100)"

{ printf 'My own line above.\n'; cat "$CFG/CLAUDE.md"; printf 'My own line below.\n'; } > "$T/fm/mixed"
cp "$T/fm/mixed" "$CFG/CLAUDE.md"
check "memory: an older payload never replaces a newer block" has "$(hook v50)" "current (kept newer v100; this checkout has v50)"
check "memory: and the file is untouched" same "$CFG/CLAUDE.md" "$T/fm/mixed"
check "memory: a newer payload installs" has "$(hook v200)" "installed (v200"
OUT=$(cat "$CFG/CLAUDE.md")
check "memory: exactly one block after an upgrade" test "$(grep -cxF -- "$BEGIN" "$CFG/CLAUDE.md")" = 1
check "memory: the upgrade dropped the old payload" lacks "$OUT" "Rule one."
check "memory: a payload without a final newline still ends before the END marker" grep -qxF "Rule two." "$CFG/CLAUDE.md"
check "memory: the user's own lines are kept" bash -c 'grep -qxF "My own line above." "$1" && grep -qxF "My own line below." "$1"' _ "$CFG/CLAUDE.md"

cp "$CFG/CLAUDE.md" "$T/fm/keep"
printf '<!-- guidance-version: 300 -->\r\nRule.\r\n' > "$T/fm/bad/fleet-guidance.md"
check "memory: a CRLF payload is refused" has "$(hook bad)" "DEGRADED — payload has CRLF line endings"
printf '<!-- guidance-version: 300 -->\n%s\n' "$END" > "$T/fm/bad/fleet-guidance.md"
check "memory: a payload holding a marker line is refused" has "$(hook bad)" "DEGRADED — payload contains a block marker line"
printf '# Base\nno version\n' > "$T/fm/bad/fleet-guidance.md"
check "memory: a payload without the version line is refused" has "$(hook bad)" "DEGRADED — payload has no guidance-version line"
rm "$T/fm/bad/fleet-guidance.md"
OUT=$(hook bad); RC=$?
check "memory: a missing payload is DEGRADED" has "$OUT" "DEGRADED — payload"
check "memory: DEGRADED still exits 0" test "$RC" = 0
check "memory: no refusal changed the file" same "$CFG/CLAUDE.md" "$T/fm/keep"

# A symlinked CLAUDE.md stays a symlink and its target gets the block.
LCFG=$T/fm/linkcfg; mkdir -p "$LCFG" "$T/fm/dotfiles"
printf 'Mine.\n' > "$T/fm/dotfiles/CLAUDE.md"
ln -s "$T/fm/dotfiles/CLAUDE.md" "$LCFG/CLAUDE.md"
CLAUDE_CONFIG_DIR=$LCFG bash "$T/fm/v100/fleet-memory.sh" >/dev/null
check "memory: a symlinked CLAUDE.md stays a symlink" test -L "$LCFG/CLAUDE.md"
check "memory: the link's target holds the block" grep -qxF "Rule one." "$T/fm/dotfiles/CLAUDE.md"

OUT=$(CLAUDE_CONFIG_DIR=$CFG FLEET_GUIDANCE_SKIP=1 bash "$T/fm/v100/fleet-memory.sh")
check "memory: FLEET_GUIDANCE_SKIP says skipped" has "$OUT" "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)"
check "memory: FLEET_GUIDANCE_SKIP removes the block" lacks "$(cat "$CFG/CLAUDE.md")" "$BEGIN"
check "memory: FLEET_GUIDANCE_SKIP keeps the user's lines" \
  test "$(cat "$CFG/CLAUDE.md")" = "$(printf 'My own line above.\nMy own line below.')"

# ============================================================ check-privacy.py
P=$ROOT/templates/check-privacy.py
G=$T/priv; git init -q "$G"
printf 'hello\n' > "$G/README.md"; git -C "$G" add -A; git -C "$G" commit -q -m base
BASE=$(git -C "$G" rev-parse HEAD)
printf '# my names\n\nJane Q\\. Example\nsecret-host\\.example\\.net\n' > "$T/denylist"
priv() { (cd "$G" && PRIVACY_DENYLIST_FILE=${DL-$T/denylist} python3 "$P" "$BASE" HEAD 2>&1); }

printf 'hello\nnothing personal\n' > "$G/README.md"; git -C "$G" commit -q -am "clean change"
OUT=$(priv); RC=$?
check "privacy: a clean change passes" test "$RC" = 0
OUT=$(DL=$T/missing priv); RC=$?
check "privacy: no denylist fails closed" test "$RC" = 1
check "privacy: and says so" has "$OUT" "failing closed"
printf '# only a comment\n\n' > "$T/empty"
OUT=$(DL=$T/empty priv); RC=$?
check "privacy: an empty denylist fails closed" test "$RC" = 1

printf 'hello\nnothing personal\nwritten by jane q. example\n' > "$G/README.md"; git -C "$G" commit -q -am "add a credit"
OUT=$(priv); RC=$?
check "privacy: an added line that matches fails" test "$RC" = 1
check "privacy: it names file:line and the pattern number" has "$OUT" "privacy: README.md:3: pattern 1"
check "privacy: it never prints the matched text" lacks "$OUT" "example"
git -C "$G" reset -q --hard "$BASE"

git -C "$G" commit -q --allow-empty -m "thanks to Jane Q. Example"
OUT=$(priv); RC=$?
check "privacy: a commit message that matches fails" test "$RC" = 1
check "privacy: reported as a commit message, without the text" bash -c 'grep -q "message: pattern 1" <<<"$1" && ! grep -qi "jane" <<<"$1"' _ "$OUT"
git -C "$G" reset -q --hard "$BASE"

mkdir -p "$G/notes"; printf 'x\n' > "$G/notes/secret-host.example.net.txt"; git -C "$G" add -A; git -C "$G" commit -q -m "notes"
OUT=$(priv); RC=$?
check "privacy: an added path that matches fails" test "$RC" = 1
check "privacy: and the path is never printed" bash -c 'grep -q "an added path: pattern 2" <<<"$1" && ! grep -q "secret-host" <<<"$1"' _ "$OUT"
git -C "$G" reset -q --hard "$BASE"

printf 'hello\n++ secret-host.example.net\n' > "$G/README.md"; git -C "$G" commit -q -am "plus plus"
OUT=$(priv); RC=$?
check "privacy: an added line starting with '++ ' is scanned, not taken for a header" has "$OUT" "README.md:2: pattern 2"
git -C "$G" reset -q --hard "$BASE"

printf 'written by Jane Q. Example\n' > "$G/README.md"; git -C "$G" commit -q -am "seed"
BASE=$(git -C "$G" rev-parse HEAD)
printf 'hello\n' > "$G/README.md"; git -C "$G" commit -q -am "remove the credit"
OUT=$(priv); RC=$?
check "privacy: a removed line is not an added line" test "$RC" = 0

# ============================================================ agent-checks runner
A=$T/ac; git init -q "$A"
mkdir -p "$A/.githooks"; cp "$ROOT/templates/agent-checks" "$A/.githooks/agent-checks"
printf 'x\n' > "$A/README.md"; git -C "$A" add -A; git -C "$A" commit -q -m base
ABASE=$(git -C "$A" rev-parse HEAD)
runner() { (cd "$A" && bash .githooks/agent-checks "$@" 2>&1); }

printf 'TOKEN=x\n' > "$A/.env"; git -C "$A" add .env
OUT=$(runner pre-commit); RC=$?
check "runner: a staged .env is refused" test "$RC" = 1
check "runner: the refusal says how to fix it" has "$OUT" "git rm --cached -- .env"
git -C "$A" rm -q --cached .env; rm "$A/.env"
printf 'x\n' > "$A/app.env.example"; git -C "$A" add -A
check "runner: an .example file is allowed" runner pre-commit
git -C "$A" commit -q -m example

printf 'k\n' > "$A/id_rsa"; git -C "$A" add -A; git -C "$A" commit -q -m "add key"
git -C "$A" rm -q id_rsa; git -C "$A" commit -q -m "remove key"
OUT=$(runner ci "$ABASE" HEAD); RC=$?
check "runner: ci walks every commit, so an added-then-removed key is refused" test "$RC" = 1
check "runner: ci names the file" has "$OUT" "id_rsa looks like a secret"
OUT=$(runner tree HEAD); RC=$?
check "runner: the final tree alone is clean" test "$RC" = 0
RC=0; runner ci not-a-rev HEAD >/dev/null || RC=$?
check "runner: a bad range fails instead of reading as nothing to check" test "$RC" = 1

# .agent-checks/local.sh comes from AGENT_CHECKS_REF, not the working tree.
mkdir -p "$A/.agent-checks"; printf 'echo "local: refused" >&2; exit 1\n' > "$A/.agent-checks/local.sh"
git -C "$A" add -A; git -C "$A" commit -q -m "strict local check"
STRICT=$(git -C "$A" rev-parse HEAD)
printf 'exit 0\n' > "$A/.agent-checks/local.sh"      # a branch weakens it in the working tree
OUT=$(cd "$A" && AGENT_CHECKS_REF=$STRICT bash .githooks/agent-checks tree HEAD 2>&1); RC=$?
check "runner: local.sh is read from AGENT_CHECKS_REF" test "$RC" = 1
check "runner: and its refusal is shown" has "$OUT" "local: refused"
check "runner: without the ref, the working tree's local.sh runs" runner tree HEAD

# ============================================================ the example lesson check
# How to test a lesson check of your own: copy the runner, enable the check in
# the copy, and prove it red on one bad commit and green on a good one.
L=$T/lesson; git init -q "$L"
mkdir -p "$L/.githooks"
sed 's/^# \(lesson_checks=.*\)  # example-lesson: enable$/\1/' "$ROOT/templates/agent-checks" > "$L/.githooks/agent-checks"
check "lesson: the sed enabled exactly one line" \
  test "$(diff "$ROOT/templates/agent-checks" "$L/.githooks/agent-checks" | grep -c '^>')" = 1
lrun() { (cd "$L" && bash .githooks/agent-checks "$@" 2>&1); }
LM='TODO(remove-before''-merge)'
printf 'x\n' > "$L/README.md"; git -C "$L" add -A; git -C "$L" commit -q -m base
LBASE=$(git -C "$L" rev-parse HEAD)
printf 'def f():\n    return 1  # %s\n' "$LM" > "$L/app.py"; git -C "$L" add app.py
OUT=$(lrun pre-commit); RC=$?
check "lesson: a staged marker is refused" test "$RC" = 1
check "lesson: the refusal names the lesson id" has "$OUT" "lesson-remove-before-merge"
check "lesson: and the file" has "$OUT" "app.py still holds"
OUT=$(cd "$A" && cp "$L/app.py" app.py && git add app.py && bash .githooks/agent-checks pre-commit 2>&1); RC=$?
check "lesson: the shipped runner (example disabled) allows the same commit" test "$RC" = 0
git -C "$A" rm -q --cached app.py; rm "$A/app.py"
printf 'def f():\n    return 1\n' > "$L/app.py"; git -C "$L" add app.py
check "lesson: the same file without the marker is allowed" lrun pre-commit
git -C "$L" commit -q -m clean
printf 'note: %s\n' "$LM" > "$L/notes.txt"; git -C "$L" add -A; git -C "$L" commit -q -m marked
git -C "$L" rm -q notes.txt; git -C "$L" commit -q -m unmarked
OUT=$(lrun ci "$LBASE" HEAD); RC=$?
check "lesson: ci reads each commit's content, so an added-then-removed marker is refused" test "$RC" = 1
check "lesson: the final tree is clean" lrun tree HEAD
check "lesson: the runner itself does not hold the marker" lacks "$(cat "$L/.githooks/agent-checks")" "$LM"

# ============================================================ .agent-checks/local.sh template
LT=$T/localtpl; git init -q "$LT"; mkdir -p "$LT/.agent-checks"
cp "$ROOT/.agent-checks/local.sh" "$LT/.agent-checks/local.sh"
ltrun() { (cd "$LT" && bash .agent-checks/local.sh "$@" 2>&1); }
check "local.sh template: exits 0 on an empty path list" ltrun pre-commit
printf 'x\n' > "$LT/a.py.orig"; git -C "$LT" add -A
check "local.sh template: as shipped it refuses nothing" ltrun pre-commit
sed 's/^\( *\)# \(.*\)  # local-example: enable$/\1\2/' "$ROOT/.agent-checks/local.sh" > "$LT/.agent-checks/local.sh"
check "local.sh template: the sed enabled exactly one line" \
  test "$(diff "$ROOT/.agent-checks/local.sh" "$LT/.agent-checks/local.sh" | grep -c '^>')" = 1
OUT=$(ltrun pre-commit); RC=$?
check "local.sh template: the enabled example refuses a matching path" test "$RC" = 1
check "local.sh template: and names it" has "$OUT" "a.py.orig is a merge or patch leftover"
git -C "$LT" rm -q --cached a.py.orig; printf 'x\n' > "$LT/a.py"; git -C "$LT" add a.py
check "local.sh template: and allows a path that does not match" ltrun pre-commit

# ============================================================ agent-guidance's own copies
# The sync skips this repo, so it carries its own copies of the delivered
# agent-checks files; they must be byte for byte the templates.
check "own copies: the starter's copies are byte for byte templates/" bash "$ROOT/scripts/update-own-copies.sh" --check
for pair in templates/agent-checks:.githooks/agent-checks templates/check-privacy.py:.githooks/check-privacy.py \
            templates/agent-checks.yml:.github/workflows/agent-checks.yml; do
  check "own copies: ${pair#*:} is ${pair%%:*}" cmp -s "$ROOT/${pair%%:*}" "$ROOT/${pair#*:}"
done
OC=$T/owncopies; git init -q "$OC"
mkdir -p "$OC/scripts"; cp -rp "$ROOT/templates" "$ROOT/.githooks" "$ROOT/.github" "$ROOT/.agent-checks" "$OC/"
cp -p "$ROOT/scripts/update-own-copies.sh" "$OC/scripts/"
git -C "$OC" add -A && git -C "$OC" commit -q -m base
OCBASE=$(git -C "$OC" rev-parse HEAD)
ocrun() { (cd "$OC" && bash .agent-checks/local.sh "$@" 2>&1); }
check "own copies: local.sh passes the identical copies (tree)" ocrun tree HEAD
printf '# drift\n' >> "$OC/.githooks/agent-checks"; git -C "$OC" add -A
OUT=$(ocrun pre-commit); RC=$?
check "own copies: a staged copy that differs is refused" test "$RC" = 1
check "own copies: the refusal names the copy and the fix" has "$OUT" ".githooks/agent-checks is not byte for byte templates/agent-checks"
git -C "$OC" commit -q -m drift
OUT=$(ocrun ci "$OCBASE" HEAD); RC=$?
check "own copies: ci refuses a head whose copy differs" test "$RC" = 1
chmod -x "$OC/.githooks/check-privacy.py"; (cd "$OC" && bash scripts/update-own-copies.sh --check >/dev/null 2>&1); RC=$?
check "own copies: --check reports a copy (or its mode) that differs" test "$RC" = 1
(cd "$OC" && bash scripts/update-own-copies.sh >/dev/null) && git -C "$OC" add -A && git -C "$OC" commit -q -m fix
check "own copies: update-own-copies.sh brings them back, mode included" bash -c 'cd "$1" && bash scripts/update-own-copies.sh --check' _ "$OC"
check "own copies: and ci passes again" ocrun ci "$OCBASE" HEAD
printf '# a template change\n' >> "$OC/templates/check-privacy.py"; git -C "$OC" add -A
check "own copies: a template edited without its copy is refused too" bash -c '! (cd "$1" && bash .agent-checks/local.sh pre-commit)' _ "$OC"

# ============================================================ routine specs
check "eval-runner spec: results go to claude/eval-results-<run id>" \
  has "$(cat "$ROOT/docs/routines/eval-runner.md")" 'claude/eval-results-<run id>'
check "eval-runner spec: no branch without its lane" \
  test -z "$(grep -E 'claude/eval-<' "$ROOT/docs/routines/eval-runner.md" "$ROOT/docs/routines/eval-runner.prompt.txt")"

# ============================================================ stub pin rule
# Guide 3.3 offers two pin styles, one per repo; the stub must not demand one.
PIN=$(awk '/^- \*\*Pin every third-party action/{p=1} p&&/^- /&&!/Pin every/{exit} p' "$ROOT/agents-md/stub.md")
check "stub: the pin rule says one style per repo" has "$PIN" 'one style per repo'
check "stub: the pin rule offers the full-SHA style" has "$PIN" '40-character commit SHA on every `uses:` line'
check "stub: the pin rule offers the lockfile style with --verify in CI" has "$PIN" 'gh actions-lock --verify` run in'
check "stub: no other line says every uses: goes through actions.lock" \
  test -z "$(grep -n 'Every `uses:` is pinned through' "$ROOT/agents-md/stub.md")"

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
