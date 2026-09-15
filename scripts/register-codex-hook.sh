#!/usr/bin/env bash
set -euo pipefail
#
# register-codex-hook.sh — Idempotently register the fleet-guidance
# SessionStart hook in Codex's USER-level hooks.json.
#
# WHY USER LEVEL, when skills-bootstrap and fleet-memory are both delivered
# per-repo by the sync. Codex (codex-cli 0.154.0, measured 2026-09-14) requires
# every non-managed hook to be REVIEWED AND TRUSTED BY HASH before it runs, and
# it records that trust per hook definition per config layer. A
# `<repo>/.codex/hooks.json` delivered by the sync would therefore cost one
# review prompt in every repo it reached — 19 of them — and each would only
# load at all once that repo's `.codex/` layer was itself trusted. One entry in
# `~/.codex/hooks.json` is reviewed once per MACHINE and then covers every repo
# opened on it, which is the same shape as the delivery it triggers: the hook
# writes the guidance to a GLOBAL destination (~/.codex/AGENTS.md), so a
# per-repo registration would be N registrations driving one global write.
#
# It also keeps `.codex/` out of the fleet's repos entirely. The sync writes
# AGENTS.md, CLAUDE.md and `.claude/`; nothing here adds a fourth surface to 19
# repos for a hook that has nothing repo-specific to say.
#
# AFTER RUNNING THIS, the operator has one manual step and the hook does not
# run until they take it: trust the definition once in the Codex TUI with
# `/hooks` (Codex prints a warning at startup when hooks need review). For a
# one-off non-interactive run, `codex exec --dangerously-bypass-hook-trust`
# runs enabled hooks without persisted trust. Neither is something this script
# can do on the operator's behalf, which is why it says so instead.
#
# THE COMMAND resolves the repo's OWN synced copy of the hook at the git root:
#
#   bash -c 'h="$(git rev-parse --show-toplevel 2>/dev/null)/.claude/hooks/fleet-memory.sh"; [ -f "$h" ] && exec bash "$h"; exit 0'
#
# Codex runs command hooks with the session `cwd` as the working directory and
# may be started from a SUBDIRECTORY, so the docs recommend resolving repo-local
# paths from the git root rather than relatively — that is what the
# `git rev-parse` does. Outside a fleet repo (or outside a git repo at all) the
# file is simply not there and the hook exits 0 having printed nothing: a
# silent no-op, never an error in someone else's project.
#
# APPEND, NEVER OVERWRITE — the same posture as register-bootstrap-hook.sh, for
# the same reason. A machine's ~/.codex/hooks.json may already carry the
# operator's own hooks; this adds a SEPARATE matcher group to
# `hooks.SessionStart` and leaves every existing group's matcher, timeout,
# command and ORDER exactly as it found them. Adding our command inside
# someone else's group would silently inherit their matcher and timeout.
#
# Refuses rather than guesses. If the file is present but not parseable as a
# JSON object, this script writes NOTHING and exits 3.
#
# The safety proof is a semantic guard, not a promise. After building the new
# text we re-parse it and require it to equal, exactly, the ORIGINAL parsed
# document with our one group appended. If that comparison fails the file is
# left untouched. So a successful write provably means "the old hooks plus our
# element" — no key dropped, no value coerced, no group reordered.
#
# Formatting is NOT preserved: the file is re-serialized with 2-space indent.
# Only a file this script actually modifies is reformatted; a machine that is
# already registered is never rewritten at all.
#
# NEVER CREATES ~/.codex. A machine that has never run Codex has no business
# growing a Codex config directory because a guidance repo was checked out on
# it — an empty ~/.codex reads as "Codex is set up here" to everything that
# probes for it, this repo's own hook included. So a missing parent directory
# is a refusal (exit 4) naming what to do, not a `mkdir -p`.
#
# Usage: register-codex-hook.sh [path-to-hooks.json]
#        (default: ${CODEX_HOME:-$HOME/.codex}/hooks.json)
#
# Prints exactly one line, beginning with one of:
#   register-codex-hook: registered           — the file was created or appended to
#   register-codex-hook: already-registered   — no write; the hook was already named
#   register-codex-hook: refused-unparseable  — no write; the file is not a JSON object
#   register-codex-hook: refused-no-codex-home — no write; the parent directory is absent
#
# Exit: 0 on registered or already-registered, 2 on usage, 3 on an unparseable
#       file, 4 when there is no Codex home to register into.

TARGET="${1:-${CODEX_HOME:-$HOME/.codex}/hooks.json}"
if [[ -z "$TARGET" ]]; then
    echo "Usage: register-codex-hook.sh [path-to-hooks.json]" >&2
    exit 2
fi

TARGET_DIR="$(dirname "$TARGET")"
if [[ ! -d "$TARGET_DIR" ]]; then
    echo "register-codex-hook: refused-no-codex-home — $TARGET_DIR does not exist, so Codex is not installed for this user (set CODEX_HOME if it lives elsewhere). Nothing written."
    exit 4
fi

# The hook command and its metadata are the delivery contract. `timeout` is in
# SECONDS in Codex; 30 is generous for a strip-and-copy of one file and well
# under Codex's 600s default. `statusMessage` is what the TUI shows while the
# hook runs, so it names the thing being delivered rather than the script.
HOOK_COMMAND="bash -c 'h=\"\$(git rev-parse --show-toplevel 2>/dev/null)/.claude/hooks/fleet-memory.sh\"; [ -f \"\$h\" ] && exec bash \"\$h\"; exit 0'"
HOOK_COMMAND="${CODEX_HOOK_COMMAND:-$HOOK_COMMAND}"
HOOK_MATCHER="${CODEX_HOOK_MATCHER:-startup|resume}"
HOOK_TIMEOUT="${CODEX_HOOK_TIMEOUT:-30}"
HOOK_STATUS="${CODEX_HOOK_STATUS:-fleet-guidance}"
HOOK_NEEDLE="${CODEX_HOOK_NEEDLE:-fleet-memory.sh}"

result=$(python3 -c '
import copy, json, os, sys

target   = sys.argv[1]
command  = sys.argv[2]
matcher  = sys.argv[3]
timeout  = int(sys.argv[4])
status   = sys.argv[5]
needle   = sys.argv[6]

group = {
    "matcher": matcher,
    "hooks": [{
        "type": "command",
        "command": command,
        "timeout": timeout,
        "statusMessage": status,
    }],
}

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

# Idempotence: anything that already names the hook in a SessionStart command
# is left completely alone — including a hand-written entry whose quoting,
# timeout or matcher differs from ours. Re-registering over the top would give
# the operator a SECOND definition to review and trust in the /hooks browser,
# and two entries that both run the same delivery.
hooks = doc.get("hooks")
existing = hooks.get("SessionStart", []) if isinstance(hooks, dict) else []
if isinstance(existing, list):
    for g in existing:
        if not isinstance(g, dict):
            continue
        entries = g.get("hooks", [])
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and needle in str(e.get("command", "")):
                print("already-registered")
                sys.exit(0)

# A "hooks" or "SessionStart" of the wrong TYPE is not something to coerce —
# overwriting it would destroy configuration we do not understand.
if hooks is not None and not isinstance(hooks, dict):
    print("refused-unparseable")
    sys.exit(3)
if isinstance(hooks, dict) and "SessionStart" in hooks \
        and not isinstance(hooks["SessionStart"], list):
    print("refused-unparseable")
    sys.exit(3)

want = copy.deepcopy(doc)
want.setdefault("hooks", {}).setdefault("SessionStart", []).append(group)

candidate = json.dumps(want, indent=2) + "\n"

# The guard. Re-parsing the bytes we are about to write must reproduce exactly
# "the original document plus our group" — nothing dropped, nothing coerced.
# If it does not, write nothing.
if json.loads(candidate) != want:
    print("refused-unparseable")
    sys.exit(3)

with open(target, "w", encoding="utf-8") as fh:
    fh.write(candidate)
print("registered")
' "$TARGET" "$HOOK_COMMAND" "$HOOK_MATCHER" "$HOOK_TIMEOUT" "$HOOK_STATUS" "$HOOK_NEEDLE") || {
    status=$?
    if [[ "$result" == "refused-unparseable" ]]; then
        echo "register-codex-hook: refused-unparseable — $TARGET is not a JSON object. Nothing written; fix or move that file and re-run."
    elif [[ -n "$result" ]]; then
        echo "register-codex-hook: $result"
    fi
    exit "$status"
}

case "$result" in
    already-registered)
        echo "register-codex-hook: already-registered — $TARGET already runs fleet-memory.sh on SessionStart. Nothing written."
        ;;
    registered)
        echo "register-codex-hook: registered — added a SessionStart hook to $TARGET. Trust it once in the Codex TUI with \`/hooks\` before it will run (or pass --dangerously-bypass-hook-trust for a one-off \`codex exec\`)."
        ;;
    *)
        echo "register-codex-hook: $result"
        ;;
esac
