#!/usr/bin/env bash
set -euo pipefail
#
# hook-eol-gitattributes.sh — Keep the fleet's hook scripts LF in every checkout.
#
# The sync delivers `.claude/hooks/*.sh` to every consumer repo, and Windows
# Git's `core.autocrlf=true` checks them out with CRLF. Bash run from WSL then
# fails to parse them (`syntax error near unexpected token $'{\r'`), so a WSL
# Claude Code or Codex session started in a `/mnt/<drive>` clone loses its
# SessionStart hooks. One attribute line fixes that for every clone on every
# machine, because attributes travel with the repo. See
# docs/decisions/0019-sync-pins-hook-scripts-to-lf-via-gitattributes.md.
#
# The single shared owner of that line: scripts/sync.sh writes it and
# scripts/drift-report.sh reports it, so "is the line there?" is decided in
# exactly one place (the same shape as bridge-status.sh).
#
# Usage: hook-eol-gitattributes.sh status <path>
#          Prints `present` (some line equals the managed line once its
#          surrounding whitespace, CR included, is stripped) or `missing`
#          (no such line, or no file). Never writes.
#        hook-eol-gitattributes.sh ensure <path>
#          Prints what it did: `created` (no file; written with the comment and
#          the line), `appended` (added at the end) or `present` (nothing
#          written). APPEND, NEVER REWRITE: every existing byte is kept, lines
#          are never reordered or removed, a missing final newline is supplied
#          before appending, and a CRLF file is appended to with CRLF.
#          Refuses with exit 3 and prints `refused` when <path> exists but is
#          not a regular file (a symlink or a directory): a file this script
#          cannot reason about is one it must not edit.
#
# Appending is correct even after a broad rule such as `* -text`: for each
# attribute, git takes the LAST matching line in the file, so the managed line
# at the end wins for the hook scripts and changes nothing else.

LINE='.claude/hooks/*.sh text eol=lf'
COMMENT='# managed by _agent-guidance: hook scripts must stay LF for bash (see ADR 0019)'

usage() {
    echo "Usage: $(basename "${BASH_SOURCE[0]}") status|ensure <path>" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
mode="$1" path="$2"
case "$mode" in status|ensure) ;; *) usage ;; esac

# Bytes, not lines: bash's `read` and command substitution both lose the
# distinctions this script exists to preserve (a final newline, a CR).
python3 - "$mode" "$path" "$LINE" "$COMMENT" <<'PY'
import os
import sys

mode, path, line, comment = sys.argv[1:5]
line_b = line.encode()
comment_b = comment.encode()


def has_line(data: bytes) -> bool:
    return any(raw.strip() == line_b for raw in data.split(b"\n"))


if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
    if mode == "status":
        # A symlink still resolves for git, but this script does not follow
        # one: report what it can read without writing.
        try:
            with open(path, "rb") as fh:
                print("present" if has_line(fh.read()) else "missing")
        except OSError:
            print("missing")
        sys.exit(0)
    print("refused")
    sys.exit(3)

if not os.path.exists(path):
    if mode == "status":
        print("missing")
        sys.exit(0)
    with open(path, "wb") as fh:
        fh.write(comment_b + b"\n" + line_b + b"\n")
    print("created")
    sys.exit(0)

with open(path, "rb") as fh:
    data = fh.read()

if has_line(data):
    print("present")
    sys.exit(0)
if mode == "status":
    print("missing")
    sys.exit(0)

# The file's own line ending: the terminator of its first line. A file with
# no line terminator at all gets LF.
first_nl = data.find(b"\n")
eol = b"\r\n" if first_nl > 0 and data[first_nl - 1:first_nl] == b"\r" else b"\n"

addition = b""
if data and not data.endswith(b"\n"):
    addition += eol
# The comment and the line are one managed unit. If the comment is already the
# last non-blank line (the line was removed by hand, the comment left), only
# the line is added back, so the comment is never duplicated.
non_blank = [raw.strip() for raw in data.split(b"\n") if raw.strip()]
if not (non_blank and non_blank[-1] == comment_b):
    addition += comment_b + eol
addition += line_b + eol

with open(path, "ab") as fh:
    fh.write(addition)
print("appended")
PY
