#!/usr/bin/env python3
"""plan_week.py: the weekly plan, as JSON. Plans only; dispatch_fires.py fires.

    plan_week.py --state state.json --guard guard.json [--budget evals/budget.yml]

guard.json is allowance_guard.py's result: {"room": 0.21, "per_run_cap": 0.03,
"improve_reservation": 0.12}. "room" is the fraction of the weekly allowance
still free; the plan never spends more than that.

state.json (built by the workflow from the results branch and the API):
{
  "now": "2026-10-28T12:00:00Z",
  "open_removal_skills": ["skill-a"],
  "skills": [{"skill": "skill-a", "uses_4w": 40, "pass_rate": 0.55, "fixtures": 4,
              "open_improve_pr": false, "last_rejection": "2026-10-01" | null,
              "consecutive_rejections": 0, "changed_since_rejection": false}],
  "cells":  [{"skill": "skill-a", "fixture": "basic", "model": "m1", "runs": 3,
              "last_run": "2026-10-20" | null, "bare_done": false}]
}

Bands (money = fractions of the allowance; min shares are of the room):
 1 safety (all fixtures of a skill with an open removal PR; first, uncapped
   except by the room)  2 missing evidence >= 30%  3 near a decision >= 20%
 4 improvements >= 20%  5 re-checks (not measured for 8 weeks)  6 bare baselines
Pass A fills bands 4, 2, 3 up to their minimum (an item may overshoot its
minimum by itself, so an expensive improve fire still fits). Pass B spends the
rest in band order 2, 3, 4, 5, 6.
"""
import argparse
import datetime as dt
import json
import sys

LOOKS = (5, 10, 20)
MIN_SHARE = {2: 0.30, 3: 0.20, 4: 0.20}
HEADROOM_CEILING = 0.90
IMPROVE_MIN_FIXTURES = 4
PAUSE_DAYS_AFTER_REJECTION = 14
PAUSE_AFTER_REJECTIONS_IN_A_ROW = 3
RECHECK_DAYS = 56
TRIALS = 3


def _date(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def pause_reason(sk, today):
    if sk.get("open_improve_pr"):
        return "improve PR open"
    if sk.get("consecutive_rejections", 0) >= PAUSE_AFTER_REJECTIONS_IN_A_ROW \
            and not sk.get("changed_since_rejection"):
        return "3 rejections in a row, nothing changed"
    last = sk.get("last_rejection")
    if last and (today - _date(last)).days < PAUSE_DAYS_AFTER_REJECTION:
        return "rejected within 14 days"
    return None


def _eval_fire(band, c, arms="both"):
    return {"band": band, "mode": "eval", "skill": c["skill"], "fixture": c["fixture"],
            "model": c["model"], "arms": arms, "trials": TRIALS}


def build_candidates(state, today, improve_cost):
    """Return (candidates by band, work_items, paused). Each candidate is
    (cost-bearing fire dict, rank key); lists are already in rank order."""
    cells = state.get("cells", [])
    removal = set(state.get("open_removal_skills", []))
    cand = {1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    items, paused = [], []
    for c in cells:
        if c["skill"] in removal:
            cand[1].append(_eval_fire(1, c))
    for c in sorted(cells, key=lambda c: (c["skill"], c["fixture"], c["model"])):
        if c["skill"] in removal:
            continue
        runs, last = c.get("runs", 0), c.get("last_run")
        if runs == 0:
            cand[2].append(_eval_fire(2, c))
        else:
            nxt = [k for k in LOOKS if k > runs]
            if nxt:
                cand[3].append((nxt[0] - runs, _eval_fire(3, c)))
            if last and (today - _date(last)).days >= RECHECK_DAYS:
                cand[5].append(_eval_fire(5, c))
        if not c.get("bare_done"):
            cand[6].append(_eval_fire(6, c, arms="bare"))
    cand[3] = [f for _, f in sorted(cand[3], key=lambda t: t[0])]
    ranked = []
    for sk in sorted(state.get("skills", []), key=lambda s: s["skill"]):
        name = sk["skill"]
        if sk["pass_rate"] >= HEADROOM_CEILING:
            items.append({"kind": "needs-harder-fixtures", "skill": name,
                          "detail": "passing at least 90% of checks; no headroom"})
            continue
        if sk.get("fixtures", 0) < IMPROVE_MIN_FIXTURES:
            items.append({"kind": "write-fixtures", "skill": name,
                          "detail": f"improve mode needs {IMPROVE_MIN_FIXTURES} fixtures"})
            continue
        why = pause_reason(sk, today)
        if why:
            paused.append({"skill": name, "reason": why})
            continue
        value = sk.get("uses_4w", 0) * (1 - sk["pass_rate"]) / improve_cost
        ranked.append((-value, name, {"band": 4, "mode": "improve", "skill": name,
                                      "value": round(value, 4)}))
    cand[4] = [f for _, _, f in sorted(ranked, key=lambda t: (t[0], t[1]))]
    return cand, items, paused


def plan(state, guard):
    today = _date(state["now"])
    eval_cost = guard["per_run_cap"]
    improve_cost = guard.get("improve_reservation", 12 * eval_cost)
    room = guard["room"]
    cand, items, paused = build_candidates(state, today, improve_cost)
    cost = lambda f: improve_cost if f["mode"] == "improve" else eval_cost  # noqa: E731
    fires, taken, remaining = [], set(), room
    spent = {b: 0.0 for b in range(1, 7)}

    def key(f):
        return (f["mode"], f["skill"], f.get("fixture"), f.get("model"), f.get("arms"))

    def take(f, band):
        nonlocal remaining
        c = cost(f)
        k = key(f)
        if k in taken or c > remaining + 1e-9:
            return False
        taken.add(k)
        fires.append({**f, "band": band, "cost": c})
        remaining -= c
        spent[band] += c
        return True

    for f in cand[1]:
        take(f, 1)
    for band in (4, 2, 3):
        for f in cand[band]:
            if spent[band] >= MIN_SHARE[band] * room - 1e-9:
                break
            take(f, band)
    for band in (2, 3, 4, 5, 6):
        for f in cand[band]:
            take(f, band)
    skipped_improve = [f["skill"] for f in cand[4] if key(f) not in taken]
    for s in skipped_improve:
        items.append({"kind": "improve-not-funded", "skill": s,
                      "detail": "the room left did not cover an improve reservation"})
    return {"now": state["now"], "room": room, "spent": round(room - remaining, 6),
            "fires": fires, "work_items": items, "paused": paused}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--guard", required=True)
    a = ap.parse_args(argv)
    with open(a.state, encoding="utf-8") as f:
        state = json.load(f)
    with open(a.guard, encoding="utf-8") as f:
        guard = json.load(f)
    json.dump(plan(state, guard), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
