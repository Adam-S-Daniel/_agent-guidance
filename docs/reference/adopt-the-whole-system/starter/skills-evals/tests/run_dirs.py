"""Writes a run directory the way run_eval.py does (its `run()` builds the
same dicts inline, so this mirrors it; the ingest schema test in
test_results.py keeps the two honest). Layout under a results root:

    routine-results/<run id>/run.json
    routine-results/<run id>/<arm>/summary.json
"""
import json
import os

TOKEN_NAMES = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def run_id_for(i):
    return f"20261001T0000{i:02d}Z-{i:06x}"


def ok(k, pass_rate, status="ok", tokens=10):
    return {"trial": k, "status": status, "errored": False, "error_kind": None,
            "num_turns": 3, "cost_usd": 0.01, "duration_s": 1.5,
            "tokens": {n: tokens for n in TOKEN_NAMES}, "pass_rate": pass_rate,
            "checks": [{"id": "c1", "passed": pass_rate > 0, "error": False, "detail": ""}]}


def errored(k, status="error", kind="cli_failed"):
    return {"trial": k, "status": status, "errored": True, "error_kind": kind,
            "num_turns": 0, "cost_usd": 0.0, "duration_s": 1.5, "tokens": {},
            "pass_rate": 0.0, "checks": []}


def stopped(k):
    return {"trial": k, "status": "budget_stop", "errored": False}


def summary_doc(run_id, arm, trials, judge=None, skill="s", fixture="evals/s/f", model="claude-m"):
    ran = [t for t in trials if t["status"] != "budget_stop"]
    rates = [t["pass_rate"] for t in ran]
    return {
        "schema_version": 1, "run_id": run_id, "fixture": fixture, "skill": skill, "arm": arm,
        "model": model, "trials_requested": len(trials), "trials_run": len(ran),
        "trials_skipped": len(trials) - len(ran),
        "errored_trials": sum(1 for t in ran if t["errored"]),
        "check_errors": sum(1 for t in ran for c in t.get("checks", []) if c["error"]),
        "pass_rate": round(sum(rates) / len(rates) if rates else 0.0, 6),
        "pass_rate_trials": rates,
        "tokens_total": sum(sum(t.get("tokens", {}).values()) for t in ran),
        "cost_usd_total": round(sum(t.get("cost_usd", 0.0) for t in ran), 6),
        "turns_total": sum(t.get("num_turns", 0) for t in ran),
        "trials": trials,
        "judge": None if judge is None else {"score": judge, "dimensions": {"quality": judge}},
        "budget_stop": None,
    }


def run_doc(run_id, arms, trials, cli="2.1.300", skill="s", fixture="evals/s/f", model="claude-m"):
    return {
        "schema_version": 1, "run_id": run_id, "mode": "eval", "fixture": fixture, "skill": skill,
        "fixture_digest": "0" * 64, "cli_version": cli,
        "models": {"arms": model, "judge": None, "reported": [model]},
        "arms": list(arms), "trials": trials,
        "started_at": "2026-10-01T00:00:00Z", "finished_at": "2026-10-01T00:30:00Z",
        "meter_before": {"seven_day": 0.1, "resets_at": "2026-10-04T09:00:00Z"},
        "meter_after": {"seven_day": 0.12, "resets_at": "2026-10-04T09:00:00Z"},
        "meter_delta": 0.02, "allowance_cost": 0.025, "budget_stop": None, "judged": False,
        "guards": {"config_dir_clean": True, "sandbox_available": True, "fixture_unchanged": True},
    }


def write_run(root, run_id, arms, cli="2.1.300", skill="s", fixture="evals/s/f", model="claude-m"):
    """arms: {arm name: (list of trial dicts, judge score or None)}."""
    d = os.path.join(str(root), "routine-results", run_id)
    os.makedirs(d, exist_ok=True)
    n = max(len(t) for t, _ in arms.values())
    with open(os.path.join(d, "run.json"), "w") as f:
        json.dump(run_doc(run_id, arms, n, cli, skill, fixture, model), f, indent=2)
    for arm, (trials, judge) in arms.items():
        os.makedirs(os.path.join(d, arm), exist_ok=True)
        with open(os.path.join(d, arm, "summary.json"), "w") as f:
            json.dump(summary_doc(run_id, arm, trials, judge, skill, fixture, model), f, indent=2)
    return d
