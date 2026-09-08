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
#   unparseable   — content we must not rewrite: not valid JSON, not a JSON
#                   object, or a `hooks` / `hooks.<event>` of a type we cannot
#                   append to. The last two are deliberate — see the classify()
#                   comment; a file the registrar will refuse must not read as
#                   one the sync can safely deliver to.
#   unwritable    — the hook is NOT registered here and this is not a file we
#                   could add it to: a symlink (writing follows it out of the
#                   tree), a directory, a FIFO, a device. Separate from
#                   `unparseable` because the two are withheld for different
#                   reasons and the operator is told which — and separate from
#                   `registered` because THAT question is answered by CONTENT,
#                   whatever the file's type. See the dispatch below.
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

# THE NEEDLE HAS TO IDENTIFY OUR HOOK, and non-empty is not the same test. The
# needle is used as `needle in str(command)`, so any string SHORT enough to
# appear inside an unrelated command answers `registered` for a settings.json
# that names something else entirely: measured, `' '` (one space), `'.'` and
# `'s'` each read `registered` against a file whose only entry was
# `some-other.sh`, and the sync would then skip a repo whose hook never runs.
# The empty-value guard above closed one value; this closes the class, by
# requiring the shape of an actual hook FILENAME.
if [[ ! "$HOOK_BASENAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.sh$ ]]; then
    echo "bootstrap-status.sh: BOOTSTRAP_HOOK_BASENAME must be a hook filename ending in .sh, got '$HOOK_BASENAME'" >&2
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
        # A SYMLINK IS TWO SEPARATE QUESTIONS, and answering them with one
        # word cost a healthy consumer its delivery. "Is the hook registered
        # here?" is about CONTENT and is answered by reading THROUGH the link,
        # exactly as it is for a regular file. "May we write here?" is about
        # the FILE, and the answer is no -- `open(target, "w")` follows the
        # link, so the edit lands outside the tree while `git add` stages an
        # unchanged symlink.
        #
        # Collapsing both into `unparseable` made sync.sh withdraw the whole
        # repo's delivery mode over a settings.json that was working: measured,
        # a consumer left perfect by the previous sync (hook current, payload
        # current, fleet-memory registered) whose ONLY difference was a
        # symlinked settings.json had its AGENTS.md pushed from the 5,687-byte
        # stub back to 57,971 bytes of inline guidance -- while the hook stayed
        # registered and kept installing the same 57 kB into
        # ~/.claude/CLAUDE.md, so that repo's sessions loaded the guidance
        # TWICE. `0 failed` on the tally.
        #
        # So: registered is registered, whatever the file's type; anything else
        # through a link is `unwritable`, which withholds the WRITE rather than
        # the repo. A dangling link, or one pointing at a FIFO or a device,
        # reads as `unwritable` too -- `-s` is false for all of them, and
        # writing there would create or block on something outside the tree.
        if [[ -L "$1" ]]; then
            # `-f` as well as `-s`, because both follow the link: a link to a
            # DIRECTORY is `-s` true (directories have nonzero size), and
            # `classify < "$1"` would then redirect stdin from a directory and
            # kill python3 with a core-level fatal error.
            link_state="missing"
            [[ -f "$1" && -s "$1" ]] && link_state="$(classify < "$1")"
            if [[ "$link_state" == "registered" ]]; then
                echo "registered"
            else
                echo "unwritable"
            fi
        elif [[ ! -e "$1" ]]; then
            echo "missing"
        elif [[ ! -f "$1" ]]; then
            # A DIRECTORY IS ONE REPO'S SHAPE, NOT THE RUN'S. This branch used
            # to `exit 2` as a caller error, and sync.sh calls this script in a
            # plain command substitution under `set -euo pipefail` -- so a
            # consumer repo that committed a TREE at .claude/settings.json (a
            # shape git stores and checks out perfectly well) ended the whole
            # fleet run at whichever repo sorted first: measured, exit 2 after
            # 1 of 6 repos, no summary, no failure tally. One repo's shape must
            # fail one repo, which is the same rule the gitignore handling and
            # the per-repo `continue`s exist for.
            #
            # `unwritable` rather than `unparseable` because that is what was
            # found: nothing here can be parsed OR appended to, and the caller
            # prints which. A FIFO, a socket and a device land here too, and
            # none of them is opened -- `-f` is a stat, so there is nothing to
            # block on.
            echo "unwritable"
        elif [[ ! -s "$1" ]]; then
            echo "missing"
        else
            classify < "$1"
        fi
        ;;
esac
