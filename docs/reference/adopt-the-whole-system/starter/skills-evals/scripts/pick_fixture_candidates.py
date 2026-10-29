#!/usr/bin/env python3
"""pick_fixture_candidates.py: rank merged pull requests as fixture candidates.

    pick_fixture_candidates.py --prs prs.json --evidence evidence.json [--limit 3]

prs.json: public metadata only, from the REST API:
  [{"repo": "<you>/app", "number": 12, "merged_at": "2026-10-27T10:00:00Z",
    "files": ["src/a.py", ".github/workflows/ci.yml"]}]
evidence.json: {"skills": {"<skill>": ["<regex for paths that are evidence>"]},
                "used": ["<you>/app#9"]}     # already turned into drafts
A PR is evidence for the skill whose path patterns match the most of its
files. PRs touching more than 25 files are skipped (too large to be a fixture).
Order: more matching files, then fewer files in total, then newer, then number.
Prints a JSON list of at most --limit {repo, number, skill, matched, total}.
"""
import argparse
import json
import re
import sys

MAX_FILES = 25


def rank(prs, skills, used, limit=3):
    pats = {s: [re.compile(p) for p in ps] for s, ps in skills.items()}
    out = []
    for pr in prs:
        ref = f"{pr['repo']}#{pr['number']}"
        files = pr.get("files", [])
        if ref in used or not files or len(files) > MAX_FILES:
            continue
        best = None
        for skill in sorted(pats):
            n = sum(1 for f in files if any(p.search(f) for p in pats[skill]))
            if n and (best is None or n > best[1]):
                best = (skill, n)
        if best:
            out.append({"repo": pr["repo"], "number": pr["number"], "skill": best[0],
                        "matched": best[1], "total": len(files), "merged_at": pr.get("merged_at", "")})
    out.sort(key=lambda r: r["number"])   # stable passes: last key is the main one
    out.sort(key=lambda r: r["merged_at"], reverse=True)
    out.sort(key=lambda r: (-r["matched"], r["total"]))
    return [{k: v for k, v in r.items() if k != "merged_at"} for r in out[:limit]]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prs", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--limit", type=int, default=3)
    a = ap.parse_args(argv)
    with open(a.prs, encoding="utf-8") as f:
        prs = json.load(f)
    with open(a.evidence, encoding="utf-8") as f:
        ev = json.load(f)
    json.dump(rank(prs, ev["skills"], set(ev.get("used", [])), a.limit), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
