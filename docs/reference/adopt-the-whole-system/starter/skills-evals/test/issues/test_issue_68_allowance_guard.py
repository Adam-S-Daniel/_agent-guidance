#!/usr/bin/env python3
"""Issue #68: the weekly allowance guard.

The owner's standing decision (2026-10-10,
https://github.com/Adam-S-Daniel/skills-evals/issues/68#issuecomment-6097945979):
all automated evals stay within 30% of the weekly subscription allowance.
Three layers enforce it, all reading `evals/budget.yml`:

  * the budget file itself: exactly four keys, each above 0 and at most 1,
    `per_run_cap` no larger than `weekly_cap` (harness/budget.py);
  * the in-run stop: harness/run_eval.py starts no trial and no judge call
    once the weekly meter has moved `per_run_cap` since the run's first
    reading, or reads `stop_when_account_above`; a run with no reading, or an
    API-billed one, is never stopped; the stop is recorded as
    `usage.budget_stop`, which the ingester accepts and checks;
  * the pre-fire check (scripts/allowance_guard.py): spent (ingested runs of
    the last seven days, each its measured delta plus the overrun, or the full
    reservation when unmeasured, counted once per run_eval invocation) plus
    pending (successful fires without results: reserved in full under PENDING_FULL_HOLD
    old, the overrun alone after that) plus
    this run's reservation must stay within the cap; an API error refuses;
    routine-eval-fire.yml runs it before the fire in every mode, and the
    weekly planner plans only the fires that fit, skipping its dispatch job
    when none does.

Hermetic: the CLI is test/fake-claude, the one GitHub API call is replaced by
a stand-in, and `now` is passed in. Workflows are parsed with PyYAML.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
ROOT = TEST_DIR.parent
HARNESS_DIR = ROOT / "harness"
FAKE_CLAUDE = TEST_DIR / "fake-claude"
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TEST_DIR))

import allowance_guard as guard  # noqa: E402
from lockfile_pins import lock_problem  # noqa: E402
import budget  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import run_eval  # noqa: E402
from arm_test_env import install_arm_test_environment  # noqa: E402

REGISTRY_URL = "https://github.com/example-adopter/agentskills"
LIMITS = {"weekly_cap": 0.30, "per_run_cap": 0.03, "per_run_overrun": 0.005,
          "stop_when_account_above": 0.80}
RESERVE = 0.035
NOW = datetime(2026, 10, 20, 15, 30, 0, tzinfo=timezone.utc)
#: The hold window in hours, read from the guard so the tests follow it.
HOLD_H = guard.PENDING_FULL_HOLD.total_seconds() / 3600
VALID_BUDGET = ("weekly_cap: 0.30\nper_run_cap: 0.03\nper_run_overrun: 0.005\n"
                "stop_when_account_above: 0.80\n")


def meter(week: float, resets: int = 1791824400) -> dict:
    """One `rate_limit_info` as CLI 2.1.296 printed it (test_issue_370)."""
    return {"status": "allowed", "resetsAt": 1791660000,
            "rateLimitType": "five_hour", "overageStatus": "rejected",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": 0.01, "resetsAt": 1791660000},
                "seven_day": {"utilization": week, "resetsAt": resets}}}


def flags(**given):
    import argparse
    return argparse.Namespace(**{"run_billing": "subscription", "run_runner": None,
                                 "run_location": None, "run_id": None,
                                 "asserted_quiet": None, **given})


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


# ---------------------------------------------------------------------------
# evals/budget.yml

class BudgetFileTests(unittest.TestCase):

    def test_the_committed_budget_is_the_standing_cap(self):
        # C6: the kit ships the standing defaults.
        self.assertEqual(budget.load_budget(), LIMITS)
        self.assertEqual(budget.DEFAULT_BUDGET, ROOT / "evals" / "budget.yml")
        self.assertAlmostEqual(budget.run_reservation(LIMITS), RESERVE, places=12)

    def test_the_committed_file_explains_its_unit(self):
        text = (ROOT / "evals" / "budget.yml").read_text(encoding="utf-8")
        self.assertIn("points = percent of the weekly allowance", text)

    def test_bad_budgets_are_refused_without_echoing_a_value(self):
        cases = {
            "unknown key": VALID_BUDGET + "extra_cap: 0.1\n",
            "missing key": "weekly_cap: 0.3\nper_run_cap: 0.03\nper_run_overrun: 0.005\n",
            "duplicate key": VALID_BUDGET + "weekly_cap: 0.31\n",
            "zero": VALID_BUDGET.replace("per_run_overrun: 0.005", "per_run_overrun: 0"),
            "negative": VALID_BUDGET.replace("0.03\n", "-0.0377\n"),
            "above one": VALID_BUDGET.replace("0.80", "1.0377"),
            "bool": VALID_BUDGET.replace("0.80", "true"),
            "string": VALID_BUDGET.replace("0.80", "'0.8'"),
            "nan": VALID_BUDGET.replace("0.80", ".nan"),
            "infinite": VALID_BUDGET.replace("0.80", ".inf"),
            "per run above weekly": VALID_BUDGET.replace("per_run_cap: 0.03",
                                                         "per_run_cap: 0.3377"),
            "not a mapping": "- 0.3\n",
            "not yaml": "weekly_cap: [0.3\n",
            "empty": "",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(budget.BudgetError) as caught:
                    budget.parse_budget(text)
                self.assertNotIn("377", str(caught.exception))

    def test_one_and_integers_are_numbers_in_range(self):
        loaded = budget.parse_budget("weekly_cap: 1\nper_run_cap: 1\n"
                                     "per_run_overrun: 0.5\nstop_when_account_above: 1\n")
        self.assertEqual(loaded["weekly_cap"], 1.0)

    def test_a_missing_file_is_a_budget_error(self):
        with self.assertRaises(budget.BudgetError):
            budget.load_budget(ROOT / "evals" / "no-such-budget.yml")


# ---------------------------------------------------------------------------
# The in-run stop

class StopReadingTests(unittest.TestCase):

    def test_the_per_run_cap_stops_at_the_cap_not_a_float_below_it(self):
        # 0.73 - 0.70 is 0.0299999... in floats; rounded as _meter_delta
        # rounds a delta, it is the cap.
        self.assertEqual(
            run_eval.budget_stop_reading([(0.70, 1), (0.73, 1)], LIMITS),
            {"reason": "per_run_cap", "utilization": 0.73, "moved": 0.03,
             "limit": 0.03})

    def test_below_both_limits_nothing_stops(self):
        self.assertIsNone(run_eval.budget_stop_reading([(0.70, 1), (0.72, 1)], LIMITS))
        self.assertIsNone(run_eval.budget_stop_reading([(0.79, 1)], LIMITS))
        self.assertIsNone(run_eval.budget_stop_reading([], LIMITS))

    def test_the_account_limit_stops_and_wins_over_the_run_cap(self):
        self.assertEqual(
            run_eval.budget_stop_reading([(0.70, 1), (0.80, 1)], LIMITS),
            {"reason": "account_above", "utilization": 0.80, "moved": 0.1,
             "limit": 0.80})

    def test_after_a_window_reset_the_new_reading_is_all_moved(self):
        self.assertEqual(
            run_eval.budget_stop_reading([(0.70, 1), (0.04, 2)], LIMITS),
            {"reason": "per_run_cap", "utilization": 0.04, "moved": 0.04,
             "limit": 0.03})
        self.assertIsNone(run_eval.budget_stop_reading([(0.70, 1), (0.02, 2)], LIMITS))


class TelemetryStopTests(unittest.TestCase):

    def setUp(self):
        self.addCleanup(run_eval._TELEMETRY.begin)

    def begin(self, **given):
        run_eval._TELEMETRY.begin(flags(**given))
        run_eval._TELEMETRY.budget = dict(LIMITS)

    def queue(self, *weeks):
        now = datetime(2026, 10, 12, 17, 0, tzinfo=timezone.utc)
        run_eval._TELEMETRY.record("agent", [{
            "started": now, "ended": now, "outcome": "ok",
            "events": [{"at": None, "info": meter(week)} for week in weeks]}])

    def test_no_reading_no_stop(self):
        self.begin()
        self.assertIsNone(run_eval._TELEMETRY.budget_stop())

    def test_readings_still_queued_for_a_summary_count_and_the_stop_sticks(self):
        self.begin()
        self.queue(0.70)
        self.assertIsNone(run_eval._TELEMETRY.budget_stop())
        self.queue(0.74)
        stop = run_eval._TELEMETRY.budget_stop()
        self.assertEqual(stop["reason"], "per_run_cap")
        # A later, lower reading does not undo it.
        run_eval._TELEMETRY.pending = []
        self.assertIs(run_eval._TELEMETRY.budget_stop(), stop)

    def test_an_unreadable_or_windowless_reading_is_ignored(self):
        self.begin()
        now = datetime(2026, 10, 12, 17, 0, tzinfo=timezone.utc)
        run_eval._TELEMETRY.record("agent", [{
            "started": now, "ended": now, "outcome": "ok",
            "events": [{"at": None, "info": None},
                       {"at": None, "info": {"unifiedWindows": {"five_hour": {}}}},
                       {"at": None, "info": {"unifiedWindows": {
                           "seven_day": {"utilization": True}}}}]}])
        self.assertIsNone(run_eval._TELEMETRY.budget_stop())

    def test_an_api_billed_run_is_never_stopped(self):
        self.begin(run_billing="api")
        self.queue(0.95)
        self.assertIsNone(run_eval._TELEMETRY.budget_stop())

    def test_no_budget_set_stops_nothing(self):
        run_eval._TELEMETRY.begin(flags())
        self.queue(0.95)
        self.assertIsNone(run_eval._TELEMETRY.budget_stop())

    def test_a_stopped_guidance_arm_is_not_started(self):
        self.begin()
        self.queue(0.95)
        run_eval._TELEMETRY.budget_stop()
        with self.assertRaises(run_eval.BudgetStopped) as caught:
            run_eval._run_guidance_arm({"name": "treatment", "mode": "section"},
                                       {}, Path("/nonexistent"), {}, flags(), "T")
        self.assertIn("account_above", str(caught.exception))
        self.assertNotIn("0.95", str(caught.exception))


class EndToEndStopTests(unittest.TestCase):
    """run_eval.py against test/fake-claude, with the committed budget."""

    TS = "20261012T170000Z"
    SKILL = "budget-probe"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "argv.log"
        self.batches = self.tmp / "meter.json"
        self.eval_dir = self.tmp / "evals" / self.SKILL
        (self.eval_dir / "seed").mkdir(parents=True)
        (self.eval_dir / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        self.registry = self.tmp / "registry"
        skill_dir = self.registry / "plugins" / "b" / "skills" / self.SKILL
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {self.SKILL}\ndescription: stand-in.\n---\n", encoding="utf-8")
        self.results = self.tmp / "results"

    def fixture(self, batches=None) -> None:
        env = {"FAKE_CLAUDE_MODE": "agent_and_judge",
               "FAKE_CLAUDE_ARGV_LOG": str(self.log)}
        if batches is not None:
            self.batches.write_text(json.dumps(batches), encoding="utf-8")
            env["FAKE_CLAUDE_RATE_LIMIT_FILE"] = str(self.batches)
        (self.eval_dir / "fixture.yaml").write_text(yaml.safe_dump({
            "skill": self.SKILL, "registry": REGISTRY_URL, "prompt": "do it",
            "model": "claude-sonnet-5", "judge_rubric": "r",
            "judge": {"model": "fake-judge"}, "env": env,
            "objective_checks": [{"id": "seed-kept", "description": "d",
                                  "type": "files_unchanged",
                                  "paths": ["README.md"]}]}), encoding="utf-8")

    def run_eval(self, *args):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GITHUB_ACTIONS", "GITHUB_RUN_ID",
                            "CLAUDE_CODE_REMOTE_SESSION_ID")}
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        env.update({"CLAUDE_BIN": str(FAKE_CLAUDE), "HOME": str(home),
                    "XDG_STATE_HOME": str(home / "state"),
                    "FAKE_CLAUDE_MODE": "agent_and_judge",
                    "FAKE_CLAUDE_ARGV_LOG": str(self.log)})
        return subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(self.eval_dir),
             "--arm", "both", "--registry", f"agentskills={self.registry}",
             "--results-dir", str(self.results), "--timeout", "30",
             "--timestamp", self.TS, *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)

    def summaries(self) -> dict:
        return {path.relative_to(self.results).as_posix():
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.results.rglob("summary.json"))}

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return ["judge" if "fake-judge" in argv else "agent"
                for argv in (json.loads(line)["argv"] for line in
                             self.log.read_text(encoding="utf-8").splitlines())
                if "--version" not in argv]

    def assert_ingests(self, found: dict) -> None:
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                ingest.check_summary(summary, rel, ingest.parse_result_path(rel))

    def test_the_per_run_cap_stops_new_trials_and_judge_calls(self):
        self.fixture(batches=[[meter(0.70)], [meter(0.74)], [meter(0.74)],
                              [meter(0.74)]])
        proc = self.run_eval("--trials", "2", "--judge", "always",
                             "--run-billing", "subscription")
        said = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, said)
        self.assertIn("Budget stop: per_run_cap", proc.stdout)
        # Trial 1's agent and judge; trial 2's agent, whose reading reached
        # the cap, so its judge and every later trial never started.
        self.assertEqual(self.calls(), ["agent", "judge", "agent"])
        found = self.summaries()
        self.assertEqual(len(found), 6, sorted(found))
        self.assert_ingests(found)
        stop = {"reason": "per_run_cap", "utilization": 0.74, "moved": 0.04,
                "limit": budget.load_budget()["per_run_cap"]}
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assertEqual(summary["usage"]["budget_stop"], stop)
        base = f"{self.SKILL}/{self.TS}"
        self.assertIsNone(found[f"{base}/with_skill/trial-1/summary.json"]["error"])
        second = found[f"{base}/with_skill/trial-2/summary.json"]
        self.assertIsNone(second["error"])
        self.assertIn("judge call not started", second["judge"]["error"])
        self.assertEqual(second["usage"]["measured"]["judge"]["calls"], 0)
        for trial in (1, 2):
            skipped = found[f"{base}/without_skill/trial-{trial}/summary.json"]
            self.assertEqual(skipped["error"]["type"], run_eval.BUDGET_STOP_ERROR)
            self.assertEqual(skipped["usage"]["measured"]["agent"]["calls"], 0)
        report = (self.results / self.SKILL / self.TS / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("Budget stop: per_run_cap", report)

    def test_the_account_limit_stops_the_next_trial_and_a_held_judge(self):
        self.fixture(batches=[[meter(0.96)], [meter(0.96)]])
        proc = self.run_eval("--run-billing", "subscription")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), ["agent"])
        found = self.summaries()
        self.assert_ingests(found)
        for summary in found.values():
            self.assertEqual(summary["usage"]["budget_stop"]["reason"], "account_above")
        base = f"{self.SKILL}/{self.TS}"
        self.assertIn("judge call not started",
                      found[f"{base}/with_skill/summary.json"]["judge"]["error"])
        self.assertEqual(found[f"{base}/without_skill/summary.json"]["error"]["type"],
                         "budget_stop")

    def test_an_api_billed_run_is_not_stopped(self):
        self.fixture(batches=[[meter(0.95)], [meter(0.99)]])
        proc = self.run_eval("--judge", "always", "--run-billing", "api")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), ["agent", "judge", "agent", "judge"])
        for summary in self.summaries().values():
            self.assertNotIn("budget_stop", summary["usage"])

    def test_a_run_whose_cli_prints_no_reading_is_not_stopped(self):
        self.fixture()
        proc = self.run_eval("--judge", "always", "--run-billing", "subscription")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.calls(), ["agent", "judge", "agent", "judge"])
        for summary in self.summaries().values():
            self.assertNotIn("budget_stop", summary["usage"])

    def test_an_invalid_budget_is_a_configuration_error_before_any_call(self):
        self.fixture()
        bad = self.tmp / "budget.yml"
        bad.write_text(VALID_BUDGET + "surprise: 1\n", encoding="utf-8")
        proc = self.run_eval("--budget", str(bad))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("configuration error: budget has unknown keys", proc.stdout)
        self.assertEqual(self.calls(), [])


# ---------------------------------------------------------------------------
# The ingester accepts `usage.budget_stop` and checks it

class IngestBudgetStopTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(run_eval._TELEMETRY.begin)
        now = datetime(2026, 10, 12, 17, 0, tzinfo=timezone.utc)
        # The run block reads what the environment observes; a hosted
        # session's own id would otherwise land in it.
        quiet = {k: v for k, v in os.environ.items()
                 if k not in ("GITHUB_ACTIONS", "GITHUB_RUN_ID",
                              "CLAUDE_CODE_REMOTE_SESSION_ID")}
        with mock.patch.object(run_eval, "_now", return_value=now), \
             mock.patch.dict(os.environ, quiet, clear=True):
            run_eval._TELEMETRY.begin(flags(run_runner="routine", run_location="cloud",
                                            run_id="20261012T170500Z-ab12cd"))
            run_eval._TELEMETRY.budget = dict(LIMITS)
            for week in (0.70, 0.74):
                run_eval._TELEMETRY.record("agent", [{
                    "started": now, "ended": now, "outcome": "ok",
                    "events": [{"at": None, "info": meter(week)}]}])
            self.assertIsNotNone(run_eval._TELEMETRY.budget_stop())
            run_eval._write_summary(self.tmp, "s", "with_skill", "20261012T170000Z",
                                    None, None, None, None, None, extra={"n": 1},
                                    harness_version="2.1.296 (Claude Code)")
            run_eval._TELEMETRY.finish()
        self.rel = "s/20261012T170000Z/with_skill/summary.json"
        self.good = json.loads((self.tmp / self.rel).read_text(encoding="utf-8"))

    def check(self, summary):
        ingest.check_summary(summary, self.rel, ingest.parse_result_path(self.rel))

    def changed(self, **stop):
        summary = json.loads(json.dumps(self.good))
        summary["usage"]["budget_stop"].update(stop)
        return summary

    def test_the_harness_record_is_accepted(self):
        self.assertEqual(self.good["usage"]["budget_stop"],
                         {"reason": "per_run_cap", "utilization": 0.74,
                          "moved": 0.04, "limit": 0.03})
        self.check(self.good)

    def test_a_summary_without_one_still_ingests(self):
        summary = json.loads(json.dumps(self.good))
        del summary["usage"]["budget_stop"]
        self.check(summary)

    def test_records_the_harness_never_writes_are_rejected(self):
        bad = {
            "unknown reason": self.changed(reason="weekly_cap"),
            "extra key": self.changed(note="x"),
            "zero limit": self.changed(limit=0),
            "limit above one": self.changed(limit=1.5),
            "not reached": self.changed(moved=0.01),
            "account not reached": self.changed(reason="account_above", limit=0.8),
            "string number": self.changed(moved="0.04"),
            "bool": self.changed(utilization=True),
        }
        missing = json.loads(json.dumps(self.good))
        del missing["usage"]["budget_stop"]["limit"]
        bad["missing key"] = missing
        not_object = json.loads(json.dumps(self.good))
        not_object["usage"]["budget_stop"] = "stopped"
        bad["not an object"] = not_object
        api = json.loads(json.dumps(self.good))
        api["run"]["billing"] = "api"
        bad["api billed"] = api
        for name, summary in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(ingest.Rejected):
                    self.check(summary)

    def test_the_ingesters_reasons_are_the_harness_reasons(self):
        found = {run_eval.budget_stop_reading(readings, LIMITS)["reason"]
                 for readings in ([(0.1, 1), (0.2, 1)], [(0.9, 1)])}
        self.assertEqual(found, set(ingest.BUDGET_STOP_REASONS))
        self.assertEqual(set(run_eval.budget_stop_reading([(0.9, 1)], LIMITS)),
                         set(ingest.BUDGET_STOP_KEYS))


# ---------------------------------------------------------------------------
# The pre-fire check

def usage_block(delta=0.02, *, reset=False, started="2026-10-19T10:00:00Z"):
    window = {"from": 0.5, "to": 0.5 + (delta or 0),
              "delta": None if reset else delta, "window_reset": reset}
    return {"started_at": started, "meter_delta": None if delta is None else {
        "confounded": True, "scope": "account-wide", "windows": {"seven_day": window}},
        "measured": {"scope": "trial", "started_at": started}}


class GuardTests(unittest.TestCase):

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)

    def run_dir(self, stamp: str, suffix: str = "a1b2c3") -> Path:
        path = self.tree / "routine-results" / f"{stamp}-{suffix}"
        path.mkdir(parents=True)
        return path

    def summary(self, run_dir: Path, rel: str, usage) -> None:
        path = run_dir / rel / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"arm": "with_skill"} if usage is None else {"arm": "x", "usage": usage}
        path.write_text(json.dumps(doc), encoding="utf-8")

    def spent(self, fires=(0, 0)) -> dict:
        # The matching rules, over a full seven days; WeekWindowTests covers
        # where the window starts.
        return guard.account(self.tree, LIMITS, NOW, fires, since=NOW - guard.WINDOW)

    def test_a_measured_run_is_its_delta_plus_the_overrun(self):
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "k/20261019T100000Z/with_skill/trial-1", usage_block(0.02))
        self.assertAlmostEqual(self.spent()["spent"], 0.025, places=12)

    def test_an_unmeasured_run_is_charged_the_full_reservation(self):
        cases = {"null delta": usage_block(None),
                 "window reset": usage_block(0.02, reset=True),
                 "no usage (before #370)": None}
        for name, block in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(self.tree / "routine-results", ignore_errors=True)
                run = self.run_dir("20261019T100000Z")
                self.summary(run, "k/20261019T100000Z/with_skill", block)
                self.assertAlmostEqual(self.spent()["spent"], RESERVE, places=12)

    def test_an_unreadable_or_empty_run_is_charged_the_full_reservation(self):
        run = self.run_dir("20261019T100000Z")
        (run / "summary.json").write_text("{not json", encoding="utf-8")
        self.run_dir("20261019T110000Z", "d4e5f6")  # report only, no summary
        self.assertAlmostEqual(self.spent()["spent"], 2 * RESERVE, places=12)

    def test_a_falling_meter_is_charged_the_overrun_only(self):
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "k/20261019T100000Z/with_skill", usage_block(-0.01))
        self.assertAlmostEqual(self.spent()["spent"], 0.005, places=12)

    def test_every_summary_of_one_invocation_is_counted_once(self):
        run = self.run_dir("20261019T100000Z")
        block = usage_block(0.02)
        for arm in ("with_skill", "without_skill"):
            for rel in (arm, f"{arm}/trial-1", f"{arm}/trial-2"):
                trial_block = json.loads(json.dumps(block))
                trial_block["measured"]["started_at"] = rel  # differs per summary
                self.summary(run, f"k/20261019T100000Z/{rel}", trial_block)
        state = self.spent()
        self.assertEqual(state["runs"], 1)
        self.assertAlmostEqual(state["spent"], 0.025, places=12)

    def test_two_invocations_in_one_run_are_each_charged(self):
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "a/20261019T100000Z/with_skill", usage_block(0.02))
        self.summary(run, "b/20261019T101000Z/with_skill",
                     usage_block(0.01, started="2026-10-19T10:10:00Z"))
        self.assertAlmostEqual(self.spent()["spent"], 0.025 + 0.015, places=12)

    def test_the_window_is_the_seven_days_up_to_now(self):
        edge = NOW - timedelta(days=7)
        inside = self.run_dir(edge.strftime("%Y%m%dT%H%M%SZ"))
        outside = self.run_dir((edge - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ"),
                               "d4e5f6")
        ahead = self.run_dir((NOW + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"),
                             "0a0b0c")
        for run in (inside, outside, ahead):
            self.summary(run, "k/20261013T000000Z/with_skill", usage_block(0.02))
        self.assertEqual(guard.ingested_runs(self.tree, NOW), sorted([inside, ahead]))
        self.assertAlmostEqual(self.spent()["spent"], 0.05, places=12)

    def test_other_directories_are_not_runs(self):
        (self.tree / "routine-results" / "notes").mkdir(parents=True)
        (self.tree / "routine-results" / "20261019T100000Z-XYZ123").mkdir()
        (self.tree / "routine-results" / "20261399T100000Z-a1b2c3").mkdir()
        self.assertEqual(self.spent()["runs"], 0)
        self.assertEqual(guard.account(self.tree / "absent", LIMITS, NOW, (0, 0))["spent"], 0)

    def test_fires_without_results_are_reserved_and_never_negative(self):
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "k/20261019T100000Z/with_skill", usage_block(0.02))
        self.assertAlmostEqual(self.spent(fires=(3, 3))["pending"], 2 * RESERVE, places=12)
        self.assertEqual(self.spent(fires=(0, 0))["pending"], 0)

    def test_old_fires_without_results_are_charged_the_overrun_only(self):
        for index in range(6):
            run = self.run_dir("20261019T100000Z", suffix=f"a1b2c{index}")
            self.summary(run, "k/20261019T100000Z/with_skill", usage_block(0.0))
        state = self.spent(fires=(16, 0))
        self.assertAlmostEqual(state["pending"], 10 * 0.005, places=12)
        self.assertEqual(state["recent"], 0)
        self.assertTrue(guard.check(state))

    def test_fires_inside_the_day_stay_reserved_in_full(self):
        state = self.spent(fires=(3, 3))
        self.assertAlmostEqual(state["pending"], 3 * RESERVE, places=12)
        self.assertEqual(state["recent"], 3)

    def test_old_and_recent_fires_are_charged_apart(self):
        # 5 fires, 2 of them recent, 1 ingested run whose id is a day old (an
        # old fire's): 4 unresulted = 2 recent in full + 2 old at the overrun.
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "k/20261019T100000Z/with_skill", usage_block(0.02))
        state = self.spent(fires=(5, 2))
        self.assertAlmostEqual(state["pending"], 2 * 0.035 + 2 * 0.005, places=12)
        self.assertEqual(state["recent"], 2)

    def test_an_old_ingested_run_frees_an_old_fire_not_a_recent_one(self):
        run = self.run_dir("20261019T100000Z")
        self.summary(run, "k/20261019T100000Z/with_skill", usage_block(0.02))
        # Two fires, both recent, one ingested run stamped a day before now:
        # one unresulted, held in full.
        self.assertAlmostEqual(self.spent(fires=(2, 2))["pending"], RESERVE, places=12)
        # A 24h count above the 7d count (a fire landed between the two API
        # calls) cannot hold more than the unresulted fires.
        self.assertAlmostEqual(self.spent(fires=(1, 3))["pending"], 0, places=12)

    def recent_run(self, hours_ago: float, suffix: str) -> Path:
        stamp = (NOW - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%SZ")
        run = self.run_dir(stamp, suffix)
        self.summary(run, f"k/{stamp}/with_skill", usage_block(0.02))
        return run

    def test_recent_ingested_runs_free_the_recent_fires(self):
        # The run id is stamped at fire time, so a run from the hold window
        # is one of its fires: three fired, three ingested, none held.
        for index, hours in enumerate((HOLD_H * 0.05, HOLD_H * 0.5, HOLD_H * 0.95)):
            self.recent_run(hours, f"a1b2c{index}")
        state = self.spent(fires=(3, 3))
        self.assertEqual(state["recent"], 0)
        self.assertEqual(state["pending"], 0)
        # With one old fire too, the unresulted fire is that old one: charged
        # the overrun, not held in full.
        state = self.spent(fires=(4, 3))
        self.assertEqual(state["recent"], 0)
        self.assertAlmostEqual(state["pending"], 0.005, places=12)

    def test_old_ingested_runs_leave_the_recent_fires_held(self):
        # 3 recent fires and 2 old ones; the 2 ingested runs are old ones, so
        # the 3 recent fires are still held in full.
        for index in range(2):
            run = self.run_dir("20261018T100000Z", f"a1b2c{index}")
            self.summary(run, "k/20261018T100000Z/with_skill", usage_block(0.02))
        state = self.spent(fires=(5, 3))
        self.assertEqual(state["recent"], 3)
        self.assertAlmostEqual(state["pending"], 3 * RESERVE, places=12)

    def test_more_recent_runs_than_recent_fires_hold_nothing(self):
        # A manual run, or one stamped ahead of this clock, can leave more
        # recent results than recent fires: no recent fire is held, and the
        # count is never negative.
        self.recent_run(HOLD_H * 0.25, "a1b2c0")
        self.recent_run(HOLD_H * 0.5, "a1b2c1")
        ahead = (NOW + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
        self.summary(self.run_dir(ahead, "a1b2c2"), f"k/{ahead}/with_skill",
                     usage_block(0.02))
        state = self.spent(fires=(4, 1))
        self.assertEqual(state["recent"], 0)
        # 4 fires, 3 runs: the one unresulted fire is an old one, at the overrun.
        self.assertAlmostEqual(state["pending"], 0.005, places=12)

    def test_recent_and_old_runs_are_matched_to_their_own_fires(self):
        # 8 fires, 4 of them recent; 2 recent runs and 1 old run ingested.
        # 5 unresulted: 4 - 2 = 2 recent in full, 3 old at the overrun.
        self.recent_run(HOLD_H * 0.5, "a1b2c0")
        self.recent_run(HOLD_H, "a1b2c1")  # exactly PENDING_FULL_HOLD old: recent
        old = self.run_dir("20261019T100000Z", "a1b2c2")
        self.summary(old, "k/20261019T100000Z/with_skill", usage_block(0.02))
        state = self.spent(fires=(8, 4))
        self.assertEqual(state["runs"], 3)
        self.assertEqual(state["recent"], 2)
        self.assertAlmostEqual(state["pending"], 2 * RESERVE + 3 * 0.005, places=12)

    def test_the_check_is_spent_plus_pending_plus_one_reservation(self):
        # Seven fires reserved (0.245) leave room for one more (0.28 <= 0.30);
        # eight (0.28) do not (0.315).
        self.assertTrue(guard.check(self.spent(fires=(7, 7))))
        self.assertEqual(guard.fits(self.spent(fires=(7, 7))), 1)
        self.assertFalse(guard.check(self.spent(fires=(8, 8))))
        self.assertEqual(guard.fits(self.spent(fires=(8, 8))), 0)
        self.assertEqual(guard.fits(self.spent(fires=(0, 0))), 8)
        state = self.spent()
        state["spent"] = 0.31
        self.assertEqual(guard.fits(state), 0)

    def test_an_exact_fit_is_allowed_despite_float_noise(self):
        # 0.3 - 0.2 is 0.0999... in floats: an exact fit, not a refusal.
        state = {"spent": 0.2, "pending": 0.0, "reservation": 0.1, "cap": 0.3,
                 "runs": 0, "fires": 0}
        self.assertEqual(guard.fits(state), 1)
        state["spent"] = 0.2001
        self.assertEqual(guard.fits(state), 0)

    def test_a_bad_fire_count_is_refused(self):
        for fires in ((-1, 0), (True, 0), (1.5, 0), (None, 0), (0, -1), (0, None),
                      5, None, (1,), (1, 2, 3)):
            with self.subTest(fires=fires):
                with self.assertRaises(guard.GuardError):
                    self.spent(fires=fires)

    def budget_file(self):
        # The guard's own numbers are tested against LIMITS, not whatever
        # evals/budget.yml holds this week.
        path = self.tree / "budget.yml"
        path.write_text(VALID_BUDGET, encoding="utf-8")
        return path

    def main(self, command, fires):
        out = io.StringIO()
        summary = self.tree / "step-summary.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
             contextlib.redirect_stdout(out):
            code = guard.main([command, "--results-tree", str(self.tree),
                               "--budget", str(self.budget_file())], now=NOW,
                              fire_count=fires, improve_count=lambda now: (0, 0))
        return code, out.getvalue(), summary.read_text(encoding="utf-8") \
            if summary.exists() else ""

    def test_check_passes_and_reports_numbers_only(self):
        run = self.run_dir("20261020T100000Z")
        self.summary(run, "k/20261020T100000Z/with_skill", usage_block(0.02))
        code, out, summary = self.main("check", lambda now: (1, 1))
        self.assertEqual(code, 0, out)
        self.assertIn("spent 2.5 points over 1 ingested run(s)", out)
        self.assertIn("pending 0.0 points", out)
        self.assertIn("(0 from the last 0.75h held in full)", out)
        self.assertIn("cap 30.0", out)
        self.assertIn("### Weekly allowance guard", summary)
        self.assertNotIn("20261020T100000Z", out + summary)
        self.assertNotIn(str(self.tree), out + summary)

    def test_a_run_before_the_accounts_weekly_reset_is_not_charged(self):
        # NOW is Tuesday 15:30 UTC; the account's week began Monday 17:00.
        before = self.run_dir("20261019T165900Z", "aa11bb")
        self.summary(before, "k/20261019T165900Z/with_skill", usage_block(0.2))
        after = self.run_dir("20261019T170000Z", "cc22dd")
        self.summary(after, "k/20261019T170000Z/with_skill", usage_block(0.02))
        code, out, _ = self.main("check", lambda now: (1, 0))
        self.assertEqual(code, 0, out)
        self.assertIn("spent 2.5 points over 1 ingested run(s)", out)

    def test_check_refuses_over_the_cap(self):
        code, out, _ = self.main("check", lambda now: (8, 8))
        self.assertEqual(code, 1)
        self.assertIn(guard.REFUSAL, out)

    def test_fits_prints_the_count(self):
        code, out, _ = self.main("fits", lambda now: (6, 6))
        self.assertEqual(code, 0)
        self.assertIn("fires=2", out)

    def test_an_api_error_refuses_the_fire(self):
        def failing(now):
            raise guard.GuardError("the workflow runs API answered 502")
        code, out, _ = self.main("check", failing)
        self.assertEqual(code, 1)
        self.assertIn("answered 502; refusing", out)

    def test_a_bad_budget_refuses_the_fire(self):
        bad = self.tree / "budget.yml"
        bad.write_text("weekly_cap: 2\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = guard.main(["check", "--results-tree", str(self.tree),
                               "--budget", str(bad)], now=NOW, fire_count=lambda now: (0, 0),
                              improve_count=lambda now: (0, 0))
        self.assertEqual(code, 1)
        self.assertIn("refusing", out.getvalue())


class LiveFiresTests(unittest.TestCase):
    """_live_fires makes the same call twice, at the 7-day and 24-hour bounds."""

    def live(self, opener):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "example-adopter/skills-evals",
                                          "GH_TOKEN": "tok"}), \
             self.use(opener):
            return guard._live_fires(NOW)

    def use(self, opener):
        # The opener is a default argument, bound at definition: wrap the call.
        real = guard.count_successful_fires
        return mock.patch.object(
            guard, "count_successful_fires",
            lambda *args, **kwargs: real(*args, opener=opener, **kwargs))

    def test_it_counts_the_week_and_the_last_day(self):
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            total = 5 if "2026-10-13T15" in request.full_url else 2
            return FireCountTests.Response(json.dumps({"total_count": total}).encode())
        self.assertEqual(self.live(opener), (5, 2))
        self.assertEqual(len(seen), 2)
        self.assertIn("created=%3E%3D2026-10-13T15%3A30%3A00Z", seen[0])
        self.assertIn("created=%3E%3D2026-10-20T14%3A45%3A00Z", seen[1])
        self.assertTrue(all("per_page=1" in url and "status=success" in url for url in seen))

    def test_a_failed_hold_window_count_fails_closed(self):
        def opener(request, timeout):
            if "2026-10-20T14%3A45" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 502, "Bad", {},
                                             io.BytesIO(b"private-body-marker"))
            return FireCountTests.Response(b'{"total_count": 5}')
        with self.assertRaises(guard.GuardError) as caught:
            self.live(opener)
        self.assertEqual(str(caught.exception), "the workflow runs API answered 502")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "example-adopter/skills-evals",
                                          "GH_TOKEN": "tok"}), \
             self.use(opener), contextlib.redirect_stdout(out):
            code = guard.main(["check", "--results-tree", tempfile.gettempdir()], now=NOW)
        self.assertEqual(code, 1)
        self.assertIn("answered 502; refusing", out.getvalue())
        self.assertNotIn("private-body-marker", out.getvalue())


class FireCountTests(unittest.TestCase):
    """count_successful_fires, against a stand-in opener: no network."""

    class Response:
        def __init__(self, body: bytes, status: int = 200):
            self.body, self.status = body, status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit=-1):
            return self.body

    def count(self, opener, repo="example-adopter/skills-evals"):
        return guard.count_successful_fires(repo, NOW - timedelta(days=7), "tok",
                                            opener=opener)

    def test_it_asks_for_successful_fires_since_the_window_start(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            return self.Response(b'{"total_count": 4, "workflow_runs": []}')
        self.assertEqual(self.count(opener), 4)
        [request] = seen
        self.assertTrue(request.full_url.startswith(
            "https://api.github.com/repos/example-adopter/skills-evals/actions/"
            "workflows/routine-eval-fire.yml/runs?"))
        self.assertIn("created=%3E%3D2026-10-13T15%3A30%3A00Z", request.full_url)
        self.assertIn("status=success", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer tok")

    def test_an_http_error_names_only_its_status(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b"private-body-marker"))
        with self.assertRaises(guard.GuardError) as caught:
            self.count(opener)
        self.assertEqual(str(caught.exception), "the workflow runs API answered 403")

    def test_unreachable_and_unreadable_answers_fail_closed(self):
        def unreachable(request, timeout):
            raise urllib.error.URLError("down")
        bodies = (b"not json", b'{"total_count": -1}', b'{"total_count": true}',
                  b'["private-body-marker"]', b"{}")
        openers = [unreachable] + [
            (lambda body: lambda request, timeout: self.Response(body))(body)
            for body in bodies]
        openers.append(lambda request, timeout: self.Response(b"{}", status=204))
        for opener in openers:
            with self.subTest(opener=opener):
                with self.assertRaises(guard.GuardError) as caught:
                    self.count(opener)
                self.assertNotIn("private-body-marker", str(caught.exception))

    def test_a_malformed_repository_is_refused_before_any_call(self):
        def opener(request, timeout):
            raise AssertionError("no call expected")
        for repo in ("", "no-slash", "a/b/c", "a b/c"):
            with self.subTest(repo=repo):
                with self.assertRaises(guard.GuardError):
                    self.count(opener, repo=repo)


# ---------------------------------------------------------------------------
# The weekly planner plans only what fits

def snapshot(week, *, at=NOW - timedelta(hours=1), resets_in=timedelta(days=1)):
    """One `usage.measured.meter.snapshots` entry, `at` as the harness writes it."""
    resets = int((NOW + resets_in).timestamp())
    return {"at": at.strftime("%Y-%m-%dT%H:%M:%S.123Z"), "at_source": "cli_message",
            "info": meter(week, resets)}


class PaceRoomTests(unittest.TestCase):
    """the owner, 2026-10-25: "The percentage of weekly should allow for more
    usage if I'm approaching end of week and under pace". The newest meter
    snapshot releases max(0, stop - utilization) * week elapsed; the fixed cap
    left stays a floor."""

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)

    def metered(self, snapshots, stamp="20261020T100000Z", suffix="a1b2c3") -> Path:
        run = self.tree / "routine-results" / f"{stamp}-{suffix}"
        path = run / "k" / stamp / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        block = usage_block(0.02)
        block["measured"]["meter"] = {"snapshots": snapshots, "omitted": 0}
        path.write_text(json.dumps({"arm": "x", "usage": block}), encoding="utf-8")
        return path

    def state(self, fires=(8, 8)) -> dict:
        return guard.account(self.tree, LIMITS, NOW, fires)

    def test_no_snapshot_leaves_the_fixed_cap_alone(self):
        run = self.tree / "routine-results" / "20261020T100000Z-a1b2c3" / "summary.json"
        run.parent.mkdir(parents=True)
        run.write_text(json.dumps({"arm": "x", "usage": usage_block(0.02)}),
                       encoding="utf-8")
        state = self.state()
        self.assertEqual((state["pace_room"], state["account_used"],
                          state["week_elapsed"]), (0.0, None, None))
        self.assertEqual(guard.fits(state), 0)
        self.assertEqual(guard.fits(self.state((0, 0))), 7)

    def test_under_pace_late_in_the_week_allows_more_than_the_cap(self):
        self.metered([snapshot(0.36)])
        state = self.state((1, 1))
        # Spent 0.025, nothing pending: the cap alone leaves 0.275, 7 fires.
        # One day of seven left at 36%: (0.80 - 0.36) * 6/7 = 0.377, 10 fires.
        self.assertEqual(state["pending"], 0)
        self.assertAlmostEqual(state["pace_room"], 0.44 * 6 / 7, places=12)
        self.assertAlmostEqual(state["account_used"], 0.36, places=12)
        self.assertAlmostEqual(state["week_elapsed"], 6 / 7, places=12)
        self.assertEqual(guard.fits(state), 10)
        self.assertTrue(guard.check(state))
        self.assertEqual(guard.fires_that_fit(self.tree, LIMITS, NOW, (1, 1)), 10)

    def test_fires_without_results_come_off_the_pace_room(self):
        # The snapshot may predate them, so each pending fire (3.5 points)
        # costs one fire of pace room; otherwise every fire would fit until
        # the next snapshot is ingested.
        self.metered([snapshot(0.36)])
        for extra in range(12):
            with self.subTest(pending_fires=extra):
                state = self.state((1 + extra, 1 + extra))
                self.assertAlmostEqual(state["pending"], extra * RESERVE, places=12)
                self.assertEqual(guard.fits(state), max(0, 10 - extra))

    def test_the_fixed_cap_stays_the_floor_as_fires_pend(self):
        # At 60% the pace room, 0.20 * 6/7 = 0.171, is under the 0.275 the
        # cap leaves, so the cap's count holds as fires pend.
        self.metered([snapshot(0.60)])
        for extra in range(9):
            with self.subTest(pending_fires=extra):
                state = self.state((1 + extra, 1 + extra))
                self.assertEqual(guard.fits(state), max(0, 7 - extra))

    def test_early_in_the_week_the_fixed_cap_rules(self):
        self.metered([snapshot(0.05, resets_in=timedelta(days=7, hours=-1))])
        state = self.state()
        self.assertAlmostEqual(state["week_elapsed"], 1 / 168, places=12)
        self.assertAlmostEqual(state["pace_room"], 0.75 / 168, places=12)
        self.assertEqual(guard.fits(state), 0)
        self.assertEqual(guard.fits(self.state((0, 0))), 7)

    def test_at_or_over_pace_there_is_no_pace_room(self):
        for week in (0.80, 0.95, 1.0):
            with self.subTest(utilization=week):
                shutil.rmtree(self.tree / "routine-results", ignore_errors=True)
                self.metered([snapshot(week)])
                state = self.state()
                self.assertEqual(state["pace_room"], 0)
                self.assertAlmostEqual(state["account_used"], week, places=12)
                self.assertEqual(guard.fits(state), 0)

    def test_an_unusable_snapshot_gives_no_pace_room(self):
        def week(**window):
            snap = snapshot(0.36)
            snap["info"]["unifiedWindows"]["seven_day"].update(window)
            return [snap]
        resets = int((NOW + timedelta(days=1)).timestamp())
        cases = {
            "stale": [snapshot(0.36, at=NOW - timedelta(hours=12, seconds=1))],
            "ahead of the clock": [snapshot(0.36, at=NOW + timedelta(minutes=6))],
            "reset past": [snapshot(0.36, resets_in=timedelta(seconds=-1))],
            "reset now": [snapshot(0.36, resets_in=timedelta(0))],
            "reset over a week after the reading": [
                snapshot(0.36, resets_in=timedelta(days=7))],
            "above one": week(utilization=1.5),
            "negative": week(utilization=-0.1),
            "bool utilization": week(utilization=True),
            "string utilization": week(utilization="0.36"),
            "nan utilization": week(utilization=float("nan")),
            "missing utilization": week(utilization=None),
            "bool reset": week(resetsAt=True),
            "string reset": week(resetsAt=str(resets)),
            "infinite reset": week(resetsAt=float("inf")),
            "no seven-day window": [{**snapshot(0.36), "info": {"unifiedWindows": {}}}],
            "info not an object": [{**snapshot(0.36), "info": "x"}],
            "malformed entries only": ["x", 5, None, {"at": 5}, {"at": "yesterday"},
                                       {"at": "2026-10-20T14:30:00"}],
            "snapshots not a list": {"at": "2026-10-20T14:30:00Z"},
        }
        for name, snapshots in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(self.tree / "routine-results", ignore_errors=True)
                self.metered(snapshots)
                state = self.state()
                self.assertEqual(state["pace_room"], 0)
                self.assertIsNone(state["account_used"])
                self.assertEqual(guard.fits(state), 0)

    def test_an_untrusted_summary_gives_no_pace_room(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        for name in ("symlink", "oversize", "unparseable"):
            with self.subTest(case=name):
                shutil.rmtree(self.tree / "routine-results", ignore_errors=True)
                path = self.metered([snapshot(0.36)])
                if name == "symlink":
                    target = outside / "summary.json"
                    shutil.move(path, target)
                    path.symlink_to(target)
                elif name == "oversize":
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    doc["pad"] = "x" * guard.SUMMARY_MAX_BYTES
                    path.write_text(json.dumps(doc), encoding="utf-8")
                else:
                    path.write_text(path.read_text(encoding="utf-8")[:-1],
                                    encoding="utf-8")
                state = self.state()
                self.assertEqual(state["pace_room"], 0)
                self.assertIsNone(state["account_used"])

    def test_the_newest_snapshot_wins(self):
        # The newest sits in the earlier run directory and between two older
        # readings, so neither file nor list order decides.
        self.metered([snapshot(0.10, at=NOW - timedelta(hours=3)),
                      snapshot(0.50, at=NOW - timedelta(minutes=30)),
                      snapshot(0.20, at=NOW - timedelta(hours=2))])
        self.metered([snapshot(0.05, at=NOW - timedelta(hours=1))],
                     stamp="20261020T120000Z", suffix="d4e5f6")
        state = self.state()
        self.assertAlmostEqual(state["account_used"], 0.50, places=12)
        self.assertAlmostEqual(state["pace_room"], 0.30 * 6 / 7, places=12)

    def test_a_newest_snapshot_that_is_unusable_is_not_passed_over(self):
        # A fresh reading that cannot be read never falls back to an older,
        # lower one: the safe direction.
        bad = snapshot(0.50)
        bad["info"]["unifiedWindows"]["seven_day"]["utilization"] = "0.5"
        self.metered([snapshot(0.10, at=NOW - timedelta(hours=3)), bad])
        self.assertEqual(self.state()["pace_room"], 0)

    def test_describe_names_the_pace_numbers_only(self):
        self.metered([snapshot(0.36)])
        line = guard.describe(self.state())
        self.assertIn("; account at 36.0% with 85.7% of its week gone, so pace "
                      "allows 37.7 points", line)
        shutil.rmtree(self.tree / "routine-results")
        self.assertTrue(guard.describe(self.state()).endswith(
            "; no recent account reading, so no pace room"))


class ImproveCoverageTests(unittest.TestCase):
    """2026-10-27: two improve fires were held at 21 points against a 30-point
    cap while the account meter read 5%, and every fire was refused for the
    week. An improve fire that finished before the newest snapshot is already
    in its account-wide utilization, so it is held at no more than that."""

    SNAP = NOW - timedelta(hours=1)

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)

    def metered(self, week=0.05):
        run = self.tree / "routine-results" / "20261020T100000Z-a1b2c3"
        path = run / "k" / "20261020T100000Z" / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True)
        block = usage_block(0.02)
        block["measured"]["meter"] = {"snapshots": [snapshot(week, at=self.SNAP)],
                                      "omitted": 0}
        path.write_text(json.dumps({"arm": "x", "usage": block}), encoding="utf-8")

    def state(self, started, count=None):
        return guard.account(self.tree, LIMITS, NOW, (len(started),) * 2,
                             (len(started) if count is None else count,) * 2,
                             since=NOW - guard.WINDOW, improve_started=started)

    def test_a_covered_improve_fire_is_held_at_the_meter_reading(self):
        self.metered(0.05)
        charge = guard.improve_charge(self.tree, LIMITS)
        self.assertGreater(charge, 0.05)
        started = [self.SNAP - guard.IMPROVE_MAX_RUN,  # exactly covered
                   self.SNAP - timedelta(days=2)]
        state = self.state(started)
        self.assertEqual(state["improve_covered"], 2)
        # The reading less the metered run's own 2-point delta, already in spent.
        self.assertAlmostEqual(state["pending"], 0.03, places=12)
        self.assertIn("2 of them finished before the account reading, so held "
                      "at most at its 5.0 points less 2.0 already charged to "
                      "eval runs inside it", guard.describe(state))

    def test_covered_fires_are_still_charged_when_the_reading_is_larger(self):
        self.metered(0.9)
        charge = guard.improve_charge(self.tree, LIMITS)
        state = self.state([self.SNAP - timedelta(hours=5)])
        self.assertAlmostEqual(state["pending"], charge, places=12)

    def test_an_improve_fire_newer_than_the_snapshot_keeps_the_full_charge(self):
        self.metered(0.05)
        charge = guard.improve_charge(self.tree, LIMITS)
        newer = self.SNAP - guard.IMPROVE_MAX_RUN + timedelta(seconds=1)
        state = self.state([newer, self.SNAP - timedelta(days=1)])
        self.assertEqual(state["improve_covered"], 1)
        self.assertAlmostEqual(state["pending"], charge + 0.03, places=12)

    def add_run(self, stamp, delta, snapshot_at):
        run = self.tree / "routine-results" / f"{stamp}-d4e5f6"
        path = run / "k" / stamp / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True)
        block = usage_block(delta)
        block["measured"]["meter"] = {"snapshots": [snapshot(0.01, at=snapshot_at)],
                                      "omitted": 0}
        path.write_text(json.dumps({"arm": "x", "usage": block}), encoding="utf-8")

    def test_eval_spend_inside_the_reading_is_not_counted_twice(self):
        """2026-10-28: the reading held every eval run's use, and spent
        charged it again, so a guidance-improve fire was refused at 28%
        account use against a 55-point cap."""
        self.metered(0.28)
        self.add_run("20261020T110000Z", 0.06, self.SNAP - timedelta(hours=2))
        state = self.state([self.SNAP - timedelta(days=1)] * 6)
        self.assertAlmostEqual(state["eval_inside"], 0.08, places=12)
        self.assertAlmostEqual(state["pending"], 0.20, places=12)
        # Safe: spent plus the hold still covers the whole reading.
        self.assertGreaterEqual(state["spent"] + state["pending"] + 1e-12, 0.28)

    def test_an_unread_run_is_not_subtracted(self):
        self.metered(0.28)
        run = self.tree / "routine-results" / "20261020T110000Z-d4e5f6"
        path = run / "k" / "20261020T110000Z" / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        state = self.state([self.SNAP - timedelta(days=1)])
        self.assertAlmostEqual(state["eval_inside"], 0.02, places=12)

    def test_a_run_without_any_snapshot_is_not_subtracted(self):
        self.metered(0.28)
        run = self.tree / "routine-results" / "20261020T110000Z-d4e5f6"
        path = run / "k" / "20261020T110000Z" / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"arm": "x", "usage": usage_block(0.06)}),
                        encoding="utf-8")
        state = self.state([self.SNAP - timedelta(days=1)])
        self.assertAlmostEqual(state["eval_inside"], 0.02, places=12)

    def test_the_hold_never_goes_below_zero(self):
        self.metered(0.01)
        self.add_run("20261020T110000Z", 0.06, self.SNAP - timedelta(hours=2))
        state = self.state([self.SNAP - timedelta(days=1)])
        self.assertEqual(state["pending"], 0.0)

    def test_no_usable_snapshot_keeps_the_full_charge(self):
        charge = guard.improve_charge(self.tree, LIMITS)
        state = self.state([NOW - timedelta(days=2)] * 2)
        self.assertEqual((state["improve_covered"], state["account_used"]), (0, None))
        self.assertAlmostEqual(state["pending"], 2 * charge, places=12)
        self.assertNotIn("finished before", guard.describe(state))

    def test_without_start_times_nothing_changes(self):
        self.metered(0.05)
        charge = guard.improve_charge(self.tree, LIMITS)
        state = guard.account(self.tree, LIMITS, NOW, (2, 2), (2, 2),
                              since=NOW - guard.WINDOW)
        self.assertAlmostEqual(state["pending"], 2 * charge, places=12)

    def test_covered_never_exceeds_the_improve_count(self):
        self.metered(0.05)
        state = self.state([self.SNAP - timedelta(days=1)] * 3, count=1)
        self.assertEqual(state["improve_covered"], 1)

    def test_start_times_that_are_not_aware_datetimes_fail_closed(self):
        self.metered(0.05)
        for bad in (["2026-10-19T00:00:00Z"], [datetime(2026, 10, 19)], [None], "x"):
            with self.subTest(bad=bad):
                with self.assertRaises(guard.GuardError):
                    self.state(bad, count=1)
        budget = self.tree / "budget.yml"
        budget.write_text(VALID_BUDGET, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = guard.main(["check", "--results-tree", str(self.tree),
                               "--budget", str(budget)], now=NOW,
                              fire_count=lambda now: (1, 1),
                              improve_count=lambda now: (1, 1),
                              improve_starts=lambda now: ["not a datetime"])
        self.assertEqual(code, 1)
        self.assertIn("refusing", out.getvalue())

    def test_main_releases_room_a_covered_improve_fire_would_have_held(self):
        self.metered(0.05)
        budget = self.tree / "budget.yml"
        budget.write_text(VALID_BUDGET, encoding="utf-8")

        def run(starts):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = guard.main(["check", "--results-tree", str(self.tree),
                                   "--budget", str(budget), "--mode", "improve"],
                                  now=NOW, fire_count=lambda now: (2, 0),
                                  improve_count=lambda now: (2, 0),
                                  improve_starts=starts)
            return code
        # Two charges alone are over the 30-point cap; covered, they are 5.
        self.assertEqual(run(lambda now: [self.SNAP - timedelta(days=1)] * 2), 0)
        self.assertEqual(run(None), 1)

    def test_improve_starts_reads_successful_improve_fires_in_the_window(self):
        def row(n, title, when, conclusion="success"):
            stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"id": n, "display_title": title, "status": "completed",
                    "conclusion": conclusion, "created_at": stamp,
                    "run_started_at": stamp, "updated_at": stamp}
        t = NOW - timedelta(hours=3)
        runs = [row(1, "Fire eval routine: improve a ", t),
                row(2, "Fire eval routine: improve a ", t, "failure"),
                row(3, "Fire eval routine: eval evals/x m", t),
                row(4, "Fire eval routine: improve a ", NOW - timedelta(days=9))]
        starts = guard.improve_starts(runs, NOW)
        self.assertEqual(starts, [t.replace(microsecond=0)])


class RefreshFireTests(unittest.TestCase):
    """2026-10-27: two refused improve fires that finished after the newest
    snapshot held 21 points in full; with 3.1 points of room against a
    3.5-point reservation no eval fire could go, so no newer snapshot could
    come. One eval fire may go to bring it."""

    SNAP = NOW - timedelta(hours=6)

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        run = self.tree / "routine-results" / "20261020T100000Z-a1b2c3"
        path = run / "k" / "20261020T100000Z" / "with_skill" / "summary.json"
        path.parent.mkdir(parents=True)
        block = usage_block(0.02)
        block["measured"]["meter"] = {"snapshots": [snapshot(0.05, at=self.SNAP)],
                                      "omitted": 0}
        path.write_text(json.dumps({"arm": "x", "usage": block}), encoding="utf-8")
        self.charge = guard.improve_charge(self.tree, LIMITS)

    def state(self, started, fires=None, recent=0):
        n = len(started)
        return guard.account(self.tree, LIMITS, NOW,
                             (n + (fires or 0), recent), (n, 0),
                             since=NOW - guard.WINDOW, improve_started=started)

    def blocked(self):
        # Enough finished-but-uncovered fires that no eval fire fits.
        count = int(guard.room(self.state([])) // self.charge) + 1
        return [NOW - guard.IMPROVE_MAX_RUN] * count

    def test_finished_uncovered_fires_allow_one_refresh_eval_fire(self):
        state = self.state(self.blocked())
        self.assertEqual(guard.fits(state), 0)
        self.assertEqual(state["improve_finished"], len(self.blocked()))
        self.assertTrue(guard.refresh(state))
        self.assertTrue(guard.check(state, "eval"))
        self.assertIn("finished after it, held in full", guard.describe(state))

    def test_the_refresh_never_admits_an_improve_scaffold_or_draft_fire(self):
        state = self.state(self.blocked())
        for mode in ("improve", "guidance-improve", "scaffold", "draft"):
            with self.subTest(mode=mode):
                self.assertFalse(guard.check(state, mode))

    def test_a_recent_eval_fire_without_results_stops_another_refresh(self):
        state = self.state(self.blocked(), fires=2, recent=1)  # one is the ingested run
        self.assertEqual(state["recent"], 1)
        self.assertFalse(guard.check(state, "eval"))

    def test_improve_fires_still_running_give_no_refresh(self):
        running = [NOW - guard.IMPROVE_MAX_RUN + timedelta(seconds=1)] * len(self.blocked())
        state = self.state(running)
        self.assertEqual(state["improve_finished"], 0)
        self.assertFalse(guard.check(state, "eval"))

    def test_covered_fires_are_not_counted_as_finished(self):
        state = self.state([self.SNAP - guard.IMPROVE_MAX_RUN] * 3)
        self.assertEqual((state["improve_covered"], state["improve_finished"]), (3, 0))

    def test_no_refresh_when_spend_alone_leaves_no_room(self):
        state = self.state(self.blocked())
        state["spent"] = state["cap"]
        state["pace_room"] = 0.0
        self.assertFalse(guard.check(state, "eval"))

    def test_main_names_the_refresh_fire(self):
        budget = self.tree / "budget.yml"
        budget.write_text(VALID_BUDGET, encoding="utf-8")
        starts = self.blocked()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = guard.main(["check", "--results-tree", str(self.tree),
                               "--budget", str(budget)], now=NOW,
                              fire_count=lambda now: (len(starts), 0),
                              improve_count=lambda now: (len(starts), 0),
                              improve_starts=lambda now: starts)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("allowed as the one refresh fire", out.getvalue())


# ---------------------------------------------------------------------------
# The workflows run it

def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):

    RESULTS_CHECKOUT = {"ref": "persistent/eval-results", "path": "eval-results-branch",
                        "persist-credentials": False, "sparse-checkout": "routine-results"}

    def assert_results_checkout(self, step, workflow: str) -> None:
        self.assertTrue(step["uses"].startswith("actions/checkout@"))
        self.assertIsNone(lock_problem(step["uses"], workflow))
        self.assertEqual(step["with"], self.RESULTS_CHECKOUT)

    def test_the_fire_workflow_checks_the_allowance_before_it_fires(self):
        doc = load("routine-eval-fire.yml")
        self.assertEqual(doc["permissions"], {"contents": "read", "actions": "read"})
        steps = doc["jobs"]["fire"]["steps"]
        names = [s.get("name") for s in steps]
        ids = [s.get("id") for s in steps]
        at = ids.index("allowance")
        step = steps[at]
        # Every mode: no `if:`; the job token only, never the bearer.
        self.assertNotIn("if", step)
        self.assertEqual(step["env"], {"GH_TOKEN": "${{ github.token }}",
                                       "MODE": "${{ steps.validate.outputs.mode }}"})
        self.assertEqual(step["run"], "python3 scripts/allowance_guard.py check "
                                      "--results-tree eval-results-branch --mode \"$MODE\"")
        self.assertLess(ids.index("validate"), at)
        self.assertLess(at, ids.index("snapshot"))
        self.assertLess(at, names.index("Fire the eval routine"))
        checkout = names.index("Check out persistent/eval-results (the ingested runs)")
        self.assertLess(checkout, at)
        self.assert_results_checkout(steps[checkout], "routine-eval-fire.yml")
        # The SHA this file already pins for its default-branch checkout.
        self.assertEqual(steps[checkout]["uses"], steps[0]["uses"])

    def test_the_weekly_plan_reads_the_results_and_skips_an_empty_dispatch(self):
        jobs = load("routine-eval-weekly.yml")["jobs"]
        self.assertEqual(jobs["plan"]["permissions"]["actions"], "read")
        steps = jobs["plan"]["steps"]
        ids = [s.get("id") for s in steps]
        names = [s.get("name") for s in steps]
        checkout = names.index("Check out persistent/eval-results (the ingested runs)")
        self.assertLess(checkout, ids.index("plan"))
        # The whole branch: the planner reads the usage census beside the runs.
        self.assertEqual(steps[checkout]["with"],
                         {"ref": "persistent/eval-results", "path": "eval-results-branch",
                          "persist-credentials": False})
        self.assertEqual(steps[checkout]["uses"], steps[0]["uses"])
        plan = steps[ids.index("plan")]
        self.assertIn("--results-tree eval-results-branch", plan["run"])
        self.assertEqual(plan["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(jobs["plan"]["outputs"]["count"], "${{ steps.plan.outputs.count }}")
        self.assertTrue(jobs["dispatch"]["if"].endswith(
            "&& needs.plan.outputs.count != '0'"))

    def test_the_docs_describe_the_three_layers(self):
        text = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")
        self.assertIn("\n### The weekly allowance guard\n", text)
        for name in ("evals/budget.yml", "scripts/allowance_guard.py",
                     "scripts/local_eval.py"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()


class WeekWindowTests(unittest.TestCase):
    """The cap's window is the account's allowance week (the owner, 2026-10-26),
    a fixed instant every seven days, so daylight saving does not move it."""

    def at(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_the_window_starts_at_the_last_reset(self):
        cases = {
            self.at(2026, 10, 26, 17, 0): self.at(2026, 10, 26, 17, 0),
            self.at(2026, 10, 26, 16, 59, 59): self.at(2026, 10, 19, 17, 0),
            self.at(2026, 10, 20, 15, 30): self.at(2026, 10, 19, 17, 0),
            # After US clocks fall back on 2026-11-01 the reset reads 12 PM
            # Eastern, and is the same UTC instant.
            self.at(2026, 11, 3, 9, 0): self.at(2026, 11, 2, 17, 0),
            # Before the anchor, whole weeks back from it.
            self.at(2026, 9, 1, 0, 0): self.at(2026, 8, 31, 17, 0),
        }
        for now, start in cases.items():
            with self.subTest(now=now.isoformat()):
                self.assertEqual(guard.window_start(now), start)

    def test_the_window_is_never_longer_than_seven_days(self):
        now = self.at(2026, 10, 26, 16, 59, 59)
        self.assertLessEqual(now - guard.window_start(now), guard.WINDOW)
        self.assertEqual(guard.window_start(now).weekday(), 0)

    def test_the_default_window_is_the_accounts_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            for stamp in ("20261019T165900Z", "20261019T170000Z"):
                (tree / "routine-results" / f"{stamp}-ab12cd").mkdir(parents=True)
            runs = guard.ingested_runs(tree, NOW, guard.window_start(NOW))
            self.assertEqual([r.name for r in runs], ["20261019T170000Z-ab12cd"])

