#!/usr/bin/env python3
"""refresh_roster.py: rebuild evals/roster.yml from evals/roster-source.yml.

    refresh_roster.py [--source evals/roster-source.yml] [--roster evals/roster.yml] [--write]

Seats the `seats` models with the largest share (ties by id), in that order,
and the judge, under roster.yml's own leading comment block, which is kept
byte for byte. Prints {"changed": true|false, "arms": [...], "judge": ...};
--write rewrites roster.yml only when it would change. No network: the
source file is the only input (the reference setup fills it from usage data).
Exit 0 ok, 2 a bad source file (the message names the rule, never the text).
"""
import argparse
import json
import re
import sys

import yaml

MODEL = re.compile(r"\Aclaude-[a-z0-9][a-z0-9.-]{0,62}\Z")
TIER = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
WHY = re.compile(r"\A[A-Za-z0-9 %,.()'-]{1,80}\Z")


class BadSource(ValueError):
    pass


def load_source(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        raise BadSource("cannot read the source file") from None
    if not isinstance(doc, dict) or set(doc) != {"seats", "models", "judge"}:
        raise BadSource("the source needs exactly seats, models and judge")
    seats, models, judge = doc["seats"], doc["models"], doc["judge"]
    if type(seats) is not int or not 1 <= seats <= 4:
        raise BadSource("seats must be 1 to 4")
    if not isinstance(judge, str) or not MODEL.match(judge):
        raise BadSource("judge is not a claude model id")
    if not isinstance(models, list) or len(models) < seats:
        raise BadSource("fewer models than seats")
    seen = set()
    for m in models:
        if not isinstance(m, dict) or not {"id", "tier", "share"} <= set(m) <= {"id", "tier", "share", "why"}:
            raise BadSource("a model needs id, tier, share and may have why")
        if not isinstance(m["id"], str) or not MODEL.match(m["id"]) or m["id"] in seen:
            raise BadSource("a model id is bad or repeated")
        seen.add(m["id"])
        if not isinstance(m["tier"], str) or not TIER.match(m["tier"]):
            raise BadSource("a tier is bad")
        if isinstance(m["share"], bool) or not isinstance(m["share"], (int, float)) or not 0 <= m["share"] <= 1:
            raise BadSource("a share is not between 0 and 1")
        if "why" in m and (not isinstance(m["why"], str) or not WHY.match(m["why"])):
            raise BadSource("a why is not short plain text")
    return seats, models, judge


def build(seats, models, judge):
    top = sorted(models, key=lambda m: (-m["share"], m["id"]))[:seats]
    arms = [{"id": m["id"], "tier": m["tier"],
             "why": m.get("why") or f"{round(m['share'] * 100)}% of my recent turns"} for m in top]
    return arms, judge


def render(header, arms, judge):
    lines = ["arms:"]
    for a in arms:
        lines += [f"  - id: {a['id']}", f"    tier: {a['tier']}", f"    why: {a['why']}"]
    lines += ["judge:", f"  id: {judge}"]
    return header + "\n".join(lines) + "\n"


def header_of(text):
    out = []
    for line in text.splitlines(keepends=True):
        if not line.startswith("#"):
            break
        out.append(line)
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default="evals/roster-source.yml")
    ap.add_argument("--roster", default="evals/roster.yml")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    try:
        arms, judge = build(*load_source(a.source))
        with open(a.roster, encoding="utf-8") as f:
            current = f.read()
    except (BadSource, OSError) as e:
        print(f"refresh_roster: {e if isinstance(e, BadSource) else 'cannot read roster.yml'}", file=sys.stderr)
        return 2
    new = render(header_of(current), arms, judge)
    changed = new != current
    if changed and a.write:
        with open(a.roster, "w", encoding="utf-8") as f:
            f.write(new)
    print(json.dumps({"changed": changed, "arms": [x["id"] for x in arms], "judge": judge}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
