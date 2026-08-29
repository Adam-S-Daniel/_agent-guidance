#!/usr/bin/env bash
# fleet-memory.sh — deliver the fleet's managed guidance ONCE per session.
#
# WHY THIS EXISTS
# ---------------
# The managed guidance used to be inlined into every repo's AGENTS.md, which
# Claude Code imports from that repo's CLAUDE.md. In a session with N repos
# attached that loads N identical copies. Measured 2026-08-29 on a hosted
# multi-repo session: 37 memory files, 332.3k tokens — a third of a 1M window —
# of which the overwhelming majority was ONE ~52 kB managed block repeated.
#
# User-level memory (~/.claude/CLAUDE.md) is read ONCE per session no matter
# how many repos are attached, so that is where the managed block belongs. The
# repos keep only what is genuinely theirs.
#
# THE TWO MEASUREMENTS THIS DESIGN RESTS ON (local CLI 2.1.251, tool-free
# single-turn probes; baseline context 34.5k):
#
#   1. An `@import` inlines ONLY when the target resolves INSIDE the project
#      tree. A nested in-tree import works (53,441 tokens, canary retrieved);
#      `@~/.claude/...` and out-of-tree absolute paths silently load NOTHING
#      (34,4xx, canary absent). So "one shared file every repo imports" is not
#      available — this is why the content is delivered to USER memory instead
#      of imported from a shared path.
#   2. A SessionStart hook runs BEFORE memory is assembled. With no
#      ~/.claude/CLAUDE.md present at launch, this hook wrote one and the SAME
#      session read it (53,439 tokens, canary retrieved). That is what makes
#      first-session-in-a-fresh-container correct rather than one-session-late.
#
# It writes a MARKED BLOCK, never the whole file: a developer's own
# ~/.claude/CLAUDE.md is theirs, and anything outside the markers is preserved
# byte-for-byte.
set -uo pipefail

BEGIN_MARK='<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->'
END_MARK='<!-- END FLEET GUIDANCE -->'

# Resolve the payload: shipped beside the hook, outside any memory-file path so
# it costs zero always-on context.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$HOOK_DIR/fleet-guidance.md"

[ -r "$PAYLOAD" ] || { echo "fleet-guidance: no payload at $PAYLOAD — skipped"; exit 0; }

DEST_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$DEST_DIR/CLAUDE.md"
mkdir -p "$DEST_DIR" 2>/dev/null || { echo "fleet-guidance: cannot create $DEST_DIR — skipped"; exit 0; }

# Preserve everything outside the managed block. A file with no block gets one
# appended; a file with a block gets it replaced in place.
tmp="$(mktemp)" || { echo "fleet-guidance: mktemp failed — skipped"; exit 0; }
trap 'rm -f "$tmp"' EXIT

if [ -f "$DEST" ]; then
  BEGIN_MARK="$BEGIN_MARK" END_MARK="$END_MARK" awk '
    BEGIN { b=ENVIRON["BEGIN_MARK"]; e=ENVIRON["END_MARK"]; skip=0 }
    index($0,b)==1 { skip=1; next }
    index($0,e)==1 { skip=0; next }
    !skip { print }
  ' "$DEST" > "$tmp"
  # Trim trailing blank lines so repeated runs cannot grow the file.
  printf '%s\n' "$(cat "$tmp")" > "$tmp.trim" && mv "$tmp.trim" "$tmp"
  [ -s "$tmp" ] && printf '\n' >> "$tmp"
fi

{
  printf '%s\n' "$BEGIN_MARK"
  cat "$PAYLOAD"
  printf '%s\n' "$END_MARK"
} >> "$tmp"

if cmp -s "$tmp" "$DEST" 2>/dev/null; then
  echo "fleet-guidance: current"
else
  cp "$tmp" "$DEST" && echo "fleet-guidance: installed ($(wc -c < "$PAYLOAD") bytes) -> $DEST"
fi
exit 0
