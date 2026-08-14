#!/usr/bin/env bash
set -euo pipefail
#
# register-bootstrap-hook.sh — Idempotently register the skills-bootstrap hook
# in a repo's .claude/settings.json.
#
# APPEND, NEVER OVERWRITE. Both live consumers already run a SessionStart hook
# of their own (`scripts/setup-hooks.sh`), so this script adds a SEPARATE
# group to the `hooks.SessionStart` array and leaves every existing group's
# matcher, timeout, command and ORDER exactly as it found them. Adding our
# command inside someone else's group would silently inherit their `matcher`
# and `timeout` — a 30s timeout around a hook that fetches two git registries
# is a hook that fails — so the separate group is a correctness requirement,
# not a stylistic one. The shape written here mirrors the live reference in
# adamdaniel.ai byte-for-byte.
#
# Refuses rather than guesses. If the file is present but not parseable as a
# JSON object, this script writes NOTHING and exits 3. That mirrors the
# CLAUDE.md bridge's never-rewrite-a-hand-written-file default: a settings.json
# we cannot read is a settings.json we cannot safely edit, and a repo losing
# its harness config is far worse than a repo missing the bootstrap hook.
#
# The safety proof is a semantic guard, not a promise. After building the new
# text we re-parse it and require it to equal, exactly, the ORIGINAL parsed
# document with our one group appended. If that comparison fails the file is
# left untouched. So a successful write provably means "the old settings plus
# our element" — no key dropped, no value coerced, no group reordered.
#
# Formatting is NOT preserved: the file is re-serialized with 2-space indent,
# so an inline array elsewhere in the file (e.g. adamdaniel.ai's
# `"symlinkDirectories": ["vendor", ...]`) is re-emitted one element per line.
# That is a real, visible diff in the one file we edit, and it is the accepted
# cost of using a real JSON parser instead of hand-splicing text. Only files
# this script actually modifies are reformatted; a repo that is already
# registered is never rewritten at all.
#
# `.claude/settings.local.json` is never read or written — it is a developer's
# personal, gitignored file.
#
# Usage: register-bootstrap-hook.sh <path-to-settings.json>
#
# Prints exactly one of:
#   already-registered  — no write; the hook was already named
#   registered          — the file was created or appended to
#   refused-unparseable — no write; the existing file is not a JSON object
#
# Exit: 0 on either written or already-registered, 2 on usage, 3 on refusal.

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
    echo "Usage: register-bootstrap-hook.sh <path-to-settings.json>" >&2
    exit 2
fi

# The command string and timeout are the delivery contract; keep them in step
# with bootstrap-status.sh's basename key and with the live consumer shape.
HOOK_COMMAND="${BOOTSTRAP_HOOK_COMMAND:-bash \"\$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\"}"
HOOK_MATCHER="${BOOTSTRAP_HOOK_MATCHER:-startup|resume}"
HOOK_TIMEOUT="${BOOTSTRAP_HOOK_TIMEOUT:-90}"
HOOK_BASENAME="${BOOTSTRAP_HOOK_BASENAME:-skills-bootstrap.sh}"

result=$(python3 -c '
import copy, json, os, sys

target   = sys.argv[1]
command  = sys.argv[2]
matcher  = sys.argv[3]
timeout  = int(sys.argv[4])
needle   = sys.argv[5]

group = {
    "matcher": matcher,
    "hooks": [{"type": "command", "command": command, "timeout": timeout}],
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

# Idempotence: same semantic test bootstrap-status.sh applies. Anything that
# already names the hook in a SessionStart command is left completely alone —
# including a hand-written entry whose quoting or timeout differs from ours.
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
' "$TARGET" "$HOOK_COMMAND" "$HOOK_MATCHER" "$HOOK_TIMEOUT" "$HOOK_BASENAME") || {
    status=$?
    [[ -n "$result" ]] && echo "$result"
    exit "$status"
}

echo "$result"
