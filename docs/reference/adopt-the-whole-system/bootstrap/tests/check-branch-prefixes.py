#!/usr/bin/env python3
"""check-branch-prefixes.py: the eval loop's branch lanes agree across the
exported skills-evals and the kit's docs.

The eval runner routine pushes its results to claude/<lane>-<run> branches,
and one no-permission "pushed" workflow per lane listens for them
(on.push.branches) and hands off to its gate. So:

  * the lanes are the claude/ branch patterns of skills-evals' workflows,
    and there are the five the system has: results (claude/eval-*),
    improve, guidance improve, scaffold and draft-skill;
  * every claude/<lane>- branch the routine prompt (docs/routine-prompt.md)
    or another skills-evals doc names matches a lane, and every lane is
    named by the routine prompt (no workflow listens for a branch nothing
    pushes);
  * where one branch name matches two lanes (claude/eval-improve-<run> also
    matches claude/eval-*), every lane it matches has a paths filter, and the
    filters share no directory, so exactly one lane's workflow fires;
  * the kit's docs/evals-and-loop.md names every lane pattern.

Workflow structure is parsed with PyYAML; branch names are lexical tokens.
Prints "N assertions passed" and exits 1 on any failure.
"""
import fnmatch
import re
import sys
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent.parent.parent
SE = KIT / "starter" / "skills-evals"
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


lanes = {}   # pattern -> (workflow name, set of top-level path dirs or None)
for path in sorted((SE / ".github" / "workflows").glob("*.yml")):
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    on = doc.get(True, doc.get("on"))
    push = on.get("push") if isinstance(on, dict) else None
    if not isinstance(push, dict):
        continue
    for b in push.get("branches") or []:
        if b.startswith("claude/"):
            paths = push.get("paths")
            lanes[b] = (path.name, {p.split("/")[0] for p in paths} if paths else None)
check(f"skills-evals listens on the five routine lanes ({sorted(lanes)})", set(lanes) == {
    "claude/eval-*", "claude/eval-improve-*", "claude/eval-guidance-improve-*",
    "claude/scaffold-*", "claude/draft-skill-*"})

BRANCH = re.compile(r"claude/[a-z][a-z-]*-")
prompt = (SE / "docs" / "routine-prompt.md").read_text(encoding="utf-8")
named_in_prompt = set(BRANCH.findall(prompt))
check("the routine prompt names its branches", bool(named_in_prompt))
for doc in sorted((SE / "docs").glob("*.md")):
    rel = doc.relative_to(KIT)
    for token in sorted(set(BRANCH.findall(doc.read_text(encoding="utf-8")))):
        sample = token + "run"
        hits = [p for p in lanes if fnmatch.fnmatchcase(sample, p)]
        check(f"{rel}: {token}<run> is a branch some lane listens on", bool(hits))
        if len(hits) > 1:
            dirs = [lanes[p][1] for p in hits]
            check(f"{rel}: {token}<run> matches {sorted(hits)}, and each has a paths filter", all(d for d in dirs))
            if all(dirs):
                seen = set()
                for d in dirs:
                    check(f"{rel}: the lanes {token}<run> matches write disjoint directories ({sorted(seen & d)})",
                          not (seen & d))
                    seen |= d
for pattern in sorted(lanes):
    check(f"the routine prompt pushes to {pattern} ({lanes[pattern][0]} listens)",
          any(fnmatch.fnmatchcase(t + "run", pattern) for t in named_in_prompt))

loop_doc = (KIT / "docs" / "evals-and-loop.md").read_text(encoding="utf-8")
for pattern in sorted(lanes):
    check(f"docs/evals-and-loop.md names the lane `{pattern}`", f"`{pattern}`" in loop_doc)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
