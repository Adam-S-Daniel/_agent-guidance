"""One file per policy, every reader accepts it. evals/budget.yml is read by
harness/budget.py (strict schema) and tests/test_planner.py; gate-policy.yml
by compute_gates.py (strict) and test_budget.py; improve-policy.yml by
improve_gate.py (strict), the planner's constants and test_budget.py."""
import pytest
import yaml

import compute_gates as CG
import improve_gate as IG
import plan_week as PW
from conftest import ROOT
from harness.budget import load_budget

EVALS = ROOT / "evals"


def test_the_budget_file_satisfies_the_strict_loader_and_the_planner():
    b = load_budget(EVALS / "budget.yml")
    assert b["improve_reservation"] == 0.12 and b["weekly_reset_fallback"] == "mon 17:00"


def test_the_gate_policy_file_loads_with_the_guide_names_mapped():
    pol = CG.load_policy(str(EVALS / "gate-policy.yml"))
    assert set(pol) == set(CG.DEFAULT_POLICY)
    assert (pol["min_runs"], pol["min_cli_versions"], pol["ceiling_pass"]) == (5, 2, 0.90)
    assert (pol["no_benefit_pass"], pol["no_benefit_judge"]) == (0.05, 0.5)
    assert pol["looks"] == [5, 10, 20] and pol["bootstrap_seed"] == 20261028
    # The guide's reference values are the code's defaults, so either file shape agrees.
    assert pol == CG.DEFAULT_POLICY


@pytest.mark.parametrize("text", [
    "insufficient_runs: 5\nmin_runs: 5\n",       # both spellings
    "looks_required: 3\n",                       # the code checks two looks, no other number
    "looks_required: true\n",
    "surprise: 1\n",
    "- a list\n",
])
def test_the_gate_policy_loader_refuses(tmp_path, text):
    p = tmp_path / "p.yml"
    p.write_text(text)
    with pytest.raises(ValueError):
        CG.load_policy(str(p))


def test_the_improve_policy_file_loads_with_the_guide_names_mapped():
    pol = IG.load_policy(str(EVALS / "improve-policy.yml"))
    assert set(pol) == set(IG.DEFAULT_POLICY)
    assert (pol["min_train_gain"], pol["judge_tie_gain"], pol["min_fixtures"]) == (0.10, 1.0, 4)
    assert pol["improve_merge_mode"] == "draft"     # the guide: start with draft


def test_the_improve_policy_planner_keys_match_the_planner():
    """plan_week.py keeps these as constants; the file must not say otherwise."""
    doc = yaml.safe_load((EVALS / "improve-policy.yml").read_text())
    assert doc["trials"] == PW.TRIALS
    assert doc["min_fixtures"] == PW.IMPROVE_MIN_FIXTURES
    assert doc["pause_after_rejection_days"] == PW.PAUSE_DAYS_AFTER_REJECTION
    assert doc["pause_after_consecutive_rejections"] == PW.PAUSE_AFTER_REJECTIONS_IN_A_ROW


@pytest.mark.parametrize("text", [
    "training_gain_min: 0.1\nmin_train_gain: 0.1\n",   # both spellings
    "holdout_may_not_regress: false\n",                # cannot be switched off
    "no_errored_fixture: 1\n",
    "trials: 0\n",
    "pause_after_rejection_days: 1.5\n",
    "improve_merge_mode: yolo\n",
    "surprise: 1\n",
])
def test_the_improve_policy_loader_refuses(tmp_path, text):
    p = tmp_path / "p.yml"
    p.write_text(text)
    with pytest.raises(ValueError):
        IG.load_policy(str(p))

