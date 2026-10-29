#!/usr/bin/env python3
"""check-steps.py: bootstrap/steps.json is a well-formed list of the guide's steps.

    check-steps.py <kit root>

1. Every step has the required fields with the right types: a unique id, a
   title, run text, a kind (script, browser, machine or manual), a phase
   from 0 to 10, a section, a link, a check and minutes > 0.
2. Every check is config, verify (with groups), files (with repo and path)
   or ask; step 0 is the config check; a script step is never an ask.
3. Every script a step runs (the first word of a `code` span, or the one
   after `bash`) exists in the kit, and every starter/<folder> it names is a
   folder there. Every file a files check names exists in that starter
   folder, so a merged starter pull request is what puts it there.
4. Every link is README.md#<anchor> for a real heading (GitHub's anchor
   rules, duplicates numbered), or an https URL the guide already names.

Prints "N assertions passed" and exits 1 on any failure. Standard library only.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1])
GUIDE = (ROOT / "README.md").read_text(encoding="utf-8")
STEPS = json.loads((ROOT / "bootstrap" / "steps.json").read_text(encoding="utf-8"))["steps"]
REPOS = {"agent-guidance", "agent-skills", "agent-skills-private", "skills-evals", "repo-settings"}
KINDS = {"script", "browser", "machine", "manual"}
passed = failed = 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def anchors(text):
    """The anchors GitHub gives the headings outside fenced code blocks."""
    seen, out, fenced = {}, set(), False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"#{1,6} (.+)", line)
        if not m:
            continue
        slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


ANCHORS = anchors(GUIDE)
check("the guide's anchors were found", "quick-start" in ANCHORS and "the-config-file" in ANCHORS)
check("steps.json lists steps", len(STEPS) > 0)

ids = [s.get("id") for s in STEPS]
check(f"step ids are unique: {ids}", len(ids) == len(set(ids)))
check("the first step is 0", ids[:1] == ["0"])
for s in STEPS:
    sid = s.get("id")
    for field, kind in (("id", str), ("title", str), ("run", str), ("kind", str), ("phase", int),
                        ("section", str), ("link", str), ("check", dict), ("minutes", int)):
        check(f"step {sid}: {field} is a {kind.__name__}", isinstance(s.get(field), kind) and s.get(field) != "")
    check(f"step {sid}: id is a number with an optional letter", re.fullmatch(r"\d+[a-z]?", str(sid)) is not None)
    check(f"step {sid}: kind is one of {sorted(KINDS)}", s.get("kind") in KINDS)
    check(f"step {sid}: phase is 0 to 10", isinstance(s.get("phase"), int) and 0 <= s["phase"] <= 10)
    check(f"step {sid}: minutes > 0", isinstance(s.get("minutes"), int) and s["minutes"] > 0)

    c = s.get("check") or {}
    t = c.get("type")
    check(f"step {sid}: check type is config, verify, files or ask", t in ("config", "verify", "files", "ask"))
    check(f"step {sid}: only step 0 is the config check", (t == "config") == (sid == "0"))
    if t == "verify":
        check(f"step {sid}: verify names groups", isinstance(c.get("groups"), list) and len(c["groups"]) > 0
              and all(isinstance(g, str) and g for g in c["groups"]))
    if t == "files":
        files = c.get("files") if isinstance(c.get("files"), list) else []
        check(f"step {sid}: files names at least one file", len(files) > 0)
        for f in files:
            repo, path = f.get("repo"), f.get("path", "")
            check(f"step {sid}: {repo} is a fleet repo", repo in REPOS)
            check(f"step {sid}: starter/{repo}/{path} exists", bool(path) and (ROOT / "starter" / str(repo) / path).is_file())
    if s.get("kind") == "script":
        check(f"step {sid}: a script step is checked against the real state, not asked", t in ("verify", "files"))

    # 3. what it runs exists
    for span in re.findall(r"`([^`]+)`", s.get("run", "")):
        words = span.split()
        cmd = words[1] if words[0] == "bash" and len(words) > 1 else words[0]
        if re.fullmatch(r"(bootstrap|starter)/\S+\.(sh|py)", cmd) and not cmd.endswith("cloud-setup-script.sh"):
            check(f"step {sid}: {cmd} exists", (ROOT / cmd).is_file())
        for w in words:
            if re.fullmatch(r"starter/[\w-]+", w):
                check(f"step {sid}: {w}/ is a starter folder", (ROOT / w).is_dir())

    # 4. the link resolves
    link = s.get("link", "")
    if link.startswith("https://"):
        check(f"step {sid}: {link} is a page the guide names", link in GUIDE)
    else:
        m = re.fullmatch(r"README\.md#([\w-]+)", link)
        check(f"step {sid}: {link} is README.md#<anchor> for a real heading", m is not None and m.group(1) in ANCHORS)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
