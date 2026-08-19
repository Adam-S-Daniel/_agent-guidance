#!/usr/bin/env bash
set -euo pipefail
#
# check-agents-md.sh — Structural validator for a generated AGENTS.md.
#
# Why this exists alongside the "Self-guidance is current" staleness check
# (the diff against `build-agents-md.sh` output) rather than being subsumed
# by it: staleness only proves the file is a FIXED POINT of the regen
# recipe — that regenerating it produces byte-identical output — and a
# doubled file can be a fixed point too.
#
# This actually happened (commit c86465f, carried through dea99d3d, merge
# 81391bd, 2be482e, 7b87581). Some tooling split AGENTS.md on the FIRST
# occurrence of the substring "## Repo-specific additions" instead of the
# real marker LINE. The managed block's own BEGIN header quotes the marker
# verbatim:
#
#   <!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
#
# — so a first-occurrence split anchors on line 1 of that header, not on the
# real "## Repo-specific additions" heading further down, and treats the
# ENTIRE previous file (the whole prior managed block included) as
# "repo-specific content to preserve". The next regen then PREPENDS a fresh
# managed block on top of that preserved copy, and the file now carries two
# managed blocks — two, sometimes contradictory, copies of the same
# guidance. The corrupted BEGIN header line leaves a telltale truncated
# fragment further down the file: the tail end of that same quoted comment,
# stranded on its own line —
#
#   ## Repo-specific additions" -->
#
# — which starts with the real marker text but is not the marker: it has
# `" -->` trailing after it. That fragment is what invariant 5 below exists
# to catch by name, because it is the single most diagnostic clue for
# "this file suffered a first-occurrence split", as opposed to some other
# way of being malformed.
#
# The doubled file regenerates to itself forever: `build-agents-md.sh`'s
# output is deterministic, and `sed -n '/^## Repo-specific additions/,$p'`
# — anchored at the START of the line but not the END — matches that same
# truncated fragment line first on every subsequent run, so the "repo
# specific" tail it extracts is always the same (corrupted) slice. Nothing
# about the recipe converges toward a fix; nothing about the recipe detects
# the problem either. Only a check that looks at the file's STRUCTURE — how
# many of each marker there are, and in what order — can tell a doubled file
# from a well-formed one, which is what this script does.
#
# Usage: check-agents-md.sh [path ...]   (defaults to AGENTS.md)
#
# Exit 0 if every named file is well-formed. Exit 1 otherwise, printing one
# plain-English line per violated invariant, each naming the file and the
# invariant it broke. No `::error::` annotations are emitted here — the
# calling workflow step adds its own if it wants GitHub Actions to surface
# this as a check annotation.
#
# Invariants (per file):
#   1. Exactly one line equal to "## Repo-specific additions" (whole-line).
#   2. Exactly one line containing "BEGIN MANAGED SECTION".
#   3. Exactly one line containing "END MANAGED SECTION".
#   4. Exactly one line containing "Managed by".
#   5. No line that STARTS WITH the marker text but has trailing characters
#      after it (the truncated-header fingerprint described above).
#   6. The single BEGIN MANAGED SECTION line precedes the single END MANAGED
#      SECTION line, which precedes the single "## Repo-specific additions"
#      line — the ordering sync.sh's parse depends on (managed content
#      above the marker, repo-specific content at-and-below it).

MARKER="## Repo-specific additions"

check_file() {
    local file="$1"
    local ok=0

    if [[ ! -f "$file" ]]; then
        echo "check-agents-md: $file — file does not exist"
        return 1
    fi

    # Invariants 1-4 all have the same shape — exactly one line matching some
    # pattern — and the same two ways to go wrong, which read very
    # differently to a human even though the fix is the same class of bug:
    # zero means the marker is missing outright; two-or-more, for the
    # BEGIN/END/"Managed by" trio, is exactly what a doubled managed block
    # looks like (c86465f). count_check prints the invariant-appropriate
    # wording for whichever it finds; count_check itself never fails the
    # calling `set -e` shell even when grep -c matches nothing (exit 1).
    count_check() {
        local label="$1" pattern="$2" exact="$3" count
        if [[ "$exact" == "x" ]]; then
            count=$(grep -cxF -- "$pattern" "$file" || true)
        else
            count=$(grep -c -- "$pattern" "$file" || true)
        fi
        if [[ "$count" -eq 1 ]]; then
            return 0
        elif [[ "$count" -eq 0 ]]; then
            echo "check-agents-md: $file — expected exactly one $label, found none"
        else
            echo "check-agents-md: $file — expected exactly one $label, found $count — a doubled managed block (see c86465f) looks exactly like this"
        fi
        return 1
    }

    # Invariant 1: exactly one line that IS the marker, whole-line.
    count_check "line equal to \"$MARKER\"" "$MARKER" x || ok=1

    # Invariant 2: exactly one BEGIN MANAGED SECTION line.
    count_check 'line containing "BEGIN MANAGED SECTION"' 'BEGIN MANAGED SECTION' "" || ok=1

    # Invariant 3: exactly one END MANAGED SECTION line.
    count_check 'line containing "END MANAGED SECTION"' 'END MANAGED SECTION' "" || ok=1

    # Invariant 4: exactly one "Managed by" line.
    count_check 'line containing "Managed by"' 'Managed by' "" || ok=1

    # Invariant 5: no line starts with the marker text but has trailing
    # characters after it. A genuine marker line is EXACTLY "## Repo-specific
    # additions" with nothing following; a line matching this pattern is the
    # truncated fingerprint of the managed block's own BEGIN header
    # (`## Repo-specific additions" -->`) stranded on its own line — the
    # specific, diagnostic sign of a first-occurrence marker split (see the
    # header comment above). The trailing "." in the pattern requires at
    # least one more character after the marker text, so the real marker
    # line — which has nothing after it — does not match.
    local fingerprint_lines
    fingerprint_lines=$(grep -nE -- "^${MARKER}." "$file" || true)
    if [[ -n "$fingerprint_lines" ]]; then
        while IFS= read -r hit; do
            echo "check-agents-md: $file — line ${hit%%:*} starts with the repo-specific marker but has trailing characters after it (\"${hit#*:}\") — this is the truncated-header fingerprint of a first-occurrence marker split (see c86465f), not a genuine marker line"
        done <<< "$fingerprint_lines"
        ok=1
    fi

    # Invariant 6: BEGIN precedes END precedes the marker. Only meaningful
    # to check when each anchor was found at least once — invariants 2-4
    # already report an anchor that is missing entirely, so this skips
    # rather than piling on a second, less specific complaint about the
    # same absence. Uses the FIRST occurrence of each when duplicates exist
    # (invariant 2/3/1 already flag the duplication itself).
    local begin_line end_line marker_line
    begin_line=$(grep -n -- 'BEGIN MANAGED SECTION' "$file" | head -1 | cut -d: -f1 || true)
    end_line=$(grep -n -- 'END MANAGED SECTION' "$file" | head -1 | cut -d: -f1 || true)
    marker_line=$(grep -nxF -- "$MARKER" "$file" | head -1 | cut -d: -f1 || true)
    if [[ -n "$begin_line" && -n "$end_line" && -n "$marker_line" ]]; then
        if ! { [[ "$begin_line" -lt "$end_line" ]] && [[ "$end_line" -lt "$marker_line" ]]; }; then
            echo "check-agents-md: $file — markers are out of order (BEGIN at line $begin_line, END at line $end_line, marker at line $marker_line) — expected BEGIN before END before the marker, which is the ordering sync.sh's parse depends on"
            ok=1
        fi
    fi

    return "$ok"
}

# ── Main ─────────────────────────────────────────────────────────────────

files=("${@:-AGENTS.md}")
overall=0
for f in "${files[@]}"; do
    if ! check_file "$f"; then
        overall=1
    fi
done

exit "$overall"
