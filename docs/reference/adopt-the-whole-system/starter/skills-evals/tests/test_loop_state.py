import json

import pytest
import yaml

import dispatch_fires as D
import fire_inputs as fi
import loop_state as L
import plan_week as P
from conftest import FIXTURE_REPO, ROOT
from run_dirs import ok, run_id_for, write_run

MODEL = "claude-sonnet-5-5"
FX = "evals/workflow-path-audit/basic"


def files(tmp_path, rows=None, models=(MODEL,)):
    sched = tmp_path / "scheduled.yml"
    sched.write_text(yaml.safe_dump({"fixtures": rows if rows is not None else [
        {"fixture": FX, "status": "scheduled"},
        {"fixture": "evals/example-skill/basic", "status": "draft"}]}))
    roster = tmp_path / "roster.yml"
    roster.write_text(yaml.safe_dump({"arms": [{"id": m, "tier": "t", "why": "w"} for m in models],
                                      "judge": {"id": "claude-opus-5-0"}}))
    prs = tmp_path / "prs.json"
    prs.write_text("[]")
    return sched, roster, prs


def test_cells_are_scheduled_fixtures_times_roster_seats(tmp_path):
    sched, roster, _ = files(tmp_path, models=(MODEL, "claude-haiku-5-0"))
    assert L.cells(sched, roster) == [
        {"skill": "workflow-path-audit", "fixture": FX, "model": MODEL},
        {"skill": "workflow-path-audit", "fixture": FX, "model": "claude-haiku-5-0"}]


def test_cells_match_what_compute_gates_reads_from_a_run(tmp_path):
    import compute_gates
    sched, roster, _ = files(tmp_path)
    results = tmp_path / "results"
    for i in range(3):
        write_run(results, run_id_for(i), {"with_skill": ([ok(1, 1.0)], None), "without_skill": ([ok(1, 0.0)], None)},
                  skill="workflow-path-audit", fixture=FX, model=MODEL)
    pol = compute_gates.load_policy(str(ROOT / "evals" / "gate-policy.yml"))
    out = compute_gates.compute(compute_gates.load_runs(str(results)), L.cells(sched, roster), pol)
    assert out["cells"][0]["runs"] == 3          # the cell found its runs


def test_state_counts_runs_and_feeds_the_planner_and_the_fire_workflow(tmp_path):
    sched, roster, prs = files(tmp_path)
    results = tmp_path / "results"
    write_run(results, run_id_for(1), {"with_skill": ([ok(1, 0.5)], None), "without_skill": ([ok(1, 0.2)], None)},
              skill="workflow-path-audit", fixture=FX, model=MODEL)
    write_run(results, run_id_for(2), {"with_skill": ([ok(1, 0.7)], None), "without_skill": ([ok(1, 0.2)], None),
                                       "bare": ([ok(1, 0.1)], None)},
              skill="workflow-path-audit", fixture=FX, model=MODEL)
    st = L.state(results, sched, roster, FIXTURE_REPO / "evals", prs, "2026-10-28T12:00:00Z")
    assert st["cells"] == [{"skill": "workflow-path-audit", "fixture": "basic", "model": MODEL,
                            "runs": 2, "last_run": "2026-10-01", "bare_done": True}]
    (sk,) = st["skills"]
    assert sk["pass_rate"] == pytest.approx(0.6) and sk["fixtures"] == 1 and sk["uses_4w"] == 0
    assert st["open_removal_skills"] == [] and sk["open_improve_pr"] is False
    plan = P.plan(st, {"room": 0.30, "per_run_cap": 0.03, "improve_reservation": 0.12})
    assert plan["fires"], plan
    for fire in plan["fires"]:
        sent = D.inputs_for(fire, "20261028T120000Z-1a2b3c")
        event = {"inputs": {k: "" for k in fi.KNOWN} | {"arms": "both", "trials": "3"} | sent}
        assert fi.build_payload(event, ROOT / "evals" / "roster.yml", FIXTURE_REPO)["mode"] == "eval"


def test_open_loop_prs_pause_improve_and_a_removal_goes_to_band_one(tmp_path):
    sched, roster, prs = files(tmp_path)
    results = tmp_path / "results"
    write_run(results, run_id_for(1), {"with_skill": ([ok(1, 0.5)], None), "without_skill": ([ok(1, 0.2)], None)},
              skill="workflow-path-audit", fixture=FX, model=MODEL)
    prs.write_text(json.dumps([{"headRefName": "retire-skill/workflow-path-audit"},
                               {"headRefName": "feature/x"}]))
    st = L.state(results, sched, roster, FIXTURE_REPO / "evals", prs, "2026-10-28T12:00:00Z")
    assert st["open_removal_skills"] == ["workflow-path-audit"]
    assert st["skills"][0]["open_improve_pr"] is True
    assert [f["band"] for f in P.plan(st, {"room": 0.3, "per_run_cap": 0.03, "improve_reservation": 0.12})["fires"]][0] == 1


def test_a_skill_with_no_runs_gets_no_skills_row(tmp_path):
    sched, roster, prs = files(tmp_path)
    st = L.state(tmp_path / "none", sched, roster, FIXTURE_REPO / "evals", prs, "2026-10-28T12:00:00Z")
    assert st["skills"] == [] and st["cells"][0]["runs"] == 0 and st["cells"][0]["last_run"] is None


@pytest.mark.parametrize("bad", [
    {"fixtures": [{"fixture": "evals/x", "status": "scheduled"}]},
    {"fixtures": [{"fixture": "evals/a/b", "status": "maybe"}]},
    {"nope": []},
])
def test_bad_scheduled_file_is_exit_2(tmp_path, bad, capsys):
    sched, roster, _ = files(tmp_path)
    sched.write_text(yaml.safe_dump(bad))
    assert L.main(["cells", "--scheduled", str(sched), "--roster", str(roster)]) == 2


def test_usage_and_prs_files_are_checked(tmp_path):
    sched, roster, prs = files(tmp_path)
    usage = tmp_path / "u.json"
    usage.write_text(json.dumps({"Bad Name": 3}))
    args = ["state", "--results", str(tmp_path / "r"), "--scheduled", str(sched), "--roster", str(roster),
            "--evals-dir", str(FIXTURE_REPO / "evals"), "--open-prs", str(prs), "--now", "2026-10-28T12:00:00Z"]
    assert L.main(args + ["--usage", str(usage)]) == 2
    prs.write_text('{"headRefName": "x"}')
    assert L.main(args) == 2
    prs.write_text("[]")
    assert L.main(args[:-1] + ["yesterday"]) == 2
    assert L.main(args) == 0


def test_merge_mode_and_retire_candidate(tmp_path, capsys):
    pol = tmp_path / "p.yml"
    pol.write_text("improve_merge_mode: auto\n")
    assert L.main(["merge-mode", "--policy", str(pol)]) == 0
    assert capsys.readouterr().out == "auto\n"
    pol.write_text("improve_merge_mode: sometimes\n")
    assert L.main(["merge-mode", "--policy", str(pol)]) == 2
    status = tmp_path / "s.json"
    status.write_text(json.dumps({"skills": {"zeta": {"verdict": "retire"}, "alpha": {"verdict": "retire"},
                                             "beta": {"verdict": "keep"}, "Bad;": {"verdict": "retire"}}}))
    capsys.readouterr()
    assert L.main(["retire-candidate", "--status", str(status)]) == 0
    assert capsys.readouterr().out == "alpha\n"
    status.write_text(json.dumps({"skills": {"beta": {"verdict": "keep"}}}))
    assert L.main(["retire-candidate", "--status", str(status)]) == 0
    assert capsys.readouterr().out == ""
