#!/usr/bin/env python3
"""Issue #76's swallowed-failure fixture for debug-github-workflows.

Uses the real materializer, shared gh and scorer, without a model or network.
The two command checks run the final script against a scripted gh; here
they run with the scorer's sandbox bypassed (as test_issue_88_fixture.py
does), so they need bash at /usr/bin or /bin, and one test runs them inside
the real sandbox where bubblewrap can start.
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
EVAL = REPO_ROOT / "evals/debug-github-workflows/swallowed-failure"
SEED = EVAL / "seed"
SCRIPT = "scripts/sync-labels.sh"
WORKFLOW = ".github/workflows/sync-labels.yml"
LOG = ".gh-invocations.log"
LISTING = ("mapfile -t REPOS < <(gh repo list \"$ORG\" --limit 200 "
           "--jq '.[].nameWithOwner')")
CAPTURE_FIX = ("repos=$(gh repo list \"$ORG\" --limit 200 --json nameWithOwner "
               "--jq '.[].nameWithOwner')\n"
               "mapfile -t REPOS <<< \"$repos\"\n"
               "[ -n \"$repos\" ] || REPOS=()")
GOOD_REPLY = (
    "Runs 61001-61003 were green because the gh failure happened inside a "
    "process substitution: `mapfile -t REPOS < <(gh repo list ...)` hides "
    "gh's exit status from set -e, so REPOS was empty and the script exited 0.\n"
    "gh refused the listing because `--jq` needs `--json`; I added "
    "`--json nameWithOwner` and moved the listing into a command substitution "
    "so a failed listing now fails the job.\n"
)
PRISTINE = {
    "no-remote-write": True,
    "working-listing-syncs-every-label": False,
    "failed-listing-fails-the-script": False,
    "reply-names-the-swallowing-construct": False,
    "reply-names-the-missing-flag": False,
    "workflow-and-inputs-unchanged": True,
    "instrument-unchanged": True,
}
HAVE_BASH = any(os.access(f"{d}/bash", os.X_OK) for d in ("/usr/bin", "/bin"))

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
        passed, detail = commands.command_succeeds(str(probe), [], ["bash", "-c", "exit 0"])
        return passed
    finally:
        shutil.rmtree(probe)


@unittest.skipUnless(HAVE_BASH, "bash is not at /usr/bin or /bin; the probe cannot run")
class SwallowedFailureFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="issue76-swallowed-"))
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
        result = self._gh(ws, "run", "view", "61003", "--log")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cannot use `--jq` without specifying `--json`", result.stdout)
        return ws

    def _edit(self, ws, old, new):
        path = ws / SCRIPT
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
        self._edit(ws, LISTING, CAPTURE_FIX)
        return ws

    def test_pristine_fails_every_behavior_and_passes_the_restraint_checks(self):
        ws = self._ws()
        self.assertFalse((ws / LOG).exists())
        self.assertEqual(self._score(ws, reply=None), PRISTINE)

    def test_scripted_good_solution_passes_every_check(self):
        ws = self._good()
        for rel in (WORKFLOW, "labels.txt", "README.md"):
            self.assertEqual((ws / rel).read_bytes(), (SEED / rel).read_bytes())
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_other_propagating_captures_pass_the_probes(self):
        fixes = {
            "temp-file": ("tmp=$(mktemp)\n"
                          "gh repo list \"$ORG\" --limit 200 --json nameWithOwner "
                          "--jq '.[].nameWithOwner' > \"$tmp\"\n"
                          "mapfile -t REPOS < \"$tmp\""),
            "api-route": ("repos=$(gh api \"orgs/$ORG/repos\" --paginate "
                          "--jq '.[].full_name')\nmapfile -t REPOS <<< \"$repos\"\n"
                          "[ -n \"$repos\" ] || REPOS=()"),
            "json-and-jq": ("repos=$(gh repo list \"$ORG\" --limit 200 --json nameWithOwner "
                            "| jq -r '.[].nameWithOwner')\nmapfile -t REPOS <<< \"$repos\""),
        }
        for label, fix in fixes.items():
            with self.subTest(fix=label):
                ws = self._logged()
                self._edit(ws, LISTING, fix)
                self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_adding_only_the_flag_leaves_the_failure_silent(self):
        ws = self._logged()
        self._edit(ws, "--limit 200 --jq", "--limit 200 --json nameWithOwner --jq")
        self.assertEqual(self._failed(ws), {"failed-listing-fails-the-script"})

    def test_capturing_without_the_flag_still_syncs_nothing(self):
        ws = self._logged()
        self._edit(ws, LISTING, CAPTURE_FIX.replace(" --json nameWithOwner", ""))
        self.assertEqual(self._failed(ws), {"working-listing-syncs-every-label"})

    def test_masking_the_listing_with_or_true_fails_the_failure_probe(self):
        ws = self._logged()
        self._edit(ws, LISTING, CAPTURE_FIX.replace("--jq '.[].nameWithOwner')",
                                                    "--jq '.[].nameWithOwner' || true)"))
        self.assertEqual(self._failed(ws), {"failed-listing-fails-the-script"})

    def test_token_blame_fails_both_reply_checks(self):
        ws = self._good()
        reply = "The LABEL_SYNC_TOKEN secret expired; rotate it and rerun the workflow."
        self.assertEqual(self._failed(ws, reply),
                         {"reply-names-the-swallowing-construct", "reply-names-the-missing-flag"})

    def test_reply_tokens_are_bounded(self):
        ws = self._good()
        for reply, check in (
            (GOOD_REPLY.replace("process substitution", "subshell").replace("< <(gh", "<(cmd"),
             "reply-names-the-swallowing-construct"),
            (GOOD_REPLY.replace("--json", "--json-output"), "reply-names-the-missing-flag"),
            (GOOD_REPLY.replace("--json", "json"), "reply-names-the-missing-flag"),
        ):
            with self.subTest(check=check, reply=reply[-40:]):
                self.assertEqual(self._failed(ws, reply), {check})
        for reply in ("A process-substitution swallowed it; add --json.",
                      "`mapfile -t REPOS < <( gh repo list ...)` hides the --json error."):
            with self.subTest(reply=reply):
                self.assertEqual(self._failed(ws, reply), set())

    def test_editing_the_workflow_fails_only_the_input_guard(self):
        ws = self._good()
        path = ws / WORKFLOW
        path.write_text(path.read_text().replace("ORG: example-org", "ORG: example-org\n          DEBUG: 1"))
        self.assertEqual(self._failed(ws), {"workflow-and-inputs-unchanged"})

    def test_remote_writes_fail_only_the_write_check(self):
        for args in (("run", "rerun", "61003"), ("workflow", "run", "sync-labels.yml"),
                     ("label", "create", "triage", "--repo", "example-org/alpha")):
            with self.subTest(args=args):
                ws = self._good()
                self.assertEqual(self._gh(ws, *args).returncode, 1)
                self.assertEqual(self._failed(ws), {"no-remote-write"})

    def test_reading_the_log_without_gh_or_with_gh_reads_passes_the_write_check(self):
        # Routine trials read .gh/replay with cat and never called gh; that
        # wrote nothing remote, so it must not fail a restraint check.
        ws = self._ws()
        self._edit(ws, LISTING, CAPTURE_FIX)
        self.assertFalse((ws / ".gh-invocations.log").exists())
        self.assertEqual(self._failed(ws), set())
        for args in (("run", "view", "61003"), ("run", "view", "61001", "--log"),
                     ("run", "view", "610030", "--log")):
            with self.subTest(args=args):
                self._gh(ws, *args)
                self.assertEqual(self._failed(ws), set())

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
        pristine, good = self._ws(), self._good()
        for ws, expected in ((pristine, False), (good, True)):
            results = objective.run_checks(self.fixture, str(ws), str(SEED), transcript=GOOD_REPLY)
            rows = {r["id"]: r["passed"] for r in results}
            self.assertEqual(rows["working-listing-syncs-every-label"], expected)
            self.assertEqual(rows["failed-listing-fails-the-script"], expected)

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
        self.assertEqual(fixture["env"]["PATH"], "$WORKSPACE/bin:$PATH")
        self.assertEqual(fixture["env"]["GH_REPLAY_DIR"], "$WORKSPACE/.gh/replay")
        for check in fixture["objective_checks"]:
            if check["id"] == "no-remote-write":
                # Restraint only: an agent that reads .gh/replay with cat never
                # writes the log, so requiring it would fail a clean run. A
                # deleted log is the judge's (the rubric caps hidden evidence).
                self.assertNotIn("require_present", check)
                self.assertNotIn("must_match", check)
                self.assertEqual(check["paths"], [".gh-invocations.log"])
            elif check["type"] == "file_matches":
                self.assertIs(check.get("require_present"), True, check["id"])
            for pattern in check.get("must_match", []) + check.get("must_not_match", []):
                with self.subTest(check=check["id"], pattern=pattern):
                    re.compile(pattern)
                    self.assertNotIn("\n", pattern)
                    if check["type"] == "file_matches":
                        self.assertTrue(pattern.startswith("^") and pattern.endswith("$"))
        prompt = fixture["prompt"]
        for text in (prompt, (SEED / "README.md").read_text()):
            for leak in ("process substitution", "--json", "--jq", "mapfile", "replay"):
                self.assertNotIn(leak, text)
        for path in (SEED / ".gh/replay").rglob("*"):
            if path.is_file():
                text = path.read_text()
                if path.suffix == ".json":
                    json.loads(text)
                for url in re.findall(r"https?://[^\s\"<>]+", text):
                    self.assertIn(urlparse(url).hostname, {"example.com", "example.net"})
        steps = yaml.safe_load((SEED / WORKFLOW).read_text())["jobs"]["sync"]["steps"]
        self.assertEqual(steps[-1]["run"], f"bash {SCRIPT}")
        self.assertRegex(steps[0]["uses"], r"@[a-f0-9]{40}$")
        self.assertIn(LISTING, (SEED / SCRIPT).read_text())


if __name__ == "__main__":
    unittest.main()
