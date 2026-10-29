#!/usr/bin/env bash
# test-skills-bootstrap.sh: tests for templates/skills-bootstrap.sh and
# templates/skill-digest.py against a local bare registry.
# No network: https://github.com/ resolves to file:// repos through
# url.insteadOf in a scratch GIT_CONFIG_GLOBAL; HOME is scratch, commit times
# are fixed. Exit 0 and "N assertions passed" mean it worked.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
OWNER=acme-example

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
git config --global "url.file://$T/remotes/.insteadOf" https://github.com/
export GIT_AUTHOR_DATE="1700000000 +0000" GIT_COMMITTER_DATE="1700000000 +0000"
unset CLAUDE_CONFIG_DIR

# ---- the registry: two skills in one plugin, pushed to a bare "GitHub" remote
REG=$T/registry
mkdir -p "$REG/plugins/demo-coding/skills/alpha" "$REG/plugins/demo-coding/skills/beta/references"
printf -- '---\nname: alpha\ndescription: Alpha. Use when testing.\n---\nAlpha body.\n' > "$REG/plugins/demo-coding/skills/alpha/SKILL.md"
printf -- '---\nname: beta\ndescription: Beta. Use when testing.\n---\nBeta body.\n' > "$REG/plugins/demo-coding/skills/beta/SKILL.md"
printf 'notes\n' > "$REG/plugins/demo-coding/skills/beta/references/notes.md"
git init -q "$REG"; git -C "$REG" add -A; git -C "$REG" commit -q -m "two skills"
git init -q --bare "$T/remotes/$OWNER/agent-skills.git"
git -C "$REG" push -q "https://github.com/$OWNER/agent-skills.git" main
REF1=$(git -C "$REG" rev-parse HEAD)

D=$ROOT/templates/skill-digest.py
LOCK=$(python3 "$D" lock "$REG" "$OWNER/agent-skills" demo-coding)
check "digest: lock pins HEAD" has "$LOCK" "\"ref\": \"$REF1\""
check "digest: lock lists both skills with sha256 digests" python3 -c '
import json,re,sys
d=json.loads(sys.argv[1]); s=d["skills"]
assert sorted(s)==["demo-coding/alpha","demo-coding/beta"], s
assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in s.values())' "$LOCK"
check "digest: digest of the checked-out skill equals the lock's" test \
  "$(python3 "$D" digest "$REG/plugins/demo-coding/skills/beta")" = "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["skills"]["demo-coding/beta"])' "$LOCK")"
printf 'stray\n' > "$REG/plugins/demo-coding/skills/alpha/untracked.md"
check "digest: an untracked file never reaches the lock" test "$(python3 "$D" lock "$REG" "$OWNER/agent-skills" demo-coding)" = "$LOCK"
rm "$REG/plugins/demo-coding/skills/alpha/untracked.md"
mkdir -p "$T/lnk"; printf 'x\n' > "$T/lnk/a"; ln -s /etc/hostname "$T/lnk/b"
check "digest: a symlink in a skill is refused" bash -c '! python3 "$1" digest "$2"' _ "$D" "$T/lnk"

# ---- a consumer repo holding the hook, with its registry set to the test owner
APP=$T/proj/app
mkdir -p "$APP/.claude/hooks" "$APP/scripts"
sed "s|^ALLOWED_REGISTRY = \"<you>/agent-skills\"|ALLOWED_REGISTRY = \"$OWNER/agent-skills\"|" \
  "$ROOT/templates/skills-bootstrap.sh" > "$APP/.claude/hooks/skills-bootstrap.sh"
check "setup: the registry line was replaced exactly once" test "$(grep -c "ALLOWED_REGISTRY = \"$OWNER/agent-skills\"" "$APP/.claude/hooks/skills-bootstrap.sh")" = 1
cp "$D" "$APP/scripts/skill-digest.py"
git init -q "$APP"
printf '%s\n' "$LOCK" > "$APP/skills.lock"
SK=$HOME/.claude/skills
boot() { CLAUDE_CODE_REMOTE=true CLAUDE_PROJECT_DIR=${PROJ:-$APP} bash "$APP/.claude/hooks/skills-bootstrap.sh"; }

OUT=$(env -u CLAUDE_CODE_REMOTE CLAUDE_PROJECT_DIR=$APP bash "$APP/.claude/hooks/skills-bootstrap.sh"); RC=$?
check "bootstrap: off the cloud it does nothing" test -z "$OUT" -a "$RC" = 0 -a ! -e "$SK"

OUT=$(boot); RC=$?
check "bootstrap: installs both skills" has "$OUT" "installed 2 skills from $OWNER/agent-skills"
check "bootstrap: output is the hook's JSON with reloadSkills" python3 -c '
import json,sys; d=json.loads(sys.argv[1]); assert d["hookSpecificOutput"]["reloadSkills"] is True' "$OUT"
check "bootstrap: exits 0" test "$RC" = 0
check "bootstrap: the files are the registry's" cmp -s "$SK/beta/references/notes.md" "$REG/plugins/demo-coding/skills/beta/references/notes.md"
check "bootstrap: it records what it installed" test "$(cat "$SK/.skills-bootstrap.json")" = '["alpha", "beta"]'

# A digest that does not match is not installed, and says so.
python3 - "$APP/skills.lock" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["skills"]["demo-coding/alpha"] = "sha256:" + "0" * 64
json.dump(d, open(p, "w"))
PY
OUT=$(boot)
check "bootstrap: a digest mismatch is DEGRADED and names the skill" bash -c 'grep -q "DEGRADED" <<<"$1" && grep -q "alpha" <<<"$1"' _ "$OUT"

# A commit that is not on the registry's main branch is refused.
git -C "$REG" checkout -q -b side
printf 'side\n' > "$REG/plugins/demo-coding/skills/alpha/side.md"; git -C "$REG" add -A; git -C "$REG" commit -q -m side
git -C "$REG" push -q "https://github.com/$OWNER/agent-skills.git" side
SIDE_LOCK=$(python3 "$D" lock "$REG" "$OWNER/agent-skills" demo-coding)
printf '%s\n' "$SIDE_LOCK" > "$APP/skills.lock"
OUT=$(boot)
check "bootstrap: a pinned commit off main installs nothing" has "$OUT" "DEGRADED - installed 0"
git -C "$REG" checkout -q main

# Another registry or a ref that is not a sha is refused outright.
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d["registry"]="someone-else/agent-skills"; print(json.dumps(d))' "$LOCK" > "$APP/skills.lock"
check "bootstrap: another registry is refused" has "$(boot)" "names another registry"

# A skill folder it did not install is never overwritten.
printf '%s\n' "$LOCK" > "$APP/skills.lock"
rm -rf "$SK"; mkdir -p "$SK/alpha"; printf 'mine\n' > "$SK/alpha/SKILL.md"
OUT=$(boot)
check "bootstrap: a hand-made skill of the same name is left alone" test "$(cat "$SK/alpha/SKILL.md")" = mine
check "bootstrap: and reported" has "$OUT" "was not installed by this hook"
check "bootstrap: the other skill still installs" test -f "$SK/beta/SKILL.md"

# A skill no lock pins any more is removed next session.
rm -rf "$SK"; boot >/dev/null
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); del d["skills"]["demo-coding/beta"]; print(json.dumps(d))' "$LOCK" > "$APP/skills.lock"
boot >/dev/null
check "bootstrap: a skill dropped from the lock is removed" test ! -e "$SK/beta"
check "bootstrap: the pinned one stays" test -f "$SK/alpha/SKILL.md"

# Multi-repo: two locks pinning one skill differently install neither copy.
P=$T/multi; mkdir -p "$P/one" "$P/two"
printf '%s\n' "$LOCK" > "$P/one/skills.lock"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d["skills"]["demo-coding/beta"]="sha256:"+"1"*64; print(json.dumps(d))' "$LOCK" > "$P/two/skills.lock"
rm -rf "$SK"
OUT=$(PROJ=$P boot)
check "bootstrap: conflicting locks install neither copy" test ! -e "$SK/beta"
check "bootstrap: and the conflict is reported" bash -c 'grep -q "DEGRADED" <<<"$1" && grep -q "beta" <<<"$1"' _ "$OUT"
check "bootstrap: an unconflicted skill still installs" test -f "$SK/alpha/SKILL.md"

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
