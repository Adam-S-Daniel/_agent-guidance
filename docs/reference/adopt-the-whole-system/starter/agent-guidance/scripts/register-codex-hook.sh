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
#   bash -c 'r="$(git rev-parse --show-toplevel 2>/dev/null)"; h="$r/.claude/hooks/fleet-memory.sh"; [ -n "$r" ] && [ -f "$h" ] && exec bash "$h"; for h in ./*/.claude/hooks/fleet-memory.sh; do grep -q -e --workspace "$h" 2>/dev/null && exec bash "$h" --workspace "$PWD"; done; exit 0'
#
# Codex runs command hooks with the session `cwd` as the working directory and
# may be started from a SUBDIRECTORY, so the docs recommend resolving repo-local
# paths from the git root rather than relatively — that is what the
# `git rev-parse` does. From a multi-repo parent, the first immediate child
# with a workspace-capable hook delivers the freshest child payload. Elsewhere
# without such a child, the hook exits silently. The old default command is
# upgraded in place on re-registration; the changed definition needs one new
# trust action in `/hooks`.
#
# NATIVE WINDOWS CODEX. On Windows, Codex runs a hook's command through
# %COMSPEC% as `cmd.exe /C "<command>"`
# (https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/command_runner.rs,
# `build_command` / `default_shell_program`). That has two consequences:
#
#   1. In cmd.exe, `bash` resolves to C:\Windows\System32\bash.exe — WSL's
#      launcher (measured with `where bash`) — so the plain `bash -c '...'`
#      command would run the hook INSIDE WSL and update WSL's ~/.codex rather
#      than Windows'.
#   2. cmd.exe does not treat single quotes as quoting. The snippet's double
#      quotes toggle cmd's quote state, so its `2>/dev/null` and `&&` land
#      outside cmd's quotes and cmd runs them as its own redirection and
#      command separator (measured through real cmd.exe: "The system cannot
#      find the path specified."). Escaping per quote state is fragile.
#
# A handler may carry a per-handler override, `commandWindows` (alias
# `command_windows`), used instead of `command` when Codex is built for Windows
# (https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/hook_config.rs).
# So when this script runs under Git Bash/MSYS/Cygwin (`uname -s` matching
# MINGW*|MSYS*|CYGWIN*), or with `--windows` (for tests), the handler it writes
# ALSO carries a `commandWindows` that holds NO shell logic at all, only two
# quoted paths — Git Bash and a tracked launcher that holds the snippet:
#
#   "commandWindows": "\"C:\Program Files\Git\bin\bash.exe\" \"D:/repos/_agent-guidance/.claude/hooks/codex-session-start.sh\""
#
# The launcher, .claude/hooks/codex-session-start.sh, is the `command` snippet
# as a file (same git-root lookup, same --workspace loop, same silent exit).
# Its path is this script's OWN checkout, in Windows mixed form (`cygpath -m`
# when available); `--launcher <path>` overrides it, like `--hook` in
# register-memory-home-hook.sh. The Git Bash path defaults to
# C:\Program Files\Git\bin\bash.exe; set CODEX_HOOK_GIT_BASH (a Windows-form
# path) to override it. The script refuses (exit 5) instead of writing a hook
# that cannot run: when `cygpath` is available and Git Bash or the launcher is
# absent, or when either path contains a cmd metacharacter (& | < > ^ %).
# An exact registration that lacks `commandWindows` is upgraded in place on
# Windows (group, position and other fields preserved; the trust hash changes,
# so re-trust it in `/hooks`); one that already carries a `commandWindows`,
# whatever its value, is left alone. Off Windows nothing here changes: no
# `commandWindows` is ever written.
#
# APPEND FOR NEW REGISTRATIONS — the same posture as
# register-bootstrap-hook.sh, for the same reason. A machine's
# ~/.codex/hooks.json may already carry the operator's own hooks; a new
# registration adds a SEPARATE matcher group to `hooks.SessionStart`. The one
# exception is an entry whose command is exactly our old default: upgrade
# that command in place, preserving its group, position and other fields.
# Adding our command inside someone else's group would silently inherit their
# matcher and timeout.
#
# Refuses rather than guesses. If the file is present but not parseable as a
# JSON object, this script writes NOTHING and exits 3.
#
# The safety proof is a semantic guard, not a promise. After building the new
# text we re-parse it and require it to equal, exactly, the ORIGINAL parsed
# document with our one group appended, one exact legacy command changed, or
# one `commandWindows` field added.
# If that comparison fails the file is left untouched. No key is dropped, no
# value coerced, and no group reordered.
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
# Usage: register-codex-hook.sh [--windows] [--launcher <path>] [path-to-hooks.json]
#        (default: ${CODEX_HOME:-$HOME/.codex}/hooks.json)
#
# Prints exactly one line, beginning with one of:
#   register-codex-hook: registered           — the file was created or appended to
#   register-codex-hook: updated              — exact legacy command replaced in place
#   register-codex-hook: updated-windows      — commandWindows added to an exact registration
#   register-codex-hook: already-registered   — no write; the hook was already named
#   register-codex-hook: refused-unparseable  — no write; the file is not a JSON object
#   register-codex-hook: refused-no-codex-home — no write; the parent directory is absent
#   register-codex-hook: refused-no-git-bash  — no write; Windows, but Git Bash or the launcher cannot be used
#
# Exit: 0 on registered, updated, or already-registered; 2 on usage, 3 on an unparseable
#       file, 4 when there is no Codex home to register into, 5 when a Windows
#       registration has no usable Git Bash or launcher to point at.

USAGE="Usage: register-codex-hook.sh [--windows] [--launcher <path>] [path-to-hooks.json]"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER=""
WINDOWS=0
case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;;
esac
TARGET_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --windows) WINDOWS=1 ;;
        --launcher)
            LAUNCHER="${2:-}"
            if [[ -z "$LAUNCHER" ]]; then
                echo "$USAGE" >&2
                exit 2
            fi
            shift
            ;;
        --launcher=*) LAUNCHER="${1#--launcher=}" ;;
        -*)
            echo "$USAGE" >&2
            exit 2
            ;;
        *)
            if [[ -n "$TARGET_ARG" ]]; then
                echo "$USAGE" >&2
                exit 2
            fi
            TARGET_ARG="$1"
            ;;
    esac
    shift
done
TARGET="${TARGET_ARG:-${CODEX_HOME:-$HOME/.codex}/hooks.json}"
if [[ -z "$TARGET" ]]; then
    echo "$USAGE" >&2
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
LEGACY_HOOK_COMMAND="bash -c 'h=\"\$(git rev-parse --show-toplevel 2>/dev/null)/.claude/hooks/fleet-memory.sh\"; [ -f \"\$h\" ] && exec bash \"\$h\"; exit 0'"
HOOK_COMMAND="bash -c 'r=\"\$(git rev-parse --show-toplevel 2>/dev/null)\"; h=\"\$r/.claude/hooks/fleet-memory.sh\"; [ -n \"\$r\" ] && [ -f \"\$h\" ] && exec bash \"\$h\"; for h in ./*/.claude/hooks/fleet-memory.sh; do grep -q -e --workspace \"\$h\" 2>/dev/null && exec bash \"\$h\" --workspace \"\$PWD\"; done; exit 0'"
HOOK_COMMAND="${CODEX_HOOK_COMMAND:-$HOOK_COMMAND}"
HOOK_MATCHER="${CODEX_HOOK_MATCHER:-startup|resume}"
HOOK_TIMEOUT="${CODEX_HOOK_TIMEOUT:-30}"
HOOK_STATUS="${CODEX_HOOK_STATUS:-fleet-guidance}"
HOOK_NEEDLE="${CODEX_HOOK_NEEDLE:-fleet-memory.sh}"

# Windows: two quoted paths and no shell logic, so cmd.exe has no metacharacter
# to misread and cannot resolve `bash` to WSL's launcher. The snippet lives in
# the tracked launcher. Empty off Windows, which writes no field.
HOOK_COMMAND_WINDOWS=""
if [[ "$WINDOWS" -eq 1 ]]; then
    GIT_BASH="${CODEX_HOOK_GIT_BASH:-C:\\Program Files\\Git\\bin\\bash.exe}"
    DEFAULT_LAUNCHER=0
    if [[ -z "$LAUNCHER" ]]; then
        DEFAULT_LAUNCHER=1
        LAUNCHER="$SCRIPT_DIR/../.claude/hooks/codex-session-start.sh"
        LAUNCHER="$(cd "$(dirname "$LAUNCHER")" && pwd)/$(basename "$LAUNCHER")"
        if command -v cygpath >/dev/null 2>&1; then
            LAUNCHER="$(cygpath -m "$LAUNCHER")"
        fi
    fi
    refuse_no_git_bash() {
        echo "register-codex-hook: refused-no-git-bash — $1. Nothing written."
        exit 5
    }
    if command -v cygpath >/dev/null 2>&1; then
        [[ -f "$(cygpath -u "$GIT_BASH")" ]] \
            || refuse_no_git_bash "$GIT_BASH does not exist, so a Windows hook pointing at it could not run (install Git for Windows, or set CODEX_HOOK_GIT_BASH to its bash.exe)"
        [[ -f "$(cygpath -u "$LAUNCHER")" ]] \
            || refuse_no_git_bash "the launcher $LAUNCHER does not exist (pass --launcher)"
    elif [[ "$DEFAULT_LAUNCHER" -eq 1 && ! -f "$LAUNCHER" ]]; then
        refuse_no_git_bash "the launcher $LAUNCHER does not exist (pass --launcher)"
    fi
    HOOK_COMMAND_WINDOWS="\"$GIT_BASH\" \"$LAUNCHER\""
    # cmd.exe reads these outside its quotes' protection in some contexts, so a
    # path carrying one is not safe to hand it.
    if [[ "$HOOK_COMMAND_WINDOWS" == *['&|<>^%']* ]]; then
        refuse_no_git_bash "a Git Bash or launcher path contains a cmd.exe metacharacter (& | < > ^ %), which is not safe in commandWindows"
    fi
fi

result=$(python3 -c '
import copy, json, os, sys

target   = sys.argv[1]
command  = sys.argv[2]
matcher  = sys.argv[3]
timeout  = int(sys.argv[4])
status   = sys.argv[5]
needle   = sys.argv[6]
legacy   = sys.argv[7]
command_windows = sys.argv[8]  # "" off Windows: no field is written

group = {
    "matcher": matcher,
    "hooks": [{
        "type": "command",
        "command": command,
        "timeout": timeout,
        "statusMessage": status,
    }],
}
if command_windows:
    group["hooks"][0]["commandWindows"] = command_windows

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

# An exact current command anywhere means registration is done, even when an
# old default is also present. Otherwise an exact old default is ours to
# upgrade. A hand-written entry is the operators own definition and remains
# byte-identical on re-registration.
hooks = doc.get("hooks")
existing = hooks.get("SessionStart", []) if isinstance(hooks, dict) else []
legacy_location = None
windows_location = None
manual_found = False
if isinstance(existing, list):
    for group_index, g in enumerate(existing):
        if not isinstance(g, dict):
            continue
        entries = g.get("hooks", [])
        if not isinstance(entries, list):
            continue
        for entry_index, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            existing_command = e.get("command", "")
            if existing_command == command:
                # On Windows an exact registration without commandWindows
                # would run in WSL, so it is upgraded; one that already has
                # a commandWindows (ours or the operators) is left alone.
                if command_windows and "commandWindows" not in e \
                        and "command_windows" not in e:
                    if windows_location is None:
                        windows_location = (group_index, entry_index)
                    continue
                print("already-registered")
                sys.exit(0)
            if needle in str(existing_command):
                if existing_command == legacy and legacy_location is None:
                    legacy_location = (group_index, entry_index)
                else:
                    manual_found = True

if windows_location is None and legacy_location is None and manual_found:
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
if windows_location is not None:
    group_index, entry_index = windows_location
    want["hooks"]["SessionStart"][group_index]["hooks"][entry_index]["commandWindows"] = command_windows
    outcome = "updated-windows"
elif legacy_location is None:
    want.setdefault("hooks", {}).setdefault("SessionStart", []).append(group)
    outcome = "registered"
else:
    group_index, entry_index = legacy_location
    legacy_entry = want["hooks"]["SessionStart"][group_index]["hooks"][entry_index]
    legacy_entry["command"] = command
    if command_windows:
        legacy_entry["commandWindows"] = command_windows
    outcome = "updated"

candidate = json.dumps(want, indent=2) + "\n"

# The guard. Re-parsing the bytes we are about to write must reproduce exactly
# "the original document plus our group" or "the original with one exact
# legacy command changed" — nothing dropped, nothing coerced.
# If it does not, write nothing.
if json.loads(candidate) != want:
    print("refused-unparseable")
    sys.exit(3)

with open(target, "w", encoding="utf-8") as fh:
    fh.write(candidate)
print(outcome)
' "$TARGET" "$HOOK_COMMAND" "$HOOK_MATCHER" "$HOOK_TIMEOUT" "$HOOK_STATUS" "$HOOK_NEEDLE" "$LEGACY_HOOK_COMMAND" "$HOOK_COMMAND_WINDOWS") || {
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
    updated)
        echo "register-codex-hook: updated — replaced the legacy SessionStart command in $TARGET. Re-trust the changed definition once in the Codex TUI with \`/hooks\`."
        ;;
    updated-windows)
        echo "register-codex-hook: updated-windows — added commandWindows (Git Bash) to the existing SessionStart hook in $TARGET. Re-trust the changed definition once in the Codex TUI with \`/hooks\`."
        ;;
    *)
        echo "register-codex-hook: $result"
        ;;
esac
