#!/usr/bin/env python3
"""check-guide-lists.py: the kit's hand-kept lists cover what the exports ship.

    check-guide-lists.py <kit root>

1. Every exported workflow that runs on pull_request_target (parsed with
   PyYAML) is allowed for its repo by an actions_policies entry in
   repo-settings' fleet.yml (GitHub blocks that event on public repos unless
   a policy allows it, and the engine is what applies the policy), and is
   named with its repo in docs/settings-and-apps.md. A workflow that also
   has another trigger is fine, as long as it does not rely on
   pull_request_target alone.
2. Every item of export/REGENERATE.md (a "- **...**" lead under a "## `repo`"
   heading) has a row in docs/generated-files.md's table, "| repo | item |
   step |", and each step named there is a step of bootstrap/steps.json.
   A new item in REGENERATE.md without a step fails here.
3. Every environment and App in lib/common.sh's registries is named in
   docs/settings-and-apps.md.

Prints "N assertions passed" and exits 1 on any failure.
"""
import json
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(sys.argv[1])
STARTER = ROOT / "starter"
B = ROOT / "bootstrap"
SETTINGS_DOC = (ROOT / "docs" / "settings-and-apps.md").read_text(encoding="utf-8")
GEN_DOC = (ROOT / "docs" / "generated-files.md").read_text(encoding="utf-8")
REGEN = (ROOT / "export" / "REGENERATE.md").read_text(encoding="utf-8")
STEPS = {s["id"] for s in json.loads((B / "steps.json").read_text(encoding="utf-8"))["steps"]}
passed = failed = 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def bash(expr):
    return subprocess.run(["bash", "-c", f'. "$1/lib/common.sh"; {expr}', "_", str(B)],
                          capture_output=True, text=True, check=True).stdout


FLEET = bash('printf "%s\\n" "${FLEET_REPOS[@]}"').split()
TREE = {r: bash(f"kit_tree {r}").strip() for r in FLEET}

# 1. pull_request_target
fleet = yaml.safe_load((STARTER / "repo-settings" / "repo-settings" / "fleet.yml").read_text(encoding="utf-8"))
allowed = {}
for entry in fleet.get("repos") or []:
    repo = str(entry.get("name", "")).split("/", 1)[-1]
    for pol in ((entry.get("overrides") or {}).get("actions_policies") or []):
        if "pull_request_target" in (pol.get("allowed_events") or []):
            for p in ((pol.get("workflow_paths") or {}).get("include") or []):
                allowed.setdefault(repo, set()).add(p)
found = 0
for repo in FLEET:
    for f in sorted((STARTER / TREE[repo]).glob(".github/workflows/*.yml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        on = doc.get(True, doc.get("on")) or {}
        triggers = set(on) if isinstance(on, (dict, list)) else {on}
        if "pull_request_target" not in triggers:
            continue
        rel = f".github/workflows/{f.name}"
        if triggers == {"pull_request_target"}:
            found += 1
            check(f"fleet.yml allows pull_request_target for {repo}: {rel}", rel in allowed.get(repo, set()))
            check(f"docs/settings-and-apps.md names {repo}'s {rel}",
                  any(f"`{rel}`" in line and repo in line for line in SETTINGS_DOC.splitlines()))
check("the scan found the exported pull_request_target-only workflows", found >= 1)

# 2. REGENERATE.md items each have a step
rows = {}
for line in GEN_DOC.splitlines():
    m = re.fullmatch(r"\| `([^`]+)` \| (.+?) \| ([0-9, ]+) \|", line.strip())
    if m:
        rows[(m.group(1), m.group(2).strip())] = [s.strip() for s in m.group(3).split(",")]
items = []
repo = None
for line in REGEN.splitlines():
    h = re.match(r"## (.+)", line)
    if h:
        repo = h.group(1)
        continue
    m = re.match(r"- \*\*(.+?)\*\*", line)
    if m and repo:
        for r in re.findall(r"`([^`]+)`", repo):
            items.append((r, m.group(1)))
check(f"REGENERATE.md lists items ({len(items)})", len(items) >= 15)
for r, item in items:
    steps = rows.get((r, item))
    check(f"docs/generated-files.md has a row for {r}: {item}", steps is not None)
    for s in steps or []:
        check(f"docs/generated-files.md: {r}: {item} names step {s}, a step in steps.json", s in STEPS)

# 3. registries named in the docs
for line in bash('printf "%s\\n" "${ENVIRONMENTS[@]}"').splitlines():
    repo, env = line.split("|")[:2]
    check(f"docs/settings-and-apps.md names environment {env}", f"`{env}`" in SETTINGS_DOC)
for line in bash('printf "%s\\n" "${APPS[@]}"').splitlines():
    app = line.split("|")[0]
    check(f"docs/settings-and-apps.md names App {app}", f"`{app}`" in SETTINGS_DOC)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
