#!/usr/bin/env bash
# github-write-marker.sh — an agent's GitHub write carries the agent marker.
#
# THE CONTRACT
# ------------
# Every write an agent makes on GitHub goes out as Adam's own account. So
# every PR or issue body, issue or PR comment, review body and review reply an
# agent writes ends with the footer
#
#   <sub>🤖 Written by <agent> on behalf of @Adam-S-Daniel</sub>
#
# and an agent never submits an APPROVE review: an approval cannot carry the
# marker and reads as Adam's own sign-off. This PreToolUse hook blocks the
# write that breaks either rule and tells the agent what to append, so the
# agent corrects it and retries. It never rewrites the body itself. See
# docs/decisions/0023-mark-agent-contributions.md and the "Agent-made GitHub
# contributions carry a marker" section of agents-md/base.md.
#
# WHAT IT CHECKS
# --------------
# The check is the LEXICAL token `on behalf of @Adam-S-Daniel`, which the
# fleet's AST rule permits for a lexical token. It does not parse shell.
#
# - MCP tools, matched by a regex on the tool NAME, never by a remembered
#   connector prefix (the claude.ai connector's prefix varies by surface):
#     ^mcp__.*github.*__(add_issue_comment|issue_write|create_pull_request|
#       update_pull_request|pull_request_review_write|
#       add_comment_to_pending_review|add_reply_to_pull_request_comment|
#       update_issue_comment)$
#   `event: APPROVE` (any case) is blocked. A non-empty `body` without the
#   token is blocked. No `body` at all (labels or state only) is allowed.
# - Bash, on `tool_input.command`. A GitHub write is `gh issue
#   create|comment|edit`, `gh pr create|comment|edit|review`, or `gh api`
#   with `-X`/`--method` POST|PATCH|PUT or a `-f`/`-F body=` field, to a path
#   containing `/issues`, `/pulls` or `/comments`. `gh pr review --approve`
#   (or `-a`) is blocked. Any other write is allowed when the command text
#   holds the token, or when a `--body-file`/`-F <path>` or `body=@<path>`
#   file exists and holds it; a write with no body flag at all
#   (`gh issue edit --add-label x`) is allowed; the rest is blocked.
#   A command that holds the token anywhere passes as a whole, so a chain of
#   two writes with one marked passes too: the limit of a lexical check.
#
# EXIT CODES (Claude Code PreToolUse): 0 allows the call; 2 blocks it and
# shows stderr to the agent. Anything this hook cannot read is ALLOWED with a
# stderr warning: a broken hook must not wedge every tool call.
#
# The footer names the agent from `AGENT_NAME`: `codex` gives `Codex`, any
# other value or none gives `Claude Code`.
set -uo pipefail

# Read stdin ONCE.
EVENT="$(cat 2>/dev/null)" || EVENT=""

# Fast path: nothing GitHub-shaped in the event, nothing to check. Every Bash
# call reaches this hook when it is registered on `Bash`, so the common case
# never starts an interpreter.
case "$EVENT" in
    *gh*|*github*) ;;
    *) exit 0 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "github-write-marker: DEGRADED — python3 is not on PATH; GitHub writes are not being checked for the agent marker." >&2
    exit 0
fi

# The event travels in the environment because stdin carries the script.
rc=0
GITHUB_WRITE_MARKER_EVENT="$EVENT" python3 - <<'PY' || rc=$?
import json
import os
import re
import sys

TOKEN = "on behalf of @Adam-S-Daniel"
AGENT = "Codex" if os.environ.get("AGENT_NAME", "").strip().lower() == "codex" else "Claude Code"
FOOTER = "<sub>🤖 Written by %s on behalf of @Adam-S-Daniel</sub>" % AGENT

MCP_TOOL = re.compile(
    r"^mcp__.*github.*__(add_issue_comment|issue_write|create_pull_request|"
    r"update_pull_request|pull_request_review_write|add_comment_to_pending_review|"
    r"add_reply_to_pull_request_comment|update_issue_comment)$")

# Bash: a gh subcommand that writes a body-bearing object.
GH_WRITE = re.compile(r"\bgh\s+(?:issue\s+(?:create|comment|edit)|pr\s+(?:create|comment|edit|review))\b")
GH_PR_REVIEW = re.compile(r"\bgh\s+pr\s+review\b")
APPROVE_FLAG = re.compile(r"(?:^|\s)(?:--approve|-a)(?=\s|=|$|[;&|)])")
# Bash: `gh api` writing to an issues, pulls or comments endpoint.
GH_API = re.compile(r"\bgh\s+api\b")
API_METHOD = re.compile(r"(?:^|\s)(?:-X|--method)(?:\s+|=)?['\"]?(?:POST|PATCH|PUT)\b", re.I)
API_BODY_FIELD = re.compile(r"(?:^|\s)(?:-f|-F|--field|--raw-field)(?:\s+|=)['\"]?body=")
API_PATH = re.compile(r"/(?:issues|pulls|comments)\b")
# Does the command carry a body at all?
BODY_FLAG = re.compile(r"(?:^|\s)(?:--body|--body-file|-b|-F)(?=\s|=)")
# Files that may carry the body.
BODY_FILE = re.compile(r"(?:^|\s)(?:--body-file|-F)(?:\s+|=)(['\"]?)([^\s'\"]+)\1")
API_BODY_FILE = re.compile(r"(?:^|\s)(?:-F|--field)(?:\s+|=)['\"]?body=@([^\s'\"]+)")


def allow():
    sys.exit(0)


def block(what):
    sys.stderr.write(
        "github-write-marker: blocked — %s goes out as @Adam-S-Daniel with no "
        "agent marker. End the body with this line, on its own, then retry:\n"
        "%s\n"
        "(ADR 0023: docs/decisions/0023-mark-agent-contributions.md in "
        "Adam-S-Daniel/_agent-guidance.)\n" % (what, FOOTER))
    sys.exit(2)


def block_approve():
    sys.stderr.write(
        "github-write-marker: blocked — an agent never submits an APPROVE review "
        "on Adam's account: an approval cannot carry the agent marker and reads "
        "as his own sign-off. Submit a COMMENT review instead, its body ending "
        "with this line, and leave the approval to Adam:\n%s\n" % FOOTER)
    sys.exit(2)


raw = os.environ.get("GITHUB_WRITE_MARKER_EVENT", "")
try:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("not an object")
except Exception as exc:
    sys.stderr.write("github-write-marker: WARNING — the hook input is not a JSON "
                     "object (%s); allowing the call unchecked.\n" % exc.__class__.__name__)
    allow()

tool = event.get("tool_name")
tool_input = event.get("tool_input")
if not isinstance(tool, str) or not isinstance(tool_input, dict):
    allow()

# ── MCP ────────────────────────────────────────────────────────────────────
m = MCP_TOOL.match(tool)
if m:
    ev = tool_input.get("event")
    if isinstance(ev, str) and ev.strip().upper() == "APPROVE":
        block_approve()
    body = tool_input.get("body")
    if isinstance(body, str) and body.strip() and TOKEN not in body:
        block("this %s" % m.group(1))
    allow()

if tool != "Bash":
    allow()

# ── Bash ───────────────────────────────────────────────────────────────────
cmd = tool_input.get("command")
if not isinstance(cmd, str):
    allow()

is_gh_write = bool(GH_WRITE.search(cmd))
is_api_write = bool(GH_API.search(cmd) and API_PATH.search(cmd)
                    and (API_METHOD.search(cmd) or API_BODY_FIELD.search(cmd)))
if not (is_gh_write or is_api_write):
    allow()

if GH_PR_REVIEW.search(cmd) and APPROVE_FLAG.search(cmd):
    block_approve()

if TOKEN in cmd:
    allow()

cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
paths = [p for _, p in BODY_FILE.findall(cmd)] + API_BODY_FILE.findall(cmd)
for path in paths:
    path = os.path.expanduser(path)
    if cwd and not os.path.isabs(path):
        path = os.path.join(cwd, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if TOKEN in fh.read():
                allow()
    except Exception:
        pass  # a missing or unreadable file is no evidence of a marker

has_body = (is_gh_write and BODY_FLAG.search(cmd)) or (is_api_write and API_BODY_FIELD.search(cmd))
if not has_body:
    allow()

block("this GitHub write")
PY

if [ "$rc" -eq 2 ]; then
    exit 2
fi
if [ "$rc" -ne 0 ]; then
    echo "github-write-marker: WARNING — the check exited $rc without a verdict; allowing the call unchecked." >&2
fi
exit 0
