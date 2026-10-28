#!/usr/bin/env python3
"""check-guide-lists.py: the guide's hand-kept lists cover what the kit ships.

    check-guide-lists.py <kit root>

1. Every starter workflow that runs on pull_request_target (parsed with
   PyYAML) is named, with its repo, in bootstrap/README.md's manual step 4
   and in the guide's 6.3 table. GitHub turns that trigger off on public
   repos unless you allow it per workflow, so a missing name is a workflow
   that silently stops running.
2. Every starter workflow or template holding the action-pin placeholder
   <sha-of-the-v-tag-you-chose> is named, in a paragraph about pins, in the
   guide section of the phase that copies its repo (and phase 4 for the
   files phase 4 depends on).
3. Wherever the guide, its quick start or bootstrap/README.md runs
   create-apps.sh for roster-writer, the same text runs
   create-environments.sh --with-optional before it: the App's environment
   is optional, and create-apps.sh refuses an App whose environment is missing.

Prints "N assertions passed" and exits 1 on any failure.
"""
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(sys.argv[1])
STARTER = ROOT / "starter"
GUIDE = (ROOT / "README.md").read_text(encoding="utf-8")
BOOT = (ROOT / "bootstrap" / "README.md").read_text(encoding="utf-8")
PLACEHOLDER = "<sha-of-the-v-tag-you-chose>"
REPOS = ["agent-guidance", "agent-skills", "agent-skills-private", "skills-evals", "repo-settings"]
# The guide section ("## N.") of the phase that copies each repo.
COPIED_IN = {"repo-settings": "3", "agent-guidance": "4", "agent-skills": "5",
             "agent-skills-private": "5", "skills-evals": "9"}
# Files phase 4 (section 6) depends on that no sync pull request shows you.
PHASE4 = {("agent-guidance", ".github/workflows/agent-checks.yml"),
          ("agent-skills", ".github/workflows/plugin-runtime-review.yml")}
passed = failed = 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def section(num):
    m = re.search(rf"^## {num}\. .*?(?=^## \d+\. |\Z)", GUIDE, re.M | re.S)
    return m.group(0) if m else ""


def shipped():
    """(repo, path in the repo once delivered, file) for each workflow or template."""
    for repo in REPOS:
        base = STARTER / repo
        for f in sorted(base.glob(".github/workflows/*.yml")):
            yield repo, f".github/workflows/{f.name}", f
        for f in sorted(base.glob("templates/*.yml")):
            yield repo, f"templates/{f.name}", f


# 1. pull_request_target
step4 = re.search(r"^4\. \*\*Allow `pull_request_target`\*\*.*?(?=^\d+\. )", BOOT, re.M | re.S)
step4 = step4.group(0) if step4 else ""
check("bootstrap/README.md has manual step 4 for pull_request_target", step4 != "")
s6 = section("6")
found = set()
for repo, path, f in shipped():
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    on = doc.get(True, doc.get("on")) or {}
    if "pull_request_target" not in on:
        continue
    # A template is delivered by the sync as .github/workflows/<name>; the kit's
    # one such template is agent-checks.yml, which agent-guidance also copies.
    dest = path.replace("templates/", ".github/workflows/")
    found.add((repo, dest))
    want = f"`{dest}`"
    check(f"bootstrap/README.md step 4 names {repo}: {dest}",
          any(want in line and repo in line for line in step4.splitlines()))
    check(f"guide 6.3 table names {repo}: {dest}",
          any(line.startswith("|") and want in line and repo in line for line in s6.splitlines()))
check("the scan found the kit's pull_request_target workflows", len(found) >= 4)

# 2. the action-pin placeholder
for repo, path, f in shipped():
    if PLACEHOLDER not in f.read_text(encoding="utf-8"):
        continue
    sections = [COPIED_IN[repo]] + (["6"] if (repo, path) in PHASE4 else [])
    for num in sections:
        paras = re.split(r"\n\s*\n", section(num))
        check(f"guide section {num} names {repo}'s {path} where it says to fill the pins",
              any(f"`{path}`" in p and "pin" in p for p in paras))

# 3. roster-writer's environment comes first
for name, text in (("README.md", GUIDE), ("bootstrap/README.md", BOOT)):
    # Each table row, list item or fenced block is one unit of instructions.
    units = re.split(r"\n(?=\| )|\n\s*\n", text)
    hits = 0
    for u in units:
        m = re.search(r"create-apps\.sh --with-optional roster-writer", u)
        if not m:
            continue
        hits += 1
        e = u.find("create-environments.sh --with-optional")
        check(f"{name}: create-environments.sh --with-optional comes before roster-writer's create-apps.sh in: {u[:60]!r}",
              0 <= e < m.start())
    check(f"{name} says how to create roster-writer", hits > 0)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
