#!/usr/bin/env python3
"""Issue #70, part 1: the skill retirement verdict and `gates/latest.json`.

The owner's decisions: the thresholds (2026-10-10, #70) and pooled evidence
(2026-10-25), which replaced #70's four weekly sweeps, with the review rounds
of the same day: the run is the unit, the arms are independent samples pooled
by their own keys, cells come from the fixtures on main and pool only runs of
the content on main up to `--as-of`, duplicate runs count once, a run with an
errored trial does not count, floors under every standard error, zero
variance decides nothing, `cannot-discriminate`, a stricter retire side with
Holm-corrected checks, stateless confirmation at the two largest looks, and
one retirement per evaluation. harness/gates.py classifies each cell and
takes the verdict; scripts/compute_gates.py writes the artifact.

Hermetic: canned trial summaries, results trees, a fixture tree and a registry
checkout in a temporary directory, no network, no clock.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent.parent
ROOT = TEST_DIR.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))

import compute_gates  # noqa: E402
import context  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import run_eval  # noqa: E402

POLICY_FILE = ROOT / "evals" / "gate-policy.yml"
POLICY = gates.parse_gate_policy(POLICY_FILE.read_text(encoding="utf-8"))["skills"]
#: The committed objective margin, under a floor of p(1 - p) over the scored
#: trials, needs hundreds of trials per arm before an objective delta can be
#: no-benefit (`test_the_committed_margin_needs_more_than_twenty_runs_of_three`).
#: The cell rules are tested at a wider margin; the compute_gates tests use
#: the committed policy on judge-carried fixtures.
TEST_POLICY = {**POLICY, "objective_margin": 0.4}
CLIS = ["1.0.0 (Claude Code)", "1.0.1 (Claude Code)"]
T = 100
#: Runs that differ a little from run to run, the same on both arms.
FLAT = [(80, 80, 80), (80, 80, 79)] * 10
FLAT_JUDGE = [7.0, 7.2] * 10
GAIN = [(90, 90, 90), (90, 90, 89)] * 5
LOSS = [(50, 50, 50), (50, 50, 49)] * 5
NOISY = [(100, 100, 100), (20, 20, 20)] * 5
NOISY_BACK = [(20, 20, 20), (100, 100, 100)] * 5
#: The content on main the canned runs were made of: one flat fixture.
EXPECT = {"": {"fixture_digest": "f1", "tested_digest": "t1"}}
AS_OF = "20261231T000000Z"


def trial(passed=80, total=T, offset=0, overall=None, *, fail=None, error=None,
          infra=False, skipped=False, pairwise=None, cost=0.25):
    """One trial summary in the shape harness/run_eval.py writes. The failing
    checks rotate with `offset` (so no check fails in every trial) unless
    `fail` names them. `error` is an error type."""
    failing = (set(fail) if fail is not None
               else {(offset * (total - passed) + i) % total for i in range(total - passed)})
    checks = [{"id": f"c{i}", "passed": i not in failing, "detail": "ok"}
              for i in range(total)]
    if infra:
        checks[0] = {"id": "c0", "passed": False,
                     "detail": "scorer_sandbox_unavailable exit=1"}
    if skipped:
        judge = {"status": "skipped", "reason": "objective-decided"}
    elif pairwise is not None:
        judge = {"mode": "pairwise", "rank": 1, "score": 1, "dimensions_mean": pairwise}
    elif overall is not None:
        judge = {"dimensions": [], "overall": overall}
    else:
        judge = None
    return {"error": {"type": error, "detail": ""} if error else None,
            "agent": {"cost_usd": cost}, "objective_checks": checks, "judge": judge,
            "judge_conditional": False}


def stamp(r: int) -> str:
    return f"202610{1 + r // 24:02d}T{r % 24:02d}0000Z"


def arm_run(role, trials, r, *, model="model-a", fixture=None, cli=None,
            tested="t1", fdig="f1", ctx=None, carried=False, skill="demo",
            conditional=False, pair="p"):
    """One index arm run, as compute_gates.build_index produces it."""
    path = f"routine-results/{stamp(r)}-00000{r % 10}/{skill}/{stamp(r)}"
    return {"skill": skill, "fixture": fixture, "model": model, "role": role,
            "timestamp": stamp(r), "path": path, "report": f"{path}/report.md",
            "tested_digest": tested, "fixture_digest": fdig,
            "arm_context_digest": ctx or f"{role}-ctx", "pair_key": pair,
            "cli_versions": [cli or CLIS[r % 2]], "run": {"id": stamp(r)},
            "judge_carried": carried, "judge_conditional": conditional,
            "trials": [gates.trial_record(t) for t in trials]}


def figures(figure, spread=True, k=3):
    """A run's trial judge figures around `figure`: spread a little within
    the run unless `spread` is false."""
    if figure is None:
        return [None] * k
    return [figure + ((i - 1) / 10 if spread else 0) for i in range(k)]


def series(role, patterns, judges=None, *, total=T, start=0, pairwise=False,
           spread=True, **kw):
    """One arm run per pattern (passed counts of its trials)."""
    out = []
    for i, pattern in enumerate(patterns):
        r = start + i
        marks = figures(judges[i] if judges else None, spread, len(pattern))
        trials = [trial(p, total, 3 * r + k, None if pairwise else marks[k],
                        pairwise=marks[k] if pairwise else None)
                  for k, p in enumerate(pattern)]
        out.append(arm_run(role, trials, r, **kw))
    return out


def cell(with_runs, without_runs, *, carried=False, policy=TEST_POLICY):
    return gates.classify_cell(with_runs, without_runs, policy, carried)


def flat_cell(n=10, **kw):
    return (series("with", FLAT[:n], FLAT_JUDGE, **kw)
            + series("without", FLAT[:n], FLAT_JUDGE, **kw))


class PolicyTests(unittest.TestCase):

    def test_committed_policy_loads_with_its_numbers(self):
        self.assertEqual(POLICY, {
            "objective_margin": 0.05, "judge_margin": 0.5, "benefit_confidence": 0.90,
            "retire_confidence": 0.975, "check_confidence": 0.90, "min_runs": 5,
            "min_cli_versions": 2, "ceiling_fraction": 0.90, "judge_ceiling": 9.0,
            "floor_fraction": 0.10, "bootstrap_resamples": 2000,
            "retire_looks": [5, 10, 20],
            "retirement_mode": "auto"})

    def _text(self, **changes):
        values = {**POLICY, **changes}
        return "skills:\n" + "".join(f"  {k}: {v}\n" for k, v in values.items()
                                     if v is not None)

    def _mode(self, text):
        return gates.parse_gate_policy(text)["skills"]["retirement_mode"]

    def test_missing_mode_is_proposal(self):
        self.assertEqual(self._mode(self._text(retirement_mode=None)), "proposal")

    def test_invalid_mode_is_proposal(self):
        for bad in ("automatic", "[auto]", "1", "true"):
            self.assertEqual(self._mode(self._text(retirement_mode=bad)), "proposal", bad)

    def test_proposal_mode_loads(self):
        self.assertEqual(self._mode(self._text(retirement_mode="proposal")), "proposal")

    def test_bad_values_are_refused(self):
        for key, bad in (("ceiling_fraction", 1.5), ("retire_confidence", 1),
                         ("benefit_confidence", 0.4), ("min_runs", 1),
                         ("min_runs", 2.5), ("bootstrap_resamples", "true"),
                         ("judge_ceiling", 11), ("objective_margin", -0.1),
                         ("floor_fraction", 1), ("retire_looks", "[10, 5]"),
                         ("retire_looks", "[3, 10]"), ("retire_looks", "[]")):
            with self.assertRaises(gates.GatePolicyError, msg=f"{key}={bad}"):
                gates.parse_gate_policy(self._text(**{key: bad}))


    def test_missing_and_unknown_keys_are_refused(self):
        with self.assertRaises(gates.GatePolicyError):
            gates.parse_gate_policy(self._text(judge_margin=None))
        with self.assertRaises(gates.GatePolicyError):
            gates.parse_gate_policy(self._text() + "  sweeps: 4\n")
        with self.assertRaises(gates.GatePolicyError):
            gates.parse_gate_policy("- not a mapping\n")


class StatisticsTests(unittest.TestCase):

    def test_t_quantiles_match_the_standard_table(self):
        for p, df, value in ((0.95, 1, 6.314), (0.95, 5, 2.015), (0.95, 30, 1.697),
                             (0.975, 5, 2.571), (0.975, 10, 2.228), (0.995, 3, 5.841)):
            self.assertAlmostEqual(gates.t_quantile(p, df), value, places=3)

    def test_bootstrap_is_deterministic_and_order_free(self):
        rng = random.Random(1)
        a = [rng.random() for _ in range(9)]
        b = [rng.random() for _ in range(7)]
        first = gates.bootstrap_deltas(a, b, 2000)
        self.assertEqual(first, gates.bootstrap_deltas(a, b, 2000))
        self.assertEqual(first, gates.bootstrap_deltas(a[::-1], b[::-1], 2000))
        self.assertEqual(gates.bounds(a, b, 0.95, 2000), gates.bounds(a, b, 0.95, 2000))

    def test_welch_by_hand(self):
        # a: mean 3.5, sample variance 3.5; b: mean 2, variance 0. se =
        # sqrt(3.5 / 6); Welch df = 5 exactly; delta 1.5.
        delta, se, df = gates.welch([1, 2, 3, 4, 5, 6], [2] * 6)
        self.assertEqual((delta, df), (1.5, 5))
        self.assertAlmostEqual(se, (3.5 / 6) ** 0.5, places=12)
        low, high = gates.bounds([1, 2, 3, 4, 5, 6], [2] * 6, 0.95, 2000)
        self.assertAlmostEqual(low, 1.5 - 2.0150484 * (3.5 / 6) ** 0.5, places=6)
        self.assertAlmostEqual(high, 1.5 + 2.0150484 * (3.5 / 6) ** 0.5, places=6)

    def test_welch_df_falls_back_when_its_denominator_underflows(self):
        # Round 4, B3: variances near 1e-200 square to 0.0, and the
        # Welch-Satterthwaite ratio divided by zero. The fallback is the
        # smaller arm's n - 1.
        a, b = [0.0, 1e-100, 2e-100], [0.0, 1e-100, 2e-100, 3e-100, 0.0]
        delta, se, df = gates.welch(a, b)
        self.assertEqual(df, 2)
        self.assertGreater(se, 0)
        self.assertEqual(gates.welch(b, a)[2], 2)
        # A denominator that overflows to infinity takes the same fallback.
        self.assertEqual(gates.welch([0.0, 1e85, -1e85], [0.0, 1e85, -1e85, 5.0])[2], 2)

    def test_welch_df_is_floored_with_a_minimum_of_one(self):
        # Unequal variances: df = 1.83, floored to 1.
        self.assertEqual(gates.welch([0, 10], [0, 1, 0, 1, 0, 1])[2], 1)

    def test_the_binomial_floor_by_hand(self):
        # p = 0.8 over 30 scored trials: p(1 - p)/n = 0.16/30.
        self.assertAlmostEqual(gates.binomial_floor(0.8, 30), 0.16 / 30, places=15)
        # p is clipped to [1/(2n), 1 - 1/(2n)]: all passed is not zero.
        self.assertAlmostEqual(gates.binomial_floor(1.0, 30),
                               (1 / 60) * (1 - 1 / 60) / 30, places=15)
        self.assertEqual(gates.binomial_floor(None, 0), 0.0)

    def test_the_judge_floor_by_hand(self):
        # Within-run deviations: (7, 7, 7.5) has sum of squares 1/6 on 2 df,
        # (7, 7, 7) none on 2: pooled variance (2/6)/10 over five runs.
        figures_ = [[7, 7, 7.5]] * 2 + [[7, 7, 7]] * 3
        self.assertAlmostEqual(gates.judge_floor(figures_), (2 / 6) / 10 / 5, places=15)

    def test_the_floor_raises_each_arms_standard_error(self):
        a, b = [0.8] * 9 + [0.81], [0.8] * 10
        bare = gates.welch(a, b)
        floored = gates.welch(a, b, floors=(0.16 / 300, 0.16 / 300))
        self.assertAlmostEqual(floored[1], (2 * 0.16 / 300) ** 0.5, places=12)
        self.assertGreater(floored[1], bare[1])
        # Welch-Satterthwaite over the floored variances: two equal arms of
        # ten values each give 18.
        self.assertEqual(floored[2], 18)

    def test_reported_bounds_are_at_least_as_wide_as_the_bootstrap(self):
        a = [0.9, 0.7, 1.0, 0.6, 0.8, 0.9]
        b = [0.5, 0.8, 0.4, 0.7, 0.6, 0.3]
        deltas = gates.bootstrap_deltas(a, b, 2000)
        boot = (gates._percentile(deltas, 0.05), gates._percentile(deltas, 0.95))
        low, high = gates.bounds(a, b, 0.95, 2000)
        self.assertLess(low, boot[0])
        self.assertGreaterEqual(high, boot[1])

    def test_holm_correction_across_checks(self):
        a, b = [1, 2 / 3, 1, 2 / 3, 1], [2 / 3, 1 / 3, 2 / 3, 1, 1 / 3]
        self.assertEqual(gates.confident_checks({"x": a}, {"x": b}, 0.90, 2000), ["x"])
        null = {f"n{i}": [0.5, 0.6, 0.5, 0.6, 0.5] for i in range(19)}
        self.assertEqual(gates.confident_checks({"x": a, **null}, {"x": b, **null},
                                                0.90, 2000), [])

    def test_a_zero_variance_check_is_never_confident(self):
        self.assertEqual(gates.confident_checks({"x": [1.0] * 5}, {"x": [0.0] * 5},
                                                0.90, 2000), [])

    def test_a_checks_floor_can_withhold_confidence(self):
        a, b = [1, 2 / 3, 1, 2 / 3, 1], [2 / 3, 1 / 3, 2 / 3, 1, 1 / 3]
        wide = {"x": (0.05, 0.05)}
        self.assertEqual(gates.confident_checks({"x": a}, {"x": b}, 0.90, 2000,
                                                floors=wide), [])


class TrialTests(unittest.TestCase):

    def test_infrastructure_detail_makes_the_trial_an_error(self):
        record = gates.trial_record(trial(infra=True))
        self.assertFalse(record["scored"])
        self.assertTrue(record["infrastructure"])
        self.assertTrue(gates.trial_record(trial())["scored"])
        self.assertTrue(gates.trial_record(trial(error="scorer_unavailable"))
                        ["infrastructure"])
        self.assertFalse(gates.trial_record(trial(error="agent_failed"))["infrastructure"])
        self.assertTrue(gates.trial_record(trial(error="scorer_sandbox_unavailable"))
                        ["infrastructure"])

    def test_only_a_failed_checks_leading_marker_is_infrastructure(self):
        def with_detail(detail, passed=False):
            doc = trial()
            doc["objective_checks"][0] = {"id": "c0", "passed": passed, "detail": detail}
            return gates.trial_record(doc)
        self.assertTrue(with_detail("scorer_sandbox_unavailable exit=-1")["infrastructure"])
        # The agent's own text, anywhere but the start, or on a passed check,
        # is an ordinary check result.
        for detail, passed in (("command_nonzero exit=1 scorer_sandbox_unavailable", False),
                               ("x scorer_sandbox_unavailable exit=1", False),
                               ("scorer_sandbox_unavailable exit=1", True)):
            with self.subTest(detail=detail, passed=passed):
                record = with_detail(detail, passed)
                self.assertTrue(record["scored"])
                self.assertFalse(record["infrastructure"])

    def test_pairwise_is_read_from_dimensions_mean(self):
        self.assertEqual(gates.trial_record(trial(pairwise=8.0))["judge"], 8.0)
        self.assertEqual(gates.trial_record(trial(overall=6.5))["judge"], 6.5)
        self.assertIsNone(gates.trial_record(trial(skipped=True))["judge"])

    def test_a_malformed_trial_is_refused_by_name(self):
        good = trial()
        for name, doc in (
                ("checks not a list", {**good, "objective_checks": 5}),
                ("check not a mapping", {**good, "objective_checks": [5]}),
                ("passed not a bool", {**good, "objective_checks": [{"id": "c", "passed": 1}]}),
                ("judge not a mapping", {**good, "judge": [7]}),
                ("judge out of range", {**good, "judge": {"overall": 1e308}}),
                ("agent not a mapping", {**good, "agent": "x"})):
            with self.subTest(name=name), self.assertRaises(gates.MalformedSummary):
                gates.trial_record(doc)

    def test_an_unusable_cost_is_unknown_and_a_huge_sum_stays_finite(self):
        for bad in (-1, float("inf"), "1", True):
            self.assertIsNone(gates.trial_record(trial(cost=bad))["cost_usd"], bad)
        numbers = gates.run_numbers([gates.trial_record(trial(cost=1e308))] * 3, True)
        self.assertIsNone(numbers["cost_usd"])
        self.assertEqual(numbers["cost_unknown_trials"], 3)

    def test_an_agent_errored_trial_scores_zero_and_the_run_counts(self):
        good = [gates.trial_record(trial(overall=7.0)) for _ in range(2)]
        checks = sorted(good[0]["checks"])
        errored = good + [gates.trial_record(trial(error="timeout"))]
        numbers = gates.run_numbers(errored, True, checks)
        self.assertIsNone(numbers["skip_reason"])
        self.assertEqual((numbers["scored_trials"], numbers["errored_trials"]), (3, 1))
        self.assertAlmostEqual(numbers["run_mean"], (0.8 + 0.8 + 0) / 3)
        self.assertEqual((numbers["passed"], numbers["total"]), (160, 300))
        # No judge figure for the errored trial, so none for the run.
        self.assertIsNone(numbers["judge_mean"])
        # An infrastructure error is left out; two trials still count
        # (MIN_TRIALS_PER_RUN is 1, round 5), and none do not.
        infra = good + [gates.trial_record(trial(infra=True))]
        self.assertIsNone(gates.run_numbers(infra, True, checks)["skip_reason"])
        self.assertEqual(gates.run_numbers(infra[2:], True, checks)["skip_reason"],
                         "fewer than 1 scored or errored trials")

    def test_a_scored_trial_without_checks_on_a_fixture_with_checks_is_skipped(self):
        good = [gates.trial_record(trial()) for _ in range(3)]
        empty = gates.trial_record({**trial(), "objective_checks": []})
        self.assertTrue(empty["scored"])
        numbers = gates.run_numbers(good[:2] + [empty], True, sorted(good[0]["checks"]))
        self.assertEqual(numbers["skip_reason"], gates.NO_CHECKS_REASON)
        # The fixture's own checks count even when no trial reports one.
        self.assertEqual(gates.run_numbers([empty] * 3, True, (), True)["skip_reason"],
                         gates.NO_CHECKS_REASON)
        self.assertIsNone(gates.run_numbers([empty] * 3, True)["skip_reason"])

    def test_the_judge_counts_only_when_every_trial_was_judged_unconditionally(self):
        judged = [gates.trial_record(trial(overall=f)) for f in (7.0, 7.1, 7.2)]
        self.assertAlmostEqual(gates.run_numbers(judged, True)["judge_mean"], 7.1)
        self.assertTrue(gates.run_numbers(judged, True)["judge_spread"])
        self.assertIsNone(gates.run_numbers(judged, False)["judge_mean"])
        one_skipped = judged[:2] + [gates.trial_record(trial(skipped=True))]
        self.assertIsNone(gates.run_numbers(one_skipped * 2, True)["judge_mean"])
        same = [gates.trial_record(trial(overall=7.0))] * 3
        self.assertFalse(gates.run_numbers(same, True)["judge_spread"])


class CellTests(unittest.TestCase):

    def test_clear_gain_at_five_runs_is_benefit(self):
        self.assertEqual(cell(series("with", GAIN[:5]), series("without", LOSS[:5]))["class"],
                         "benefit")

    def test_flat_runs_are_no_benefit_only_at_two_looks(self):
        runs = flat_cell(5)
        result = cell(runs[:5], runs[5:])
        self.assertEqual((result["class"], [lk["class"] for lk in result["looks"]]),
                         ("no-benefit-pending", ["no-benefit"]))
        runs = flat_cell(10)
        result = cell(runs[:10], runs[10:])
        self.assertEqual((result["class"], [lk["n"] for lk in result["looks"]]),
                         ("no-benefit", [5, 10]))

    def test_the_committed_margin_needs_more_than_twenty_runs_of_three(self):
        runs = flat_cell(20)
        result = cell(runs[:20], runs[20:], policy=POLICY)
        self.assertEqual((result["class"], result["looks"][-1]["n"]), ("insufficient", 20))

    def test_one_flipped_check_in_thirty_identical_trials_is_not_no_benefit(self):
        # Ten runs per arm, every trial passing 8 of 10 checks; one with-arm
        # trial passes one more. The run means barely vary, so without a
        # floor the bound is tight and this would be no-benefit.
        def runs(role):
            out = []
            for r in range(10):
                marks = figures(FLAT_JUDGE[r])
                trials = [trial(8, 10, 3 * r + k, j) for k, j in enumerate(marks)]
                if role == "with" and r == 9:
                    trials[0] = trial(9, 10, 27, marks[0])
                out.append(arm_run(role, trials, r))
            return out
        result = cell(runs("with"), runs("without"), policy=POLICY)
        self.assertEqual(result["class"], "insufficient")
        self.assertEqual(result["looks"][-1]["n"], 10)

    def test_four_runs_are_insufficient(self):
        runs = flat_cell(4)
        self.assertEqual(cell(runs[:4], runs[4:])["class"], "insufficient")

    def test_one_cli_version_is_insufficient(self):
        runs = flat_cell(cli=CLIS[0])
        self.assertEqual(cell(runs[:10], runs[10:])["class"], "insufficient")

    def test_a_run_needs_one_scored_trial(self):
        runs = flat_cell(5)
        runs[0]["trials"] = [gates.trial_record(trial(infra=True))] * 3
        result = cell(runs[:5], runs[5:])
        self.assertEqual(result["class"], "insufficient")
        self.assertEqual(result["with"]["runs"], 4)
        self.assertEqual(result["skipped"], {"fewer than 1 scored or errored trials": 1})
        # One infrastructure error among three leaves two trials: it counts.
        runs = flat_cell(5)
        runs[0]["trials"][0] = gates.trial_record(trial(infra=True))
        self.assertEqual(cell(runs[:5], runs[5:])["with"]["runs"], 5)

    def test_errored_trials_are_never_scored_around(self):
        # Round 2's reproducer: the without arm errors in 2 of 5 trials per
        # run. Scored around, only the trials that went well would compare.
        with_runs = series("with", [(80,) * 5] * 10, FLAT_JUDGE)
        without_runs = series("without", [(80,) * 5] * 10, FLAT_JUDGE)
        for run in without_runs:
            run["trials"][:2] = [gates.trial_record(trial(error="agent_failed"))] * 2
        result = cell(with_runs, without_runs)
        self.assertNotIn(result["class"], ("no-benefit", "no-benefit-pending"))
        self.assertEqual((result["without"]["runs"], result["skipped"]), (10, {}))

    def test_without_arm_timeouts_censoring_its_failures_cannot_retire(self):
        # Round 3's reproducer (p2.py): twelve clean with runs; twenty-four
        # without runs, half of them with a timed-out trial. Dropping those
        # runs left only the without arm's good runs, level with the skill.
        with_runs = series("with", [(80, 80, 80)] * 12, [7.0, 7.2] * 6, carried=True)
        without_runs = []
        for r in range(24):
            if r % 2:
                trials = [trial(80, T, 3 * r + k, 7.0 + (k - 1) / 10) for k in range(2)] \
                    + [trial(0, T, 0, None, error="timeout")]
                without_runs.append(arm_run("without", trials, 100 + r, carried=True))
            else:
                without_runs += series("without", [(80, 80, 80)], [7.0 if r % 4 else 7.2],
                                       start=100 + r, carried=True)
        result = cell(with_runs, without_runs, carried=True, policy=POLICY)
        self.assertNotIn(result["class"], ("no-benefit", "no-benefit-pending"))
        self.assertEqual(result["without"]["runs"], 24)

    def test_without_arm_timeouts_can_show_benefit(self):
        def runs(role):
            out = []
            for r in range(10):
                marks = figures(FLAT_JUDGE[r])
                trials = [trial(p, T, 3 * r + k, marks[k]) for k, p in enumerate(FLAT[r])]
                if role == "without":
                    trials[2] = trial(0, T, 0, None, error="timeout")
                out.append(arm_run(role, trials, r))
            return out
        self.assertEqual(cell(runs("with"), runs("without"))["class"], "benefit")

    def test_an_errored_trial_blocks_no_benefit_at_the_looks(self):
        # One budget stop in one with run: that run has no judge figure,
        # the other nine do, and the numbers are still no-benefit.
        runs = flat_cell(10)
        runs[0]["trials"].append(gates.trial_record(trial(error="budget_exceeded")))
        result = cell(runs[:10], runs[10:])
        self.assertEqual(result["full_class"], "no-benefit")
        self.assertEqual(result["class"], "insufficient")
        self.assertIn("agent-errored", result["reason"])

    def test_a_run_without_check_results_keeps_the_cell_from_retiring(self):
        runs = flat_cell(10)
        runs[3]["trials"][0] = gates.trial_record({**trial(), "objective_checks": []})
        result = cell(runs[:10], runs[10:])
        self.assertEqual(result["skipped"], {gates.NO_CHECKS_REASON: 1})
        self.assertEqual(result["class"], "insufficient")

    def test_noisy_runs_are_insufficient(self):
        result = cell(series("with", NOISY[:5], FLAT_JUDGE),
                      series("without", NOISY_BACK[:5], FLAT_JUDGE))
        self.assertEqual(result["class"], "insufficient")

    def test_zero_variance_decides_nothing(self):
        same = [(80, 80, 80)] * 10
        self.assertEqual(cell(series("with", same, FLAT_JUDGE),
                              series("without", same, FLAT_JUDGE))["class"], "insufficient")
        # Constant but apart: still no decision.
        self.assertEqual(cell(series("with", [(90, 90, 90)] * 5),
                              series("without", [(40, 40, 40)] * 5))["class"], "insufficient")

    def test_pass_rate_exactly_point_nine_is_ceiling(self):
        same = [(90, 90, 90)] * 5
        self.assertEqual(cell(series("with", same), series("without", same))["class"],
                         "ceiling")

    def test_pass_rate_point_eight_nine_is_not_ceiling(self):
        same = [(89, 89, 89)] * 5
        result = cell(series("with", same), series("without", same))
        self.assertEqual(result["class"], "insufficient")

    def test_a_check_failing_everywhere_cannot_discriminate(self):
        def runs(role):
            return [arm_run(role, [trial(8, 10, fail={9, (3 * r + k) % 9}) for k in range(3)], r)
                    for r in range(5)]
        self.assertEqual(cell(runs("with"), runs("without"))["class"], "cannot-discriminate")

    def test_both_arms_near_the_floor_cannot_discriminate(self):
        low = [(1, 1, 1), (1, 1, 0)] * 3
        self.assertEqual(cell(series("with", low[:5], total=10),
                              series("without", low[:5], total=10))["class"],
                         "cannot-discriminate")

    def test_insufficient_runs_come_before_cannot_discriminate(self):
        low = [(1, 1, 1), (1, 1, 0)] * 3
        self.assertEqual(cell(series("with", low[:4], total=10),
                              series("without", low[:4], total=10))["class"],
                         "insufficient")

    def test_retire_side_uses_the_stricter_bound(self):
        wide = [(80, 80, 80), (70, 70, 70)] * 3
        runs = (series("with", wide[:5], FLAT_JUDGE), series("without", wide[:5], FLAT_JUDGE))
        strict = {**POLICY, "objective_margin": 0.2}
        self.assertEqual(cell(*runs, policy=strict)["class"], "insufficient")
        loose = {**strict, "retire_confidence": 0.6}
        self.assertEqual(cell(*runs, policy=loose)["class"], "no-benefit-pending")

    def test_one_confidently_better_check_is_benefit(self):
        # Overall the arms are level; check A is better with the skill and B
        # worse, by the same amount, and C varies on both.
        def runs(role):
            out = []
            for r in range(5):
                even = r % 2 == 0
                a, b = ((3, 0) if even else (2, 1)) if role == "with" else \
                    ((0, 3) if even else (1, 2))
                c = 3 if even else 2
                trials = [trial(fail={i for i, n in enumerate((a, b, c)) if k >= n},
                                total=3, overall=7.0) for k in range(3)]
                out.append(arm_run(role, trials, r))
            return out
        result = cell(runs("with"), runs("without"))
        self.assertEqual(result["class"], "benefit")
        self.assertEqual(result["confident_checks"], ['"c0"'])

    def test_judge_needed_needs_five_judged_runs(self):
        judges = FLAT_JUDGE[:4] + [None]
        result = cell(series("with", FLAT[:5], judges), series("without", FLAT[:5], judges))
        self.assertEqual(result["class"], "insufficient")

    def test_a_conditionally_judged_run_has_no_judge_figure(self):
        result = cell(*[series(role, FLAT[:10], FLAT_JUDGE, conditional=True)
                        for role in ("with", "without")])
        self.assertEqual(result["class"], "insufficient")
        self.assertEqual(result["with"]["judge_runs"], 0)

    def test_the_judge_needs_spread_within_two_runs_per_arm(self):
        result = cell(*[series(role, FLAT[:10], FLAT_JUDGE, spread=False)
                        for role in ("with", "without")])
        self.assertEqual(result["class"], "insufficient")
        # Spread in two runs of each arm is enough.
        runs = [series(role, FLAT[:10], FLAT_JUDGE, spread=False)
                for role in ("with", "without")]
        for arm in runs:
            for run in arm[:2]:
                run["trials"] = [{**t, "judge": t["judge"] + (k - 1) / 10}
                                 for k, t in enumerate(run["trials"])]
        self.assertEqual(cell(*runs)["class"], "no-benefit")

    def test_the_judge_standard_error_is_floored_by_the_within_run_spread(self):
        # The reproducer: judge figures (7, 7, 7.5) twice and (7, 7, 7) three
        # times, on both arms.
        def runs(role):
            return [arm_run(role, [trial(80, T, 3 * r + k, j)
                                   for k, j in enumerate(marks)], r)
                    for r, marks in enumerate([[7, 7, 7.5]] * 2 + [[7, 7, 7]] * 3)]
        result = cell(runs("with"), runs("without"), carried=True)
        self.assertNotEqual(result["class"], "no-benefit")
        wj, oj = result["with"]["judge_run_means"], result["without"]["judge_run_means"]
        floor = gates.judge_floor([[7, 7, 7.5]] * 2 + [[7, 7, 7]] * 3)
        floored = gates.bounds(wj, oj, POLICY["retire_confidence"], 2000,
                               floors=(floor, floor))[1]
        self.assertAlmostEqual(result["judge"]["retire_upper"], floored, places=12)
        self.assertGreater(floored, gates.bounds(wj, oj, POLICY["retire_confidence"],
                                                 2000)[1])

    def test_skipped_judge_is_not_needed_when_the_objective_gains(self):
        def runs(role, pattern):
            return [arm_run(role, [trial(p, offset=3 * r + k, skipped=True)
                                   for k, p in enumerate(pattern[r])], r)
                    for r in range(5)]
        self.assertEqual(cell(runs("with", GAIN), runs("without", LOSS))["class"], "benefit")

    def test_saturated_objective_with_judge_gain_is_benefit(self):
        full = [(100, 100, 100)] * 5
        self.assertEqual(cell(series("with", full, [9.6, 9.4] * 3),
                              series("without", full, [8.0, 7.8] * 3))["class"], "benefit")

    def test_class_c_keeps_on_its_objective_checks_too(self):
        # Round 3's reproducer (p1.py): a pairwise fixture whose objective
        # checks pass 0.948 with the skill and 0.398 without, the judge level.
        result = cell(series("with", [(95, 95, 95), (95, 95, 94)] * 5, [7.0, 7.2] * 5,
                             pairwise=True, carried=True),
                      series("without", [(40, 40, 40), (40, 40, 39)] * 5, [7.0, 7.2] * 5,
                             pairwise=True, carried=True), carried=True, policy=POLICY)
        self.assertEqual(result["class"], "benefit")
        self.assertIn("objective", result["reason"])
        # And a judge gain alone still keeps.
        result = cell(series("with", LOSS[:5], [9.0, 8.8] * 3),
                      series("without", GAIN[:5], [6.0, 6.2] * 3), carried=True)
        self.assertEqual(result["class"], "benefit")

    def test_class_c_is_no_benefit_only_when_its_objective_is_too(self):
        runs = flat_cell(10)
        self.assertEqual(cell(runs[:10], runs[10:], carried=True)["class"], "no-benefit")
        # The committed objective margin is not met by thirty trials per arm.
        result = cell(runs[:10], runs[10:], carried=True, policy=POLICY)
        self.assertEqual(result["class"], "insufficient")
        self.assertIn("objective", result["looks"][-1]["reason"])
        # Without objective checks the judge alone decides.
        def judge_only(role):
            return [arm_run(role, [{**trial(overall=j), "objective_checks": []}
                                   for j in figures(FLAT_JUDGE[r])], r) for r in range(10)]
        result = cell(judge_only("with"), judge_only("without"), carried=True, policy=POLICY)
        self.assertEqual(result["class"], "no-benefit")

    def test_class_c_ceiling_on_the_judge_figure(self):
        high = [9.2, 9.4] * 3
        self.assertEqual(cell(series("with", FLAT[:5], high),
                              series("without", FLAT[:5], high), carried=True)["class"],
                         "ceiling")

    def test_pairwise_class_c_reads_dimensions_mean(self):
        result = cell(series("with", FLAT[:5], [8.0, 8.2] * 3, pairwise=True),
                      series("without", FLAT[:5], [6.0, 6.2] * 3, pairwise=True),
                      carried=True)
        self.assertEqual(result["class"], "benefit")

    def test_pairwise_without_a_judge_figure_is_insufficient(self):
        self.assertEqual(cell(series("with", FLAT[:5]), series("without", FLAT[:5]),
                              carried=True)["class"], "insufficient")


class LookTests(unittest.TestCase):

    def test_a_later_look_that_is_not_no_benefit_decides(self):
        swing = [(100, 100, 100), (0, 0, 0)] * 3
        with_runs = (series("with", FLAT[:5], FLAT_JUDGE)
                     + series("with", swing[:5], FLAT_JUDGE, start=5))
        without_runs = (series("without", FLAT[:5], FLAT_JUDGE)
                        + series("without", swing[1:6], FLAT_JUDGE, start=5))
        result = cell(with_runs, without_runs)
        self.assertEqual([lk["class"] for lk in result["looks"]],
                         ["no-benefit", "insufficient"])
        self.assertEqual(result["class"], "insufficient")

    def test_no_benefit_needs_both_of_the_two_largest_looks(self):
        # The first five runs show one CLI version, so the five-run look is
        # insufficient; the ten-run look alone is not enough.
        def runs(role, n):
            return (series(role, FLAT[:5], FLAT_JUDGE, cli=CLIS[0])
                    + series(role, FLAT[5:n], FLAT_JUDGE[5:n], start=5, cli=CLIS[1]))
        result = cell(runs("with", 10), runs("without", 10))
        self.assertEqual(([lk["class"] for lk in result["looks"]], result["class"]),
                         (["insufficient", "no-benefit"], "no-benefit-pending"))
        # At twenty runs the two largest looks are ten and twenty.
        result = cell(runs("with", 20), runs("without", 20))
        self.assertEqual(([lk["class"] for lk in result["looks"]], result["class"]),
                         (["insufficient", "no-benefit", "no-benefit"], "no-benefit"))

    def test_benefit_is_declared_at_any_count(self):
        result = cell(series("with", GAIN[:7]), series("without", LOSS[:7]))
        self.assertEqual((result["class"], [lk["n"] for lk in result["looks"]]),
                         ("benefit", [5]))

    def test_the_looks_use_the_first_runs_in_timestamp_order(self):
        with_runs = series("with", FLAT[:10], FLAT_JUDGE) + series(
            "with", NOISY[:3], FLAT_JUDGE, start=10)
        without_runs = series("without", FLAT[:10], FLAT_JUDGE) + series(
            "without", NOISY_BACK[:3], FLAT_JUDGE, start=10)
        result = cell(with_runs[::-1], without_runs[::-1])
        self.assertEqual(result["class"], "no-benefit")


class VerdictTests(unittest.TestCase):
    MODELS = ["model-a", "model-b", "model-c"]

    def verdict(self, runs, models=None, expected=None):
        return gates.retirement({"runs": runs}, models or self.MODELS, "demo", TEST_POLICY,
                                expected if expected is not None else EXPECT)

    @staticmethod
    def expect(*fixtures, tested="t1", fdig="f1"):
        return {f: {"fixture_digest": fdig, "tested_digest": tested} for f in fixtures}

    def flat(self, model, n=10, **kw):
        return flat_cell(n, model=model, **kw)

    def gain(self, model, **kw):
        return series("with", GAIN[:5], model=model, **kw) + series(
            "without", LOSS[:5], model=model, **kw)

    def ceiling(self, model, **kw):
        same = [(90, 90, 90)] * 5
        return series("with", same, model=model, **kw) + series(
            "without", same, model=model, **kw)

    def low(self, model, **kw):
        low = [(1, 1, 1), (1, 1, 0)] * 3
        return series("with", low[:5], model=model, total=10, **kw) + series(
            "without", low[:5], model=model, total=10, **kw)

    def test_every_cell_no_benefit_retires(self):
        runs = [r for m in self.MODELS for f in ("one", "two")
                for r in self.flat(m, fixture=f)]
        self.assertEqual(self.verdict(runs, expected=self.expect("one", "two"))["verdict"],
                         "retire")

    def test_a_cell_with_one_look_makes_the_verdict_pending(self):
        runs = [r for m in self.MODELS[1:] for r in self.flat(m)] + self.flat("model-a", 5)
        self.assertEqual(self.verdict(runs)["verdict"], "retire-pending")

    def test_one_benefit_cell_keeps(self):
        runs = [r for m in self.MODELS[1:] for r in self.flat(m)] + self.gain("model-a")
        self.assertEqual(self.verdict(runs)["verdict"], "keep")

    def test_one_ceiling_cell_among_no_benefit_is_ceiling(self):
        runs = [r for m in self.MODELS[1:] for r in self.flat(m, 5)] + self.ceiling("model-a")
        self.assertEqual(self.verdict(runs)["verdict"], "ceiling")

    def test_all_ceiling_cells_never_retire(self):
        fixtures = ("one", "two", "three", "four")
        runs = [r for m in self.MODELS for f in fixtures for r in self.ceiling(m, fixture=f)]
        self.assertEqual(self.verdict(runs, expected=self.expect(*fixtures))["verdict"],
                         "ceiling")

    def test_cannot_discriminate_outranks_ceiling(self):
        runs = self.ceiling("model-a") + self.low("model-b") + self.flat("model-c")
        self.assertEqual(self.verdict(runs)["verdict"], "cannot-discriminate")

    def test_insufficient_outranks_cannot_discriminate(self):
        runs = self.low("model-a") + self.flat("model-b") + self.flat("model-c", 4)
        self.assertEqual(self.verdict(runs)["verdict"], "insufficient")

    def test_a_roster_model_with_no_runs_is_insufficient(self):
        result = self.verdict([r for m in self.MODELS[:2] for r in self.flat(m)])
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual([c["model"] for c in result["cells"] if c["class"] == "missing"],
                         ["model-c"])

    def test_a_model_off_the_roster_is_ignored(self):
        runs = [r for m in self.MODELS for r in self.flat(m)] + self.gain("model-old")
        self.assertEqual(self.verdict(runs)["verdict"], "retire")

    def test_no_runs_is_insufficient(self):
        self.assertEqual(self.verdict([])["verdict"], "insufficient")

    def test_an_empty_roster_never_retires(self):
        # Round 3's reproducer: no roster model, so no cell, so nothing
        # failed "every cell shows no benefit" and the verdict was retire.
        for runs in ([], [r for r in self.flat("model-a")]):
            with self.subTest(runs=len(runs)):
                result = gates.retirement({"runs": runs}, [], "demo", POLICY, EXPECT)
                self.assertEqual((result["verdict"], result["cells"]), ("insufficient", []))

    def test_cells_come_from_the_fixtures_on_main(self):
        # A writing-adrs-like skill: four fixtures, runs on only one.
        fixtures = ("a", "b", "c", "d")
        runs = self.flat("model-a", fixture="a") + self.gain("model-a", fixture="gone")
        result = self.verdict(runs, ["model-a"], self.expect(*fixtures))
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual([(c["fixture"], c["class"]) for c in result["cells"]],
                         [("a", "no-benefit"), ("b", "missing"), ("c", "missing"),
                          ("d", "missing")])

    def test_runs_of_a_fixture_no_longer_on_main_are_dropped(self):
        runs = self.flat("model-a", fixture="a") + self.gain("model-a", fixture="gone")
        result = self.verdict(runs, ["model-a"], self.expect("a"))
        self.assertEqual(result["verdict"], "retire")
        self.assertEqual(len(result["runs_used"]), 20)

    def test_a_skill_with_no_fixture_on_main_is_insufficient(self):
        result = self.verdict(self.flat("model-a"), ["model-a"], {})
        self.assertEqual((result["verdict"], result["cells"]), ("insufficient", []))
        self.assertIn("no fixture", result["reason"])

    def test_the_without_arm_pools_across_skill_content(self):
        runs = (series("with", FLAT[:10], FLAT_JUDGE, tested="new")
                + series("without", FLAT[:10], FLAT_JUDGE, tested="old"))
        self.assertEqual(self.verdict(runs, ["model-a"], self.expect("", tested="new"))
                         ["verdict"], "retire")

    def test_new_skill_content_starts_confirmation_over(self):
        old = series("with", FLAT[:10], FLAT_JUDGE, tested="old")
        new = series("with", FLAT[10:15], FLAT_JUDGE[10:15], start=10, tested="new")
        without = series("without", FLAT[:15], FLAT_JUDGE)
        result = self.verdict(old + new + without, ["model-a"], self.expect("", tested="new"))
        self.assertEqual(result["verdict"], "retire-pending")
        self.assertEqual(result["cells"][0]["with"]["runs"], 5)

    def test_a_stale_run_of_old_content_does_not_choose_the_pool(self):
        # Ten flat runs of content A, three of B (on main), then one late run
        # of A stamped newest. The newest run is not what is current.
        a = series("with", FLAT[:10], FLAT_JUDGE, tested="A")
        b = series("with", FLAT[10:13], FLAT_JUDGE[10:13], start=10, tested="B")
        stale = series("with", FLAT[:1], FLAT_JUDGE, start=13, tested="A")
        without = series("without", FLAT[:14], FLAT_JUDGE)
        result = self.verdict(a + b + stale + without, ["model-a"],
                              self.expect("", tested="B"))
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual(result["cells"][0]["with"]["runs"], 3)

    def test_runs_of_another_fixture_or_arm_context_are_not_pooled(self):
        for field in ("fdig", "ctx"):
            with self.subTest(field=field):
                old = series("with", FLAT[:2], FLAT_JUDGE, **{field: "older"})
                new = series("with", FLAT[2:5], FLAT_JUDGE, start=2)
                without = series("without", FLAT[:5], FLAT_JUDGE)
                result = self.verdict(old + new + without, ["model-a"])
                self.assertEqual(result["verdict"], "insufficient")
                self.assertEqual(result["cells"][0]["with"]["runs"], 3)

    def test_a_moved_fixture_pools_its_flat_runs_only_with_the_same_digest(self):
        # #422/#427: a flat fixture nested as `nested`, content unchanged.
        # Its old runs (fixture None) pool with the new name; a run of other
        # content under the old name does not.
        old = (series("with", FLAT[:6], FLAT_JUDGE)
               + series("without", FLAT[:6], FLAT_JUDGE))
        new = (series("with", FLAT[6:10], FLAT_JUDGE, start=6, fixture="nested")
               + series("without", FLAT[6:10], FLAT_JUDGE, start=6, fixture="nested"))
        stale = series("with", FLAT[:3], FLAT_JUDGE, start=20, fdig="older")
        with mock.patch.dict(gates.FIXTURE_ALIASES, {("demo", ""): "nested"}):
            result = self.verdict(old + new + stale, ["model-a"], self.expect("nested"))
            mismatched = self.verdict(old, ["model-a"], self.expect("nested", fdig="changed"))
        self.assertEqual(result["verdict"], "retire")
        self.assertEqual((result["cells"][0]["fixture"], result["cells"][0]["with"]["runs"],
                          result["cells"][0]["without"]["runs"]), ("nested", 10, 10))
        self.assertEqual(len(result["runs_used"]), 20)
        # Old content is a fixture no longer on main: the cell has no run.
        self.assertEqual((mismatched["cells"][0]["class"], mismatched["cells"][0]["runs"]),
                         ("missing", {"with": [], "without": []}))
        # Without the alias the old runs belong to a fixture no longer on main.
        plain = self.verdict(old + new, ["model-a"], self.expect("nested"))
        self.assertEqual(len(plain["cells"][0]["runs"]["with"]), 4)

    def test_the_declared_fixture_aliases_point_at_nested_fixtures_on_main(self):
        # The kit ships no skill fixtures (DECISIONS X9): the aliases are
        # checked against the example checkout's (test/example_evals.py),
        # which carries the one nested fixture an alias names.
        sys.path.insert(0, str(TEST_DIR))
        import example_evals
        evals = example_evals.checkout() / "evals"
        present = {key: new for key, new in gates.FIXTURE_ALIASES.items()
                   if (evals / key[0]).is_dir()}
        self.assertTrue(present)
        for (skill, old), new in present.items():
            with self.subTest(skill=skill):
                self.assertFalse((evals / skill / old / "fixture.yaml").exists())
                self.assertTrue((evals / skill / new / "fixture.yaml").is_file())
                self.assertIn(new, compute_gates.expected_content(evals, skill, {}))

    def test_the_arms_are_paired_on_one_guidance_and_skill_set(self):
        # Round 5's reproducer (test_ctx.py): twenty without runs from old
        # guidance G1, then fifteen of each arm from new guidance G2, which
        # lowers both arms; the skill still adds 0.1. Pooled independently,
        # the without arm took G1's runs (it had the most), and the cell
        # compared G2 with against G1 without: retire. Chosen jointly, only
        # G2 with G2 counts.
        lower = [(50, 50, 50), (50, 50, 49)] * 10
        without_g1 = series("without", FLAT[:20], FLAT_JUDGE, ctx="skills0:G1",
                            pair="others:G1")
        with_g2 = series("with", FLAT[:20], FLAT_JUDGE, ctx="skills1:G2", start=30,
                         pair="others:G2")
        without_g2 = series("without", lower[:15], FLAT_JUDGE, ctx="skills0:G2", start=30,
                            pair="others:G2")
        matched = self.verdict(with_g2 + without_g2, ["model-a"])
        self.assertEqual(matched["verdict"], "keep")
        mixed = self.verdict(without_g1 + with_g2 + without_g2, ["model-a"])
        self.assertEqual(mixed["verdict"], "keep")
        self.assertEqual(mixed["cells"][0]["key"]["without_context"], "skills0:G2")
        self.assertEqual((mixed["cells"][0]["with"]["runs"],
                          mixed["cells"][0]["without"]["runs"]), (20, 15))

    def test_no_shared_pair_or_a_tie_is_insufficient(self):
        unpaired = (series("with", FLAT[:10], FLAT_JUDGE, pair="others:G2")
                    + series("without", FLAT[:10], FLAT_JUDGE, pair="others:G1"))
        result = self.verdict(unpaired, ["model-a"])
        self.assertEqual(result["verdict"], "insufficient")
        self.assertIn("pooling key incomplete", result["cells"][0]["reason"])
        # No pair key at all (a summary from before others_digest).
        result = self.verdict(flat_cell(pair=None), ["model-a"])
        self.assertEqual(result["verdict"], "insufficient")
        # Two pairs with as many runs in their smaller arms: neither.
        tie = (flat_cell(6, pair="others:G1")
               + series("with", FLAT[:6], FLAT_JUDGE, start=40, ctx="w2", pair="others:G2")
               + series("without", FLAT[:6], FLAT_JUDGE, start=40, ctx="o2",
                        pair="others:G2"))
        self.assertEqual(self.verdict(tie, ["model-a"])["cells"][0]["key"]["with_context"],
                         None)
        # The pair with more runs in its SMALLER arm wins, not the larger.
        lopsided = (series("with", FLAT[:20], FLAT_JUDGE, pair="others:G1")
                    + series("without", FLAT[:5], FLAT_JUDGE, pair="others:G1")
                    + series("with", FLAT[:6], FLAT_JUDGE, start=40, ctx="w2",
                             pair="others:G2")
                    + series("without", FLAT[:6], FLAT_JUDGE, start=40, ctx="o2",
                             pair="others:G2"))
        key = self.verdict(lopsided, ["model-a"])["cells"][0]["key"]
        self.assertEqual((key["with_context"], key["without_context"]), ("w2", "o2"))

    def test_one_trial_runs_count_and_retire_only_at_the_same_looks(self):
        # Round 5, N1: the weekly sweep fires one trial per run. Such a run
        # counts, and no-benefit still needs both of the two largest looks.
        one = [(80,), (79,)] * 10
        judges = [7.0, 7.2] * 10

        def runs(n):
            return (series("with", one[:n], judges, spread=False)
                    + series("without", one[:n], judges, spread=False))
        for n, want in ((4, "insufficient"), (9, "insufficient"),
                        (10, "no-benefit-pending"), (19, "no-benefit-pending"),
                        (20, "no-benefit")):
            with self.subTest(n=n):
                result = cell(*(lambda r: (r[:n], r[n:]))(runs(n)), carried=True)
                self.assertEqual(result["class"], want, result["reason"])
                self.assertEqual(result["with"]["runs"], n)
        # Its bounds are the run-level ones: the judge floor is 0 (no run has
        # two trials) and the objective floor is binomial over its trials.
        result = cell(*(lambda r: (r[:20], r[20:]))(runs(20)), carried=True)
        self.assertEqual((result["with"]["judge_floor"], result["without"]["judge_floor"]),
                         (0.0, 0.0))
        self.assertEqual([look["n"] for look in result["looks"]], [5, 10, 20])
        rates = [r / T for r in (80, 79) * 10]
        _, upper = gates.bounds(
            rates, rates, TEST_POLICY["retire_confidence"],
            TEST_POLICY["bootstrap_resamples"],
            floors=tuple(gates.binomial_floor(sum(rates) / 20, 20) for _ in "ab"))
        self.assertAlmostEqual(result["objective"]["retire_upper"], upper)
        # Equal judge figures in every one-trial run of an arm cannot bound
        # a delta, even when the other arm's vary: there is no spread to
        # stand in for the missing within-run floor.
        flat = (series("with", one, [7.0] * 20, spread=False)
                + series("without", one, [7.0] * 20, spread=False))
        self.assertEqual(cell(flat[:20], flat[20:], carried=True)["class"], "insufficient")
        half = (series("with", one, [7.0] * 20, spread=False)
                + series("without", one, judges, spread=False))
        result = cell(half[:20], half[20:], carried=True)
        self.assertEqual(result["class"], "insufficient", result["reason"])
        self.assertIn("run judge figures that are not all equal", result["reason"])

    def test_the_arm_context_is_the_newest_among_runs_of_the_content_on_main(self):
        # The newest with run is of other content and another context; the
        # context comes from the newest run of the content on main.
        runs = flat_cell() + series("with", FLAT[:1], FLAT_JUDGE, start=19,
                                    tested="other", ctx="other-ctx")
        result = self.verdict(runs, ["model-a"])
        self.assertEqual(result["cells"][0]["key"]["with_context"], "with-ctx")
        self.assertEqual(result["verdict"], "retire")

    def test_unknown_content_on_main_is_insufficient(self):
        for field in ("tested_digest", "fixture_digest"):
            with self.subTest(field=field):
                expected = {"": {**EXPECT[""], field: None}}
                result = self.verdict(flat_cell(), ["model-a"], expected)
                self.assertEqual(result["verdict"], "insufficient")
                self.assertIn("pooling key incomplete", result["cells"][0]["reason"])
                self.assertIn(field, result["cells"][0]["reason"])

    def test_cells_record_runs_reports_and_cost(self):
        result = self.verdict(self.flat("model-a"), ["model-a"])
        c = result["cells"][0]
        self.assertEqual(len(c["runs"]["with"]), 10)
        entry = c["runs"]["with"][0]
        self.assertEqual(entry["run"], {"id": stamp(0)})
        self.assertEqual(entry["report"]["url"],
                         "https://github.com/example-adopter/skills-evals/blob/"
                         "persistent/eval-results/" + entry["report"]["path"])
        self.assertAlmostEqual(c["cost_usd"], 20 * 3 * 0.25, places=12)
        self.assertEqual(len(result["runs_used"]), 20)
        self.assertEqual(result["runs_used"][0].split("#")[1:], ["", "with"])

    def test_the_run_key_names_the_fixture(self):
        one, two = (series("with", FLAT[:1], fixture=f)[0] for f in ("one", "two"))
        self.assertEqual(one["path"], two["path"])
        self.assertNotEqual(gates.run_key(one), gates.run_key(two))
        self.assertEqual(gates.run_key(one), f"{one['path']}#one#with")


class RunEvalRecordTests(unittest.TestCase):
    """What harness/run_eval.py records for the verdict to key on."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def repo(self):
        """A Git checkout holding one fixture, everything staged."""
        repo = self.tmp / "repo"
        fixture = repo / "evals" / "demo"
        (fixture / "seed").mkdir(parents=True)
        (fixture / "references").mkdir()
        (fixture / "fixture.yaml").write_text("skill: demo\n", encoding="utf-8")
        (fixture / "seed" / "a.txt").write_text("a", encoding="utf-8")
        (fixture / "references" / "r.md").write_text("r", encoding="utf-8")
        (repo / "shared.txt").write_text("s", encoding="utf-8")
        (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        self.git("init", "-q", cwd=repo)
        self.git("add", "-A", cwd=repo)
        return repo, fixture

    def test_the_requested_model_is_recorded_apart_from_models_used(self):
        run_eval._write_summary(self.tmp, "demo", "with_skill", "20261001T000000Z",
                                None, None, [], None, None,
                                models=["model-sub", "model-x"], agent_model="model-x",
                                agent_usage={"tokens": {"model-sub": {}, "model-x": {}},
                                             "dropped": 0, "complete": True})
        doc = json.loads((self.tmp / "demo" / "20261001T000000Z" / "with_skill"
                          / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["cross_model"]["model"], "model-x")
        self.assertEqual(run_eval._arm_model([doc]), "model-x")

    def test_fixture_digest_covers_tracked_fixture_files_only(self):
        repo, fixture = self.repo()
        first = run_eval.fixture_digest(fixture)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        # An ignored node_modules does not move it.
        (fixture / "seed" / "node_modules" / "pkg").mkdir(parents=True)
        (fixture / "seed" / "node_modules" / "pkg" / "index.js").write_text("x",
                                                                           encoding="utf-8")
        self.assertEqual(first, run_eval.fixture_digest(fixture))
        # The same tracked content elsewhere gives the same digest.
        copy = self.tmp / "copy"
        shutil.copytree(repo, copy, symlinks=True)
        self.assertEqual(first, run_eval.fixture_digest(copy / "evals" / "demo"))
        for rel in ("fixture.yaml", "seed/a.txt", "references/r.md"):
            with self.subTest(rel=rel):
                path = fixture / rel
                original = path.read_text(encoding="utf-8")
                path.write_text(original + "!", encoding="utf-8")
                self.git("add", "--", rel, cwd=fixture)
                self.assertNotEqual(first, run_eval.fixture_digest(fixture))
                path.write_text(original, encoding="utf-8")
                self.git("add", "--", rel, cwd=fixture)
        self.assertEqual(first, run_eval.fixture_digest(fixture))
        # A file outside the fixture directory does not count.
        (repo / "evals" / "other.txt").write_text("o", encoding="utf-8")
        self.git("add", "--", "evals/other.txt", cwd=repo)
        self.assertEqual(first, run_eval.fixture_digest(fixture))

    def test_no_digest_outside_git_or_for_a_dirty_fixture(self):
        repo, fixture = self.repo()
        outside = self.tmp / "plain"
        shutil.copytree(fixture, outside)
        self.assertIsNone(run_eval.fixture_digest(outside))
        # A tracked file changed but not staged.
        (fixture / "seed" / "a.txt").write_text("changed", encoding="utf-8")
        self.assertIsNone(run_eval.fixture_digest(fixture))
        self.git("checkout", "--", "evals/demo/seed/a.txt", cwd=repo)
        self.assertIsNotNone(run_eval.fixture_digest(fixture))
        # An untracked file that is not ignored.
        (fixture / "seed" / "scratch.txt").write_text("x", encoding="utf-8")
        self.assertIsNone(run_eval.fixture_digest(fixture))

    def test_a_symlinks_digest_folds_in_its_tracked_target(self):
        repo, fixture = self.repo()
        os.symlink("../../../shared.txt", fixture / "seed" / "link")
        self.git("add", "-A", cwd=repo)
        first = run_eval.fixture_digest(fixture)
        (repo / "shared.txt").write_text("changed", encoding="utf-8")
        self.git("add", "-A", cwd=repo)
        self.assertNotEqual(first, run_eval.fixture_digest(fixture))

    def test_a_path_that_is_not_utf_8_is_hashed_not_decoded_by_locale(self):
        repo, fixture = self.repo()
        name = os.fsencode(str(fixture / "seed")) + b"/caf\xe9.txt"
        with open(name, "wb") as stream:
            stream.write(b"x")
        self.git("-c", "core.quotepath=false", "add", "-A", cwd=repo)
        first = run_eval.fixture_digest(fixture)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, run_eval.fixture_digest(fixture))

    def test_a_checkouts_clean_filter_never_runs(self):
        # Round 3's reproducer: `ls-files -m` ran `filter.evil.clean`, named
        # by the fixture's own .gitattributes.
        repo, fixture = self.repo()
        marker = self.tmp / "PWNED"
        (fixture / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
        self.git("add", "-A", cwd=repo)
        self.git("config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'", cwd=repo)
        marker.unlink(missing_ok=True)
        attributes = self.tmp / "attributes"
        attributes.write_text("* filter=evil\n", encoding="utf-8")
        self.git("config", "core.attributesFile", str(attributes), cwd=repo)
        self.assertIsNotNone(run_eval.fixture_digest(fixture))
        (fixture / "seed" / "a.txt").write_text("changed", encoding="utf-8")
        self.assertIsNone(run_eval.fixture_digest(fixture))
        self.assertFalse(marker.exists())

    def test_a_symlink_to_a_directory_has_no_digest(self):
        repo, fixture = self.repo()
        (repo / "shared").mkdir()
        (repo / "shared" / "f.txt").write_text("f", encoding="utf-8")
        os.symlink("../../../shared", fixture / "seed" / "dir")
        self.git("add", "-A", cwd=repo)
        self.assertIsNone(run_eval.fixture_digest(fixture))
        # Checked out as text, the target is still a tracked directory.
        (fixture / "seed" / "dir").unlink()
        (fixture / "seed" / "dir").write_text("../../../shared", encoding="utf-8")
        self.assertIsNone(run_eval.fixture_digest(fixture))

    def test_a_symlink_to_anything_git_does_not_pin_has_no_digest(self):
        # Round 4, N4: the target must be a regular file tracked in the
        # checkout, or a change to it is no change to the digest.
        repo, fixture = self.repo()
        (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (repo / "ignored.txt").write_text("i", encoding="utf-8")
        (repo / "loose.txt").write_text("l", encoding="utf-8")
        (self.tmp / "outside.txt").write_text("o", encoding="utf-8")
        os.symlink("shared.txt", repo / "alias.txt")
        self.git("add", ".gitignore", "alias.txt", cwd=repo)
        link = fixture / "seed" / "link"
        for target in ("../../../alias.txt", "../../../ignored.txt", "../../../loose.txt",
                       os.path.relpath(self.tmp / "outside.txt", link.parent),
                       str(self.tmp / "outside.txt"), "../../../missing.txt"):
            with self.subTest(target=target):
                link.unlink(missing_ok=True)
                os.symlink(target, link)
                self.git("add", "-f", str(link.relative_to(repo)), cwd=repo)
                self.assertIsNone(run_eval.fixture_digest(fixture))
        link.unlink()
        os.symlink("../../../shared.txt", link)
        self.git("add", str(link.relative_to(repo)), cwd=repo)
        self.assertIsNotNone(run_eval.fixture_digest(fixture))

    def test_a_working_file_that_is_not_its_staged_blob_has_no_digest(self):
        repo, fixture = self.repo()
        self.assertIsNotNone(run_eval.fixture_digest(fixture))
        # Same size, other bytes: only a content hash tells them apart.
        (fixture / "seed" / "a.txt").write_text("b", encoding="utf-8")
        self.assertIsNone(run_eval.fixture_digest(fixture))
        (fixture / "seed" / "a.txt").write_text("a", encoding="utf-8")
        self.assertIsNotNone(run_eval.fixture_digest(fixture))
        (fixture / "seed" / "a.txt").chmod(0o755)
        self.assertIsNone(run_eval.fixture_digest(fixture))
        (fixture / "seed" / "a.txt").chmod(0o644)
        (fixture / "seed" / "a.txt").unlink()
        self.assertIsNone(run_eval.fixture_digest(fixture))


    def _run_trials(self, fixture, hold=None):
        args = argparse.Namespace(results_dir=self.tmp, trials=2)
        item = {"fixture": fixture, "seed": self.tmp, "name": None}
        arm_dir = self.tmp / "demo" / "20261001T000000Z" / "with_skill"
        for k in (1, 2):
            run_eval._run_arm("with_skill", fixture, self.tmp, {}, args,
                              "20261001T000000Z", ("model-x", None, "no judge"),
                              out_dir=arm_dir / f"trial-{k}", extra={"trial": k},
                              hold=hold)
        run_eval._aggregate_arm("with_skill", item, args, "20261001T000000Z")
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in (arm_dir / "trial-1" / "summary.json", arm_dir / "summary.json")]

    def test_each_arm_summary_carries_the_fixture_digest(self):
        digest = "ab" * 32
        for doc in self._run_trials({"skill": "demo", "prompt": "p",
                                     "_fixture_digest": digest}):
            self.assertEqual(doc["fixture_digest"], digest)

    def test_no_fixture_digest_means_no_field(self):
        for doc in self._run_trials({"skill": "demo", "prompt": "p"}):
            self.assertNotIn("fixture_digest", doc)

    def test_each_arm_summary_says_whether_its_judge_was_conditional(self):
        fixture = {"skill": "demo", "prompt": "p"}
        for doc in self._run_trials(fixture):
            self.assertIs(doc["judge_conditional"], False)
        shutil.rmtree(self.tmp / "demo")
        for doc in self._run_trials(fixture, hold=[]):
            self.assertIs(doc["judge_conditional"], True)

    def test_the_ingester_checks_the_new_fields(self):
        tokens = {"model-x": {"input_tokens": 1, "output_tokens": 1,
                              "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 0,
                              "canonical_model": "model-x"}}

        def summary(**extra):
            out = self.tmp / "s" / str(len(list(self.tmp.glob("s/*"))))
            run_eval._write_summary(out, "demo", "with_skill", "20261001T000000Z",
                                    None, None, [], None, None, models=["model-x"],
                                    agent_model="model-x", extra={"n": 1, **extra},
                                    agent_usage={"tokens": tokens, "dropped": 0,
                                                 "complete": True})
            return json.loads((out / "demo" / "20261001T000000Z" / "with_skill"
                               / "summary.json").read_text(encoding="utf-8"))
        parts = ingest.parse_result_path("demo/20261001T000000Z/with_skill/summary.json")
        ingest.check_summary(summary(fixture_digest="ab" * 32, judge_conditional=False),
                             "ok", parts)
        for name, extra in (("short digest", {"fixture_digest": "ab"}),
                            ("upper-case digest", {"fixture_digest": "AB" * 32}),
                            ("digest not a string", {"fixture_digest": 7}),
                            ("conditional not a bool", {"judge_conditional": 0})):
            with self.subTest(name=name), self.assertRaises(ingest.Rejected):
                ingest.check_summary(summary(**extra), name, parts)


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def write_arm_runs(tree: Path, skill, model, patterns, judges, *, tested, fdig,
                   start=0, model_tag="a", requested=True, without_judges=None,
                   fixture=None, checks=True):
    """Both arms of each run, laid out as routine-results; the without arm
    repeats `judges` unless `without_judges` gives its own. Without `checks`
    the trials have no objective checks (a judge-only fixture). Returns the
    run ids written."""
    run_ids = []
    for i, pattern in enumerate(patterns):
        r = start + i
        run_ids.append(f"{stamp(r)}-{model_tag}0000{r % 10}")
        rel = f"routine-results/{run_ids[-1]}/{skill}/{stamp(r)}"
        base = tree / rel
        base.mkdir(parents=True, exist_ok=True)
        (base / "report.md").write_text("# report\n", encoding="utf-8")
        for role in ("with", "without"):
            arm = base / (fixture or "") / f"{role}_skill"
            write_json(arm / "summary.json", {
                "skill": skill, "arm": f"{role}_skill", "role": role,
                "aggregate": {}, "harness": {"version": CLIS[r % 2]},
                "cross_model": {"model": model if requested else None},
                "context_subject": {"tested_digest": tested},
                "arm_context": {"skills_digest": role, "guidance_digest": "g",
                                "others_digest": "o"},
                "context": {"guidance": {"digest": "g"}},
                "fixture_digest": fdig, "judge_conditional": False,
                "n": len(pattern), "errors": 0,
                "run": {"id": stamp(r), "billing": "api"}})
            marks = figures((judges if role == "with" or without_judges is None
                             else without_judges)[i])
            for k, passed in enumerate(pattern):
                # As in real trials, no two arms' trial bytes are the same
                # (the arms differ in cost): identical bytes are a copy, and
                # a copy counts once.
                doc = trial(passed, T, 3 * r + k, marks[k],
                            cost=0.25 if role == "with" else 0.5)
                if not checks:
                    doc["objective_checks"] = []
                write_json(arm / f"trial-{k + 1}" / "summary.json",
                           {**doc, "skill": skill, "timestamp": stamp(r),
                            # A trial records its own requested model, as
                            # run_eval's `_write_summary` does.
                            **({"cross_model": {"model": model}} if requested else {})})
    return run_ids


class ComputeGatesTests(unittest.TestCase):
    """The committed policy, on judge-carried fixtures (no objective checks
    in fixture.yaml): the cells are decided on the judge."""
    SKILLS = ("flat", "gain", "unasked", "one", "two", "guidancebot")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tree = self.tmp / "tree"
        self.roster = self.tmp / "roster.yml"
        self.roster.write_text("arms:\n  - id: model-a\n", encoding="utf-8")
        # The fixtures on main, tracked in Git, and a registry checkout.
        self.evals = self.tmp / "repo" / "evals"
        self.registry = self.tmp / "registry"
        for skill in self.SKILLS:
            self._fixture(self.evals / skill, skill)
            self._skill(skill, "body")
        for name in ("a", "b", "c", "d"):
            self._fixture(self.evals / "adrs" / name, "adrs")
        self._skill("adrs", "body")
        subprocess.run(["git", "init", "-q"], cwd=self.evals.parent, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.evals.parent, check=True)
        # An older-shape summary with no aggregate is not a validated run.
        write_json(self.tree / "results" / "flat" / "20260801T000000Z" / "with_skill"
                   / "summary.json", {"skill": "flat", "arm": "with_skill"})

    @staticmethod
    def _fixture(directory: Path, skill: str) -> None:
        directory.mkdir(parents=True)
        (directory / "fixture.yaml").write_text(f"skill: {skill}\nprompt: p\n",
                                                encoding="utf-8")

    def _skill(self, skill: str, body: str) -> None:
        path = self.registry / "plugins" / "bundle" / "skills" / skill / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {skill}\ndescription: d\n---\n{body}\n",
                        encoding="utf-8")

    def digest_of(self, skill):
        path = self.registry / "plugins" / "bundle" / "skills" / skill
        return context._file_digest(delivery._tree_files(path)[0])

    def write(self, skill, patterns, judges, *, fixture=None, tested=None,
              model="model-a", **kw):
        directory = self.evals / skill / (fixture or "")
        kw.setdefault("checks", False)
        run_ids = write_arm_runs(self.tree, skill, model, patterns, judges, fixture=fixture,
                                 tested=tested or self.digest_of(skill),
                                 fdig=run_eval.fixture_digest(directory), **kw)
        return run_ids


    def run_gates(self, name, registry=True, as_of=AS_OF):
        out = self.tmp / name
        args = ["--results-tree", str(self.tree), "--roster", str(self.roster),
                "--policy", str(POLICY_FILE), "--evals-dir", str(self.evals),
                "--as-of", as_of, "--out", str(out)]
        if registry:
            args += ["--registry", f"agentskills={self.registry}"]
        self.assertEqual(compute_gates.main(args), 0)

        def refuse(value):
            raise ValueError(value)
        return out, json.loads(out.read_text(encoding="utf-8"), parse_constant=refuse)

    def edit(self, path, change):
        """Rewrite the JSON at `path`."""
        doc = json.loads(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")

    def test_output_is_deterministic(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        first, _ = self.run_gates("a.json")
        second, _ = self.run_gates("b.json")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_verdicts_links_and_skips(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        # One fixture per routine run id, as the fire workflow makes them.
        self.write("gain", FLAT[:5], [8.5, 8.7] * 3, without_judges=[6.0, 6.2] * 3,
                   model_tag="b")
        self.write("unasked", FLAT[:10], FLAT_JUDGE, requested=False, model_tag="c")
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")
        self.assertEqual(doc["skills"]["gain"]["verdict"], "keep")
        self.assertEqual(doc["skills"]["unasked"]["verdict"], "insufficient")
        self.assertEqual(doc["skipped"], {"no aggregate block": 1,
                                          "no requested arm model": 20})
        self.assertEqual((doc["roster_models"], doc["as_of"]), (["model-a"], AS_OF))
        entry = doc["skills"]["flat"]["cells"][0]["runs"]["with"][0]
        self.assertEqual(entry["run"]["billing"], "api")
        self.assertTrue((self.tree / entry["report"]["path"]).is_file())
        self.assertEqual(doc["retirement_mode"], "auto")
        self.assertNotIn("previous", doc)

    def in_place_fixture(self, skill):
        """A fixture on main that runs in place (a top-level `context`
        mapping, ADR 0012), tracked in Git, with its registry copy."""
        directory = self.evals / skill
        directory.mkdir(parents=True)
        (directory / "fixture.yaml").write_text(
            f"skill: {skill}\nprompt: p\ncontext:\n  repository: example/repo\n",
            encoding="utf-8")
        self._skill(skill, "body")
        subprocess.run(["git", "add", "-A"], cwd=self.evals.parent, check=True)

    def bare(self, skill, r, *, model_tag="z", pairing=None):
        """A one-arm run: only `without_skill`. With no `pairing` it is an
        isolation arm, with no context blocks, as a bare fire (`arms: bare`,
        ADR 0012's 2026-10-27 addendum) leaves it."""
        (run_id,) = self.write(skill, FLAT[:1], FLAT_JUDGE, start=r, model_tag=model_tag)
        base = self.tree / "routine-results" / run_id / skill / stamp(r)
        shutil.rmtree(base / "with_skill")

        def change(doc):
            if pairing is None:
                for key in ("arm_context", "context", "context_subject"):
                    doc.pop(key)
            else:
                doc["pairing"] = pairing
        self.edit(base / "without_skill" / "summary.json", change)
        return run_id, base

    def test_a_bare_run_is_reported_apart_and_never_pooled(self):
        self.in_place_fixture("ctxbot")
        run_id, base = self.bare("ctxbot", 40)
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["bare_runs"], [{
            "skill": "ctxbot", "fixture": None, "model": "model-a", "timestamp": stamp(40),
            "path": f"routine-results/{run_id}/ctxbot/{stamp(40)}",
            "report": gates._link(f"routine-results/{run_id}/ctxbot/{stamp(40)}/report.md")}])
        cell = doc["skills"]["ctxbot"]["cells"][0]
        # Pooled, it would be the cell's `without` arm and put the cell on
        # its model; set apart, the cell still has no run at all.
        self.assertEqual((cell["class"], cell["runs"]), ("missing", {"with": [], "without": []}))
        self.assertEqual(doc["skills"]["ctxbot"]["runs_used"], [])
        self.assertIsNone(doc["newest_run"])
        # Its in-place pairs are decided as before, the bare run beside them.
        self.write("ctxbot", FLAT[:10], FLAT_JUDGE)
        _, with_bare = self.run_gates("b.json")
        shutil.rmtree(base.parent.parent)
        _, without_bare = self.run_gates("c.json")
        self.assertEqual(with_bare["skills"], without_bare["skills"])
        self.assertEqual(len(with_bare["bare_runs"]), 1)
        self.assertEqual(without_bare["bare_runs"], [])

    def test_only_a_lone_isolation_without_arm_on_an_in_place_fixture_is_bare(self):
        self.in_place_fixture("ctxbot")
        # An in-place summary is in-place evidence, alone or not.
        self.bare("ctxbot", 41, model_tag="y", pairing="in_place")
        # A fixture with no `context` mapping: its isolation `without` arm
        # is an ordinary run, as before.
        self.bare("one", 42, model_tag="x")
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["bare_runs"], [])
        for skill in ("ctxbot", "one"):
            with self.subTest(skill=skill):
                cell = doc["skills"][skill]["cells"][0]
                self.assertEqual(cell["class"], "insufficient")
        self.assertEqual(len(doc["skills"]["one"]["cells"][0]["runs"]["without"]), 1)
        # A `with` arm beside it makes it a pair, even an unreadable one.
        run_id, base = self.bare("ctxbot", 43, model_tag="w")
        write_json(base / "with_skill" / "summary.json", {"skill": "ctxbot"})
        index = compute_gates.build_index(self.tree, self.evals, AS_OF)
        self.assertEqual(index["bare_runs"], [])
        self.assertIn(f"routine-results/{run_id}/ctxbot/{stamp(43)}",
                      {r["path"] for r in index["runs"]})
        # After as-of, a bare run is not read either.
        self.bare("ctxbot", 44, model_tag="v")
        index = compute_gates.build_index(self.tree, self.evals, stamp(43))
        self.assertEqual((index["bare_runs"], index["skipped"].get("after as-of")), ([], 1))
        self.assertEqual(len(compute_gates.build_index(self.tree, self.evals,
                                                       AS_OF)["bare_runs"]), 1)

    def test_five_runs_are_pending_and_ten_retire(self):
        self.write("flat", FLAT[:5], FLAT_JUDGE)
        _, doc = self.run_gates("1.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire-pending")
        self.write("flat", FLAT[5:10], FLAT_JUDGE[5:10], start=5)
        _, doc = self.run_gates("2.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")

    def test_as_of_is_required(self):
        with self.assertRaises(SystemExit):
            compute_gates.main(["--results-tree", str(self.tree), "--roster",
                                str(self.roster), "--policy", str(POLICY_FILE),
                                "--out", str(self.tmp / "x.json")])
        for bad in ("yesterday", "2026-10-01"):
            with self.subTest(bad=bad):
                self.assertEqual(compute_gates.main([
                    "--results-tree", str(self.tree), "--roster", str(self.roster),
                    "--policy", str(POLICY_FILE), "--as-of", bad,
                    "--out", str(self.tmp / "x.json")]), 2)


    def test_a_run_stamped_after_as_of_is_not_read(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        later = "20261002T073000Z"
        run = self.tree / "routine-results" / f"{stamp(30)}-f00000" / "flat" / later
        shutil.copytree(self.tree / "routine-results" / f"{stamp(0)}-a00000" / "flat"
                        / stamp(0), run)
        for role in ("with", "without"):
            self.edit(run / f"{role}_skill" / "summary.json",
                      lambda d: d["arm_context"].update(skills_digest="later"))
            for k in (1, 2, 3):
                self.edit(run / f"{role}_skill" / f"trial-{k}" / "summary.json",
                          lambda d, c={"with": 0.75, "without": 0.85}[role]:
                          d["agent"].update(cost_usd=c))
        _, doc = self.run_gates("a.json", as_of="20261002T062000Z")
        # Not a duplicate (its bytes are its own), so only as-of keeps it out.
        self.assertEqual((doc["skipped"].get("after as-of"),
                          doc["skipped"].get("duplicate arm run")), (2, None))
        self.assertEqual(len(doc["skills"]["flat"]["runs_used"]), 20)
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")
        # Read, it is one run against ten: the context with the most runs
        # is chosen, so the verdict stands.
        _, doc = self.run_gates("b.json", as_of=later)
        self.assertNotIn("after as-of", doc["skipped"])
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")

    def copy_run(self, source_id, run_id, timestamp=None, *, reserialize=False):
        """`routine-results/<source_id>` again as `<run_id>`, its one
        skill's timestamp directory renamed to `timestamp` when given."""
        source = self.tree / "routine-results" / source_id
        target = self.tree / "routine-results" / run_id
        shutil.copytree(source, target)
        if timestamp is not None:
            (stamp_dir,) = (target / "flat").iterdir()
            stamp_dir.rename(stamp_dir.with_name(timestamp))
        if reserialize:
            for path in target.rglob("summary.json"):
                path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")),
                                           indent=1), encoding="utf-8")
        return target


    def test_a_copy_is_counted_once(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        _, before = self.run_gates("a.json")
        for run in sorted((self.tree / "routine-results").iterdir()):
            copy = run.name.replace("-a0000", "-e0000")
            self.copy_run(run.name, copy, reserialize=True)
        _, after = self.run_gates("b.json")
        self.assertEqual(after["skills"]["flat"]["runs_used"],
                         before["skills"]["flat"]["runs_used"])
        self.assertEqual(after["skipped"]["duplicate arm run"], 20)

    def test_each_duplicate_key_alone_drops_a_copy(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        source_id = f"{stamp(0)}-a00000"

        def summaries(run):
            return [run / f"{role}_skill" / "summary.json" for role in ("with", "without")]

        def trials(run):
            return [run / f"{role}_skill" / f"trial-{k}" / "summary.json"
                    for role in ("with", "without") for k in (1, 2, 3)]

        for name, timestamp, edited in (
                ("same skill, fixture, model, role and timestamp", stamp(0),
                 lambda run: summaries(run) + trials(run)),
                ("same arm summary bytes", stamp(40), trials),
                ("same trial summary bytes", stamp(41), summaries)):
            with self.subTest(name=name):
                run_id = f"{timestamp}-e00000"
                copy = self.copy_run(source_id, run_id, timestamp)
                for path in edited(copy / "flat" / timestamp):
                    self.edit(path, lambda d: d.update(copied=True))
                _, doc = self.run_gates("a.json")
                self.assertEqual(doc["skipped"]["duplicate arm run"], 2)
                self.assertEqual(len(doc["skills"]["flat"]["runs_used"]), 20)
                shutil.rmtree(copy)

    def test_a_skill_whose_name_starts_with_guidance_is_evaluated(self):
        self.write("guidancebot", FLAT[:10], FLAT_JUDGE)
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["guidancebot"]["raw_verdict"], "retire")

    def test_without_a_registry_every_cell_is_insufficient(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        _, doc = self.run_gates("a.json", registry=False)
        verdict = doc["skills"]["flat"]
        self.assertEqual(verdict["verdict"], "insufficient")
        self.assertIn("tested_digest", verdict["cells"][0]["reason"])

    def test_runs_of_other_skill_content_do_not_count(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        self._skill("flat", "changed on main")
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "insufficient")
        self.assertEqual(doc["skills"]["flat"]["cells"][0]["runs"]["with"], [])

    def test_cells_come_from_the_fixtures_on_main(self):
        self.write("adrs", FLAT[:10], FLAT_JUDGE, fixture="a")
        _, doc = self.run_gates("a.json")
        verdict = doc["skills"]["adrs"]
        self.assertEqual(verdict["verdict"], "insufficient")
        self.assertEqual([(c["fixture"], c["class"]) for c in verdict["cells"]],
                         [("a", "no-benefit"), ("b", "missing"), ("c", "missing"),
                          ("d", "missing")])

    def test_crafted_summaries_are_skipped_never_a_crash(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        runs = sorted((self.tree / "routine-results").iterdir())
        trial_path = runs[0] / "flat" / stamp(0) / "with_skill" / "trial-1" / "summary.json"
        self.edit(trial_path, lambda d: d.update(objective_checks=5))
        nan_path = runs[1] / "flat" / stamp(1) / "with_skill" / "trial-1" / "summary.json"
        nan_path.write_text(nan_path.read_text(encoding="utf-8").replace(
            '"cost_usd": 0.25', '"cost_usd": NaN'), encoding="utf-8")
        for k in (1, 2, 3):
            self.edit(runs[2] / "flat" / stamp(2) / "without_skill" / f"trial-{k}"
                      / "summary.json", lambda d: d["agent"].update(cost_usd=1e308))
        self.edit(runs[3] / "flat" / stamp(3) / "with_skill" / "summary.json",
                  lambda d: d.update(fixture_digest=["not", "a", "digest"]))
        self.edit(runs[4] / "flat" / stamp(4) / "with_skill" / "summary.json",
                  lambda d: d.update(role=["with"]))
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skipped"], {"malformed trial summary": 1,
                                          "unreadable trial summary": 1,
                                          "malformed arm summary": 2,
                                          "no aggregate block": 1})
        self.assertIsNone(doc["skills"]["flat"]["cells"][0]["cost_usd"])
        # The arms are independent samples: the four refused with arms leave
        # their without arms counted, and six with runs reach one look.
        self.assertEqual(len(doc["skills"]["flat"]["runs_used"]), 16)
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire-pending")

    def test_the_verdict_reads_the_roster_it_is_given(self):
        # The roster follows current use (2026-10-25): a superseded model
        # leaves it at once. Its runs, a clear gain here, then count toward
        # nothing, and no evidence is asked of it.
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        self.write("flat", FLAT[:5], [8.5, 8.7] * 3, without_judges=[6.0, 6.2] * 3,
                   model="model-old", model_tag="c", start=50)
        for arms, verdict in ((["model-a"], "retire"),
                              (["model-old", "model-a"], "keep"),
                              (["model-a", "model-new"], "insufficient")):
            with self.subTest(arms=arms):
                self.roster.write_text("arms:\n" + "".join(f"  - id: {m}\n" for m in arms),
                                       encoding="utf-8")
                _, doc = self.run_gates("a.json")
                flat = doc["skills"]["flat"]
                self.assertEqual((doc["roster_models"], flat["verdict"]),
                                 (sorted(arms), verdict))
                self.assertEqual(sorted(c["model"] for c in flat["cells"]), sorted(arms))
                if arms == ["model-a"]:
                    self.assertFalse([k for k in flat["runs_used"] if "-c0000" in k])

    def test_runs_of_two_models_at_one_timestamp_both_count(self):
        # One routine run may stamp every model's arms with one timestamp
        # (`--timestamp`): the same (skill, fixture, role, timestamp) on
        # another model is another run, not a copy.
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        self.write("flat", FLAT[:10], FLAT_JUDGE, model="model-b", model_tag="b")
        self.roster.write_text("arms:\n  - id: model-a\n  - id: model-b\n",
                               encoding="utf-8")
        _, doc = self.run_gates("a.json")
        self.assertNotIn("duplicate arm run", doc["skipped"])
        self.assertEqual(len(doc["skills"]["flat"]["runs_used"]), 40)
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")

    # -- round 3: an empty roster ---------------------------------------------

    def test_an_empty_or_malformed_roster_exits_2(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        for text in ("arms: []\n", "judge:\n  id: model-j\n",
                     "arms:\n  - model-a\n", "arms:\n  - id: model-a\n  - model-b\n"):
            with self.subTest(roster=text):
                self.roster.write_text(text, encoding="utf-8")
                out = self.tmp / "x.json"
                self.assertEqual(compute_gates.main([
                    "--results-tree", str(self.tree), "--roster", str(self.roster),
                    "--policy", str(POLICY_FILE), "--evals-dir", str(self.evals),
                    "--as-of", AS_OF, "--out", str(out)]), 2)
                self.assertFalse(out.exists())


    def test_trial_files_must_be_one_to_n_and_agree_with_the_arm_summary(self):
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        runs = sorted((self.tree / "routine-results").iterdir())
        arm = runs[0] / "flat" / stamp(0) / "with_skill"
        (arm / "trial-3").rename(arm / "trial-4")
        self.edit(runs[1] / "flat" / stamp(1) / "with_skill" / "summary.json",
                  lambda d: d.update(n=2))
        self.edit(runs[2] / "flat" / stamp(2) / "with_skill" / "summary.json",
                  lambda d: d.update(errors=1))
        self.edit(runs[3] / "flat" / stamp(3) / "with_skill" / "summary.json",
                  lambda d: d.update(scored=1))
        self.edit(runs[4] / "flat" / stamp(4) / "with_skill" / "summary.json",
                  lambda d: d.pop("n"))
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skipped"]["trial files are not numbered 1 to n"], 1)
        self.assertEqual(doc["skipped"]["arm summary's n or errors disagree with its "
                                        "trial files"], 4)


    def set_context(self, run_ids, context, **arm_context):
        for run_id in run_ids:
            for path in (self.tree / "routine-results" / run_id).rglob("*_skill/summary.json"):
                self.edit(path, lambda d: d["arm_context"].update(
                    skills_digest=context + d["role"], **arm_context))

    def test_no_single_run_chooses_the_context(self):
        # Round 4's reproducer (test_p1): fifteen runs that show a gain, then
        # ten later flat ones with another arm context. The newest run's
        # context once chose the pool, leaving only the later runs.
        self.write("gain", FLAT[:15], [8.5, 8.7] * 8, without_judges=[6.0, 6.2] * 8,
                   model_tag="b")
        _, real = self.run_gates("a.json")
        self.assertEqual(real["skills"]["gain"]["verdict"], "keep")
        later = self.write("gain", FLAT[:10], FLAT_JUDGE, start=40, model_tag="f")
        self.set_context(later, "later-")
        _, doc = self.run_gates("b.json")
        self.assertEqual(doc["skills"]["gain"]["verdict"], "keep")
        self.assertEqual(doc["skills"]["gain"]["cells"][0]["key"],
                         real["skills"]["gain"]["cells"][0]["key"])
        self.assertEqual(doc["skills"]["gain"]["runs_used"],
                         real["skills"]["gain"]["runs_used"])

    def test_a_context_tie_is_insufficient(self):
        self.write("flat", FLAT[:6], FLAT_JUDGE)
        other = self.write("flat", FLAT[:6], FLAT_JUDGE, start=40, model_tag="b")
        self.set_context(other, "other-")
        _, doc = self.run_gates("a.json")
        cell = doc["skills"]["flat"]["cells"][0]
        self.assertEqual((cell["key"]["with_context"], cell["key"]["without_context"]),
                         (None, None))
        self.assertEqual(doc["skills"]["flat"]["verdict"], "insufficient")

    def test_off_roster_runs_do_not_choose_the_context(self):
        self.write("flat", FLAT[:6], FLAT_JUDGE)
        _, alone = self.run_gates("a.json")
        off = self.write("flat", FLAT[:10], FLAT_JUDGE, start=40, model_tag="b",
                         model="model-z")
        self.set_context(off, "off-")
        _, doc = self.run_gates("b.json")
        self.assertEqual(doc["skills"]["flat"]["cells"][0]["key"],
                         alone["skills"]["flat"]["cells"][0]["key"])
        self.assertEqual(doc["skills"]["flat"]["runs_used"],
                         alone["skills"]["flat"]["runs_used"])


    def test_judge_figures_whose_variance_underflows_do_not_crash(self):
        # Round 4's reproducer (test_p5): judge figures around 1e-100 square
        # to 0 in the Welch-Satterthwaite denominator: ZeroDivisionError.
        run_ids = self.write("flat", FLAT[:10], FLAT_JUDGE)
        for run_id in run_ids:
            for path in (self.tree / "routine-results" / run_id).rglob("trial-*/summary.json"):
                k = int(path.parent.name.split("-")[1])
                bump = 1e-100 if "with_skill" in path.parts and run_id[-1] in "13579" else 0
                self.edit(path, lambda d, v=(k - 1) * 1e-100 + bump:
                          d["judge"].update(overall=v))
        _, doc = self.run_gates("a.json")
        # Decided, not caught: a delta of 5e-101 is under every margin.
        self.assertEqual((doc["skills"]["flat"]["verdict"], doc["skills"]["flat"]["reason"]),
                         ("retire", "every cell shows no benefit"))

    def test_one_skills_failure_is_its_own(self):
        from unittest import mock
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        self.write("gain", FLAT[:5], [8.5, 8.7] * 3, without_judges=[6.0, 6.2] * 3,
                   model_tag="b")
        real = gates.retirement

        def failing(index, models, skill, policy, expected):
            if skill == "gain":
                raise ZeroDivisionError("a message that is not reported")
            return real(index, models, skill, policy, expected)
        with mock.patch.object(gates, "retirement", failing):
            _, doc = self.run_gates("a.json")
        gain = doc["skills"]["gain"]
        self.assertEqual((gain["verdict"], gain["cells"], gain["runs_used"], gain["reason"]),
                         ("insufficient", [], [], "the verdict raised ZeroDivisionError"))
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")


    def test_a_results_copy_of_a_routine_run_counts_once(self):
        # The same run ingested under both trees is one run of its cell.
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        run_id = f"{stamp(0)}-a00000"
        source = self.tree / "routine-results" / run_id / "flat" / stamp(0)
        shutil.copytree(source, self.tree / "results" / "flat" / stamp(0))
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skipped"]["duplicate arm run"], 2)
        used = doc["skills"]["flat"]["runs_used"]
        self.assertEqual(len(used), 20)
        self.assertEqual(sum(k.startswith("results/") for k in used), 2)
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")

    def test_reads_are_bounded_and_never_follow_a_symlink(self):
        # Round 5's reproducer (symtree/): a summary.json that is a symlink
        # to /dev/zero was read without bound.
        self.write("flat", FLAT[:1], FLAT_JUDGE)
        link = self.tree / "results" / "demo" / "20261001T000000Z" / "with_skill" / "summary.json"
        link.parent.mkdir(parents=True)
        os.symlink("/dev/zero", link)
        big = self.tree / "results" / "flat" / "20261002T000000Z" / "with_skill" / "summary.json"
        big.parent.mkdir(parents=True)
        big.write_text(json.dumps({"pad": "x" * (compute_gates.READ_CAPS["summary.json"])}),
                       encoding="utf-8")
        (big.parent.parent / "report.md").write_text("# r\n", encoding="utf-8")
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skipped"]["no readable arm summary"], 1)
        self.assertEqual(doc["skipped"]["arm summary over its size cap"], 1)
        # The reader itself: a symlink, a FIFO and an oversize file.
        target = self.tmp / "t.json"
        target.write_text("{}", encoding="utf-8")
        os.symlink(target, self.tmp / "l.json")
        os.mkfifo(self.tmp / "fifo")
        self.assertEqual(compute_gates._read_bytes(self.tmp / "l.json", 10), (None, "unreadable"))
        self.assertEqual(compute_gates._read_bytes(self.tmp / "fifo", 10), (None, "unreadable"))
        self.assertEqual(compute_gates._read_bytes(target, 1), (None, "oversize"))
        self.assertEqual(compute_gates._read_bytes(target, 2), (b"{}", None))

    def test_a_memory_error_is_one_skills_failure(self):
        from unittest import mock
        self.write("flat", FLAT[:10], FLAT_JUDGE)
        self.write("gain", FLAT[:5], [8.5, 8.7] * 3, without_judges=[6.0, 6.2] * 3,
                   model_tag="b")
        real = gates.retirement

        def failing(index, models, skill, policy, expected):
            if skill == "gain":
                raise MemoryError
            return real(index, models, skill, policy, expected)
        with mock.patch.object(gates, "retirement", failing):
            _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["gain"]["reason"], "the verdict raised MemoryError")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")

    def test_arms_of_other_skill_sets_are_not_paired(self):
        # compute_gates' pair key: arm_context.others_digest and the frozen
        # guidance, both arms alike.
        run_ids = self.write("flat", FLAT[:10], FLAT_JUDGE)
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "retire")
        for run_id in run_ids:
            for path in (self.tree / "routine-results" / run_id).rglob(
                    "without_skill/summary.json"):
                self.edit(path, lambda d: d["arm_context"].update(others_digest="other"))
        _, doc = self.run_gates("b.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "insufficient")
        for run_id in run_ids:
            for path in (self.tree / "routine-results" / run_id).rglob(
                    "without_skill/summary.json"):
                self.edit(path, lambda d: (d["arm_context"].update(others_digest="o"),
                                           d["context"]["guidance"].update(digest="g2")))
        _, doc = self.run_gates("c.json")
        self.assertEqual(doc["skills"]["flat"]["verdict"], "insufficient")

    def test_only_one_skill_retires_per_evaluation(self):
        self.write("one", FLAT[:10], FLAT_JUDGE)
        self.write("two", FLAT[:11], FLAT_JUDGE, model_tag="b")
        _, doc = self.run_gates("a.json")
        self.assertEqual(doc["skills"]["two"]["verdict"], "retire")
        self.assertEqual(doc["skills"]["one"]["verdict"], "retire-pending")
        self.assertEqual(doc["skills"]["one"]["raw_verdict"], "retire")

    # --- Round 6 -----------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
