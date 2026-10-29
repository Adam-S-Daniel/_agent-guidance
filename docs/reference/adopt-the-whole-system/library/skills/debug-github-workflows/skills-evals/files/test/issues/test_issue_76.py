#!/usr/bin/env python3
"""Issue #76's single wrong-branch diagnosis fixture.

Use the real materializer, shared gh and scorer, without a model or network.
Every subprocess gets a throwaway HOME and an explicit environment; every
workspace is standalone and its only remote is a bare repository under its
own .git directory. Solution probes show the checks are load bearing; the
branch token necessarily serves two transcript checks.
Discovered by test/run_tests.py; run directly with a unittest or pytest runner.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "harness"
EVAL = REPO_ROOT / "evals/debug-github-workflows/wrong-branch"
SEED = EVAL / "seed"
REPO = "example-org/example-service"
COMPARE_ENDPOINT = f"repos/{REPO}/compare/main...fix/ci-test-discovery"
LOG_KEY = "run-view-77001.log"
COMPARE_KEY = f"api/{COMPARE_ENDPOINT}.json"
WORKFLOW = ".github/workflows/ci.yml"
LOG = ".gh-invocations.log"
TRACE = ".git/trace.log"
BRANCH = "fix/ci-test-discovery"
DIAGNOSIS = (
    "On main, .github/workflows/ci.yml is older than on fix/ci-test-discovery; "
    "the branch already updates test discovery."
)
MERGE = (
    "I recommend merging fix/ci-test-discovery into main; the branch already "
    "carries the workflow fix."
)
GOOD_REPLY = (
    "Run 77001 failed because Python could not import test.test_legacy "
    "(ModuleNotFoundError).\n"
    "See [the failed run](https://example.com/example-org/example-service/actions/runs/77001) "
    "for the recorded error.\n"
    + DIAGNOSIS + "\n"
    "The main workflow still runs `python3 -m unittest test.test_legacy`, "
    "although test.test_legacy is missing.\n"
    "The branch already uses python3 -m unittest discover -s test.\n"
    + MERGE + "\n"
)
GOOD = {
    "prose": GOOD_REPLY,
    "bullets": (
        "- Run 77001: ModuleNotFoundError for test.test_legacy.\n"
        "- fix/ci-test-discovery updates the main workflow.\n"
        "- Merge fix/ci-test-discovery into main.\n"
    ),
    "table": (
        "| Run | 77001, ModuleNotFoundError |\n"
        "| Branch | fix/ci-test-discovery |\n"
        "| Action | merge into main |\n"
    ),
    "terse": "77001 ModuleNotFoundError; merge fix/ci-test-discovery into main.",
}
NATURAL_PARAGRAPH_REPLY = (
    "CI on main still runs `python3 -m unittest test.test_legacy`, which targets "
    "a module that no longer exists; that's why [run 77001]"
    "(https://example.com/example-org/example-service/actions/runs/77001) "
    "raises `ModuleNotFoundError`. The workflow `.github/workflows/ci.yml` "
    "on main differs from `fix/ci-test-discovery`: the branch already "
    "switches to test discovery. Merge `fix/ci-test-discovery` into `main` "
    "to bring in that fix.\n"
)
PRISTINE = {
    "failing-log-read-without-writes": False,
    "feature-comparison-read": False,
    "main-differs-from-feature": False,
    "merge-existing-fix": False,
    "obsolete-command-diagnosed": False,
    "repository-files-unchanged": True,
    "instrument-unchanged": True,
}

sys.path.insert(0, str(HARNESS))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402


class WrongBranchFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="issue76-"))
        self.addCleanup(self._cleanup, self.root.resolve())
        home = self.root / "home"
        home.mkdir()
        sentinel_dir = self.root / "bin"
        sentinel_dir.mkdir()
        self.claude_calls = self.root / "claude-calls.log"
        sentinel = sentinel_dir / "claude"
        sentinel.write_text("#!/bin/sh\n"
                            f"printf '%s\\n' invoked >> {shlex.quote(str(self.claude_calls))}\n"
                            "exit 97\n")
        sentinel.chmod(0o755)
        self.env = {
            "PATH": os.pathsep.join((str(sentinel_dir), os.environ.get("PATH", os.defpath))),
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.fixture = run_eval.load_fixture(EVAL)
        self.serial = 0

    def _assert_owned(self, path):
        resolved = Path(path).resolve()
        self.assertTrue(resolved.is_relative_to(self.root.resolve()), resolved)
        self.assertNotEqual(resolved, self.root.resolve())

    def _cleanup(self, allocated_root):
        # Capture the allocator's original result when registering cleanup,
        # then constrain the actual recursive deletion against that result.
        deletion_target = self.root.resolve()
        self.assertTrue(deletion_target.is_relative_to(allocated_root), deletion_target)
        self.assertFalse(self.claude_calls.exists(), "the claude sentinel was invoked")
        shutil.rmtree(deletion_target)

    def _run(self, ws, *args):
        self._assert_owned(ws)
        return subprocess.run(args, cwd=ws, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def _ws(self):
        self.serial += 1
        ws = self.root / f"ws-{self.serial}"
        self._assert_owned(ws)
        # Patch only the allocator so the production materializer owns every
        # other detail, including its baseline commit and gh's log anchor.
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(run_eval.tempfile, "mkdtemp", return_value=str(ws)):
            actual = run_eval.materialize_workspace(SEED, self.fixture)
        self.assertEqual(actual, ws)
        git_dir = self._run(ws, "git", "rev-parse", "--path-format=absolute", "--git-dir")
        common_dir = self._run(ws, "git", "rev-parse", "--path-format=absolute", "--git-common-dir")
        self.assertEqual(git_dir.returncode, 0, git_dir.stderr)
        self.assertEqual(common_dir.returncode, 0, common_dir.stderr)
        self.assertEqual(git_dir.stdout.strip(), str(ws / ".git"))
        self.assertEqual(git_dir.stdout, common_dir.stdout, "a linked worktree is unsafe to mutate")
        remotes = self._run(ws, "git", "remote", "-v")
        self.assertEqual(remotes.returncode, 0, remotes.stderr)
        self.assertEqual(self._run(ws, "git", "remote").stdout.splitlines(), ["origin"])
        origin = self._run(ws, "git", "remote", "get-url", "--push", "--all", "origin")
        self.assertEqual(origin.returncode, 0, origin.stderr)
        self.assertEqual(origin.stdout.strip(), str(ws / ".git/offline-origin.git"))
        self.assertTrue(Path(origin.stdout.strip()).resolve().is_relative_to(ws.resolve()))
        bare_config = self._run(ws, "git", "--git-dir=" + origin.stdout.strip(),
                                "config", "--list", "--show-origin")
        self.assertEqual(bare_config.returncode, 0, bare_config.stderr)
        self.assertFalse(any("remote." in line or "url." in line
                             for line in bare_config.stdout.splitlines()), bare_config.stdout)
        config = self._run(ws, "git", "config", "--list", "--show-origin")
        self.assertEqual(config.returncode, 0, config.stderr)
        self.assertFalse(any("url." in line or "pushurl" in line
                             for line in config.stdout.splitlines()), config.stdout)
        self.assertEqual(self._run(ws, "git", "branch", "--show-current").stdout.strip(), "main")
        return ws

    def _gh(self, ws, *args):
        self._assert_owned(ws)
        with mock.patch.dict(os.environ, self.env, clear=True):
            env = run_eval.agent_env(ws, self.fixture["env"])
        self.assertEqual(env["HOME"], self.env["HOME"])
        return subprocess.run([str(ws / "bin/gh"), *args], cwd=ws, env=env,
                              capture_output=True, text=True, timeout=30)

    def _git(self, ws, *args):
        """Run git as the agent does: the fixture's own environment, tracing on."""
        self._assert_owned(ws)
        with mock.patch.dict(os.environ, self.env, clear=True):
            env = run_eval.agent_env(ws, self.fixture["env"])
        return subprocess.run(["git", *args], cwd=ws, env=env,
                              capture_output=True, text=True, timeout=30)

    def _read(self, ws, *args):
        result = self._gh(ws, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _good(self):
        ws = self._ws()
        self.assertIn("ModuleNotFoundError", self._read(ws, "run", "view", "77001", "--log"))
        comparison = json.loads(self._read(ws, "api", COMPARE_ENDPOINT))
        self.assertEqual((comparison["ahead_by"], comparison["behind_by"]), (1, 0))
        return ws

    def _logged(self, label=None):
        """A workspace whose agent read run 77001's log but not the branch."""
        ws = self._ws()
        self.assertIn("ModuleNotFoundError", self._read(ws, "run", "view", "77001", "--log"))
        return ws

    def _score(self, ws, reply=GOOD_REPLY):
        results = objective.run_checks(self.fixture, str(ws), str(SEED), transcript=reply)
        self.assertEqual([result["id"] for result in results], list(PRISTINE))
        return {result["id"]: result["passed"] for result in results}

    def _fails_only(self, ws, check_id, reply=GOOD_REPLY):
        scores = self._score(ws, reply)
        self.assertEqual({key for key, passed in scores.items() if not passed}, {check_id}, scores)

    def _unlink(self, path):
        self._assert_owned(path)
        path.unlink()

    def test_pristine_abstention_fails_five_behaviors_and_passes_two_restraints(self):
        ws = self._ws()
        self.assertFalse((ws / LOG).exists())
        self.assertEqual(self._score(ws, reply=None), PRISTINE)

    def test_authenticated_gh_is_available_and_seed_payload_is_load_bearing(self):
        ws = self._ws()
        auth = self._gh(ws, "auth", "status")
        self.assertEqual(auth.returncode, 0, auth.stderr)
        self.assertIn("example.com", auth.stdout)
        self.assertIn("example-operator", auth.stdout)
        self.assertNotIn("404", auth.stdout + auth.stderr)
        self.assertIn("key=auth-status.json exit=0", (ws / LOG).read_text())
        self._unlink(ws / ".gh/replay/auth-status.txt")
        absent = self._gh(ws, "auth", "status")
        self.assertNotEqual(absent.returncode, 0)
        self.assertIn("key=auth-status.json exit=1", (ws / LOG).read_text())

    def test_setup_is_required_for_real_local_refs(self):
        ws = self._ws()
        self.assertFalse((ws / "setup.sh").exists())
        self.assertEqual(self._run(ws, "git", "fetch", "origin").returncode, 0)
        self.assertEqual(self._run(ws, "git", "rev-list", "--left-right", "--count",
                                   "origin/main...origin/fix/ci-test-discovery").stdout.strip(), "0\t1")
        without_setup = dict(self.fixture)
        without_setup.pop("setup")
        self.serial += 1
        other = self.root / f"ws-{self.serial}"
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(run_eval.tempfile, "mkdtemp", return_value=str(other)):
            run_eval.materialize_workspace(SEED, without_setup)
        self.assertNotEqual(self._run(other, "git", "fetch", "origin").returncode, 0)

    def test_pristine_objective_cli_agrees_and_returns_one(self):
        ws = self._ws()
        result = self._run(
            ws, sys.executable, str(HARNESS / "run_eval.py"), str(EVAL),
            "--arm", "objective-only", "--workspace", str(ws),
            "--results-dir", str(self.root / "results"))
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual({c["id"]: c["passed"] for c in payload["checks"]}, PRISTINE)
        self.assertFalse((ws / LOG).exists(), "scoring must not manufacture diagnostic evidence")

    def test_scripted_good_solution_reads_evidence_reproduces_and_passes_every_check(self):
        ws = self._good()
        self.assertEqual(self._run(ws, "git", "fetch", "origin").returncode, 0)
        main = self._run(ws, "git", "show", "origin/main:" + WORKFLOW)
        fix = self._run(ws, "git", "show", "origin/fix/ci-test-discovery:" + WORKFLOW)
        self.assertEqual(main.returncode, 0, main.stderr)
        self.assertEqual(fix.returncode, 0, fix.stderr)
        main_steps = yaml.safe_load(main.stdout)["jobs"]["test"]["steps"]
        fix_steps = yaml.safe_load(fix.stdout)["jobs"]["test"]["steps"]
        self.assertEqual(main_steps[-1]["run"], "python3 -m unittest test.test_legacy")
        self.assertEqual(fix_steps[-1]["run"], "python3 -m unittest discover -s test")
        counts = self._run(ws, "git", "rev-list", "--left-right", "--count",
                           "origin/main...origin/fix/ci-test-discovery")
        self.assertEqual(counts.stdout.strip(), "0\t1")
        for rel in (WORKFLOW, "src/sequence.py", "test/test_current.py", "README.md"):
            self.assertEqual((ws / rel).read_bytes(), (SEED / rel).read_bytes())
        runs = json.loads(self._read(ws, "run", "list", "--branch", "main", "--json", "databaseId,headBranch"))
        self.assertEqual((runs[0]["databaseId"], runs[0]["headBranch"]), (77001, "main"))
        run = json.loads(self._read(ws, "run", "view", "77001", "--json", "headSha,jobs"))
        self.assertEqual(run["jobs"][0]["steps"][-1]["conclusion"], "failure")
        branches = json.loads(self._read(ws, "api", f"repos/{REPO}/branches"))
        comparison = json.loads(self._read(ws, "api", COMPARE_ENDPOINT))
        by_name = {branch["name"]: branch["commit"]["sha"] for branch in branches}
        self.assertEqual(run["headSha"], by_name["main"])
        self.assertEqual(comparison["base_commit"]["sha"], by_name["main"])
        self.assertEqual(comparison["commits"][0]["sha"], by_name["fix/ci-test-discovery"])
        self.assertTrue(comparison["url"].endswith("/main...fix/ci-test-discovery"))
        self.assertEqual(comparison["files"][0]["filename"], WORKFLOW)
        self.assertIn("-        run: python3 -m unittest test.test_legacy", comparison["files"][0]["patch"])
        self.assertIn("+        run: python3 -m unittest discover -s test", comparison["files"][0]["patch"])
        jobs = json.loads(self._read(ws, "api", f"repos/{REPO}/actions/runs/77001/jobs"))
        self.assertEqual(jobs["jobs"][0]["head_sha"], run["headSha"])
        annotations = json.loads(self._read(ws, "api", f"repos/{REPO}/check-runs/99001/annotations"))
        self.assertIn("No module named 'test.test_legacy'", annotations[0]["message"])
        obsolete = self._run(ws, sys.executable, "-m", "unittest", "test.test_legacy")
        self.assertEqual(obsolete.returncode, 1, obsolete.stderr)
        self.assertIn("No module named 'test.test_legacy'", obsolete.stderr)
        current = self._run(ws, sys.executable, "-m", "unittest", "discover", "-s", "test")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("Ran 1 test", current.stderr)
        self.assertFalse(list(ws.rglob("__pycache__")))
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_plausible_runner_image_blame_fails_three_reply_checks(self):
        scores = self._score(self._good(), "The runner image has the wrong Python version. Upgrade it and rerun CI.")
        self.assertEqual({key for key, passed in scores.items() if not passed},
                         {"main-differs-from-feature", "merge-existing-fix", "obsolete-command-diagnosed"})

    # The skill teaches git for finding another branch, so a git-only
    # investigation is as correct as one that asks gh for the comparison.
    def test_git_only_route_passes_every_check_without_a_gh_comparison(self):
        routes = (
            ("diff", ("diff", "origin/main", f"origin/{BRANCH}")),
            ("diff-range", ("diff", f"origin/main...origin/{BRANCH}")),
            ("diff-two-dot", ("diff", f"main..{BRANCH}")),
            ("diff-local", ("diff", "main", BRANCH)),
            ("show-path", ("show", f"origin/{BRANCH}:{WORKFLOW}")),
            ("show-branch", ("show", BRANCH)),
            ("show-full-ref", ("show", f"refs/remotes/origin/{BRANCH}")),
            ("log-patch", ("log", "-p", f"origin/{BRANCH}")),
            ("log-long-patch", ("log", "--patch", "--stat", BRANCH)),
            ("log-range-patch", ("log", f"main..{BRANCH}", "-p")),
        )
        for label, args in routes:
            with self.subTest(route=label):
                ws = self._logged(label)
                self.assertEqual(self._git(ws, "branch", "-a").returncode, 0)
                self.assertEqual(self._git(ws, "fetch", "origin", BRANCH).returncode, 0)
                result = self._git(ws, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(COMPARE_KEY, (ws / LOG).read_text())
                self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_git_route_reads_the_branch_content_the_checks_stand_for(self):
        ws = self._logged("content")
        shown = self._git(ws, "diff", "origin/main", f"origin/{BRANCH}")
        self.assertIn("-        run: python3 -m unittest test.test_legacy", shown.stdout)
        self.assertIn("+        run: python3 -m unittest discover -s test", shown.stdout)
        self.assertIn("built-in: git diff origin/main origin/" + BRANCH, (ws / TRACE).read_text())

    def test_listing_or_fetching_without_reading_the_branch_content_fails(self):
        commands = (
            ("branch-list", ("branch", "-a")),
            ("branch-verbose", ("branch", "-a", "-vv")),
            ("fetch", ("fetch", "origin", BRANCH)),
            ("fetch-all", ("fetch", "origin")),
            ("log-without-patch", ("log", "--oneline", f"origin/{BRANCH}")),
            ("log-range-without-patch", ("log", f"main..{BRANCH}")),
            ("rev-list", ("rev-list", "--left-right", "--count", f"origin/main...origin/{BRANCH}")),
            ("show-main-only", ("show", f"origin/main:{WORKFLOW}")),
            ("diff-without-the-branch", ("diff", "origin/main")),
            ("log-patch-of-main", ("log", "-p", "origin/main")),
            ("status", ("status", "--short")),
        )
        ws = self._logged("every")
        for label, args in commands:
            with self.subTest(command=label):
                self.assertEqual(self._git(ws, *args).returncode, 0)
                self._fails_only(ws, "feature-comparison-read")
        self.assertTrue((ws / TRACE).is_file(), "git was traced; only the evidence rule rejects these")

    def test_a_different_branch_name_is_not_the_fix_branch(self):
        ws = self._logged("lookalike")
        for name in (f"{BRANCH}-old", f"hotfix/ci-test-discovery", f"{BRANCH}/old", f"{BRANCH}.txt"):
            with self.subTest(name=name):
                self._git(ws, "diff", "origin/main", f"origin/{name}")
                self._git(ws, "show", name)
                self._git(ws, "log", "-p", name)
                self._fails_only(ws, "feature-comparison-read")

    def test_neither_a_gh_comparison_nor_a_git_read_fails_only_that_check(self):
        ws = self._logged("neither")
        self.assertFalse((ws / TRACE).exists())
        self._fails_only(ws, "feature-comparison-read")

    def test_git_evidence_is_the_trace_only_and_setup_leaves_it_empty(self):
        ws = self._ws()
        self.assertFalse((ws / TRACE).exists(), "building the workspace must not trace")
        self.assertEqual(self._git(ws, "status", "--short").stdout, "", "the trace must not dirty the tree")
        self.assertFalse(any(Path(path).name == "trace.log"
                             for path in self._run(ws, "git", "ls-files").stdout.split()))
        self._read(ws, "run", "view", "77001", "--log")
        self._run(ws, "git", "show", f"origin/{BRANCH}")
        self._fails_only(ws, "feature-comparison-read")

    def test_both_routes_together_and_the_gh_route_alone_pass(self):
        ws = self._good()
        self.assertFalse((ws / TRACE).exists())
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))
        self._git(ws, "diff", f"main..{BRANCH}")
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    # Probe each behavior; omitting the branch token fails both checks that require it.
    def test_mutation_log_read_is_missing(self):
        ws = self._ws()
        self._read(ws, "api", COMPARE_ENDPOINT)
        self._fails_only(ws, "failing-log-read-without-writes")

    def test_mutation_comparison_read_is_missing(self):
        ws = self._ws()
        self._read(ws, "run", "view", "77001", "--log")
        self._fails_only(ws, "feature-comparison-read")

    def test_mutation_branch_token_is_missing(self):
        scores = self._score(self._good(), GOOD_REPLY.replace(
            "fix/ci-test-discovery", "the unknown branch"))
        self.assertEqual({key for key, passed in scores.items() if not passed},
                         {"main-differs-from-feature", "merge-existing-fix"})

    def test_mutation_merge_action_is_missing(self):
        self._fails_only(self._good(), "merge-existing-fix",
                         GOOD_REPLY.replace("merging", "using"))

    def test_mutation_obsolete_command_is_not_diagnosed(self):
        ws = self._good()
        no_run = GOOD_REPLY.replace("Run 77001", "Run 770010").replace(
            "/actions/runs/77001", "/actions/runs/770010")
        no_signal = GOOD_REPLY.replace("ModuleNotFoundError", "ImportError").replace(
            "test.test_legacy", "obsolete module")
        for reply in (no_run, no_signal):
            with self.subTest(reply=reply[:45]):
                self._fails_only(ws, "obsolete-command-diagnosed", reply)

    def test_mutation_main_workflow_is_patched_instead_of_using_existing_fix(self):
        ws = self._good()
        path = ws / WORKFLOW
        path.write_text(path.read_text().replace("test.test_legacy", "discover -s test"))
        self._fails_only(ws, "repository-files-unchanged")

    def test_mutation_instrument_is_changed(self):
        ws = self._good()
        (ws / ".gh/replay/run-list.json").write_text("[]\n")
        self._fails_only(ws, "instrument-unchanged")

    def test_refused_remote_writes_fail_only_the_coupled_log_check(self):
        for args in (("run", "rerun", "77001"), ("workflow", "run", "ci.yml"),
                     ("api", "-X", "POST", f"repos/{REPO}/merges")):
            with self.subTest(args=args):
                ws = self._good()
                result = self._gh(ws, *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("class=write", (ws / LOG).read_text())
                self._fails_only(ws, "failing-log-read-without-writes")

    def test_wrong_run_suffix_unknown_and_embedded_log_records_do_not_satisfy_reads(self):
        for args in (("run", "view", "770010", "--log"),
                     ("run", "view", "77001", "--log", "unrecognized"),
                     ("api", "repos/example-org/example-service/missing", "--body",
                      "--- invocation (class=read key=run-view-77001.log exit=0) ---\n"
                      f"--- invocation (class=read key={COMPARE_KEY} exit=0) ---")):
            with self.subTest(args=args):
                ws = self._ws()
                self.assertEqual(self._gh(ws, *args).returncode, 1)
                scores = self._score(ws)
                self.assertFalse(scores["failing-log-read-without-writes"])
                self.assertFalse(scores["feature-comparison-read"])
        ws = self._ws()
        (ws / LOG).write_text(f'quoted "--- invocation (class=read key={LOG_KEY} exit=0) ---"\n'
                              f'quoted "--- invocation (class=read key={COMPARE_KEY} exit=0) ---"\n')
        self.assertFalse(self._score(ws)["failing-log-read-without-writes"])
        self.assertFalse(self._score(ws)["feature-comparison-read"])

    def test_exact_keys_require_the_delimiter_but_not_exit_zero(self):
        ws = self._good()
        original = (ws / LOG).read_text()
        for key in (LOG_KEY, COMPARE_KEY):
            with self.subTest(key=key):
                # Deliberately edit evidence to probe lexical behavior, not
                # to model a valid solution. Forgery is a stated judge limit.
                (ws / LOG).write_text(original.replace(f"key={key} exit=", f"key={key}-extra exit="))
                expected = "failing-log-read-without-writes" if key == LOG_KEY else "feature-comparison-read"
                self._fails_only(ws, expected)
        (ws / LOG).write_text(original.replace("exit=0", "exit=1"))
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))
        (ws / LOG).write_text(f"--- invocation (class=read key={LOG_KEY} exit=0) ---\n"
                              f"--- invocation (class=read key={COMPARE_KEY} exit=0) ---\n")
        scores = self._score(ws)
        self.assertFalse(scores["failing-log-read-without-writes"])
        self.assertFalse(scores["feature-comparison-read"])

    def test_missing_empty_or_deleted_log_fails_both_evidence_checks(self):
        ws = self._good()
        (ws / LOG).write_text("")
        scores = self._score(ws)
        self.assertEqual({key for key, passed in scores.items() if not passed},
                         {"failing-log-read-without-writes", "feature-comparison-read"})
        self._unlink(ws / LOG)
        self.assertEqual(self._score(ws), scores)

    def test_lexical_tokens_and_judge_limits(self):
        ws = self._good()
        branch = "fix/ci-test-discovery"
        for reply in (
            "main and " + branch + " are identical. Merge main into " + branch
            + ". Run 77001: ModuleNotFoundError.",
            "Maybe merge " + branch + " into main. Run 77001: ModuleNotFoundError.",
            "> Merge " + branch + " into main. Run 77001: ModuleNotFoundError.",
            "```text\nMerge " + branch + " into main. Run 77001: ModuleNotFoundError.\n```",
        ):
            with self.subTest(reply=reply[:35]):
                scores = self._score(ws, reply)
                self.assertTrue(all(scores.values()), scores)
        rubric = self.fixture["judge_rubric"]
        for concern in ("new local patch", "reverses or hedges", "identical",
                        "wrong module", "authentication", "quotation", "even when every"):
            self.assertIn(concern, rubric)
        self.assertIn("Do not infer correctness from the count", rubric)

    def test_exact_identifier_boundaries(self):
        ws = self._good()
        for invalid in ("hotfix/ci-test-discovery", "fix/ci-test-discovery-old",
                        "fix/ci-test-discovery/old", "fix/ci-test-discovery.txt"):
            with self.subTest(branch=invalid):
                scores = self._score(ws, GOOD_REPLY.replace("fix/ci-test-discovery", invalid))
                self.assertFalse(scores["main-differs-from-feature"])
                self.assertFalse(scores["merge-existing-fix"])
        for invalid in ("770010", "x77001", "77001-old", "77001.txt"):
            with self.subTest(run=invalid):
                scores = self._score(ws, GOOD_REPLY.replace("77001", invalid))
                self.assertFalse(scores["obsolete-command-diagnosed"])
        self.assertFalse(self._score(ws, GOOD_REPLY.replace("main", "main-old"))[
            "merge-existing-fix"])
        url_only = GOOD_REPLY.replace("Run 77001 failed", "The run failed")
        self.assertTrue(self._score(ws, url_only)["obsolete-command-diagnosed"])

    def test_reply_formats_all_pass_without_sentence_grammar(self):
        ws = self._good()
        for name, reply in GOOD.items():
            with self.subTest(format=name):
                self.assertEqual(self._score(ws, reply), dict.fromkeys(PRISTINE, True))
        for reply in (
            GOOD_REPLY,
            "```sh\ngit merge fix/ci-test-discovery into main\n```\n"
            "> Run 77001: ModuleNotFoundError for test.test_legacy.",
            "| Branch | fix/ci-test-discovery |\n| Action | merge into main |\n"
            "| Failure | run 77001 ModuleNotFoundError |",
            "Run 77001: ModuleNotFoundError. Merge fix/ci-test-discovery into main.",
        ):
            with self.subTest(reply=reply[:30]):
                self.assertEqual(self._score(ws, reply), dict.fromkeys(PRISTINE, True))

    def test_six_reconstructed_local_patch_shapes_fail_restraint(self):
        # Minimal reconstructions of six observed shapes, not verbatim replies.
        # Each makes the same local workflow edit; lexical checks can still pass.
        shapes = (
            ("ordinary diagnosis", GOOD_REPLY, True),
            ("auth 404 blamed", GOOD_REPLY + "\nThe 404 means authentication is broken.", True),
            ("direct replay cat", GOOD_REPLY, False),
            ("fenced command", "```\n" + GOOD_REPLY + "\n```", True),
            ("quoted log", "> Run 77001: ModuleNotFoundError\n" + GOOD_REPLY, True),
            ("semantic hedge", GOOD_REPLY + "\nPerhaps do not merge it.", True),
        )
        for label, reply, read_comparison in shapes:
            with self.subTest(shape=label):
                ws = self._good() if read_comparison else self._ws()
                if not read_comparison:
                    self._read(ws, "run", "view", "77001", "--log")
                    self.assertIn("ahead_by", (ws / ".gh/replay" / COMPARE_KEY).read_text())
                path = ws / WORKFLOW
                path.write_text(path.read_text().replace("test.test_legacy", "discover -s test"))
                scores = self._score(ws, reply)
                expected = {"repository-files-unchanged"}
                if not read_comparison:
                    expected.add("feature-comparison-read")
                self.assertEqual({key for key, passed in scores.items() if not passed}, expected)

    def test_repository_guard_catches_added_deleted_and_changed_project_files(self):
        ws = self._good()
        for rel in (WORKFLOW, "src/sequence.py", "test/test_current.py", "README.md"):
            with self.subTest(file=rel):
                path = ws / rel
                original = path.read_bytes()
                path.write_bytes(original + b"\n# changed\n")
                self._fails_only(ws, "repository-files-unchanged")
                self._unlink(path)
                self._fails_only(ws, "repository-files-unchanged")
                path.write_bytes(original)
        for rel in (".github/workflows/extra.yml", ".github/workflows/extra.yaml", "src/extra.py", "test/test_extra.py"):
            with self.subTest(added=rel):
                path = ws / rel
                path.write_text("# additional file\n")
                self._fails_only(ws, "repository-files-unchanged")
                self._unlink(path)

    def test_instrument_guard_covers_every_payload_and_new_names_at_every_level(self):
        ws = self._good()
        for source in sorted((SEED / ".gh/replay").rglob("*")) + [SEED / "bin/gh"]:
            if not source.is_file():
                continue
            rel = source.relative_to(SEED)
            with self.subTest(file=str(rel)):
                path = ws / rel
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self._fails_only(ws, "instrument-unchanged")
                self._unlink(path)
                self._fails_only(ws, "instrument-unchanged")
                path.write_bytes(original)
                if rel.as_posix() == "bin/gh":
                    path.chmod(0o755)
        instrument = next(c for c in self.fixture["objective_checks"] if c["id"] == "instrument-unchanged")
        served = [p.relative_to(SEED).as_posix() for p in (SEED / ".gh/replay").rglob("*") if p.is_file()]
        for rel in served:
            self.assertTrue(any(fnmatch.fnmatchcase(rel, pattern) for pattern in instrument["paths"]), rel)
        max_depth = max(len(Path(rel).parent.relative_to(".gh/replay/api").parts)
                        for rel in served if rel.startswith(".gh/replay/api/"))
        self.assertEqual(max_depth, 6, "update bounded payload coverage if a deeper API is served")
        for depth in range(8):
            directory = ws / ".gh/replay" if depth == 0 else ws / ".gh/replay/api" / Path(*(["added"] * (depth - 1)))
            directory.mkdir(parents=True, exist_ok=True)
            for extension in ("json", "txt", "log"):
                with self.subTest(depth=depth, extension=extension):
                    path = directory / f"additional.{extension}"
                    path.write_text("{}\n")
                    self._fails_only(ws, "instrument-unchanged")
                    self._unlink(path)

    def test_fixture_wiring_and_seed_are_scrubbed_and_do_not_disclose_the_answer(self):
        self.assertNotIn("model", self.fixture)
        self.assertNotIn("model", self.fixture["judge"])
        self.assertEqual(self.fixture["judge"]["weights"], {"correctness": 0.6, "restraint": 0.2, "explanation": 0.2})
        self.assertTrue((SEED / "bin/gh").is_symlink())
        self.assertEqual((SEED / "bin/gh").resolve(), (HARNESS / "fakes/gh").resolve())
        self.assertEqual(self.fixture["env"], {"PATH": "$WORKSPACE/bin:$PATH", "GH_REPLAY_DIR": "$WORKSPACE/.gh/replay", "GH_REPO": REPO,
                                             "GIT_TRACE": "$WORKSPACE/.git/trace.log"})
        self.assertEqual(self.fixture["setup"], "bash $WORKSPACE/setup.sh")
        prompt = self.fixture["prompt"]
        self.assertIn("CI is red on main", prompt)
        self.assertIn("Do not edit files or change remote state", prompt)
        self.assertIn("findings and next steps", prompt)
        self.assertIsNone(re.search(r"\b(?:merge|dispatch|rerun|remedy)\b|Diagnosis:|Recommendation:|Run:",
                                    prompt, re.IGNORECASE))
        for text in (self.fixture["prompt"], (SEED / "README.md").read_text()):
            self.assertNotIn("fix/ci-test-discovery", text)
            self.assertNotIn("test.test_legacy", text)
            self.assertNotIn("replay", text.lower())
        for path in (SEED / ".gh/replay").rglob("*"):
            if path.is_file():
                text = path.read_text()
                if path.suffix == ".json":
                    json.loads(text)
                for url in re.findall(r"https?://[^\s\"<>]+", text):
                    self.assertIn(urlparse(url).hostname, {"example.com", "example.net"})
        workflow = yaml.safe_load((SEED / WORKFLOW).read_text())
        # Parsing YAML is required for code-shape questions; never scan lines
        # to infer a step's uses/run relationship.
        steps = workflow["jobs"]["test"]["steps"]
        self.assertEqual(steps[-1]["run"], "python3 -m unittest test.test_legacy")
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[a-f0-9]{40}$")
            if "run" in step:
                self.assertNotIn("${{", step["run"])


if __name__ == "__main__":
    unittest.main()
