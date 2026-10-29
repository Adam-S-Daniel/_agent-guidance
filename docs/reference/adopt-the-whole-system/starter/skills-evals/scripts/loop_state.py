#!/usr/bin/env python3
"""loop_state.py: the small inputs the loop workflows build from files.

    loop_state.py cells --scheduled evals/scheduled.yml --roster evals/roster.yml
        The gate roster for compute_gates.py: every "scheduled" fixture times
        every roster arm, as [{"skill", "fixture": "evals/<skill>/<name>", "model"}].

    loop_state.py state --results DIR --scheduled ... --roster ... --evals-dir evals
                        --open-prs prs.json --now ISO [--usage usage.json]
        plan_week.py's state.json. Cells are the same scheduled fixtures and
        seats, with "fixture" as the short name plan_week.py and
        dispatch_fires.py use; "runs" counts paired with/without runs on the
        results branch, "last_run" is the newest one's date, "bare_done" says
        a bare arm ran. A skill gets a "skills" row only once it has a paired
        run (its pass_rate is the mean with-skill pass rate; with no evidence
        there is nothing to improve against). "fixtures" counts the fixture
        directories under evals/<skill>/. prs.json is `gh pr list --json
        headRefName` of the registry: any open loop pull request pauses every
        improve fire (the improve gate opens one at a time), and each
        retire-skill/<skill> one puts <skill> in band 1. usage.json is an
        optional {"<skill>": <uses in four weeks>}; without it every skill
        counts 0. Improve rejections are not on the results branch, so
        last_rejection, consecutive_rejections and changed_since_rejection
        keep plan_week.py's defaults.

    loop_state.py merge-mode [--policy evals/improve-policy.yml]
        Prints auto or draft (improve_merge_mode, read by improve_gate.py's
        strict loader). The scaffold and removal lanes follow it too.

    loop_state.py retire-candidate --status status.json
        Prints the first skill (by name) whose verdict is "retire", or nothing.

Reads files only; no network, no clock (--now is a flag). Exit 0 ok, 2 bad input.
"""
import argparse
import json
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from harness.results import ResultsError, load_arms, pair_runs  # noqa: E402

FIXTURE = re.compile(r"\Aevals/([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9-]*)\Z")
MODEL = re.compile(r"\Aclaude-[a-z0-9][a-z0-9.-]{0,62}\Z")
SKILL = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
LOOP_BRANCH = re.compile(r"\A(eval-improve|eval-scaffold|eval-draft|retire-skill|eval-guidance-improve)/")
RUN_DATE = re.compile(r"\A([0-9]{4})([0-9]{2})([0-9]{2})T")


class BadInput(ValueError):
    pass


def _yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        raise BadInput(f"cannot read {os.path.basename(path)}") from None
    if not isinstance(doc, dict):
        raise BadInput(f"{os.path.basename(path)} is not a mapping")
    return doc


def scheduled_fixtures(path):
    rows = _yaml(path).get("fixtures")
    if not isinstance(rows, list):
        raise BadInput("scheduled.yml has no fixtures list")
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in ("scheduled", "draft"):
            raise BadInput("bad scheduled.yml row")
        m = FIXTURE.match(str(row.get("fixture", "")))
        if not m:
            raise BadInput("bad fixture path in scheduled.yml")
        if row["status"] == "scheduled":
            out.append((m.group(1), m.group(2)))
    return out


def roster_models(path):
    arms = _yaml(path).get("arms")
    if not isinstance(arms, list) or not arms:
        raise BadInput("roster.yml has no arms")
    models = []
    for a in arms:
        mid = a.get("id") if isinstance(a, dict) else None
        if not isinstance(mid, str) or not MODEL.match(mid):
            raise BadInput("bad model id in roster.yml")
        models.append(mid)
    return models


def cells(scheduled, roster):
    return [{"skill": s, "fixture": f"evals/{s}/{f}", "model": m}
            for s, f in scheduled_fixtures(scheduled) for m in roster_models(roster)]


def _date(run_id):
    y, mo, d = RUN_DATE.match(run_id).groups()
    return f"{y}-{mo}-{d}"


def _fixture_count(evals_dir, skill):
    d = os.path.join(evals_dir, skill)
    try:
        return sum(1 for n in os.listdir(d)
                   if os.path.isfile(os.path.join(d, n, "fixture.yaml")))
    except OSError:
        return 0


def _open_prs(path):
    try:
        with open(path, encoding="utf-8") as f:
            prs = json.load(f)
    except (OSError, ValueError):
        raise BadInput("cannot read the open pull requests file") from None
    if not isinstance(prs, list) or not all(isinstance(p, dict) and isinstance(p.get("headRefName"), str)
                                            for p in prs):
        raise BadInput("open pull requests must be a list of {headRefName}")
    return [p["headRefName"] for p in prs]


def _usage(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        raise BadInput("cannot read the usage file") from None
    if not isinstance(doc, dict) or not all(
            SKILL.match(k) and type(v) is int and v >= 0 for k, v in doc.items()):
        raise BadInput("usage must map skill names to non-negative integers")
    return doc


def state(results, scheduled, roster, evals_dir, open_prs, now, usage=None):
    arms = load_arms(results)
    runs = pair_runs(arms)
    heads = _open_prs(open_prs)
    uses = _usage(usage)
    loop_open = any(LOOP_BRANCH.match(h) for h in heads)
    removal = sorted({h.split("/", 1)[1] for h in heads
                      if h.startswith("retire-skill/") and SKILL.match(h.split("/", 1)[1])})
    out_cells, with_rates = [], {}
    for c in cells(scheduled, roster):
        key = (c["skill"], c["fixture"], c["model"])
        mine = [r for r in runs if (r.skill, r.fixture, r.model) == key]
        bare = any(a.arm == "bare" and a.trials and (a.skill, a.fixture, a.model) == key for a in arms)
        out_cells.append({"skill": c["skill"], "fixture": c["fixture"].rsplit("/", 1)[1],
                          "model": c["model"], "runs": len(mine),
                          "last_run": _date(mine[-1].run_id) if mine else None,
                          "bare_done": bare})
        for r in mine:
            with_rates.setdefault(c["skill"], []).append(r.with_arm.pass_rate)
    skills = [{"skill": s, "uses_4w": uses.get(s, 0), "pass_rate": sum(v) / len(v),
               "fixtures": _fixture_count(evals_dir, s), "open_improve_pr": loop_open,
               "last_rejection": None, "consecutive_rejections": 0,
               "changed_since_rejection": False}
              for s, v in sorted(with_rates.items())]
    return {"now": now, "open_removal_skills": removal, "skills": skills, "cells": out_cells}


def merge_mode(policy):
    import improve_gate
    return improve_gate.load_policy(policy)["improve_merge_mode"]


def retire_candidate(status):
    try:
        with open(status, encoding="utf-8") as f:
            skills = json.load(f)["skills"]
    except (OSError, ValueError, KeyError, TypeError):
        raise BadInput("cannot read the gate status") from None
    if not isinstance(skills, dict):
        raise BadInput("gate status skills is not a mapping")
    for name in sorted(skills):
        if SKILL.match(name) and isinstance(skills[name], dict) and skills[name].get("verdict") == "retire":
            return name
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cells")
    s = sub.add_parser("state")
    for p in (c, s):
        p.add_argument("--scheduled", default="evals/scheduled.yml")
        p.add_argument("--roster", default="evals/roster.yml")
    s.add_argument("--results", required=True)
    s.add_argument("--evals-dir", default="evals")
    s.add_argument("--open-prs", required=True)
    s.add_argument("--now", required=True)
    s.add_argument("--usage")
    m = sub.add_parser("merge-mode")
    m.add_argument("--policy", default="evals/improve-policy.yml")
    r = sub.add_parser("retire-candidate")
    r.add_argument("--status", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "cells":
            out = cells(a.scheduled, a.roster)
        elif a.cmd == "state":
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", a.now):
                raise BadInput("--now must be YYYY-MM-DDTHH:MM:SSZ")
            out = state(a.results, a.scheduled, a.roster, a.evals_dir, a.open_prs, a.now, a.usage)
        elif a.cmd == "merge-mode":
            print(merge_mode(a.policy))
            return 0
        else:
            name = retire_candidate(a.status)
            if name:
                print(name)
            return 0
    except (BadInput, ResultsError, ValueError, OSError) as e:
        print(f"loop_state: {e}", file=sys.stderr)
        return 2
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
