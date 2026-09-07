#!/usr/bin/env bash
# fleet-memory.sh — deliver the fleet's managed guidance ONCE per session.
#
# WHY THIS EXISTS
# ---------------
# The managed guidance used to be inlined into every repo's AGENTS.md, which
# Claude Code imports from that repo's CLAUDE.md. A session with N repos
# attached therefore loaded N identical copies. Measured 2026-08-29 on a hosted
# multi-repo session: 37 memory files, 332.3k tokens — a third of a 1M window —
# almost all of it ONE ~52 kB managed block repeated 19 times.
#
# User-level memory (~/.claude/CLAUDE.md) is read ONCE per session no matter how
# many repos are attached. That is where the fleet-wide block belongs; each repo
# keeps only what is genuinely its own, plus a small stub.
#
# THE THREE MEASUREMENTS THIS DESIGN RESTS ON
# -------------------------------------------
# Local CLI 2.1.251, tool-free single-turn probes, baseline context 34.5k:
#
#   1. An `@import` inlines ONLY when the target resolves INSIDE the project
#      tree. A nested in-tree import works (53,441 tokens, canary retrieved);
#      `@~/.claude/...` and out-of-tree absolute paths silently load NOTHING
#      (34,4xx, canary absent) — no error, no warning. So "one shared file that
#      every repo imports" is NOT available, which is why the content is
#      delivered to USER memory instead of imported from a shared path.
#   2. A SessionStart hook runs BEFORE memory is assembled. With no
#      ~/.claude/CLAUDE.md at launch, this hook wrote one and the SAME session
#      read it (53,439 tokens, canary retrieved). That is what makes the first
#      session in a fresh container correct rather than one session late.
#   3. The hosted harness does the same. Verified in a cloud session on
#      _agent-guidance@claude/agents-md-redundancy-uvw9am: the canary was in
#      context at session start, attributed to
#      "Contents of /root/.claude/CLAUDE.md (user's private global instructions
#      for all projects)". Local-only evidence would not have settled this,
#      because the hosted harness assembles memory itself.
#
# WHAT IT WRITES
# --------------
# A MARKED BLOCK, never the whole file. A developer's own ~/.claude/CLAUDE.md is
# theirs; everything outside the markers is preserved byte-for-byte.
#
# It always exits 0. A guidance delivery that breaks the session is worse than
# one that degrades, and the repo stub is the floor: every repo still carries
# the load-bearing rules inline, so a DEGRADED session is diminished, not blind.
# The verdict line below is what keeps that degradation VISIBLE rather than
# silent — the failure mode this hook most needs to avoid is working quietly
# until the day it doesn't.
set -uo pipefail

BEGIN_MARK='<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->'
END_MARK='<!-- END FLEET GUIDANCE -->'

# Payload ships beside this hook, deliberately OUTSIDE any memory-file path
# (only CLAUDE.md / AGENTS.md are auto-loaded), so it costs zero always-on
# context in the repo that carries it.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
PAYLOAD="${FLEET_GUIDANCE_PAYLOAD:-$HOOK_DIR/fleet-guidance.md}"

DEST_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$DEST_DIR/CLAUDE.md"

# THE STATE FILE, and why this hook writes one at all.
#
# This hook can only report what it did TO THE FILE. What the session actually
# LOADED is a different question, and the InstructionsLoaded hook beside this
# one is the only thing that can answer it — but only if it has something to
# compare against. That is this file: the version, the byte count and the
# digest of the payload this run installed.
#
# The receipt written back the other way (instructions-receipt.state) is the
# return path. Measured on CLI 2.1.261: an InstructionsLoaded hook's stdout
# reaches nothing — not the CLI's stdout, not its stderr, not the transcript —
# so the load-time verdict has no way to the session on its own. Printing the
# PREVIOUS session's receipt here is that way, and it is why a mismatch is
# never silent for more than one session.
STATE_FILE="$DEST_DIR/fleet-guidance.state"
RECEIPT_FILE="$DEST_DIR/instructions-receipt.state"
LOG_FILE="$DEST_DIR/instructions-log.jsonl"

# Print what the last session's load-time hook recorded, beside this run's own
# verdict. A healthy agents-md stays quiet — it is the mismatch that has to
# travel, and a line printed every session on every machine stops being read.
#
# EXACTLY ONCE, which is the half that needs a flag. The receipt carries
# `unread=1` until a SessionStart announces it; this is the only reader, so
# clearing it here is what "never silent for more than one session" means
# operationally. Without it, a machine whose load-time hook stopped running —
# an older CLI, a settings entry lost — re-announces one stale verdict at
# every session start forever, and two sessions sharing one config dir erase
# each other's verdicts before either is announced.
report_previous_session() {
    [ -r "$RECEIPT_FILE" ] || return 0
    local unread fleet agents
    unread="$(grep -m1 -- '^unread=' "$RECEIPT_FILE" 2>/dev/null | cut -d= -f2-)"
    [ "$unread" = "1" ] || return 0
    fleet="$(grep -m1 -- '^fleet=' "$RECEIPT_FILE" 2>/dev/null | cut -d= -f2-)"
    agents="$(grep -m1 -- '^agents=' "$RECEIPT_FILE" 2>/dev/null | cut -d= -f2-)"
    [ -n "$fleet" ] && echo "fleet-guidance: previous session $fleet"
    case "$agents" in
        ""|current*) ;;
        *) echo "agents-md: previous session $agents" ;;
    esac
    mark_receipt_read
    return 0
}

# Clear the flag, preserving the verdict itself so `cat`ing the receipt still
# says what happened. Best-effort like write_state: a receipt that cannot be
# rewritten costs a repeated line, never the session. The mv renames over the
# receipt without following a link planted there.
#
# A FIXED TMP NAME WAS A SECOND, PERMANENT ROUTE TO "ANNOUNCED FOREVER". With
# `$RECEIPT_FILE.read.tmp` hard-coded, a DIRECTORY planted at that path made
# `rm -f` fail, the redirection fail, and `2>/dev/null` swallow both: the flag
# was never cleared and the same verdict was announced at 5 of 5 consecutive
# session starts, with nothing on stderr. mktemp cannot collide with anything
# already there, and when it cannot create a file at all this says so out loud
# rather than looping in silence -- the one thing a "never silent for more
# than one session" contract cannot afford is a silent failure to clear.
#
# mktemp creates the file 0600, which is also what keeps the receipt at the
# mode open_owned gave it. The older `sed > "$tmp"` inherited the umask and
# quietly relaxed it to 0644 at the first session start after every write.
#
# THE WINDOW, recorded the way the hook records its own (write_receipt): the
# caller greps `unread` out of the receipt, announces, and only then does this
# re-read the file and rewrite it. A verdict written by a load-time hook
# BETWEEN that grep and this sed is marked read without ever being announced.
# It is microseconds wide and this hook runs before memory is assembled, so it
# was not provoked; closing it means sed-ing the exact verdict that was
# announced rather than whatever the file now holds, which trades a lost line
# for a receipt this hook could rewrite from a stale read. Recorded rather
# than closed, and named here so the sibling that has the same shape is not
# the only one that documents it.
mark_receipt_read() {
    local tmp
    if ! tmp="$(mktemp "$RECEIPT_FILE.read.XXXXXX" 2>/dev/null)"; then
        echo "fleet-guidance: could not clear the previous session's receipt — the line above will repeat next session."
        return 0
    fi
    # Same wrapping as write_state, for the same reason: the failure of
    # `> "$tmp"` is the shell's message, not sed's, so an inner 2>/dev/null
    # would not cover it.
    {
        if sed 's/^unread=1$/unread=0/' "$RECEIPT_FILE" > "$tmp"; then
            mv "$tmp" "$RECEIPT_FILE"
        fi
        rm -f "$tmp"
    } 2>/dev/null
    return 0
}

# Record what this run installed. Best-effort by construction: a state file
# that cannot be written costs the next session its load-time verdict and
# nothing else, so it must never turn a working delivery into a DEGRADED one.
# The outer `2>/dev/null` covers the REDIRECTION, not just the commands inside
# the braces. `{ ...; } > "$F" 2>/dev/null` applies the suppression only after
# the redirection has been set up, so a directory at that path printed
# "…state.tmp: Is a directory" into the session beside the verdict. Wrapping
# the whole compound is what makes "costs the next session its verdict and
# nothing else" true.
write_state() {   # <verdict> <version> <bytes> <sha256>
    {
        {
            printf 'version=%s\n' "$2"
            printf 'bytes=%s\n' "$3"
            printf 'sha256=%s\n' "$4"
            printf 'verdict=%s\n' "$1"
            printf 'ts=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
        } > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
        rm -f "$STATE_FILE.tmp"
    } 2>/dev/null
    return 0
}

degraded() {
    echo "fleet-guidance: DEGRADED — $1. Repo stub only; read the fleet guidance in _agent-guidance/agents-md/base.md before non-trivial work."
    exit 0
}

# Write $1 with the managed block stripped out and everything else verbatim.
#
# NEVER OVERWRITE A FILE WHOSE CURRENT CONTENTS COULD NOT BE READ. On a durable
# machine ~/.claude/CLAUDE.md is the developer's own global memory and this hook
# is a guest in it. An earlier draft fell back to an EMPTY strip result when the
# read failed, then appended the block to that emptiness and copied it over the
# top — so a file that was writable but not readable (mode 0200, a mount quirk,
# an ACL) was silently REPLACED by the fleet block alone, and the verdict still
# said `installed`. Measured: a personal CLAUDE.md destroyed, with a success
# line. Every unreadable-or-unparseable shape degrades instead.
strip_managed_block() {
    local out="$1"
    if [ ! -e "$DEST" ]; then : > "$out"; return 0; fi
    [ -f "$DEST" ] || degraded "$DEST exists but is not a regular file — refusing to replace it"
    [ -r "$DEST" ] || degraded "$DEST exists but is not readable — refusing to overwrite content I cannot preserve"

    if ! BEGIN_MARK="$BEGIN_MARK" END_MARK="$END_MARK" awk '
        BEGIN { b = ENVIRON["BEGIN_MARK"]; e = ENVIRON["END_MARK"]; skip = 0 }
        index($0, b) == 1 { skip = 1; next }
        index($0, e) == 1 { skip = 0; next }
        !skip { print }
    ' "$DEST" > "$out.raw" 2>/dev/null; then
        degraded "could not read or parse $DEST — refusing to overwrite content I cannot preserve"
    fi

    # Drop trailing blank lines so repeated runs cannot grow the file, and so a
    # file whose ONLY content was the block collapses to empty rather than
    # accumulating a blank line per run.
    local kept; kept="$(cat "$out.raw" 2>/dev/null)"
    if [ -n "$kept" ]; then printf '%s\n' "$kept" > "$out"; else : > "$out"; fi
    rm -f "$out.raw"
}

# ── Opt-out ────────────────────────────────────────────────────────────────
#
# FLEET_GUIDANCE_SKIP exists because user memory is GLOBAL on a durable
# machine. ~/.claude/CLAUDE.md is read in EVERY session on that box, so once a
# fleet repo has been opened once, the guidance rides along into unrelated
# projects too. That is a fair trade for some machines and not others, and it
# is the developer's call, not this hook's.
#
# It REMOVES an already-installed block rather than merely declining to write
# one. Skipping the write alone would leave a block installed by an earlier
# session sitting in the file and still loading — an opt-out that does not opt
# you out, which is worse than none because it looks like it worked.
#
# `0`, `false`, `no` and `off` are honoured as OFF. Treating any non-empty
# value as ON would make `FLEET_GUIDANCE_SKIP=0` mean "skip", and a flag whose
# disabled spelling enables it is a trap worth two lines of code to avoid.
case "${FLEET_GUIDANCE_SKIP:-}" in
    ""|0|false|FALSE|no|NO|off|OFF) ;;
    *)
        # Everything, not just the block: a state file and a log left behind
        # by an earlier session are still this hook's artifacts sitting in
        # someone's config dir after they opted out.
        rm -f "$STATE_FILE" "$RECEIPT_FILE" "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null
        if [ -e "$DEST" ]; then
            tmp="$(mktemp 2>/dev/null)" || degraded "mktemp failed"
            trap 'rm -f "$tmp" "$tmp.raw"' EXIT
            strip_managed_block "$tmp"
            if cmp -s "$tmp" "$DEST" 2>/dev/null; then
                echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) — no managed block present"
            elif cp "$tmp" "$DEST" 2>/dev/null; then
                echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) — removed the managed block from ~/.claude/CLAUDE.md; your own content is untouched"
            else
                degraded "FLEET_GUIDANCE_SKIP is set but $DEST could not be rewritten to remove the managed block"
            fi
        else
            echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)"
        fi
        exit 0
        ;;
esac

# Before anything that can degrade, so a load-time mismatch recorded last
# session is still reported by a run that cannot deliver this time.
report_previous_session

[ -n "$HOOK_DIR" ] || degraded "cannot resolve hook directory"
[ -r "$PAYLOAD" ]  || degraded "no readable payload at $PAYLOAD"
[ -s "$PAYLOAD" ]  || degraded "payload at $PAYLOAD is empty"

mkdir -p "$DEST_DIR" 2>/dev/null || degraded "cannot create $DEST_DIR"

# Short content id, so the verdict names WHICH guidance landed. Any of these
# three digest tools may be absent; a missing one is cosmetic, never fatal.
payload_sha="$( { sha256sum "$PAYLOAD" 2>/dev/null || shasum -a 256 "$PAYLOAD" 2>/dev/null || openssl dgst -sha256 "$PAYLOAD" 2>/dev/null; } \
            | tr ' ' '\n' | grep -oE '^[0-9a-f]{64}$' | head -1 )"
version="${payload_sha:0:8}"
[ -n "$version" ] || version="unknown"

tmp="$(mktemp 2>/dev/null)" || degraded "mktemp failed"
trap 'rm -f "$tmp" "$tmp.raw"' EXIT

strip_managed_block "$tmp"
[ -s "$tmp" ] && printf '\n' >> "$tmp"

{
    printf '%s\n' "$BEGIN_MARK"
    printf '<!-- fleet-guidance-version: %s -->\n' "$version"
    cat "$PAYLOAD"
    printf '%s\n' "$END_MARK"
} >> "$tmp" 2>/dev/null || degraded "could not assemble the guidance block"

bytes="$(wc -c < "$PAYLOAD" 2>/dev/null | tr -d ' ')"

if cmp -s "$tmp" "$DEST" 2>/dev/null; then
    write_state current "$version" "$bytes" "$payload_sha"
    echo "fleet-guidance: current (v$version, ${bytes} bytes) — ~/.claude/CLAUDE.md"
    exit 0
fi

if cp "$tmp" "$DEST" 2>/dev/null; then
    write_state installed "$version" "$bytes" "$payload_sha"
    echo "fleet-guidance: installed (v$version, ${bytes} bytes) -> ~/.claude/CLAUDE.md"
else
    degraded "could not write $DEST"
fi
exit 0
