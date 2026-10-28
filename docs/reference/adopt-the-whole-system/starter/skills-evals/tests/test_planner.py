import pytest
import os

import dispatch_fires as D
import plan_week as P

GUARD = {"room": 0.60, "per_run_cap": 0.03, "improve_reservation": 0.12}


def skill(name, **kw):
    return {"skill": name, "uses_4w": 10, "pass_rate": 0.5, "fixtures": 4,
            "open_improve_pr": False, "last_rejection": None, "consecutive_rejections": 0,
            "changed_since_rejection": False, **kw}


def cell(s, f, runs=0, last=None, bare=True, model="m1"):
    return {"skill": s, "fixture": f, "model": model, "runs": runs, "last_run": last, "bare_done": bare}


def state(skills=(), cells=(), removal=()):
    return {"now": "2026-10-28T12:00:00Z", "skills": list(skills), "cells": list(cells),
            "open_removal_skills": list(removal)}


def improve_skills(plan):
    return [f["skill"] for f in plan["fires"] if f["mode"] == "improve"]


def test_improve_is_ranked_by_uses_times_headroom_per_cost():
    st = state([skill("low", uses_4w=5), skill("high", uses_4w=50), skill("mid", uses_4w=20, pass_rate=0.8)])
    plan = P.plan(st, GUARD)
    assert improve_skills(plan) == ["high", "mid", "low"]   # 25, 4, 2.5 expected points
    values = {f["skill"]: f["value"] for f in plan["fires"]}
    assert values["high"] == pytest.approx(50 * 0.5 / 0.12, abs=1e-3)


def test_no_improve_at_90_percent_but_a_work_item():
    plan = P.plan(state([skill("done", pass_rate=0.90)]), GUARD)
    assert improve_skills(plan) == []
    assert {"kind": "needs-harder-fixtures", "skill": "done",
            "detail": "passing at least 90% of checks; no headroom"} in plan["work_items"]


def test_too_few_fixtures_is_a_work_item():
    plan = P.plan(state([skill("thin", fixtures=3)]), GUARD)
    assert improve_skills(plan) == []
    assert plan["work_items"][0]["kind"] == "write-fixtures"


def test_pauses():
    st = state([
        skill("open-pr", open_improve_pr=True),
        skill("recent", last_rejection="2026-10-20", consecutive_rejections=1),
        skill("stuck", last_rejection="2026-08-01", consecutive_rejections=3),
        skill("old-reject", last_rejection="2026-10-13", consecutive_rejections=1),   # 15 days
        skill("unstuck", last_rejection="2026-08-01", consecutive_rejections=3, changed_since_rejection=True),
    ])
    plan = P.plan(st, GUARD)
    assert {p["skill"]: p["reason"] for p in plan["paused"]} == {
        "open-pr": "improve PR open", "recent": "rejected within 14 days",
        "stuck": "3 rejections in a row, nothing changed"}
    assert sorted(improve_skills(plan)) == ["old-reject", "unstuck"]


def test_fourteen_day_boundary():
    on = P.pause_reason(skill("x", last_rejection="2026-10-14"), P._date("2026-10-28"))
    assert on is None   # exactly 14 days ago: the pause is over
    assert P.pause_reason(skill("x", last_rejection="2026-10-15"), P._date("2026-10-28"))


def test_bands_get_their_minimum_shares():
    cells = [cell("a", f"m{i}", runs=0, bare=False) for i in range(60)]
    cells += [cell("b", f"n{i}", runs=3) for i in range(30)]
    st = state([skill("a", uses_4w=100)], cells)
    guard = {"room": 0.40, "per_run_cap": 0.03, "improve_reservation": 0.12}
    plan = P.plan(st, guard)
    spent = {}
    for f in plan["fires"]:
        spent[f["band"]] = spent.get(f["band"], 0) + f["cost"]
    assert spent[2] >= 0.30 * 0.40 - 1e-9
    assert spent[3] >= 0.20 * 0.40 - 1e-9
    assert spent[4] >= 0.20 * 0.40 - 1e-9
    assert plan["spent"] <= 0.40 + 1e-9


def test_never_spends_more_than_the_room():
    cells = [cell("a", f"m{i}", runs=0, bare=False) for i in range(100)]
    for room in (0.0, 0.02, 0.05, 0.31):
        plan = P.plan(state([skill("a")], cells), {**GUARD, "room": room})
        assert sum(f["cost"] for f in plan["fires"]) <= room + 1e-9


def test_improve_not_funded_when_room_is_small():
    plan = P.plan(state([skill("a")], [cell("a", "x", runs=0)]), {**GUARD, "room": 0.10})
    assert improve_skills(plan) == []
    assert any(i["kind"] == "improve-not-funded" for i in plan["work_items"])


def test_safety_band_comes_first_and_covers_every_fixture():
    cells = [cell("gone", "a", runs=7), cell("gone", "b", runs=7), cell("x", "c", runs=0)]
    plan = P.plan(state([], cells, removal=["gone"]), GUARD)
    first = plan["fires"][:2]
    assert [f["band"] for f in first] == [1, 1]
    assert {f["fixture"] for f in first} == {"a", "b"}


def test_near_decision_orders_by_distance_to_next_look():
    cells = [cell("a", "far", runs=6), cell("a", "near", runs=9), cell("a", "mid", runs=3)]
    plan = P.plan(state([], cells), {**GUARD, "room": 0.09})
    assert [f["fixture"] for f in plan["fires"] if f["band"] == 3] == ["near", "mid", "far"]


def test_recheck_and_bare_baseline():
    cells = [cell("a", "old", runs=20, last="2026-08-01"), cell("a", "new", runs=20, last="2026-10-20"),
             cell("a", "nobare", runs=20, last="2026-10-20", bare=False)]
    plan = P.plan(state([], cells), GUARD)
    by = {(f["fixture"], f["band"]): f for f in plan["fires"]}
    assert ("old", 5) in by and ("new", 5) not in by
    assert by[("nobare", 6)]["arms"] == "bare"


def test_dispatch_keeps_three_in_flight_and_spaces_fires():
    fires = [{"mode": "eval", "skill": "a", "fixture": f"f{i}", "model": "m", "arms": "both", "trials": 3}
             for i in range(5)]
    state_ = {"flight": 0, "peak": 0, "sleeps": [], "sent": []}

    def in_flight():
        # one earlier fire finishes each time we are asked while full
        if state_["flight"] >= 3:
            state_["flight"] -= 1
            return 3
        return state_["flight"]

    def run(payload):
        state_["flight"] += 1
        state_["peak"] = max(state_["peak"], state_["flight"])
        state_["sent"].append(payload)

    ids = iter(f"20261028T120000Z-{i:06x}" for i in range(99))
    sent = D.dispatch(fires, in_flight=in_flight, run=run, sleep=state_["sleeps"].append,
                      run_id=lambda: next(ids))
    assert len(sent) == 5 and state_["peak"] <= 3
    assert state_["sleeps"].count(60) == 4
    assert sent[0] == {"mode": "eval", "fixture": "evals/a/f0", "model": "m", "arms": "both",
                       "trials": "3", "run_id": "20261028T120000Z-000000"}


def test_dispatch_never_retries_a_failed_fire():
    calls = []

    def run(payload):
        calls.append(payload)
        raise RuntimeError("lost response")
    try:
        D.dispatch([{"mode": "improve", "skill": "a"}] * 2, in_flight=lambda: 0, run=run,
                   sleep=lambda s: None, run_id=lambda: "20261028T120000Z-000000")
    except RuntimeError:
        pass
    assert len(calls) == 1


def test_improve_inputs():
    assert D.inputs_for({"mode": "improve", "skill": "a"}, "r") == {"mode": "improve", "skill": "a", "run_id": "r"}


def test_gh_dispatch_sends_one_dash_f_per_input(monkeypatch):
    calls = []
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: calls.append(argv))
    D.gh_dispatch({"mode": "improve", "skill": "a", "run_id": "r"})
    assert calls == [["gh", "workflow", "run", "routine-eval-fire.yml",
                      "-f", "mode=improve", "-f", "skill=a", "-f", "run_id=r"]]
    assert not any(a.startswith("payload=") for a in calls[0])


def test_budget_file_has_the_reservation():
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert yaml.safe_load(open(os.path.join(root, "evals", "budget.yml")))["improve_reservation"] == 0.12
