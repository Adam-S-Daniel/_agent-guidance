#!/usr/bin/env bash
# memory-home.sh — every auto-memory note NAMES the committed copy of its fact.
#
# THE CONTRACT
# ------------
# Claude Code's auto-memory writes one markdown file per fact under
# `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<encoded-cwd>/memory/<slug>.md`,
# plus a `MEMORY.md` index. Those directories are OUTSIDE every repo — 22 of
# them on this machine — so a fact recorded there is invisible to every other
# session, every other agent and every other person, and it versions with
# nothing.
#
#   Every memory note whose `metadata.type` is NOT `user` must carry
#   `metadata.home`: `<owner>/<repo>:<path>` (preferred), or
#   `https://github.com/<owner>/<repo>/blob/<ref>/<path>`. That is the
#   COMMITTED file holding the durable copy — an ADR under `docs/decisions/`,
#   a `docs/` page, the repo's `## Repo-specific additions`, or a skill in the
#   registry. With a home, the note is a POINTER; the repo copy is the source
#   of truth. A `type: user` note (who the person is, what they prefer) is
#   EXEMPT: it is about the person, not about the work, and no repo owns it.
#
# A home is DANGLING when a local clone of `<repo>` is found but `<path>` does
# not exist in it. Clone candidates, in order: `$CLAUDE_PROJECT_DIR/<repo>`,
# `$CLAUDE_PROJECT_DIR` itself when its basename is `<repo>`,
# `$(dirname "$CLAUDE_PROJECT_DIR")/<repo>`, `$HOME/repos/<repo>`. When no
# clone is found the home is accepted UNVERIFIED and silently — the alternative
# is nagging about a repo this machine simply does not have checked out.
#
# AUTOMATIC MOVING IS DELIBERATELY NOT DONE. Choosing which repo owns a fact,
# and which file inside it, is judgment: an ADR, a docs page, a repo fact, a
# skill. So this hook NUDGES and GATES, and the agent does the promotion. It
# never modifies or deletes a note. See
# docs/decisions/0013-a-memory-note-outside-a-repo-names-its-home.md.
#
# WHAT THE HOOK REFERENCE SAYS, AND WHAT THIS RELIES ON
# ----------------------------------------------------
# Confirmed against https://code.claude.com/docs/en/hooks (fetched 2026-09-14):
#
# - Common input fields, on stdin as one JSON object: `session_id`,
#   `transcript_path`, `cwd`, `hook_event_name`, and `permission_mode` ("Not
#   all events receive this field"). This script reads `session_id`,
#   `hook_event_name` and — on Stop — `stop_hook_active`.
# - SessionStart matchers are `startup`, `resume`, `clear`, `compact`, `fork`;
#   the registrar wires `startup|resume`. SessionStart is one of the four
#   events where "Claude Code adds plain-text stdout as context that Claude can
#   see and act on", which is why the nudge is a plain line and not JSON.
# - Stop input: "In addition to the common input fields, Stop hooks receive
#   `stop_hook_active` … The `stop_hook_active` field is `true` when Claude
#   Code is already continuing as a result of a stop hook. Check this value or
#   process the transcript to avoid blocking on a condition that will never
#   resolve. Claude Code overrides the hook and ends the turn after 8
#   consecutive blocks."
# - Stop decision control: `decision` — "`"block"` prevents Claude from
#   stopping. Omit to allow Claude to stop"; `reason` — "Required when
#   `decision` is `"block"`. Tells Claude why it should continue". The JSON
#   route is the exit-0 route: "Instead of exiting with code 2 to block, exit 0
#   and print a JSON object to stdout." So a block here is
#   `{"decision":"block","reason":"…"}` on stdout with EXIT 0, and "Your hook's
#   stdout must contain only the JSON object" — which is why a blocking run
#   prints nothing else.
# - `systemMessage` is a universal JSON output field, "Warning message shown to
#   the user": "Every event accepts them, but some events discard them or
#   deliver `systemMessage` somewhere other than the transcript. Each event's
#   section says so." The Stop section says nothing of the kind, so Stop
#   honours it — that is the one-nudge arm below.
#
# ALWAYS EXITS 0, AND DEGRADES RATHER THAN CRASHING — but a DEGRADE is a
# RUN-LEVEL fault only: no python3, no PyYAML, a hook event on stdin that is not
# readable JSON, a marker directory that cannot be written or read. Those print
# one `memory-home: DEGRADED — <reason>` line and gate nothing, because none of
# them is evidence about anybody's notes.
#
# A NOTE THIS SCRIPT CANNOT PARSE IS A FINDING ABOUT THAT NOTE, NOT A VERDICT
# ABOUT THE RUN. The scan keeps going, the note is flagged exactly like a
# homeless one, and the label says what is wrong with it:
#
#   /…/memory/feedback_decap.md (unparseable frontmatter — quote the description or fix the YAML)
#
# That is not a hypothetical. Claude Code writes `description:` values itself,
# and it does not quote them, so an ordinary description containing `: ` —
# "… with delete: true + editorial_workflow" — is frontmatter PyYAML rejects
# outright ("mapping values are not allowed here"). FOUR such notes existed on
# this machine the day this hook was written. An earlier draft treated a parse
# failure as a run-level degrade, and a SessionStart probe against the real
# config directory printed those four filenames and gated nothing: every
# session on the machine would have been DEGRADED forever, and no note would
# ever have been nudged. A gate whose commonest input turns it off is not a
# gate.
#
# On SessionStart a DEGRADED line is context Claude sees. On Stop, exit 0
# with plain-text stdout goes to the debug log only ("For most events, Claude
# Code writes stdout to the debug log and doesn't show it in the transcript")
# and is never a hook error — the right posture for a degrade, which must not
# block and must not look like a fault. A degrade with a standing cause shows
# up at the next SessionStart, where it IS visible.
#
# NOTHING IS EVER WRITTEN INSIDE A NOTE, and the only file this hook creates is
# its own per-session marker under `${CLAUDE_CONFIG_DIR:-~/.claude}/memory-home/`.
set -uo pipefail

# Read stdin ONCE. Everything after this is a single python3 process: the scan
# walks ~30 small files and parses each one's YAML frontmatter, and one
# interpreter start is the difference between a hook that is free and a hook
# that is felt on every SessionStart.
EVENT="$(cat 2>/dev/null)" || EVENT=""

if ! command -v python3 >/dev/null 2>&1; then
    echo "memory-home: DEGRADED — python3 is not on PATH, so memory notes are not being checked for a repo home."
    exit 0
fi

# The event JSON travels in the environment, not on stdin, because stdin is
# carrying the script itself. It is also why nothing below has to survive
# single-quote nesting inside `python3 -c`.
rc=0
out="$(MEMORY_HOME_EVENT="$EVENT" python3 - <<'PY'
import json
import os
import string
import sys
import time

MARKER_MAX_AGE_SECONDS = 14 * 86400
LIST_LIMIT = 5


def emit(line):
    sys.stdout.write(line + "\n")


def degraded(reason):
    emit("memory-home: DEGRADED — " + reason)
    sys.exit(0)


# ── The event ──────────────────────────────────────────────────────────────
raw = os.environ.get("MEMORY_HOME_EVENT", "")
try:
    event = json.loads(raw) if raw.strip() else {}
    if not isinstance(event, dict):
        raise ValueError("hook event is not a JSON object")
except Exception as exc:
    degraded("the hook event on stdin is not readable JSON (%s); nothing gated."
             % exc.__class__.__name__)

hook_event = event.get("hook_event_name")
if hook_event not in ("SessionStart", "Stop"):
    # Registered for two events; anything else is somebody else's wiring.
    sys.exit(0)

# PyYAML parses the frontmatter. A hand-rolled `key: value` scanner would
# mis-read a quoted colon, a folded scalar or a comment — and the whole point
# of this gate is that it reasons about a note's STRUCTURE.
try:
    import yaml
except Exception as exc:
    degraded("PyYAML is not importable (%s), so note frontmatter cannot be "
             "parsed; nothing gated." % exc.__class__.__name__)

HOME = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
MARKER_DIR = os.path.join(CONFIG_DIR, "memory-home")

SAFE_SESSION_CHARS = set(string.ascii_letters + string.digits + "-_")


def marker_name(session_id):
    """A session id from stdin is untrusted input that becomes a PATH here.

    Anything outside [A-Za-z0-9_-] is dropped rather than escaped, so no value
    can walk out of MARKER_DIR whatever the harness hands us. An id that
    sanitizes to nothing gets no marker, which the Stop arm reads as "no
    marker" and declines to gate on.
    """
    if not isinstance(session_id, str):
        return ""
    return "".join(c for c in session_id if c in SAFE_SESSION_CHARS)[:128]


# ── The notes ──────────────────────────────────────────────────────────────
def note_paths():
    import glob as globmod
    pattern = os.path.join(CONFIG_DIR, "projects", "*", "memory", "*.md")
    return sorted(p for p in globmod.glob(pattern)
                  if os.path.basename(p) != "MEMORY.md")


def note_metadata(path):
    """-> (metadata dict, None) | (None, label).

    The label is a SHORT reason, shown in parentheses beside the note's own
    path — it names a fault in that one file, so it never carries the path
    itself and it never ends the run.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        return None, "could not be read (%s)" % exc.__class__.__name__

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, "no YAML frontmatter block — add one naming metadata.home"
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None, "unterminated frontmatter block"
    try:
        front = yaml.safe_load("\n".join(lines[1:end]))
    except Exception:
        # Overwhelmingly this is an unquoted `: ` inside `description:` — see
        # the header. The remedy is named rather than the exception class,
        # because the class is what an agent cannot act on.
        return None, "unparseable frontmatter — quote the description or fix the YAML"

    if front is None:
        front = {}
    if not isinstance(front, dict):
        return None, "frontmatter is not a mapping"
    meta = front.get("metadata")
    # A note with no `metadata:` at all is not malformed — it is simply a note
    # with no type and no home, which is exactly what this gate is for.
    return (meta if isinstance(meta, dict) else {}), None


def split_home(value):
    """-> (repo, path-within-repo) | None when the value is in neither form.

    A home in neither documented form names nothing this can resolve, so it
    counts as no home at all rather than as an unverifiable one.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    prefix = "https://github.com/"
    if v.startswith(prefix):
        parts = [p for p in v[len(prefix):].split("/") if p]
        # <owner>/<repo>/blob/<ref>/<path…>
        if len(parts) >= 5 and parts[2] == "blob":
            return parts[1], "/".join(parts[4:])
        return None
    if ":" in v:
        left, _, right = v.partition(":")
        owner_repo = [p for p in left.split("/") if p]
        right = right.strip().lstrip("/")
        if len(owner_repo) == 2 and right:
            return owner_repo[1], right
    return None


def find_clone(repo):
    """The first candidate that exists, or None — see the header for the order."""
    project = os.environ.get("CLAUDE_PROJECT_DIR") or ""
    candidates = []
    if project:
        project = os.path.normpath(project)
        candidates.append(os.path.join(project, repo))
        if os.path.basename(project) == repo:
            candidates.append(project)
        candidates.append(os.path.join(os.path.dirname(project), repo))
    candidates.append(os.path.join(HOME, "repos", repo))
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def scan():
    """-> [(path, display)] — one entry per note that needs attention.

    `display` is the path, plus a parenthesised label when the note could not
    be parsed. A parse failure never stops the walk and never becomes a
    run-level verdict: an unreadable note is one finding among however many
    the directory holds, and the notes beside it are still judged.
    """
    findings = []
    for path in note_paths():
        meta, label = note_metadata(path)
        if label is not None:
            findings.append((path, "%s (%s)" % (path, label)))
            continue
        note_type = meta.get("type")
        if isinstance(note_type, str) and note_type.strip() == "user":
            continue
        resolved = split_home(meta.get("home"))
        if resolved is None:
            findings.append((path, path))
            continue
        repo, rel = resolved
        clone = find_clone(repo)
        if clone is None:
            continue                      # accepted unverified, silently
        if not os.path.exists(os.path.join(clone, rel)):
            # Dangling: the clone is here, the file it names is not.
            findings.append((path, path))
    return findings


def render(displays):
    shown = ", ".join(displays[:LIST_LIMIT])
    extra = len(displays) - LIST_LIMIT
    if extra > 0:
        shown += ", +%d more" % extra
    return shown


# ── SessionStart: mark the session, then nudge — silently when clean ───────
if hook_event == "SessionStart":
    marker_reasons = []
    name = marker_name(event.get("session_id"))
    if name:
        try:
            os.makedirs(MARKER_DIR, exist_ok=True)
            with open(os.path.join(MARKER_DIR, name), "w", encoding="utf-8") as fh:
                fh.write("")
            # Prune by mtime so a machine that has held sessions for a year does
            # not accumulate a marker per session. 14 days is far longer than
            # any session and short enough that the directory stays readable.
            cutoff = time.time() - MARKER_MAX_AGE_SECONDS
            for entry in os.listdir(MARKER_DIR):
                stale = os.path.join(MARKER_DIR, entry)
                try:
                    if os.path.isfile(stale) and os.path.getmtime(stale) < cutoff:
                        os.remove(stale)
                except Exception:
                    pass              # one unremovable marker is not a failure
        except Exception as exc:
            marker_reasons.append(
                "the session marker under %s could not be written (%s), so the "
                "Stop gate has nothing to scope to" % (MARKER_DIR, exc.__class__.__name__))

    # A marker we could not write is RUN-LEVEL: without it the Stop arm has
    # nothing to scope to. Nothing the scan finds is run-level.
    if marker_reasons:
        degraded("; ".join(marker_reasons))

    findings = scan()
    if not findings:
        sys.exit(0)                   # ANTI-NAG: a clean scan says nothing
    emit("memory-home: %d memory note(s) outside any repo have no repo home — %s. "
         "Promote each into the repo that owns the fact (ADR, docs/, "
         "'## Repo-specific additions', the skills registry) and set "
         "metadata.home; a note about the person (type: user) is exempt."
         % (len(findings), render([d for _, d in findings])))
    sys.exit(0)

# ── Stop: gate on what THIS session wrote ──────────────────────────────────
name = marker_name(event.get("session_id"))
marker = os.path.join(MARKER_DIR, name) if name else ""
if not marker or not os.path.isfile(marker):
    # No marker means this hook was registered mid-session, or the id was
    # unusable: there is no honest way to tell what this session wrote, and a
    # gate that blocks on a guess is worse than no gate.
    sys.exit(0)
try:
    since = os.path.getmtime(marker)
except Exception as exc:
    degraded("the session marker %s could not be read (%s); nothing gated."
             % (marker, exc.__class__.__name__))

written_this_session = []
for path, display in scan():
    try:
        if os.path.getmtime(path) >= since:
            written_this_session.append(display)
    except Exception:
        pass                          # a note that vanished mid-scan is not a block
if not written_this_session:
    sys.exit(0)

listed = render(written_this_session)

# THE LOOP GUARD. `stop_hook_active` is true when Claude is already continuing
# because of a stop hook, so blocking again would be blocking on a condition
# the same turn is already working on. Say it once, then get out of the way.
if event.get("stop_hook_active") is True:
    emit(json.dumps({
        "systemMessage": "memory-home: still %d note(s) without a repo home after "
                         "one nudge — not blocking again: %s"
                         % (len(written_this_session), listed),
    }, ensure_ascii=False))
    sys.exit(0)

emit(json.dumps({
    "decision": "block",
    "reason": "memory-home: this session wrote %d memory note(s) with no repo home: %s. "
              "Before stopping: put the durable content in the repo that owns it (an ADR "
              "under docs/decisions/, a docs/ page, that repo's '## Repo-specific "
              "additions', or the skills registry), commit it on your branch, then set "
              "metadata.home to <owner>/<repo>:<path> in each note — or delete the note, "
              "or mark it type: user if it is about the person. The note is a pointer; "
              "the repo copy is the source of truth." % (len(written_this_session), listed),
}, ensure_ascii=False))
sys.exit(0)
PY
)" || rc=$?

if [ "$rc" -ne 0 ]; then
    # The scan itself fell over. Say so on one line and gate nothing: an
    # interpreter fault is not evidence about anybody's memory notes.
    if [ -n "$out" ]; then
        printf '%s\n' "$out"
    else
        echo "memory-home: DEGRADED — the note scan exited $rc without a verdict; nothing gated."
    fi
    exit 0
fi

[ -n "$out" ] && printf '%s\n' "$out"
exit 0
