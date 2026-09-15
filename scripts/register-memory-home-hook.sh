#!/usr/bin/env bash
set -euo pipefail
#
# register-memory-home-hook.sh — Idempotently register the memory-home hook in
# Claude Code's USER-level settings.json, on two events.
#
# WHY USER LEVEL. The thing being gated is not in any repo. Auto-memory notes
# live under `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<encoded-cwd>/memory/`,
# one directory per cwd a session has ever run in — 22 on this machine — and a
# session running in an unrelated project writes them just the same. A
# per-repo `.claude/settings.json` would therefore gate only the repos that
# happened to adopt it, and leave the notes written everywhere else
# unchecked, which is most of them. One entry in `~/.claude/settings.json` is
# registered once per MACHINE and covers every session on it, which is the same
# scope as the data it is about.
#
# TWO SEPARATE GROUPS, one per event, never one group listing both:
#
#   hooks.SessionStart  matcher "startup|resume" — the nudge. Silent when every
#                       note has a home, so it costs nothing to leave on.
#   hooks.Stop          no matcher — the gate. Stop matchers do not filter by
#                       anything this hook cares about, and an absent matcher
#                       is the documented "match all".
#
# THE COMMAND names an ABSOLUTE path and checks it at run time:
#
#   bash -c 'h=<ABS>; if [ -f "$h" ]; then exec bash "$h"; else echo "memory-home: DEGRADED — no hook at $h …"; fi'
#
# The obvious alternative — `[ -f "$h" ] && exec bash "$h"` — fails SILENTLY
# when the checkout has moved or been deleted, and a gate that is off without
# saying so is the exact failure this hook exists to prevent in the first
# place. So a missing script prints one DEGRADED line at every SessionStart
# until someone re-registers it. The hook lives in a checkout rather than in
# `~/.claude/hooks/` on purpose: it is versioned with this repo, so `git pull`
# updates it, and `--hook` exists so a run from a worktree can wire the MAIN
# checkout's copy instead of a path that disappears with the worktree.
#
# APPEND, NEVER OVERWRITE — the posture of register-bootstrap-hook.sh and
# register-codex-hook.sh, for the same reason. `~/.claude/settings.json` is the
# operator's own machine-wide config; this adds its two groups and leaves every
# existing group's matcher, timeout, command and ORDER exactly as it found
# them. Adding our command inside someone else's group would silently inherit
# their matcher and timeout.
#
# Refuses rather than guesses. If the file is present but not parseable as a
# JSON object, this script writes NOTHING and exits 3.
#
# The safety proof is a semantic guard, not a promise. After building the new
# text we re-parse it and require it to equal, exactly, the ORIGINAL parsed
# document with our groups appended. If that comparison fails the file is left
# untouched. So a successful write provably means "the old settings plus our
# elements" — no key dropped, no value coerced, no group reordered.
#
# Formatting is NOT preserved: the file is re-serialized with 2-space indent.
# Only a file this script actually modifies is reformatted; a machine that is
# already registered is never rewritten at all.
#
# NEVER CREATES THE CONFIG DIRECTORY. A missing `~/.claude` means Claude Code
# has never run for this user, and conjuring one so a hook can be registered
# into it writes config for a tool that is not set up. Missing parent is a
# refusal (exit 4) naming what to do, not a `mkdir -p`. The settings FILE
# itself is created when absent — that is the ordinary first-hook case.
#
# `.claude/settings.local.json` is never read or written — it is a developer's
# personal, gitignored file.
#
# Usage: register-memory-home-hook.sh [--hook <abs path to memory-home.sh>] [settings.json]
#        (defaults: this checkout's .claude/hooks/memory-home.sh,
#                   ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json)
#
# Prints exactly one line, beginning with one of:
#   register-memory-home-hook: registered            — the file was created or appended to
#   register-memory-home-hook: already-registered    — no write; the hook was already named
#   register-memory-home-hook: refused-unparseable   — no write; not a JSON object
#   register-memory-home-hook: refused-no-config-dir — no write; the parent directory is absent
#
# Exit: 0 on registered or already-registered, 2 on usage, 3 on an unparseable
#       file, 4 when there is no Claude config directory to register into.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOOK_PATH=""
TARGET=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --hook)
            HOOK_PATH="${2:-}"
            if [[ -z "$HOOK_PATH" ]]; then
                echo "Usage: register-memory-home-hook.sh [--hook <path>] [settings.json]" >&2
                exit 2
            fi
            shift 2
            ;;
        --hook=*) HOOK_PATH="${1#--hook=}"; shift ;;
        -h|--help)
            echo "Usage: register-memory-home-hook.sh [--hook <path>] [settings.json]"
            exit 0
            ;;
        -*)
            echo "Usage: register-memory-home-hook.sh [--hook <path>] [settings.json]" >&2
            exit 2
            ;;
        *)
            if [[ -n "$TARGET" ]]; then
                echo "Usage: register-memory-home-hook.sh [--hook <path>] [settings.json]" >&2
                exit 2
            fi
            TARGET="$1"
            shift
            ;;
    esac
done

HOOK_PATH="${HOOK_PATH:-$REPO_ROOT/.claude/hooks/memory-home.sh}"
TARGET="${TARGET:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json}"

TARGET_DIR="$(dirname "$TARGET")"
if [[ ! -d "$TARGET_DIR" ]]; then
    echo "register-memory-home-hook: refused-no-config-dir — $TARGET_DIR does not exist, so Claude Code is not set up for this user (set CLAUDE_CONFIG_DIR if it lives elsewhere). Nothing written."
    exit 4
fi

# `timeout` is in SECONDS in Claude Code settings. 15 is generous for one
# python3 process over ~30 small files and well under the 600s command-hook
# default — a gate the operator waits on at every session start is a gate they
# will turn off.
HOOK_COMMAND="bash -c 'h=$HOOK_PATH; if [ -f \"\$h\" ]; then exec bash \"\$h\"; else echo \"memory-home: DEGRADED — no hook at \$h (pull _agent-guidance, or re-run scripts/register-memory-home-hook.sh)\"; fi'"
HOOK_MATCHER="${MEMORY_HOME_HOOK_MATCHER:-startup|resume}"
HOOK_TIMEOUT="${MEMORY_HOME_HOOK_TIMEOUT:-15}"
HOOK_NEEDLE="${MEMORY_HOME_HOOK_NEEDLE:-memory-home.sh}"

result=$(python3 -c '
import copy, json, os, sys

target   = sys.argv[1]
command  = sys.argv[2]
matcher  = sys.argv[3]
timeout  = int(sys.argv[4])
needle   = sys.argv[5]

handler = {"type": "command", "command": command, "timeout": timeout}
# SessionStart filters on how the session started; Stop has nothing to filter
# on, and an omitted matcher is the documented "match all".
wanted = {
    "SessionStart": {"matcher": matcher, "hooks": [dict(handler)]},
    "Stop": {"hooks": [dict(handler)]},
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

hooks = doc.get("hooks")
# A "hooks" or per-event value of the wrong TYPE is not something to coerce —
# overwriting it would destroy configuration we do not understand.
if hooks is not None and not isinstance(hooks, dict):
    print("refused-unparseable")
    sys.exit(3)
for event in wanted:
    if isinstance(hooks, dict) and event in hooks \
            and not isinstance(hooks[event], list):
        print("refused-unparseable")
        sys.exit(3)


def already(event):
    """Anything that already names the hook on this event is left alone —
    including a hand-written entry whose quoting or timeout differs from ours.
    Re-registering over the top would give the operator two definitions that
    both run the same scan."""
    groups = hooks.get(event, []) if isinstance(hooks, dict) else []
    if not isinstance(groups, list):
        return False
    for g in groups:
        if not isinstance(g, dict):
            continue
        entries = g.get("hooks", [])
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict) and needle in str(e.get("command", "")):
                return True
    return False


# Per EVENT, not all-or-nothing: a settings.json carrying only one half (an
# interrupted run, a hand edit) gets the missing half rather than a report that
# it is already registered.
missing = [event for event in ("SessionStart", "Stop") if not already(event)]
if not missing:
    print("already-registered")
    sys.exit(0)

want = copy.deepcopy(doc)
for event in missing:
    want.setdefault("hooks", {}).setdefault(event, []).append(wanted[event])

candidate = json.dumps(want, indent=2) + "\n"

# The guard. Re-parsing the bytes we are about to write must reproduce exactly
# "the original document plus our groups" — nothing dropped, nothing coerced.
# If it does not, write nothing.
if json.loads(candidate) != want:
    print("refused-unparseable")
    sys.exit(3)

with open(target, "w", encoding="utf-8") as fh:
    fh.write(candidate)
print("registered " + ",".join(missing))
' "$TARGET" "$HOOK_COMMAND" "$HOOK_MATCHER" "$HOOK_TIMEOUT" "$HOOK_NEEDLE") || {
    status=$?
    if [[ "$result" == "refused-unparseable" ]]; then
        echo "register-memory-home-hook: refused-unparseable — $TARGET is not a JSON object. Nothing written; fix or move that file and re-run."
    elif [[ -n "$result" ]]; then
        echo "register-memory-home-hook: $result"
    fi
    exit "$status"
}

case "$result" in
    already-registered)
        echo "register-memory-home-hook: already-registered — $TARGET already runs memory-home.sh on SessionStart and Stop. Nothing written."
        ;;
    registered*)
        echo "register-memory-home-hook: registered — added ${result#registered } to $TARGET, wired to $HOOK_PATH. It takes effect in the NEXT session, and the Stop gate only scopes to sessions that have a SessionStart marker."
        ;;
    *)
        echo "register-memory-home-hook: $result"
        ;;
esac
