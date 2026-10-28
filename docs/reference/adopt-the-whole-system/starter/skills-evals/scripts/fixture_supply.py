#!/usr/bin/env python3
"""fixture_supply.py: the file halves of fixture-supply.yml.

    fixture_supply.py prepare --sources evals/fixture-sources.yml
                              --used evals/scaffold-sources.json --out DIR
        Validates the sources file and writes DIR/repos.txt (one repo a line),
        DIR/evidence.json (pick_fixture_candidates.py's --evidence: the
        skills' path patterns, and "used": the pull requests already
        scaffolded) and DIR/limit.txt (per_day).

    fixture_supply.py plan --picks picks.json
        pick_fixture_candidates.py's output as dispatch_fires.py's plan:
        {"fires": [{"mode": "scaffold", "repo", "number"}]}.

No network. A repo still named <you>/... is refused: fill the file first.
Exit 0 ok, 2 bad input (the message names the rule, never the text).
"""
import argparse
import json
import os
import re
import sys

import yaml

REPO = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}\Z")
SKILL = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")


class BadInput(ValueError):
    pass


def load_sources(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        raise BadInput("cannot read the sources file") from None
    if not isinstance(doc, dict) or set(doc) != {"repos", "skills", "per_day"}:
        raise BadInput("the sources file needs exactly repos, skills and per_day")
    repos, skills, per_day = doc["repos"], doc["skills"], doc["per_day"]
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) and REPO.match(r) for r in repos):
        raise BadInput("repos must be a list of <owner>/<repo> (replace <you>)")
    if not isinstance(skills, dict) or not skills:
        raise BadInput("skills must map skill names to path patterns")
    for name, pats in skills.items():
        if not isinstance(name, str) or not SKILL.match(name) or not isinstance(pats, list) or not pats:
            raise BadInput("each skill needs a list of path patterns")
        for p in pats:
            try:
                re.compile(p)
            except (re.error, TypeError):
                raise BadInput("a path pattern is not a regular expression") from None
    if type(per_day) is not int or not 1 <= per_day <= 3:
        raise BadInput("per_day must be 1 to 3")
    return sorted(set(repos)), skills, per_day


def load_used(path):
    try:
        with open(path, encoding="utf-8") as f:
            used = json.load(f)
    except (OSError, ValueError):
        raise BadInput("cannot read the used sources file") from None
    if not isinstance(used, list) or not all(isinstance(u, str) for u in used):
        raise BadInput("the used sources file must be a list of strings")
    return used


def plan(picks):
    if not isinstance(picks, list):
        raise BadInput("picks must be a list")
    fires = []
    for p in picks:
        if not isinstance(p, dict) or not isinstance(p.get("repo"), str) or not REPO.match(p["repo"]) \
                or type(p.get("number")) is not int or p["number"] < 1:
            raise BadInput("a pick needs repo and number")
        fires.append({"mode": "scaffold", "repo": p["repo"], "number": p["number"]})
    return {"fires": fires}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prepare")
    pr.add_argument("--sources", default="evals/fixture-sources.yml")
    pr.add_argument("--used", default="evals/scaffold-sources.json")
    pr.add_argument("--out", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--picks", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            repos, skills, per_day = load_sources(a.sources)
            used = load_used(a.used)
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, "repos.txt"), "w", encoding="utf-8") as f:
                f.write("".join(r + "\n" for r in repos))
            with open(os.path.join(a.out, "evidence.json"), "w", encoding="utf-8") as f:
                json.dump({"skills": skills, "used": used}, f, indent=2)
            with open(os.path.join(a.out, "limit.txt"), "w", encoding="utf-8") as f:
                f.write(f"{per_day}\n")
            return 0
        try:
            with open(a.picks, encoding="utf-8") as f:
                picks = json.load(f)
        except (OSError, ValueError):
            raise BadInput("cannot read the picks file") from None
        json.dump(plan(picks), sys.stdout, indent=2)
        print()
        return 0
    except BadInput as e:
        print(f"fixture_supply: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
