#!/usr/bin/env python3
"""Issue #76's secret-name fixture for debug-github-workflows.

Uses the real materializer, shared gh and scorer, without a model or network.
Every check here reads files or the reply, so nothing runs in a sandbox.
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
EVAL = REPO_ROOT / "evals/debug-github-workflows/secret-name"
SEED = EVAL / "seed"
WORKFLOW = ".github/workflows/release-notes.yml"
LOG = ".gh-invocations.log"
SECRETS_API = "repos/example-org/example-docs/actions/secrets"
OLD = "GH_TOKEN: ${{ secrets.RELEASE_NOTES_TOKEN }}"
NEW = "GH_TOKEN: ${{ secrets.RELEASE_NOTES_PAT }}"
GOOD_REPLY = (
    "Runs 43001 and 43002 logged an empty GH_TOKEN: the workflow reads "
    "secrets.RELEASE_NOTES_TOKEN, but `gh secret list` shows the bot's token "
    "is stored as RELEASE_NOTES_PAT. A missing secret expands to an empty "
    "string, so gh asked for GH_TOKEN. I pointed GH_TOKEN at "
    "secrets.RELEASE_NOTES_PAT; gh's github.token hint does not apply, because "
    "an edit made with GITHUB_TOKEN would not start the Docs workflow.\n"
)
PRISTINE = {
    "run-log-read-without-writes": False,
    "secret-names-listed": False,
    "workflow-reads-the-existing-secret": False,
    "workflows-still-parse": True,
    "permissions-stay-read-only": True,
    "reply-names-both-secrets": False,
    "everything-else-unchanged": True,
    "instrument-unchanged": True,
}

sys.path.insert(0, str(HARNESS))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402


class SecretNameFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="issue76-secret-"))
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

    def _read(self, ws, *args):
        result = self._gh(ws, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _investigated(self):
        ws = self._ws()
        self.assertIn("  GH_TOKEN: \n", self._read(ws, "run", "view", "43002", "--log"))
        self.assertIn("RELEASE_NOTES_PAT", self._read(ws, "secret", "list"))
        return ws

    def _edit(self, ws, old, new, rel=WORKFLOW):
        path = ws / rel
        text = path.read_text()
        self.assertEqual(text.count(old), 1)
        path.write_text(text.replace(old, new))

    def _score(self, ws, reply=GOOD_REPLY):
        results = objective.run_checks(self.fixture, str(ws), str(SEED), transcript=reply)
        self.assertEqual([r["id"] for r in results], list(PRISTINE))
        return {r["id"]: r["passed"] for r in results}

    def _failed(self, ws, reply=GOOD_REPLY):
        return {key for key, passed in self._score(ws, reply).items() if not passed}

    def _good(self):
        ws = self._investigated()
        self._edit(ws, OLD, NEW)
        return ws

    def test_pristine_fails_every_behavior_and_passes_the_guards(self):
        ws = self._ws()
        self.assertFalse((ws / LOG).exists())
        self.assertEqual(self._score(ws, reply=None), PRISTINE)

    def test_scripted_good_solution_passes_every_check(self):
        ws = self._good()
        step = yaml.safe_load((ws / WORKFLOW).read_text())["jobs"]["notes"]["steps"][1]
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ secrets.RELEASE_NOTES_PAT }}")
        self.assertEqual(self._score(ws), dict.fromkeys(PRISTINE, True))

    def test_equivalent_spellings_and_the_api_route_pass(self):
        ws = self._ws()
        self._read(ws, "run", "view", "43001", "--log")
        self.assertIn("RELEASE_NOTES_PAT", self._read(ws, "api", SECRETS_API))
        self._edit(ws, OLD, "GH_TOKEN: ${{secrets.RELEASE_NOTES_PAT}}  # the bot's token")
        self.assertEqual(self._failed(ws), set())

    def test_following_ghs_hint_fails_the_secret_check(self):
        for token in ("${{ github.token }}", "${{ secrets.GITHUB_TOKEN }}"):
            with self.subTest(token=token):
                ws = self._investigated()
                self._edit(ws, OLD, f"GH_TOKEN: {token}")
                self.assertEqual(self._failed(ws), {"workflow-reads-the-existing-secret"})

    def test_following_ghs_hint_and_widening_permissions_fails_both(self):
        ws = self._investigated()
        self._edit(ws, OLD, "GH_TOKEN: ${{ github.token }}")
        self._edit(ws, "  contents: read\n", "  contents: write\n")
        self.assertEqual(self._failed(ws), {"workflow-reads-the-existing-secret",
                                            "permissions-stay-read-only"})
        ws = self._good()
        self._edit(ws, "permissions:\n  contents: read\n", "permissions: write-all\n")
        self.assertEqual(self._failed(ws), {"permissions-stay-read-only"})

    def test_keeping_the_old_name_beside_the_new_one_fails(self):
        ws = self._good()
        self._edit(ws, "          TAG:", "          OLD_TOKEN: ${{ secrets.RELEASE_NOTES_TOKEN }}\n          TAG:")
        self.assertEqual(self._failed(ws), {"workflow-reads-the-existing-secret"})

    def test_creating_the_missing_secret_is_a_refused_write(self):
        ws = self._investigated()
        result = self._gh(ws, "secret", "set", "RELEASE_NOTES_TOKEN", "--body", "placeholder")
        self.assertEqual(result.returncode, 1)
        reply = "Create the RELEASE_NOTES_TOKEN secret; RELEASE_NOTES_PAT is unrelated."
        self.assertEqual(self._failed(ws, reply), {"run-log-read-without-writes",
                                                   "workflow-reads-the-existing-secret"})

    def test_guessing_without_listing_fails_only_the_listing_check(self):
        ws = self._ws()
        self._read(ws, "run", "view", "43002", "--log")
        self._edit(ws, OLD, NEW)
        self.assertEqual(self._failed(ws), {"secret-names-listed"})

    def test_broken_yaml_fails_the_parse_check(self):
        ws = self._good()
        self._edit(ws, "        run: |\n", "        run: |\n  bad: [\n")
        self.assertIn("workflows-still-parse", self._failed(ws))

    def test_reply_tokens_are_bounded(self):
        ws = self._good()
        for reply in (GOOD_REPLY.replace("RELEASE_NOTES_TOKEN", "the old secret"),
                      GOOD_REPLY.replace("RELEASE_NOTES_PAT", "RELEASE_NOTES_PATH"),
                      GOOD_REPLY.replace("RELEASE_NOTES_PAT", "the bot secret")):
            with self.subTest(reply=reply[:60]):
                self.assertEqual(self._failed(ws, reply), {"reply-names-both-secrets"})

    def test_other_edits_fail_only_the_guard(self):
        for rel, old, new in ((".github/workflows/docs.yml", "edited", "edited, published"),
                              ("CHANGES.md", "## v1.4.0", "## v1.4.1"),
                              ("scripts/render_notes.py", "lines, keep = [], False", "lines, keep = [], True")):
            with self.subTest(file=rel):
                ws = self._good()
                self._edit(ws, old, new, rel)
                self.assertEqual(self._failed(ws), {"everything-else-unchanged"})

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
        for path in [SEED / "README.md", SEED / "CHANGES.md", *(SEED / ".github").rglob("*.yml")]:
            text = path.read_text()
            self.assertNotIn("RELEASE_NOTES_PAT", text, path)
            self.assertNotIn("replay", text.lower(), path)
        self.assertNotIn("secret", fixture["prompt"].lower())
        self.assertNotIn("token", fixture["prompt"].lower())
        for path in (SEED / ".gh/replay").rglob("*"):
            if path.is_file():
                text = path.read_text()
                if path.suffix == ".json":
                    json.loads(text)
                for url in re.findall(r"https?://[^\s\"<>]+", text):
                    self.assertIn(urlparse(url).hostname, {"example.com", "example.net"})
        workflow = yaml.safe_load((SEED / WORKFLOW).read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        step = workflow["jobs"]["notes"]["steps"][1]
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ secrets.RELEASE_NOTES_TOKEN }}")
        self.assertNotIn("${{", step["run"])
        for doc in (workflow, yaml.safe_load((SEED / ".github/workflows/docs.yml").read_text())):
            for job in doc["jobs"].values():
                for item in job["steps"]:
                    if "uses" in item:
                        self.assertRegex(item["uses"], r"@[a-f0-9]{40}$")


if __name__ == "__main__":
    unittest.main()
