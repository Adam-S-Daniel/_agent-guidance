#!/usr/bin/env python3
"""test_memory_home.py: tests for memory-home.py, the user-level Stop hook.
Each case builds a scratch project directory (a transcript beside a memory/
directory), feeds the hook a Stop event on stdin and reads its decision. No
clock is read here: an "old" note gets mtime 0 (1970). Run it directly; it
prints "N assertions passed" and exits 1 on any failure."""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory-home.py")
passed = failed = 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def note(memory, name, front, old=False):
    path = os.path.join(memory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(front)
    if old:
        os.utime(path, (0, 0))
    return path


def run(project, event=None):
    ev = {"transcript_path": os.path.join(project, "session.jsonl")} if event is None else event
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None)


try:
    import yaml  # noqa: F401  the hook needs it; without it, it does nothing by design
except ImportError:
    print("FAIL: PyYAML is missing; memory-home.py does nothing without it")
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    project = os.path.join(tmp, "projects", "-home-user-app")
    memory = os.path.join(project, "memory")
    os.makedirs(memory)

    rc, out = run(project)
    check("no notes: the stop goes ahead", rc == 0 and out is None)

    homeless = note(memory, "results-branch.md",
                    "---\nname: results-branch\ndescription: d\nmetadata:\n  type: project\n---\nA fact.\n")
    rc, out = run(project)
    check("a fresh note without metadata.home blocks the stop", rc == 0 and out and out["decision"] == "block")
    check("the reason names the note", out and homeless in out["reason"])

    rc, out = run(project, {"transcript_path": os.path.join(project, "session.jsonl"), "stop_hook_active": True})
    check("it blocks at most once per turn (stop_hook_active)", out is None)

    note(memory, "results-branch.md", "---\nname: results-branch\nmetadata:\n  type: project\n"
         "  home: example-owner/skills-evals:docs/decisions/0001-results-branch.md\n---\nPointer.\n")
    rc, out = run(project)
    check("a note with metadata.home passes", out is None)

    note(memory, "about-me.md", "---\nname: about-me\nmetadata:\n  type: user\n---\nPrefers short replies.\n")
    check("a note about the person (type: user) is exempt", run(project)[1] is None)

    note(memory, "top-level.md", "---\nname: top-level\ntype: project\nhome: example-owner/app:AGENTS.md\n---\nx\n")
    check("a top-level home is accepted too", run(project)[1] is None)

    note(memory, "MEMORY.md", "- [results-branch](results-branch.md)\n")
    check("the MEMORY.md index is not a note", run(project)[1] is None)

    note(memory, "stale.md", "---\nname: stale\nmetadata:\n  type: project\n---\nOld.\n", old=True)
    check("a note older than a day does not block new work", run(project)[1] is None)

    broken = note(memory, "broken.md", "---\n: : not yaml [\n---\nx\n")
    rc, out = run(project)
    check("a note whose frontmatter does not parse counts as having no home", out and broken in out["reason"])
    os.remove(broken)

    other = os.path.join(tmp, "projects", "-home-user-other", "memory")
    os.makedirs(other)
    note(other, "elsewhere.md", "---\nname: elsewhere\nmetadata:\n  type: project\n---\nx\n")
    check("only this session's project is checked", run(project)[1] is None)

    for bad in ("not json", ""):
        r = subprocess.run([sys.executable, HOOK], input=bad, capture_output=True, text=True)
        check(f"unreadable input {bad!r} lets the stop happen", r.returncode == 0 and not r.stdout.strip())
    check("no transcript_path lets the stop happen", run(project, {})[1] is None)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
