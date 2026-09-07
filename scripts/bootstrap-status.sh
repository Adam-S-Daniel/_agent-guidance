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
# The EVENT is a seam, not a constant. BOOTSTRAP_HOOK_EVENT (default
# `SessionStart`) says which `hooks.<event>` array to look in, so the same
# classifier answers for the InstructionsLoaded hook the fleet also delivers.
# It isolates in both directions on purpose: a hook registered under
# SessionStart must NOT read as registered for InstructionsLoaded, or the sync
# would leave a hook nothing ever runs while reporting it live.
#
# Usage: bootstrap-status.sh <path>   — classify a file
#        bootstrap-status.sh -        — classify stdin
#
# Prints exactly one of:
#   registered    — a hook command under that event references the basename
#   no-entry      — valid JSON, but no such command (hook would never run)
#   unparseable   — content we must not rewrite: a symlink, not valid JSON,
#                   not a JSON object, or a `hooks` / `hooks.<event>` of a
#                   type we cannot append to. The last two are deliberate —
#                   see the classify() comment; a file the registrar will
#                   refuse must not read as one the sync can safely deliver
#                   to.
#   missing       — file absent or empty (or empty stdin)

# `${VAR-default}`, not `${VAR:-default}`: with the colon, a set-but-empty
# value silently becomes the default. An empty BASENAME is the dangerous one —
# `"" in str(command)` is TRUE for every string, so the classifier would
# answer `registered` for any file at all, and the sync would skip every repo
# whose hook never runs. Refused as a caller error rather than defaulted.
HOOK_BASENAME="${BOOTSTRAP_HOOK_BASENAME-skills-bootstrap.sh}"
HOOK_EVENT="${BOOTSTRAP_HOOK_EVENT-SessionStart}"

if [[ -z "$HOOK_BASENAME" || -z "$HOOK_EVENT" ]]; then
    echo "bootstrap-status.sh: BOOTSTRAP_HOOK_BASENAME and BOOTSTRAP_HOOK_EVENT must be non-empty; unset them for the defaults" >&2
    exit 2
fi

classify() {
    python3 -c '
import json, sys

needle = sys.argv[1]
event = sys.argv[2]
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

# A `hooks` object we cannot APPEND TO is not "no entry here" -- it is a file
# we must not touch, which is what `unparseable` already means to every
# caller. Reading `{"hooks": null}`, `{"hooks": []}` or a non-list
# `hooks.<event>` as `no-entry` let sync.sh keep delivering: it shrank that
# repo AGENTS.md to the stub and only THEN did register-bootstrap-hook.sh
# refuse (correctly) to edit the file -- leaving a repo stripped of the very
# rules the stub tells you to go and read, with nothing registered to bring
# them back, and `0 failed` on the tally. These are exactly the shapes the
# registrar refuses; classifying them the same way moves the refusal one step
# earlier, before anything is written.
hooks = doc.get("hooks")
# `"hooks" in doc`, not `hooks is not None`: a literal {"hooks": null} has the
# key with a None value, and the registrar refuses that shape too.
if "hooks" in doc and not isinstance(hooks, dict):
    print("unparseable")
    sys.exit(0)
groups = hooks.get(event) if isinstance(hooks, dict) else None
# `event in hooks`, and NOT `groups is not None` -- the same distinction the
# line above draws for the level up, and the same one the registrar draws
# (`isinstance(hooks, dict) and event in hooks and not isinstance(...)`).
# A literal {"hooks": {"<event>": null}} has the key with a None value, so
# `groups is not None` was false and the file classified `no-entry` while the
# registrar refused it: sync.sh wrote and COMMITTED the hook into a consumer
# nothing would ever run it in, printed a WARN, tallied `0 failed`, and did it
# again on every run. It also broke dry-run/real parity, the one thing the
# preview exists to promise -- the dry run said it would append an entry the
# real run cannot append.
if isinstance(hooks, dict) and event in hooks and not isinstance(groups, list):
    print("unparseable")
    sys.exit(0)
if groups is None:
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
' "$HOOK_BASENAME" "$HOOK_EVENT"
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
        # A directory is a caller error, not a fifth classification. `-s` is
        # TRUE for one (directories have nonzero size), so without this guard
        # control reaches `classify < "$1"`, bash redirects stdin from the
        # directory, and python3 dies with a core-level fatal error.
        if [[ -d "$1" ]]; then
            echo "bootstrap-status.sh: $1 is a directory; pass its .claude/settings.json" >&2
            exit 2
        fi
        # A SYMLINK IS CONTENT WE MUST NOT REWRITE, so it belongs in the
        # same class as a file we cannot parse -- and it has to be decided
        # HERE rather than left to the registrar, or the two disagree and the
        # sync delivers a hook it then cannot register. The registrar refuses
        # the same shape; this moves the refusal one step earlier, before
        # anything is written, exactly as the unusable `hooks` shapes do.
        if [[ -L "$1" ]]; then
            echo "unparseable"
        elif [[ ! -s "$1" ]]; then
            echo "missing"
        else
            classify < "$1"
        fi
        ;;
esac
