#!/usr/bin/env python3
"""Per-arm efficiency aggregates (#71): turns, tokens, wall time and tool
errors across trials, with the with-minus-without delta.

`run_eval.aggregate_trials` used to reduce only the agent cost. These tests
pin the figures the harness already records per trial (`num_turns`,
`duration_ms`, the four `usage` token counts, tool errors from the tool
trace) as `aggregate.efficiency`: mean, median, min, max, sum and `n`, with
`n_missing` for a trial that did not report the figure. They also pin the
delta and where it is printed, and that nothing decides on these figures.

Hermetic: pure functions and temp directories; no CLI, clock or network.
Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_efficiency_metrics.py`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
import run_eval  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "local_eval_for_efficiency", REPO_ROOT / "scripts" / "local_eval.py")
local_eval = importlib.util.module_from_spec(_SPEC)
sys.path.insert(0, str(REPO_ROOT / "scripts"))
_SPEC.loader.exec_module(local_eval)

NAMES = ("cost_usd", "num_turns", "duration_ms", "input_tokens",
         "output_tokens", "cache_creation_input_tokens",
         "cache_read_input_tokens", "tool_errors")


def trial(cost=0.5, turns=10, duration=1000, inp=100, out=50, create=20,
          read=400, **missing):
    """A scored trial summary; a keyword in `missing` set to True drops that
    figure (a usage key is dropped from `usage`)."""
    usage = {"input_tokens": inp, "output_tokens": out,
             "cache_creation_input_tokens": create,
             "cache_read_input_tokens": read}
    agent = {"cost_usd": cost, "num_turns": turns, "duration_ms": duration,
             "usage": usage}
    for key in list(missing):
        if missing[key]:
            (usage if key in usage else agent).pop(key, None)
    return {"error": None, "agent": agent, "objective_checks": None,
            "judge": None}


class TestEfficiencyAggregation(unittest.TestCase):
    def test_three_trials_get_mean_median_min_max_sum_and_n(self):
        trials = [trial(turns=4, duration=1000, inp=10, out=1, create=0, read=100, cost=0.1),
                  trial(turns=6, duration=3000, inp=20, out=2, create=5, read=300, cost=0.2),
                  trial(turns=11, duration=2000, inp=60, out=9, create=7, read=200, cost=0.9)]
        stats = run_eval.aggregate_trials(trials, [0, 2, 1])
        eff = stats["aggregate"]["efficiency"]
        self.assertEqual(tuple(eff), NAMES)
        self.assertEqual(eff["num_turns"],
                         {"n": 3, "n_missing": 0, "mean": 7.0, "median": 6,
                          "min": 4, "max": 11, "sum": 21})
        self.assertEqual(eff["duration_ms"]["median"], 2000)
        self.assertEqual(eff["input_tokens"]["mean"], 30.0)
        self.assertEqual(eff["output_tokens"]["max"], 9)
        self.assertEqual(eff["cache_creation_input_tokens"]["min"], 0)
        self.assertEqual(eff["cache_read_input_tokens"]["sum"], 600)
        self.assertEqual(eff["tool_errors"],
                         {"n": 3, "n_missing": 0, "mean": 1.0, "median": 1,
                          "min": 0, "max": 2, "sum": 3})
        self.assertAlmostEqual(eff["cost_usd"]["mean"], 0.4)
        self.assertEqual(eff["cost_usd"]["median"], 0.2)

    def test_the_cost_figures_that_existed_are_unchanged(self):
        stats = run_eval.aggregate_trials([trial(cost=0.25), trial(cost=0.75)])
        block = stats["aggregate"]
        self.assertEqual(block["cost_usd"],
                         {"n": 2, "mean": 0.5, "min": 0.25, "max": 0.75,
                          "sum": 1.0})
        self.assertEqual(block["cost_unknown_trials"], 0)
        self.assertEqual({"objective", "judge", "cost_usd",
                          "cost_unknown_trials", "efficiency", "model_tokens",
                          "cross_model"}, set(block))

    def test_a_trial_missing_a_figure_is_counted_missing_not_zero(self):
        trials = [trial(turns=4, read=100), trial(turns=8, num_turns=True,
                                                  cache_read_input_tokens=True),
                  trial(turns=6, read=300)]
        eff = run_eval.aggregate_trials(trials)["aggregate"]["efficiency"]
        self.assertEqual((eff["num_turns"]["n"], eff["num_turns"]["n_missing"]),
                         (2, 1))
        self.assertEqual(eff["num_turns"]["mean"], 5.0)
        self.assertEqual(eff["cache_read_input_tokens"]["n_missing"], 1)
        self.assertEqual(eff["cache_read_input_tokens"]["mean"], 200.0)
        # The figures every trial reported are untouched.
        self.assertEqual(eff["duration_ms"]["n_missing"], 0)

    def test_values_that_are_not_finite_numbers_are_missing(self):
        trials = [trial(turns=4), trial(), trial(), trial()]
        trials[1]["agent"]["num_turns"] = float("nan")
        trials[2]["agent"]["num_turns"] = True
        trials[3]["agent"]["num_turns"] = "7"
        eff = run_eval.aggregate_trials(trials)["aggregate"]["efficiency"]
        self.assertEqual((eff["num_turns"]["n"], eff["num_turns"]["n_missing"],
                          eff["num_turns"]["mean"]), (1, 3, 4.0))

    def test_all_fields_missing_leaves_a_block_of_nulls_not_an_absent_key(self):
        trials = [{"error": None, "agent": {}, "objective_checks": None,
                   "judge": None} for _ in range(3)]
        eff = run_eval.aggregate_trials(trials)["aggregate"]["efficiency"]
        self.assertEqual(tuple(eff), NAMES)
        for name, block in eff.items():
            self.assertEqual(block, {"n": 0, "n_missing": 3, "mean": None,
                                     "median": None, "min": None, "max": None,
                                     "sum": None}, name)

    def test_errored_trials_without_an_agent_block_are_missing(self):
        trials = [trial(turns=5),
                  {"error": {"type": "timeout", "detail": ""}, "agent": None,
                   "objective_checks": None, "judge": None}]
        stats = run_eval.aggregate_trials(trials, [3, 7])
        eff = stats["aggregate"]["efficiency"]
        self.assertEqual((eff["num_turns"]["n"], eff["num_turns"]["n_missing"]),
                         (1, 1))
        # The errored trial's tool-error count is not counted either: no
        # agent call returned for it.
        self.assertEqual((eff["tool_errors"]["n"], eff["tool_errors"]["sum"]),
                         (1, 3))
        self.assertEqual(stats["n"], 2)

    def test_without_tool_error_counts_every_trial_is_missing_for_them(self):
        eff = run_eval.aggregate_trials([trial(), trial()])["aggregate"][
            "efficiency"]
        self.assertEqual((eff["tool_errors"]["n"],
                          eff["tool_errors"]["n_missing"]), (0, 2))


class TestToolErrorCount(unittest.TestCase):
    def _trace(self, **over):
        events = [{"kind": "tool_use", "id": "a"},
                  {"kind": "tool_result", "id": "a", "is_error": True},
                  {"kind": "tool_result", "id": "b", "is_error": False},
                  {"kind": "tool_result", "id": "c", "is_error": True}]
        return {"events": events, "omitted_events": 0, **over}

    def test_counts_only_error_tool_results(self):
        self.assertEqual(run_eval._tool_error_count(self._trace()), 2)

    def test_a_repeated_tool_use_id_counts_once(self):
        # A replayed message (a resumed follow-up call, a re-emitted block)
        # repeats a result under the same id; one tool call failed once.
        events = [{"call": 0, "kind": "tool_result", "id": "a", "is_error": True},
                  {"call": 1, "kind": "tool_result", "id": "a", "is_error": True},
                  {"call": 1, "kind": "tool_result", "id": "a", "is_error": True},
                  {"call": 1, "kind": "tool_result", "id": "c", "is_error": True}]
        self.assertEqual(run_eval._tool_error_count(
            {"events": events, "omitted_events": 0}), 2)

    def test_results_without_a_usable_id_each_count(self):
        # An id the CLI sent in a shape the trace refuses is None: nothing to
        # deduplicate on, so each is its own failure.
        events = [{"kind": "tool_result", "id": None, "is_error": True},
                  {"kind": "tool_result", "id": None, "is_error": True}]
        self.assertEqual(run_eval._tool_error_count(
            {"events": events, "omitted_events": 0}), 2)

    def test_a_replayed_error_that_was_not_one_still_is_not_counted(self):
        events = [{"kind": "tool_result", "id": "a", "is_error": False},
                  {"kind": "tool_result", "id": "a", "is_error": False}]
        self.assertEqual(run_eval._tool_error_count(
            {"events": events, "omitted_events": 0}), 0)

    def test_no_trace_is_a_run_with_no_tool_call(self):
        self.assertEqual(run_eval._tool_error_count(None), 0)

    def test_a_capped_trace_is_unknown_not_a_small_count(self):
        self.assertIsNone(run_eval._tool_error_count(
            self._trace(omitted_events=3)))

    def test_the_count_is_read_from_the_trial_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            self.assertEqual(run_eval._trial_tool_errors(trial_dir), 0)
            (trial_dir / "transcripts").mkdir()
            path = trial_dir / "transcripts" / run_eval.TOOL_TRACE_NAME
            path.write_text(json.dumps(self._trace()), encoding="utf-8")
            self.assertEqual(run_eval._trial_tool_errors(trial_dir), 2)
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(run_eval._trial_tool_errors(trial_dir))


class TestEfficiencyDelta(unittest.TestCase):
    def _arm(self, turns, **kw):
        return run_eval.efficiency_stats([trial(turns=t, **kw) for t in turns])

    def test_delta_is_with_minus_without_for_mean_and_median(self):
        delta = run_eval.efficiency_delta(self._arm([4, 6, 11]),
                                          self._arm([10, 12, 20]))
        self.assertEqual(delta["num_turns"],
                         {"with_n": 3, "without_n": 3,
                          "delta_mean": 7.0 - 14.0, "delta_median": 6 - 12})
        self.assertEqual(delta["duration_ms"]["delta_mean"], 0.0)
        self.assertEqual(tuple(delta), NAMES)

    def test_a_side_with_no_value_has_a_null_delta(self):
        delta = run_eval.efficiency_delta(
            self._arm([4, 6]), run_eval.efficiency_stats([]))
        self.assertEqual(delta["num_turns"],
                         {"with_n": 2, "without_n": 0, "delta_mean": None,
                          "delta_median": None})


class TestEfficiencyReport(unittest.TestCase):
    def _arm(self, name, turns, **kw):
        stats = run_eval.aggregate_trials([trial(turns=t, **kw) for t in turns],
                                          [0] * len(turns))
        return {"arm": name, "models_used": [], "stats": stats}

    def _report(self, arms):
        return run_eval._render_trials_report(
            "demo", "20261006T000000Z", 3,
            [{"label": "demo", "prompt": "p", "arms": arms}])

    def test_both_arms_print_per_arm_cells_and_the_delta(self):
        report = self._report([self._arm("with_skill", [4, 6, 11], cost=0.1),
                               self._arm("without_skill", [10, 12, 20],
                                         cost=0.3)])
        self.assertIn("| Metric (mean / median) | with_skill | without_skill "
                      "| Delta mean | Delta median |", report)
        self.assertIn("| num_turns | 7 / 6 | 14 / 12 | -7 | -6 |", report)
        self.assertIn("| cost_usd | 0.1000 / 0.1000 | 0.3000 / 0.3000 "
                      "| -0.2000 | -0.2000 |", report)
        self.assertIn("| duration_ms | 1000 / 1000 | 1000 / 1000 | 0 | 0 |",
                      report)
        # The cost column of the table that was already there is intact.
        self.assertIn("Cost USD (mean / sum)", report)

    def test_a_single_arm_prints_no_delta_columns(self):
        report = self._report([self._arm("with_skill", [4, 6])])
        self.assertIn("| Metric (mean / median) | with_skill |", report)
        self.assertNotIn("Delta mean", report)

    def test_a_partly_reported_metric_says_how_many_trials_reported_it(self):
        arm = self._arm("with_skill", [4, 6, 8])
        arm["stats"]["aggregate"]["efficiency"]["num_turns"] = (
            run_eval._metric_stats([4, 6], 3))
        self.assertIn("| num_turns | 5 / 5 (2 of 3) |", self._report([arm]))

    def test_a_metric_no_arm_reported_gets_no_row(self):
        arm = self._arm("with_skill", [4, 6], cache_read_input_tokens=True)
        report = self._report([arm])
        self.assertNotIn("| cache_read_input_tokens |", report)
        self.assertIn("| num_turns |", report)


class TestLocalEvalAggregate(unittest.TestCase):
    def test_arm_carries_efficiency_over_scored_trials_only(self):
        records = [(1, trial(turns=4), None), (2, trial(turns=8), None),
                   (3, None, "unreadable summary.json")]
        arm = local_eval.aggregate_arm(records)
        eff = arm["efficiency"]
        self.assertEqual((arm["n"], arm["errors"], arm["scored"]), (3, 1, 2))
        self.assertEqual((eff["num_turns"]["n"], eff["num_turns"]["mean"],
                          eff["num_turns"]["median"]), (2, 6.0, 6.0))
        self.assertEqual(eff["tool_errors"]["n"], 0)
        # What was there before is as it was.
        self.assertEqual(arm["cost_usd"], {"n": 2, "mean": 0.5, "sum": 1.0})

    def test_a_fixture_run_with_both_arms_has_the_delta(self):
        skill, ts = "demo", "20261006T000000Z"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for k, (w, wo) in enumerate([(4, 10), (6, 14)], start=1):
                for arm, turns in (("with_skill", w), ("without_skill", wo)):
                    d = out / f"t{k}" / skill / ts / arm
                    d.mkdir(parents=True)
                    (d / "summary.json").write_text(
                        json.dumps(trial(turns=turns)), encoding="utf-8")
            aggregate = local_eval.build_aggregate(
                out, skill, [None], ["with_skill", "without_skill"], 2)
            entry = aggregate["fixtures"][local_eval.FLAT_NAME]
            self.assertEqual(entry["efficiency_delta"]["num_turns"]["delta_mean"],
                             -7.0)
            one = local_eval.build_aggregate(out, skill, [None],
                                             ["with_skill"], 2)
            self.assertNotIn("efficiency_delta",
                             one["fixtures"][local_eval.FLAT_NAME])


if __name__ == "__main__":
    unittest.main()
