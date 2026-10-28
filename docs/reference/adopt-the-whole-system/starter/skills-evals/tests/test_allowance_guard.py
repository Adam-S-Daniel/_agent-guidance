import json
from datetime import timedelta

import pytest

from conftest import NOW, ROOT
import allowance_guard as g
from harness.budget import load_budget, parse_instant

BUDGET = load_budget(ROOT / "evals" / "budget.yml")   # cap .30, run .03, overrun .005, improve .12
RESETS = "2026-11-02T17:00:00Z"                        # next Monday; the week is Oct 26 17:00 to Nov 2 17:00


def rid(day, n=1):
    """A run id inside the current week: day 27 = Tuesday."""
    return f"202610{day:02d}T090000Z-{n:06x}"


def fire(mode, run_id, created="2026-10-27T09:00:05Z", fired=True):
    return {"name": f"fire {mode} {run_id}", "created_at": created, "fired": fired}


def meter(before, after):
    r = "2026-11-02T17:00:00Z"
    return {"seven_day": before, "resets_at": r}, {"seven_day": after, "resets_at": r}


def ingest(results, run_id, before=0.20, after=0.215, mode="eval"):
    d = results / "routine-results" / run_id
    d.mkdir(parents=True)
    b, a = meter(before, after)
    doc = {"run_id": run_id, "meter_before": b, "meter_after": a}
    (d / "run.json").write_text(json.dumps(doc))


def decide(tmp_path, fires, kind="eval", results=None, budget=BUDGET, reset_at=RESETS):
    results = results or tmp_path / "results"
    results.mkdir(exist_ok=True)
    f = tmp_path / "fires.json"
    f.write_text(json.dumps(fires))
    return g.check(results, f, budget, NOW, kind, parse_instant(reset_at) if reset_at else None)


def test_a_fresh_week_admits_an_eval_fire_and_one_improve_fire(tmp_path):
    code, out = decide(tmp_path, [])
    assert code == 0 and out["used"] == 0 and out["reservation"] == 0.03
    code, out = decide(tmp_path, [], kind="improve")
    assert code == 0 and out["reservation"] == 0.12 <= out["weekly_cap"]


def test_unresolved_fires_are_charged_the_full_cap_until_the_cap_is_reached(tmp_path):
    nine = [fire("eval", rid(27, n)) for n in range(1, 10)]          # 9 x 0.03 = 0.27
    code, out = decide(tmp_path, nine)
    assert code == 0 and out["used"] == pytest.approx(0.27) and out["unresolved_fires"] == 9
    ten = nine + [fire("eval", rid(27, 10))]                          # 0.30 used, one more does not fit
    code, out = decide(tmp_path, ten)
    assert code == 1 and out["reason"] == "weekly_cap" and out["used"] == pytest.approx(0.30)


def test_the_last_reservation_that_just_fits_is_admitted(tmp_path):
    fires = [fire("eval", rid(27, n)) for n in range(1, 10)]
    assert decide(tmp_path, fires)[0] == 0          # 0.27 + 0.03 == 0.30 exactly (no float surprise)


def test_a_failed_fire_keeps_its_full_charge_for_the_rest_of_the_week(tmp_path):
    # Fired Tuesday, never ingested. Still a full cap on Wednesday and until the reset.
    code, out = decide(tmp_path, [fire("eval", rid(27))])
    assert out["used"] == pytest.approx(0.03) and out["unresolved_fires"] == 1
    # A rejected-at-ingest run leaves no routine-results directory: same charge.
    # A fire that never reached the fire step costs nothing.
    code, out = decide(tmp_path, [fire("eval", rid(27), fired=False)])
    assert out["used"] == 0


def test_a_fire_from_before_the_reset_is_not_charged(tmp_path):
    old = fire("eval", "20261020T090000Z-000001", created="2026-10-20T09:00:00Z")
    assert decide(tmp_path, [old])[1]["used"] == 0
    edge = fire("eval", rid(26), created="2026-10-26T17:00:00Z")      # exactly at the reset: this week
    assert decide(tmp_path, [edge])[1]["used"] == pytest.approx(0.03)


def test_an_ingested_run_costs_its_measured_delta_plus_overrun(tmp_path):
    results = tmp_path / "results"
    ingest(results, rid(27), before=0.20, after=0.215)               # 0.015 + 0.005
    code, out = decide(tmp_path, [fire("eval", rid(27))], results=results)
    assert out["used"] == pytest.approx(0.020) and out["ingested"] == 1 and out["unresolved_fires"] == 0


def test_an_ingested_run_that_cannot_be_measured_costs_the_cap(tmp_path):
    results = tmp_path / "results"
    d = results / "routine-results" / rid(27)
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"run_id": rid(27), "meter_before": None, "meter_after": None}))
    assert decide(tmp_path, [fire("eval", rid(27))], results=results)[1]["used"] == pytest.approx(0.03)
    (d / "run.json").write_text(json.dumps({
        "run_id": rid(27), "meter_before": {"seven_day": 0.5, "resets_at": "a"},
        "meter_after": {"seven_day": 0.1, "resets_at": "b"}}))     # crossed a reset
    assert decide(tmp_path, [fire("eval", rid(27))], results=results)[1]["used"] == pytest.approx(0.03)


def test_an_ingested_run_with_no_fire_entry_still_counts(tmp_path):
    results = tmp_path / "results"
    ingest(results, rid(28), before=0.1, after=0.12)
    out = decide(tmp_path, [], results=results)[1]
    assert out["used"] == pytest.approx(0.025) and out["ingested"] == 1


def test_an_improve_fire_is_charged_its_reservation_whatever_the_outcome(tmp_path):
    code, out = decide(tmp_path, [fire("improve", rid(27))])
    assert code == 0 and out["used"] == pytest.approx(0.12) and out["improve_fires"] == 1
    # Four eval fires cost the same as the one improve fire: 4 x 0.03.
    four = [fire("eval", rid(27, n)) for n in range(1, 5)]
    assert decide(tmp_path, four)[1]["used"] == pytest.approx(0.12)


def test_improve_fires_stack_until_the_next_one_no_longer_fits(tmp_path):
    two = [fire("improve", rid(27, 1)), fire("improve", rid(27, 2))]          # 0.24
    code, out = decide(tmp_path, two, kind="improve")                        # + 0.12 = 0.36 > 0.30
    assert code == 1 and out["reason"] == "weekly_cap" and out["used"] == pytest.approx(0.24)
    assert decide(tmp_path, two, kind="eval")[0] == 0                        # an eval fire (0.03) still fits
    two_evals = two + [fire("eval", rid(27, 3)), fire("eval", rid(27, 4))]    # 0.30 used
    assert decide(tmp_path, two_evals, kind="eval")[0] == 1


def test_the_account_meter_stops_a_fire_even_with_room_in_the_week(tmp_path):
    results = tmp_path / "results"
    ingest(results, rid(27), before=0.80, after=0.82)
    code, out = decide(tmp_path, [fire("eval", rid(27))], results=results)
    assert code == 1 and out["reason"] == "account_above"


def test_reset_comes_from_the_newest_summary_and_rolls_forward(tmp_path):
    results = tmp_path / "results"
    ingest(results, "20260928T090000Z-000001")       # a run weeks ago; its meter says reset Nov 2
    code, out = decide(tmp_path, [], results=results, reset_at=None)
    assert out["window_start"] == "2026-10-26T17:00:00Z" and out["window_end"] == "2026-11-02T17:00:00Z"


def test_reset_falls_back_to_the_budget_weekday_when_no_summary_exists(tmp_path):
    code, out = decide(tmp_path, [], reset_at=None)
    assert out["window_start"] == "2026-10-26T17:00:00Z"            # budget fallback "mon 17:00"
    shifted = dict(BUDGET, weekly_reset_fallback="wed 13:00")        # Wed 12:00 is before 13:00
    assert decide(tmp_path, [], budget=shifted, reset_at=None)[1]["window_start"] == "2026-10-21T13:00:00Z"


# --- failing closed -------------------------------------------------------------

def run_cli(tmp_path, results, fires_text, budget_path=None, extra=()):
    fires = tmp_path / "f.json"
    if fires_text is not None:
        fires.write_text(fires_text)
    return g.main(["check", "--results", str(results), "--fires", str(fires),
                   "--budget", str(budget_path or ROOT / "evals" / "budget.yml"),
                   "--now", "2026-10-28T12:00:00Z", "--reset-at", RESETS, *extra])


def test_cli_exit_codes_and_numbers_only_output(tmp_path, capsys):
    results = tmp_path / "r"
    results.mkdir()
    assert run_cli(tmp_path, results, "[]") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["admit"] is True and set(out) >= {"used", "reservation", "weekly_cap", "reason"}
    full = json.dumps([fire("eval", rid(27, n)) for n in range(1, 11)])
    assert run_cli(tmp_path, results, full) == 1
    assert json.loads(capsys.readouterr().out)["admit"] is False


@pytest.mark.parametrize("fires_text", [
    None,                                                    # fires file missing
    "not json",
    '{"a": 1}',                                              # not a list
    json.dumps([{"name": "fire eval nonsense", "created_at": "2026-10-27T09:00:00Z", "fired": True}]),
    json.dumps([{"name": f"fire eval {rid(27)}", "created_at": "yesterday", "fired": True}]),
    json.dumps([{"name": f"fire eval {rid(27)}", "created_at": "2026-10-27T09:00:00Z"}]),
    json.dumps([{"name": f"fire deploy {rid(27)}", "created_at": "2026-10-27T09:00:00Z", "fired": True}]),
])
def test_unreadable_fires_fail_closed(tmp_path, capsys, fires_text):
    results = tmp_path / "r"
    results.mkdir()
    assert run_cli(tmp_path, results, fires_text) == 2
    assert json.loads(capsys.readouterr().out)["admit"] is False


def test_unreadable_results_fail_closed(tmp_path, capsys):
    results = tmp_path / "r"
    d = results / "routine-results" / rid(27)
    d.mkdir(parents=True)
    (d / "run.json").write_text("{broken")
    assert run_cli(tmp_path, results, json.dumps([fire("eval", rid(27))])) == 2
    (d / "run.json").write_text(json.dumps({"run_id": rid(28)}))             # wrong id for its directory
    assert run_cli(tmp_path, results, "[]") == 2
    (d / "run.json").write_text(json.dumps({"run_id": rid(27), "meter_after": {"seven_day": 7}}))
    assert run_cli(tmp_path, results, "[]") == 2
    (results / "routine-results" / "stray-dir").mkdir()
    assert run_cli(tmp_path, results, "[]") == 2


def test_bad_budget_fails_closed(tmp_path, capsys):
    results = tmp_path / "r"
    results.mkdir()
    bad = tmp_path / "b.yml"
    bad.write_text((ROOT / "evals" / "budget.yml").read_text().replace("improve_reservation: 0.12\n", ""))
    assert run_cli(tmp_path, results, "[]", budget_path=bad) == 2


def test_reset_subcommand_prints_the_window(tmp_path, capsys):
    results = tmp_path / "r"
    results.mkdir()
    assert g.main(["reset", "--results", str(results), "--budget", str(ROOT / "evals" / "budget.yml"),
                   "--now", "2026-10-28T12:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["window_start"] == "2026-10-26T17:00:00Z"


def test_scaffold_and_draft_fires_are_charged_the_run_cap(tmp_path):
    fires = [fire("scaffold", rid(27, 1)), fire("draft", rid(27, 2))]
    code, out = decide(tmp_path, fires, kind="draft")
    assert code == 0 and out["used"] == pytest.approx(0.06) and out["reservation"] == 0.03
    code, out = decide(tmp_path, fires, kind="scaffold")
    assert out["reservation"] == 0.03 and out["unresolved_fires"] == 2


def test_room_is_what_is_left_of_the_cap_and_zero_at_the_account_stop(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    f = tmp_path / "fires.json"
    f.write_text(json.dumps([fire("eval", rid(27, 1)), fire("improve", rid(27, 2))]))
    out = g.room(results, f, BUDGET, NOW, parse_instant(RESETS))
    assert out["room"] == pytest.approx(0.30 - 0.03 - 0.12)
    assert out["per_run_cap"] == 0.03 and out["improve_reservation"] == 0.12
    ingest(results, rid(27, 3), before=0.79, after=0.81)
    assert g.room(results, f, BUDGET, NOW, parse_instant(RESETS))["room"] == 0.0


def test_room_cli_prints_the_guard_file(tmp_path, capsys):
    (tmp_path / "r").mkdir()
    (tmp_path / "f.json").write_text("[]")
    assert g.main(["room", "--results", str(tmp_path / "r"), "--budget", str(ROOT / "evals" / "budget.yml"),
                   "--now", "2026-10-28T12:00:00Z", "--fires", str(tmp_path / "f.json"),
                   "--reset-at", RESETS]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["room"] == 0.30 and set(out) >= {"room", "per_run_cap", "improve_reservation"}
