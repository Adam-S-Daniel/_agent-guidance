#!/usr/bin/env bash
set -euo pipefail
#
# bridge-status.sh — Classify whether a CLAUDE.md bridges to AGENTS.md.
#
# Claude Code reads CLAUDE.md, not AGENTS.md — the managed guidance is only
# visible if CLAUDE.md imports it via a line containing `@AGENTS.md`. This
# script is the single shared classifier: scripts/sync.sh and
# scripts/drift-report.sh both call it so "does this CLAUDE.md bridge?" is
# decided in exactly one place.
#
# Fence-aware on purpose: Claude Code does not expand imports inside fenced
# code blocks, so a fenced `@AGENTS.md` is documentation, not a working
# bridge. The check targets the fleet's standard bridge shape — an import at
# the start of its own line — and deliberately reports exotic mid-line
# imports (e.g. "See @AGENTS.md for details") as no-import.
#
# Usage: bridge-status.sh <path>   — classify a file
#        bridge-status.sh -        — classify stdin
#
# Prints exactly one of:
#   bridge-ok   — a line starting with `@AGENTS.md` (followed by whitespace
#                 or end-of-line), outside fenced code blocks
#   no-import   — content present, no such line
#   missing     — file absent or empty (or empty stdin)

# ── Classify ────────────────────────────────────────────────────────────────

classify() {
    awk '
        /^```/ { in_fence = !in_fence; next }
        !in_fence && /^@AGENTS\.md([[:space:]]|$)/ { matched = 1 }
        { seen = 1 }
        END {
            if (matched) print "bridge-ok"
            else if (seen) print "no-import"
            else print "missing"
        }
    '
}

# ── Dispatch on argument ─────────────────────────────────────────────────────

case "${1:-}" in
    "")
        echo "Usage: bridge-status.sh <path>|-" >&2
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
