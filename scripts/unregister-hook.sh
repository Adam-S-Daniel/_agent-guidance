#!/usr/bin/env bash
set -euo pipefail
#
# unregister-hook.sh — Remove, from a repo's .claude/settings.json, exactly the
# hook entries the sync itself once registered, and nothing else (ADR 0043).
# The counterpart of register-bootstrap-hook.sh for a hook the fleet stops
# delivering (fleet-memory on SessionStart, guidance-load-record on Stop).
#
# An entry matches only when its `command` string EQUALS the command the sync
# wrote (BOOTSTRAP_HOOK_COMMAND), under the event BOOTSTRAP_HOOK_EVENT. A
# hand-written entry that merely names the same script, a foreign entry in
# the same group or the same event, and every other key stay exactly as they
# were. A group the removal empties is dropped, then an emptied event array,
# then an emptied `hooks` object: only structure this removal itself emptied.
#
# JSON is read and written with python3's stdlib `json`, never a regex. As in
# register-bootstrap-hook.sh, the result is re-parsed and must equal the
# original document minus the matched entries, or nothing is written. A file
# that is not a JSON object is never touched. Formatting is not preserved
# (2-space indent), and a file this script does not modify is never rewritten.
#
# Usage: unregister-hook.sh status <settings.json>
#        unregister-hook.sh remove <settings.json>
#   env: BOOTSTRAP_HOOK_EVENT   (default SessionStart)
#        BOOTSTRAP_HOOK_COMMAND (required; the exact command string)
#
# Prints exactly one of:
#   present             (status) at least one matching entry exists
#   absent              (status) valid JSON object, no matching entry
#   removed             (remove) the matching entries were removed
#   none                (remove) nothing matched; no write
#   missing             the file is absent or empty
#   unparseable         content present but not a JSON object; no write
#   refused-guard       (remove) the re-parse guard saw more than the intended change
#   refused-unwritable  (remove) the file could not be written
#
# Exit: 0 except 2 on usage, 3 on a guard refusal, 5 when the file cannot be written.

MODE="${1:-}"
TARGET="${2:-}"
if [[ ( "$MODE" != "status" && "$MODE" != "remove" ) || -z "$TARGET" || -z "${BOOTSTRAP_HOOK_COMMAND:-}" ]]; then
    echo "Usage: BOOTSTRAP_HOOK_COMMAND=<cmd> unregister-hook.sh status|remove <settings.json>" >&2
    exit 2
fi
if [[ -d "$TARGET" ]]; then
    echo "unregister-hook.sh: $TARGET is a directory; pass its .claude/settings.json" >&2
    exit 2
fi
EVENT="${BOOTSTRAP_HOOK_EVENT:-SessionStart}"

result=$(python3 -c '
import copy, json, os, sys

mode, target, command, event = sys.argv[1:5]

if not os.path.exists(target) or os.path.getsize(target) == 0:
    print("missing")
    sys.exit(0)
with open(target, encoding="utf-8") as fh:
    raw = fh.read()
if not raw.strip():
    print("missing")
    sys.exit(0)
try:
    doc = json.loads(raw)
except Exception:
    print("unparseable")
    sys.exit(0)
if not isinstance(doc, dict):
    print("unparseable")
    sys.exit(0)

hooks = doc.get("hooks")
groups = hooks.get(event) if isinstance(hooks, dict) else None
if not isinstance(groups, list):
    groups = []

def ours(e):
    return isinstance(e, dict) and e.get("command") == command

def count(gs):
    n = 0
    for g in gs:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            n += sum(1 for e in g["hooks"] if ours(e))
    return n

found = count(groups)
if mode == "status":
    print("present" if found else "absent")
    sys.exit(0)
if not found:
    print("none")
    sys.exit(0)

want = copy.deepcopy(doc)
kept = []
for g in want["hooks"][event]:
    if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(ours(e) for e in g["hooks"]):
        g["hooks"] = [e for e in g["hooks"] if not ours(e)]
        if not g["hooks"]:
            continue
    kept.append(g)
if kept:
    want["hooks"][event] = kept
else:
    del want["hooks"][event]
    if not want["hooks"]:
        del want["hooks"]

candidate = json.dumps(want, indent=2) + "\n"
if json.loads(candidate) != want or count(want.get("hooks", {}).get(event, [])) != 0:
    print("refused-guard")
    sys.exit(3)
try:
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(candidate)
except OSError:
    print("refused-unwritable")
    sys.exit(5)
print("removed")
' "$MODE" "$TARGET" "$BOOTSTRAP_HOOK_COMMAND" "$EVENT") || {
    status=$?
    [[ -n "$result" ]] && echo "$result"
    exit "$status"
}

echo "$result"
