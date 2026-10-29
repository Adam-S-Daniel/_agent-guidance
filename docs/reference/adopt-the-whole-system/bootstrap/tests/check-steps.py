#!/usr/bin/env python3
"""check-steps.py: bootstrap/steps.json is a well-formed list of the quick start's steps.

    check-steps.py <kit root>

1. Every step has the required fields with the right types: a unique id, a
   title, run text, a kind (script, browser, machine or manual), a part, a
   link, a check and minutes > 0; `optional`, when present, is a boolean.
2. Every check is config, verify (with groups verify.sh knows), files (with
   a fleet repo and a path) or ask; step 0 is the config check; a script
   step is never an ask.
3. Every script a step runs (the first word of a `code` span, or the one
   after `bash`) exists in the kit, and every starter/<folder> it names is a
   folder there. Every file a files check names exists in that repo's
   starter tree (lib/common.sh's kit_tree), so a merged starter pull request
   is what puts it there.
4. Every link is docs/<page>.md#<anchor> for a real heading of that page
   (GitHub's anchor rules, duplicates numbered), or an https URL.

Prints "N assertions passed" and exits 1 on any failure. Standard library only.
"""
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mdlinks  # noqa: E402

ROOT = pathlib.Path(sys.argv[1]).resolve()
B = ROOT / "bootstrap"
STEPS = json.loads((B / "steps.json").read_text(encoding="utf-8"))["steps"]
KINDS = {"script", "browser", "machine", "manual"}
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
APPS = bash('for e in "${APPS[@]}"; do printf "%s\\n" "${e%%|*}"; done').split()
VALUE_GROUPS = set(bash('for e in "${OTHER_VALUES[@]}"; do IFS="|" read -r _ _ _ n _ <<<"$e"; value_group "$n"; done').split())
TASKS = bash('printf "%s\\n" "${REGEN_TASKS[@]}"').split()
check(f"the registries were read: {FLEET}", len(FLEET) == 6 and all(TREE.values()) and APPS and VALUE_GROUPS and TASKS)


def group_known(g):
    kind, _, arg = g.partition(":")
    if not arg:
        return g in {"repos", "labels", "branches", "environments", "variables", "federation", "settings"}
    return {"apps": arg in APPS, "installs": arg in APPS, "values": arg in VALUE_GROUPS,
            "regen": arg in TASKS}.get(kind, False)


check("steps.json lists steps", len(STEPS) > 0)
ids = [s.get("id") for s in STEPS]
check(f"step ids are unique: {ids}", len(ids) == len(set(ids)))
check("the first step is 0", ids[:1] == ["0"])
for s in STEPS:
    sid = s.get("id")
    for field, kind in (("id", str), ("title", str), ("run", str), ("kind", str), ("part", str),
                        ("link", str), ("check", dict), ("minutes", int)):
        check(f"step {sid}: {field} is a {kind.__name__}", isinstance(s.get(field), kind) and s.get(field) != "")
    check(f"step {sid}: id is a number", re.fullmatch(r"\d+", str(sid)) is not None)
    check(f"step {sid}: kind is one of {sorted(KINDS)}", s.get("kind") in KINDS)
    check(f"step {sid}: minutes > 0", isinstance(s.get("minutes"), int) and s["minutes"] > 0)
    check(f"step {sid}: optional, if present, is a boolean", isinstance(s.get("optional", False), bool))
    check(f"step {sid}: no fields from the old guide", "phase" not in s and "section" not in s)

    c = s.get("check") or {}
    t = c.get("type")
    check(f"step {sid}: check type is config, verify, files or ask", t in ("config", "verify", "files", "ask"))
    check(f"step {sid}: only step 0 is the config check", (t == "config") == (sid == "0"))
    if t == "verify":
        groups = c.get("groups")
        check(f"step {sid}: verify names groups", isinstance(groups, list) and len(groups) > 0)
        for g in groups if isinstance(groups, list) else []:
            check(f"step {sid}: verify group {g!r} is one verify.sh knows", isinstance(g, str) and group_known(g))
    if t == "files":
        files = c.get("files") if isinstance(c.get("files"), list) else []
        check(f"step {sid}: files names at least one file", len(files) > 0)
        for f in files:
            repo, path = f.get("repo"), f.get("path", "")
            check(f"step {sid}: {repo} is a fleet repo", repo in TREE)
            if repo in TREE:
                check(f"step {sid}: starter/{TREE[repo]}/{path} exists",
                      bool(path) and (ROOT / "starter" / TREE[repo] / path).is_file())
    if s.get("kind") == "script":
        check(f"step {sid}: a script step is checked against the real state, not asked", t in ("verify", "files"))

    # 3. what it runs exists
    for span in re.findall(r"`([^`]+)`", s.get("run", "")):
        words = span.split()
        cmd = words[1] if words[0] == "bash" and len(words) > 1 else words[0]
        if re.fullmatch(r"(bootstrap|starter)/\S+\.(sh|py)", cmd):
            check(f"step {sid}: {cmd} exists", (ROOT / cmd).is_file())
        for w in words:
            if re.fullmatch(r"starter/[\w-]+", w):
                check(f"step {sid}: {w}/ is a starter folder", (ROOT / w).is_dir())

    # 4. the link resolves
    link = s.get("link", "")
    if link.startswith("https://"):
        check(f"step {sid}: {link} is an https URL with a host", re.fullmatch(r"https://[\w.-]+(/\S*)?", link) is not None)
    else:
        m = re.fullmatch(r"docs/[\w-]+\.md#[\w-]+", link)
        why = mdlinks.resolve(ROOT / "README.md", link) if m else "not docs/<page>.md#<anchor>"
        check(f"step {sid}: {link}: {why}", why is None)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
