#!/usr/bin/env bash
set -uo pipefail
#
# instructions-report.sh — what a session actually loaded, in bytes.
#
# THE FIGURE THIS REPLACES. "A session with 19 repos open carried 19 identical
# copies: 332.3k tokens of a 1M window" is quoted in the guidance and in every
# repo's AGENTS.md stub. It was measured ONCE, on 2026-08-29, by hand. Nothing
# has re-measured it since, and nothing would notice if the number stopped
# being true — a shrink that worked and a shrink that quietly regressed look
# identical from inside a session.
#
# The InstructionsLoaded hook (.claude/hooks/instructions-loaded.sh) writes one
# JSON line per memory-file load into $CLAUDE_CONFIG_DIR/instructions-log.jsonl.
# This totals them per session: bytes per memory_type, files per load_reason.
# It is a REPORT, not a gate — it never fails a build and never writes.
#
# NO ABSOLUTE PATHS, EVER. The output is meant to be pasted into pull requests
# on a public repo. The log already stores relative paths; the config dir this
# names is relativized here for the same reason.
#
# Usage:
#   scripts/instructions-report.sh [--config-dir DIR] [--all] [--format text|json]
#
# --config-dir  where to read the log from (default: $CLAUDE_CONFIG_DIR, else
#               ~/.claude). The rotated predecessor (…jsonl.1) is read too.
# --all         every session in the log; the default is the latest one only.
# --format      text (default) or json.
#
# Exit: 0 with a report, 2 when there is nothing to report at all (an absent or
# empty log), 3 when a log IS there but could not be read -- nothing in it
# parsed, or the path is not a readable file at all -- 1 on a usage error. A run that found NOTHING must not read the same as a run that
# found nothing wrong — the convention scripts/check-guidance-coverage.js
# already follows here — and "the hook has not run" is a different answer from
# "the hook ran and its log is unreadable": the first is a wiring question,
# the second is a corrupted file.

# `${HOME:-}` and not `$HOME`: under `set -u` an unset HOME is a raw
# "HOME: unbound variable" from bash rather than this script's own error. The
# sibling hook writes it the same way, for the same reason. If the default is
# what we end up using and neither variable is set, that is a usage error with
# a sentence attached — checked after the flags are parsed, since
# --config-dir answers it.
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}"
CONFIG_DIR_GIVEN=false
SHOW_ALL=false
FORMAT=text

# `shift 2` with one argument left FAILS and shifts NOTHING, so a value-taking
# flag given last spins this loop forever -- a report that hangs a terminal
# instead of printing a usage error. Every such flag checks for its value.
need_value() {
    [[ $# -ge 2 ]] || { echo "instructions-report: $1 needs a value" >&2; exit 1; }
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir) need_value "$@"; CONFIG_DIR="$2"; CONFIG_DIR_GIVEN=true; shift 2 ;;
        --all)        SHOW_ALL=true; shift ;;
        --format)     need_value "$@"; FORMAT="$2"; shift 2 ;;
        -h|--help)    sed -n '4,36p' "$0"; exit 0 ;;
        *) echo "instructions-report: unknown argument '$1'" >&2; exit 1 ;;
    esac
done

case "$FORMAT" in
    text|json) ;;
    *) echo "instructions-report: --format must be text or json, got '$FORMAT'" >&2; exit 1 ;;
esac

if ! $CONFIG_DIR_GIVEN && [[ -z "${CLAUDE_CONFIG_DIR:-}" && -z "${HOME:-}" ]]; then
    echo "instructions-report: neither CLAUDE_CONFIG_DIR nor HOME is set — pass --config-dir DIR" >&2
    exit 1
fi

command -v python3 >/dev/null 2>&1 || {
    echo "instructions-report: python3 is required to read the log" >&2; exit 1; }

python3 - "$CONFIG_DIR" "$SHOW_ALL" "$FORMAT" <<'PY'
import json
import os
import re
import sys

config_dir, show_all, fmt = sys.argv[1], sys.argv[2] == "true", sys.argv[3]

# The log's values are DATA READ OUT OF A FILE that this script echoes into a
# terminal. `--format json` escapes them for free; the TEXT mode -- the output
# this script's own header calls "the thing people quote" and describes as
# pasted into pull requests -- printed `session`, `last_ts`, `memory_type` and
# `load_reason` verbatim, so a load_reason carrying an ANSI sequence reached
# the terminal live. Same two rules the hook applies at write time: no control
# characters, and a cap. Applied at PRINT time only, so the json form stays a
# faithful record of what the log holds.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
TEXT_CAP = 200


def clean(value):
    return CONTROL_CHARS.sub("", str(value))[:TEXT_CAP]
log = os.path.join(config_dir, "instructions-log.jsonl")
files = [log + ".1", log]      # oldest first, so ts ordering reads naturally


def shorten(path):
    """Never an absolute path. This output gets pasted into public PRs."""
    target = os.path.abspath(path)
    home = os.path.expanduser("~").rstrip("/")
    if home and target.startswith(home + "/"):
        return "~/" + target[len(home) + 1:]
    parts = [p for p in target.split("/") if p]
    return ".../" + "/".join(parts[-2:]) if parts else "..."


sessions = {}
order = []
unparseable = 0
unreadable = 0
truncated = 0
present = False

for path in files:
    if not os.path.lexists(path):
        continue
    # isfile, not exists, and then O_NONBLOCK -- the same pair the hook's
    # read_bytes and open_owned use, and for the same two reasons. A FIFO at
    # the log path passes os.path.exists and then BLOCKS FOREVER in a bare
    # open(): measured, rc 124 at a 60-second bound, on a script whose own
    # header promises "a REPORT, not a gate -- it never fails a build". A hang
    # is worse than a failure. And a DIRECTORY there passed os.path.exists,
    # raised in open(), hit the bare `continue`, and left `present` true with
    # no sessions -- so the run answered exit 2, "the hook has not run here",
    # which sends a reader to check the registration when what they have is a
    # corrupted path. Counted as unreadable, it answers exit 3 with the rest of
    # the cannot-read class.
    if not os.path.isfile(path):
        unreadable += 1
        continue
    present = True
    try:
        handle = os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "r",
                           encoding="utf-8", errors="replace")
    except Exception:
        unreadable += 1
        continue
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                # A truncated tail or a half-written line is expected on a log
                # an unattended hook appends to. Counted, never fatal.
                unparseable += 1
                continue
            # ABOVE the .get calls, not below them. A bare scalar, a list or a
            # literal `null` is valid JSON, so json.loads returns it happily
            # and `rec.get(...)` is what raises -- which the except above
            # already counted, leaving this check permanently unreachable. It
            # read as a guard that was doing something; now it is one.
            if not isinstance(rec, dict):
                unparseable += 1
                continue
            try:
                key = str(rec.get("session", ""))
                size = int(rec.get("bytes", 0))
            except Exception:
                unparseable += 1
                continue
            entry = sessions.get(key)
            if entry is None:
                entry = sessions[key] = {
                    "session": key, "files": 0, "bytes": 0,
                    "by_memory_type": {}, "by_load_reason": {}, "last_ts": "",
                }
                order.append(key)
            entry["files"] += 1
            entry["bytes"] += size
            # The hook reads at most 4 MiB of any one file, so a flagged line
            # carries the file's real size beside a digest of its first 4 MiB.
            # Said out loud rather than left in the log, because this report
            # is the thing people quote.
            if rec.get("truncated"):
                truncated += 1
            mt = str(rec.get("memory_type", "")) or "(unset)"
            lr = str(rec.get("load_reason", "")) or "(unset)"
            entry["by_memory_type"][mt] = entry["by_memory_type"].get(mt, 0) + size
            entry["by_load_reason"][lr] = entry["by_load_reason"].get(lr, 0) + 1
            ts = str(rec.get("ts", ""))
            if ts > entry["last_ts"]:
                entry["last_ts"] = ts

# "Nothing parsed" and "nothing ran" are DIFFERENT ANSWERS, and this script's
# own header makes that distinction about exit 2 versus 0. A log full of noise
# reported "the hook has not run here", which sends a reader to check the
# registration when what they have is a corrupted file.
if not sessions and unparseable:
    sys.stderr.write(
        "instructions-report: %d line(s) under %s, none of them parseable -- "
        "the hook has run here, but its log cannot be read\n"
        % (unparseable, shorten(config_dir)))
    sys.exit(3)

if not sessions and unreadable:
    sys.stderr.write(
        "instructions-report: the log under %s is not a readable file -- "
        "the hook may have run here, but its log cannot be read\n"
        % shorten(config_dir))
    sys.exit(3)

if not present or not sessions:
    sys.stderr.write(
        "instructions-report: no receipts under %s -- the InstructionsLoaded "
        "hook has not run here, or FLEET_GUIDANCE_SKIP removed its log\n"
        % shorten(config_dir))
    sys.exit(2)

chosen = sorted(sessions.values(), key=lambda s: (s["last_ts"], s["session"]))
if not show_all:
    chosen = chosen[-1:]

if fmt == "json":
    print(json.dumps({
        "log": shorten(log),
        "unparseable": unparseable,
        "truncated": truncated,
        "sessions": chosen,
    }, indent=2, sort_keys=True))
    sys.exit(0)


def pairs(mapping):
    return " | ".join("%s %s" % (clean(k), v) for k, v in sorted(mapping.items()))


total_files = sum(s["files"] for s in chosen)
total_bytes = sum(s["bytes"] for s in chosen)
print("instructions-report: %d session(s), %d files, %d bytes  (%s)"
      % (len(chosen), total_files, total_bytes, shorten(log)))
if unparseable:
    print("  %d unparseable line(s) skipped" % unparseable)
if truncated:
    print("  %d file(s) over the 4 MiB read cap: bytes are the file's real "
          "size, sha256 covers the first 4 MiB" % truncated)
for entry in chosen:
    print("")
    print("session %s  %d files, %d bytes  (last load %s)"
          % (clean(entry["session"]) or "(unset)", entry["files"],
             entry["bytes"], clean(entry["last_ts"]) or "unknown"))
    print("  bytes by memory type   %s" % pairs(entry["by_memory_type"]))
    print("  files by load reason   %s" % pairs(entry["by_load_reason"]))
PY
