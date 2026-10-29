import copy
import re
from pathlib import Path

import pytest
import yaml

from conftest import NOW, ROOT
from harness.budget import (BudgetError, last_fallback_reset, load_budget, meter_reading,
                            parse_instant, validate_budget, week_window)

GOOD = yaml.safe_load((ROOT / "evals" / "budget.yml").read_text())


def bad(**change):
    doc = copy.deepcopy(GOOD)
    for k, v in change.items():
        if v is None:
            doc.pop(k, None)
        else:
            doc[k] = v
    return doc


def test_shipped_budget_loads_with_the_guide_numbers():
    b = load_budget(ROOT / "evals" / "budget.yml")
    assert (b["weekly_cap"], b["per_run_cap"], b["per_run_overrun"]) == (0.30, 0.03, 0.005)
    assert b["stop_when_account_above"] == 0.80 and b["improve_reservation"] == 0.12


@pytest.mark.parametrize("doc", [
    bad(improve_reservation=None),                 # new key is required
    bad(improve_reservation=0.31),                 # must be at most weekly_cap
    bad(improve_reservation=0),
    bad(improve_reservation="0.12"),
    bad(improve_reservation=True),
    bad(per_run_cap=0.31),
    bad(weekly_cap=1.5),
    bad(stop_when_account_above=0),
    bad(extra_key=1),                              # unknown keys refused
    bad(weekly_reset_fallback="monday 5pm"),
    bad(weekly_reset_fallback="mon 25:00"),
    bad(per_run_overrun=-0.1),
])
def test_bad_budgets_are_refused(doc):
    with pytest.raises(BudgetError):
        validate_budget(doc)


def test_unreadable_budget_is_an_error(tmp_path):
    with pytest.raises(BudgetError):
        load_budget(tmp_path / "missing.yml")
    (tmp_path / "x.yml").write_text("- a list\n")
    with pytest.raises(BudgetError):
        load_budget(tmp_path / "x.yml")


def test_other_example_policies_parse_and_agree_with_the_guide():
    gate = yaml.safe_load((ROOT / "evals" / "gate-policy.yml").read_text())
    assert gate["looks"] == [5, 10, 20] and gate["confidence"] == 0.975
    imp = yaml.safe_load((ROOT / "evals" / "improve-policy.yml").read_text())
    assert imp["min_fixtures"] == 4 and imp["training_gain_min"] == 0.10
    assert imp["improve_merge_mode"] in ("draft", "auto")
    sched = yaml.safe_load((ROOT / "evals" / "scheduled.yml").read_text())
    # The fixtures themselves are content (evals/<skill>/<name>/), added per
    # account; the suite checks the rows' shape, not that the content exists.
    for row in sched["fixtures"]:
        assert re.fullmatch(r"evals/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", row["fixture"]), row
        assert row["status"] in ("scheduled", "draft"), row
    roster = yaml.safe_load((ROOT / "evals" / "roster.yml").read_text())
    assert roster["arms"] and roster["judge"]["id"]


def test_fallback_reset_is_the_latest_matching_instant():
    # Wednesday 2026-10-28 12:00Z, "mon 17:00" -> Monday the 26th
    assert last_fallback_reset("mon 17:00", NOW).isoformat() == "2026-10-26T17:00:00+00:00"
    # the reset instant itself, and one minute before it
    mon = parse_instant("2026-10-26T17:00:00Z")
    assert last_fallback_reset("mon 17:00", mon) == mon
    assert last_fallback_reset("mon 17:00", parse_instant("2026-10-26T16:59:00Z")).isoformat() \
        == "2026-10-19T17:00:00+00:00"


def test_week_window_rolls_any_reset_instant_to_the_current_week():
    for reset in ("2026-10-26T17:00:00Z", "2026-11-02T17:00:00Z", "2026-09-07T17:00:00Z"):
        start, end = week_window(parse_instant(reset), NOW)
        assert start.isoformat() == "2026-10-26T17:00:00+00:00"
        assert (end - start).days == 7


def test_meter_reading_takes_the_newest_valid_event():
    msgs = [
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "seven_day": {"utilization": 0.2, "resetsAt": 1791660000}}}},
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "seven_day": {"utilization": "bad"}}}},
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": 0.9}}}},
        {"type": "result"},
    ]
    r = meter_reading(msgs)
    assert r["seven_day"] == 0.2 and r["resets_at"].endswith("Z")
    assert meter_reading([{"type": "result"}]) is None
    assert meter_reading({"type": "result"}) is None
