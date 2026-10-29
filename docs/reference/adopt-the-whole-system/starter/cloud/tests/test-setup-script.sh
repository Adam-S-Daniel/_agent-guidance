#!/usr/bin/env bash
# test-setup-script.sh: tests for build-setup-script.sh and the setup script
# it writes (guide 7.3), without running that script (it installs packages as
# root). Builds it for a test owner, then checks that it parses, that its
# embedded installer is machine/install-agent-hooks.sh with only <you>
# filled, that the sources keep <you>, that a bad owner is refused, and that
# the settings file it writes runs the first attached repo's copy of each hook.
# INSTALL_AGENT_HOOKS overrides where the installer is read from.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
INSTALLER=${INSTALL_AGENT_HOOKS:-$ROOT/../machine/install-agent-hooks.sh}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
SCRIPT=$T/cloud-setup-script.sh

pass=0 fail=0
check() {
  local d=$1; shift
  if "$@" >/dev/null 2>&1; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL: $d"; fi
}

[ -f "$INSTALLER" ] || { echo "FAIL: no installer at $INSTALLER (set INSTALL_AGENT_HOOKS)"; exit 1; }
cp "$INSTALLER" "$T/installer.before"; cp "$ROOT/setup-script.template.sh" "$T/template.before"
BUILD=(bash "$ROOT/build-setup-script.sh")

check "build: --owner writes the script" "${BUILD[@]}" --owner Acme-Example --out "$SCRIPT" "$INSTALLER"
check "build: the sources keep <you> (the owner goes into the output only)" \
  bash -c 'cmp -s "$1" "$2" && cmp -s "$3" "$4"' _ "$INSTALLER" "$T/installer.before" "$ROOT/setup-script.template.sh" "$T/template.before"
check "build: no output is left in the kit folder" test ! -e "$ROOT/cloud-setup-script.sh"
check "setup script: bash -n is clean" bash -n "$SCRIPT"
check "setup script: its last line is exit 0 (a failing setup script stops sessions)" test "$(tail -n 1 "$SCRIPT")" = "exit 0"

# heredoc <file> <word>: the body of the one quoted heredoc ending at <word>.
heredoc() { awk -v w="$2" 'on && $0 == w { exit } on { print } index($0, "<<\x27" w "\x27") { on = 1 }' "$1"; }
heredoc "$SCRIPT" INSTALL > "$T/embedded"
sed 's/<you>/Acme-Example/g' "$INSTALLER" > "$T/filled"
check "setup script: the embedded installer is install-agent-hooks.sh with <you> filled" cmp -s "$T/embedded" "$T/filled"
check "setup script: its owner line names the owner" grep -qx 'owner="Acme-Example" .*' "$T/embedded"
check "setup script: no <you> is left in it" bash -c '! grep -qF "<you>" "$1"' _ "$SCRIPT"
check "setup script: the installer heredoc appears once" test "$(grep -c "<<'INSTALL'\$" "$SCRIPT")" = 1
"${BUILD[@]}" --owner Acme-Example --out "$T/again.sh" "$INSTALLER" >/dev/null 2>&1
check "build: building twice gives the same bytes" cmp -s "$T/again.sh" "$SCRIPT"
printf '# fleet.env\nOWNER=acme-example\nPLAN=free\n' > "$T/fleet.env"
check "build: --config reads OWNER from fleet.env" "${BUILD[@]}" --config "$T/fleet.env" --out "$T/from-config.sh" "$INSTALLER"
check "build: and fills that owner" grep -q '^owner="acme-example"' "$T/from-config.sh"
for bad in '<you>' 'acme/x' '../x' 'a b' "$(printf 'x%.0s' {1..40})" ''; do
  check "build: the owner [$bad] is refused with exit 2" bash -c '"$@"; [ $? = 2 ]' _ "${BUILD[@]}" --owner "$bad" --out "$T/bad.sh" "$INSTALLER"
done
check "build: a refused owner writes nothing" test ! -e "$T/bad.sh"
check "build: no owner at all is refused" bash -c '"$@"; [ $? = 2 ]' _ "${BUILD[@]}" --out "$T/bad.sh" "$INSTALLER"
printf 'OWNER=<you>\n' > "$T/unfilled.env"
check "build: a fleet.env still holding <you> is refused" bash -c '"$@"; [ $? = 2 ]' _ "${BUILD[@]}" --config "$T/unfilled.env" --out "$T/bad.sh" "$INSTALLER"
{ cat "$INSTALLER"; echo INSTALL; } > "$T/bad-installer"
check "build: an installer holding a line INSTALL is refused" bash -c '! "$@"' _ "${BUILD[@]}" --owner acme-example --out "$T/bad.sh" "$T/bad-installer"
sed 's/<you>/someone/g' "$INSTALLER" > "$T/edited-installer"
check "build: an installer already edited by hand is refused" bash -c '! "$@"' _ "${BUILD[@]}" --owner acme-example --out "$T/bad.sh" "$T/edited-installer"

# The settings file the script writes for multi-repo sessions.
heredoc "$SCRIPT" JSON > "$T/settings.json"
check "settings: valid JSON, three hooks with the guide's timeouts" python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
h = d["hooks"]
assert [g["hooks"][0]["timeout"] for g in h["SessionStart"]] == [30, 90]
assert all(g["matcher"] == "startup|resume|fork" for g in h["SessionStart"])
assert h["PreToolUse"][0]["matcher"] == "Bash|Edit|Write|MultiEdit" and h["PreToolUse"][0]["hooks"][0]["timeout"] == 10
assert d["disableSkillShellExecution"] is True' "$T/settings.json"
python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for g in d["hooks"]["SessionStart"] + d["hooks"]["PreToolUse"]:
    print(g["hooks"][0]["command"])' "$T/settings.json" > "$T/commands"
check "settings: three commands" test "$(wc -l < "$T/commands")" = 3

# Run each command as Claude Code would: bash -c <command> with CLAUDE_PROJECT_DIR set.
P=$T/project
for repo in a-repo b-repo; do
  mkdir -p "$P/$repo/.claude/hooks"
  for h in fleet-memory.sh skills-bootstrap.sh agent-checks-guard.sh; do
    printf 'echo "%s %s"\n' "$repo" "$h" > "$P/$repo/.claude/hooks/$h"
  done
done
mkdir -p "$T/empty"
i=0
while IFS= read -r cmd; do
  i=$((i + 1))
  name=$(grep -o '[a-z-]*\.sh' <<<"$cmd" | head -n 1)
  OUT=$(CLAUDE_PROJECT_DIR=$P bash -c "$cmd")
  check "settings: command $i runs the first attached repo's $name" test "$OUT" = "a-repo $name"
  OUT=$(CLAUDE_PROJECT_DIR=$T/empty bash -c "$cmd"); RC=$?
  check "settings: command $i with no repo holding the hook is silent and exits 0" test -z "$OUT" -a "$RC" = 0
done < "$T/commands"

echo "$pass assertions passed, $fail failed"
[ "$fail" = 0 ]
