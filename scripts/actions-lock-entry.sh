#!/usr/bin/env bash
# actions-lock-entry.sh — keep .github/workflows/actions.lock consistent when
# the sync writes a workflow that has no `uses:` lines.
#
#   actions-lock-entry.sh check <lockfile> <workflow-path>
#       exit 0  already consistent (nothing to do)
#       exit 10 would change (the entry is missing or different, or a
#               dependency no workflow list references is present)
#       exit 2  unrecognized shape or usage error (nothing is edited)
#   actions-lock-entry.sh apply <lockfile> <workflow-path>
#       rewrites the lockfile in place when it would change; prints
#       "updated" or "unchanged". Exit 2 and an untouched file on any shape it
#       does not recognize. Never half-edits: the result is built in a temp
#       file and copied over only after it parsed completely.
#
# What "consistent" means is what `gh actions-lock --no-fix` enforces: every
# workflow file has a `workflows:` key, and every `dependencies:` key is
# listed by some workflow. The entry written is
#     '<workflow-path>': []
# (a workflow with no `uses:` lines), replacing any entry for that path in
# place or inserted in byte-sorted position among the keys. Then every
# dependency no remaining workflow list names is dropped. Liveness is "named
# by some workflow list": in the real lockfiles checked (skills-evals, 4A,
# and the repos fixed by hand) every dependency appears in at least one list
# and gh actions-lock itself writes the lists as the full set a workflow
# needs, so an entry named by no list is an orphan the tool rejects. When no
# dependency is left the whole `dependencies:` section is omitted, as
# `gh actions-lock` itself writes a lockfile with none.
#
# The text is edited, not re-serialized, so the header comments, the 4-space
# indent, the single quotes and the key order stay exactly as gh wrote them.
# Recognized shape (anything else is refused): leading `#` comment lines;
# `version: 'v0.0.N'`; `workflows:` with `    '<path>':` keys each followed by
# `        - '<ref>'` lines or written `    '<path>': []`; then an optional
# `dependencies:` with `    '<ref>':` keys, each followed by 8-space-indented
# `key: value` lines. Plain bash and awk, both present on the sync runner.
set -euo pipefail
export LC_ALL=C

usage() { echo "usage: ${0##*/} check|apply <lockfile> <workflow-path>" >&2; exit 2; }
[[ $# -eq 3 ]] || usage
mode=$1; lock=$2; wf=$3
[[ "$mode" == "check" || "$mode" == "apply" ]] || usage
if [[ -L "$lock" || ! -f "$lock" ]]; then
    echo "${0##*/}: $lock is not a regular file" >&2
    exit 2
fi
[[ "$wf" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "${0##*/}: unsupported workflow path" >&2; exit 2; }

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

awk -v target="$wf" '
function bad(why) { print FILENAME ": " why " (line " NR ")" > "/dev/stderr"; failed = 1; exit 2 }
BEGIN { section = "header"; nw = 0; nd = 0 }
{
    line = $0
    if (section == "header") {
        if (line ~ /^#/) { head[++nh] = line; next }
        if (line ~ /^version: \047v0\.0\.[0-9]+\047$/) { ver = line; section = "afterversion"; next }
        bad("expected a # comment or the version line")
    }
    if (section == "afterversion") {
        if (line == "workflows:") { section = "workflows"; next }
        bad("expected workflows: after the version")
    }
    if (line == "dependencies:" && section == "workflows") { section = "dependencies"; seendeps = 1; next }
    if (section == "workflows") {
        if (line ~ /^    \047[^\047]+\047: \[\]$/) {
            nw++; wkey[nw] = line; sub(/^    \047/, "", wkey[nw]); sub(/\047:.*$/, "", wkey[nw]); wtext[nw] = line; open = 0; next
        }
        if (line ~ /^    \047[^\047]+\047:$/) {
            nw++; wkey[nw] = line; sub(/^    \047/, "", wkey[nw]); sub(/\047:$/, "", wkey[nw]); wtext[nw] = line; open = 1; next
        }
        if (line ~ /^        - \047[^\047]+\047$/ && open) { wtext[nw] = wtext[nw] "\n" line; next }
        bad("unrecognized line in workflows:")
    }
    if (section == "dependencies") {
        if (line ~ /^    \047[^\047]+\047:$/) {
            nd++; dkey[nd] = line; sub(/^    \047/, "", dkey[nd]); sub(/\047:$/, "", dkey[nd]); dtext[nd] = line; usesopen = 0; next
        }
        if (line ~ /^        [a-z_]+: [^ ].*$/ && nd > 0) { dtext[nd] = dtext[nd] "\n" line; usesopen = 0; next }
        # A composite action lists what it uses: `        uses:` and then
        # a deeper list of refs (the pages upload action, say).
        if (line == "        uses:" && nd > 0) { dtext[nd] = dtext[nd] "\n" line; usesopen = 1; next }
        if (line ~ /^            - \047[^\047]+\047$/ && usesopen) {
            dtext[nd] = dtext[nd] "\n" line; r = line; sub(/^            - \047/, "", r); sub(/\047$/, "", r)
            duses[nd] = duses[nd] " " r; next
        }
        bad("unrecognized line in dependencies:")
    }
}
END {
    if (failed) exit 2
    if (section != "workflows" && section != "dependencies") { print "no workflows: section" > "/dev/stderr"; exit 2 }
    if (nw == 0 && section == "workflows") { print "empty workflows: section" > "/dev/stderr"; exit 2 }
    newtext = "    \047" target "\047: []"
    placed = 0
    for (i = 1; i <= nw; i++) if (wkey[i] == target) { wtext[i] = newtext; placed = 1 }
    out_n = 0
    for (i = 1; i <= nw; i++) {
        if (!placed && wkey[i] > target) { order[++out_n] = newtext; placed = 1 }
        order[++out_n] = wtext[i]
    }
    if (!placed) order[++out_n] = newtext
    # Live refs: every ref named in any workflow block after the edit.
    for (i = 1; i <= out_n; i++) {
        m = split(order[i], parts, "\n")
        for (j = 2; j <= m; j++) { r = parts[j]; sub(/^        - \047/, "", r); sub(/\047$/, "", r); live[r] = 1 }
    }
    # ...and, transitively, every ref a live composite action itself uses
    # (its `uses:` list): gh actions-lock keeps those out of the workflow
    # lists but rejects the lockfile if they are dropped.
    do {
        grew = 0
        for (i = 1; i <= nd; i++) {
            if (!(dkey[i] in live)) continue
            k = split(duses[i], us, " ")
            for (j = 1; j <= k; j++) if (!(us[j] in live)) { live[us[j]] = 1; grew = 1 }
        }
    } while (grew)
    for (i = 1; i <= nh; i++) print head[i]
    print ver
    print "workflows:"
    for (i = 1; i <= out_n; i++) print order[i]
    kept = 0
    for (i = 1; i <= nd; i++) if (dkey[i] in live) kept++
    if (kept > 0) {
        print "dependencies:"
        for (i = 1; i <= nd; i++) if (dkey[i] in live) print dtext[i]
    }
}' "$lock" > "$tmp" || { echo "${0##*/}: $lock has a shape this helper does not recognize — not edited" >&2; exit 2; }

# A file that differs only by a missing final newline is consistent: gh
# actions-lock reads it without one (checked against v0.3.0), so neither mode
# touches it. The comparison copy gets the newline; the file itself does not.
norm=$(mktemp)
trap 'rm -f "$tmp" "$norm"' EXIT
cat "$lock" > "$norm"
[[ -z "$(tail -c1 "$lock")" ]] || echo >> "$norm"
if cmp -s "$tmp" "$norm"; then
    [[ "$mode" == "apply" ]] && echo unchanged
    exit 0
fi
if [[ "$mode" == "check" ]]; then
    exit 10
fi
cat "$tmp" > "$lock"
echo updated
