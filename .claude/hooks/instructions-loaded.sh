#!/usr/bin/env bash
# instructions-loaded.sh — a LOAD-TIME receipt for the fleet guidance.
#
# WHY THIS EXISTS
# ---------------
# The SessionStart verdict (`fleet-guidance: installed / current / DEGRADED`)
# reports what fleet-memory.sh did TO THE FILE. It cannot report what the
# session LOADED, and the gap between those two has already cost real
# incidents:
#
#   • a CLAUDE_CONFIG_DIR the CLI does not read memory from — the hook writes
#     a perfect block into a file nothing opens, and says `installed`;
#   • a block that was current at session start and truncated later. On
#     2026-09-05 a test mutation in a review container cut
#     ~/.claude/CLAUDE.md from 56,099 bytes to 154 WHILE a session ran, and
#     nothing said so until the next SessionStart;
#   • a repo AGENTS.md whose managed half is behind the guidance actually in
#     context, or edited above the "## Repo-specific additions" marker — which
#     the per-session guidance has no way to notice;
#   • the per-session context cost, quoted by hand (19 copies = 332.3k tokens,
#     measured 2026-08-29) with nothing measuring it per session.
#
# The CLI emits an `InstructionsLoaded` event for every CLAUDE.md and
# .claude/rules/*.md it loads. That event is the receipt the verdict lacks.
#
# THE TWO MEASUREMENTS THIS DESIGN RESTS ON
# -----------------------------------------
# Both taken on the CLI in the container that wrote this file (2.1.261),
# against a LOCAL STUB API endpoint (ANTHROPIC_BASE_URL -> 127.0.0.1 serving
# one canned SSE message) and never a real credential, with CLAUDE_CONFIG_DIR
# and HOME both pointed at throwaway directories.
#
#   1. THE EVENT. `claude -p` (exit 0, the stub's reply returned) fired
#      exactly two events, one per memory file:
#
#        {"cwd":"…/proj","file_path":"…/cfg/CLAUDE.md",
#         "hook_event_name":"InstructionsLoaded","load_reason":"session_start",
#         "memory_type":"User","session_id":"b469c4a3-…",
#         "transcript_path":"…/cfg/projects/…/b469c4a3-….jsonl"}
#
#        {"cwd":"…/proj","file_path":"…/proj/CLAUDE.md",
#         "hook_event_name":"InstructionsLoaded","load_reason":"session_start",
#         "memory_type":"Project","session_id":"b469c4a3-…",
#         "transcript_path":"…/cfg/projects/…/b469c4a3-….jsonl"}
#
#      `globs`, `trigger_file_path` and `parent_file_path` are absent on a
#      session_start load — the CLI's own schema marks all three optional, and
#      they carry only for the glob-match, include and nested-traversal
#      reasons. A `compact` reload could NOT be reproduced here: the CLI sets
#      that reason during post-compaction cleanup and consumes it on the NEXT
#      eager memory load, which a `claude -p` process never reaches (measured:
#      `/compact` in print mode fired no event beyond the two above), and an
#      interactive session in this container cannot start without an OAuth
#      login. This hook does not depend on it: `load_reason` is recorded
#      verbatim and never branched on.
#
#   2. WHERE STDOUT LANDS: NOWHERE. The canary this hook printed appeared in
#      neither the CLI's stdout, its stderr, the transcript, nor any file
#      under the config dir or HOME — on a failed run and on a successful one
#      alike. The CLI's own hook description agrees: "Exit code 0 — command
#      completes successfully; other exit codes — show stderr to user only.
#      This hook is observability-only." Since this hook must always exit 0,
#      it has no channel to the session at all.
#
#      That is why the verdict is written to a RECEIPT FILE and printed by the
#      NEXT session's SessionStart hook, so a mismatch is never silent for
#      more than one session. The stdout lines below are kept anyway: they
#      cost nothing, they are what the tests read, and a future CLI that
#      surfaces them turns them on with no change here.
#
# OBSERVE-ONLY, ALWAYS. No decision field, no blocking, exit 0 on every event
# — well-formed, malformed or hostile. This runs on EVERY memory load in every
# session; its failure mode has to be "nothing happened", never "the session
# broke". Nothing is ever written outside $CLAUDE_CONFIG_DIR.
set -uo pipefail

# `${HOME:-}` and not `$HOME`: under `set -u` an unset HOME is a non-zero exit
# from a hook whose whole contract is that it never has one. With neither set
# this resolves to "/.claude", which is not a directory, and the guard below
# turns that into a silent exit 0.
STATE_DIR="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
LOG="$STATE_DIR/instructions-log.jsonl"
RECEIPT="$STATE_DIR/instructions-receipt.state"
STATE="$STATE_DIR/fleet-guidance.state"

# The same opt-out fleet-memory.sh honours, with the same spellings meaning
# OFF — a flag whose disabled spelling enables it is a trap worth two lines to
# avoid, and two hooks disagreeing about it would be worse. A skip REMOVES
# what earlier sessions wrote rather than merely declining to write more:
# leaving a log behind is an opt-out that did not opt you out.
case "${FLEET_GUIDANCE_SKIP:-}" in
    ""|0|false|FALSE|no|NO|off|OFF) ;;
    *) rm -f "$LOG" "$LOG.1" "$RECEIPT" 2>/dev/null; exit 0 ;;
esac

# A config dir that does not exist is not ours to create. Creating one here
# would be this hook writing into a path no other part of the fleet uses.
[ -d "$STATE_DIR" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# The event is JSON, and a `file_path` may legally carry a quote, a backslash
# or a newline. It is therefore parsed with a real JSON parser and re-emitted
# with one — never scanned with a regex, never interpolated into a shell word.
# Everything the hook does with the path happens on the python side for that
# reason; bash never sees it.
#
# `read -d ''` exits non-zero at EOF, which is why this file runs under
# `set -uo pipefail` and not `set -e`: an always-exit-0 hook must not inherit
# a shell that aborts on the ordinary.
read -r -d '' INSTRUCTIONS_RECEIPT_PROGRAM <<'PY'
import hashlib
import json
import os
import re
import sys
import time

STATE_DIR, LOG, RECEIPT, STATE = sys.argv[1:5]

# Everything ASCII on purpose. These literals are compared against FILE BYTES,
# never against decoded text, so the byte arithmetic below ("is this block
# 154 bytes where 56,099 were installed?") is exact rather than a character
# count that a multi-byte dash would quietly shift. The real BEGIN marker ends
# with an em dash and "DO NOT EDIT"; matching its ASCII prefix is enough to
# identify it and keeps this file free of any encoding question.
BEGIN_FLEET = b"<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance)"
END_FLEET = b"<!-- END FLEET GUIDANCE -->"
VERSION_PREFIX = b"<!-- fleet-guidance-version:"
BEGIN_MANAGED = b"BEGIN MANAGED SECTION"
END_MANAGED = b"END MANAGED SECTION"
MARKER = b"## Repo-specific additions"

FILE_CAP = 4 << 20      # a memory file larger than this is not one of ours
STDIN_CAP = 1 << 20     # an InstructionsLoaded event is a few hundred bytes
LOG_CAP = 1 << 20       # rotate the log here, bounded at two files total
STATE_CAP = 64 << 10    # a state or receipt file larger than this is not ours
VALUE_CAP = 200         # a receipt value; the longest verdict is ~65 chars

# The version token is DATA READ OUT OF A FILE that ends up echoed verbatim
# into a terminal by the next session's SessionStart hook. fleet-memory.sh
# writes eight hex characters; nothing else needs to survive.
VERSION_CHARS = re.compile(r"[^0-9a-zA-Z._-]")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Read stdin to EOF BEFORE deciding whether to parse it. Exiting early on an
# oversized event would leave the writer holding a closed pipe: it takes
# SIGPIPE, exits 141, and a caller running under `pipefail` reads that as the
# hook failing. Draining first costs one buffer and removes the whole class.
raw = sys.stdin.buffer.read()
if len(raw) > STDIN_CAP:
    sys.exit(0)
try:
    event = json.loads(raw.decode("utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(event, dict):
    sys.exit(0)

file_path = event.get("file_path")
if not isinstance(file_path, str) or not file_path:
    sys.exit(0)
memory_type = event.get("memory_type") if isinstance(event.get("memory_type"), str) else ""
load_reason = event.get("load_reason") if isinstance(event.get("load_reason"), str) else ""
cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
session_id = event.get("session_id") if isinstance(event.get("session_id"), str) else ""


def read_bytes(path, cap=FILE_CAP):
    """The file's bytes, or None for anything that is not a readable file."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read(cap)
    except Exception:
        return None


def open_owned(path, flags):
    """Open a path this hook owns, refusing to follow a symlink out of the dir.

    `open(path, "a")` FOLLOWS a symlink. A link planted at the log -- or at the
    receipt's tmp file -- would therefore make this hook append outside
    $CLAUDE_CONFIG_DIR, and the header above states in absolute terms that it
    never does. O_NOFOLLOW turns that into an OSError, which every caller here
    already treats as "no receipt this time"; 0600 keeps a file we create
    private rather than inheriting the umask.

    Planting the link needs write access to ~/.claude, i.e. to the guidance and
    to settings.json's hook commands, so this buys an attacker strictly less
    than they already hold. It is fixed because the invariant is absolute, not
    because the escape is a privilege.
    """
    return os.open(path, flags | os.O_NOFOLLOW, 0o600)


def relativize(path):
    """A path that is never absolute.

    This log feeds a report that gets pasted into pull requests, and an
    absolute path carries the account's home directory -- and often its
    username -- into a public log. Relative to $HOME, else to the event's cwd,
    else the last two components with an explicit ellipsis so nobody mistakes
    a shortened path for a whole one.
    """
    try:
        target = os.path.abspath(path)
    except Exception:
        target = path
    for base, prefix in ((os.path.expanduser("~"), "~/"), (cwd, "")):
        if not base or not base.startswith("/"):
            continue
        base = base.rstrip("/")
        if target.startswith(base + "/"):
            return prefix + target[len(base) + 1:]
    parts = [p for p in target.split("/") if p]
    return ".../" + "/".join(parts[-2:]) if parts else "..."


def read_kv(path):
    """`key=value` lines from a small file this hook or its sibling wrote.

    SIZE-CHECKED BEFORE IT IS READ. fleet-memory.sh writes five short lines
    into fleet-guidance.state and this hook writes four into the receipt;
    anything above STATE_CAP was written by neither, and reading it is the one
    place a hook that runs on EVERY memory load can be made to spend real time
    and real memory. Measured on the uncapped version: a 1 GB state file took
    6-17 s and ~2 GB RSS against the 10-second timeout the sync registers, so
    one corrupt file in the config dir killed the receipt on every load.
    Refusing to read it degrades to silence, which is this hook's designed
    failure mode; spending the whole timeout budget is not.
    """
    out = {}
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > STATE_CAP:
            return {}
        with open(path, encoding="utf-8") as fh:
            for line in fh.read(STATE_CAP).split("\n"):
                if "=" in line:
                    key, _, value = line.partition("=")
                    out[key.strip()] = value.strip()
    except Exception:
        return {}
    return out


def read_state():
    """fleet-memory.sh's record of what it installed: version, bytes, sha256."""
    return read_kv(STATE)


# Every human-facing string in this program is built through here or written
# with a \u escape, so the SOURCE stays pure ASCII while the OUTPUT carries
# the fleet's em dash. The program reaches python through `python3 -c`, i.e.
# through argv, and argv decoding follows the locale; keeping the source
# ASCII means a C-locale runner cannot mangle a verdict line. Output is
# written as explicit UTF-8 bytes for the same reason.
def mismatch(text):
    return "LOAD MISMATCH \u2014 " + text


def version_token(raw):
    """A version id, or "unknown". Never anything else.

    Measured on the unsanitised version: a 100,000-character token in
    ~/.claude/CLAUDE.md produced a 100 kB receipt and a 100 kB line at the NEXT
    session start, and a token carrying `\x1b[2J\x1b[1;31mSYSTEM: ...` reached
    that line with its escapes intact -- the line the shipped stub tells every
    agent on ~20 repos to read. Not a privilege (whoever writes that file owns
    the guidance already), but a line that can be made to say anything, at any
    length, is not a verdict.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "replace")
    return VERSION_CHARS.sub("", raw)[:16] or "unknown"


def clean(value):
    """A receipt value that cannot carry a control sequence or a screenful.

    The receipt is a `key=value` file whose values are echoed into a terminal,
    so a newline in one would forge a second key and a control sequence would
    reach the session start intact. Applied to every value stored, not only to
    the ones built from a version token, so a future verdict string inherits
    the guarantee instead of re-earning it.
    """
    return CONTROL_CHARS.sub("", str(value))[:VALUE_CAP]


def fleet_verdict(data, state):
    """What the session actually loaded, against what was installed.

    Returns the verdict SUFFIX -- `loaded (...)` or `LOAD MISMATCH -- ...` -- or
    None when there is nothing to compare against. No state file means
    fleet-memory.sh never installed anything here (a machine that opted out,
    or one that has never run it), and a hook that invented a verdict for that
    would be worse than a silent one.
    """
    want_version = state.get("version", "")
    if not want_version:
        return None
    # After the emptiness guard, not before it: an empty version means "no
    # state to compare against", which version_token would turn into the
    # string "unknown" and a verdict this hook has no business making.
    want_version = version_token(want_version)

    lines = data.split(b"\n")
    # COUNTED, not "the first one". Taking the first BEGIN and the first END
    # after it parses a DOUBLED file -- a regeneration that prepended a fresh
    # block on top of the old one, the c86465f shape the AGENTS.md half of
    # this hook is named after -- as one perfect block: measured on the
    # earlier version, `golden + golden` read `loaded (v..., 57007 bytes)`
    # while the session had loaded 114 kB. Counting is the only thing that
    # tells a doubled file from a well-formed one, which is why
    # check-agents-md.sh counts for the other block too.
    begins = [i for i, l in enumerate(lines) if l.startswith(BEGIN_FLEET)]
    if not begins:
        return mismatch("block absent from the file the session loaded")
    if len(begins) != 1:
        return mismatch("the managed block appears %d times in the file the "
                        "session loaded" % len(begins))
    begin = begins[0]

    version = "unknown"
    payload_start = begin + 1
    if begin + 1 < len(lines) and lines[begin + 1].startswith(VERSION_PREFIX):
        token = lines[begin + 1][len(VERSION_PREFIX):].split(b"-->")[0].strip()
        version = version_token(token)
        payload_start = begin + 2

    ends = [i for i in range(payload_start, len(lines)) if lines[i].strip() == END_FLEET]
    end = ends[0] if ends else None

    if version != want_version:
        return mismatch("stale version (loaded v%s, installed v%s)" % (version, want_version))

    # A head-truncation keeps the version line intact and takes the END marker
    # with it, which is exactly the 2026-09-05 shape: the block still looks
    # like ours and is a fraction of its length.
    if end is None:
        present = len(b"\n".join(lines[payload_start:]))
        return mismatch("truncated (%d of %s bytes, no END marker)" % (
            present, state.get("bytes", "?")))
    # A second END with only one BEGIN is not the doubling above; it is a
    # block whose payload swallowed another one's tail. Named separately so
    # the line says which shape was found.
    if len(ends) != 1:
        return mismatch("expected exactly one END FLEET GUIDANCE line, found "
                        "%d" % len(ends))

    payload = b"\n".join(lines[payload_start:end])
    if payload:
        payload += b"\n"

    try:
        want_bytes = int(state.get("bytes", ""))
    except Exception:
        want_bytes = None
    if want_bytes is not None and len(payload) < want_bytes:
        return mismatch("truncated (%d of %d bytes)" % (len(payload), want_bytes))
    if want_bytes is not None and len(payload) != want_bytes:
        return mismatch("content differs (%d bytes, installed %d)" % (len(payload), want_bytes))

    want_sha = state.get("sha256", "")
    if want_sha and hashlib.sha256(payload).hexdigest() != want_sha:
        return mismatch("content differs (same length, different bytes)")

    return "loaded (v%s, %d bytes)" % (version, len(payload))


def agents_verdict(data, state, path):
    """The repo's AGENTS.md, against the guidance this session actually loaded.

    Two things are checkable from the file alone, and one is not. STRUCTURE is:
    the managed block's markers and their order are what sync.sh's parse
    depends on, and the doubled-block corruption of c86465f shows up here as
    two BEGIN lines. The guidance VERSION is, because every repo the sync
    reaches carries the payload it was synced with, beside its hook.

    What is NOT checkable is a hand edit to prose INSIDE a well-formed managed
    block: that needs the template the block was generated from, and a
    consumer repo does not carry one -- fleet-guidance.state records the
    digest of the PAYLOAD, while the managed region is rendered per-repo by
    build-agents-md.sh from that repo's own `sections:`. Issue #123's item
    1(b) asked for exactly that comparison; it is not implemented, and the
    verdict is named MANAGED BLOCK MALFORMED rather than EDITED ABOVE THE
    MARKER so it claims only what it measures. An operator reads the old name
    as "someone changed the text", and a one-byte prose edit inside a
    structurally intact block reads `current`. Closing 1(b) needs a
    `Managed-sha256:` header line emitted at sync time -- a change to the
    managed-block format in ~20 repos -- and is recorded as a follow-up in
    docs/guidance-impact.md rather than smuggled in behind a verdict name.

    THE FILENAME GATES ALL OF IT. Without that gate the structural checks ran
    on every Project memory file that merely QUOTED a marker anywhere -- a
    `.claude/rules/*.md` naming BEGIN MANAGED SECTION in prose, a rules file
    or a nested CLAUDE.md carrying its own `## Repo-specific additions`
    heading -- and each one produced a false EDITED verdict that travelled
    into the receipt and was printed at the next session start. This repo's
    own tree is clean, so nothing here would have shown it; the ~20 consumer
    repos are where it would have fired. The name is checked rather than the
    `<!-- Source: _agent-guidance -->` line the build script emits, because a
    corruption that removed that line would then silently stop the checking --
    a filename survives the damage the checks exist to find.
    """
    if os.path.basename(path) != "AGENTS.md":
        return None

    lines = data.split(b"\n")
    begins = [i for i, l in enumerate(lines) if BEGIN_MANAGED in l]
    ends = [i for i, l in enumerate(lines) if END_MANAGED in l]
    markers = [i for i, l in enumerate(lines) if l.rstrip(b"\r") == MARKER]
    fragments = [i for i, l in enumerate(lines)
                 if l.startswith(MARKER) and l.rstrip(b"\r") != MARKER]

    if not begins and not markers and not fragments:
        return None      # an ordinary project memory file; nothing to judge

    edited = "MANAGED BLOCK MALFORMED \u2014 "
    if len(begins) != 1:
        return edited + "expected exactly one BEGIN MANAGED SECTION line, found %d" % len(begins)
    if len(ends) != 1:
        return edited + "expected exactly one END MANAGED SECTION line, found %d" % len(ends)
    if fragments:
        return edited + "a truncated marker fragment on line %d (see c86465f)" % (fragments[0] + 1)
    if len(markers) != 1:
        return edited + 'expected exactly one "## Repo-specific additions" line, found %d' % len(markers)
    if not begins[0] < ends[0] < markers[0]:
        return edited + "the markers are out of order (BEGIN, END, then the marker)"

    want_version = state.get("version", "")
    if not want_version:
        return None
    want_version = version_token(want_version)

    payload = read_bytes(os.path.join(os.path.dirname(path), ".claude", "hooks",
                                      "fleet-guidance.md"))
    if payload is None:
        return None      # a repo the sync keeps on the inlined guidance

    repo_version = hashlib.sha256(payload).hexdigest()[:8]
    if repo_version == want_version:
        return "current (v%s)" % repo_version
    # Two content ids have no ordering, so this says WHICH is which rather
    # than claiming one is older than the other.
    return "BEHIND \u2014 this repo ships v%s, the session loaded v%s" % (
        repo_version, want_version)


def healthy(value):
    """A verdict that reports nothing wrong."""
    return value.startswith("loaded") or value.startswith("current")


def write_receipt(updates):
    """The channel that actually carries.

    Measured: this hook's stdout reaches nothing. The next session's
    SessionStart hook reads this file and prints what the last one loaded, so
    a mismatch is never silent for more than one session. Merged rather than
    replaced, so a Project event does not erase the User verdict from the same
    session, and replaced atomically so a half-written receipt is never read.

    `unread=1` is what makes "one session and no longer" TRUE rather than
    approximately true, in both directions:

      * a healthy verdict never replaces one nobody has announced yet, so two
        sessions sharing one ~/.claude cannot erase each other's. Measured on
        the earlier version: mismatch(S1) -> healthy(S2) -> mismatch(S1) ->
        healthy(S2) left `fleet=loaded` and no session ever printed the
        mismatch;
      * fleet-memory.sh clears the flag when it announces the verdict, so a
        machine whose load-time hook stopped running re-announces nothing.
        That is also why there is no `ts` here: with the flag, "when" is
        exactly "your previous session", and the per-load timestamps live in
        instructions-log.jsonl where a report can total them.
    """
    existing = read_kv(RECEIPT)
    # A MISMATCH IS NEVER OVERWRITTEN BY A HEALTHY VERDICT WITHIN ONE SESSION,
    # NOR WHILE IT IS STILL UNREAD. A multi-repo session loads many AGENTS.md
    # files; if the last one simply won, a repo reading BEHIND would be erased
    # by the next repo reading current and the receipt would report the
    # machine clean. Once a SessionStart HAS announced it, a healthy verdict
    # replaces it freely -- otherwise one bad load would follow a machine
    # forever and the line would stop meaning anything.
    sid = updates.get("session", "")
    # `bool(sid) and ...`: an event with no session_id leaves `session` empty,
    # and a bare equality test then reads EVERY later session as the same one.
    # Measured on the earlier version, that froze the receipt permanently -- a
    # mismatch recorded once survived every later healthy load and was
    # re-announced at every session start on a machine that was now healthy.
    same_session = bool(sid) and existing.get("session", "") == sid
    unread = existing.get("unread", "") == "1"
    for key in ("fleet", "agents"):
        if key not in updates:
            continue
        previous = existing.get(key, "")
        if (same_session or unread) and previous \
                and not healthy(previous) and healthy(updates[key]):
            del updates[key]
    existing.update(updates)
    existing = {k: clean(v) for k, v in existing.items()}
    order = ["session", "unread", "fleet", "agents"]
    keys = [k for k in order if k in existing] + sorted(k for k in existing if k not in order)
    tmp = RECEIPT + ".tmp"
    try:
        fd = open_owned(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "wb") as fh:
            for key in keys:
                fh.write(("%s=%s\n" % (key, existing[key])).encode("utf-8"))
        os.replace(tmp, RECEIPT)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def emit(line):
    """Explicit UTF-8 bytes, never the locale's idea of stdout.

    Measured on CLI 2.1.261: nothing reads this. It is kept because it costs
    nothing, because the tests read it, and because a future CLI that does
    surface hook stdout turns these lines on with no change here.
    """
    try:
        sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    except Exception:
        pass


# ---- the log line -------------------------------------------------------
#
# One JSON object per line: the six keys skills-evals#139's per-arm delivery
# receipt reads (file_path, memory_type, load_reason, bytes, sha256, ts), plus
# a `session` key so scripts/instructions-report.sh can total a session rather
# than a directory. `session` is a truncated DIGEST of the session id, not the
# id: it groups exactly as well and carries nothing onward.
#
# `bytes: 0` with an empty `sha256` is how an unreadable path is recorded -- a
# file that was deleted between the load and this hook, a directory, a path
# that never existed. An empty file has a real digest, so the empty string can
# only mean "not read".
content = read_bytes(file_path)
record = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "session": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8] if session_id else "",
    "load_reason": load_reason,
    "memory_type": memory_type,
    "file_path": relativize(file_path),
    "bytes": len(content) if content is not None else 0,
    "sha256": hashlib.sha256(content).hexdigest() if content is not None else "",
}
try:
    # lstat, not getsize: the size that decides a rotation must be the LOG's
    # own, never that of whatever a symlink at that path points at. A symlink
    # is a few dozen bytes, so it never rotates and never becomes the `.1`
    # file that instructions-report.sh would then read through.
    if os.path.lexists(LOG) and os.lstat(LOG).st_size >= LOG_CAP:
        os.replace(LOG, LOG + ".1")   # renames the link itself, never follows
    fd = open_owned(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    with os.fdopen(fd, "wb") as fh:
        # ensure_ascii makes this program's output pure ASCII, so the encode
        # cannot raise on a path carrying non-ASCII bytes.
        fh.write((json.dumps(record, ensure_ascii=True) + "\n").encode("ascii"))
except Exception:
    pass

# ---- the verdicts -------------------------------------------------------
state = read_state()
updates = {"session": record["session"], "unread": "1"}
verdicts = {}

if content is not None and memory_type == "User":
    suffix = fleet_verdict(content, state)
    if suffix:
        verdicts["fleet"] = suffix
        emit("fleet-guidance: " + suffix)

if content is not None and memory_type == "Project":
    suffix = agents_verdict(content, state, file_path)
    if suffix:
        verdicts["agents"] = suffix
        emit("agents-md: " + suffix)

if verdicts:
    updates.update(verdicts)
    write_receipt(updates)
PY

python3 -c "$INSTRUCTIONS_RECEIPT_PROGRAM" "$STATE_DIR" "$LOG" "$RECEIPT" "$STATE" 2>/dev/null
exit 0
