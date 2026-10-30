"""Hidden repository tests (`repo_tests`), seed agent-context stripping and
lockfile `deps:` — the fixtures DESIGN's "smallest next PR 2" for real-work
fixtures. Offline: no network, no real npm, no CLI; the network-namespace
sandbox boundary is mocked so behavior checks are the same on any host.
"""

from __future__ import annotations

import contextlib
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import guidance  # noqa: E402
import run_eval  # noqa: E402
import seed_prep  # noqa: E402
from scorers import commands, objective, repo_tests  # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
HIDDEN_TEST = (
    "import unittest\n\nimport calc\n\n\n"
    "class CalcTests(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n\n"
    "    def test_sub(self):\n        self.assertEqual(calc.sub(5, 3), 2)\n")
ADD = "test_calc.CalcTests.test_add"
SUB = "test_calc.CalcTests.test_sub"

MANAGED_AGENTS_MD = (
    "<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE \"## Repo-specific additions\" -->\n"
    "<!-- Source: _agent-guidance -->\n\n# AGENTS.md\n\n"
    "> **Managed by [`_agent-guidance`].**\n"
    "> Edit only below the `## Repo-specific additions` header.\n\n"
    "## The floor\n\n- FLEET-RULE-TEXT: every uses: is pinned.\n\n"
    "<!-- END MANAGED SECTION -->\n"
    "## Repo-specific additions\n\n- REPO-OWN-TEXT: run make check.\n")
BRIDGE_CLAUDE_MD = (
    "<!-- Managed by _agent-guidance: bridges Claude Code (which reads "
    "CLAUDE.md) to AGENTS.md. -->\n@AGENTS.md\n")
SETTINGS = {"hooks": {"SessionStart": [{"hooks": [
    {"type": "command", "command": "bash .claude/hooks/skills-bootstrap.sh"},
    {"type": "command", "command": "bash .claude/hooks/fleet-memory.sh"}]}]}}


def write_fleet_seed(seed: Path) -> None:
    """A seed shaped like a fleet repository's base tree."""
    (seed / ".claude" / "hooks").mkdir(parents=True)
    (seed / ".claude" / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    for hook in ("skills-bootstrap.sh", "fleet-memory.sh"):
        (seed / ".claude" / "hooks" / hook).write_text("#!/bin/sh\n", encoding="utf-8")
    (seed / ".claude" / "hooks" / "fleet-guidance.md").write_text(
        "FLEET-RULE-TEXT\n", encoding="utf-8")
    (seed / "skills.lock").write_text("bundles: []\n", encoding="utf-8")
    (seed / "AGENTS.md").write_text(MANAGED_AGENTS_MD, encoding="utf-8")
    (seed / "CLAUDE.md").write_text(BRIDGE_CLAUDE_MD, encoding="utf-8")
    (seed / "calc.py").write_text(BUGGY, encoding="utf-8")


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repo-tests-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.fixture_dir = self.root / "fixture"
        self.seed = self.fixture_dir / "seed"
        self.seed.mkdir(parents=True)
        (self.seed / "calc.py").write_text(BUGGY, encoding="utf-8")
        checker = self.fixture_dir / "checker"
        checker.mkdir()
        (checker / "test_calc.py").write_text(HIDDEN_TEST, encoding="utf-8")
        network = mock.patch.object(commands, "_sandbox_prefix", return_value=([], "unavailable"))
        network.start()
        self.addCleanup(network.stop)
        sandbox = mock.patch.object(commands, "_run_sandboxed",
                                    side_effect=lambda prefix, *args: commands._run_command(*args))
        sandbox.start()
        self.addCleanup(sandbox.stop)

    def workspace(self, source: str = BUGGY) -> Path:
        ws = self.root / "final"
        if ws.exists():
            shutil.rmtree(ws)
        shutil.copytree(self.seed, ws)
        (ws / "calc.py").write_text(source, encoding="utf-8")
        return ws

    def check(self, ws: Path, **overrides):
        kwargs = {"overlay": "checker", "argv": ["python3", "-m", "unittest"],
                  "fail_to_pass": [ADD], "pass_to_pass": [SUB], "timeout_s": 30}
        kwargs.update(overrides)
        return repo_tests.repo_tests(str(ws), [], seed=str(self.seed), **kwargs)


class RepoTestsCheckTests(_Base):
    def test_red_on_the_unfixed_workspace(self):
        passed, detail = self.check(self.workspace(BUGGY))
        self.assertFalse(passed)
        self.assertIn("fail_to_pass=0/1 pass_to_pass=1/1", detail)
        self.assertIn(f"{ADD} (exit=1)", detail)

    def test_green_on_the_fixed_workspace(self):
        passed, detail = self.check(self.workspace(FIXED))
        self.assertTrue(passed, detail)
        self.assertEqual(detail, "repo_tests fail_to_pass=1/1 pass_to_pass=1/1 "
                                 "network=unavailable")

    def test_hidden_tests_never_reach_the_seed_or_the_workspace(self):
        ws = self.workspace(FIXED)
        self.assertTrue(self.check(ws)[0])
        self.assertFalse((ws / "test_calc.py").exists())
        self.assertFalse((self.seed / "test_calc.py").exists())
        materialized = run_eval.materialize_workspace(self.seed, {})
        self.addCleanup(shutil.rmtree, materialized, ignore_errors=True)
        self.assertFalse((materialized / "test_calc.py").exists())

    def test_the_overlay_replaces_an_agent_written_copy(self):
        ws = self.workspace(BUGGY)
        (ws / "test_calc.py").write_text(
            "import unittest\n\nclass CalcTests(unittest.TestCase):\n"
            "    def test_add(self):\n        pass\n\n"
            "    def test_sub(self):\n        pass\n", encoding="utf-8")
        self.assertFalse(self.check(ws)[0])

    def test_an_agent_symlink_is_never_written_through(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.fixture_dir / "checker" / "tests").mkdir()
        (self.fixture_dir / "checker" / "tests" / "probe.txt").write_text(
            "hidden", encoding="utf-8")
        ws = self.workspace(FIXED)
        (ws / "tests").symlink_to(outside)
        (ws / "test_calc.py").symlink_to(outside / "planted.py")
        self.assertTrue(self.check(ws)[0])
        self.assertEqual(list(outside.iterdir()), [])

    def test_overlay_outside_the_fixture_or_inside_the_seed_is_refused(self):
        (self.root / "elsewhere").mkdir()
        (self.root / "elsewhere" / "t.py").write_text("", encoding="utf-8")
        (self.fixture_dir / "linked").symlink_to(self.root / "elsewhere")
        (self.seed / "tests").mkdir()
        (self.seed / "tests" / "t.py").write_text("", encoding="utf-8")
        ws = self.workspace(FIXED)
        for overlay in ("../elsewhere", str(self.root / "elsewhere"), "seed",
                        "seed/tests", ".", "linked", "missing", "", 3):
            with self.subTest(overlay=overlay):
                passed, detail = self.check(ws, overlay=overlay)
                self.assertFalse(passed)
                self.assertTrue(detail.startswith("repo_tests_invalid: "), detail)
                with self.assertRaises(repo_tests.RepoTestsConfigError):
                    repo_tests.validate_check(
                        {"id": "t", "type": "repo_tests", "overlay": overlay,
                         "argv": ["python3"], "fail_to_pass": [ADD]},
                        self.fixture_dir)

    def test_symlinks_and_git_paths_inside_the_overlay_are_refused(self):
        ws = self.workspace(FIXED)
        link = self.fixture_dir / "checker" / "link.py"
        link.symlink_to(self.fixture_dir / "checker" / "test_calc.py")
        self.assertIn("symlinks", self.check(ws)[1])
        link.unlink()
        (self.fixture_dir / "checker" / ".git").mkdir()
        (self.fixture_dir / "checker" / ".git" / "config").write_text("", encoding="utf-8")
        self.assertIn(".git", self.check(ws)[1])

    def test_oversized_and_malformed_checks_are_red(self):
        ws = self.workspace(FIXED)
        too_many = [ADD] * (repo_tests.MAX_TESTS + 1)
        rows = [
            {"fail_to_pass": too_many},
            {"fail_to_pass": []},
            {"fail_to_pass": [""]},
            {"fail_to_pass": [[]]},
            {"fail_to_pass": ["x" * (repo_tests.MAX_TEST_ARG_CHARS + 1)]},
            {"pass_to_pass": "test_calc"},
            {"timeout_s": repo_tests.MAX_TIMEOUT_S + 1},
            {"timeout_s": True},
            {"timeout_s": 0},
            {"argv": []},
            {"argv": "python3"},
        ]
        for row in rows:
            with self.subTest(row=row):
                passed, detail = self.check(ws, **row)
                self.assertFalse(passed)
                self.assertTrue(detail.startswith("repo_tests_invalid: "), detail)
        with mock.patch.object(repo_tests, "MAX_OVERLAY_BYTES", 10):
            self.assertIn("bytes", self.check(ws)[1])
        with mock.patch.object(repo_tests, "MAX_OVERLAY_FILES", 0):
            self.assertIn("files", self.check(ws)[1])

    def test_unknown_and_missing_keys_are_refused(self):
        base = {"id": "t", "type": "repo_tests", "overlay": "checker",
                "argv": ["python3"], "fail_to_pass": [ADD]}
        repo_tests.validate_check(base, self.fixture_dir)
        with self.assertRaises(repo_tests.RepoTestsConfigError):
            repo_tests.validate_check(dict(base, overlay_dir="checker"), self.fixture_dir)
        for key in ("overlay", "argv", "fail_to_pass"):
            with self.subTest(missing=key), \
                    self.assertRaises(repo_tests.RepoTestsConfigError):
                repo_tests.validate_check(
                    {k: v for k, v in base.items() if k != key}, self.fixture_dir)
        with self.assertRaises(ValueError):
            objective.run_checks({"objective_checks": [dict(base, extra=1)]},
                                 str(self.workspace(FIXED)), str(self.seed))

    def test_registered_and_given_the_fixture_seed_by_run_checks(self):
        self.assertIs(objective.CHECKS["repo_tests"], repo_tests.repo_tests)
        fixture = {"objective_checks": [
            {"id": "hidden", "type": "repo_tests", "overlay": "checker",
             "argv": ["python3", "-m", "unittest"], "fail_to_pass": [ADD]}]}
        red = objective.run_checks(fixture, str(self.workspace(BUGGY)), str(self.seed))
        green = objective.run_checks(fixture, str(self.workspace(FIXED)), str(self.seed))
        self.assertEqual([r["passed"] for r in red + green], [False, True])

    def test_each_selected_test_is_its_own_isolated_process(self):
        calls = []

        def record(argv, workspace, env, stdout, stderr, timeout_s):
            calls.append((argv, Path(workspace), dict(env), timeout_s))
            return 0
        ws = self.workspace(FIXED)
        with mock.patch.object(commands, "_run_command", side_effect=record):
            passed, _ = self.check(ws, pass_to_pass=[SUB, ["-k", "test_sub"]],
                                   timeout_s=7)
        self.assertTrue(passed)
        self.assertEqual([call[0][1:] for call in calls],
                         [["-m", "unittest", ADD], ["-m", "unittest", SUB],
                          ["-m", "unittest", "-k", "test_sub"]])
        self.assertEqual(calls[0][0][0], commands._fixed_interpreter("python3"))
        self.assertTrue(all(call[3] == 7 for call in calls))
        self.assertTrue(all(call[1] != ws.resolve() for call in calls),
                        "tests run in a scratch copy, never the agent's workspace")
        self.assertEqual(len({call[2]["HOME"] for call in calls}), 3)
        self.assertNotIn("ANTHROPIC_API_KEY", calls[0][2])

    def test_timeouts_and_spawn_failures_are_named_without_program_output(self):
        ws = self.workspace(FIXED)
        with mock.patch.object(commands, "_run_command",
                               side_effect=subprocess.TimeoutExpired("private", 1)):
            passed, detail = self.check(ws)
        self.assertFalse(passed)
        self.assertIn(f"{ADD} (timeout)", detail)
        self.assertNotIn("private", detail)
        with mock.patch.object(commands, "_run_command", side_effect=OSError("/home/x")):
            detail = self.check(ws)[1]
        self.assertIn("(spawn_failed)", detail)
        self.assertNotIn("/home/x", detail)


class SeedStripTests(_Base):
    def setUp(self):
        super().setUp()
        write_fleet_seed(self.seed)

    def test_strip_keeps_only_the_repo_specific_additions(self):
        ws = self.workspace(BUGGY)
        report = seed_prep.strip_agent_context(ws)
        self.assertEqual(report, {"removed": [".claude", "skills.lock"],
                                  "rewritten": ["AGENTS.md", "CLAUDE.md"]})
        agents = (ws / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("## Repo-specific additions\n"))
        self.assertIn("REPO-OWN-TEXT", agents)
        self.assertNotIn("FLEET-RULE-TEXT", agents)
        self.assertEqual((ws / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
        self.assertEqual(seed_prep.agent_context_violations(ws), [])

    def test_the_unstripped_seed_fails_the_guard(self):
        found = seed_prep.agent_context_violations(self.seed)
        self.assertIn(".claude is present", found)
        self.assertIn("skills.lock is present", found)
        self.assertTrue(any("AGENTS.md carries the fleet marker" in f for f in found))
        self.assertTrue(any("CLAUDE.md carries the fleet marker" in f for f in found))
        self.assertIn("AGENTS.md has text above '## Repo-specific additions'", found)

    def test_a_stripped_seed_commit_has_no_session_start_hook(self):
        ws = run_eval.materialize_workspace(self.seed, {"strip_agent_context": True})
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        tracked = run_eval._git("ls-files", cwd=ws).stdout.split()
        self.assertEqual(sorted(tracked), ["AGENTS.md", "CLAUDE.md", "calc.py"])
        self.assertFalse((ws / ".claude").exists())
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertNotIn("FLEET-RULE-TEXT", (ws / name).read_text(encoding="utf-8"))
            self.assertNotIn("SessionStart", (ws / name).read_text(encoding="utf-8"))

    def test_without_the_key_a_seed_is_copied_unchanged(self):
        ws = run_eval.materialize_workspace(self.seed, {})
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        self.assertTrue((ws / ".claude" / "settings.json").is_file())
        self.assertEqual((ws / "AGENTS.md").read_text(encoding="utf-8"), MANAGED_AGENTS_MD)

    def test_setup_that_writes_agent_context_back_fails_the_arm(self):
        fixture = {"strip_agent_context": True,
                   "setup": "mkdir -p .claude && echo '{}' > .claude/settings.json"}
        with self.assertRaises(run_eval.SetupFailedError) as caught:
            run_eval.materialize_workspace(self.seed, fixture)
        self.addCleanup(shutil.rmtree, caught.exception.workspace, ignore_errors=True)
        self.assertEqual(caught.exception.detail["error"], "seed_not_stripped")
        self.assertIn(".claude is present", caught.exception.detail["detail"])

    def test_a_fleet_marker_the_strip_cannot_cut_fails_the_guard(self):
        ws = self.workspace(BUGGY)
        (ws / "AGENTS.md").write_text(
            "## Repo-specific additions\n\nQuoting: BEGIN MANAGED SECTION\n",
            encoding="utf-8")
        seed_prep.strip_agent_context(ws)
        error = seed_prep.seed_guard(ws, {"strip_agent_context": True})
        self.assertEqual(error["error"], "seed_not_stripped")
        self.assertIn("BEGIN MANAGED SECTION", error["detail"])
        self.assertIsNone(seed_prep.seed_guard(ws, {}))

    def test_fleet_text_with_no_marker_line_is_removed_whole(self):
        ws = self.workspace(BUGGY)
        (ws / "AGENTS.md").write_text(
            "# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**\n- FLEET-RULE-TEXT\n",
            encoding="utf-8")
        (ws / "CLAUDE.md").unlink()
        (ws / "CLAUDE.md").symlink_to("AGENTS.md")
        report = seed_prep.strip_agent_context(ws)
        self.assertIn("AGENTS.md", report["removed"])
        self.assertIn("CLAUDE.md", report["removed"])
        self.assertEqual(seed_prep.agent_context_violations(ws), [])

    def test_a_repo_own_claude_md_is_kept(self):
        ws = self.workspace(BUGGY)
        (ws / "CLAUDE.md").write_text("# Notes\n\nRun make check.\n", encoding="utf-8")
        seed_prep.strip_agent_context(ws)
        self.assertEqual((ws / "CLAUDE.md").read_text(encoding="utf-8"),
                         "# Notes\n\nRun make check.\n")

    def test_a_none_guidance_arm_over_a_fleet_seed_sees_no_fleet_text(self):
        seen = {}

        class Stop(Exception):
            pass

        def snapshot(*args, cwd):
            seen["files"] = sorted(p.relative_to(cwd).as_posix()
                                   for p in Path(cwd).rglob("*"))
            seen["text"] = "".join(
                (Path(cwd) / name).read_text(encoding="utf-8")
                for name in ("AGENTS.md", "CLAUDE.md") if (Path(cwd) / name).exists())
            raise Stop
        fixture = {"subject": "guidance", "strip_agent_context": True}
        with mock.patch.object(run_eval, "_git", side_effect=snapshot), \
                self.assertRaises(Stop):
            run_eval._run_guidance_arm({"name": "without_guidance", "mode": "none"},
                                       fixture, self.seed, {}, SimpleNamespace(),
                                       "20261006T000000Z")
        self.assertEqual(seen["files"], ["AGENTS.md", "CLAUDE.md", "calc.py"])
        self.assertIn("REPO-OWN-TEXT", seen["text"])
        for marker in ("FLEET-RULE-TEXT", *seed_prep.FLEET_MARKERS):
            self.assertNotIn(marker, seen["text"])


class StrippedSeedScoringTests(_Base):
    """Checks that compare against the seed see the seed the agent got.

    The workspace is stripped before the agent runs, so a check that reads
    the pristine, unstripped seed reports the strip itself as the agent's
    change.
    """

    def setUp(self):
        super().setUp()
        write_fleet_seed(self.seed)

    def fixture(self, **extra):
        fixture = {
            "strip_agent_context": True,
            "objective_checks": [
                {"id": "kept", "type": "files_unchanged",
                 "paths": ["AGENTS.md", "CLAUDE.md"]},
                {"id": "listing", "type": "dir_listing_matches",
                 "paths": ["."], "ignore": [".git"],
                 "expected": ["AGENTS.md", "CLAUDE.md", "calc.py"]},
                {"id": "hidden-tests", "type": "repo_tests",
                 "overlay": "checker", "argv": ["python3", "-m", "unittest"],
                 "fail_to_pass": [ADD], "pass_to_pass": [SUB]},
            ]}
        fixture.update(extra)
        return fixture

    def stripped_workspace(self, source=FIXED):
        ws = self.workspace(source)
        seed_prep.strip_agent_context(ws)
        return ws

    def test_seed_comparing_checks_see_the_stripped_seed(self):
        results = objective.run_checks(self.fixture(), str(self.stripped_workspace()),
                                       str(self.seed))
        self.assertEqual([(r["id"], r["passed"]) for r in results],
                         [("kept", True), ("listing", True), ("hidden-tests", True)],
                         results)

    def test_dir_listing_expected_file_is_read_from_the_pristine_seed(self):
        # `expected_file` is read from the pristine seed, so it may live in a
        # path the strip removes.
        (self.seed / ".claude" / "expected.txt").write_text(
            "AGENTS.md\nCLAUDE.md\ncalc.py\n", encoding="utf-8")
        fixture = self.fixture(objective_checks=[
            {"id": "listing", "type": "dir_listing_matches", "paths": ["."],
             "ignore": [".git"], "expected_file": ".claude/expected.txt"}])
        results = objective.run_checks(fixture, str(self.stripped_workspace()),
                                       str(self.seed))
        self.assertEqual([(r["id"], r["passed"]) for r in results],
                         [("listing", True)], results)

    def test_a_real_change_to_a_kept_file_still_fails(self):
        ws = self.stripped_workspace()
        (ws / "AGENTS.md").write_text("## Repo-specific additions\n\nEdited.\n",
                                      encoding="utf-8")
        results = objective.run_checks(self.fixture(), str(ws), str(self.seed))
        kept = next(r for r in results if r["id"] == "kept")
        self.assertFalse(kept["passed"])
        self.assertIn("AGENTS.md: modified", kept["detail"])

    def test_without_the_key_the_seed_is_compared_as_is(self):
        fixture = self.fixture()
        del fixture["strip_agent_context"]
        ws = self.workspace(FIXED)
        results = objective.run_checks(
            dict(fixture, objective_checks=[
                {"id": "fleet", "type": "files_unchanged",
                 "paths": [".claude/settings.json", "skills.lock", "AGENTS.md"]}]),
            str(ws), str(self.seed))
        self.assertTrue(results[0]["passed"], results)

    def test_the_stripped_copy_is_removed_and_the_seed_is_untouched(self):
        before = sorted(p.relative_to(self.seed).as_posix() for p in self.seed.rglob("*"))
        scratch = self.root / "tmp"
        scratch.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(scratch)):
            objective.run_checks(self.fixture(), str(self.stripped_workspace()),
                                 str(self.seed))
        self.assertEqual(list(scratch.iterdir()), [])
        after = sorted(p.relative_to(self.seed).as_posix() for p in self.seed.rglob("*"))
        self.assertEqual(before, after)
        self.assertIn(".claude/settings.json", after)


class GuidanceObjectiveOnlyStripTests(_Base):
    """`subject: guidance` with `--arm objective-only` strips like every arm."""

    def write_fixture(self, checks):
        write_fleet_seed(self.seed)
        fixture = {"subject": "guidance", "section": "toy-section",
                   "strip_agent_context": True, "objective_checks": checks}
        (self.fixture_dir / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")

    def main(self):
        out = io.StringIO()
        args = ["run_eval.py", str(self.fixture_dir), "--arm", "objective-only",
                "--results-dir", str(self.root / "results")]
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(out):
            code = run_eval.main()
        return code, out.getvalue()

    def test_the_scored_workspace_carries_no_fleet_context(self):
        self.write_fixture([
            {"id": "no-fleet-dir", "type": "dir_listing_matches", "paths": ["."],
             "expected": ["AGENTS.md", "CLAUDE.md", "calc.py"]},
            {"id": "kept", "type": "files_unchanged",
             "paths": ["AGENTS.md", "CLAUDE.md", "calc.py"]}])
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertEqual([(c["id"], c["passed"]) for c in json.loads(out)["checks"]],
                         [("no-fleet-dir", True), ("kept", True)])

    def test_a_seed_the_strip_cannot_clean_fails_with_exit_2(self):
        self.write_fixture([{"id": "kept", "type": "files_unchanged",
                             "paths": ["AGENTS.md"]}])
        (self.seed / "AGENTS.md").write_text(
            "## Repo-specific additions\n\nQuoting: BEGIN MANAGED SECTION\n",
            encoding="utf-8")
        code, out = self.main()
        self.assertEqual(code, 2, out)
        self.assertIn("seed still carries agent context", out)
        self.assertNotIn('"checks"', out)


class DepsTests(_Base):
    def fake_npm(self, exit_code=0):
        bin_dir = self.root / "fake-bin"
        bin_dir.mkdir(exist_ok=True)
        npm = bin_dir / "npm"
        npm.write_text("#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$@\" > \"$WORKSPACE/npm-argv\"\n"
                       f"echo installed > \"$WORKSPACE/deps-marker\"\nexit {exit_code}\n",
                       encoding="utf-8")
        npm.chmod(0o700)
        return {"PATH": f"{bin_dir}:/usr/bin:/bin"}

    def test_a_missing_lockfile_is_a_named_failure(self):
        ws = self.workspace(FIXED)
        (ws / "e2e").mkdir()
        (ws / "e2e" / "package.json").write_text("{}", encoding="utf-8")
        error = run_eval.run_setup(ws, {"deps": [{"manager": "npm", "dir": "e2e"}],
                                        "env": self.fake_npm()})
        self.assertEqual(error["error"], "deps_failed")
        self.assertIn("package-lock.json", error["detail"])
        self.assertIn("lockfile", error["detail"])
        self.assertFalse((ws / "npm-argv").exists(), "npm must not run without a lockfile")

    def test_npm_ci_installs_from_the_lockfile_before_setup(self):
        ws = self.workspace(FIXED)
        (ws / "e2e").mkdir()
        (ws / "e2e" / "package-lock.json").write_text("{}", encoding="utf-8")
        error = run_eval.run_setup(ws, {"deps": [{"manager": "npm", "dir": "e2e"}],
                                        "env": self.fake_npm(),
                                        "setup": "test -f deps-marker"})
        self.assertIsNone(error)
        lines = (ws / "npm-argv").read_text(encoding="utf-8").split()
        self.assertEqual(Path(lines[0]).resolve(), (ws / "e2e").resolve())
        self.assertEqual(lines[1:], ["ci", "--ignore-scripts", "--no-audit", "--no-fund"])

    def test_an_npm_failure_fails_setup_by_name(self):
        ws = self.workspace(FIXED)
        (ws / "package-lock.json").write_text("{}", encoding="utf-8")
        error = run_eval.run_setup(ws, {"deps": [{"manager": "npm"}],
                                        "env": self.fake_npm(exit_code=3)})
        self.assertEqual(error["error"], "deps_failed")
        self.assertIn("npm ci exited 3", error["detail"])

    def test_deps_and_strip_are_validated_at_fixture_load(self):
        bad = [
            {"strip_agent_context": "yes"},
            {"deps": [{"manager": "pip"}]},
            {"deps": [{"manager": "npm", "dir": "../x"}]},
            {"deps": [{"manager": "npm", "dir": "/abs"}]},
            {"deps": [{"manager": "npm", "lockfile": "x"}]},
            {"deps": []},
            {"deps": "npm"},
            {"deps": [{"manager": "npm"}], "subject": "guidance"},
            {"objective_checks": [{"id": "h", "type": "repo_tests", "overlay": "seed",
                                   "argv": ["python3"], "fail_to_pass": [ADD]}]},
        ]
        for fixture in bad:
            with self.subTest(fixture=fixture), self.assertRaises(guidance.GuidanceError):
                seed_prep.validate_fixture(fixture, self.fixture_dir)
        seed_prep.validate_fixture(
            {"strip_agent_context": True, "deps": [{"manager": "npm", "dir": "e2e"}]},
            self.fixture_dir)


class BundlerDepsTests(_Base):
    """A bundler entry installs the directory's Gemfile.lock, frozen, into
    vendor/bundle beside it, and the scorer reads the install through GEM_HOME."""

    def fake_bundle(self, exit_code=0):
        bin_dir = self.root / "fake-bin"
        bin_dir.mkdir(exist_ok=True)
        bundle = bin_dir / "bundle"
        bundle.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$@\" > \"$WORKSPACE/bundle-argv\"\n"
            "env | grep -E '^(BUNDLE_|GEM_HOME)' | sort > \"$WORKSPACE/bundle-env\"\n"
            f"exit {exit_code}\n", encoding="utf-8")
        bundle.chmod(0o700)
        return {"PATH": f"{bin_dir}:/usr/bin:/bin"}

    def gemfiles(self, ws: Path, directory: str = "site") -> Path:
        site = ws / directory
        site.mkdir(parents=True, exist_ok=True)
        (site / "Gemfile").write_text('source "https://rubygems.org"\n', encoding="utf-8")
        (site / "Gemfile.lock").write_text("GEM\n", encoding="utf-8")
        return site

    def test_bundle_install_is_frozen_into_vendor_bundle_beside_the_gemfile(self):
        ws = self.workspace(FIXED)
        site = self.gemfiles(ws)
        error = run_eval.run_setup(ws, {"deps": [{"manager": "bundler", "dir": "site"}],
                                        "env": self.fake_bundle()})
        self.assertIsNone(error)
        lines = (ws / "bundle-argv").read_text(encoding="utf-8").split()
        self.assertEqual(Path(lines[0]).resolve(), site.resolve())
        self.assertEqual(lines[1:], ["install", "--quiet"])
        env = dict(line.split("=", 1) for line in
                   (ws / "bundle-env").read_text(encoding="utf-8").splitlines())
        self.assertEqual(env["BUNDLE_GEMFILE"], str(site.resolve() / "Gemfile"))
        self.assertEqual(env["BUNDLE_PATH"], "vendor/bundle")
        self.assertEqual(env["BUNDLE_FROZEN"], "true")
        self.assertEqual(env["BUNDLE_DISABLE_SHARED_GEMS"], "true")
        self.assertNotIn("GEM_HOME", env)
        # Bundler's own config and cache never land in the workspace.
        self.assertFalse(Path(env["BUNDLE_APP_CONFIG"]).is_relative_to(ws.resolve()))
        self.assertFalse((site / ".bundle").exists())

    def test_a_missing_gemfile_lock_is_a_named_failure_and_bundle_never_runs(self):
        ws = self.workspace(FIXED)
        (ws / "site").mkdir()
        (ws / "site" / "Gemfile").write_text("", encoding="utf-8")
        error = run_eval.run_setup(ws, {"deps": [{"manager": "bundler", "dir": "site"}],
                                        "env": self.fake_bundle()})
        self.assertEqual(error["error"], "deps_failed")
        self.assertIn("Gemfile.lock", error["detail"])
        self.assertFalse((ws / "bundle-argv").exists())

    def test_a_bundle_failure_and_a_preexisting_bundle_path_fail_by_name(self):
        ws = self.workspace(FIXED)
        self.gemfiles(ws)
        error = run_eval.run_setup(ws, {"deps": [{"manager": "bundler", "dir": "site"}],
                                        "env": self.fake_bundle(exit_code=5)})
        self.assertEqual(error["error"], "deps_failed")
        self.assertIn("bundle install exited 5", error["detail"])
        (ws / "site" / "vendor" / "bundle").mkdir(parents=True)
        error = run_eval.run_setup(ws, {"deps": [{"manager": "bundler", "dir": "site"}],
                                        "env": self.fake_bundle()})
        self.assertEqual(error["error"], "deps_failed")
        self.assertIn("reserved vendor/bundle path", error["detail"])

    def test_only_one_bundler_entry_is_accepted(self):
        with self.assertRaisesRegex(guidance.GuidanceError, "only one bundler entry"):
            seed_prep._deps_entries([{"manager": "bundler", "dir": "a"},
                                     {"manager": "bundler", "dir": "b"}])
        self.assertEqual(seed_prep._deps_entries([{"manager": "bundler"}]),
                         [{"manager": "bundler", "dir": "."}])

    def test_bundler_environment_names_the_one_installed_gem_home(self):
        ws = self.workspace(FIXED)
        site = self.gemfiles(ws)
        fixture = {"deps": [{"manager": "bundler", "dir": "site"}]}
        env = seed_prep.bundler_environment(ws, fixture)
        self.assertEqual(env["BUNDLE_GEMFILE"], str(site.resolve() / "Gemfile"))
        self.assertNotIn("GEM_HOME", env, "no install yet")
        (site / "vendor" / "bundle" / "ruby" / "3.3.0").mkdir(parents=True)
        env = seed_prep.bundler_environment(ws, fixture)
        self.assertEqual(env["GEM_HOME"], str(site.resolve() / "vendor" / "bundle" / "ruby" / "3.3.0"))
        self.assertEqual(seed_prep.bundler_environment(ws, {"deps": [{"manager": "npm"}]}), {})

    def test_the_scorer_rejects_a_gem_home_outside_the_workspace(self):
        ws = self.workspace(FIXED)
        self.gemfiles(ws)
        passed, detail = repo_tests.repo_tests(
            str(ws), [], overlay="checker", argv=["ruby", "spec.rb"], fail_to_pass=[["-n", "x"]],
            seed=str(self.seed), _bundler_env={"GEM_HOME": str(self.root / "elsewhere"),
                                               "BUNDLE_GEMFILE": str(ws / "site" / "Gemfile")})
        self.assertFalse(passed)
        self.assertEqual(detail, "repo_tests_invalid: the bundler GEM_HOME is outside the workspace")


class PythonDepsTests(_Base):
    PINS = ["pytest==9.0.3", "pluggy==1.6.0"]

    def fixture(self):
        return {"deps": [{"manager": "pip", "requirements": self.PINS}],
                "objective_checks": [{"id": "python", "type": "repo_tests",
                                      "overlay": "checker", "argv": ["python3", "-m", "pytest", "-q"],
                                      "fail_to_pass": ["test_calc.py::test_venv"]}]}

    def synthetic_venv(self, ws, pytest=False):
        # A real interpreter symlink + venv config, without ensurepip/network.
        venv = ws / seed_prep.PYTHON_DIR
        (venv / "bin").mkdir(parents=True)
        python = commands._fixed_interpreter("python3")
        (venv / "bin" / "python3").symlink_to(python)
        (venv / "pyvenv.cfg").write_text(
            f"home = {Path(python).parent}\ninclude-system-site-packages = false\n", encoding="utf-8")
        version = subprocess.run(
            [python, "-I", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        site = venv / "lib" / f"python{version}" / "site-packages"
        site.mkdir(parents=True)
        (site / "fixture_dependency.py").write_text("INSTALLED = True\n", encoding="utf-8")
        if pytest:
            for name in ("pytest", "_pytest", "pluggy", "packaging", "pygments", "iniconfig", "py"):
                spec = importlib.util.find_spec(name)
                if name == "py" and spec is None:
                    # Older pytest versions import its standalone compatibility module.
                    continue
                self.assertIsNotNone(spec, f"runner dependency {name} must be installed")
                if spec.submodule_search_locations is None:
                    shutil.copyfile(spec.origin, site / (name + ".py"))
                    continue
                source = Path(importlib.util.find_spec(name).origin).parent
                shutil.copytree(source, site / name)
        return venv

    def test_exact_complete_pins_are_validated_and_copied(self):
        pins = ["pytest==9.0.3", "demo_pkg==1.0rc2"]
        entry = {"manager": "pip", "requirements": pins}
        self.assertEqual(seed_prep._deps_entries([entry]), [entry])
        self.assertIsNot(seed_prep._deps_entries([entry])[0]["requirements"], pins)
        maximum = {"manager": "pip", "requirements": [f"package{i}==1" for i in range(64)]}
        self.assertEqual(seed_prep._deps_entries([maximum]), [maximum])
        invalid = [None, [], ["pytest"], ["pytest>=9"], ["pytest==9.*"],
                   ["-r local.txt"], ["https://example.com/pkg.whl"], ["./pkg"],
                   ["pytest[extra]==9.0"], ["pytest==9.0; python_version>'3'"],
                   ["pytest==9.0\n--index-url=https://example.com"], [7],
                   ["Demo_pkg==1.0", "demo-pkg==2.0"],
                   [f"package{i}==1" for i in range(65)]]
        for requirements in invalid:
            with self.subTest(requirements=requirements), self.assertRaises(guidance.GuidanceError):
                seed_prep._deps_entries([{"manager": "pip", "requirements": requirements}])
        for value in ([dict(entry, dir=".")], [entry, entry]):
            with self.subTest(value=value), self.assertRaises(guidance.GuidanceError):
                seed_prep._deps_entries(value)

    def test_install_uses_clean_environment_exact_pins_and_bounded_children(self):
        ws = self.workspace(FIXED)
        calls = []
        def install(argv, cwd, env, stdout, stderr, timeout):
            calls.append((argv, cwd, env, timeout))
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("PIP_INDEX_URL", env)
            self.assertNotIn("PRIVATE_VALUE", env)
            self.assertNotIn(str(ws), env["PATH"])
            if "venv" in argv:
                self.synthetic_venv(ws)
            else:
                requirement_file = Path(argv[-1])
                self.assertEqual(requirement_file.read_text().splitlines(), self.PINS)
            return 0
        with mock.patch.object(commands, "_run_command", side_effect=install):
            self.assertIsNone(seed_prep.install_deps(
                ws, self.fixture(), {"PATH": "/unsafe", "PYTHONPATH": "/unsafe",
                                     "PIP_INDEX_URL": "https://example.com", "PRIVATE_VALUE": "private"}, 17))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], [commands._fixed_interpreter("python3"), "-I", "-m", "venv",
                                     str(ws / seed_prep.PYTHON_DIR)])
        self.assertEqual(calls[1][0][:-1], [str(ws / seed_prep.PYTHON_DIR / "bin" / "python3"),
            "-I", "-m", "pip", "--isolated", "install", "--no-deps", "--only-binary=:all:",
            "--disable-pip-version-check", "--no-input", "--requirement"])
        self.assertEqual([call[3] for call in calls], [17, 17])
        self.assertTrue(all(call[1] == ws for call in calls))

    def test_install_failure_and_timeout_are_named_without_output(self):
        for failure in (3, subprocess.TimeoutExpired(["pip"], 17), OSError("private")):
            for step in (0, 1):
                ws = self.workspace(FIXED)
                count = 0
                def install(argv, cwd, env, stdout, stderr, timeout):
                    nonlocal count
                    stderr.write(b"do-not-publish-this-marker")
                    current, count = count, count + 1
                    if current == step:
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    self.synthetic_venv(ws)
                    return 0
                with self.subTest(failure=type(failure).__name__, step=step), \
                        mock.patch.object(commands, "_run_command", side_effect=install):
                    error = seed_prep.install_deps(ws, self.fixture(), {}, 17)
                self.assertEqual(error["error"], "deps_failed")
                self.assertNotIn("marker", error["detail"])
                self.assertNotIn("private", error["detail"])
                self.assertEqual(count, step + 1)

    def test_preexisting_environment_paths_are_refused_before_spawn(self):
        for kind in ("directory", "file", "symlink"):
            ws = self.workspace(FIXED)
            target = ws / seed_prep.PYTHON_DIR
            if kind == "directory":
                target.mkdir()
            elif kind == "file":
                target.write_text("keep")
            else:
                target.symlink_to(self.root / "missing")
            with mock.patch.object(commands, "_run_command") as spawn:
                error = seed_prep.install_deps(ws, self.fixture(), {}, 17)
            self.assertEqual(error["error"], "deps_failed")
            spawn.assert_not_called()
            self.assertTrue(os.path.lexists(target))

    def test_scorer_runs_pytest_in_venv_with_clean_env_and_no_install(self):
        ws = self.workspace(FIXED)
        self.synthetic_venv(ws, pytest=True)
        (self.fixture_dir / "checker" / "test_calc.py").write_text(
            "import os, sys, shutil\nimport fixture_dependency\n"
            "def test_venv():\n"
            "    assert sys.prefix.endswith('.fixture-python')\n"
            "    assert fixture_dependency.INSTALLED\n"
            "    assert 'PYTHONPATH' not in os.environ\n"
            "    assert 'PIP_INDEX_URL' not in os.environ\n"
            "    assert shutil.which('python3').startswith(sys.prefix)\n"
            "    assert not shutil.which('claude').startswith(sys.prefix)\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/unsafe", "PIP_INDEX_URL": "https://example.com"}), \
                mock.patch.object(seed_prep, "install_deps") as install:
            result = objective.run_checks(self.fixture(), str(ws), str(self.seed))[0]
        self.assertTrue(result["passed"], result["detail"])
        install.assert_not_called()

    def test_missing_venv_fails_closed_and_private_flag_is_not_a_fixture_key(self):
        ws = self.workspace(FIXED)
        result = objective.run_checks(self.fixture(), str(ws), str(self.seed))[0]
        self.assertFalse(result["passed"])
        self.assertIn("not an allowed executable", result["detail"])
        fixture = self.fixture()
        fixture["objective_checks"][0]["_python_deps"] = True
        with self.assertRaises(ValueError):
            objective.run_checks(fixture, str(ws), str(self.seed))

    def test_scorer_refuses_external_interpreter_and_symlinked_config(self):
        for kind in ("python", "config", "bin", "root"):
            ws = self.workspace(FIXED)
            venv = self.synthetic_venv(ws)
            if kind == "python":
                (venv / "bin" / "python3").unlink()
                other = self.root / "external-python"
                other.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                other.chmod(0o700)
                (venv / "bin" / "python3").symlink_to(other)
            elif kind == "config":
                other = self.root / "external-config"
                (venv / "pyvenv.cfg").rename(other)
                (venv / "pyvenv.cfg").symlink_to(other)
            else:
                path = venv / "bin" if kind == "bin" else venv
                other = self.root / f"external-{kind}"
                path.rename(other)
                path.symlink_to(other, target_is_directory=True)
            with self.subTest(kind=kind), mock.patch.object(commands, "_run_command") as spawn:
                result = objective.run_checks(self.fixture(), str(ws), str(self.seed))[0]
            self.assertFalse(result["passed"])
            spawn.assert_not_called()

    def test_reserved_seed_and_overlay_paths_are_refused_at_load(self):
        for location in (self.seed, self.fixture_dir / "checker"):
            path = location / seed_prep.PYTHON_DIR
            path.mkdir()
            (path / "payload").write_text("reserved", encoding="utf-8")
            with self.subTest(location=location.name), self.assertRaises(guidance.GuidanceError):
                seed_prep.validate_fixture(self.fixture(), self.fixture_dir)
            shutil.rmtree(path)


class ObjectiveOnlyRunTests(_Base):
    """The whole path through run_eval.main(): red on the seed, green fixed."""

    def write_fixture(self, **extra):
        write_fleet_seed(self.seed)
        fixture = {"skill": "toy-real-work", "prompt": "Fix add().",
                   "strip_agent_context": True,
                   "objective_checks": [{
                       "id": "hidden-tests", "type": "repo_tests",
                       "overlay": "checker", "argv": ["python3", "-m", "unittest"],
                       "fail_to_pass": [ADD], "pass_to_pass": [SUB]}]}
        fixture.update(extra)
        (self.fixture_dir / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")

    def main(self, *argv):
        out = io.StringIO()
        args = ["run_eval.py", str(self.fixture_dir), "--arm", "objective-only",
                "--results-dir", str(self.root / "results"), *map(str, argv)]
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(out):
            code = run_eval.main()
        return code, out.getvalue()

    def test_seed_is_red_and_the_fixed_workspace_is_green(self):
        self.write_fixture()
        code, out = self.main()
        self.assertEqual(code, 1, out)
        self.assertIn("fail_to_pass=0/1 pass_to_pass=1/1", out)
        code, out = self.main("--workspace", self.workspace(FIXED))
        self.assertEqual(code, 0, out)
        self.assertIn("fail_to_pass=1/1 pass_to_pass=1/1", out)

    def test_an_invalid_repo_tests_check_is_refused_before_scoring(self):
        self.write_fixture(objective_checks=[{
            "id": "hidden-tests", "type": "repo_tests", "overlay": "checker",
            "argv": ["python3"], "fail_to_pass": [ADD], "timeout_s": 600}])
        code, out = self.main()
        self.assertEqual(code, 2, out)
        self.assertIn("fixture configuration error", out)
        self.assertIn("timeout_s", out)


if __name__ == "__main__":
    unittest.main()
