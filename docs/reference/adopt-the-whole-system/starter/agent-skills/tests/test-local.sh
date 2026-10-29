#!/usr/bin/env bash
# test-local.sh: tests for .agent-checks/local.sh (the registry's own rules,
# guide 6.1) and scripts/skill-digest.py, against a tiny fake registry built
# in a scratch git repo. No network, no sleeps, fixed commit times.
# Exit 0 and "N assertions passed" mean it worked; any failure prints FAIL.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

pass=0 fail=0
check() {
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi
}
has() { grep -qF -- "$2" <<<"$1"; }

export HOME=$T/home GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_NOSYSTEM=1
mkdir -p "$HOME"
git config --global user.name tester
git config --global user.email tester@example.com
git config --global init.defaultBranch main
export GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000"

R=$T/registry
SKILL=plugins/demo-coding/skills/demo/SKILL.md
manifest() { printf '{"name": "demo-coding", "version": "%s"}\n' "$1" > "$R/plugins/demo-coding/.claude-plugin/plugin.json"; }
mkdir -p "$R/plugins/demo-coding/.claude-plugin" "$R/plugins/demo-coding/skills/demo" "$R/.agent-checks"
cp "$ROOT/.agent-checks/local.sh" "$R/.agent-checks/local.sh"
manifest 1.0.0
printf -- '---\nname: demo\ndescription: Demo. Use when testing.\n---\n\nRun `git status` yourself.\n' > "$R/$SKILL"
git init -q "$R"; git -C "$R" add -A; git -C "$R" commit -q -m base
BASE=$(git -C "$R" rev-parse HEAD)
lc() { (cd "$R" && bash .agent-checks/local.sh "$@" 2>&1); }

# ---- rule 1: no SKILL.md line that runs shell on load
check "pre-commit: nothing staged passes" lc pre-commit
printf 'Current branch: !`git branch --show-current`\n' >> "$R/$SKILL"; git -C "$R" add -A
OUT=$(lc pre-commit); RC=$?
check "pre-commit: a SKILL.md line with !\` is refused" test "$RC" = 1
check "pre-commit: the refusal says why and what to do" has "$OUT" "would run a shell command on load"
git -C "$R" reset -q --hard
printf 'A plain line: run `git status` (no bang).\n' >> "$R/$SKILL"; git -C "$R" add -A
check "pre-commit: a backtick line without ! passes" lc pre-commit
git -C "$R" reset -q --hard
mkdir -p "$R/plugins/demo-coding/skills/demo/references"
printf 'Example: !`date`\n' > "$R/plugins/demo-coding/skills/demo/references/notes.md"; git -C "$R" add -A
check "pre-commit: !\` outside SKILL.md is not this rule's business" lc pre-commit
git -C "$R" reset -q --hard; git -C "$R" clean -qfd

printf 'Now: !`date`\n' >> "$R/$SKILL"; manifest 1.0.1; git -C "$R" commit -q -am "bang line"
OUT=$(lc ci "$BASE" HEAD); RC=$?
check "ci: a committed !\` line is refused even with a version bump" test "$RC" = 1
check "ci: and the message names the rule" has "$OUT" "SKILL.md line contains"
git -C "$R" reset -q --hard "$BASE"

# ---- rule 2: in CI, a changed plugin must raise its version
printf 'More text.\n' >> "$R/$SKILL"; git -C "$R" commit -q -am "edit, no bump"
OUT=$(lc ci "$BASE" HEAD); RC=$?
check "ci: a changed plugin without a version bump is refused" test "$RC" = 1
check "ci: the refusal names the manifest" has "$OUT" "plugins/demo-coding/.claude-plugin/plugin.json"
manifest 1.0.1; git -C "$R" commit -q -am "bump"
check "ci: a raised version passes" lc ci "$BASE" HEAD
manifest 0.9.0; git -C "$R" commit -q -am "lower"
check "ci: a lowered version is refused" bash -c '! (cd "$1" && bash .agent-checks/local.sh ci "$2" HEAD)' _ "$R" "$BASE"
manifest 1.0.10; git -C "$R" commit -q -am "ten"
check "ci: versions compare as versions (1.0.10 > 1.0.1)" lc ci "$BASE" HEAD
printf 'not json\n' > "$R/plugins/demo-coding/.claude-plugin/plugin.json"; git -C "$R" commit -q -am "break manifest"
check "ci: an invalid manifest is refused" bash -c '! (cd "$1" && bash .agent-checks/local.sh ci "$2" HEAD)' _ "$R" "$BASE"
git -C "$R" reset -q --hard "$BASE"

mkdir -p "$R/plugins/new-plugin/.claude-plugin" "$R/plugins/new-plugin/skills/fresh"
printf '{"name": "new-plugin", "version": "1.0.0"}\n' > "$R/plugins/new-plugin/.claude-plugin/plugin.json"
printf -- '---\nname: fresh\ndescription: New. Use when testing.\n---\nBody.\n' > "$R/plugins/new-plugin/skills/fresh/SKILL.md"
git -C "$R" add -A; git -C "$R" commit -q -m "new plugin"
check "ci: a new plugin needs no bump" lc ci "$BASE" HEAD
git -C "$R" reset -q --hard "$BASE"
git -C "$R" rm -rq plugins/demo-coding; git -C "$R" commit -q -m "remove plugin"
check "ci: a removed plugin needs no bump" lc ci "$BASE" HEAD
git -C "$R" reset -q --hard "$BASE"

check "tree and pre-push modes have nothing of their own to check" bash -c 'cd "$1" && bash .agent-checks/local.sh tree HEAD && bash .agent-checks/local.sh pre-push </dev/null' _ "$R"

# ---- scripts/skill-digest.py: the digest format is part of every skills.lock
D=$ROOT/scripts/skill-digest.py
mkdir -p "$T/skill/references"
printf 'a\n' > "$T/skill/SKILL.md"; printf 'b\n' > "$T/skill/references/x.md"
# Pinned: if this changes, every consumer's skills.lock stops matching.
check "digest: the format is stable" test "$(python3 "$D" digest "$T/skill")" = \
  "sha256:$(printf 'SKILL.md\0%s\nreferences/x.md\0%s\n' "$(printf 'a\n' | sha256sum | cut -d' ' -f1)" "$(printf 'b\n' | sha256sum | cut -d' ' -f1)" | sha256sum | cut -d' ' -f1)"
mkdir -p "$T/empty"
check "digest: an empty skill directory is refused" bash -c '! python3 "$1" digest "$2"' _ "$D" "$T/empty"
LOCK=$(python3 "$D" lock "$R" example-owner/agent-skills demo-coding)
check "lock: names the registry and HEAD" bash -c 'grep -qF "\"registry\": \"example-owner/agent-skills\"" <<<"$1" && grep -qF "\"ref\": \"$2\"" <<<"$1"' _ "$LOCK" "$BASE"
check "lock: one entry per skill, keyed plugin/skill" python3 -c 'import json,sys; assert list(json.loads(sys.argv[1])["skills"])==["demo-coding/demo"]' "$LOCK"
check "lock: a bad plugin name is refused" bash -c '! python3 "$1" lock "$2" o/r "../x"' _ "$D" "$R"

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
