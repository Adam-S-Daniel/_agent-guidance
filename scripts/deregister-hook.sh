#!/usr/bin/env bash
set -euo pipefail
#
# deregister-hook.sh — Remove exactly one hook command this repo's sync wrote
# from a repo's .claude/settings.json, and nothing else.
#
# The inverse of register-bootstrap-hook.sh, for ADR 0043: the sync stops
# delivering fleet-memory.sh and guidance-load-record.sh and must take back
# exactly the entries it wrote. So the match is the EXACT command string the
# sync registered (DEREGISTER_COMMAND), under one event (DEREGISTER_EVENT),
# never a basename or substring: an entry whose quoting, path or arguments a
# person changed is theirs now, and stays.
#
# What is removed: every hook entry under hooks.<event>[*].hooks whose
# `command` equals the string exactly. A group left with no hooks is dropped
# (the registrar wrote each one as its own group); a group that still holds
# someone else's entry keeps them, its matcher and its position. An event left
# with no groups is dropped, and so is a `hooks` object left empty. Every
# other key, event, group and entry is untouched, in order.
#
# Refuses rather than guesses, like the registrar: a file that is not a JSON
# object, or whose `hooks` or `hooks.<event>` has the wrong type, is not
# written (exit 3). The semantic guard re-parses the bytes about to be written
# and requires them to equal the original document with only the matching
# entries (and the containers they emptied) removed.
#
# Formatting is not preserved in a file this script writes (2-space indent,
# as the registrar writes); a file with nothing to remove is never rewritten.
#
# Usage: DEREGISTER_EVENT=<event> DEREGISTER_COMMAND=<command> \
#            deregister-hook.sh [--check] <path-to-settings.json>
#
# --check writes nothing and reports what a run would do.
#
# Prints exactly one of:
#   not-registered      — nothing to remove (also: no file, or an empty one)
#   would-remove N      — --check only: N entries match
#   removed N           — N entries were removed and the file rewritten
#   refused-unparseable — no write; the file is not a JSON object, or a
#                         container on the path has the wrong type
#   refused-guard       — no write; the re-parse guard saw another change
#   refused-unwritable  — no write; the file could not be written
#
# Exit: 0 on not-registered, would-remove and removed; 2 on usage; 3 on a
# refusal (unparseable or guard); 5 when the file cannot be written.

CHECK=false
if [[ "${1:-}" == "--check" ]]; then
    CHECK=true
    shift
fi
TARGET="${1:-}"
EVENT="${DEREGISTER_EVENT:-}"
COMMAND="${DEREGISTER_COMMAND:-}"
if [[ -z "$TARGET" || -z "$EVENT" || -z "$COMMAND" || $# -ne 1 ]]; then
    echo "Usage: DEREGISTER_EVENT=<event> DEREGISTER_COMMAND=<command> deregister-hook.sh [--check] <settings.json>" >&2
    exit 2
fi

python3 -c '
import copy, json, os, sys

target, event, command, check = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "true"

if not os.path.exists(target) or os.path.getsize(target) == 0:
    print("not-registered")
    sys.exit(0)
with open(target, encoding="utf-8") as fh:
    raw = fh.read()
if not raw.strip():
    print("not-registered")
    sys.exit(0)
try:
    doc = json.loads(raw)
except Exception:
    print("refused-unparseable")
    sys.exit(3)
if not isinstance(doc, dict):
    print("refused-unparseable")
    sys.exit(3)

hooks = doc.get("hooks")
if hooks is None:
    print("not-registered")
    sys.exit(0)
if not isinstance(hooks, dict):
    print("refused-unparseable")
    sys.exit(3)
groups = hooks.get(event)
if groups is None:
    print("not-registered")
    sys.exit(0)
if not isinstance(groups, list):
    print("refused-unparseable")
    sys.exit(3)

def ours(e):
    return isinstance(e, dict) and e.get("command") == command

removed = 0
kept_groups = []
for g in groups:
    entries = g.get("hooks") if isinstance(g, dict) else None
    if not isinstance(entries, list):
        kept_groups.append(g)
        continue
    keep = [e for e in entries if not ours(e)]
    n = len(entries) - len(keep)
    removed += n
    if n == 0:
        kept_groups.append(g)
    elif keep:
        g2 = copy.deepcopy(g)
        g2["hooks"] = keep
        kept_groups.append(g2)
    # else: the group held only our entry; drop it.

if removed == 0:
    print("not-registered")
    sys.exit(0)
if check:
    print(f"would-remove {removed}")
    sys.exit(0)

want = copy.deepcopy(doc)
if kept_groups:
    want["hooks"][event] = kept_groups
else:
    del want["hooks"][event]
    if not want["hooks"]:
        del want["hooks"]

candidate = json.dumps(want, indent=2) + "\n"

# The guard: the written bytes re-parse to `want`, and `want` is the original
# with only our entries gone. Rebuild that independently: put every other
# event back and compare, then require the remaining entries of this event,
# flattened in order, to be exactly the original ones minus ours.
reparsed = json.loads(candidate)
if reparsed != want:
    print("refused-guard"); sys.exit(3)
others_before = {k: v for k, v in doc.items() if k != "hooks"}
others_after = {k: v for k, v in reparsed.items() if k != "hooks"}
events_before = {k: v for k, v in hooks.items() if k != event}
events_after = {k: v for k, v in reparsed.get("hooks", {}).items() if k != event}
def flat(gs):
    out = []
    for g in gs:
        es = g.get("hooks") if isinstance(g, dict) else None
        out.extend(es if isinstance(es, list) else [("group", g)])
    return out
after_groups = reparsed.get("hooks", {}).get(event, [])
if others_before != others_after or events_before != events_after \
        or flat(after_groups) != [e for e in flat(groups) if not ours(e)] \
        or any(ours(e) for e in flat(after_groups)):
    print("refused-guard"); sys.exit(3)

try:
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(candidate)
except OSError:
    print("refused-unwritable"); sys.exit(5)
print(f"removed {removed}")
' "$TARGET" "$EVENT" "$COMMAND" "$CHECK"
