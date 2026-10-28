"""The dashboard reads what the harness writes: a run directory made by
run_eval.py (through the conftest rig and the fake claude) goes through
scripts/dashboard-refresh.py with nothing skipped."""
import importlib.util
import json
import shutil

from conftest import ROOT, RUN_ID

spec = importlib.util.spec_from_file_location("dashboard_refresh", ROOT / "scripts" / "dashboard-refresh.py")
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_a_harness_run_is_a_valid_dashboard_row(rig, tmp_path, monkeypatch):
    # The routine passes the fixture as evals/<skill>/<name>, relative to the checkout.
    monkeypatch.chdir(rig.tmp)
    rig.fixture = rig.fixture.relative_to(rig.tmp)
    assert rig.run() == 0
    results = tmp_path / "results"
    shutil.copytree(rig.out, results / "routine-results" / RUN_ID)
    out = tmp_path / "data"
    assert dr.main(["--results", str(results), "--out", str(out)]) == 0
    runs = json.loads((out / "runs.json").read_text())
    assert runs["skipped_invalid"] == 0
    (cell,) = runs["cells"]
    run = rig.run_doc()
    assert (cell["fixture"], cell["model"]) == (run["fixture"], run["models"]["arms"])
    assert run["cli_version"] == "2.1.999 (Claude Code)"     # what `claude --version` prints
    assert cell["cli_versions"] == ["2.1.999"]
    assert cell["arms"]["with_skill"]["pass_rate"] == rig.summary("with_skill")["pass_rate"]
    assert cell["arms"]["without_skill"]["pass_rate"] == rig.summary("without_skill")["pass_rate"]
    allowance = json.loads((out / "allowance.json").read_text())
    assert allowance["unmeasured_runs"] == 0 and len(allowance["weeks"]) == 1
