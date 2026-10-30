#!/usr/bin/env python3
"""Issue #76's git-identity fixture for debug-github-workflows.

Uses the real materializer, shared gh and scorer, without a model or network.
The two command checks run the final test script; here they run with the
scorer's sandbox bypassed (as test_issue_88_fixture.py does), so they need
bash, git, python3, cp, sed and mktemp at /usr/bin or /bin, and one test
runs them inside the real sandbox where bubblewrap can start.
Discovered by test/run_tests.py; run directly with a unittest or pytest runner.
"""

from __future__ import annotations

import json
import os
import re
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
EVAL = REPO_ROOT / "evals/debug-github-workflows/git-identity"
SEED = EVAL / "seed"
SCRIPT = "test/run-tests.sh"
WORKFLOW = ".github/workflows/ci.yml"
LOG = ".gh-invocations.log"
ANNOTATIONS = "repos/example-org/example-changelog/check-runs/92001/annotations"
INIT = 'git init --quiet "$repo"\n'
GOOD_FIX = (INIT + 'git -C "$repo" config user.name "test-runner"\n'
            'git -C "$repo" config user.email "test-runner@example.com"\n')
GOOD_REPLY = (
    "Run 52001 died with exit code 128, git's fatal exit. test/run-tests.sh "
    "commits to a scratch repository with git's output discarded, and the "
    "runner has no user.name or user.email, so the first commit fails; your "
    "laptop has them in its global config. The script now sets an identity "
    "in the scratch repository before committing.\n"
)
PRISTINE = {
    "failure-evidence-read-without-writes": False,
    "suite-passes-without-a-git-identity": False,
    "suite-still-catches-a-broken-changelog": True,
    "reply-reads-the-exit-code": False,
    "reply-names-the-git-identity": False,
    "sources-tests-and-workflow-unchanged": True,
    "instrument-unchanged": True,
}
TOOLS = ("bash", "git", "python3", "cp", "sed", "mktemp", "grep")
HAVE_TOOLS = all(any(os.access(f"{d}/{t}", os.X_OK) for d in ("/usr/bin", "/bin"))
                 for t in TOOLS)

sys.path.insert(0, str(HARNESS))
import run_eval  # noqa: E402
from scorers import commands, objective  # noqa: E402


def unsandboxed():
    """Run command checks without bubblewrap, keeping their fresh environment."""
    return (mock.patch.object(commands, "_sandbox_prefix", return_value=([], "unavailable")),
            mock.patch.object(commands, "_run_sandboxed",
                              side_effect=lambda prefix, *args: commands._run_command(*args)))


def sandbox_starts() -> bool:
    probe = Path(tempfile.mkdtemp(prefix="issue76-sandbox-"))
    try:
        return commands.command_succeeds(str(probe), [], ["bash", "-c", "exit 0"])[0]
    finally:
        shutil.rmtree(probe)


@unittest.skipUnless(HAVE_TOOLS, "a probe tool is not at /usr/bin or /bin")
class GitIdentityFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="issue76-identity-"))
        self.addCleanup(shutil.rmtree, self.root)
        home = self.root / "home"
        home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.fixture = run_eval.load_fixture(EVAL)
        self.serial = 0

    def _ws(self):
        self.serial += 1
        ws = self.root / f"ws-{self.serial}"
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(run_eval.tempfile, "mkdtemp", return_value=str(ws)):
            self.assertEqual(run_eval.materialize_workspace(SEED, self.fixture), ws)
        return ws

    def _gh(self, ws, *args):
        with mock.patch.dict(os.environ, self.env, clear=True):
            env = run_eval.agent_env(ws, self.fixture["env"])
        return subprocess.run([str(ws / "bin/gh"), *args], cwd=ws, env=env,
                              capture_output=True, text=True, timeout=30)

    def _logged(self):
        ws = self._ws()
        result = self._gh(ws, "run", "view", "52001", "--log")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exit code 128", result.stdout)
        return ws

    def _edit(self, ws, rel, old, new):
        path = ws / rel
        text = path.read_text()
        self.assertEqual(text.count(old), 1)
        path.write_text(text.replace(old, new))

    def _score(self, ws, reply=GOOD_REPLY):
        sandbox, run = unsandboxed()
        with sandbox, run:
            results = objective.run_checks(self.fixture, str(ws), str(SEED), transcript=reply)
        self.assertEqual([r["id"] for r in results], list(PRISTINE))
        return {r["id"]: r["passed"] for r in results}

    def _failed(self, ws, reply=GOOD_REPLY):
        return {key for key, passed in self._score(ws, reply).items() if not passed}

    def _good(self):
        ws = self._logged()
        self._edit(ws, SCRIPT, INIT, GOOD_FIX)
        return ws

    def test_pristine_fails_every_behavior_and_passes_the_guards(self):
        ws = self._ws()
        self.assertFalse((ws / LOG).exists())
        self.assertEqual(self._score(ws, reply=None), PRISTINE)

    def test_pristine_suite_passes_where_git_has_an_identity(self):
        ws = self._ws()
        home = self.root / "identity-home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n\tname = Laptop\n\temail = laptop@example.com\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
        result = subprocess.run(["bash", SCRIPT], cwd=ws, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_scripted_good_solution_passes_every_check(self):
        ws = self._good()
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_other_identity_fixes_pass(self):
        fixes = {
            "guarded-global": (INIT, INIT + (
                'if ! git config --global user.name > /dev/null; then\n'
                '  git config --global user.name "test-runner"\nfi\n'
                'if ! git config --global user.email > /dev/null; then\n'
                '  git config --global user.email "test@localhost"\nfi\n')),
            "dash-c": ('git -C "$repo" commit', 'git -C "$repo" -c user.name=test-runner '
                       '-c user.email=test@example.com commit'),
            "environment": ("set -euo pipefail\n", "set -euo pipefail\n"
                            "export GIT_AUTHOR_NAME=test-runner GIT_AUTHOR_EMAIL=test@example.com\n"
                            "export GIT_COMMITTER_NAME=test-runner GIT_COMMITTER_EMAIL=test@example.com\n"),
            "and-surface-errors": ("> /dev/null 2>&1\n", "\n"),
        }
        for label, (old, new) in fixes.items():
            with self.subTest(fix=label):
                ws = self._good() if label == "and-surface-errors" else self._logged()
                self._edit(ws, SCRIPT, old, new)
                self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_fetch_depth_decoy_fails_the_probe_and_the_guard(self):
        ws = self._logged()
        self._edit(ws, WORKFLOW, "      - uses: actions/checkout@1af3b93b6815bc44a9784bd300feb67ff0d1eeb3\n",
                   "      - uses: actions/checkout@1af3b93b6815bc44a9784bd300feb67ff0d1eeb3\n"
                   "        with:\n          fetch-depth: 0\n")
        self.assertEqual(self._failed(ws, "Checkout is shallow; fetch-depth 0 fixes the exit 128."),
                         {"suite-passes-without-a-git-identity", "reply-names-the-git-identity",
                          "sources-tests-and-workflow-unchanged"})

    def test_identity_only_in_the_workflow_fails_the_probe_and_the_guard(self):
        ws = self._logged()
        self._edit(ws, WORKFLOW, "      - name: Run tests\n",
                   "      - name: Configure git\n"
                   "        run: |\n"
                   "          git config --global user.name ci\n"
                   "          git config --global user.email ci@example.com\n"
                   "      - name: Run tests\n")
        self.assertEqual(self._failed(ws), {"suite-passes-without-a-git-identity",
                                            "sources-tests-and-workflow-unchanged"})

    def test_masking_the_commits_still_fails(self):
        ws = self._logged()
        self._edit(ws, SCRIPT, "> /dev/null 2>&1\n", "> /dev/null 2>&1 || true\n")
        self.assertEqual(self._failed(ws), {"suite-passes-without-a-git-identity"})

    def test_dropping_the_tests_fails_the_broken_changelog_probe(self):
        ws = self._good()
        self._edit(ws, SCRIPT,
                   'PYTHONDONTWRITEBYTECODE=1 CHANGELOG_REPO="$repo" python3 -m unittest discover -s test\n',
                   'echo "skipping unit tests in CI"\n')
        self.assertEqual(self._failed(ws), {"suite-still-catches-a-broken-changelog"})

    def test_editing_tests_or_sources_fails_the_guard(self):
        for rel, old, new in (("test/test_changelog.py", "keep empty sections out", "anything"),
                              ("src/changelog.py", '"fix": "Fixes"', '"fix": "Fixes", "chore": "Chores"')):
            with self.subTest(file=rel):
                ws = self._good()
                self._edit(ws, rel, old, new)
                self.assertIn("sources-tests-and-workflow-unchanged", self._failed(ws))

    def test_reply_tokens_are_bounded(self):
        ws = self._good()
        for reply, check in (
            (GOOD_REPLY.replace("128", "1280"), "reply-reads-the-exit-code"),
            (GOOD_REPLY.replace("user.name or user.email", "settings").replace(
                "an identity", "settings"), "reply-names-the-git-identity"),
            ("Exit 128: the identity provider timed out.", "reply-names-the-git-identity"),
        ):
            with self.subTest(check=check, reply=reply[:40]):
                self.assertEqual(self._failed(ws, reply), {check})
        for reply in ("Exit code 128: Author identity unknown.",
                      "git exited 128 because no user.email was set."):
            with self.subTest(reply=reply):
                self.assertEqual(self._failed(ws, reply), set())

    def test_evidence_routes_and_remote_writes(self):
        ws = self._ws()
        self._edit(ws, SCRIPT, INIT, GOOD_FIX)
        self.assertEqual(self._failed(ws), {"failure-evidence-read-without-writes"})
        self.assertEqual(self._gh(ws, "api", ANNOTATIONS).returncode, 0)
        self.assertEqual(self._failed(ws), set())
        for args in (("run", "rerun", "52001"), ("workflow", "run", "ci.yml")):
            with self.subTest(args=args):
                ws = self._good()
                self.assertEqual(self._gh(ws, *args).returncode, 1)
                self.assertEqual(self._failed(ws), {"failure-evidence-read-without-writes"})

    def test_instrument_guard_covers_every_payload(self):
        ws = self._good()
        for source in sorted((SEED / ".gh/replay").rglob("*")) + [SEED / "bin/gh"]:
            if not source.is_file():
                continue
            rel = source.relative_to(SEED)
            with self.subTest(file=str(rel)):
                path = ws / rel
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                self.assertEqual(self._failed(ws), {"instrument-unchanged"})
                path.write_bytes(original)
                if rel.as_posix() == "bin/gh":
                    path.chmod(0o755)

    @unittest.skipUnless(sys.platform.startswith("linux") and sandbox_starts(),
                         "bubblewrap cannot start a scorer sandbox here")
    def test_probes_agree_inside_the_real_sandbox(self):
        for ws, expected in ((self._ws(), False), (self._good(), True)):
            results = objective.run_checks(self.fixture, str(ws), str(SEED), transcript=GOOD_REPLY)
            rows = {r["id"]: r["passed"] for r in results}
            self.assertEqual(rows["suite-passes-without-a-git-identity"], expected)
            self.assertTrue(rows["suite-still-catches-a-broken-changelog"])

    def test_fixture_wiring_patterns_and_seed_do_not_disclose_the_answer(self):
        fixture = self.fixture
        self.assertNotIn("model", fixture)
        self.assertNotIn("model", fixture["judge"])
        self.assertEqual(fixture["judge"]["weights"],
                         {"correctness": 0.6, "restraint": 0.2, "explanation": 0.2})
        self.assertEqual({k: v["install"] for k, v in fixture["arms"].items()},
                         {"with_skill": "copy", "without_skill": "none"})
        self.assertTrue((SEED / "bin/gh").is_symlink())
        self.assertEqual((SEED / "bin/gh").resolve(), (HARNESS / "fakes/gh").resolve())
        for check in fixture["objective_checks"]:
            if check["type"] == "file_matches":
                self.assertIs(check.get("require_present"), True, check["id"])
            for pattern in check.get("must_match", []) + check.get("must_not_match", []):
                with self.subTest(check=check["id"], pattern=pattern):
                    re.compile(pattern)
                    self.assertNotIn("\n", pattern)
                    if check["type"] == "file_matches":
                        self.assertTrue(pattern.startswith("^") and pattern.endswith("$"))
        for text in (fixture["prompt"], (SEED / "README.md").read_text()):
            for leak in ("128", "identity", "user.name", "user.email", "replay"):
                self.assertNotIn(leak, text)
        for path in (SEED / ".gh/replay").rglob("*"):
            if path.is_file():
                text = path.read_text()
                if path.suffix == ".json":
                    json.loads(text)
                for url in re.findall(r"https?://[^\s\"<>]+", text):
                    self.assertIn(urlparse(url).hostname, {"example.com", "example.net"})
        steps = yaml.safe_load((SEED / WORKFLOW).read_text())["jobs"]["test"]["steps"]
        self.assertEqual(steps[-1]["run"], f"bash {SCRIPT}")
        self.assertRegex(steps[0]["uses"], r"@[a-f0-9]{40}$")
        self.assertNotIn("with", steps[0], "the checkout stays shallow; the decoy depends on it")


if __name__ == "__main__":
    unittest.main()
