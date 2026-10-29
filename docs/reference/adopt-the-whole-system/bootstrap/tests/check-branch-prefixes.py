#!/usr/bin/env python3
"""check-branch-prefixes.py: the loop's branch names agree across the kit.

  * Every `claude/eval-<lane>-` a routine doc names (skills-evals docs/ and
    agent-guidance docs/routines/) is a branch some skills-evals workflow
    listens on (on.push.branches), and no doc names `claude/eval-<run id>`
    without its lane.
  * The pull request branches the loop opens (open_loop_pr.sh's --branch
    rule, plus the improve gate's eval-improve/) are exactly what the two
    disarm workflows cover: agent-skills' loop-disarm.yml and skills-evals'
    scaffold-disarm.yml.
  * dashboard-refresh.md lists exactly the branch prefixes the gates count
    as open loop pull requests (routine-improve-gate.yml and open_loop_pr.sh
    agree on that set), and no doc says draft-skill/ for the draft lane.

Workflow structure is parsed with PyYAML; branch names are lexical tokens.
Prints "N assertions passed" and exits 1 on any failure.
"""
import re
import sys
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent.parent
SE = KIT / "starter" / "skills-evals"
AG = KIT / "starter" / "agent-guidance"
AS = KIT / "starter" / "agent-skills"
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


def load(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


pushed = set()
for path in sorted((SE / ".github" / "workflows").glob("*.yml")):
    push = load(path)["on"].get("push") if isinstance(load(path)["on"], dict) else None
    for b in (push or {}).get("branches", []):
        if b.startswith("claude/"):
            pushed.add(b[:-1])                       # claude/eval-<lane>-*  ->  claude/eval-<lane>-
check("skills-evals listens on four routine lanes", pushed == {
    "claude/eval-results-", "claude/eval-improve-", "claude/eval-scaffold-", "claude/eval-draft-"})

docs = sorted((SE / "docs").glob("*.md")) + sorted((AG / "docs" / "routines").glob("*"))
for doc in docs:
    text = doc.read_text(encoding="utf-8")
    rel = doc.relative_to(KIT)
    named = set(re.findall(r"claude/eval-[a-z]+-", text))
    check(f"{rel}: names only branches a workflow listens on ({sorted(named - pushed)})", named <= pushed)
    check(f"{rel}: no branch without its lane", not re.search(r"claude/eval-(?![a-z]+-)", text))
    check(f"{rel}: the draft lane's branches are eval-draft/, not draft-skill/", "draft-skill/" not in text)


def open_prefixes(text):
    m = re.search(r'test\("\^\(([a-z|-]+)\)/"\)', text)
    return {p + "/" for p in m.group(1).split("|")} if m else set()


gate = (SE / ".github" / "workflows" / "routine-improve-gate.yml").read_text()
opener = (SE / "scripts" / "open_loop_pr.sh").read_text()
counted = open_prefixes(gate)
check("the improve gate counts some loop branches", bool(counted))
check("open_loop_pr.sh counts the same open loop branches as the improve gate", open_prefixes(opener) == counted)
m = re.search(r"\^\(([a-z|-]+)\)/\[A-Za-z0-9", opener)
opened = {p + "/" for p in m.group(1).split("|")} if m else set()
opened.add("eval-improve/")                          # the improve gate opens these itself


def disarmed(path):
    cond = load(path)["jobs"]["disarm"]["if"]
    return set(re.findall(r"head\.ref, '([a-z-]+/)'\)", cond))


covered = disarmed(AS / ".github" / "workflows" / "loop-disarm.yml") | \
    disarmed(SE / ".github" / "workflows" / "scaffold-disarm.yml")
check(f"every branch a gate opens is disarmed on a later push (opened {sorted(opened)}, covered {sorted(covered)})",
      opened == covered)
check("every lane the loop opens is counted as an open loop pull request", opened <= counted)

dash = (AG / "docs" / "routines" / "dashboard-refresh.md").read_text()
m = re.search(r"branch names (.*?)\)", dash, re.S)
listed = set(re.findall(r"`([a-z-]+/)`", m.group(1))) if m else set()
check(f"dashboard-refresh.md lists the counted loop branches (listed {sorted(listed)}, counted {sorted(counted)})",
      listed == counted)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
