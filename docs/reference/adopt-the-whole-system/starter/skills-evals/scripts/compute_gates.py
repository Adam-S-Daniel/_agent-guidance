#!/usr/bin/env python3
"""compute_gates.py: retirement verdicts from the results branch.

    compute_gates.py --results <dir> --roster <roster.json> \
        [--policy evals/gate-policy.yml]

roster.json is a list of cells: [{"skill": "...", "fixture": "...", "model": "..."}].
Output (stdout) is JSON: every cell's class and every skill's verdict
("retire" or "keep"). Exit 0 always on success; 2 on unreadable input.

Classes, tested in this order (thresholds from the policy file):
  insufficient, cannot-discriminate, benefit, ceiling, no-benefit,
  and "inconclusive" for a cell that fits none.
Evidence is a run (one with/without pair), not a trial. A cell is judged
at fixed looks (the first 5, 10 and 20 runs, by run id). The classes above
are computed on the largest look reached. no-benefit must ALSO hold at the
second largest look, so a cell with one look reached can never be no-benefit.
Errored trials count as zero and block no-benefit. A skill is retire only if
it has at least one cell and every cell is no-benefit.
"""
import argparse
import json
import math
import os
import random
import sys
from statistics import NormalDist

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from harness.results import ResultsError, load_runs  # noqa: E402

DEFAULT_POLICY = {
    "min_runs": 5, "min_cli_versions": 2, "cannot_discriminate_below": 0.10,
    "ceiling_pass": 0.90, "ceiling_judge": 9.0, "no_benefit_pass": 0.05,
    "no_benefit_judge": 0.5, "confidence": 0.975, "looks": [5, 10, 20],
    "bootstrap_resamples": 2000, "bootstrap_seed": 20261028,
}


# evals/gate-policy.yml uses the guide's key names (9.2); the code uses the
# short names on the right. Either spelling is accepted, never both.
POLICY_ALIASES = {
    "insufficient_runs": "min_runs", "insufficient_cli_versions": "min_cli_versions",
    "ceiling_pass_rate": "ceiling_pass", "no_benefit_pass_rate_bound": "no_benefit_pass",
    "no_benefit_judge_bound": "no_benefit_judge",
}
LOOKS_REQUIRED = 2   # classify_cell checks the two largest looks reached; nothing else


def load_policy(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    if not isinstance(doc, dict):
        raise ValueError("policy must be a mapping")
    doc = dict(doc)
    if "looks_required" in doc:
        v = doc.pop("looks_required")
        if type(v) is not int or v != LOOKS_REQUIRED:
            raise ValueError("looks_required: only %d is supported" % LOOKS_REQUIRED)
    for guide_name, name in POLICY_ALIASES.items():
        if guide_name in doc:
            if name in doc:
                raise ValueError("policy sets both %s and %s" % (guide_name, name))
            doc[name] = doc.pop(guide_name)
    unknown = set(doc) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError("unknown policy keys: " + ", ".join(sorted(unknown)))
    return {**DEFAULT_POLICY, **doc}


def t_quantile(p, df):
    """Student t quantile by the Cornish-Fisher expansion; fine for df >= 4."""
    z = NormalDist().inv_cdf(p)
    return (z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3))


def _mean(xs):
    return sum(xs) / len(xs)


def _var(xs):
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch_bounds(a, b, conf):
    """One-sided bounds of mean(a) - mean(b): (lower, upper)."""
    diff = _mean(a) - _mean(b)
    va, vb = _var(a) / len(a), _var(b) / len(b)
    se2 = va + vb
    if se2 == 0:
        return diff, diff
    se = math.sqrt(se2)
    df = se2**2 / ((va**2 / (len(a) - 1)) + (vb**2 / (len(b) - 1)))
    t = t_quantile(conf, max(df, 4.0))
    return diff - t * se, diff + t * se


def bootstrap_bounds(a, b, conf, resamples, seed):
    """One-sided percentile bounds of the mean paired difference."""
    d = [x - y for x, y in zip(a, b)]
    rng = random.Random(seed)
    n = len(d)
    means = sorted(_mean([d[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    hi = means[min(resamples - 1, math.ceil(conf * resamples) - 1)]
    lo = means[max(0, math.floor((1 - conf) * resamples))]
    return lo, hi


def wide_bounds(a, b, pol):
    """The wider of the Welch and bootstrap intervals: (lower, upper)."""
    wl, wu = welch_bounds(a, b, pol["confidence"])
    bl, bu = bootstrap_bounds(a, b, pol["confidence"], pol["bootstrap_resamples"],
                              pol["bootstrap_seed"])
    return min(wl, bl), max(wu, bu)


def _series(runs):
    wp = [r.with_arm.pass_rate for r in runs]
    op = [r.without_arm.pass_rate for r in runs]
    pairs = [(r.with_arm.judge, r.without_arm.judge) for r in runs
             if r.with_arm.judge is not None and r.without_arm.judge is not None]
    return wp, op, [p[0] for p in pairs], [p[1] for p in pairs]


def classify_window(runs, pol):
    """Classes 2 to 5 on one window of runs. Returns (class, detail)."""
    wp, op, wj, oj = _series(runs)
    detail = {"runs": len(runs), "with_pass": _mean(wp), "without_pass": _mean(op)}
    if _mean(wp) < pol["cannot_discriminate_below"] and _mean(op) < pol["cannot_discriminate_below"]:
        return "cannot-discriminate", detail
    p_lo, p_hi = wide_bounds(wp, op, pol)
    detail.update(pass_lower=p_lo, pass_upper=p_hi)
    if len(wj) >= 2:
        j_lo, j_hi = wide_bounds(wj, oj, pol)
        detail.update(judge_lower=j_lo, judge_upper=j_hi)
    else:
        j_lo = j_hi = 0.0   # no judge evidence: neither helps nor blocks
    if p_lo > 0 or j_lo > 0:
        return "benefit", detail
    judge_ceiling = bool(wj) and _mean(wj) >= pol["ceiling_judge"] and _mean(oj) >= pol["ceiling_judge"]
    if (_mean(wp) >= pol["ceiling_pass"] and _mean(op) >= pol["ceiling_pass"]) or judge_ceiling:
        return "ceiling", detail
    if p_hi < pol["no_benefit_pass"] and j_hi < pol["no_benefit_judge"]:
        return "no-benefit", detail
    return "inconclusive", detail


def classify_cell(runs, pol):
    """runs: the cell's paired runs, sorted by run id."""
    n = len(runs)
    versions = {r.cli_version for r in runs}
    out = {"runs": n, "cli_versions": len(versions)}
    if n < pol["min_runs"] or len(versions) < pol["min_cli_versions"]:
        return {**out, "class": "insufficient"}
    looks = [k for k in sorted(pol["looks"]) if k <= n]
    window = runs[:looks[-1]]
    cls, detail = classify_window(window, pol)
    out.update(detail=detail, look=looks[-1])
    if cls == "no-benefit":
        if len(looks) < 2:
            cls = "inconclusive"
            out["reason"] = "only one look reached"
        elif sum(r.errors for r in window):
            cls = "inconclusive"
            out["reason"] = "errors in the runs"
        else:
            prev_cls, _ = classify_window(runs[:looks[-2]], pol)
            if prev_cls != "no-benefit" or sum(r.errors for r in runs[:looks[-2]]):
                cls = "inconclusive"
                out["reason"] = "earlier look did not hold"
    return {**out, "class": cls}


def compute(runs, roster, pol):
    by = {}
    for r in runs:
        by.setdefault((r.skill, r.fixture, r.model), []).append(r)
    cells, skills = [], {}
    for c in roster:
        key = (c["skill"], c["fixture"], c["model"])
        res = classify_cell(sorted(by.get(key, []), key=lambda r: r.run_id), pol)
        cells.append({"skill": key[0], "fixture": key[1], "model": key[2], **res})
        skills.setdefault(key[0], []).append(res["class"])
    verdicts = {s: {"verdict": "retire" if cl and all(x == "no-benefit" for x in cl) else "keep",
                    "classes": cl} for s, cl in skills.items()}
    return {"cells": cells, "skills": verdicts}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--roster", required=True)
    ap.add_argument("--policy", default="evals/gate-policy.yml")
    a = ap.parse_args(argv)
    try:
        pol = load_policy(a.policy)
        with open(a.roster, encoding="utf-8") as f:
            roster = json.load(f)
        runs = load_runs(a.results)
    except (OSError, ValueError, ResultsError) as e:
        print(f"compute_gates: cannot read input ({type(e).__name__})", file=sys.stderr)
        return 2
    json.dump(compute(runs, roster, pol), sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
