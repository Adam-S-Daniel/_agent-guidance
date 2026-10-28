#!/usr/bin/env python3
"""mine_sessions.py: the LOCAL-ONLY session miner.

    mine_sessions.py [--projects-dir ~/.claude/projects] [--cursor FILE] [--out FILE]

Reads ~/.claude/projects/*/*.jsonl transcripts newer than the cursor and
prints one JSON record per lesson moment:
  {"sid": <12 hex of sha256 of the session id>, "ts": ..., "kind": ..., "excerpt": ...}
kinds: correction, interrupt, repeated-failure, ci-red-after-push, unfinished.
Excerpts are redacted and cut to 200 characters. A session with no tool calls
or fewer than 3 assistant turns is not real work and is skipped.

REFUSES to run in a cloud session (CLAUDE_CODE_REMOTE=true): transcripts hold
private data, and a cloud session can push to public repos. Exit 3.
Exit codes: 0 ok; 2 bad usage; 3 refused (cloud).

validate_lesson() is the check the draft-skill dispatcher applies to a lesson
excerpt before it goes into a fire payload.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

EXCERPT_MAX = 200
LESSON_MAX = 2000

_SECRET_WORDS = r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|auth|credential|key)"
REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "<secret>"),
    (re.compile(r"https?://[^\s'\"<>)]+"), "<url>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<email>"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
                r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*)"), "<secret>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <secret>"),
    (re.compile(rf"(?i)\b([\w-]*{_SECRET_WORDS}[\w-]*)\s*[=:]\s*(?!<|bearer\b)[^\s'\",;]{{4,}}"), r"\1=<secret>"),
    (re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\Users\\)[^/\\\s]+"), "~"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[A-Za-z0-9_+/=-]{32,}\b"), "<secret>"),
]


def redact(text: str) -> str:
    for pat, repl in REDACTIONS:
        text = pat.sub(repl, text)
    return text


def excerpt(text: str) -> str:
    text = " ".join(redact(text).split())
    return text[:EXCERPT_MAX]


def validate_lesson(text) -> str | None:
    """Return a reason the text may not travel in a fire payload, or None."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text) > LESSON_MAX:
        return "too long"
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text):
        return "control characters"
    if redact(text) != text:
        return "contains something redaction would remove"
    return None


CORRECTION = re.compile(
    r"(?i)^\s*(no[,.! ]|nope|wrong|stop\b|don'?t\b|do not\b|that'?s (not|wrong)|actually\b|i (said|told you|asked)\b"
    r"|why did you|you (should|shouldn'?t|didn'?t|forgot|missed)\b|not what i)")
UNFINISHED = re.compile(r"(?i)\b(next,? i|still need|remaining|not yet|haven'?t|couldn'?t|todo|unfinished|i'?ll continue)\b")
CI_RED = re.compile(r"(?i)\b(fail(?:ed|ure|ing)?|red|error)\b")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _result_text(block) -> str:
    c = block.get("content")
    return c if isinstance(c, str) else _text_of(c)


def analyze(records: list, since: str = "") -> list:
    """Lesson moments in one session's records: [(ts, kind, text)]."""
    cmds, fail_count, out = {}, {}, []
    pushed, assistant_turns, tool_calls = None, 0, 0
    last_assistant_text, last_ts, last_role = "", "", ""
    for r in records:
        msg, ts = r.get("message") or {}, r.get("timestamp", "")
        role = msg.get("role") or r.get("type")
        content = msg.get("content")
        if ts:
            last_ts = ts
        last_role = role or last_role
        if role == "assistant":
            assistant_turns += 1
            last_assistant_text = _text_of(content) or last_assistant_text
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_calls += 1
                    cmd = (b.get("input") or {}).get("command")
                    if b.get("name") == "Bash" and isinstance(cmd, str):
                        cmds[b.get("id")] = cmd
                        if re.search(r"\bgit push\b", cmd):
                            pushed = ts
        elif role == "user":
            blocks = content if isinstance(content, list) else []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    cmd = cmds.get(b.get("tool_use_id"))
                    if cmd is None:
                        continue
                    res = _result_text(b)
                    if b.get("is_error"):
                        key = re.sub(r"\s+", " ", cmd)[:100]
                        fail_count[key] = fail_count.get(key, 0) + 1
                        if fail_count[key] == 3:
                            out.append((ts, "repeated-failure", cmd))
                    if pushed and re.search(r"\bgh (pr|run)\b", cmd) and CI_RED.search(res):
                        out.append((ts, "ci-red-after-push", cmd + " -> " + res[:100]))
                        pushed = None
            text = _text_of(content).strip()
            if text.startswith("[Request interrupted by user"):
                out.append((ts, "interrupt", text))
            elif text and not blocks and CORRECTION.match(text):
                out.append((ts, "correction", text))
    if last_role == "assistant" and UNFINISHED.search(last_assistant_text[-300:]):
        out.append((last_ts, "unfinished", last_assistant_text[-300:]))
    if tool_calls == 0 or assistant_turns < 3:
        return []
    return [o for o in out if o[0] > since]


def mine(projects_dir: str, since: str = ""):
    recs, newest = [], since
    for path in sorted(glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))):
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        if not rows:
            continue
        sid = next((r["sessionId"] for r in rows if isinstance(r.get("sessionId"), str)),
                   os.path.splitext(os.path.basename(path))[0])
        hashed = hashlib.sha256(sid.encode()).hexdigest()[:12]
        for ts, kind, text in analyze(rows, since):
            recs.append({"sid": hashed, "ts": ts, "kind": kind, "excerpt": excerpt(text)})
            newest = max(newest, ts)
    recs.sort(key=lambda r: (r["ts"], r["sid"], r["kind"]))
    return recs, newest


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("CLAUDE_CODE_REMOTE") == "true":
        print("mine_sessions: refusing to run in a cloud session", file=sys.stderr)
        return 3
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--cursor")
    ap.add_argument("--out")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    since = ""
    if a.cursor and os.path.exists(a.cursor):
        with open(a.cursor, encoding="utf-8") as f:
            since = f.read().strip()
    recs, newest = mine(a.projects_dir, since)
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in recs)
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    if a.cursor and newest:
        with open(a.cursor, "w", encoding="utf-8") as f:
            f.write(newest + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
