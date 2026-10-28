import json
import os

import pytest

import compute_gates as G
from harness import results as R
import run_dirs as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POL = G.load_policy(os.path.join(ROOT, "evals", "gate-policy.yml"))


def arm(run_i, arm_name, trials, cli="2.0.0", skill="s", fixture="f", model="m", judge=None):
    return R.ArmSummary(f"20261001T0000{run_i:02d}Z-{run_i:06x}", skill, fixture, model, cli,
                        arm_name, tuple(R.Trial(*t) for t in trials), judge)


def run(i, w, o, cli=None, wj=None, oj=None, werr=False, **kw):
    cli = cli or ("2.0.0" if i % 2 else "2.1.0")
    wt = [(w, werr, 100)] + [(w, False, 100)] * 2
    ot = [(o, False, 100)] * 3
    return R.pair_runs([arm(i, "with_skill", wt, cli, judge=wj, **kw),
                        arm(i, "without_skill", ot, cli, judge=oj, **kw)])[0]


def series(n, w, o, jitter=0.0, **kw):
    out = []
    for i in range(1, n + 1):
        s = jitter if i % 2 else -jitter
        out.append(run(i, w + s, o + s, **kw))
    return out


def cls(runs):
    return G.classify_cell(runs, POL)


def test_insufficient_runs():
    assert cls(series(4, 0.5, 0.5))["class"] == "insufficient"


def test_insufficient_cli_versions():
    runs = [run(i, 0.5, 0.5, cli="2.0.0") for i in range(1, 11)]
    assert cls(runs)["class"] == "insufficient"


def test_cannot_discriminate():
    assert cls(series(5, 0.05, 0.02))["class"] == "cannot-discriminate"


def test_benefit():
    assert cls(series(5, 0.8, 0.4, jitter=0.01))["class"] == "benefit"


def test_benefit_on_judge_points_alone():
    runs = [run(i, 0.5, 0.5, wj=8.0 + 0.1 * (i % 2), oj=5.0 + 0.1 * (i % 2)) for i in range(1, 6)]
    assert cls(runs)["class"] == "benefit"


def test_benefit_is_tested_before_ceiling():
    assert cls(series(5, 0.99, 0.91, jitter=0.002))["class"] == "benefit"


def test_ceiling_by_pass_rate():
    assert cls(series(5, 0.95, 0.95))["class"] == "ceiling"


def test_ceiling_by_judge():
    runs = [run(i, 0.5, 0.5, wj=9.2, oj=9.2) for i in range(1, 6)]
    assert cls(runs)["class"] == "ceiling"


def test_no_benefit_needs_two_looks():
    one_look = cls(series(9, 0.5, 0.5, jitter=0.01))
    assert one_look["class"] == "inconclusive" and one_look["reason"] == "only one look reached"
    assert cls(series(10, 0.5, 0.5, jitter=0.01))["class"] == "no-benefit"
    assert cls(series(20, 0.5, 0.5, jitter=0.01))["class"] == "no-benefit"


def test_both_largest_looks_must_hold(monkeypatch):
    real = G.classify_window

    def fake(runs, pol):
        return ("no-benefit" if len(runs) == 10 else "inconclusive"), {}
    monkeypatch.setattr(G, "classify_window", fake)
    out = cls(series(10, 0.5, 0.5))
    assert out["class"] == "inconclusive" and out["reason"] == "earlier look did not hold"
    monkeypatch.setattr(G, "classify_window", real)


def test_noisy_cell_is_not_no_benefit():
    assert cls(series(10, 0.5, 0.5, jitter=0.3))["class"] == "inconclusive"


def test_errored_trial_counts_as_zero_and_blocks_no_benefit():
    a = arm(1, "with_skill", [(0.9, True, 5), (0.9, False, 5)])
    assert a.pass_rate == pytest.approx(0.45) and a.errors == 1
    runs = series(10, 0.5, 0.5, jitter=0.01)
    runs[0] = run(1, 0.5, 0.5, werr=True)
    out = cls(runs)
    assert out["class"] == "inconclusive" and out["reason"] == "errors in the runs"


def test_interval_is_the_wider_of_two(monkeypatch):
    a, b = [0.5, 0.52, 0.48, 0.5, 0.51], [0.5, 0.5, 0.5, 0.5, 0.5]
    _, wu = G.welch_bounds(a, b, 0.975)
    _, bu = G.bootstrap_bounds(a, b, 0.975, 2000, 1)
    _, u = G.wide_bounds(a, b, POL | {"bootstrap_seed": 1})
    assert u == max(wu, bu)
    assert G.bootstrap_bounds(a, b, 0.975, 2000, 1) == G.bootstrap_bounds(a, b, 0.975, 2000, 1)


def roster(*skills):
    return [{"skill": s, "fixture": "f", "model": "m"} for s in skills]


def test_skill_retires_only_if_every_cell_is_no_benefit():
    runs = series(10, 0.5, 0.5, jitter=0.01, skill="a") + series(10, 0.5, 0.5, jitter=0.01, skill="b")
    runs += series(10, 0.9, 0.2, jitter=0.01, skill="b", fixture="g")
    out = G.compute(runs, roster("a", "b") + [{"skill": "b", "fixture": "g", "model": "m"}], POL)
    assert out["skills"]["a"]["verdict"] == "retire"
    assert out["skills"]["b"]["verdict"] == "keep"


def test_cell_without_runs_is_insufficient_and_blocks_retirement():
    runs = series(10, 0.5, 0.5, jitter=0.01, skill="a")
    out = G.compute(runs, roster("a") + [{"skill": "a", "fixture": "g", "model": "m"}], POL)
    assert out["skills"]["a"]["verdict"] == "keep"


def test_empty_roster_retires_nothing():
    out = G.compute(series(10, 0.5, 0.5, jitter=0.01), [], POL)
    assert out == {"cells": [], "skills": {}}


def test_policy_rejects_unknown_keys(tmp_path):
    p = tmp_path / "p.yml"
    p.write_text("min_runs: 5\nsurprise: 1\n")
    with pytest.raises(ValueError):
        G.load_policy(str(p))


def write_pair(root, i, pass_rate, cli):
    trials = [D.ok(1, pass_rate)]
    D.write_run(root, D.run_id_for(i), {"with_skill": (trials, None), "without_skill": (trials, None)},
                cli=cli, fixture="f", model="m")


def test_cli_end_to_end(tmp_path, capsys):
    res = tmp_path / "results"
    res.mkdir()
    for i in range(1, 11):
        write_pair(res, i, 0.5, "2.0.0" if i % 2 else "2.1.0")
    (tmp_path / "roster.json").write_text(json.dumps(roster("s")))
    code = G.main(["--results", str(res), "--roster", str(tmp_path / "roster.json"),
                   "--policy", os.path.join(ROOT, "evals", "gate-policy.yml")])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["skills"]["s"]["verdict"] == "retire"


def test_cli_malformed_run_exits_2(tmp_path):
    write_pair(tmp_path, 1, 0.5, "2.0.0")
    (tmp_path / "routine-results" / D.run_id_for(1) / "run.json").write_text("{}")
    (tmp_path / "roster.json").write_text(json.dumps(roster("s")))
    assert G.main(["--results", str(tmp_path), "--roster", str(tmp_path / "roster.json"),
                   "--policy", os.path.join(ROOT, "evals", "gate-policy.yml")]) == 2


def test_cli_unreadable_input_exits_2(tmp_path):
    assert G.main(["--results", str(tmp_path), "--roster", str(tmp_path / "none.json"),
                   "--policy", os.path.join(ROOT, "evals", "gate-policy.yml")]) == 2
