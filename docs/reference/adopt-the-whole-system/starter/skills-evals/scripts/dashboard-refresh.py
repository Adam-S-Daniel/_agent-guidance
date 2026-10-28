#!/usr/bin/env python3
"""dashboard-refresh.py: build the dashboard's small JSON datasets from a local
checkout of the persistent/eval-results branch.

  dashboard-refresh.py --results <checkout> --out <dir>
                       [--weekly-cap 0.30] [--previous-manifest <file>]

Reads   <checkout>/routine-results/<run id>/run.json            (shape: section 9.3)
        <checkout>/routine-results/<run id>/<arm>/summary.json  (one per arm in run.json)
        <checkout>/gates/status.json                            (optional, compute_gates.py's output)
Writes  runs.json       one row per fixture and model: with / without / bare scores
        allowance.json  allowance used per allowance week, against the weekly cap
        gates.json      how many cells are in each gate class, and each skill's verdict
        manifest.json   sha256 of each dataset, and which ones changed since --previous-manifest

The results branch is untrusted data. Every file is size-limited and checked
against a strict shape; a run that fails the check is skipped and only counted.
Numbers keep full precision until the files are written. Nothing reads the
clock: "generated_at" is the newest finished_at in the data. No dataset holds
free text from the input, only values that matched a strict pattern."""
import argparse
import hashlib
import json
import os
import re
import sys

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
FIXTURE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}(/[a-z0-9][a-z0-9._-]{0,63}){1,3}")
SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}(/[a-z0-9][a-z0-9-]{0,63})?")
MODEL = re.compile(r"claude-[a-z0-9][a-z0-9.-]{0,62}")
CLI_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")
# run.json's cli_version is what run_eval.py's cli_version() keeps of
# `claude --version`: "2.1.251 (Claude Code)", or "unknown". The dataset keeps
# only the leading version, or "unknown".
CLI_VERSION_FIELD = re.compile(r"[0-9A-Za-z .()+_-]{1,60}")
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
ARMS = ("with_skill", "without_skill", "bare")
STATUSES = ("ok", "max_turns", "error", "timeout", "budget_stop")
CLASSES = ("insufficient", "cannot-discriminate", "benefit", "ceiling", "no-benefit", "inconclusive")
VERDICTS = ("retire", "keep")
MAX_BYTES = 1_000_000
MAX_TRIALS = 50


def num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def need(cond, why):
    if not cond:
        raise ValueError(why)


def load_json(path):
    need(not os.path.islink(path) and os.path.isfile(path), "not a regular file")
    need(os.path.getsize(path) <= MAX_BYTES, "too large")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_meter(m):
    need(m is None or (isinstance(m, dict) and set(m) == {"seven_day", "resets_at"}), "meter")
    if m is not None:
        need(m["seven_day"] is None or num(m["seven_day"], 0, 1), "seven_day")
        need(m["resets_at"] is None or (isinstance(m["resets_at"], str) and STAMP.fullmatch(m["resets_at"])), "resets_at")
    return m


def check_run(doc, dir_name):
    """Return run.json's fields the dashboard uses, else raise ValueError."""
    need(isinstance(doc, dict), "not an object")
    need(doc.get("schema_version") == 1, "schema_version")
    need(isinstance(doc.get("run_id"), str) and RUN_ID.fullmatch(doc["run_id"]) and doc["run_id"] == dir_name, "run_id")
    need(isinstance(doc.get("fixture"), str) and FIXTURE.fullmatch(doc["fixture"]), "fixture")
    models = doc.get("models")
    need(isinstance(models, dict) and isinstance(models.get("arms"), str) and MODEL.fullmatch(models["arms"]), "model")
    need(isinstance(doc.get("cli_version"), str) and CLI_VERSION_FIELD.fullmatch(doc["cli_version"]), "cli_version")
    words = doc["cli_version"].split()
    cli = words[0] if words and CLI_VERSION.fullmatch(words[0]) else "unknown"
    need(isinstance(doc.get("finished_at"), str) and STAMP.fullmatch(doc["finished_at"]), "finished_at")
    arms = doc.get("arms")
    need(isinstance(arms, list) and arms and len(set(arms)) == len(arms) and set(arms) <= set(ARMS), "arms")
    return {"run_id": doc["run_id"], "fixture": doc["fixture"], "model": models["arms"],
            "cli_version": cli, "finished_at": doc["finished_at"], "arm_names": arms,
            "meter_before": check_meter(doc.get("meter_before")), "meter_after": check_meter(doc.get("meter_after"))}


def check_arm(doc, run, arm):
    """Return {"trials", "judge"} from one arm's summary.json, else raise ValueError."""
    need(isinstance(doc, dict) and doc.get("schema_version") == 1, "schema_version")
    need(doc.get("run_id") == run["run_id"] and doc.get("arm") == arm, "run_id or arm")
    need(doc.get("fixture") == run["fixture"] and doc.get("model") == run["model"], "fixture or model")
    trials = doc.get("trials")
    need(isinstance(trials, list) and len(trials) <= MAX_TRIALS, "trials")
    for t in trials:
        need(isinstance(t, dict) and t.get("status") in STATUSES and isinstance(t.get("errored"), bool), "trial status")
        if t["status"] != "budget_stop" and not t["errored"]:
            need(num(t.get("pass_rate"), 0, 1), "pass_rate")
    judge = doc.get("judge")
    need(judge is None or (isinstance(judge, dict) and num(judge.get("score"), 0, 10)), "judge")
    return {"trials": trials, "judge": None if judge is None else judge["score"]}


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def arm_score(arm):
    """One run's score for one arm. An errored trial counts as zero (it never
    helps an arm); a budget_stop trial was never run and is left out. The judge
    score belongs to the arm."""
    counted = [t for t in arm["trials"] if t["status"] != "budget_stop"]
    if not counted:
        return None
    return {
        "pass_rate": mean([0.0 if t["errored"] else t["pass_rate"] for t in counted]),
        "judge": arm["judge"],
        "errors": sum(1 for t in counted if t["errored"]),
    }


def read_run(d, name):
    """One run directory: run.json plus each of its arms' summary.json."""
    run = check_run(load_json(os.path.join(d, "run.json")), name)
    arms = {}
    for arm in run.pop("arm_names"):
        need(not os.path.islink(os.path.join(d, arm)), "symlinked arm directory")
        arms[arm] = check_arm(load_json(os.path.join(d, arm, "summary.json")), run, arm)
    run["arms"] = arms
    return run


def read_runs(results):
    runs, skipped = [], 0
    base = os.path.join(results, "routine-results")
    if not os.path.isdir(base):
        return runs, skipped
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if os.path.islink(d) or not os.path.isdir(d):
            skipped += 1
            continue
        try:
            runs.append(read_run(d, name))
        except (ValueError, OSError):   # JSONDecodeError is a ValueError
            skipped += 1
    return runs, skipped


def build_runs(runs, skipped, generated_at):
    cells = {}
    for r in runs:
        cell = cells.setdefault((r["fixture"], r["model"]), {"runs": [], "cli": set(), "last": ""})
        cell["runs"].append(r)
        cell["cli"].add(r["cli_version"])
        cell["last"] = max(cell["last"], r["finished_at"])
    rows, per_arm = [], {a: [] for a in ARMS}
    for (fixture, model), cell in sorted(cells.items()):
        arms = {}
        for a in ARMS:
            scores = [s for s in (arm_score(r["arms"][a]) for r in cell["runs"] if a in r["arms"]) if s]
            if scores:
                arms[a] = {
                    "runs": len(scores),
                    "pass_rate": mean([s["pass_rate"] for s in scores]),
                    "judge": mean([s["judge"] for s in scores if s["judge"] is not None]),
                    "errors": sum(s["errors"] for s in scores),
                }
                per_arm[a].append(arms[a]["pass_rate"])
        adv = None
        if "with_skill" in arms and "without_skill" in arms:
            adv = arms["with_skill"]["pass_rate"] - arms["without_skill"]["pass_rate"]
        rows.append({"fixture": fixture, "model": model, "runs": len(cell["runs"]),
                     "cli_versions": sorted(cell["cli"]), "last_run": cell["last"],
                     "arms": arms, "advantage": adv})
    return {"generated_at": generated_at, "skipped_invalid": skipped,
            "overall_pass_rate": {a: mean(v) for a, v in per_arm.items()},
            "cells": rows}


def build_allowance(runs, cap, generated_at):
    weeks, unmeasured = {}, 0
    for r in runs:
        before, after = r["meter_before"], r["meter_after"]
        # a reading that is missing, or taken in two different weeks, measures nothing
        if not (before and after and before["seven_day"] is not None and after["seven_day"] is not None
                and before["resets_at"] is not None and before["resets_at"] == after["resets_at"]):
            unmeasured += 1
            continue
        w = weeks.setdefault(before["resets_at"], {"runs": 0, "used": 0.0})
        w["runs"] += 1
        w["used"] += max(0.0, after["seven_day"] - before["seven_day"])
    out = [{"week_resets_at": k, "runs": v["runs"], "used": v["used"],
            "fraction_of_cap": v["used"] / cap, "over_cap": v["used"] > cap}
           for k, v in sorted(weeks.items())]
    return {"generated_at": generated_at, "weekly_cap": cap, "unmeasured_runs": unmeasured, "weeks": out}


def build_gates(results, generated_at):
    """status.json is compute_gates.py's output: {"cells": [{"class": ..}, ..],
    "skills": {<skill>: {"verdict": .., "classes": [..]}}}."""
    path = os.path.join(results, "gates", "status.json")
    try:
        doc = load_json(path)
        need(isinstance(doc, dict) and isinstance(doc.get("cells"), list) and isinstance(doc.get("skills"), dict), "gates shape")
        counts, skills = {c: 0 for c in CLASSES}, []
        for c in doc["cells"]:
            need(isinstance(c, dict) and c.get("class") in CLASSES, "cell class")
            counts[c["class"]] += 1
        for key, s in sorted(doc["skills"].items()):
            need(SKILL.fullmatch(key) and isinstance(s, dict) and s.get("verdict") in VERDICTS, "skill entry")
            classes = s.get("classes")
            need(isinstance(classes, list) and all(c in CLASSES for c in classes), "classes")
            skills.append({"skill": key, "verdict": s["verdict"], "cells": len(classes)})
        return {"generated_at": generated_at, "available": True, "cell_classes": counts, "skills": skills}
    except (ValueError, OSError):
        return {"generated_at": generated_at, "available": False, "cell_classes": {}, "skills": []}


def round_floats(x, places=4):
    """Round only here, at the write."""
    if isinstance(x, float):
        return round(x, places)
    if isinstance(x, dict):
        return {k: round_floats(v, places) for k, v in x.items()}
    if isinstance(x, list):
        return [round_floats(v, places) for v in x]
    return x


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True, help="checkout of persistent/eval-results")
    ap.add_argument("--out", required=True)
    ap.add_argument("--weekly-cap", type=float, default=0.30, help="weekly_cap from evals/budget.yml")
    ap.add_argument("--previous-manifest", help="manifest.json from the last refresh")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.results):
        print("dashboard-refresh: --results is not a directory", file=sys.stderr)
        return 2
    if not 0 < a.weekly_cap <= 1:
        print("dashboard-refresh: --weekly-cap must be above 0 and at most 1", file=sys.stderr)
        return 2

    runs, skipped = read_runs(a.results)
    generated_at = max((r["finished_at"] for r in runs), default="1970-01-01T00:00:00Z")
    datasets = {
        "runs.json": build_runs(runs, skipped, generated_at),
        "allowance.json": build_allowance(runs, a.weekly_cap, generated_at),
        "gates.json": build_gates(a.results, generated_at),
    }
    os.makedirs(a.out, exist_ok=True)
    digests = {}
    for name, data in datasets.items():
        text = json.dumps(round_floats(data), indent=2, sort_keys=True) + "\n"
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            f.write(text)
        digests[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    previous = {}
    if a.previous_manifest and os.path.isfile(a.previous_manifest):
        try:
            previous = load_json(a.previous_manifest).get("datasets") or {}
        except (ValueError, OSError, AttributeError):
            previous = {}
    changed = sorted(n for n, d in digests.items() if previous.get(n) != d)
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"datasets": digests, "changed": changed}, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"dashboard-refresh: {len(runs)} runs, {skipped} skipped, changed: {', '.join(changed) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
