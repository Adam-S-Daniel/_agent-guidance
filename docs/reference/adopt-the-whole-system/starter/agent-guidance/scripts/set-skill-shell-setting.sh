#!/usr/bin/env bash
set -euo pipefail
#
# set-skill-shell-setting.sh — Deliver `"disableSkillShellExecution": true` to a
# repo's .claude/settings.json (_agent-guidance ADR 0029).
#
# Claude Code runs a SKILL.md line of the form !`cmd` (and a fenced block
# opened with ```!) as a shell command the moment the skill is invoked, before
# the model reads the text. The setting turns that off for the project.
#
# Same posture as register-bootstrap-hook.sh: set one key, preserve every
# other key and their order, re-serialize with 2-space indent (so an inline
# array elsewhere in the file is re-emitted one element per line), refuse a
# file that is not a JSON object, and prove with a re-parse guard that the
# write equals the original document plus this one key. A file that already
# has the key true is never rewritten. `.claude/settings.local.json` is never
# touched.
#
# Usage: set-skill-shell-setting.sh <status|ensure> <path-to-settings.json>
#
# `status` writes nothing and prints one of: already-set, unset,
#   refused-unparseable.
# `ensure` prints one of:
#   already-set         — the key is true; no write
#   set                 — the file was created, or the key added or changed
#   refused-unparseable — no write; the existing file is not a JSON object
#   refused-guard       — no write; the re-parse guard saw more than the change
#   refused-unwritable  — no write; the file could not be written
#
# Exit: 0 on already-set, unset or set; 2 on usage; 3 on refusal (unparseable
# or guard); 5 when the file cannot be written.

MODE="${1:-}"
TARGET="${2:-}"
if [[ ( "$MODE" != "status" && "$MODE" != "ensure" ) || -z "$TARGET" ]]; then
    echo "Usage: set-skill-shell-setting.sh <status|ensure> <path-to-settings.json>" >&2
    exit 2
fi

KEY="disableSkillShellExecution"

result=$(python3 -c '
import copy, json, os, sys

mode, target, key = sys.argv[1], sys.argv[2], sys.argv[3]

if os.path.exists(target) and os.path.getsize(target) > 0:
    with open(target, encoding="utf-8") as fh:
        raw = fh.read()
else:
    raw = ""

if raw.strip():
    try:
        doc = json.loads(raw)
    except Exception:
        print("refused-unparseable")
        sys.exit(3)
    if not isinstance(doc, dict):
        print("refused-unparseable")
        sys.exit(3)
else:
    doc = {}

if doc.get(key) is True:
    print("already-set")
    sys.exit(0)
if mode == "status":
    print("unset")
    sys.exit(0)

want = copy.deepcopy(doc)
want[key] = True
candidate = json.dumps(want, indent=2) + "\n"

# The guard: the bytes we are about to write must reproduce the original
# document with only this key changed.
check = json.loads(candidate)
check_orig = copy.deepcopy(check)
if key in doc:
    check_orig[key] = doc[key]
else:
    check_orig.pop(key, None)
if check != want or check_orig != doc:
    print("refused-guard")
    sys.exit(3)

try:
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(candidate)
except OSError:
    print("refused-unwritable")
    sys.exit(5)
print("set")
' "$MODE" "$TARGET" "$KEY") || {
    status=$?
    [[ -n "$result" ]] && echo "$result"
    exit "$status"
}

echo "$result"
