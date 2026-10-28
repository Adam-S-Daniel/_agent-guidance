"""Tests for dashboard-refresh.py. Run: python3 -m pytest test_dashboard_refresh.py -q
Deterministic: reads fixtures/dashboard/results, writes to tmp_path, no clock, no network."""
import ast
import importlib.util
import json
import os
import shutil

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "dashboard-refresh.py")
FIXTURES = os.path.join(HERE, "fixtures", "dashboard", "results")

spec = importlib.util.spec_from_file_location("dashboard_refresh", SCRIPT)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def refresh(tmp_path, results=FIXTURES, *extra):
    out = tmp_path / "out"
    rc = dr.main(["--results", str(results), "--out", str(out), *extra])
    return rc, out


def load(out, name):
    return json.loads((out / name).read_text())


def cell(runs, fixture, model):
    return next(c for c in runs["cells"] if c["fixture"] == fixture and c["model"] == model)


RUNS = "routine-results"


def edit_json(path, fn):
    doc = json.loads(path.read_text())
    fn(doc)
    path.write_text(json.dumps(doc))


def copy_run(results, src, rid, finished_at, resets_at):
    """A copy of run `src` under a new id, finished at a new time, in another allowance week."""
    dst = results / RUNS / rid
    shutil.copytree(results / RUNS / src, dst)
    for p in dst.rglob("*.json"):
        def fix(d):
            d["run_id"] = rid
            if "finished_at" in d:
                d["finished_at"] = finished_at
                d["meter_before"]["resets_at"] = d["meter_after"]["resets_at"] = resets_at
        edit_json(p, fix)
    return dst


def test_script_parses_with_ast():
    ast.parse(open(SCRIPT, encoding="utf-8").read())


def test_runs_per_fixture_and_model(tmp_path):
    rc, out = refresh(tmp_path)
    assert rc == 0
    runs = load(out, "runs.json")
    assert [(c["fixture"], c["model"]) for c in runs["cells"]] == [
        ("evals/example-skill/basic", "claude-sonnet-5-5"),
        ("evals/workflow-path-audit/ci-skips-docs", "claude-opus-4-7"),
        ("evals/workflow-path-audit/ci-skips-docs", "claude-sonnet-5-5"),
    ]
    c = cell(runs, "evals/workflow-path-audit/ci-skips-docs", "claude-sonnet-5-5")
    assert c["runs"] == 2 and c["cli_versions"] == ["2.1.250", "2.1.251"]
    assert c["last_run"] == "2026-10-22T12:30:00Z"
    # run 1: mean(0.8, 1.0) = 0.9; run 2: mean(1.0, errored trial as 0) = 0.5; cell = 0.7
    assert c["arms"]["with_skill"] == {"runs": 2, "pass_rate": 0.7, "judge": 7.0, "errors": 1}
    assert c["arms"]["without_skill"] == {"runs": 2, "pass_rate": 0.5, "judge": 5.0, "errors": 0}
    assert c["arms"]["bare"]["pass_rate"] == 0.2 and c["arms"]["bare"]["runs"] == 1
    assert c["advantage"] == 0.2          # 0.7 - 0.5 is 0.19999999999999996 until the write


def test_budget_stop_trials_are_left_out(tmp_path):
    _, out = refresh(tmp_path)
    c = cell(load(out, "runs.json"), "evals/workflow-path-audit/ci-skips-docs", "claude-opus-4-7")
    assert c["arms"]["with_skill"]["pass_rate"] == 0.9 and c["arms"]["with_skill"]["errors"] == 0
    assert "bare" not in c["arms"]


def test_overall_pass_rate_by_arm(tmp_path):
    _, out = refresh(tmp_path)
    o = load(out, "runs.json")["overall_pass_rate"]
    assert o == {"with_skill": 0.8667, "without_skill": 0.7333, "bare": 0.2}


def test_invalid_summary_is_skipped_and_counted(tmp_path):
    _, out = refresh(tmp_path)
    runs = load(out, "runs.json")
    assert runs["skipped_invalid"] == 1             # the run whose pass_rate is 2.0
    assert cell(runs, "evals/example-skill/basic", "claude-sonnet-5-5")["runs"] == 1


def test_allowance_per_week(tmp_path):
    _, out = refresh(tmp_path)
    a = load(out, "allowance.json")
    assert a["weekly_cap"] == 0.3 and a["unmeasured_runs"] == 1
    assert a["weeks"] == [
        {"week_resets_at": "2026-10-23T09:00:00Z", "runs": 2, "used": 0.05, "fraction_of_cap": 0.1667, "over_cap": False},
        {"week_resets_at": "2026-10-30T09:00:00Z", "runs": 1, "used": 0.03, "fraction_of_cap": 0.1, "over_cap": False},
    ]


def test_weekly_cap_flag_changes_over_cap(tmp_path):
    _, out = refresh(tmp_path, FIXTURES, "--weekly-cap", "0.04")
    weeks = load(out, "allowance.json")["weeks"]
    assert [w["over_cap"] for w in weeks] == [True, False]


def test_cli_version_as_the_harness_records_it(tmp_path):
    """run_eval.py keeps `claude --version` as printed, "2.1.251 (Claude Code)",
    or "unknown"; the dataset keeps the version alone, and a value outside the
    harness's own pattern skips the run."""
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    edit_json(results / RUNS / "20261026T120000Z-cccccc" / "run.json", lambda d: d.update(cli_version="2.1.260 (Claude Code)"))
    _, out = refresh(tmp_path, results)
    assert cell(load(out, "runs.json"), "evals/workflow-path-audit/ci-skips-docs", "claude-opus-4-7")["cli_versions"] == ["2.1.260"]
    edit_json(results / RUNS / "20261026T120000Z-cccccc" / "run.json", lambda d: d.update(cli_version="unknown"))
    _, out = refresh(tmp_path, results)
    assert cell(load(out, "runs.json"), "evals/workflow-path-audit/ci-skips-docs", "claude-opus-4-7")["cli_versions"] == ["unknown"]
    edit_json(results / RUNS / "20261026T120000Z-cccccc" / "run.json", lambda d: d.update(cli_version="2.1.260; rm -rf /"))
    _, out = refresh(tmp_path, results)
    assert load(out, "runs.json")["skipped_invalid"] == 2


def test_run_with_a_disagreeing_or_missing_arm_summary_is_skipped(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    edit_json(results / RUNS / "20261020T120000Z-aaaaaa" / "bare" / "summary.json",
              lambda d: d.update(run_id="20261022T120000Z-bbbbbb"))
    os.remove(results / RUNS / "20261022T120000Z-bbbbbb" / "without_skill" / "summary.json")
    _, out = refresh(tmp_path, results)
    runs = load(out, "runs.json")
    assert runs["skipped_invalid"] == 3                 # aaaaaa, bbbbbb and the invalid dddddd
    assert [(c["fixture"], c["model"]) for c in runs["cells"]] == [
        ("evals/example-skill/basic", "claude-sonnet-5-5"),
        ("evals/workflow-path-audit/ci-skips-docs", "claude-opus-4-7")]


def test_meter_read_in_two_weeks_is_unmeasured(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    edit_json(results / RUNS / "20261022T120000Z-bbbbbb" / "run.json",
              lambda d: d["meter_after"].update(resets_at="2026-10-30T09:00:00Z"))
    _, out = refresh(tmp_path, results)
    a = load(out, "allowance.json")
    assert a["unmeasured_runs"] == 2 and [w["runs"] for w in a["weeks"]] == [1, 1]


def test_gate_status(tmp_path):
    _, out = refresh(tmp_path)
    g = load(out, "gates.json")
    assert g["available"] is True
    assert g["cell_classes"] == {"insufficient": 2, "cannot-discriminate": 0, "benefit": 0, "ceiling": 1,
                                 "no-benefit": 1, "inconclusive": 0}
    assert g["skills"] == [
        {"skill": "example-skill", "verdict": "keep", "cells": 1},
        {"skill": "guidance/example-section", "verdict": "retire", "cells": 1},
        {"skill": "workflow-path-audit", "verdict": "keep", "cells": 2},
    ]


def test_missing_gate_file_is_reported_not_guessed(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    os.remove(results / "gates" / "status.json")
    _, out = refresh(tmp_path, results)
    assert load(out, "gates.json")["available"] is False


def test_bad_gate_class_makes_gates_unavailable(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    p = results / "gates" / "status.json"
    d = json.loads(p.read_text())
    d["cells"][0]["class"] = "made up"
    p.write_text(json.dumps(d))
    _, out = refresh(tmp_path, results)
    assert load(out, "gates.json")["available"] is False


def test_generated_at_comes_from_the_data_and_output_is_stable(tmp_path):
    rc1, out1 = refresh(tmp_path / "one")
    rc2, out2 = refresh(tmp_path / "two")
    assert load(out1, "runs.json")["generated_at"] == "2026-10-27T13:30:00Z"
    for n in ("runs.json", "allowance.json", "gates.json", "manifest.json"):
        assert (out1 / n).read_bytes() == (out2 / n).read_bytes()


def test_manifest_lists_only_changed_datasets(tmp_path):
    _, out = refresh(tmp_path / "first")
    m = load(out, "manifest.json")
    assert m["changed"] == ["allowance.json", "gates.json", "runs.json"]
    prev = tmp_path / "prev.json"
    shutil.copy(out / "manifest.json", prev)
    _, out2 = refresh(tmp_path / "second", FIXTURES, "--previous-manifest", str(prev))
    assert load(out2, "manifest.json")["changed"] == []
    # one more run moves generated_at, which every dataset carries, so all three change
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    copy_run(results, "20261026T120000Z-cccccc", "20261028T120000Z-ffffff", "2026-10-28T12:30:00Z",
             "2026-11-06T09:00:00Z")
    _, out3 = refresh(tmp_path / "third", results, "--previous-manifest", str(prev))
    assert load(out3, "manifest.json")["changed"] == ["allowance.json", "gates.json", "runs.json"]
    assert len(load(out3, "allowance.json")["weeks"]) == 3


def test_symlinked_run_directory_is_not_followed(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    outside = tmp_path / "outside"
    shutil.copytree(results / "routine-results" / "20261020T120000Z-aaaaaa", outside)
    os.symlink(outside, results / "routine-results" / "20261029T120000Z-abcdef")
    _, out = refresh(tmp_path, results)
    assert load(out, "runs.json")["skipped_invalid"] == 2


def test_oversized_summary_is_skipped(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    run = results / RUNS / "20261020T120000Z-aaaaaa"
    (run / "run.json").write_text(" " * (dr.MAX_BYTES + 1))
    (results / RUNS / "20261022T120000Z-bbbbbb" / "with_skill" / "summary.json").write_text(" " * (dr.MAX_BYTES + 1))
    _, out = refresh(tmp_path, results)
    assert load(out, "runs.json")["skipped_invalid"] == 3


def test_outputs_hold_only_matched_values(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(FIXTURES, results)
    run = results / RUNS / "20261020T120000Z-aaaaaa"
    for p in (run / "run.json", run / "with_skill" / "summary.json"):
        edit_json(p, lambda d: d.update(note="SECRET-FREE-TEXT-SHOULD-NOT-APPEAR"))
    _, out = refresh(tmp_path, results)
    for n in ("runs.json", "allowance.json", "gates.json", "manifest.json"):
        assert "SECRET-FREE-TEXT" not in (out / n).read_text()


def test_missing_results_directory_exits_2(tmp_path):
    assert dr.main(["--results", str(tmp_path / "nope"), "--out", str(tmp_path / "o")]) == 2


def test_empty_results_checkout_gives_empty_datasets(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc, out = refresh(tmp_path, empty)
    assert rc == 0
    assert load(out, "runs.json")["cells"] == []
    assert load(out, "allowance.json")["weeks"] == []
