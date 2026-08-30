#!/usr/bin/env bash
set -euo pipefail
#
# build-agents-md.sh — Assemble a complete AGENTS.md from base + requested sections.
#
# Usage:  ./scripts/build-agents-md.sh [section ...]
# Example: ./scripts/build-agents-md.sh python docker
#
# Writes the managed portion of AGENTS.md to stdout.
# The caller is responsible for appending the repo-specific section.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# The ALWAYS-ON half. base.md is deliberately NOT inlined here any more: it is
# delivered once per session to user memory by the fleet-memory SessionStart
# hook (see .claude/hooks/fleet-memory.sh). Inlining it made every repo carry
# an identical ~52 kB, so a session with 19 repos attached spent 332.3k tokens
# on 19 copies of one file — measured 2026-08-29. stub.md is what each repo
# still needs on its own: the pointer, the session-start verdict to check, and
# the floor of rules that must hold even when the guidance did not load.
STUB_FILE="$REPO_ROOT/agents-md/stub.md"
PAYLOAD_FILE="$REPO_ROOT/agents-md/base.md"
SECTIONS_DIR="$REPO_ROOT/agents-md/sections"
SECTIONS=("${@}")

# MODE decides whether this repo gets the always-on STUB (the default, and what
# every repo that can receive the fleet-memory hook gets) or the FULL guidance
# inlined the old way.
#
# `full` is the fail-safe, and it is the whole reason the stub is safe to ship.
# A repo that cannot receive the hook — `.claude/` is gitignored there, or its
# settings.json is not parseable and we refuse to rewrite it — would otherwise
# end up with a stub pointing at guidance that will never be delivered to it:
# a repo silently stripped of the very rules the stub tells you to go and read.
# sync.sh sets this to `full` for exactly those repos, so shrinking a repo's
# AGENTS.md and giving it the hook are the same decision, never two.
MODE="${AGENTS_MD_MODE:-stub}"
case "$MODE" in
    stub|full) ;;
    *) echo "ERROR: AGENTS_MD_MODE must be 'stub' or 'full', got '$MODE'." >&2; exit 2 ;;
esac

# ── Managed-section header (machine-readable markers) ──────────────────────
echo "<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE \"## Repo-specific additions\" -->"
echo "<!-- Source: _agent-guidance -->"
echo "<!-- Sections: ${SECTIONS[*]:-none} -->"
echo "<!-- Mode: ${MODE} -->"
echo ""

# ── Managed content ────────────────────────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
    cat "$PAYLOAD_FILE"
else
    cat "$STUB_FILE"
fi

# ── Requested language / tooling sections ──────────────────────────────────
for section in "${SECTIONS[@]}"; do
    section_file="$SECTIONS_DIR/${section}.md"
    if [[ -f "$section_file" ]]; then
        echo ""
        cat "$section_file"
    else
        echo ""
        echo "<!-- WARNING: unknown section '${section}' — no file at sections/${section}.md -->"
    fi
done

echo ""
echo "<!-- END MANAGED SECTION -->"
echo ""
