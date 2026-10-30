#!/usr/bin/env python3
"""check-catalog.py: bootstrap/content-catalog.json matches the kit's library/ and starter/.

    check-catalog.py <kit root>

1. Every item has a unique id, a kind (skill, guidance, git-hook), a tier
   (core, recommended, opinionated), a title, a short sentence and a
   source_link that is a file in the kit. A recommended guidance item has a
   group (safety or habits); an opinionated item has details and clashes.
2. Every recommended or opinionated item has library_path
   library/<kind folder>/<id>, which exists; a core item has none. Every
   folder under library/<kind folder>/ is an item of that kind.
3. Every core item is in starter/: a skill's SKILL.md in a registry plugin,
   a guidance section's row in eval-coverage.yml and its heading in base.md,
   a git-hook check's id in the agent-checks runner.
4. starter/skills-evals/evals/retirement-exempt.yml names exactly the core
   skills and the core guidance sections.
5. The /adopt skill's questions fit AskUserQuestion: at most 4 items in each
   list it asks (recommended skills, each recommended guidance group, each
   opinionated kind).

Prints "N assertions passed" and exits 1 on any failure. Standard library only.
"""
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(sys.argv[1]).resolve()
CATALOG = json.loads((ROOT / "bootstrap" / "content-catalog.json").read_text(encoding="utf-8"))["items"]
KIND_DIRS = {"skill": "skills", "guidance": "guidance", "git-hook": "git-hooks"}
TIERS = {"core", "recommended", "opinionated"}
S = ROOT / "starter"
passed = failed = 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def text(p):
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def yaml_list(src, key):
    """The `- name` lines under a top-level `key:` of a plain YAML file."""
    out, on = [], False
    for line in src.splitlines():
        if line.startswith(key + ":"):
            on = True
        elif line and not line.startswith((" ", "#")):
            on = False
        elif on and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
    return sorted(out)


ids = [i.get("id") for i in CATALOG]
check(f"catalog ids are unique: {[k for k, n in Counter(ids).items() if n > 1]}", len(ids) == len(set(ids)))
coverage = text(S / "agent-guidance" / "agents-md" / "eval-coverage.yml")
base = text(S / "agent-guidance" / "agents-md" / "base.md")
runner = text(S / "agent-guidance" / "templates" / "agent-checks" / "agent-checks")
for i in CATALOG:
    iid, kind, tier = i.get("id"), i.get("kind"), i.get("tier")
    for f in ("id", "title", "short", "source_link"):
        check(f"{iid}: {f} is a non-empty string", isinstance(i.get(f), str) and i.get(f) != "")
    check(f"{iid}: kind is one of {sorted(KIND_DIRS)}", kind in KIND_DIRS)
    check(f"{iid}: tier is one of {sorted(TIERS)}", tier in TIERS)
    check(f"{iid}: source_link {i.get('source_link')} is a file in the kit", (ROOT / str(i.get("source_link"))).is_file())
    if tier == "recommended" and kind == "guidance":
        check(f"{iid}: a recommended guidance item has group safety or habits", i.get("group") in ("safety", "habits"))
    if tier == "opinionated":
        for f in ("details", "clashes"):
            check(f"{iid}: an opinionated item has {f}", isinstance(i.get(f), str) and i.get(f) != "")
    if tier == "core":
        check(f"{iid}: a core item has no library_path", "library_path" not in i)
        if kind == "skill":
            check(f"{iid}: the core skill is in starter/agent-skills",
                  any((S / "agent-skills" / "plugins").glob(f"*/skills/{iid}/SKILL.md")))
        elif kind == "guidance":
            check(f"{iid}: the core section has its eval-coverage row", f"\n- id: {iid}\n" in coverage)
            check(f"{iid}: the core section is in base.md", f"\n## {i.get('title')}\n" in base)
        elif kind == "git-hook":
            check(f"{iid}: the core check is in the agent-checks runner", f"problem {iid} " in runner)
    elif kind in KIND_DIRS:
        want = f"library/{KIND_DIRS[kind]}/{iid}"
        check(f"{iid}: library_path is {want}", i.get("library_path") == want)
        check(f"{iid}: {want}/ exists", (ROOT / want).is_dir())

for kind, d in KIND_DIRS.items():
    listed = {i["id"] for i in CATALOG if i.get("kind") == kind and i.get("tier") != "core"}
    present = {p.name for p in (ROOT / "library" / d).iterdir() if p.is_dir()} if (ROOT / "library" / d).is_dir() else set()
    check(f"library/{d}/ holds exactly the catalog's optional {kind} items (extra: {sorted(present - listed)}, "
          f"missing: {sorted(listed - present)})", present == listed)

exempt = text(S / "skills-evals" / "evals" / "retirement-exempt.yml")
for kind, key in (("skill", "skills"), ("guidance", "guidance")):
    core = sorted(i["id"] for i in CATALOG if i.get("kind") == kind and i.get("tier") == "core")
    check(f"retirement-exempt.yml {key} are the core {kind} items", yaml_list(exempt, key) == core)

lists = Counter()
for i in CATALOG:
    if i.get("tier") == "recommended":
        lists[("recommended", i.get("kind"), i.get("group"))] += 1
    elif i.get("tier") == "opinionated":
        lists[("opinionated", i.get("kind"))] += 1
for key, n in sorted(lists.items(), key=str):
    check(f"the /adopt question for {key} has at most 4 items ({n})", n <= 4)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
