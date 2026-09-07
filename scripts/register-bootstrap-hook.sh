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
# The EVENT is a seam, not a constant. BOOTSTRAP_HOOK_EVENT (default
# `SessionStart`) says which `hooks.<event>` array to append to, so the
# InstructionsLoaded hook the fleet also delivers gets this same
# append-never-overwrite proof rather than a second registrar written beside
# it — a second one would be a second place for that proof to rot.
#
# Usage: register-bootstrap-hook.sh <path-to-settings.json>
#
# Prints exactly one of:
#   already-registered  — no write; the hook was already named
#   registered          — the file was created or appended to
#   refused-unparseable — no write; the existing file is a symlink, or is not
#                         a JSON object, or has a `hooks` / `hooks.<event>` of
#                         a type we cannot append to
#   refused-bad-env     — no write; a BOOTSTRAP_HOOK_* value is unusable
#
# Exit: 0 on either written or already-registered, 2 on usage (including a bad
# environment value), 3 on refusal.

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
    echo "Usage: register-bootstrap-hook.sh <path-to-settings.json>" >&2
    exit 2
fi

# A SYMLINK IS A CONFIG WE DO NOT UNDERSTAND, which is this file's whole
# posture. `open(target, "w")` follows one, so the link was preserved and its
# TARGET rewritten -- in a consumer repo `git add .claude/settings.json` would
# then stage an unchanged symlink while the real edit landed outside the tree,
# and the sync would report a registration that the repo does not carry.
# bootstrap-status.sh classifies the same shape `unparseable`, so the sync
# withholds before it ever gets here rather than delivering a hook this would
# then refuse to register.
if [[ -L "$TARGET" ]]; then
    echo "refused-unparseable"
    exit 3
fi

# The command string and timeout are the delivery contract; keep them in step
# with bootstrap-status.sh's basename key and with the live consumer shape.
#
# `${VAR-default}`, NOT `${VAR:-default}`. With the colon an EXPLICITLY EMPTY
# value silently becomes the default, so `BOOTSTRAP_HOOK_EVENT=` registered
# under SessionStart and `BOOTSTRAP_HOOK_TIMEOUT=` wrote 90 — a caller that
# passed an empty variable by accident got a working registration in the wrong
# place rather than an error. Unset still means "use the default"; set-but-
# empty is now a refusal.
HOOK_COMMAND="${BOOTSTRAP_HOOK_COMMAND-bash \"\$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\"}"
HOOK_MATCHER="${BOOTSTRAP_HOOK_MATCHER-startup|resume}"
HOOK_TIMEOUT="${BOOTSTRAP_HOOK_TIMEOUT-90}"
HOOK_BASENAME="${BOOTSTRAP_HOOK_BASENAME-skills-bootstrap.sh}"
HOOK_EVENT="${BOOTSTRAP_HOOK_EVENT-SessionStart}"

# Validated HERE rather than in the python program, so a bad value is a named
# one-line refusal instead of an interpreter traceback: `int(sys.argv[4])` on
# `BOOTSTRAP_HOOK_TIMEOUT=abc` produced a raw ValueError and exit 1, which
# sync.sh logs as `WARN: could not register ... ()` with an empty reason.
#
# An empty BASENAME is the one with teeth: `"" in str(command)` is TRUE for
# every string, so the idempotence test would read "already-registered" for
# any file at all and the hook would never be registered anywhere.
bad_env() {
    echo "refused-bad-env"
    echo "register-bootstrap-hook.sh: $1" >&2
    exit 2
}
[[ -n "$HOOK_EVENT" ]] || bad_env "BOOTSTRAP_HOOK_EVENT is set but empty; unset it to mean SessionStart"
[[ -n "$HOOK_BASENAME" ]] || bad_env "BOOTSTRAP_HOOK_BASENAME is set but empty; an empty needle matches every command"
[[ -n "$HOOK_COMMAND" ]] || bad_env "BOOTSTRAP_HOOK_COMMAND is set but empty; there would be nothing to run"
# The last of the four empties, and the one that used to be accepted in
# silence: an empty matcher registered `"matcher": ""`, which is neither of
# the two things a caller could have meant (every event, or a named one).
[[ -n "$HOOK_MATCHER" ]] || bad_env "BOOTSTRAP_HOOK_MATCHER is set but empty; pass '*' to match every event"
# A whole number of seconds, 1 to 3600. The bounds are stated rather than
# left to the CLI: 0 registers a hook that can never finish, and a value with
# more digits than an hour has seconds is a typo, not a timeout -- both were
# accepted unvalidated. The digit-count bound comes FIRST so the comparison
# below is never handed a number bash cannot represent.
[[ "$HOOK_TIMEOUT" =~ ^[0-9]{1,4}$ ]] || bad_env "BOOTSTRAP_HOOK_TIMEOUT must be a whole number of seconds between 1 and 3600, got '$HOOK_TIMEOUT'"
[[ "$HOOK_TIMEOUT" -ge 1 && "$HOOK_TIMEOUT" -le 3600 ]] || bad_env "BOOTSTRAP_HOOK_TIMEOUT must be between 1 and 3600 seconds, got '$HOOK_TIMEOUT'"

result=$(python3 -c '
import copy, json, os, sys

target   = sys.argv[1]
command  = sys.argv[2]
matcher  = sys.argv[3]
timeout  = int(sys.argv[4])
needle   = sys.argv[5]
event    = sys.argv[6]

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
existing = hooks.get(event, []) if isinstance(hooks, dict) else []
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
#
# `"hooks" in doc`, not `hooks is not None`: a literal `{"hooks": null}` has
# the key with a None value, so the older test let it through to
# setdefault("hooks", {}), which RETURNS the existing None and then raises
# AttributeError -- a raw interpreter traceback on stderr and exit 1, which
# sync.sh logged as `WARN: could not register ... ()` with an empty reason.
if "hooks" in doc and not isinstance(hooks, dict):
    print("refused-unparseable")
    sys.exit(3)
if isinstance(hooks, dict) and event in hooks \
        and not isinstance(hooks[event], list):
    print("refused-unparseable")
    sys.exit(3)

want = copy.deepcopy(doc)
want.setdefault("hooks", {}).setdefault(event, []).append(group)

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
' "$TARGET" "$HOOK_COMMAND" "$HOOK_MATCHER" "$HOOK_TIMEOUT" "$HOOK_BASENAME" "$HOOK_EVENT") || {
    status=$?
    [[ -n "$result" ]] && echo "$result"
    exit "$status"
}

echo "$result"
