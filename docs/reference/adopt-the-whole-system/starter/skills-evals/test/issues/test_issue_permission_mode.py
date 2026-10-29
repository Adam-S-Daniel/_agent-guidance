"""The arms' CLI permission mode is a recorded harness setting (#71).

ADR 0010 runs eval arms in a Claude routine, whose sandbox runs as root, and
the CLI refuses `bypassPermissions` as root (probe 2026-10-06). The harness
now launches every agent arm and the judge with `--permission-mode auto` by
default, keeps `bypassPermissions` selectable for comparability with older
results, records the mode in every summary.json, and never averages runs
made under different modes into one badge.

Hermetic: `subprocess.run` is mocked, or the CLI is a stand-in script; no
real CLI, no network.
"""

from __future__ import annotations

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
import local_eval  # noqa: E402
import make_badge  # noqa: E402
import run_eval  # noqa: E402
from scorers import judge  # noqa: E402

SKIP_FLAG = "--dangerously-skip-permissions"


def agent_reply() -> dict:
    return {"type": "result", "is_error": False, "result": "done",
            "total_cost_usd": 0.0, "num_turns": 1, "duration_ms": 1,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "modelUsage": {"model-a": {"inputTokens": 1, "costUSD": 0.0}},
            "session_id": "sess-1"}


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


def mode_values(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv)
            if arg == "--permission-mode"]


class RunAgentPermissionMode(unittest.TestCase):

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="workspace-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def _launch(self, **arm) -> Recorder:
        cli = Recorder(json.dumps(agent_reply()))
        arm = {"name": "without_skill", "timeout": 30, "model": "model-a", **arm}
        with mock.patch.object(run_eval.subprocess, "run", cli), \
             mock.patch.dict(run_eval.os.environ, {"CLAUDE_BIN": "fake-claude"}):
            out = run_eval.run_agent(self.workspace, "Do the task.", arm)
        self.assertNotIn("error", out, out)
        return cli

    def test_the_default_is_auto_and_only_one_flag(self):
        cli = self._launch()
        self.assertEqual(len(cli.argvs), 1)
        self.assertEqual(mode_values(cli.argvs[0]), ["auto"])
        self.assertNotIn(SKIP_FLAG, cli.argvs[0])
        self.assertNotIn("bypassPermissions", cli.argvs[0])

    def test_an_explicit_mode_is_passed_through(self):
        for mode in guidance.PERMISSION_MODES:
            with self.subTest(mode=mode):
                cli = self._launch(permission_mode=mode)
                self.assertEqual(mode_values(cli.argvs[0]), [mode])
                self.assertNotIn(SKIP_FLAG, cli.argvs[0])

    def test_an_unknown_mode_is_refused_before_any_spawn(self):
        cli = Recorder("{}")
        with mock.patch.object(run_eval.subprocess, "run", cli):
            for mode in ("dontAsk", "", None, "auto --dangerously-skip-permissions"):
                with self.subTest(mode=mode), \
                     self.assertRaises(guidance.GuidanceError):
                    run_eval.run_agent(self.workspace, "x", {
                        "name": "without_skill", "timeout": 30,
                        "permission_mode": mode})
        self.assertEqual(cli.argvs, [])


class JudgePermissionMode(unittest.TestCase):

    def _launch(self, **kwargs) -> list[str]:
        cli = Recorder(json.dumps({"type": "result", "result": "{}"}))
        with mock.patch.object(judge.subprocess, "run", cli):
            judge._run_judge_cli("prompt", model=None, timeout=30, **kwargs)
        self.assertEqual(len(cli.argvs), 1)
        return cli.argvs[0]

    def test_the_judge_defaults_to_auto(self):
        argv = self._launch()
        self.assertEqual(mode_values(argv), ["auto"])
        self.assertNotIn(SKIP_FLAG, argv)

    def test_the_judge_follows_an_explicit_mode(self):
        self.assertEqual(mode_values(self._launch(
            permission_mode="bypassPermissions")), ["bypassPermissions"])

    def test_the_judge_refuses_an_unknown_mode(self):
        with self.assertRaises(guidance.GuidanceError):
            self._launch(permission_mode="plan")


class NoHardcodedBypassInLaunchArgv(unittest.TestCase):
    """Parsed, not grepped: no list literal in the harness spells the old
    flag or a literal `bypassPermissions` mode into an argv."""

    def test_no_launch_list_hardcodes_bypass(self):
        offenders = []
        for path in sorted((ROOT / "harness").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                values = [elt.value for elt in node.elts
                          if isinstance(elt, ast.Constant)
                          and isinstance(elt.value, str)]
                if SKIP_FLAG in values or "bypassPermissions" in values and \
                        "--permission-mode" in values:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])


def _write_cli(path: Path, log: Path) -> Path:
    """A stand-in CLI: answers `--version`, logs every other argv to `log`
    (an absolute path baked in, since an arm's environment is an allowlist),
    and replies with a result that parses as both an agent transcript and a
    judge verdict."""
    verdict = json.dumps({"dimensions": [{"name": "d", "score": 5,
                                          "rationale": "r"}], "overall": 5})
    reply = json.dumps({"type": "result", "is_error": False, "result": verdict,
                        "total_cost_usd": 0.0, "num_turns": 1,
                        "duration_ms": 1, "usage": {}, "modelUsage": {}})
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('fake-claude 0.0.0')\n"
        "    sys.exit(0)\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({reply!r})\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class SummaryRecordsTheMode(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "argv.log"
        self.cli = _write_cli(self.tmp / "claude", self.log)
        self.eval_dir = self.tmp / "evals" / "mode-probe"
        (self.eval_dir / "seed").mkdir(parents=True)
        (self.eval_dir / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        (self.eval_dir / "fixture.yaml").write_text(yaml.safe_dump({
            "skill": "mode-probe", "prompt": "do it", "model": "fake-model-a",
            "judge_rubric": "r", "judge": {"model": "fake-judge"},
            "objective_checks": [{"id": "seed-kept", "description": "d",
                                  "type": "files_unchanged",
                                  "paths": ["README.md"]}]}),
            encoding="utf-8")

    def _run(self, *extra):
        env = os.environ.copy()
        env["CLAUDE_BIN"] = str(self.cli)
        results = self.tmp / "results"
        proc = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"),
             str(self.eval_dir), "--arm", "without_skill",
             "--results-dir", str(results), "--timeout", "30", *extra],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        [run_dir] = list((results / "mode-probe").iterdir())
        summary = json.loads((run_dir / "without_skill" / "summary.json")
                             .read_text(encoding="utf-8"))
        argvs = [json.loads(line) for line in
                 self.log.read_text(encoding="utf-8").splitlines()]
        return summary, argvs

    def _check(self, summary, argvs, mode):
        self.assertEqual(summary["harness"]["permission_mode"], mode)
        self.assertIsNone(summary["error"])
        self.assertNotIn("error", summary["judge"] or {}, summary["judge"])
        # The agent call and the judge call.
        self.assertEqual(len(argvs), 2, argvs)
        for argv in argvs:
            self.assertEqual(mode_values(argv), [mode])
            self.assertNotIn(SKIP_FLAG, argv)

    def test_the_default_run_records_auto(self):
        self._check(*self._run(), "auto")

    def test_an_explicit_bypass_run_records_bypass(self):
        self._check(*self._run("--permission-mode", "bypassPermissions"),
                    "bypassPermissions")

    def test_an_unknown_mode_is_an_argparse_error(self):
        env = os.environ.copy()
        env["CLAUDE_BIN"] = str(self.cli)
        proc = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"),
             str(self.eval_dir), "--permission-mode", "dontAsk"],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--permission-mode", proc.stderr)
        self.assertFalse(self.log.exists())


class LocalEvalPassesTheMode(unittest.TestCase):

    def test_default_and_explicit(self):
        base = ["evals/x", "--results-dir", "/nonexistent/out"]
        self.assertEqual(local_eval.parse_args(base).permission_mode, "auto")
        self.assertEqual(local_eval.parse_args(
            [*base, "--permission-mode", "bypassPermissions"]).permission_mode,
            "bypassPermissions")


class BadgeNeverMixesModes(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, ts: str, mode, with_passed: int, without_passed: int = 0,
             without_mode="same"):
        for arm, passed, arm_mode in (
                ("with_skill", with_passed, mode),
                ("without_skill", without_passed,
                 mode if without_mode == "same" else without_mode)):
            arm_dir = self.tmp / "s" / ts / arm
            arm_dir.mkdir(parents=True)
            summary = {"arm": arm, "error": None, "n": 1,
                       "objective_checks": [{"passed": i < passed}
                                            for i in range(2)],
                       "judge": None,
                       "harness": {"name": "claude-code", "version": "v"}}
            if arm_mode is not None:
                summary["harness"]["permission_mode"] = arm_mode
            (arm_dir / "summary.json").write_text(json.dumps(summary),
                                                  encoding="utf-8")

    def test_a_summary_without_a_mode_is_legacy_bypass(self):
        self.assertEqual(make_badge.permission_mode({}), "bypassPermissions")
        self.assertEqual(make_badge.permission_mode(
            {"harness": {"name": "claude-code", "version": None}}),
            "bypassPermissions")
        self.assertIsNone(make_badge.permission_mode(
            {"harness": {"permission_mode": 3}}))

    def test_the_newest_mode_wins_and_older_modes_drop(self):
        self._run("20261001T000000Z", None, 0)           # legacy = bypass
        self._run("20261002T000000Z", "bypassPermissions", 0)
        self._run("20261003T000000Z", "auto", 2)
        usable = make_badge.usable_units(self.tmp, "s", 5)
        self.assertEqual([u[0].name for u in usable], ["20261003T000000Z"])
        badge = make_badge.build_badge(self.tmp, "s", 5)
        self.assertTrue(badge["message"].startswith("with 2/2 vs without 0/2"),
                        badge)

    def test_legacy_and_explicit_bypass_average_together(self):
        self._run("20261001T000000Z", None, 0)
        self._run("20261002T000000Z", "bypassPermissions", 2)
        usable = make_badge.usable_units(self.tmp, "s", 5)
        self.assertEqual(len(usable), 2)

    def test_a_pair_whose_arms_disagree_is_dropped(self):
        self._run("20261001T000000Z", "auto", 2, without_mode="bypassPermissions")
        self.assertEqual(make_badge.usable_units(self.tmp, "s", 5), [])


if __name__ == "__main__":
    unittest.main()
