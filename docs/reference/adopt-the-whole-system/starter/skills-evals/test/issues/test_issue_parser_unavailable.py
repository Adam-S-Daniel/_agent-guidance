#!/usr/bin/env python3
"""A missing Bash parser is a scorer error, never an agent failure (#88).

`shell_staged_tool_guard` needs the pinned Tree-sitter wheels. A local run in
a python without them used to score every trial's check as FAILED, the same
as an agent that wrote a bad hook. These tests drive `run_eval.py` as a
subprocess with a `tree_sitter` package on PYTHONPATH whose import raises, so
no real wheel is removed, and with the scripted CLI from test_issue_66.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_parser_unavailable.py`.
"""

from __future__ import annotations

import builtins
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_66 import (HARNESS_DIR, TS, _HarnessCase, _child_env,  # noqa: E402
                           _write_fixture)

sys.path.insert(0, str(HARNESS_DIR))
import run_eval  # noqa: E402
from scorers import bash_ast, objective, shell_capture  # noqa: E402

SKILL = "parser-skill"
CHECKS = [
    {"id": "readme-kept", "type": "file_count", "paths": ["README.md"],
     "min": 1, "max": 1},
    {"id": "guard", "type": "shell_staged_tool_guard", "paths": ["hook.sh"],
     "tools": ["gofmt"]},
    {"id": "capture-files", "type": "shell_capture_safe", "paths": ["hook.sh"],
     "source": "files"},
    {"id": "capture-transcript", "type": "shell_capture_safe",
     "source": "transcript"},
]


class _ParserCase(_HarnessCase):
    def setUp(self):
        super().setUp()
        self.fixture_dir = _write_fixture(self.evals / SKILL, SKILL,
                                          objective_checks=CHECKS)
        self.arm_dir = self.results / SKILL / TS / "without_skill"
        blocker = self.tmp / "no-parser" / "tree_sitter"
        blocker.mkdir(parents=True)
        (blocker / "__init__.py").write_text(
            "raise ImportError('deliberately absent')\n", encoding="utf-8")
        self.blocker = str(blocker.parent)

    def _run_with_pythonpath(self, pythonpath: str, *flags: str):
        # _HarnessCase._run builds the child environment itself (HOME is a
        # temp dir, so a user-site install is invisible to the child), so
        # what the child can import rides in through a wrapper that adds
        # PYTHONPATH.
        original = _child_env

        def with_path(tmp, **extra):
            return original(tmp, PYTHONPATH=pythonpath, **extra)

        import test_issue_66
        test_issue_66._child_env = with_path
        try:
            return self._run(self.fixture_dir, *flags)
        finally:
            test_issue_66._child_env = original

    def _run_blocked(self, *flags: str):
        return self._run_with_pythonpath(self.blocker, *flags)

    def _run_with_parser(self, *flags: str):
        """The child sees the parser wherever THIS process imports it from
        (a venv, site-packages or a user site), not only where HOME finds it."""
        import tree_sitter
        import tree_sitter_bash
        roots = dict.fromkeys(
            str(Path(module.__file__).resolve().parent.parent)
            for module in (tree_sitter, tree_sitter_bash))
        return self._run_with_pythonpath(os.pathsep.join(roots), *flags)

    def _summary(self, *parts: str) -> dict:
        return json.loads(self.arm_dir.joinpath(*parts).read_text(encoding="utf-8"))


class TestParserUnavailable(_ParserCase):
    def test_trials_are_errors_not_failed_checks(self):
        self._script(agents=[{"write": "hook.sh"}] * 3)
        proc = self._run_blocked("--arm", "without_skill", "--trials", "3",
                                 "--timestamp", TS, "--no-judge")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        summary = self._summary("summary.json")
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (3, 3, 0))
        self.assertEqual([e["type"] for e in summary["trial_errors"]],
                         ["scorer_unavailable"] * 3)
        for entry in summary["trial_errors"]:
            self.assertIn("tree-sitter==0.26.0", entry["detail"])
            self.assertIn("tree-sitter-bash==0.25.1", entry["detail"])
        # No check is scored, so none can be a failed one.
        self.assertIsNone(summary["aggregate"]["objective"])
        for k in (1, 2, 3):
            trial = self._summary(f"{run_eval.TRIAL_DIR_PREFIX}{k}", "summary.json")
            self.assertEqual(trial["error"]["type"], "scorer_unavailable")
            self.assertIsNone(trial["objective_checks"])
        self.assertEqual(self._calls("judges"), [])

    def test_objective_only_names_the_missing_parser(self):
        workspace = self.tmp / "ws"
        workspace.mkdir()
        (workspace / "README.md").write_text("# seed\n", encoding="utf-8")
        (workspace / "hook.sh").write_text("gofmt -w x\n", encoding="utf-8")
        proc = self._run_blocked("--arm", "objective-only",
                                 "--workspace", str(workspace))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("scorer_unavailable", proc.stdout)
        self.assertIn("tree-sitter-bash==0.25.1", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    @unittest.skipUnless(bash_ast.parser_importable(),
                         "the pinned parser is not installed here")
    def test_parser_present_scores_the_check_as_before(self):
        self._script(agents=[{"write": "hook.sh"}] * 2)
        proc = self._run_with_parser("--arm", "without_skill",
                                     "--trials", "2", "--timestamp", TS, "--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self._summary("summary.json")
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (2, 0, 2))
        checks = {c["id"]: c for c in summary["aggregate"]["objective"]["checks"]}
        self.assertEqual((checks["guard"]["n"], checks["guard"]["passed"]), (2, 0))
        # The shell_capture_safe checks are scored too, and pass: no dispatch
        # and discovery share a substitution.
        for check_id in ("capture-files", "capture-transcript"):
            self.assertEqual((checks[check_id]["n"], checks[check_id]["passed"]),
                             (2, 2), check_id)
        self.assertEqual(checks["readme-kept"]["passed"], 2)
        trial = self._summary(f"{run_eval.TRIAL_DIR_PREFIX}1", "summary.json")
        results = {c["id"]: c for c in trial["objective_checks"]}
        self.assertFalse(results["guard"]["passed"])
        self.assertTrue(results["guard"]["detail"].startswith("shell_staged_tool_guard: "))
        self.assertNotIn("parser_unavailable", results["guard"]["detail"])


def _without_parser(name, *args, **kwargs):
    if name == "tree_sitter":
        raise ImportError("deliberately absent")
    return _REAL_IMPORT(name, *args, **kwargs)


_REAL_IMPORT = builtins.__import__


class TestEveryParserBackedCheck(unittest.TestCase):
    """Each check in PARSER_BACKED_CHECKS raises, whatever the agent wrote."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _fixture(self, check: dict) -> dict:
        return {"objective_checks": [check]}

    def test_the_set_names_every_check_that_parses_bash(self):
        self.assertEqual(objective.PARSER_BACKED_CHECKS,
                         {"shell_staged_tool_guard", "shell_capture_safe"})

    def test_shell_capture_safe_raises_for_both_sources_and_any_content(self):
        contents = ("", "echo hi\n", "x=$(gh workflow run a.yml && gh run list)\n")
        for source in ("files", "transcript"):
            for text in contents:
                with self.subTest(source=source, text=text):
                    (self.tmp / "hook.sh").write_text(text, encoding="utf-8")
                    check = {"id": "c", "type": "shell_capture_safe",
                             "source": source, "paths": ["hook.sh"]}
                    with mock.patch("builtins.__import__", side_effect=_without_parser):
                        with self.assertRaises(objective.ScorerUnavailableError) as ctx:
                            objective.run_checks(
                                self._fixture(check), str(self.tmp), str(self.tmp),
                                transcript=f"```bash\n{text}```\n")
                    self.assertNotIsInstance(ctx.exception, ValueError)
                    self.assertIn("tree-sitter-bash==0.25.1", str(ctx.exception))

    def test_a_bad_source_is_still_a_value_error_without_the_parser(self):
        with mock.patch("builtins.__import__", side_effect=_without_parser):
            with self.assertRaises(ValueError):
                shell_capture.shell_capture_safe("", [], source="other")

    @unittest.skipUnless(bash_ast.parser_importable(),
                         "the pinned parser is not installed here")
    def test_parser_present_scores_unchanged(self):
        (self.tmp / "hook.sh").write_text(
            "x=$(gh workflow run a.yml && gh run list)\n", encoding="utf-8")
        bad = shell_capture.shell_capture_safe(str(self.tmp), ["hook.sh"])
        self.assertFalse(bad[0])
        self.assertIn("share a command substitution", bad[1])
        (self.tmp / "hook.sh").write_text("echo hi\n", encoding="utf-8")
        self.assertTrue(shell_capture.shell_capture_safe(
            str(self.tmp), ["hook.sh"])[0])

    def test_every_caller_of_the_parser_is_covered(self):
        # The modules that import bash_ast's parser, found from source: a new
        # one has to be added to PARSER_BACKED_CHECKS (or to a check already
        # in it) and to this list.
        found = sorted(p.name for p in (HARNESS_DIR / "scorers").glob("*.py")
                       if "parse_bash" in p.read_text(encoding="utf-8")
                       and p.name != "bash_ast.py")
        self.assertEqual(found, ["objective.py", "shell_capture.py", "shell_guard.py"])


class TestGuidanceArmScorerUnavailable(unittest.TestCase):
    """The guidance path scores with `run_checks` too: same trial error."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.args = types.SimpleNamespace(
            results_dir=self.tmp / "results", model="fake-model-a",
            timeout=30, no_judge=False, harness_version=None)
        self.ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
                    "guidance_dir": self.tmp, "row": {}, "section": "s",
                    "key": "guidance/s"}
        self.arm = {"name": "treatment", "mode": "section", "objective_checks": [
            {"id": "c", "type": "shell_capture_safe", "source": "transcript"}]}
        self.fixture = {"prompt": "do it", "judge_rubric": "r"}

    def _run(self):
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}
        guard = {"ok": True, "expected": True, "observed": True, "detail": "d"}
        agent = {"transcript": "t", "usage": {}, "cost_usd": 0.0, "num_turns": 1,
                 "duration_ms": 1, "raw": {"modelUsage": {"fake-model-a": {}}}}
        judge_score = mock.Mock()
        with mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", return_value=guard), \
             mock.patch.object(run_eval, "run_agent", return_value=agent), \
             mock.patch.object(run_eval.judge, "score", judge_score), \
             mock.patch("builtins.__import__", side_effect=_without_parser):
            result = run_eval._run_guidance_arm(
                self.arm, self.fixture, self.tmp / "no-seed", self.ctx,
                self.args, "T")
        return result, judge_score

    def test_a_missing_parser_is_a_trial_error_and_skips_the_judge(self):
        result, judge_score = self._run()
        self.assertEqual(result["error"]["type"], "scorer_unavailable")
        self.assertIsNone(result["objective_checks"])
        judge_score.assert_not_called()
        path = self.args.results_dir / "guidance" / "s" / "T" / "treatment" / "summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(summary["error"]["type"], "scorer_unavailable")
        self.assertIsNone(summary["objective_checks"])



class TestGuidanceObjectiveOnlyScorerUnavailable(_ParserCase):
    """`subject: guidance` with `--arm objective-only` scores with `run_checks`
    too: a missing parser is named and exits 2, as the skill path does, never
    a traceback and exit 1 (the code a failing check returns)."""

    def setUp(self):
        super().setUp()
        self.fixture_dir = self.evals / "guidance-parser"
        (self.fixture_dir / "seed").mkdir(parents=True)
        (self.fixture_dir / "seed" / "hook.sh").write_text(
            "gofmt -w x\n", encoding="utf-8")
        doc = {"subject": "guidance", "section": "parser-section",
               "objective_checks": [
                   {"id": "capture-files", "type": "shell_capture_safe",
                    "paths": ["hook.sh"], "source": "files"}]}
        (self.fixture_dir / "fixture.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def test_objective_only_names_the_missing_parser(self):
        proc = self._run_blocked("--arm", "objective-only")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("scorer_unavailable", proc.stdout)
        self.assertIn("tree-sitter-bash==0.25.1", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        # Nothing scored, so no `checks` document that could read as a result.
        self.assertNotIn('"checks"', proc.stdout)

    @unittest.skipUnless(bash_ast.parser_importable(),
                         "the pinned parser is not installed here")
    def test_parser_present_scores_the_check_as_before(self):
        proc = self._run_with_parser("--arm", "objective-only")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual([(c["id"], c["passed"]) for c in out["checks"]],
                         [("capture-files", True)])


class TestGuidanceObjectiveOnlyInvalidFixture(_ParserCase):
    """`subject: guidance` with `--arm objective-only` and a fixture value the
    scorer refuses (`strip_seed: "no"`) is a named `invalid_fixture` and exit
    2, as the skill path reports it, never a traceback and exit 1 (the code a
    failing check returns)."""

    def setUp(self):
        super().setUp()
        self.fixture_dir = self.evals / "guidance-bad-fixture"
        (self.fixture_dir / "seed").mkdir(parents=True)
        (self.fixture_dir / "seed" / "hook.sh").write_text(
            "gofmt -w x\n", encoding="utf-8")
        doc = {"subject": "guidance", "section": "bad-fixture-section",
               "objective_checks": [
                   {"id": "reply-reads", "type": "transcript_matches",
                    "must_match": ["x"], "strip_seed": "no"}]}
        (self.fixture_dir / "fixture.yaml").write_text(
            yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def test_a_bad_fixture_value_is_invalid_fixture_and_exit_2(self):
        proc = self._run(self.fixture_dir, "--arm", "objective-only")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("invalid_fixture: ", proc.stdout)
        self.assertIn("strip_seed", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        # Nothing scored, so no `checks` document that could read as a result.
        self.assertNotIn('"checks"', proc.stdout)


class TestAggregateDenominator(unittest.TestCase):
    def test_a_scorer_error_trial_is_outside_every_check_denominator(self):
        scored = {"error": None, "agent": {"cost_usd": 0.1},
                  "objective_checks": [{"id": "guard", "passed": True}]}
        errored = {"error": {"type": "scorer_unavailable", "detail": "d"},
                   "agent": {"cost_usd": 0.1}, "objective_checks": None}
        stats = run_eval.aggregate_trials([scored, errored, scored])
        self.assertEqual((stats["n"], stats["errors"], stats["scored"]), (3, 1, 2))
        self.assertEqual(stats["trial_errors"][0]["type"], "scorer_unavailable")
        check = stats["aggregate"]["objective"]["checks"][0]
        self.assertEqual((check["n"], check["passed"], check["pass_rate"]), (2, 2, 1.0))


if __name__ == "__main__":
    unittest.main()
