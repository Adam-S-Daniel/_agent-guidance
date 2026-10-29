"""Per-model token counts, a cross-model flag and an explicit effort level.

An agent arm can start subagents on other models, so a trial's work may be
done mostly by a model other than the arm's own `--model`. Every trial
summary carries `model_tokens` (the four token counts per model, from the CLI
result's `modelUsage`) and `cross_model` (whether that accounting is
complete, and the share of its tokens spent on other models, flagged above
`CROSS_MODEL_SHARE_THRESHOLD`); an arm's aggregate carries both per arm. The
improvement loop's `tokens` are the total across every model (ADR 0005,
2026-10-07 addendum), and a record says which basis it measured.

Every agent arm can also be launched with an explicit `--effort` (the run's
flag, else the fixture's `effort:`), on its first and every follow-up call;
the effective level, or null for the CLI's default, is in every summary.

Expected values are written out as literals, not computed by the code under
test. Hermetic: `subprocess.run` is mocked, or the CLI is an in-test
stand-in script with canned JSON; no real CLI, no network, no clock.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
ROOT = TEST_DIR.parent
HARNESS_DIR = ROOT / "harness"
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

import guidance  # noqa: E402
import improve_gate  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import local_eval  # noqa: E402
import propose_skill_edit as pse  # noqa: E402
import run_eval  # noqa: E402

OWN = "claude-sonnet-5"
SUBAGENT = "claude-opus-4-8"
REGISTRY_URL = "https://github.com/example-adopter/acsi-agentskills"


def usage_entry(inp, out, read, write, canonical=None) -> dict:
    """One `modelUsage` entry in the CLI's own shape (camelCase counts plus
    keys `model_usage` does not keep)."""
    entry = {"inputTokens": inp, "outputTokens": out,
             "cacheReadInputTokens": read, "cacheCreationInputTokens": write,
             "webSearchRequests": 0, "costUSD": 0.01,
             "contextWindow": 200000, "maxOutputTokens": 32000}
    if canonical is not None:
        entry["canonicalModel"] = canonical
    return entry


#: The parent plus a subagent on another model that did most of the work:
#: the parent spent 100 tokens, the subagent 900.
MULTI_MODEL_USAGE = {OWN: usage_entry(10, 20, 50, 20, OWN),
                     SUBAGENT: usage_entry(100, 200, 500, 100, SUBAGENT)}

#: `model_tokens` for MULTI_MODEL_USAGE, written out.
MULTI_MODEL_TOKENS = {
    SUBAGENT: {"input_tokens": 100, "output_tokens": 200,
               "cache_read_input_tokens": 500,
               "cache_creation_input_tokens": 100,
               "canonical_model": SUBAGENT},
    OWN: {"input_tokens": 10, "output_tokens": 20,
          "cache_read_input_tokens": 50, "cache_creation_input_tokens": 20,
          "canonical_model": OWN}}

#: `cross_model` for MULTI_MODEL_USAGE under `--model claude-sonnet-5`.
MULTI_MODEL_CROSS = {"model": OWN, "canonical_model": OWN, "complete": True,
                     "dropped": 0, "other_share": 0.9, "threshold": 0.5,
                     "flagged": True}


def agent_reply(model_usage=None, session="sess-1") -> dict:
    verdict = json.dumps({"dimensions": [{"name": "d", "score": 5,
                                          "rationale": "r"}], "overall": 5})
    return {"type": "result", "is_error": False, "result": verdict,
            "total_cost_usd": 0.0, "num_turns": 1, "duration_ms": 1,
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_creation_input_tokens": 20,
                      "cache_read_input_tokens": 50},
            "modelUsage": MULTI_MODEL_USAGE if model_usage is None else model_usage,
            "session_id": session}


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class Recorder:
    """Stands in for subprocess.run and records every argv."""

    def __init__(self, stdout: str):
        self.stdout = stdout
        self.argvs: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.argvs.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


def effort_values(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "--effort"]


def usage_of(usage) -> dict:
    return run_eval.model_usage({"modelUsage": usage})


def cross_of(usage, own=OWN) -> dict:
    found = usage_of(usage)
    return run_eval.cross_model(found["tokens"], own, complete=found["complete"],
                                dropped=found["dropped"])


# ---------------------------------------------------------------------------
# model_usage

class ModelUsageTests(unittest.TestCase):

    def test_every_model_gets_its_four_counts_and_canonical_model(self):
        self.assertEqual(usage_of(MULTI_MODEL_USAGE),
                         {"tokens": MULTI_MODEL_TOKENS, "dropped": 0,
                          "complete": True})
        self.assertEqual(run_eval.model_tokens({"modelUsage": MULTI_MODEL_USAGE}),
                         MULTI_MODEL_TOKENS)

    def test_a_missing_invalid_or_oversized_count_is_null_never_zero(self):
        entry = usage_entry(True, -1, 1e308, 4)
        del entry["costUSD"]
        entry["cacheReadInputTokens"] = 10 ** 12 + 1
        del entry["outputTokens"]
        self.assertEqual(usage_of({OWN: entry})["tokens"], {OWN: {
            "input_tokens": None, "output_tokens": None,
            "cache_read_input_tokens": None, "cache_creation_input_tokens": 4,
            "canonical_model": None}})

    def test_a_dropped_entry_makes_the_accounting_incomplete(self):
        found = usage_of({OWN: usage_entry(1, 1, 1, 1), "has space": {},
                          SUBAGENT: "not an entry"})
        self.assertEqual(found, {"tokens": {OWN: {
            "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1,
            "cache_creation_input_tokens": 1, "canonical_model": None}},
            "dropped": 2, "complete": False})

    def test_more_than_sixteen_models_is_incomplete(self):
        usage = {f"claude-m-{i:02d}": usage_entry(1, 0, 0, 0) for i in range(17)}
        found = usage_of(usage)
        self.assertEqual(len(found["tokens"]), 16)
        self.assertNotIn("claude-m-16", found["tokens"])
        self.assertEqual((found["dropped"], found["complete"]), (1, False))

    def test_absent_or_malformed_model_usage_gives_nothing(self):
        for result in (None, {}, {"modelUsage": None}, {"modelUsage": []},
                       {"modelUsage": {}}):
            with self.subTest(result=result):
                self.assertEqual(run_eval.model_usage(result),
                                 {"tokens": {}, "dropped": 0, "complete": False})


class CanonicalModelIdTests(unittest.TestCase):

    def test_spellings_resolve_and_aliases_do_not(self):
        cases = {
            "claude-sonnet-5": "claude-sonnet-5",
            "claude-sonnet-5[1m]": "claude-sonnet-5",
            "claude-haiku-4-5-20251001": "claude-haiku-4-5",
            "us.anthropic.claude-sonnet-5-v1:0": "claude-sonnet-5",
            # Bedrock suffix first, then the date: the other order leaves
            # the date on.
            "us.anthropic.claude-haiku-4-5-20251001-v1:0": "claude-haiku-4-5",
            "anthropic.claude-opus-4-8-v1": "claude-opus-4-8",
            "claude-opus-4-8@20260101": "claude-opus-4-8",
            "sonnet": None, "opus[1m]": None, "gpt-4": None, "": None,
            None: None, 7: None,
        }
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(run_eval.canonical_model_id(model), expected)


class CrossModelTests(unittest.TestCase):

    def test_a_subagent_doing_most_of_the_work_is_flagged(self):
        self.assertEqual(cross_of(MULTI_MODEL_USAGE), MULTI_MODEL_CROSS)

    def test_a_parent_doing_most_of_the_work_is_not_flagged(self):
        block = cross_of({OWN: usage_entry(100, 200, 500, 100),
                          SUBAGENT: usage_entry(10, 20, 50, 20)})
        self.assertAlmostEqual(block["other_share"], 0.1)
        self.assertIs(block["flagged"], False)

    def test_the_threshold_itself_is_not_flagged(self):
        self.assertEqual(run_eval.CROSS_MODEL_SHARE_THRESHOLD, 0.5)
        block = cross_of({OWN: usage_entry(50, 0, 0, 0),
                          SUBAGENT: usage_entry(50, 0, 0, 0)})
        self.assertEqual((block["other_share"], block["flagged"]), (0.5, False))

    def test_the_flag_is_exact_at_the_threshold(self):
        just_over = cross_of({OWN: usage_entry(499999999999, 0, 0, 0),
                              SUBAGENT: usage_entry(500000000001, 0, 0, 0)})
        self.assertIs(just_over["flagged"], True)
        half = cross_of({OWN: usage_entry(500000000000, 0, 0, 0),
                         SUBAGENT: usage_entry(500000000000, 0, 0, 0)})
        self.assertEqual((half["other_share"], half["flagged"]), (0.5, False))

    def test_spellings_of_the_arms_model_are_the_arm_itself(self):
        cases = [
            ("dated snapshot", {"claude-haiku-4-5-20251001": usage_entry(900, 0, 0, 0),
                                SUBAGENT: usage_entry(100, 0, 0, 0)},
             "claude-haiku-4-5"),
            ("dated arm id", {"claude-haiku-4-5": usage_entry(900, 0, 0, 0),
                              SUBAGENT: usage_entry(100, 0, 0, 0)},
             "claude-haiku-4-5-20251001"),
            ("1m variant key", {"claude-sonnet-5[1m]": usage_entry(900, 0, 0, 0),
                                SUBAGENT: usage_entry(100, 0, 0, 0)}, OWN),
            ("provider key with canonicalModel",
             {"us.anthropic.claude-sonnet-5-v1:0": usage_entry(900, 0, 0, 0, OWN),
              SUBAGENT: usage_entry(100, 0, 0, 0)}, OWN),
            # A key no spelling rule resolves: only canonicalModel names it.
            ("opaque key with canonicalModel",
             {"custom-deployment-7": usage_entry(900, 0, 0, 0, OWN),
              SUBAGENT: usage_entry(100, 0, 0, 0)}, OWN),
            ("provider arm id", {OWN: usage_entry(900, 0, 0, 0),
                                 SUBAGENT: usage_entry(100, 0, 0, 0)},
             "us.anthropic.claude-sonnet-5-v1:0"),
        ]
        for label, usage, own in cases:
            with self.subTest(case=label):
                block = cross_of(usage, own)
                self.assertAlmostEqual(block["other_share"], 0.1)
                self.assertIs(block["flagged"], False)

    def test_a_longer_id_is_another_model(self):
        block = cross_of({"claude-haiku-4-5-1": usage_entry(900, 0, 0, 0)},
                         "claude-haiku-4-5")
        self.assertEqual((block["other_share"], block["flagged"]), (1.0, True))

    def test_an_unresolvable_model_is_unknown_never_a_guess(self):
        cases = [
            # `--model sonnet` and usage keyed by the full id: the alias
            # cannot be resolved here, so the share is unknown, not 100%.
            ("alias arm", {OWN: usage_entry(100, 0, 0, 0)}, "sonnet"),
            ("no arm model", MULTI_MODEL_USAGE, None),
            ("alias key without canonicalModel", {"sonnet": usage_entry(1, 0, 0, 0)},
             OWN),
        ]
        for label, usage, own in cases:
            with self.subTest(case=label):
                block = cross_of(usage, own)
                self.assertIsNone(block["other_share"])
                self.assertIsNone(block["flagged"])
        self.assertIsNone(cross_of({OWN: usage_entry(1, 0, 0, 0)},
                                   "sonnet")["canonical_model"])

    def test_dropped_entries_make_the_share_unknown(self):
        # A valid parent and a malformed subagent entry: not 0% other.
        for label, usage in (
                ("malformed entry", {OWN: usage_entry(100, 0, 0, 0),
                                     SUBAGENT: "not an entry"}),
                ("invalid key", {OWN: usage_entry(100, 0, 0, 0),
                                 "has space": usage_entry(900, 0, 0, 0)}),
                ("truncated", {OWN: usage_entry(100, 0, 0, 0),
                               **{f"claude-z-{i:02d}": usage_entry(9, 0, 0, 0)
                                  for i in range(16)}})):
            with self.subTest(case=label):
                block = cross_of(usage)
                self.assertEqual((block["complete"], block["dropped"] > 0,
                                  block["other_share"], block["flagged"]),
                                 (False, True, None, None))

    def test_a_missing_or_oversized_count_makes_the_share_unknown(self):
        for entry in (usage_entry(1, 1, 1, None), usage_entry(1e308, 1, 1, 1),
                      usage_entry(10 ** 12, 10 ** 12, 10 ** 12, 10 ** 12)):
            with self.subTest(entry=entry):
                block = cross_of({OWN: usage_entry(1, 1, 1, 1), SUBAGENT: entry})
                self.assertIsNone(block["other_share"])
                self.assertIsNone(block["flagged"])

    def test_no_nan_or_infinity_reaches_a_summary(self):
        huge = {f"claude-h-{i:02d}": usage_entry(1e308, 1e308, 1e308, 1e308)
                for i in range(8)}
        huge.update({f"claude-k-{i:02d}": usage_entry(10 ** 12, 10 ** 12,
                                                      10 ** 12, 10 ** 12)
                     for i in range(8)})
        with tempfile.TemporaryDirectory() as tmp:
            arm_dir = Path(tmp) / "arm"
            trials = []
            for k in (1, 2):
                run_eval._write_summary(Path(tmp), "s", "with_skill", "T", None,
                                        None, None, None, None,
                                        arm_dir=arm_dir / f"t{k}",
                                        agent_usage=usage_of(huge),
                                        agent_model="claude-k-00")
                trials.append(json.loads((arm_dir / f"t{k}" / "summary.json")
                                         .read_text(encoding="utf-8")))
            stats = run_eval.aggregate_trials(trials)
        summary = trials[0]
        json.dumps(summary, allow_nan=False)
        json.dumps(stats, allow_nan=False)
        self.assertIsNone(summary["model_tokens"]["claude-h-00"]["input_tokens"])
        self.assertEqual(summary["model_tokens"]["claude-k-00"]["input_tokens"],
                         10 ** 12)
        self.assertIsNone(summary["cross_model"]["other_share"])
        self.assertIsNone(run_eval.model_usage_total(summary["model_tokens"], True))


class AggregateTests(unittest.TestCase):

    def trial(self, usage, own=OWN):
        found = usage_of(usage)
        return {"error": None, "agent": {"cost_usd": 0.0},
                "model_tokens": found["tokens"],
                "cross_model": run_eval.cross_model(
                    found["tokens"], own, complete=found["complete"],
                    dropped=found["dropped"])}

    def test_per_model_stats_and_flagged_trials_per_arm(self):
        trials = [self.trial(MULTI_MODEL_USAGE),
                  self.trial({OWN: usage_entry(100, 0, 0, 0)}),
                  {"error": {"type": "timeout", "detail": "x"}, "agent": None}]
        stats = run_eval.aggregate_trials(trials)["aggregate"]
        # The parent-only trial (complete) used none of the subagent model;
        # the errored trial reported nothing and is missing.
        self.assertEqual(stats["model_tokens"][SUBAGENT]["input_tokens"],
                         {"n": 2, "n_missing": 1, "mean": 50, "median": 50.0,
                          "min": 0, "max": 100, "sum": 100})
        self.assertEqual(stats["model_tokens"][OWN]["input_tokens"]["sum"], 110)
        # The errored trial's tokens are unknown, so the arm's share is too.
        self.assertEqual(stats["cross_model"], {
            "model": OWN, "threshold": 0.5, "n": 2, "n_unknown": 1,
            "flagged_trials": 1, "other_share": None, "flagged": None})
        known = run_eval.aggregate_trials(trials[:2])["aggregate"]["cross_model"]
        self.assertAlmostEqual(known.pop("other_share"), 900 / 1100)
        self.assertEqual(known, {"model": OWN, "threshold": 0.5, "n": 2,
                                 "n_unknown": 0, "flagged_trials": 1,
                                 "flagged": True})
        self.assertEqual(run_eval.sum_model_usage(trials), {
            "tokens": {
                SUBAGENT: {"input_tokens": 100, "output_tokens": 200,
                           "cache_read_input_tokens": 500,
                           "cache_creation_input_tokens": 100,
                           "canonical_model": SUBAGENT},
                OWN: {"input_tokens": 110, "output_tokens": 20,
                      "cache_read_input_tokens": 50,
                      "cache_creation_input_tokens": 20,
                      "canonical_model": None}},
            "dropped": 0, "complete": False})
        self.assertIs(run_eval.sum_model_usage(trials[:2])["complete"], True)

    def test_a_trial_whose_entries_were_all_dropped_is_unknown(self):
        all_dropped = self.trial({"has space": usage_entry(900, 0, 0, 0),
                                  SUBAGENT: "not an entry"})
        self.assertEqual((all_dropped["model_tokens"],
                          all_dropped["cross_model"]["dropped"]), ({}, 2))
        trials = [self.trial(MULTI_MODEL_USAGE), all_dropped]
        self.assertEqual(run_eval.sum_model_usage(trials)["complete"], False)
        self.assertEqual(run_eval.sum_model_usage(trials)["dropped"], 2)
        self.assertEqual(run_eval.aggregate_trials(trials)["aggregate"]["cross_model"],
                         {"model": OWN, "threshold": 0.5, "n": 1, "n_unknown": 1,
                          "flagged_trials": 1, "other_share": None,
                          "flagged": None})

    def test_a_model_an_incomplete_trial_does_not_show_is_missing_not_zero(self):
        incomplete = self.trial({OWN: usage_entry(100, 0, 0, 0),
                                 SUBAGENT: "not an entry"})
        trials = [self.trial(MULTI_MODEL_USAGE), incomplete]
        stats = run_eval.aggregate_trials(trials)["aggregate"]
        sub = stats["model_tokens"][SUBAGENT]["input_tokens"]
        self.assertEqual((sub["n"], sub["n_missing"], sub["sum"]), (1, 1, 100))
        self.assertEqual(stats["cross_model"]["n"], 1)
        self.assertEqual(stats["cross_model"]["n_unknown"], 1)
        self.assertIsNone(stats["cross_model"]["other_share"])
        self.assertIsNone(stats["cross_model"]["flagged"])
        summed = run_eval.sum_model_usage(trials)
        self.assertIsNone(summed["tokens"][SUBAGENT]["input_tokens"])
        self.assertEqual(summed["tokens"][OWN]["input_tokens"], 110)
        self.assertEqual((summed["dropped"], summed["complete"]), (1, False))

    def test_old_summaries_without_the_fields_aggregate_to_nothing(self):
        trials = [{"error": None, "agent": {"cost_usd": 0.1}}]
        stats = run_eval.aggregate_trials(trials)["aggregate"]
        self.assertEqual(stats["model_tokens"], {})
        self.assertEqual(stats["cross_model"], {
            "model": None, "threshold": 0.5, "n": 0, "n_unknown": 1,
            "flagged_trials": 0, "other_share": None, "flagged": None})
        self.assertEqual(run_eval.sum_model_usage(trials),
                         {"tokens": {}, "dropped": 0, "complete": False})


# ---------------------------------------------------------------------------
# --effort on the agent's command

class RunAgentEffortTests(unittest.TestCase):

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="workspace-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)
        # A multi-turn arm persists its session under HOME; keep it scratch.
        self.home = Path(tempfile.mkdtemp(prefix="home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def launch(self, **arm) -> Recorder:
        cli = Recorder(json.dumps(agent_reply()))
        arm = {"name": "without_skill", "timeout": 30, "model": OWN, **arm}
        with mock.patch.object(run_eval.subprocess, "run", cli), \
             mock.patch.dict(run_eval.os.environ,
                             {"CLAUDE_BIN": "fake-claude", "HOME": str(self.home),
                              "XDG_STATE_HOME": str(self.home / "state")}):
            out = run_eval.run_agent(self.workspace, "Do the task.", arm)
        self.assertNotIn("error", out, out)
        return cli

    def test_effort_reaches_the_first_and_every_followup_call(self):
        cli = self.launch(effort="high", followups=["go on", "and finish"])
        self.assertEqual(len(cli.argvs), 3)
        for argv in cli.argvs:
            self.assertEqual(effort_values(argv), ["high"])
        self.assertIn("--resume", cli.argvs[1])
        self.assertEqual(cli.argvs[1][2], "go on")

    def test_no_effort_flag_when_unset(self):
        for arm in ({}, {"effort": None}, {"followups": ["go on"]}):
            with self.subTest(arm=arm):
                for argv in self.launch(**arm).argvs:
                    self.assertNotIn("--effort", argv)

    def test_every_listed_level_is_passed_through(self):
        self.assertEqual(guidance.EFFORT_LEVELS,
                         ("low", "medium", "high", "xhigh", "max"))
        for level in guidance.EFFORT_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(effort_values(self.launch(effort=level).argvs[0]),
                                 [level])

    def test_an_unknown_level_is_refused_before_any_spawn(self):
        cli = Recorder("{}")
        with mock.patch.object(run_eval.subprocess, "run", cli):
            for level in ("High", "", "ultra", 3, "high --model x"):
                with self.subTest(level=level), \
                     self.assertRaises(guidance.GuidanceError):
                    run_eval.run_agent(self.workspace, "x", {
                        "name": "without_skill", "timeout": 30,
                        "effort": level})
        self.assertEqual(cli.argvs, [])


# ---------------------------------------------------------------------------
# End to end: run_eval.py against a stand-in CLI, both skill arms

def write_cli(path: Path, log: Path, reply: dict) -> Path:
    """A stand-in CLI: answers `--version`, logs every other argv to `log`
    (an absolute path baked in, since an arm's environment is an allowlist),
    and prints `reply`, which parses as both a transcript and a verdict."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('fake-claude 0.0.0')\n"
        "    sys.exit(0)\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({json.dumps(reply)!r})\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def ingest_parts(summary_path: Path, results: Path) -> dict:
    """ingest_routine_results' own reading of a summary's path."""
    return ingest.parse_result_path(summary_path.relative_to(results).as_posix())


class EndToEndTests(unittest.TestCase):

    TS = "20261007T000000Z"
    SKILL = "tokens-probe"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "argv.log"
        self.cli = write_cli(self.tmp / "claude", self.log, agent_reply())
        self.eval_dir = self.tmp / "evals" / self.SKILL
        (self.eval_dir / "seed").mkdir(parents=True)
        (self.eval_dir / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        self.registry = self.tmp / "registry"
        skill_dir = self.registry / "plugins" / "b" / "skills" / self.SKILL
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {self.SKILL}\ndescription: stand-in.\n---\n",
            encoding="utf-8")
        self.results = self.tmp / "results"

    def fixture(self, **extra):
        (self.eval_dir / "fixture.yaml").write_text(yaml.safe_dump({
            "skill": self.SKILL, "registry": REGISTRY_URL, "prompt": "do it",
            "model": OWN, "judge_rubric": "r", "judge": {"model": "fake-judge"},
            "objective_checks": [{"id": "seed-kept", "description": "d",
                                  "type": "files_unchanged",
                                  "paths": ["README.md"]}], **extra}),
            encoding="utf-8")

    def run_eval(self, *flags, arm="both"):
        env = os.environ.copy()
        env["CLAUDE_BIN"] = str(self.cli)
        return subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"),
             str(self.eval_dir), "--arm", arm,
             "--registry", f"acsi-agentskills={self.registry}",
             "--results-dir", str(self.results), "--timeout", "30",
             "--timestamp", self.TS, *flags],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)

    def summary_path(self, arm, rel="summary.json") -> Path:
        return self.results / self.SKILL / self.TS / arm / rel

    def summary(self, arm, rel="summary.json") -> dict:
        return json.loads(self.summary_path(arm, rel).read_text(encoding="utf-8"))

    def ingests(self, arm, rel="summary.json"):
        path = self.summary_path(arm, rel)
        ingest.check_summary(self.summary(arm, rel), str(path.name),
                             ingest_parts(path, self.results))

    def argvs(self) -> list[list[str]]:
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def test_both_skill_arms_carry_per_model_counts_and_the_flag(self):
        self.fixture()
        proc = self.run_eval("--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for arm in ("with_skill", "without_skill"):
            with self.subTest(arm=arm):
                summary = self.summary(arm)
                self.assertEqual(summary["model_tokens"], MULTI_MODEL_TOKENS)
                self.assertEqual(summary["cross_model"], MULTI_MODEL_CROSS)
                self.assertEqual(summary["models_used"], [SUBAGENT, OWN])
                self.assertIsNone(summary["harness"]["effort"])
                json.dumps(summary, allow_nan=False)
                self.ingests(arm)

    def test_an_arm_aggregate_carries_them_per_arm(self):
        self.fixture()
        proc = self.run_eval("--no-judge", "--trials", "2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for arm in ("with_skill", "without_skill"):
            with self.subTest(arm=arm):
                aggregate = self.summary(arm)
                self.assertEqual(aggregate["model_tokens"], {
                    SUBAGENT: {"input_tokens": 200, "output_tokens": 400,
                               "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 200,
                               "canonical_model": SUBAGENT},
                    OWN: {"input_tokens": 20, "output_tokens": 40,
                          "cache_read_input_tokens": 100,
                          "cache_creation_input_tokens": 40,
                          "canonical_model": OWN}})
                self.assertEqual(aggregate["cross_model"], MULTI_MODEL_CROSS)
                block = aggregate["aggregate"]
                self.assertEqual(block["cross_model"], {
                    "model": OWN, "threshold": 0.5, "n": 2, "n_unknown": 0,
                    "flagged_trials": 2, "other_share": 0.9, "flagged": True})
                self.assertEqual(block["model_tokens"][OWN]["input_tokens"],
                                 {"n": 2, "n_missing": 0, "mean": 10,
                                  "median": 10.0, "min": 10, "max": 10,
                                  "sum": 20})
                self.ingests(arm)
                for k in (1, 2):
                    rel = f"{run_eval.TRIAL_DIR_PREFIX}{k}/summary.json"
                    self.assertEqual(self.summary(arm, rel)["cross_model"],
                                     MULTI_MODEL_CROSS)
                    self.ingests(arm, rel)

    def test_no_effort_records_null_and_passes_no_flag(self):
        self.fixture()
        proc = self.run_eval(arm="without_skill")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("effort", self.summary("without_skill")["harness"])
        self.assertIsNone(self.summary("without_skill")["harness"]["effort"])
        argvs = self.argvs()
        self.assertEqual(len(argvs), 2, argvs)  # the agent and the judge
        for argv in argvs:
            self.assertNotIn("--effort", argv)

    def test_the_run_flag_reaches_both_agents_not_the_judge_and_is_recorded(self):
        self.fixture(effort="low")
        proc = self.run_eval("--effort", "max")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for arm in ("with_skill", "without_skill"):
            self.assertEqual(self.summary(arm)["harness"]["effort"], "max")
            self.ingests(arm)
        argvs = self.argvs()
        self.assertEqual(len(argvs), 4, argvs)
        # Agent calls load project settings; the judge loads none.
        def is_agent(argv):
            return argv[argv.index("--setting-sources") + 1] == "project"
        agents = [a for a in argvs if is_agent(a)]
        judges = [a for a in argvs if not is_agent(a)]
        self.assertEqual(len(agents), 2, argvs)
        for argv in agents:
            self.assertEqual(effort_values(argv), ["max"])
        for argv in judges:
            self.assertNotIn("--effort", argv)

    def test_the_fixture_key_applies_without_the_flag(self):
        self.fixture(effort="medium")
        proc = self.run_eval("--no-judge", arm="with_skill")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.summary("with_skill")["harness"]["effort"], "medium")
        self.assertEqual(effort_values(self.argvs()[0]), ["medium"])

    def test_an_unknown_fixture_level_is_a_configuration_error(self):
        self.fixture(effort="ultra")
        proc = self.run_eval("--no-judge")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("`effort:`", proc.stdout)
        self.assertFalse(self.log.exists())

    def test_an_unknown_flag_level_is_an_argparse_error(self):
        self.fixture()
        proc = self.run_eval("--effort", "ultra")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--effort", proc.stderr)
        self.assertFalse(self.log.exists())

    def test_local_eval_refuses_an_unknown_fixture_level(self):
        self.fixture(effort="ultra")
        with self.assertRaisesRegex(local_eval.Refused, "`effort:`"):
            local_eval.load_skill_fixture(self.eval_dir)


class GuidanceArmTests(unittest.TestCase):
    """The guidance subject's arm records the same fields, with delivery,
    guard and the agent call patched."""

    def test_a_guidance_arm_records_effort_and_per_model_tokens(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        args = argparse.Namespace(results_dir=tmp / "results", model=OWN,
                                  timeout=30, no_judge=True,
                                  harness_version=None, effort="xhigh")
        ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
               "guidance_dir": tmp, "row": {}, "section": "s",
               "key": "guidance/s"}
        arm = {"name": "treatment", "mode": "section", "objective_checks": []}
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}
        guard = {"ok": True, "expected": True, "observed": True, "detail": "d"}
        seen = {}

        def fake_run_agent(workspace, prompt, arm_config):
            seen.update(arm_config)
            return {"transcript": "t", "usage": {}, "cost_usd": 0.0,
                    "num_turns": 1, "duration_ms": 1,
                    "raw": {"modelUsage": MULTI_MODEL_USAGE}}

        with mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", return_value=guard), \
             mock.patch.object(run_eval, "run_agent", fake_run_agent):
            run_eval._run_guidance_arm(arm, {"prompt": "do it"}, tmp / "no-seed",
                                       ctx, args, "T")
        self.assertEqual(seen["effort"], "xhigh")
        summary = json.loads((tmp / "results" / "guidance" / "s" / "T"
                              / "treatment" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["harness"]["effort"], "xhigh")
        self.assertEqual(summary["model_tokens"], MULTI_MODEL_TOKENS)
        self.assertEqual(summary["cross_model"], MULTI_MODEL_CROSS)


    def test_an_unpinned_guidance_arm_runs_and_records_the_runs_roster_model(self):
        # 2026-10-28: unpinned guidance runs recorded `cross_model.model`
        # null, and compute_gates dropped every one ("no requested arm
        # model"). The run resolves the model once; the arm uses it.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        args = argparse.Namespace(results_dir=tmp / "results", model=None,
                                  timeout=30, no_judge=True,
                                  harness_version=None, effort=None)
        ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
               "guidance_dir": tmp, "row": {}, "section": "s",
               "key": "guidance/s", "agent_model": OWN}
        arm = {"name": "treatment", "mode": "section", "objective_checks": []}
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}
        guard = {"ok": True, "expected": True, "observed": True, "detail": "d"}
        seen = {}

        def fake_run_agent(workspace, prompt, arm_config):
            seen.update(arm_config)
            return {"transcript": "t", "usage": {}, "cost_usd": 0.0,
                    "num_turns": 1, "duration_ms": 1,
                    "raw": {"modelUsage": MULTI_MODEL_USAGE}}

        with mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", return_value=guard), \
             mock.patch.object(run_eval, "run_agent", fake_run_agent):
            run_eval._run_guidance_arm(arm, {"prompt": "do it"}, tmp / "no-seed",
                                       ctx, args, "T")
        self.assertEqual(seen["model"], OWN)
        summary = json.loads((tmp / "results" / "guidance" / "s" / "T"
                              / "treatment" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["cross_model"]["model"], OWN)

    def test_the_guidance_run_model_is_the_flag_the_pin_or_the_rosters_first_arm(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        roster = tmp / "roster.yml"
        roster.write_text("arms:\n  - id: claude-arm-one\n  - id: claude-arm-two\n"
                          "judge: claude-judge\n", encoding="utf-8")
        flag = argparse.Namespace(model="claude-flag", roster=roster)
        bare = argparse.Namespace(model=None, roster=roster)
        self.assertEqual(run_eval._guidance_agent_model({"model": "claude-pin"}, flag),
                         "claude-flag")
        self.assertEqual(run_eval._guidance_agent_model({"model": "claude-pin"}, bare),
                         "claude-pin")
        self.assertEqual(run_eval._guidance_agent_model({}, bare), "claude-arm-one")
        # No readable roster: the CLI default, as before.
        missing = argparse.Namespace(model=None, roster=tmp / "absent.yml")
        self.assertIsNone(run_eval._guidance_agent_model({}, missing))


# ---------------------------------------------------------------------------
# The improvement loop's tokens: the total across every model

class FixtureMetricsTests(unittest.TestCase):
    """`fixture_metrics`' `tokens` are the total across every model in
    `modelUsage`; the main loop's `usage` is kept as `main_loop_tokens`."""

    SKILL, TS = "demo", "20261007T000000Z"

    def metrics(self, usages):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for k, usage in enumerate(usages, start=1):
                trial = (run_dir / f"t{k}" / self.SKILL / self.TS / "fx"
                         / "with_skill")
                trial.mkdir(parents=True)
                summary = {"error": None, "trial": k,
                           "agent": {"cost_usd": 0.1, "usage": {
                               "input_tokens": 1, "output_tokens": 1,
                               "cache_creation_input_tokens": 1,
                               "cache_read_input_tokens": 1}},
                           "objective_checks": [{"id": "c", "passed": True}]}
                if usage is not None:
                    summary.update(model_tokens=usage_of(usage)["tokens"],
                                   cross_model=cross_of(usage))
                (trial / "summary.json").write_text(json.dumps(summary))
            return pse.fixture_metrics(run_dir, self.SKILL, self.TS, "fx",
                                       len(usages))

    def test_tokens_are_the_total_across_every_model(self):
        heavier = {OWN: usage_entry(10, 20, 50, 20),
                   SUBAGENT: usage_entry(300, 400, 1000, 200)}
        out = self.metrics([MULTI_MODEL_USAGE, heavier])
        # (1000 + 2000) / 2; the main loop's usage is 4 per trial.
        self.assertEqual(out["tokens"], 1500)
        self.assertEqual(out["tokens_basis"], "model_usage_total")
        self.assertEqual(out["main_loop_tokens"], 4)
        self.assertEqual(out["model_tokens"][SUBAGENT],
                         {"input_tokens": 200, "output_tokens": 300,
                          "cache_read_input_tokens": 750,
                          "cache_creation_input_tokens": 150})
        self.assertEqual(out["cross_model"]["flagged_trials"], 2)
        self.assertIs(out["cross_model"]["flagged"], True)

    def test_incomplete_or_missing_accounting_leaves_tokens_unknown(self):
        cases = {
            "a trial without model_tokens": [MULTI_MODEL_USAGE, None],
            "a dropped entry": [MULTI_MODEL_USAGE,
                                {OWN: usage_entry(1, 1, 1, 1), SUBAGENT: []}],
            "a missing count": [MULTI_MODEL_USAGE,
                                {OWN: usage_entry(1, 1, 1, None)}],
            "an oversized count": [MULTI_MODEL_USAGE,
                                   {OWN: usage_entry(1e308, 1, 1, 1)}],
        }
        for label, usages in cases.items():
            with self.subTest(case=label):
                out = self.metrics(usages)
                self.assertIsNone(out["tokens"])
                # decide's existing path for a missing measurement.
                accepted, reasons = pse.decide(
                    {"t": dict(out, passed=1, total=2, error=None),
                     "v": dict(out, passed=1, total=2, error=None)},
                    {"t": dict(out, passed=2, total=2, error=None),
                     "v": dict(out, passed=1, total=2, error=None)}, ["t"], "v")
                self.assertFalse(accepted)
                self.assertTrue(reasons[0].startswith(
                    "inconclusive: missing token data"), reasons)


class TokensBasisTests(unittest.TestCase):

    def m(self, passed, basis="model_usage_total", tokens=1000):
        out = {"passed": passed, "total": 10, "judge_mean": None,
               "error": None, "tokens": tokens}
        if basis is not None:
            out["tokens_basis"] = basis
        return out

    def test_a_mixed_basis_comparison_is_refused_by_name(self):
        base = {"t": self.m(5, basis=None), "v": self.m(8, basis=None)}
        cand = {"t": self.m(8), "v": self.m(8)}
        accepted, reasons = pse.decide(base, cand, ["t"], "v")
        self.assertFalse(accepted)
        self.assertEqual(reasons, [
            "inconclusive: token bases differ ['model_usage_total', "
            "'usage_main_loop']; baseline and candidate must be measured the "
            "same way"])

    def test_the_same_basis_on_both_sides_is_decided(self):
        for basis in (None, "model_usage_total"):
            with self.subTest(basis=basis):
                accepted, _ = pse.decide(
                    {"t": self.m(5, basis), "v": self.m(8, basis)},
                    {"t": self.m(8, basis), "v": self.m(8, basis)}, ["t"], "v")
                self.assertTrue(accepted)

    def test_legacy_metrics_read_as_the_main_loop(self):
        self.assertEqual(pse.tokens_basis({"tokens": 1}), "usage_main_loop")
        self.assertEqual(pse.tokens_basis(None), "usage_main_loop")
        self.assertEqual(pse.TOKENS_BASIS, "model_usage_total")


class ConsumersAgreeTests(unittest.TestCase):
    """The strict schemas downstream hold the same names and caps."""

    def test_the_ingester_and_gate_constants_match_the_harness(self):
        self.assertEqual(ingest.EFFORT_LEVELS, guidance.EFFORT_LEVELS)
        self.assertEqual(ingest.MODEL_TOKEN_KEYS,
                         tuple(name for name, _ in run_eval.MODEL_TOKEN_FIELDS))
        self.assertEqual(ingest.MAX_TOKEN_COUNT, run_eval.MAX_TOKEN_COUNT)
        self.assertEqual(ingest.MAX_MODELS, run_eval.MODELS_USED_MAX)
        self.assertEqual(set(ingest.CROSS_MODEL_KEYS),
                         set(run_eval.cross_model({}, None)))
        self.assertEqual(ingest.CROSS_MODEL_SHARE_THRESHOLD,
                         run_eval.CROSS_MODEL_SHARE_THRESHOLD)
        for model in ("claude-sonnet-5", "claude-sonnet-5[1m]", "sonnet",
                      "claude-haiku-4-5-20251001", "anthropic.claude-opus-4-8-v1",
                      "us.anthropic.claude-sonnet-5-v1:0", "claude-opus-4-8@20260101",
                      "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                      "gpt-4", "", None, "Claude-Sonnet-5"):
            with self.subTest(model=model):
                self.assertEqual(ingest.canonical_model_id(model),
                                 run_eval.canonical_model_id(model))
        self.assertEqual(set(improve_gate.TOKENS_BASES),
                         {pse.TOKENS_BASIS, pse.TOKENS_BASIS_LEGACY})


def _function_shape(path: Path, name: str) -> tuple[str, dict]:
    """(`ast.dump` of function `name`'s arguments and body, its docstring
    left out, and {module-level name it reads: `ast.dump` of the value
    assigned to it}), parsed from `path`. Attributes (line numbers) are off,
    so only the code's shape is compared."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    [func] = [node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == name]
    body = func.body
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    shape = ast.dump(ast.Module(body=[func.args, *body], type_ignores=[]))
    assigned = {target.id: node.value for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    used = {node.id for node in ast.walk(func)
            if isinstance(node, ast.Name) and node.id in assigned}
    return shape, {used_name: ast.dump(assigned[used_name])
                   for used_name in sorted(used)}


class CanonicalModelIdCopiesTests(unittest.TestCase):
    """scripts/ingest_routine_results.py imports nothing from the harness, so
    it carries a copy of `canonical_model_id`. Parsed, not grepped: the two
    function bodies and every module-level constant they read must be the
    same code."""

    def test_the_two_copies_are_the_same_code(self):
        harness_body, harness_consts = _function_shape(
            HARNESS_DIR / "run_eval.py", "canonical_model_id")
        ingest_body, ingest_consts = _function_shape(
            ROOT / "scripts" / "ingest_routine_results.py", "canonical_model_id")
        self.assertEqual(ingest_body, harness_body)
        self.assertEqual(ingest_consts, harness_consts)
        self.assertEqual(sorted(harness_consts), [
            "_CANONICAL_ID", "_ID_PREFIX_BEDROCK", "_ID_SUFFIX_BEDROCK",
            "_ID_SUFFIX_DATE", "_ID_SUFFIX_VARIANT"])


if __name__ == "__main__":
    unittest.main()
