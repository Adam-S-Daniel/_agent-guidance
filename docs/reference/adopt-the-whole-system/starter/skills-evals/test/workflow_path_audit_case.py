"""Shared scaffolding for the nested workflow-path-audit fixture tests.

Each `test_issue_wpa_*.py` module subclasses `FixtureCase`, names its fixture
directory, the pristine verdict of every check, a hand-written good result,
and for each check a mutation of that result which breaks only it. The tests
here then pin, for every such module:

  * the pristine seed FAILS every behavior check and PASSES every restraint
    check, read from the harness's own command line;
  * the good result passes every check, and each mutation fails exactly the
    check it is named for;
  * each module's plausible wrong solutions fail exactly the checks listed;
  * the fixture uses only check types and keys the scorer knows, pins no
    model, puts the offline `gh` in front of any real one, and asks a prompt
    no sibling fixture asks.

Every score is read from `harness/run_eval.py --arm objective-only`, never
from a private call into the scorer, in an environment built here (HOME and
TMPDIR under the test's own mkdtemp).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
SKILL_DIR = REPO_ROOT / "evals" / "workflow-path-audit"
GH_TARGET = "../../../../../harness/fakes/gh"

sys.path.insert(0, str(HARNESS_DIR))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402


def replace_once(path: Path, old: str, new: str) -> None:
    """Replace `old` with `new` in `path`, where `old` occurs exactly once,
    so an anchor that drifted out of the seed is an error, not a no-op."""
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise AssertionError(f"{path.name}: expected exactly one {old!r}, "
                             f"found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


class FixtureCase(unittest.TestCase):
    """Scoring helpers only. It has no `test_` methods, so a module that
    imports it collects nothing from it; the tests are `FixtureTests`."""

    FIXTURE: str = ""
    EXPECTED_PRISTINE: dict[str, bool] = {}

    def setUp(self):
        self.eval_dir = SKILL_DIR / self.FIXTURE
        self.seed = self.eval_dir / "seed"
        self.tmp = Path(tempfile.mkdtemp(prefix=f"wpa-{self.FIXTURE}-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(self.eval_dir)
        self.checks = {c["id"]: c for c in self.fixture["objective_checks"]}
        self._workspaces = 0

    # ---- hooks a module fills in ---------------------------------------------

    def apply_good(self, ws: Path) -> None:
        raise NotImplementedError

    def mutations(self) -> dict:
        """{check id: edit to the good result that breaks only that check}."""
        raise NotImplementedError

    def wrong_solutions(self) -> dict:
        """{label: (edit applied to a seed copy, set of check ids it fails)}."""
        raise NotImplementedError

    # ---- helpers -------------------------------------------------------------

    def ws(self) -> Path:
        """A fresh copy of the pristine seed, links resolved as the harness
        resolves them."""
        self._workspaces += 1
        ws = self.tmp / f"ws-{self._workspaces}"
        shutil.copytree(self.seed, ws)
        return ws

    def good(self) -> Path:
        ws = self.ws()
        self.apply_good(ws)
        return ws

    def score(self, ws: Path | None = None) -> tuple[int, dict[str, bool]]:
        """(exit code, {check id: passed}) from the harness's command line."""
        home, child_tmp = self.tmp / "home", self.tmp / "child-tmp"
        home.mkdir(exist_ok=True)
        child_tmp.mkdir(exist_ok=True)
        env = {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(child_tmp),
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        extra = ["--workspace", str(ws)] if ws is not None else []
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(self.eval_dir),
               "--arm", "objective-only",
               "--results-dir", str(self.tmp / "results"), *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"run_eval.py printed no JSON (exit {proc.returncode}):\n"
                      f"{proc.stdout}\n{proc.stderr}")
        self.assertEqual(payload["arm"], "objective-only")
        results = {c["id"]: c["passed"] for c in payload["checks"]}
        self.assertEqual(len(results), len(payload["checks"]), "duplicate check id")
        self.assertEqual(proc.returncode, 0 if all(results.values()) else 1,
                         "the exit code must agree with the per-check results")
        return proc.returncode, results

    def failed(self, ws: Path) -> set[str]:
        _, results = self.score(ws)
        return {check_id for check_id, passed in results.items() if not passed}


class FixtureTests:
    """The tests every workflow-path-audit fixture module runs. A mixin, so
    importing it does not collect a second, fixture-less copy."""

    def test_pristine_seed_fails_every_behavior_check_and_passes_every_restraint_check(self):
        code, results = self.score()
        self.assertEqual(code, 1)
        self.assertEqual(results, self.EXPECTED_PRISTINE)
        self.assertEqual(list(results), [c["id"] for c in self.fixture["objective_checks"]],
                         "check order drifted from the fixture's")
        self.assertIn(False, results.values())
        self.assertIn(True, results.values())

    def test_scoring_a_seed_copy_as_a_workspace_agrees_with_the_default_run(self):
        self.assertEqual(self.score(self.ws()), self.score())

    def test_hand_written_good_result_passes_every_check(self):
        code, results = self.score(self.good())
        self.assertEqual({k for k, v in results.items() if not v}, set())
        self.assertEqual(code, 0)

    def test_each_mutation_of_the_good_result_fails_only_its_own_check(self):
        mutations = self.mutations()
        self.assertEqual(set(mutations), set(self.checks),
                         "every check needs a mutation that breaks it alone")
        for check_id, mutate in mutations.items():
            with self.subTest(check=check_id):
                ws = self.good()
                mutate(ws)
                self.assertEqual(self.failed(ws), {check_id})

    def test_plausible_wrong_solutions_fail_the_checks_they_should(self):
        wrong = self.wrong_solutions()
        self.assertGreaterEqual(len(wrong), 2)
        for label, (edit, expected) in wrong.items():
            with self.subTest(solution=label):
                ws = self.ws()
                edit(ws)
                self.assertEqual(self.failed(ws), expected)

    # ---- the fixture file itself ---------------------------------------------

    def test_only_existing_check_types_and_keys_are_used(self):
        for check in self.checks.values():
            with self.subTest(check=check["id"]):
                self.assertIn(check["type"], objective.CHECKS)
                allowed = (objective._CHECK_META_KEYS
                           | objective._CHECK_ALLOWED_KEYS.get(check["type"], set()))
                self.assertEqual(set(check) - allowed, set())

    def test_no_model_is_pinned_and_both_arms_are_declared(self):
        # Arms follow the roster (ADR 0013); no reason to pin here is stated.
        self.assertNotIn("model", self.fixture)
        self.assertNotIn("model", self.fixture["judge"])
        self.assertEqual(self.fixture["skill"], "workflow-path-audit")
        self.assertEqual(self.fixture["registry"],
                         "https://github.com/example-adopter/acsi-agentskills")
        self.assertEqual(self.fixture["arms"],
                         {"with_skill": {"install": "copy"},
                          "without_skill": {"install": "none"}})

    def test_correctness_outweighs_restraint_in_the_judge_weights(self):
        weights = self.fixture["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertIn("restraint", weights)
        self.assertGreater(max(w for k, w in weights.items() if k != "restraint"),
                           weights["restraint"])

    def test_the_offline_gh_shadows_any_real_one(self):
        env = self.fixture["env"]
        self.assertEqual(env["PATH"], "$WORKSPACE/bin:$PATH")
        self.assertEqual(env["GH_REPLAY_DIR"], "$WORKSPACE/.gh/replay")
        self.assertEqual(env["GH_REPO"], "example-org/example-service")
        self.assertEqual(os.readlink(self.seed / "bin" / "gh"), GH_TARGET)
        self.assertTrue((self.seed / "bin" / "gh").resolve().samefile(
            HARNESS_DIR / "fakes" / "gh"))

    def test_the_replayed_rules_agree_with_the_committed_ruleset(self):
        # The skill reads required checks from the API; the seed also carries
        # the ruleset as a file. Both must tell the agent the same thing.
        committed = json.loads((self.seed / ".github" / "rulesets" / "main.json")
                               .read_text(encoding="utf-8"))
        replay = self.seed / ".gh" / "replay" / "api" / "repos" / "example-org" / \
            "example-service"
        in_force = json.loads((replay / "rules" / "branches" / "main.json")
                              .read_text(encoding="utf-8"))
        strip = {"ruleset_source_type", "ruleset_source", "ruleset_id"}
        self.assertEqual([{k: v for k, v in r.items() if k not in strip}
                          for r in in_force], committed["rules"])

    def test_the_prompt_is_this_fixtures_own(self):
        # Improve mode drops a held-out fixture's prompt from the trigger
        # training set by text; a shared prompt would leak across the split.
        prompts = {}
        for path in sorted(SKILL_DIR.glob("*/fixture.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            prompts[path.parent.name] = " ".join(doc["prompt"].split())
        self.assertIn(self.FIXTURE, prompts)
        self.assertEqual(len(set(prompts.values())), len(prompts), prompts)
