#!/usr/bin/env bash
set -euo pipefail
#
# bootstrap-status.sh — Classify whether a .claude/settings.json registers the
# skills-bootstrap SessionStart hook.
#
# Delivering `.claude/hooks/skills-bootstrap.sh` into a repo does nothing on
# its own: Claude Code only runs it if `.claude/settings.json` names it in a
# SessionStart hook. A hook that lands but is never registered is SILENTLY
# DEAD — it costs a file in the tree and buys nothing, and no session will ever
# tell you. This script is the single shared classifier: scripts/sync.sh and
# scripts/drift-report.sh both call it so "is the hook registered?" is decided
# in exactly one place — the same arrangement bridge-status.sh gives the
# CLAUDE.md bridge.
#
# The idempotence key is the hook's BASENAME appearing in a
# `hooks.SessionStart[*].hooks[*].command` string. It is deliberately a
# semantic test on the parsed document, not a grep over the file:
#   • a mention inside an unrelated key (a comment-ish field, a different hook
#     event, a "description") must NOT count as registered, or the sync would
#     skip a repo whose hook never runs;
#   • the command string's exact spelling varies across repos that adopted by
#     hand (agentskills' own entry carries no `matcher` and no `timeout`), so
#     matching the whole command verbatim would re-register an already-working
#     repo on every run.
#
# JSON is parsed with python3's stdlib `json`, never a regex or a line scanner:
# this file decides whether a program executes at session start, and a
# hand-rolled parser that mis-reads it either double-registers the hook or
# silently declares a dead one healthy.
#
# Usage: bootstrap-status.sh <path>   — classify a file
#        bootstrap-status.sh -        — classify stdin
#
# Prints exactly one of:
#   registered    — a SessionStart hook command references skills-bootstrap.sh
#   no-entry      — valid JSON, but no such command (hook would never run)
#   unparseable   — content present but not valid JSON (sync must not rewrite it)
#   missing       — file absent or empty (or empty stdin)

HOOK_BASENAME="${BOOTSTRAP_HOOK_BASENAME:-skills-bootstrap.sh}"

classify() {
    python3 -c '
import json, sys

needle = sys.argv[1]
raw = sys.stdin.read()

if not raw.strip():
    print("missing")
    sys.exit(0)

try:
    doc = json.loads(raw)
except Exception:
    print("unparseable")
    sys.exit(0)

# A settings.json whose top level is not an object is not something we can
# reason about, let alone append to. Treat it exactly like malformed JSON so
# the caller takes the same do-not-touch path.
if not isinstance(doc, dict):
    print("unparseable")
    sys.exit(0)

groups = doc.get("hooks", {})
groups = groups.get("SessionStart", []) if isinstance(groups, dict) else []
if not isinstance(groups, list):
    groups = []

for group in groups:
    if not isinstance(group, dict):
        continue
    entries = group.get("hooks", [])
    if not isinstance(entries, list):
        continue
    for entry in entries:
        if isinstance(entry, dict) and needle in str(entry.get("command", "")):
            print("registered")
            sys.exit(0)

print("no-entry")
' "$HOOK_BASENAME"
}

# ── Dispatch on argument ─────────────────────────────────────────────────────

case "${1:-}" in
    "")
        echo "Usage: bootstrap-status.sh <path>|-" >&2
        exit 2
        ;;
    -)
        classify
        ;;
    *)
        if [[ ! -s "$1" ]]; then
            echo "missing"
        else
            classify < "$1"
        fi
        ;;
esac
