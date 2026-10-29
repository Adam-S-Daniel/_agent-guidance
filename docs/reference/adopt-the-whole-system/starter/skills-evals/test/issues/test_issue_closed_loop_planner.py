#!/usr/bin/env python3
"""G5/G6: the weekly planner (scripts/plan_week.py), improve outcomes
derived from existing evidence (scripts/improve_outcomes.py), and the
allowance guard's improve charge (thresholds doc, plumbing gap 2).

Hermetic: GitHub rows are recorded dicts, `now` is passed in, sleeps and
clocks are injected. Workflows are parsed with PyYAML.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import example_evals  # noqa: E402

# The planner reads a checkout's committed fixtures and schedule; the kit
# ships none (its DECISIONS X9), so these tests read the example checkout
# (test/example_evals.py): this repository plus invented fixtures.
ROOT = example_evals.checkout()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "harness"))

import allowance_guard as guard  # noqa: E402
import improve_outcomes as io_mod  # noqa: E402
import compute_gates as plan_week_compute_gates  # noqa: E402
import plan_week  # noqa: E402

NOW = datetime(2026, 10, 27, 7, 0, 0, tzinfo=timezone.utc)
LIMITS = {"weekly_cap": 0.30, "per_run_cap": 0.03, "per_run_overrun": 0.005,
          "stop_when_account_above": 0.80}


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fire(run_id, title, started, seconds=40, conclusion="success", status="completed"):
    """One row of the workflow runs API, as recorded."""
    return {"id": run_id, "display_title": title, "status": status,
            "conclusion": conclusion, "run_started_at": iso(started),
            "created_at": iso(started), "updated_at": iso(started + timedelta(seconds=seconds))}


def stamp(moment):
    return moment.strftime("%Y%m%dT%H%M%SZ") + "-abc123"


#: routine-eval-fire.yml's one conditional in `run-name`: ` bare` after an
#: eval mode whose `arms` is bare (ADR 0012, bare baseline addendum).
BARE_EXPRESSION = "${{ inputs.mode == 'eval' && inputs.arms == 'bare' && ' bare' || '' }}"


def render_title(template, **inputs):
    """The fire workflow's `run-name` as GitHub renders it for `inputs`:
    the bare conditional, then each `${{ inputs.<name> }}`."""
    bare = " bare" if inputs.get("mode") == "eval" and inputs.get("arms") == "bare" else ""
    text = template.replace(BARE_EXPRESSION, bare)
    return re.sub(r"\$\{\{ inputs\.(\w+) \}\}", lambda m: inputs.get(m.group(1), ""), text)


class TitleAndOutcomeTests(unittest.TestCase):

    def test_titles_parse_strictly(self):
        self.assertEqual(io_mod.parse_title("Fire eval routine: improve writing-adrs "),
                         {"mode": "improve", "target": "writing-adrs", "model": None,
                          "bare": False})
        self.assertEqual(io_mod.parse_title(
            "Fire eval routine: eval evals/writing-adrs/bootstrap claude-opus-5-5"),
            {"mode": "eval", "target": "evals/writing-adrs/bootstrap",
             "model": "claude-opus-5-5", "bare": False})
        self.assertEqual(io_mod.parse_title(
            "Fire eval routine: eval bare evals/writing-adrs/bootstrap claude-opus-5-5"),
            {"mode": "eval", "target": "evals/writing-adrs/bootstrap",
             "model": "claude-opus-5-5", "bare": True})
        # A skill named `bare` is still a skill, not a marker.
        self.assertEqual(io_mod.parse_title("Fire eval routine: improve bare ")["target"],
                         "bare")
        for bad in ("Fire eval routine", "Fire eval routine: improve ",
                    "Fire eval routine: deploy x", "Fire eval routine: improve a;b",
                    "Fire eval routine: improve x y z", None, 3,
                    # ` bare` belongs to eval mode only.
                    "Fire eval routine: improve bare writing-adrs ",
                    "Fire eval routine: scaffold bare a__b__1 ",
                    "Fire eval routine: eval bare  ", "Fire eval routine: evalbare evals/x m"):
            self.assertIsNone(io_mod.parse_title(bad), bad)

    def test_the_fire_workflow_renders_the_title_the_parser_reads(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/routine-eval-fire.yml").read_text())
        template = doc["run-name"]
        self.assertIn(BARE_EXPRESSION, template)

        def render(**inputs):
            inputs.setdefault("arms", "both")
            return render_title(template, **inputs)
        self.assertNotIn("${{", render(mode="eval"))
        self.assertEqual(io_mod.parse_title(render(mode="improve", skill="writing-adrs")),
                         {"mode": "improve", "target": "writing-adrs", "model": None,
                          "bare": False})
        self.assertEqual(io_mod.parse_title(render(mode="eval", fixture="evals/x",
                                                   model="claude-opus-5-5"))["model"],
                         "claude-opus-5-5")
        self.assertEqual(io_mod.parse_title(render(mode="scaffold", candidate="a__b__1"))["mode"],
                         "scaffold")
        self.assertEqual(io_mod.parse_title(render(mode="eval", arms="bare", fixture="evals/x",
                                                   model="claude-opus-5-5")),
                         {"mode": "eval", "target": "evals/x", "model": "claude-opus-5-5",
                          "bare": True})
        for arms in ("with", "without"):
            self.assertFalse(io_mod.parse_title(render(mode="eval", arms=arms,
                                                       fixture="evals/x"))["bare"])
        # The workflow refuses bare outside eval mode; its title never says so.
        self.assertEqual(io_mod.parse_title(render(mode="improve", arms="bare",
                                                   skill="writing-adrs"))["bare"], False)

    def test_outcomes_from_fires_and_branches(self):
        t = NOW - timedelta(days=3)
        runs = [fire(1, "Fire eval routine: improve writing-adrs ", t),
                fire(2, "Fire eval routine: improve skills-doctor ", t + timedelta(hours=1)),
                fire(3, "Fire eval routine: improve writing-adrs ", t + timedelta(hours=2),
                     conclusion="failure"),
                fire(4, "Fire eval routine: improve writing-adrs ", NOW - timedelta(hours=1)),
                fire(5, "Fire eval routine: improve writing-adrs ", NOW - timedelta(minutes=1),
                     conclusion=None, status="in_progress"),
                fire(6, "Fire eval routine: eval evals/x claude-opus-5-5", t)]
        branches = [{"run_id": stamp(t + timedelta(seconds=10)), "skill": "writing-adrs"},
                    # A branch whose summary names another skill matches nothing.
                    {"run_id": stamp(t + timedelta(hours=1, seconds=5)), "skill": "other"},
                    {"run_id": "not-a-run-id", "skill": "skills-doctor"}]
        got = [(r["fire_run"], r["outcome"]) for r in io_mod.outcomes(runs, branches, NOW)]
        self.assertEqual(got, [(1, "accepted"), (2, "rejected"), (3, "fire-failed"),
                               (4, "pending"), (5, "pending")])

    def test_an_unreadable_branch_skill_matches_only_a_unique_span(self):
        t = NOW - timedelta(days=1)
        runs = [fire(1, "Fire eval routine: improve a ", t, seconds=120),
                fire(2, "Fire eval routine: improve b ", t + timedelta(seconds=30))]
        branch = [{"run_id": stamp(t + timedelta(seconds=40)), "skill": None}]
        self.assertEqual({r["outcome"] for r in io_mod.outcomes(runs, branch, NOW)},
                         {"rejected"})
        # Fire 2's span (plus slack) ends at t + 2m40s; fire 1's at t + 4m.
        branch = [{"run_id": stamp(t + timedelta(seconds=200)), "skill": None}]
        self.assertEqual([r["outcome"] for r in io_mod.outcomes(runs, branch, NOW)],
                         ["accepted", "rejected"])

    def test_fetchers_page_and_read_branch_summaries_as_data(self):
        pages = {1: [fire(i, "t", NOW) for i in range(100)], 2: [fire(100, "t", NOW)]}
        seen = []

        def get(path, token, **kw):
            seen.append(path)
            return {"workflow_runs": pages[int(path.rsplit("page=", 1)[1])]}
        self.assertEqual(len(io_mod.fetch_fire_runs("example-adopter/skills-evals", "t",
                                                    NOW, get=get)), 101)
        self.assertIn("event=workflow_dispatch", seen[0])
        with self.assertRaises(io_mod.EvidenceError):
            io_mod.fetch_fire_runs("example-adopter/skills-evals", "t", NOW,
                                   get=lambda p, t, **k: {"workflow_runs": [{}] * 100})
        good, evil = stamp(NOW), stamp(NOW - timedelta(hours=1))

        def refs(path, token, raw=False, cap=None):
            if "matching-refs" in path:
                return [{"ref": f"refs/heads/claude/eval-improve-{good}"},
                        {"ref": f"refs/heads/claude/eval-improve-{evil}"},
                        {"ref": "refs/heads/claude/eval-improve-junk"}]
            return (b'{"skill": "writing-adrs"}' if good in path
                    else b'{"skill": "$(rm -rf /)"}')
        self.assertEqual(io_mod.fetch_improve_branches("example-adopter/skills-evals", "t", refs),
                         [{"run_id": good, "skill": "writing-adrs"},
                          {"run_id": evil, "skill": None}])

    def test_github_get_never_prints_a_body(self):
        def failing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 502, "Bad", {},
                                         io.BytesIO(b"private-body-marker"))
        with self.assertRaises(io_mod.EvidenceError) as caught:
            io_mod.github_get("/x", "t", opener=failing)
        self.assertEqual(str(caught.exception), "the GitHub API answered 502")

        def missing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "No", {}, io.BytesIO(b""))
        self.assertIsNone(io_mod.github_get("/x", "t", opener=missing))


def measured_summary(run_dir: Path, delta: float, name="k/20261019T100000Z/with_skill"):
    path = run_dir / name / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"usage": {"meter_delta": {"windows": {"seven_day": {
        "delta": delta, "window_reset": False}}}, "invocation": name}}))


class GuardImproveChargeTests(unittest.TestCase):

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)

    def test_unmeasured_history_charges_full_reservations(self):
        self.assertAlmostEqual(guard.eval_fire_cost(self.tree, LIMITS), 0.035)
        self.assertAlmostEqual(guard.improve_charge(self.tree, LIMITS),
                               guard.IMPROVE_FIRE_UNITS * 0.035)

    def test_measured_history_sets_the_charge(self):
        old = self.tree / "routine-results" / "20260901T000000Z-aaaaaa"
        measured_summary(old, 0.01)
        new = self.tree / "routine-results" / "20261026T000000Z-bbbbbb"
        measured_summary(new, 0.0)
        # (0.01 + 0.005 + 0.0 + 0.005) / 2 = 0.01 per eval run.
        self.assertAlmostEqual(guard.eval_fire_cost(self.tree, LIMITS), 0.01)
        self.assertAlmostEqual(guard.improve_charge(self.tree, LIMITS), 0.12)

    def test_improve_fires_are_held_all_week_and_not_matched_to_runs(self):
        state = guard.account(self.tree, LIMITS, NOW, (3, 0), (2, 0))
        charge = guard.improve_charge(self.tree, LIMITS)
        # One eval fire without results, past the hold: the overrun only.
        self.assertAlmostEqual(state["pending"], 0.005 + 2 * charge)
        self.assertEqual(state["fires"], 1)
        self.assertIn("2 improve fire(s) held at", guard.describe(state))
        # Without the improve counts the same fires looked like cheap evals.
        self.assertAlmostEqual(guard.account(self.tree, LIMITS, NOW, (3, 0))["pending"], 0.015)

    def test_check_needs_room_for_the_improve_charge(self):
        state = guard.account(self.tree, LIMITS, NOW, (0, 0))
        self.assertTrue(guard.check(state, "eval"))
        self.assertFalse(guard.check(state, "improve"))  # 0.42 > 0.30
        roomy = dict(state, cap=0.50)
        self.assertTrue(guard.check(roomy, "improve"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            budget_path = self.tree / "budget.yml"
            budget_path.write_text("weekly_cap: 0.30\nper_run_cap: 0.03\n"
                                   "per_run_overrun: 0.005\nstop_when_account_above: 0.80\n")
            code = guard.main(["check", "--results-tree", str(self.tree), "--mode", "improve",
                               "--budget", str(budget_path)],
                              now=NOW, fire_count=lambda now: (0, 0),
                              improve_count=lambda now: (0, 0))
        self.assertEqual(code, 1)
        self.assertIn(guard.REFUSAL, out.getvalue())

    def test_improve_counts_read_titles(self):
        runs = [fire(1, "Fire eval routine: improve a ", NOW - timedelta(minutes=30)),
                fire(2, "Fire eval routine: improve a ", NOW - timedelta(days=3)),
                fire(3, "Fire eval routine: improve a ", NOW - timedelta(days=9)),
                fire(4, "Fire eval routine: improve a ", NOW, conclusion="failure"),
                fire(5, "Fire eval routine: eval evals/x m", NOW)]
        self.assertEqual(guard.improve_counts(runs, NOW), (2, 1))
        self.assertCountEqual(guard.improve_starts(runs, NOW),
                              [NOW - timedelta(minutes=30), NOW - timedelta(days=3)])


def rec(skill, outcome, at):
    return {"skill": skill, "fire_run": 1, "at": iso(at), "outcome": outcome, "run_id": None}


class PauseTests(unittest.TestCase):

    def pause(self, records=(), prs=(), on_hold=(), changed=None, remeasured=False):
        return plan_week.pause("writing-adrs", list(records), list(prs), list(on_hold),
                               NOW, changed, lambda moment: remeasured)

    def test_a_clean_skill_is_not_paused(self):
        self.assertIsNone(self.pause())

    def test_open_pr_on_hold_and_in_flight(self):
        self.assertIn("pull request is open", self.pause(prs=[{
            "skill": "writing-adrs", "state": "open", "created_at": iso(NOW),
            "closed_at": None, "merged_at": None}]))
        self.assertIn("on-hold", self.pause(on_hold=["Pause `writing-adrs` for now"]))
        self.assertIsNone(self.pause(on_hold=["writing-adrs-extra is held", None]))
        self.assertIn("in flight", self.pause([rec("writing-adrs", "pending", NOW)]))

    def test_a_rejection_cools_down_for_14_days(self):
        self.assertIn("cooling down", self.pause([rec("writing-adrs", "rejected",
                                                      NOW - timedelta(days=13))]))
        self.assertIsNone(self.pause([rec("writing-adrs", "rejected",
                                          NOW - timedelta(days=15))]))
        self.assertIsNone(self.pause([rec("other", "rejected", NOW)]))

    def test_three_rejections_stop_until_a_change_or_90_days(self):
        three = [rec("writing-adrs", "rejected", NOW - timedelta(days=d)) for d in (60, 40, 20)]
        self.assertIn("3 rejections in a row", self.pause(three))
        self.assertIsNone(self.pause(three, changed=NOW - timedelta(days=1)))
        self.assertIn("3 rejections", self.pause(three, changed=NOW - timedelta(days=30)))
        old = [rec("writing-adrs", "rejected", NOW - timedelta(days=d)) for d in (130, 110, 91)]
        self.assertIsNone(self.pause(old))
        # Failed fires between them do not break the streak.
        mixed = three[:2] + [rec("writing-adrs", "fire-failed", NOW - timedelta(days=30))] \
            + three[2:]
        self.assertIn("3 rejections", self.pause(mixed))

    def test_a_guidance_section_is_paused_by_its_own_records_and_prs(self):
        def srec(section, outcome, at):
            return {"section": section, "fire_run": 1, "at": iso(at), "outcome": outcome,
                    "run_id": None}

        def spause(records=(), prs=()):
            return plan_week.pause("sec", list(records), list(prs), [], NOW, None,
                                   lambda moment: False, key="section")
        open_pr = {"section": "sec", "state": "open", "created_at": iso(NOW),
                   "closed_at": None, "merged_at": None}
        self.assertIsNone(spause())
        self.assertIn("pull request is open", spause(prs=[open_pr]))
        self.assertIn("in flight", spause([srec("sec", "pending", NOW)]))
        self.assertIn("cooling down", spause([srec("sec", "rejected",
                                                   NOW - timedelta(days=3))]))
        three = [srec("sec", "rejected", NOW - timedelta(days=d)) for d in (60, 40, 20)]
        self.assertIn("3 rejections in a row", spause(three))
        self.assertIsNone(spause([srec("other", "pending", NOW)]))

    def test_the_guidance_lane_holds_one_guidance_edit_at_a_time(self):
        lane = plan_week.guidance_lane
        other_pr = {"section": "other", "state": "open"}
        self.assertIsNone(lane("sec", [], [], []))
        self.assertIsNone(lane("sec", [], [{**other_pr, "state": "closed"}], []))
        self.assertIn("another guidance-loop pull request", lane("sec", [], [other_pr], []))
        self.assertIn("another guidance-loop pull request", lane("sec", [], [], ["other"]))
        self.assertIn("removal pull request for it", lane("sec", [], [], ["sec"]))
        self.assertIn("another guidance-improve attempt",
                      lane("sec", [{"section": "other", "outcome": "pending"}], [], []))
        self.assertIsNone(lane("sec", [{"section": "other", "outcome": "rejected"}], [], []))

    def test_infrastructure_failures_retry_twice(self):
        two = [rec("writing-adrs", "fire-failed", NOW - timedelta(hours=h)) for h in (3, 2)]
        self.assertIsNone(self.pause(two))
        three = two + [rec("writing-adrs", "fire-failed", NOW - timedelta(hours=1))]
        self.assertIn("failed 3 times", self.pause(three))

    def test_after_a_merge_wait_for_the_with_arm(self):
        accepted = [rec("writing-adrs", "accepted", NOW - timedelta(days=5))]
        pr = {"skill": "writing-adrs", "state": "closed", "created_at": iso(NOW - timedelta(days=5)),
              "closed_at": iso(NOW - timedelta(days=4)),
              "merged_at": iso(NOW - timedelta(days=4))}
        self.assertIn("re-measured", self.pause(accepted, [pr]))
        self.assertIsNone(self.pause(accepted, [pr], remeasured=True))
        self.assertIn("no registry pull request", self.pause(accepted))
        closed = dict(pr, merged_at=None)
        self.assertIn("cooling down", self.pause(accepted, [closed]))


def trial(checks, infra=False):
    return {"scored": True, "infrastructure": infra, "errored": False, "checks": checks}


class TrainEvidenceTests(unittest.TestCase):

    def test_headroom_and_targets(self):
        with_trials = [trial({"a": True, "b": i % 2 == 0}) for i in range(6)]
        cells = {("one", "m"): {"with": [{"timestamp": "20261001T000000Z", "trials": with_trials}],
                                "without": []},
                 ("holdout", "m"): {"with": [{"timestamp": "x", "trials": [trial({"z": False})] * 9}],
                                    "without": []}}
        got = plan_week.train_evidence(cells, ["one"])
        self.assertEqual(got["with_trials"], 6)
        self.assertAlmostEqual(got["headroom"], 0.25)
        self.assertEqual(got["targets"], ["one:b"])
        # Five trials are too few; a check that never passed anywhere is broken.
        cells[("one", "m")]["with"][0]["trials"] = with_trials[:5]
        self.assertEqual(plan_week.train_evidence(cells, ["one"])["targets"], [])
        never = [trial({"a": True, "c": False}) for _ in range(6)]
        cells[("one", "m")]["with"][0]["trials"] = never
        self.assertEqual(plan_week.train_evidence(cells, ["one"])["targets"], [])
        cells[("one", "m")]["without"] = [{"timestamp": "x", "trials": [trial({"c": True})]}]
        self.assertEqual(plan_week.train_evidence(cells, ["one"])["targets"], ["one:c"])
        cells[("one", "m")]["with"][0]["trials"] = [trial({"c": False}, infra=True)] * 9
        self.assertIsNone(plan_week.train_evidence(cells, ["one"])["headroom"])

    def test_with_run_after(self):
        cells = {("a", "m"): {"with": [{"timestamp": "20261020T000000Z"}], "without": []}}
        self.assertTrue(plan_week.with_run_after(cells, NOW - timedelta(days=8)))
        self.assertFalse(plan_week.with_run_after(cells, NOW - timedelta(days=6)))


POLICY = {"min_runs": 5, "retire_looks": [5, 10, 20]}


def cell(fixture, model, cls, runs=0, newest=None, usd=None, empty=0, skill=None):
    return {"skill": skill or fixture.split("/")[1], "fixture": fixture, "model": model,
            "class": cls, "with_runs": runs, "without_runs": runs, "newest": newest,
            "usd_per_fire": usd, "empty_fires": empty}


def skill(fixtures=4, headroom=0.3, targets=("one:b",), verdict="keep", paused=None,
          registry_ok=True):
    return {"fixtures": fixtures, "holdout": "aaa" if fixtures >= 4 else "",
            "registry_ok": registry_ok, "verdict": verdict, "pause": paused,
            "evidence": {"headroom": headroom, "pass_rate": None, "with_trials": 6,
                         "targets": list(targets)}}


def ctx(**over):
    base = {"policy": POLICY, "reservation": 0.035, "overrun": 0.005,
            "improve_charge": 0.10, "room": 0.30, "points_per_usd": None,
            "model_weights": {"heavy": 0.9, "light": 0.1}, "removal_prs": [],
            "cells": [], "skills": {}, "lessons": []}
    base.update(over)
    return base


class BandTests(unittest.TestCase):

    def test_safety_first_then_minimum_shares_then_leftovers(self):
        cells = [cell(f"evals/m{i}", "heavy", "missing") for i in range(8)] \
            + [cell(f"evals/n{i}", "heavy", "insufficient", runs=4 - i % 3) for i in range(6)] \
            + [cell("evals/r0", "heavy", "benefit", runs=9, newest="20260801T000000Z")] \
            + [cell("evals/r1", "heavy", "benefit", runs=9, newest="20261020T000000Z")] \
            + [cell("evals/s0/x", "light", "benefit", runs=9, skill="gone")]
        plan = plan_week.build_plan(ctx(cells=cells, removal_prs=["gone"], room=1.0,
                                        skills={"writing-adrs": skill()}), NOW)
        bands = [f["band"] for f in plan["fires"]]
        self.assertEqual(bands[0], "safety")
        self.assertEqual(bands[-1], "improve")
        self.assertEqual(plan["fires"][-1]["mode"], "improve")
        self.assertEqual(plan["fires"][-1]["holdout"], "aaa")
        self.assertEqual(plan["fires"][-1]["change_type"], "skill-edit")
        # 1.0 - 0.035 safety = 0.965: missing gets at least 30% (8 x 0.035 =
        # 0.28 fits), near 20% (5 of 6), improve 20% (one 0.10 fire), and the
        # rest goes in band order: the sixth near cell, then the old re-check.
        self.assertEqual(bands.count("missing"), 8)
        self.assertEqual(bands.count("near"), 6)
        self.assertEqual(bands.count("improve"), 1)
        self.assertEqual(bands.count("recheck"), 1)
        self.assertNotIn("evals/r1", [f["fixture"] for f in plan["fires"]])
        self.assertLessEqual(sum(f["cost"] for f in plan["fires"]), 1.0 + 1e-9)
        near = [f["fixture"] for f in plan["fires"] if f["band"] == "near"]
        # Runs 4 are one short of the 5-run look, so they lead.
        self.assertEqual(near[:2], ["evals/n0", "evals/n3"])
        self.assertEqual(plan["matrix"]["include"][0]["eval_key"], "s0/x")

    def test_a_tight_room_still_reserves_each_band_its_share(self):
        cells = [cell(f"evals/m{i}", "heavy", "missing") for i in range(20)] \
            + [cell(f"evals/n{i}", "heavy", "insufficient", runs=2) for i in range(20)]
        plan = plan_week.build_plan(ctx(cells=cells, room=0.35), NOW)
        bands = [f["band"] for f in plan["fires"]]
        # 30% of 0.35 holds 3 missing fires, 20% holds 2 near; the rest (0.175,
        # 5 fires) goes to missing first.
        self.assertEqual(bands.count("near"), 2)
        self.assertEqual(bands.count("missing"), 8)

    def test_missing_cells_go_heaviest_model_first_and_broken_ones_leave(self):
        cells = [cell("evals/a", "light", "missing"), cell("evals/a", "heavy", "missing"),
                 cell("evals/b", "heavy", "missing", empty=2)]
        plan = plan_week.build_plan(ctx(cells=cells), NOW)
        self.assertEqual([(f["fixture"], f["model"]) for f in plan["fires"]],
                         [("evals/a", "heavy"), ("evals/a", "light")])
        self.assertEqual(plan["work"], [{"kind": "fixture-broken", "change_type": "fixture",
                                         "skill": "b", "fixture": "evals/b",
                                         "model": "heavy", "route": "issue"}])

    def test_historical_cost_is_clamped_and_breaks_ties(self):
        c = ctx(points_per_usd=0.01)
        self.assertAlmostEqual(plan_week.fire_cost({"usd_per_fire": 2.0}, c), 0.02)
        self.assertAlmostEqual(plan_week.fire_cost({"usd_per_fire": 100.0}, c), 0.035)
        self.assertAlmostEqual(plan_week.fire_cost({"usd_per_fire": 0.0}, c), 0.005)
        self.assertAlmostEqual(plan_week.fire_cost({"usd_per_fire": None}, c), 0.035)
        cells = [cell("evals/a", "heavy", "missing", usd=3.0),
                 cell("evals/b", "heavy", "missing", usd=1.0)]
        plan = plan_week.build_plan(ctx(cells=cells, points_per_usd=0.01), NOW)
        self.assertEqual([f["fixture"] for f in plan["fires"]], ["evals/b", "evals/a"])

    def test_max_fires_caps_the_plan(self):
        cells = [cell(f"evals/m{i}", "heavy", "missing") for i in range(40)]
        plan = plan_week.build_plan(ctx(cells=cells, room=10.0), NOW, max_fires=30)
        self.assertEqual(len(plan["fires"]), 30)
        self.assertEqual(plan["unplanned"]["missing"], 10)

    def test_improve_eligibility_and_needs_fixtures(self):
        skills = {"ok": skill(), "few": skill(fixtures=2, headroom=0.5),
                  "few-flat": skill(fixtures=1, headroom=0.05),
                  "flat": skill(headroom=0.05), "aimless": skill(targets=()),
                  "retiring": skill(verdict="retire-pending"),
                  "paused": skill(paused="cooling down after a rejection"),
                  "private": skill(registry_ok=False)}
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0), NOW)
        self.assertEqual([f["skill"] for f in plan["fires"]], ["ok"])
        self.assertEqual(sorted((w["kind"], w["skill"], w["change_type"]) for w in plan["work"]),
                         [("needs-fixtures", "few", "fixture"),
                          ("needs-harder-fixtures", "few-flat", "fixture"),
                          ("needs-harder-fixtures", "flat", "fixture")])
        status = plan["skills"]
        self.assertEqual(status["paused"]["reasons"], ["cooling down after a rejection"])
        self.assertIn("owned by retirement (retire-pending)", status["retiring"]["reasons"])
        self.assertIn("no failing check to target", status["aimless"]["reasons"])
        self.assertIn("no train headroom", status["flat"]["reasons"])

    def test_a_skill_above_ninety_percent_wants_harder_fixtures_not_an_edit(self):
        skills = {"high": skill(headroom=0.05), "eligible": skill(headroom=0.3),
                  "retiring": skill(headroom=0.05, verdict="retire"),
                  "private": skill(headroom=0.05, registry_ok=False)}
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0), NOW)
        self.assertEqual([f["skill"] for f in plan["fires"]], ["eligible"])
        self.assertEqual(plan["work"], [{
            "kind": "needs-harder-fixtures", "change_type": "fixture", "skill": "high",
            "fixtures": 4, "headroom": 0.05, "route": "issue"}])

    def test_skill_usage_weight_orders_improvements_unknown_is_neutral(self):
        skills = {"a": skill(headroom=0.5), "b": skill(headroom=0.3), "c": skill(headroom=0.2),
                  "d": skill(fixtures=1, headroom=0.9), "e": skill(fixtures=1, headroom=0.4)}
        weights = {"a": 1.0, "b": None, "c": 50.0, "d": None, "e": 10.0}
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0, improve_charge=0.01,
                                        skill_weights=weights), NOW)
        # Known 1, 10, 50: the neutral weight is their median, 10, never 0.
        self.assertEqual([f["skill"] for f in plan["fires"]], ["c", "b", "a"])
        self.assertEqual([w["skill"] for w in plan["work"]], ["d", "e"])
        self.assertEqual(plan_week.neutral_weights({"x": None}), {"x": 1.0})
        self.assertEqual(plan_week.neutral_weights({"x": None, "y": 0, "z": 0, "w": 4}),
                         {"x": 4, "y": 0, "z": 0, "w": 4})

    def test_skill_weights_use_the_usage_module_or_stay_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            with mock.patch.dict(sys.modules, {"skill_usage": None}):
                self.assertEqual(plan_week.skill_weights(tree, ["a"], NOW), {"a": None})
            seen = []
            fake = mock.Mock()
            fake.load.side_effect = lambda path: seen.append(path) or {"doc": 1}
            fake.weight.side_effect = lambda doc, skill, now: {"a": 7, "b": 0}.get(skill)
            with mock.patch.dict(sys.modules, {"skill_usage": fake}):
                self.assertEqual(plan_week.skill_weights(tree, ["a", "b", "c"], NOW),
                                 {"a": 7, "b": 0, "c": None})
            self.assertEqual(seen, [tree / "usage" / "weekly-local.json"])

    def test_agent_check_lessons_lead_the_work_list(self):
        lessons = [{"schema_version": 1, "id": "L-2", "subject": "s2", "kind": "agent-check",
                    "evidence_count": 2, "examples": [], "check_sketch": "x"},
                   {"schema_version": 1, "id": "L-5", "subject": "s5", "kind": "agent-check",
                    "evidence_count": 5},
                   {"schema_version": 1, "id": "L-x", "subject": "s", "kind": "skill-edit",
                    "evidence_count": 9},
                   {"schema_version": 1, "id": "bad id", "subject": "s", "kind": "agent-check",
                    "evidence_count": 9},
                   {"schema_version": 1, "id": "L-0", "subject": "s", "kind": "agent-check",
                    "evidence_count": True}, "junk"]
        plan = plan_week.build_plan(ctx(skills={"few": skill(fixtures=2)}, lessons=lessons), NOW)
        self.assertEqual([(w["kind"], w.get("id")) for w in plan["work"]],
                         [("agent-check", "L-5"), ("agent-check", "L-2"),
                          ("needs-fixtures", None)])
        self.assertEqual(plan["work"][0]["change_type"], "agent-check")
        self.assertNotIn("check_sketch", plan["work"][1])
        lines = "\n".join(plan_week.summary_lines(plan))
        self.assertIn("agent-check 2", lines)
        self.assertNotIn("s5", lines)

    def test_read_lessons_skips_unreadable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a").mkdir()
            (base / "a" / "one.json").write_text('{"id": "x"}')
            (base / "bad.json").write_text("{")
            self.assertEqual(plan_week.read_lessons(base), [{"id": "x"}])
        self.assertEqual(plan_week.read_lessons(None), [])


CHECKLIST_SKILL = """---
name: example-checks
description: Rules: never do x.
---

# Example checks

Intro line.

## Checklist

### 1. Pipes

```bash
# not a heading: inside a fence
cmd | tail
```

- Never pipe a watch into tail.
- Always read PIPESTATUS.
"""

PROSE_SKILL = """---
name: example-prose
description: Write things.
---

# Example prose

Read the room first, then decide.

## How to write

Open with the point.
Say why it matters.
Close with the next step.

## Rules

- Keep it short.
"""


class TriggerLaneTests(unittest.TestCase):
    """Skills that almost never trigger (usage tracking, 2026-10-26): a
    trigger item when a reviewed trigger set exists, a fixture item to write
    one when not, and hook candidates in the summary."""

    @staticmethod
    def triggers(**sets):
        return {name: {"trigger_set": has, "hook_candidate": name == "rules"}
                for name, has in sets.items()}

    def test_fresh_near_zero_usage_gets_a_trigger_item_unknown_gets_none(self):
        triggers = self.triggers(quiet=True, silent=False, rules=False, unknown=True,
                                 busy=True, retiring=True)
        weights = {"quiet": 1, "silent": 0, "rules": 0, "unknown": None, "busy": 2,
                   "retiring": 0}
        skills = {"few": skill(fixtures=2), "retiring": skill(verdict="retire")}
        plan = plan_week.build_plan(ctx(skills=skills, triggers=triggers,
                                        skill_weights=weights,
                                        cells=[cell("evals/b", "heavy", "missing", empty=2)]),
                                    NOW)
        got = [(w["kind"], w["skill"], w["change_type"], w.get("route")) for w in plan["work"]]
        self.assertEqual(got, [
            ("needs-trigger-set", "rules", "fixture", "issue"),
            ("needs-trigger-set", "silent", "fixture", "issue"),
            ("trigger-optimize", "quiet", "trigger", plan_week.TRIGGER_ROUTE),
            ("needs-fixtures", "few", "fixture", "issue"),
            ("fixture-broken", "b", "fixture", "issue")])
        self.assertEqual(plan["work"][1]["trigger_set"], "evals/silent/trigger-eval-set.json")
        self.assertTrue(plan["work"][0]["hook_candidate"])
        self.assertFalse(any(f["mode"] == "improve" and f["skill"] == "quiet"
                             for f in plan["fires"]))
        self.assertEqual(plan["hook_candidates"], ["rules"])
        lines = "\n".join(plan_week.summary_lines(plan))
        self.assertIn("Hook candidates (mostly checklist or rules sections): rules", lines)
        self.assertIn("trigger-optimize 1", lines)

    def test_trigger_skills_do_not_move_the_improve_neutral_weight(self):
        skills = {"a": skill(headroom=0.5), "b": skill(headroom=0.3)}
        weights = {"a": 2.0, "b": None, "c": 50.0, "d": 60.0}
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0, improve_charge=0.01,
                                        skill_weights=weights,
                                        triggers=self.triggers(c=False, d=False)), NOW)
        # The neutral weight is the median of a and b's known weights (2), so
        # b's tie breaks on headroom; c's 50 would have put b first.
        self.assertEqual([f["skill"] for f in plan["fires"]], ["a", "b"])

    def test_hook_candidate_heuristic(self):
        self.assertTrue(plan_week.hook_candidate(CHECKLIST_SKILL))
        self.assertFalse(plan_week.hook_candidate(PROSE_SKILL))
        self.assertFalse(plan_week.hook_candidate(""))
        # A fenced line that looks like a heading does not close the section.
        fenced = "## Rules\n\n```\n# comment\n```\n- a\n- b\n\n## Notes\n\nc\n"
        self.assertTrue(plan_week.hook_candidate(fenced))
        # A ruleset is not a rules section.
        self.assertFalse(plan_week.hook_candidate("## Ruleset\n\n- a\n- b\n"))

    def test_trigger_contexts_read_the_registry_and_the_evals_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skills = base / "registry" / "plugins" / "p" / "skills"
            for name, text in (("example-checks", CHECKLIST_SKILL),
                               ("example-prose", PROSE_SKILL)):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text(text)
            (skills / "linked").mkdir()
            (skills / "linked" / "SKILL.md").symlink_to(skills / "example-prose" / "SKILL.md")
            evals = base / "evals"
            (evals / "example-prose").mkdir(parents=True)
            (evals / "example-prose" / "trigger-eval-set.json").write_text("[]")
            got = plan_week.trigger_contexts(
                evals, {"acsi-agentskills": {"path": str(base / "registry")}})
            self.assertEqual(got, {
                "example-checks": {"trigger_set": False, "hook_candidate": True},
                "example-prose": {"trigger_set": True, "hook_candidate": False}})
            self.assertEqual(plan_week.trigger_contexts(evals, {}), {})

    def test_the_trigger_route_is_documented(self):
        text = (ROOT / "docs" / "how-it-works.md").read_text()
        self.assertIn("### The trigger lane", text)
        self.assertIn(plan_week.TRIGGER_ROUTE, text)
        self.assertIn("--trigger-eval-set evals/<skill>/trigger-eval-set.json", text)


class DispatchLegTests(unittest.TestCase):

    def test_in_flight_and_waiting(self):
        runs = [fire(1, "Fire eval routine: eval evals/x m", NOW - timedelta(minutes=5)),
                fire(2, "Fire eval routine: improve a ", NOW - timedelta(hours=2)),
                fire(3, "t", NOW - timedelta(minutes=1), conclusion=None, status="queued"),
                fire(4, "Fire eval routine: eval evals/x m", NOW - timedelta(hours=1)),
                fire(5, "Fire eval routine: improve a ", NOW - timedelta(hours=4)),
                fire(6, "t", NOW, conclusion="failure")]
        self.assertEqual(plan_week.in_flight(runs, NOW), 3)
        clock = [NOW]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += timedelta(seconds=seconds)
        fetched = []

        def fetch(since):
            fetched.append(since)
            return runs
        self.assertTrue(plan_week.wait_slot(fetch, 1500, lambda: clock[0], sleep))
        # Run 1 leaves its 20-minute flight 15 minutes on.
        self.assertEqual(slept, [60] * 15)
        self.assertEqual(fetched[0], NOW - plan_week.IMPROVE_FLIGHT)
        slept.clear()
        clock[0] = NOW
        self.assertFalse(plan_week.wait_slot(fetch, 300, lambda: clock[0], sleep))
        self.assertEqual(slept, [60] * 5)

    def test_validate_fire_accepts_only_planned_shapes(self):
        good = {"MODE": "eval", "ARMS": "both", "FIXTURE": "evals/writing-adrs/bootstrap",
                "EVAL_KEY": "writing-adrs/bootstrap", "MODEL": "claude-opus-5-5",
                "SKILL": "", "HOLDOUT": ""}
        plan_week.validate_fire(good)
        # A bare leg on a scheduled fixture that runs in place.
        plan_week.validate_fire({**good, "ARMS": "bare"})
        guidance = {"FIXTURE": GUIDANCE_FIXTURE, "EVAL_KEY": GUIDANCE_FIXTURE[len("evals/"):]}
        plan_week.validate_fire({**good, **guidance})
        for change in ({"EVAL_KEY": "writing-adrs/supersede"}, {"MODEL": "claude-fable-5-1"},
                       {"FIXTURE": "evals/writing-adrs/supersede",
                        "EVAL_KEY": "writing-adrs/supersede"},
                       {"SKILL": "writing-adrs"}, {"MODE": "scaffold"},
                       {"ARMS": ""}, {"ARMS": "with"}, {"ARMS": "without"},
                       # A scheduled fixture with no `context` mapping.
                       {**guidance, "ARMS": "bare"}):
            with self.assertRaises(plan_week.PlanError, msg=change):
                plan_week.validate_fire({**good, **change})
        improve = {"MODE": "improve", "ARMS": "", "FIXTURE": "", "EVAL_KEY": "", "MODEL": "",
                   "SKILL": "writing-adrs", "HOLDOUT": "bootstrap"}
        # fire-check reads propose_skill_edit's EVALS_DIR, which is this
        # checkout's only when no earlier test in the worker imported the
        # module from the repository; point it at the example checkout.
        import propose_skill_edit
        patcher = mock.patch.object(propose_skill_edit, "EVALS_DIR", ROOT / "evals")
        patcher.start()
        self.addCleanup(patcher.stop)
        plan_week.validate_fire(improve)
        for change in ({"HOLDOUT": ""}, {"HOLDOUT": "nope"}, {"SKILL": "skills-doctor"},
                       {"MODEL": "claude-opus-5-5"}, {"ARMS": "bare"}, {"ARMS": "both"}):
            with self.assertRaises(plan_week.PlanError, msg=change):
                plan_week.validate_fire({**improve, **change})
        # A section on an improve or eval leg is refused; only guidance-improve
        # legs carry one.
        for leg in (good, improve):
            with self.assertRaises(plan_week.PlanError):
                plan_week.validate_fire({**leg, "SECTION": GUIDANCE_SECTION})
        gleg = {"MODE": "guidance-improve", "ARMS": "", "FIXTURE": "", "EVAL_KEY": "",
                "MODEL": "", "SKILL": "", "SECTION": GUIDANCE_SECTION,
                "HOLDOUT": GUIDANCE_FIXTURE[len("evals/"):]}
        plan_week.validate_fire(gleg)
        # A planned leg always names its holdout, as an improve leg does.
        for change in ({"HOLDOUT": ""}, {"SECTION": ""}, {"SECTION": "_delivery"},
                       {"SECTION": "two-setup-gaps", "HOLDOUT": ""},
                       {"HOLDOUT": "handoff"}, {"HOLDOUT": "guidance/x/handoff"},
                       {"SKILL": "writing-adrs"}, {"MODEL": "claude-opus-5-5"},
                       {"ARMS": "both"}, {"FIXTURE": GUIDANCE_FIXTURE}):
            with self.assertRaises(plan_week.PlanError, msg=change):
                plan_week.validate_fire({**gleg, **change})


class PlanCommandTests(unittest.TestCase):
    """The step's own command on the real evals directory, an empty results
    tree and recorded evidence: every planned leg passes its revalidation."""

    def test_plan_writes_a_matrix_whose_legs_revalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "tree").mkdir()
            evidence = base / "evidence.json"
            evidence.write_text(json.dumps({"fire_runs": [], "improve_branches": [],
                                            "registry_prs": [], "on_hold": []}))
            output, summary, plan_file = base / "output", base / "summary", base / "plan.json"
            argv = ["plan", "--results-tree", str(base / "tree"), "--as-of",
                    "2026-10-26T12:00:00+00:00", "--evidence", str(evidence),
                    "--out", str(plan_file)]
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output),
                                              "GITHUB_STEP_SUMMARY": str(summary)}), \
                 contextlib.redirect_stdout(out):
                self.assertEqual(plan_week.main(argv), 0, out.getvalue())
            lines = output.read_text().splitlines()
            matrix = json.loads(lines[0].removeprefix("matrix="))
            self.assertEqual(lines[1], f"count={len(matrix['include'])}")
            self.assertGreater(len(matrix["include"]), 0)
            self.assertTrue(all(row["band"] == "missing" for row in matrix["include"]))
            for row in matrix["include"]:
                plan_week.validate_fire({"MODE": row["mode"], "ARMS": row["arms"],
                                         "FIXTURE": row["fixture"],
                                         "EVAL_KEY": row["eval_key"], "MODEL": row["model"],
                                         "SKILL": row["skill"], "HOLDOUT": row["holdout"]})
            self.assertIn("### Weekly plan:", summary.read_text())
            plan = json.loads(plan_file.read_text())
            self.assertEqual(plan["schema_version"], 1)
            # Every in-place cell is a bare candidate on an empty tree; the
            # missing band takes the room first, so none is planned yet.
            roster = yaml.safe_load((ROOT / "evals" / "roster.yml").read_text())
            in_place = [f for f in plan_week.schedule.scheduled_fixtures(
                            ROOT / "evals" / "scheduled.yml",
                            plan_week.schedule.committed_fixtures(ROOT))
                        if plan_week.schedule.declares_context(ROOT / f)]
            self.assertTrue(in_place)
            self.assertEqual(plan["unplanned"]["bare"], len(in_place) * len(roster["arms"]))

    def test_an_unreadable_evidence_file_plans_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "evidence.json").write_text("{")
            output = base / "output"
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                 contextlib.redirect_stdout(out):
                code = plan_week.main(["plan", "--results-tree", str(base), "--as-of",
                                       "2026-10-26T12:00:00+00:00",
                                       "--evidence", str(base / "evidence.json")])
            self.assertEqual(code, 1)
            self.assertFalse(output.exists())
            self.assertEqual(out.getvalue(), "::error::weekly plan: the plan could not be made\n")

    def test_registry_prs_split_improve_and_removal(self):
        rows = [{"head": {"ref": "eval-improve/writing-adrs"}, "state": "open",
                 "created_at": "a", "closed_at": None, "merged_at": None},
                {"head": {"ref": "retire-skill/old-skill"}, "state": "open"},
                {"head": {"ref": "retire-skill/done"}, "state": "closed"},
                {"head": {"ref": "eval-improve/$(x)"}, "state": "open"}, {"head": None}]
        improve, removal = plan_week.registry_prs(rows)
        self.assertEqual([p["skill"] for p in improve], ["writing-adrs"])
        self.assertEqual(removal, ["old-skill"])

    def test_model_weights_from_the_census(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(plan_week.model_weights(base, ["a", "b"]), {"a": 0.5, "b": 0.5})
            (base / "usage").mkdir()
            (base / "usage" / "latest.json").write_text(json.dumps(
                {"a": {"2026-W42": 3}, "b": {"2026-W42": 1, "2026-W43": True}, "weeks": 2}))
            self.assertEqual(plan_week.model_weights(base, ["a", "b"]), {"a": 0.75, "b": 0.25})


GUIDANCE_FIXTURE = "evals/guidance/anything-you-name-gets-its-link/handoff"
GUIDANCE_SECTION = "anything-you-name-gets-its-link"


class GuidanceFixtureTests(unittest.TestCase):
    """Scheduled guidance-section fixtures (#124) are eval cells keyed by
    their section, never a skill: the verdict document's `guidance` half is
    their evidence, and skill-only logic skips them."""

    def test_guidance_cells_read_the_section_verdict_and_name_no_skill(self):
        self.assertIn(GUIDANCE_FIXTURE, plan_week.schedule.scheduled_fixtures(
            ROOT / "evals" / "scheduled.yml",
            plan_week.schedule.committed_fixtures(ROOT)))
        run = {"timestamp": "20261020T100000Z-abc123"}
        gates_doc = {"roster_models": ["m"], "skills": {}, "guidance": {
            "anything-you-name-gets-its-link": {"cells": [
                {"fixture": "handoff", "model": "m", "class": "insufficient",
                 "cost_usd": 2.0, "runs": {"with": [run], "without": [run]}}]}}}
        cells = plan_week.eval_cells(ROOT / "evals", gates_doc, [], NOW)
        mine = [c for c in cells if c["fixture"] == GUIDANCE_FIXTURE]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["skill"], mine[0]["section"], mine[0]["class"],
                          mine[0]["with_runs"], mine[0]["usd_per_fire"]),
                         ("", "anything-you-name-gets-its-link", "insufficient", 1, 2.0))
        other = next(c for c in cells if c["fixture"].startswith("evals/guidance/")
                     and c["fixture"] != GUIDANCE_FIXTURE)
        self.assertEqual((other["skill"], other["class"]), ("", "missing"))
        self.assertTrue(all(c["section"] is None and c["skill"] for c in cells
                            if not c["fixture"].startswith("evals/guidance/")))

    def test_guidance_cells_plan_eval_fires_and_skip_skill_logic(self):
        section = "anything-you-name-gets-its-link"
        cells = [{**cell(GUIDANCE_FIXTURE, "heavy", "missing"), "skill": "",
                  "section": section},
                 {**cell("evals/guidance/s/x", "heavy", "missing", empty=2), "skill": "",
                  "section": "s"}]
        # A removal PR named like the section is a skill's, not this cell's.
        plan = plan_week.build_plan(ctx(cells=cells, removal_prs=[section, ""]), NOW)
        self.assertEqual([(f["band"], f["mode"], f["fixture"], f["eval_key"])
                          for f in plan["fires"]],
                         [("missing", "eval", GUIDANCE_FIXTURE,
                           "guidance/anything-you-name-gets-its-link/handoff")])
        self.assertEqual(plan["matrix"]["include"][0]["skill"], "")
        self.assertEqual(plan["work"], [{"kind": "fixture-broken", "change_type": "fixture",
                                         "skill": "", "section": "s",
                                         "fixture": "evals/guidance/s/x", "model": "heavy",
                                         "route": "issue"}])

    def test_the_plan_command_names_no_guidance_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "tree").mkdir()
            evidence = base / "evidence.json"
            evidence.write_text(json.dumps({"fire_runs": [], "improve_branches": [],
                                            "registry_prs": [], "on_hold": []}))
            plan_file = base / "plan.json"
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": "",
                                              "GITHUB_STEP_SUMMARY": ""}), \
                 contextlib.redirect_stdout(out):
                code = plan_week.main(["plan", "--results-tree", str(base / "tree"),
                                       "--as-of", "2026-10-26T12:00:00+00:00",
                                       "--evidence", str(evidence), "--max-fires", "30",
                                       "--out", str(plan_file)])
            self.assertEqual(code, 0, out.getvalue())
            plan = json.loads(plan_file.read_text())
            self.assertNotIn("guidance", plan["skills"])
            self.assertFalse(any(w.get("skill") == "guidance" for w in plan["work"]))


def gsection(fixtures=4, headroom=0.4, verdict="keep"):
    """A guidance section's context, as plan_week.guidance_contexts builds it."""
    return {"fixtures": fixtures, "holdout": "zzz" if fixtures >= 4 else "",
            "verdict": verdict,
            "evidence": {"headroom": headroom, "pass_rate": None, "with_trials": 6,
                         "targets": []}}


class ImproveValueTests(unittest.TestCase):
    """The improve candidates, skills and guidance sections, ranked by
    value = use x headroom / cost."""

    def test_the_value_formula_and_what_makes_it_unknown(self):
        self.assertAlmostEqual(plan_week.value_of(10, 0.5, 0.25), 20.0)
        self.assertEqual(plan_week.value_of(0, 0.5, 0.25), 0.0)
        for args in ((None, 0.5, 0.25), (10, None, 0.25), (10, 0.5, None), (10, 0.5, 0)):
            self.assertIsNone(plan_week.value_of(*args), args)

    def test_cost_is_improve_trials_times_each_fixtures_mean_fire_cost(self):
        # usd 1 and 3 on one fixture: points 0.01 and 0.03, mean 0.02; the
        # second fixture 0.02; an unknown cell adds nothing.
        cells = [cell("evals/a/f1", "heavy", "missing", usd=1.0),
                 cell("evals/a/f1", "light", "missing", usd=3.0),
                 cell("evals/a/f2", "heavy", "missing", usd=2.0),
                 cell("evals/a/f3", "heavy", "missing"),
                 cell("evals/b/f1", "heavy", "missing")]
        c = ctx(cells=cells, points_per_usd=0.01)
        self.assertAlmostEqual(plan_week.cost_estimate(c, "skill", "a"),
                               plan_week.IMPROVE_TRIALS * 0.04)
        self.assertIsNone(plan_week.cost_estimate(c, "skill", "b"))
        self.assertIsNone(plan_week.cost_estimate(ctx(cells=cells), "skill", "a"))
        guidance = {**cell("evals/guidance/s/x", "heavy", "missing", usd=2.0), "skill": "",
                    "section": "s"}
        c = ctx(cells=[guidance], points_per_usd=0.01)
        self.assertAlmostEqual(plan_week.cost_estimate(c, "guidance", "s"),
                               plan_week.IMPROVE_TRIALS * 0.02)
        self.assertIsNone(plan_week.cost_estimate(c, "skill", "s"))

    def test_ranking_rows_carry_value_use_and_cost(self):
        cells = [cell("evals/a/f1", "heavy", "missing", usd=2.0)]
        plan = plan_week.build_plan(ctx(
            skills={"a": skill(headroom=0.5)}, skill_weights={"a": 4}, cells=cells,
            points_per_usd=0.01, room=1.0), NOW)
        cost = plan_week.IMPROVE_TRIALS * 0.02
        self.assertEqual(plan["improve_ranking"], [{
            "subject": "skill", "name": "a", "value": 4 * 0.5 / cost, "use": 4,
            "use_known": True, "headroom": 0.5, "cost": cost, "cost_known": True,
            "eligible": True, "reasons": []}])
        improve = [f for f in plan["fires"] if f["mode"] == "improve"]
        self.assertEqual([f["value"] for f in improve], [4 * 0.5 / cost])
        self.assertIsNone(plan["fires"][0]["value"] if plan["fires"][0]["mode"] == "eval"
                          else None)
        self.assertNotIn("value", plan["matrix"]["include"][0])

    def test_unknown_use_and_cost_are_neutral_and_a_known_zero_stays_zero(self):
        skills = {"known": skill(), "unknown": skill(), "unused": skill()}
        plan = plan_week.build_plan(ctx(
            skills=skills, skill_weights={"known": 8, "unknown": None, "unused": 0},
            improve_charge=0.2, room=1.0, guidance_use=None,
            guidance={"sec": gsection(headroom=0.5)}), NOW)
        rows = {r["name"]: r for r in plan["improve_ranking"]}
        self.assertEqual((rows["unknown"]["use"], rows["unknown"]["use_known"]), (8, False))
        self.assertEqual((rows["unused"]["use"], rows["unused"]["use_known"],
                          rows["unused"]["value"]), (0, True, 0.0))
        # Guidance's unknown use is the same neutral value; no cost known
        # anywhere, so the improve charge stands in.
        self.assertEqual((rows["sec"]["use"], rows["sec"]["use_known"]), (8, False))
        self.assertTrue(all(r["cost"] == 0.2 and not r["cost_known"] for r in rows.values()))
        self.assertAlmostEqual(rows["sec"]["value"], 8 * 0.5 / 0.2)

    def test_an_unknown_cost_is_the_median_of_the_known_ones(self):
        cells = [cell("evals/a/f1", "heavy", "missing", usd=1.0),
                 cell("evals/b/f1", "heavy", "missing", usd=3.0)]
        plan = plan_week.build_plan(ctx(
            skills={"a": skill(), "b": skill(), "c": skill()}, cells=cells,
            points_per_usd=0.01, improve_charge=0.9, room=1.0), NOW)
        rows = {r["name"]: r for r in plan["improve_ranking"]}
        self.assertAlmostEqual(rows["c"]["cost"], plan_week.IMPROVE_TRIALS * 0.02)
        self.assertFalse(rows["c"]["cost_known"])
        self.assertTrue(rows["a"]["cost_known"] and rows["b"]["cost_known"])

    def test_the_improve_band_is_ordered_by_value_not_usage(self):
        # The heavier skill used to lead; the one with far more headroom now does.
        skills = {"heavy": skill(headroom=0.12), "light": skill(headroom=0.9)}
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0, improve_charge=0.01,
                                        skill_weights={"heavy": 10, "light": 5}), NOW)
        self.assertEqual([f["skill"] for f in plan["fires"]], ["light", "heavy"])
        self.assertEqual([r["name"] for r in plan["improve_ranking"]], ["light", "heavy"])
        self.assertGreater(plan["fires"][0]["value"], plan["fires"][1]["value"])

    def test_ties_break_on_name(self):
        plan = plan_week.build_plan(ctx(skills={"b": skill(), "a": skill()}, room=1.0,
                                        improve_charge=0.01,
                                        skill_weights={"a": 3, "b": 3}), NOW)
        self.assertEqual([f["skill"] for f in plan["fires"]], ["a", "b"])

    def test_an_eligible_guidance_section_is_an_improve_band_fire(self):
        plan = plan_week.build_plan(ctx(
            guidance={"sec": {**gsection(headroom=0.4),
                              "holdout_fixture": "guidance/sec/zzz"}},
            guidance_use=100.0, improve_charge=0.1, room=1.0), NOW)
        self.assertEqual(plan["work"], [])
        [fire] = plan["fires"]
        self.assertAlmostEqual(fire.pop("value"), 100.0 * 0.4 / 0.1)
        self.assertEqual(fire, {
            "slot": 0, "band": "improve", "mode": "guidance-improve", "arms": "",
            "fixture": "", "eval_key": "", "model": "", "skill": "", "section": "sec",
            "holdout": "guidance/sec/zzz", "change_type": "guidance-edit", "cost": 0.1})
        self.assertEqual(plan["matrix"]["include"], [{
            "slot": 0, "band": "improve", "mode": "guidance-improve", "arms": "", "fixture": "", "eval_key": "",
            "model": "", "skill": "", "section": "sec", "holdout": "guidance/sec/zzz"}])
        self.assertEqual(plan["guidance"], {"sec": {
            "eligible": True, "reasons": [], "headroom": 0.4, "fixtures": 4}})
        self.assertEqual(plan["skills"], {})

    def test_one_guidance_fire_per_plan_the_highest_value_first(self):
        plan = plan_week.build_plan(ctx(
            guidance={"low": gsection(headroom=0.2), "high": gsection(headroom=0.8)},
            guidance_use=10.0, improve_charge=0.1, room=1.0), NOW)
        self.assertEqual([f["section"] for f in plan["fires"]], ["high"])
        self.assertTrue(plan["guidance"]["low"]["eligible"])
        self.assertIn("high ranks higher", plan["guidance"]["low"]["deferred"])
        self.assertEqual(plan["work"], [])

    def test_a_paused_section_gets_no_fire_and_no_work_item(self):
        for pause in ("an attempt is in flight",
                      "another guidance-loop pull request is open in _agent-guidance"):
            with self.subTest(pause=pause):
                plan = plan_week.build_plan(ctx(
                    guidance={"sec": {**gsection(headroom=0.5), "pause": pause}},
                    guidance_use=10.0, improve_charge=0.1, room=1.0), NOW)
                self.assertEqual(plan["fires"], [])
                self.assertEqual(plan["work"], [])
                self.assertEqual(plan["guidance"]["sec"]["reasons"], [pause])
                self.assertFalse(plan["guidance"]["sec"]["eligible"])
        plan = plan_week.build_plan(ctx(
            guidance={"sec": {**gsection(headroom=0.5), "row_ok": False}},
            guidance_use=10.0, improve_charge=0.1, room=1.0), NOW)
        self.assertEqual((plan["fires"], plan["work"]), ([], []))
        self.assertEqual(plan["guidance"]["sec"]["reasons"],
                         ["its eval-coverage.yml row is skipped or missing"])

    def test_a_section_id_the_fire_workflow_refuses_stays_a_work_item(self):
        # `Sec_1` is a manifest id the fire workflow's section pattern
        # refuses, so it keeps the local route instead of a fire.
        plan = plan_week.build_plan(ctx(
            guidance={"Sec_1": gsection(headroom=0.4)}, guidance_use=100.0,
            improve_charge=0.1, room=1.0), NOW)
        self.assertEqual(plan["fires"], [])
        [item] = plan["work"]
        self.assertAlmostEqual(item.pop("value"), 100.0 * 0.4 / 0.1)
        self.assertEqual(item, {
            "kind": "guidance-improve", "change_type": "guidance-edit", "section": "Sec_1",
            "fixtures": 4, "headroom": 0.4, "route": plan_week.GUIDANCE_IMPROVE_ROUTE})

    def test_guidance_sections_that_are_not_eligible(self):
        plan = plan_week.build_plan(ctx(
            guidance={"few": gsection(fixtures=2, headroom=0.5),
                      "flat": gsection(headroom=0.05),
                      "retiring": gsection(verdict="retire"),
                      "pending": gsection(verdict="retire-pending", headroom=0.01),
                      "blank": gsection(headroom=None)},
            guidance_use=10.0, room=1.0), NOW)
        by_section = {w["section"]: w for w in plan["work"]}
        self.assertEqual(sorted(by_section), ["few", "flat"])
        self.assertEqual((by_section["few"]["kind"], by_section["few"]["skill"],
                          by_section["few"]["fixtures"], by_section["few"]["headroom"]),
                         ("needs-fixtures", "", 2, 0.5))
        self.assertEqual((by_section["flat"]["kind"], by_section["flat"]["skill"]),
                         ("needs-harder-fixtures", ""))
        status = plan["guidance"]
        self.assertEqual(status["few"]["reasons"], ["2 fixture(s), under 4"])
        self.assertIn("owned by retirement (retire)", status["retiring"]["reasons"])
        self.assertIn("no train headroom", status["blank"]["reasons"])
        self.assertFalse(any(s["eligible"] for s in status.values()))
        self.assertEqual(plan["fires"], [])

    def test_the_ranking_is_by_value_with_unknown_headroom_last(self):
        skills = {"blank": skill(headroom=None), "low": skill(headroom=0.2),
                  "high": skill(headroom=0.8)}
        plan = plan_week.build_plan(ctx(
            skills=skills, skill_weights={"blank": 99, "low": 5, "high": 5},
            guidance={"sec": gsection(headroom=0.5)}, guidance_use=5.0,
            improve_charge=0.1, room=1.0), NOW)
        ranking = plan["improve_ranking"]
        self.assertEqual([(r["subject"], r["name"]) for r in ranking],
                         [("skill", "high"), ("guidance", "sec"), ("skill", "low"),
                          ("skill", "blank")])
        self.assertIsNone(ranking[-1]["value"])
        self.assertIsNone(ranking[-1]["headroom"])
        self.assertIn("no train headroom", ranking[-1]["reasons"])
        self.assertTrue(ranking[0]["eligible"] and ranking[1]["eligible"])
        self.assertEqual([r["value"] for r in ranking[:3]],
                         sorted((r["value"] for r in ranking[:3]), reverse=True))

    def test_the_summary_lists_the_top_ten_with_rounding_only_there(self):
        skills = {f"s{i:02d}": skill(headroom=0.3 + i / 100) for i in range(12)}
        skills["blank"] = skill(headroom=None)
        plan = plan_week.build_plan(ctx(skills=skills, room=1.0, improve_charge=0.07,
                                        skill_weights={n: 2 for n in skills}), NOW)
        lines = plan_week.summary_lines(plan)
        at = lines.index("Improve ranking (value = use × headroom ÷ cost)")
        rows = [line for line in lines[at:] if line.startswith("- ")]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0], "- skill s11: value 11.7 (use 2.0, headroom 0.41, "
                                  "cost 7.00 points), eligible")
        self.assertNotEqual(plan["improve_ranking"][0]["value"], 11.7)
        blank = plan_week.summary_lines({**plan, "improve_ranking": plan["improve_ranking"][-1:]})
        self.assertIn("- skill blank: value unknown (use 2.0, headroom unknown, cost 7.00 "
                      "points): no train headroom", blank)

    def test_session_rate_scales_the_transcript_window_to_28_days(self):
        import skill_usage
        doc = skill_usage.document([], NOW, NOW - timedelta(days=14), 50)
        self.assertAlmostEqual(skill_usage.session_rate(doc, NOW), 100.0)
        self.assertAlmostEqual(skill_usage.session_rate(doc, NOW, days=7), 25.0)
        self.assertIsNone(skill_usage.session_rate(None, NOW))
        self.assertIsNone(skill_usage.session_rate(doc, NOW + timedelta(days=15)))
        for change in ({"sessions_scanned": -1}, {"sessions_scanned": True},
                       {"from": "yesterday"}, {"to": doc["sources"][0]["from"]},
                       {"scope": "hooks"}, {"surface": "cloud"}):
            bad = {**doc, "sources": [{**doc["sources"][0], **change}]}
            self.assertIsNone(skill_usage.session_rate(bad, NOW), change)
        self.assertIsNone(skill_usage.session_rate({**doc, "sources": []}, NOW))
        # A known zero is zero, not unknown.
        zero = skill_usage.document([], NOW, NOW - timedelta(days=14), 0)
        self.assertEqual(skill_usage.session_rate(zero, NOW), 0.0)

    def test_guidance_use_reads_the_census_or_stays_unknown(self):
        import skill_usage
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            self.assertIsNone(plan_week.guidance_use(tree, NOW))
            (tree / "usage").mkdir()
            path = tree / "usage" / "weekly-local.json"
            path.write_text(json.dumps(skill_usage.document(
                [], NOW - timedelta(days=1), NOW - timedelta(days=15), 56)))
            self.assertAlmostEqual(plan_week.guidance_use(tree, NOW), 112.0)
            self.assertIsNone(plan_week.guidance_use(tree, NOW + timedelta(days=30)))
            path.write_text("{")
            self.assertIsNone(plan_week.guidance_use(tree, NOW))
            with mock.patch.dict(sys.modules, {"skill_usage": None}):
                self.assertIsNone(plan_week.guidance_use(tree, NOW))

    def test_guidance_contexts_need_the_checkout_and_pool_the_in_situ_full_arm(self):
        section = "anything-you-name-gets-its-link"
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            doc = {"roster_models": ["m"], "guidance": {section: {"verdict": "keep"}}}
            self.assertEqual(plan_week.guidance_contexts(
                ROOT / "evals", tree, "20261026T120000Z", doc, None), {})
            # done-summary, handoff, no-url are train; waiting-on-review (last
            # in name order) is the holdout and must not count.
            expected = {n: {"fixture_digest": f"fd-{n}", "tested_digest": "t"}
                        for n in ("done-summary", "handoff", "no-url", "waiting-on-review")}

            def run(name, role, checks):
                return {"skill": section, "fixture": name, "model": "m", "role": role,
                        "pair": "in_situ", "tested_digest": "t",
                        "fixture_digest": f"fd-{name}", "arm_context_digest": "rest",
                        "pair_key": "rest", "timestamp": "20261020T000000Z", "path": name,
                        "trials": [trial(checks)]}
            runs = [run("done-summary", "with", {"a": True, "b": False}),
                    run("done-summary", "without", {"a": False, "b": False}),
                    run("handoff", "with", {"a": True, "b": True}),
                    run("handoff", "without", {"a": False, "b": True}),
                    run("waiting-on-review", "with", {"a": False, "b": False}),
                    run("waiting-on-review", "without", {"a": False, "b": False})]
            index = {"guidance_runs": runs + [{**runs[0], "pair": "alone"}]}
            with mock.patch.object(plan_week_compute_gates, "build_index",
                                   return_value=index), \
                 mock.patch.object(plan_week_compute_gates, "expected_guidance",
                                   return_value=expected):
                got = plan_week.guidance_contexts(ROOT / "evals", tree, "20261026T120000Z",
                                                  doc, tree)
        self.assertEqual(got[section]["fixtures"], 4)
        self.assertEqual(got[section]["holdout"], "waiting-on-review")
        self.assertEqual(got[section]["verdict"], "keep")
        # 3 of 4 train checks passed: headroom 0.25, the holdout's failures left out.
        self.assertAlmostEqual(got[section]["evidence"]["headroom"], 0.25)
        # A section with one fixture keeps no holdout and an unknown verdict.
        self.assertEqual(got["finding-your-unknowns"]["holdout"], "")
        self.assertEqual(got["finding-your-unknowns"]["verdict"], "insufficient")
        self.assertNotIn("_delivery", got)

    def test_the_fire_check_refuses_a_section_with_no_train_headroom(self):
        # 2026-10-28: a hand-dispatched guidance-improve fire on a section at
        # 72/72 ran its whole loop before rejecting. The fire workflow now
        # asks the plan's own rule A first.
        section = "anything-you-name-gets-its-link"

        def check(headroom, present=True):
            ctx = ({section: {"evidence": {"headroom": headroom}}} if present else {})
            out = io.StringIO()
            with mock.patch.object(plan_week_compute_gates, "compute",
                                   return_value={"roster_models": ["m"]}), \
                 mock.patch.object(plan_week, "guidance_contexts", return_value=ctx), \
                 contextlib.redirect_stdout(out):
                code = plan_week.main(["guidance-headroom", section, "--results-tree", "t",
                                       "--as-of", "2026-10-28T18:07:56+00:00",
                                       "--guidance", "g"])
            return code, out.getvalue()

        code, out = check(0.0)
        self.assertEqual(code, 1, out)
        self.assertIn("::error::", out)
        self.assertIn("needs harder fixtures", out)
        code, out = check(plan_week.MIN_GAIN - 0.01)
        self.assertEqual(code, 1, out)
        self.assertEqual(check(plan_week.MIN_GAIN)[0], 0)
        self.assertEqual(check(0.25)[0], 0)
        # Unknown headroom passes: the proposer measures before it decides.
        self.assertEqual(check(None)[0], 0)
        self.assertEqual(check(None, present=False)[0], 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = plan_week.main(["guidance-headroom", "../x", "--results-tree", "t",
                                   "--as-of", "2026-10-28T18:07:56+00:00", "--guidance", "g"])
        self.assertEqual(code, 1)

    def test_ranking_out_writes_the_ranking_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "tree").mkdir()
            evidence = base / "evidence.json"
            evidence.write_text(json.dumps({"fire_runs": [], "improve_branches": [],
                                            "registry_prs": [], "on_hold": []}))
            plan_file, ranking_file = base / "plan.json", base / "ranking.json"
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}), \
                 contextlib.redirect_stdout(out):
                code = plan_week.main(["plan", "--results-tree", str(base / "tree"),
                                       "--as-of", "2026-10-26T12:00:00+00:00",
                                       "--evidence", str(evidence), "--out", str(plan_file),
                                       "--ranking-out", str(ranking_file)])
            self.assertEqual(code, 0, out.getvalue())
            plan = json.loads(plan_file.read_text())
            ranking = json.loads(ranking_file.read_text())
            self.assertEqual(sorted(ranking), ["generated_at", "improve_ranking",
                                               "schema_version"])
            self.assertEqual(ranking["schema_version"], 1)
            self.assertEqual(ranking["generated_at"], plan["generated_at"])
            self.assertEqual(ranking["improve_ranking"], plan["improve_ranking"])
            self.assertTrue(ranking["improve_ranking"])
            self.assertTrue(all(r["subject"] == "skill" and r["value"] is None
                                for r in ranking["improve_ranking"]))
            self.assertIn("Improve ranking (value = use × headroom ÷ cost)", out.getvalue())

    def test_the_weekly_workflow_uploads_the_ranking(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/routine-eval-weekly.yml").read_text())
        steps = doc["jobs"]["plan"]["steps"]
        at = next(i for i, step in enumerate(steps) if step.get("id") == "plan")
        self.assertIn('--ranking-out "$RUNNER_TEMP/improve-ranking.json"', steps[at]["run"])
        upload = steps[at + 1]
        self.assertEqual(upload["uses"], "actions/upload-artifact@v7.0.2")
        self.assertEqual(upload["with"], {
            "name": "improve-ranking", "path": "${{ runner.temp }}/improve-ranking.json",
            "retention-days": 30, "if-no-files-found": "error"})
        lock = (ROOT / ".github/workflows/actions.lock").read_text()
        self.assertIn("actions/upload-artifact@v7.0.2", lock)


BARE_FIXTURES = ("evals/writing-adrs/bootstrap", "evals/disarm-inherited-reach",
                 "evals/embeddable-tool-pages", "evals/github-actions-sha-pinning",
                 "evals/windows-elevation-from-wsl")


class BareBaselineTests(unittest.TestCase):
    """ADR 0012's 2026-10-27 addendum: one bare fire per (scheduled in-place
    fixture, roster model), once, after every other band, reserved as one
    arm; never an empty fire of the cell's pair, and one run to the guard."""

    def scheduled_in_place(self):
        committed = plan_week.schedule.committed_fixtures(ROOT)
        return [f for f in plan_week.schedule.scheduled_fixtures(
                    ROOT / "evals" / "scheduled.yml", committed)
                if plan_week.schedule.declares_context(ROOT / f)]

    def test_the_scheduled_in_place_fixtures(self):
        in_place = self.scheduled_in_place()
        # The example checkout's in-place skill fixtures (test/example_evals.py).
        for fixture in ("evals/workflow-path-audit/required-check-routing",
                        "evals/writing-adrs/bootstrap"):
            self.assertIn(fixture, in_place)
        # Guidance fixtures declare no context: their arms are their own.
        self.assertFalse([f for f in in_place if f.startswith("evals/guidance/")])

    def test_bare_cells_are_the_in_place_cells_with_no_bare_run(self):
        bootstrap, disarm, embeddable, pinning, windows = BARE_FIXTURES
        gates_doc = {"roster_models": ["m1", "m2"], "skills": {}, "bare_runs": [
            # Ingested: done, on m1 only.
            {"skill": "writing-adrs", "fixture": "bootstrap", "model": "m1"},
            {"skill": "disarm-inherited-reach", "fixture": None, "model": "m2"}]}
        t = NOW - timedelta(days=2)
        runs = [fire(1, f"Fire eval routine: eval bare {embeddable} m1", t),
                fire(2, f"Fire eval routine: eval bare {pinning} m1", NOW,
                     conclusion=None, status="in_progress"),
                # Refused or failed: not done.
                fire(3, f"Fire eval routine: eval bare {windows} m1", t, conclusion="failure"),
                # A pair fire is not a bare run.
                fire(4, f"Fire eval routine: eval {windows} m2", t),
                # A bare fire with no model names no cell.
                fire(5, f"Fire eval routine: eval bare {windows} ", t)]
        every = {(f, m) for f in self.scheduled_in_place() for m in ("m1", "m2")}
        got = plan_week.bare_cells(ROOT / "evals", gates_doc, runs)
        self.assertEqual(len(got), len({(c["fixture"], c["model"]) for c in got}))
        self.assertEqual({(c["fixture"], c["model"]) for c in got},
                         every - {(bootstrap, "m1"), (disarm, "m2"), (embeddable, "m1"),
                                  (pinning, "m1")})
        self.assertEqual(next(c for c in got if c["fixture"] == bootstrap)["skill"],
                         "writing-adrs")
        # Once done, never again; a new model id on the roster gets its own.
        done = [{"skill": c["skill"], "fixture": c["fixture"].split("/")[2]
                 if c["fixture"].count("/") == 2 else None, "model": c["model"]}
                for c in got] + gates_doc["bare_runs"]
        later = {**gates_doc, "bare_runs": done}
        self.assertEqual(plan_week.bare_cells(ROOT / "evals", later, runs), [])
        later["roster_models"] = ["m1", "m2", "m3"]
        self.assertEqual({(c["fixture"], c["model"])
                          for c in plan_week.bare_cells(ROOT / "evals", later, runs)},
                         {(f, "m3") for f in self.scheduled_in_place()})

    def test_bare_fires_take_only_the_room_left_and_cost_one_arm(self):
        c = ctx(points_per_usd=0.005)
        self.assertAlmostEqual(plan_week.bare_cost({"usd_per_arm_trial": 1.0}, c),
                               1.0 * plan_week.BARE_TRIALS * 0.005)
        self.assertAlmostEqual(plan_week.bare_cost({"usd_per_arm_trial": 100.0}, c), 0.035)
        self.assertAlmostEqual(plan_week.bare_cost({"usd_per_arm_trial": 0.0}, c), 0.005)
        self.assertAlmostEqual(plan_week.bare_cost(None, c), 0.035)
        self.assertAlmostEqual(plan_week.bare_cost({"usd_per_arm_trial": 1.0}, ctx()), 0.035)
        cells = [{**cell("evals/a", "heavy", "missing", usd=2.0), "usd_per_arm_trial": 1.0},
                 cell("evals/b", "light", "missing")]
        bare = [{"fixture": "evals/a", "model": "heavy", "skill": "a"},
                {"fixture": "evals/a", "model": "light", "skill": "a"}]
        # Room for the two missing fires (0.01 + 0.035) and one bare fire.
        plan = plan_week.build_plan(ctx(cells=cells, bare=bare, points_per_usd=0.005,
                                        room=0.045 + 0.015 + 0.01,
                                        skills={"writing-adrs": skill()},
                                        improve_charge=1.0), NOW)
        got = [(f["band"], f["mode"], f["arms"], f["fixture"], f["model"]) for f in plan["fires"]]
        self.assertEqual(got, [("missing", "eval", "both", "evals/a", "heavy"),
                               ("missing", "eval", "both", "evals/b", "light"),
                               ("bare", "eval", "bare", "evals/a", "heavy")])
        self.assertAlmostEqual(plan["fires"][2]["cost"], 0.015)
        self.assertEqual(plan["unplanned"]["bare"], 1)
        leg = plan["matrix"]["include"][2]
        self.assertEqual((leg["mode"], leg["arms"], leg["skill"], leg["holdout"]),
                         ("eval", "bare", "", ""))
        self.assertIn("- bare: eval bare evals/a heavy, 1.50 points",
                      plan_week.summary_lines(plan))
        # Bare goes after every other band, improve included, but an improve
        # fire is still dispatched last.
        plan = plan_week.build_plan(ctx(bare=bare, room=1.0, improve_charge=0.10,
                                        skills={"writing-adrs": skill()}), NOW)
        self.assertEqual([(f["band"], f["arms"]) for f in plan["fires"]],
                         [("bare", "bare"), ("bare", "bare"), ("improve", "")])

    def test_a_bare_fire_is_not_an_empty_fire_of_the_cells_pair(self):
        fixture = "evals/writing-adrs/bootstrap"
        gates_doc = {"roster_models": ["m1"], "skills": {}}
        old = NOW - timedelta(days=1)
        bare = [fire(i, f"Fire eval routine: eval bare {fixture} m1", old) for i in (1, 2)]
        pair = [fire(i, f"Fire eval routine: eval {fixture} m1", old) for i in (3, 4)]

        def empty(runs):
            cells = plan_week.eval_cells(ROOT / "evals", gates_doc, runs, NOW)
            return next(c for c in cells if c["fixture"] == fixture)["empty_fires"]
        self.assertEqual(empty(bare), 0)
        self.assertEqual(empty(pair), 2)

    def test_a_bare_fire_is_one_run_to_the_allowance_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            runs = [fire(1, "Fire eval routine: eval bare evals/writing-adrs/bootstrap m1",
                         NOW - timedelta(minutes=10))]
            # Not an improve fire, so never held at improve_charge.
            self.assertEqual(guard.improve_counts(runs, NOW), (0, 0))
            state = guard.account(tree, LIMITS, NOW, plan_week._fire_pair(runs, NOW),
                                  guard.improve_counts(runs, NOW))
            # One fire in the hold: one reservation, as an eval fire of both arms.
            self.assertAlmostEqual(state["pending"], guard.budget_file.run_reservation(LIMITS))
            pair = [fire(1, "Fire eval routine: eval evals/writing-adrs/bootstrap m1",
                         NOW - timedelta(minutes=10))]
            self.assertEqual(state, guard.account(tree, LIMITS, NOW,
                                                  plan_week._fire_pair(pair, NOW),
                                                  guard.improve_counts(pair, NOW)))
            self.assertTrue(guard.check(state, "eval"))

    def test_evidence_start_times_bound_a_covered_improve_fire(self):
        # The planner passes the same evidence rows' start times to `account`.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            run = tree / "routine-results" / "20261026T000000Z-abc123"
            path = run / "k" / "with_skill" / "summary.json"
            path.parent.mkdir(parents=True)
            snap_at = NOW - timedelta(hours=1)
            doc = {"usage": {"measured": {"meter": {"snapshots": [{
                "at": iso(snap_at), "info": {"unifiedWindows": {"seven_day": {
                    "utilization": 0.05,
                    "resetsAt": int((NOW + timedelta(days=1)).timestamp())}}}}]}}}}
            path.write_text(json.dumps(doc))
            runs = [fire(1, "Fire eval routine: improve a ", NOW - timedelta(days=1))]
            state = guard.account(tree, LIMITS, NOW, plan_week._fire_pair(runs, NOW),
                                  guard.improve_counts(runs, NOW),
                                  since=NOW - guard.WINDOW,
                                  improve_started=guard.improve_starts(runs, NOW))
            self.assertEqual(state["improve_covered"], 1)
            self.assertGreater(state["improve_charge"], 0.05)
            self.assertAlmostEqual(
                state["pending"], min(state["improve_charge"], 0.05), places=12)


class DocsTests(unittest.TestCase):

    def test_adr_0012_records_the_bare_baseline_addendum(self):
        text = (ROOT / "docs/decisions/0012-ab-runs-in-the-deployed-context-minus-the-subject.md"
                ).read_text()
        self.assertIn("## Bare baseline addendum (the owner, 2026-10-27)", text)
        self.assertIn("one time per model\nper version of model", text)
        self.assertIn("Ablation, the in-place pair of decision 1, stays the main approach", text)
        doc = (ROOT / "docs/how-it-works.md").read_text()
        self.assertIn("6. **Bare baselines**", doc)
        self.assertIn("bare-baseline-addendum-the-owner-2026-10-27", doc)

    def test_adr_0010_records_the_2026_10_26_addendum(self):
        text = (ROOT / "docs/decisions/0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md"
                ).read_text()
        self.assertIn("Decision 4 addendum (2026-10-26)", text)
        self.assertIn("getting the closed loop completed and working for all aspects of "
                      "self-improvement", text)

    def test_agents_md_table_names_the_planner(self):
        text = (ROOT / "AGENTS.md").read_text()
        row = next(line for line in text.splitlines()
                   if line.startswith("  | `routine-eval-weekly.yml`"))
        self.assertIn("plan_week.py", row)


if __name__ == "__main__":
    unittest.main()
