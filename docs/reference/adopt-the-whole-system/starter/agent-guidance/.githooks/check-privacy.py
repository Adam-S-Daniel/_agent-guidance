#!/usr/bin/env python3
"""check-privacy.py <base> <head>: refuse added lines and commit messages in
base..head that match a denylist of your real names, emails and hosts.

The denylist is one regular expression per line (case-insensitive; blank lines
and lines starting with # are skipped), in the file PRIVACY_DENYLIST_FILE
names. Never commit that file. Output is only file:line and the pattern's
number, never the matched text, because CI logs on a public repo are public."""
import os, re, subprocess, sys

base, head = sys.argv[1:3]
path = os.environ.get("PRIVACY_DENYLIST_FILE") or ""
if not os.path.isfile(path):
    print("privacy: no denylist file; failing closed", file=sys.stderr)
    sys.exit(1)
patterns = [re.compile(l.strip(), re.I) for l in open(path, encoding="utf-8")
            if l.strip() and not l.lstrip().startswith("#")]
if not patterns:
    print("privacy: the denylist is empty; failing closed", file=sys.stderr)
    sys.exit(1)

def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout.decode("utf-8", "replace")

hits = []
def scan(where, text):
    for n, p in enumerate(patterns, 1):
        if p.search(text):
            hits.append(f"{where}: pattern {n}")

# Added paths are scanned too. A path that matches is never printed.
for path in git("diff", "--name-only", "-z", "--diff-filter=ACMR", f"{base}...{head}").split("\0"):
    if path:
        scan("an added path", path)
safe = {}
def where(path, line):
    if path not in safe:
        safe[path] = not any(p.search(path) for p in patterns)
    return f"{path}:{line}" if safe[path] else f"(a file whose path matches):{line}"

# --text scans binary files as text. A "+++ " row is a file header only
# between "diff --git" and the file's first "@@"; inside a hunk it is an
# added line whose text starts with "++ ".
name, line, in_hunk = "?", 0, False
for row in git("diff", "--no-color", "--no-ext-diff", "--text", "--unified=0", f"{base}...{head}").splitlines():
    if row.startswith("diff --git "):
        in_hunk = False
    elif not in_hunk and row.startswith("+++ "):
        name = row[6:] if row.startswith("+++ b/") else row[4:]
    elif row.startswith("@@"):
        in_hunk = True
        m = re.search(r"\+(\d+)", row)
        line = int(m.group(1)) if m else 0
    elif in_hunk and row.startswith("+"):
        scan(where(name, line), row[1:])
        line += 1
for sha in git("rev-list", f"{base}..{head}").split():
    scan(f"commit {sha[:12]} message", git("log", "-1", "--format=%B", sha))
for h in hits:
    print("privacy:", h)
sys.exit(1 if hits else 0)
