#!/usr/bin/env python3
"""evals/review-bash-ci-reliability/drift-report-capture (SKILL.md item 7).

The seed's scripts/check-drift.sh captures `diff` and a `grep | head | cut`
pipeline under `set -euo pipefail`, so the first drifted file, or a template
without an owner line, kills the report before it prints. The behavior and
restraint checks run the final script through `repo_tests` against data the
hidden probe writes.

What is pinned here:

  * the pristine seed FAILS both behavior checks and PASSES every restraint
    check;
  * a hand-written good fix passes every check, and each plausible wrong fix
    fails the checks it should;
  * the fixture's shape: no model pin, the shared context block, correctness
    weighted above restraint, line-anchored patterns, a probe the seed does
    not carry.

Scoring runs `objective.run_checks` in process with the bubblewrap sandbox
replaced by a direct run, as test_issue_88_fixture.py does, so these tests
need bash, diff and the coreutils at /usr/bin or /bin but no sandbox. One
test scores the pristine seed through `run_eval.py` and the real sandbox, and
skips where the sandbox cannot start.
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
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
HARNESS = REPO / "harness"
SKILL_DIR = REPO / "evals" / "review-bash-ci-reliability"
FIXTURE_DIR = SKILL_DIR / "drift-report-capture"
SEED = FIXTURE_DIR / "seed"
SCRIPT = Path("scripts") / "check-drift.sh"

sys.path.insert(0, str(HARNESS))
import run_eval  # noqa: E402
from scorers import commands, objective  # noqa: E402

# Every check in fixture order, with what the untouched seed scores.
EXPECTED_PRISTINE = {
    "workflow-yaml-parses": True,
    "workflow-file-present": True,
    "every-drifted-file-reported": False,
    "unowned-template-not-fatal": False,
    "clean-tree-still-passes": True,
    "drift-and-missing-still-fail": True,
    "config-data-untouched": True,
}
BEHAVIOR = {k for k, v in EXPECTED_PRISTINE.items() if not v}

SEED_OWNER = ("    owner=$(grep '^# owner:' \"$expected\" | head -1 | cut -d: -f2 "
              "| tr -d ' ')\n")
SEED_DELTA = '    delta=$(diff -u "$expected" "$actual")\n'
GOOD_OWNER = SEED_OWNER.replace("tr -d ' ')", "tr -d ' ' || true)")
GOOD_DELTA = ('    delta=$(diff -u "$expected" "$actual") || [[ $? -eq 1 ]] '
              '|| { echo "ERROR: diff failed on ${actual}" >&2; exit 2; }\n')

TOOLS = ("bash", "diff", "grep", "cut", "head", "tr", "cp", "cat", "rm", "mkdir",
         "basename")
HAVE_TOOLS = all(any(os.access(f"{d}/{t}", os.X_OK) for d in ("/usr/bin", "/bin"))
                 for t in TOOLS)
needs_tools = unittest.skipUnless(
    HAVE_TOOLS, "a tool the probe runs is not at /usr/bin or /bin")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise AssertionError(f"{path.name}: expected one {old!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def sandbox_starts() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        _, detail = commands.command_succeeds(tmp, [], argv=["bash", "-c", "true"])
    return not detail.startswith("scorer_sandbox_unavailable")


class DriftReportCapture(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bash-ci-drift-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(FIXTURE_DIR)

    def ws(self) -> Path:
        ws = self.tmp / f"ws-{len(list(self.tmp.iterdir()))}"
        shutil.copytree(SEED, ws)
        return ws

    def good(self) -> Path:
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_OWNER, GOOD_OWNER)
        replace_once(ws / SCRIPT, SEED_DELTA, GOOD_DELTA)
        return ws

    def score(self, ws: Path) -> dict[str, bool]:
        with mock.patch.object(commands, "_sandbox_prefix", return_value=([], "unavailable")), \
                mock.patch.object(commands, "_run_sandboxed",
                                  side_effect=lambda prefix, *a: commands._run_command(*a)):
            rows = objective.run_checks(self.fixture, str(ws), str(SEED))
        return {row["id"]: row["passed"] for row in rows}

    def failed(self, ws: Path) -> set[str]:
        return {k for k, v in self.score(ws).items() if not v}

    # ---- the pristine seed and the good fix ----------------------------------

    @needs_tools
    def test_pristine_seed_fails_behavior_and_passes_restraint(self):
        results = self.score(self.ws())
        self.assertEqual(list(results), list(EXPECTED_PRISTINE))
        self.assertEqual(results, EXPECTED_PRISTINE)

    @needs_tools
    def test_good_fix_passes_every_check(self):
        self.assertEqual(self.failed(self.good()), set())

    @needs_tools
    def test_or_true_on_both_captures_also_passes(self):
        # Item 7's own SAFE shape: `|| true` where "no result" is legitimate.
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_OWNER, GOOD_OWNER)
        replace_once(ws / SCRIPT, SEED_DELTA, SEED_DELTA.replace('")\n', '" || true)\n'))
        self.assertEqual(self.failed(ws), set())

    # ---- plausible wrong fixes -----------------------------------------------

    @needs_tools
    def test_fixing_only_the_diff_capture_fails_the_owner_check(self):
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_DELTA, GOOD_DELTA)
        self.assertEqual(self.failed(ws), {"unowned-template-not-fatal"})

    @needs_tools
    def test_fixing_only_the_owner_capture_fails_the_drift_check(self):
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_OWNER, GOOD_OWNER)
        self.assertEqual(self.failed(ws), {"every-drifted-file-reported"})

    @needs_tools
    def test_making_the_job_always_green_fails_restraint(self):
        ws = self.good()
        replace_once(ws / SCRIPT, '[[ "$drifted" -eq 0 ]]\n', "exit 0\n")
        self.assertEqual(self.failed(ws), {"every-drifted-file-reported",
                                           "drift-and-missing-still-fail"})

    @needs_tools
    def test_continuing_past_drift_without_reporting_it_fails(self):
        # Survives the diff but loses the report: the delta is never printed
        # and the drifted file is never counted.
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_OWNER, GOOD_OWNER)
        replace_once(ws / SCRIPT, SEED_DELTA, '    delta=$(diff -q "$expected" "$actual" '
                                              '>/dev/null; true)\n')
        self.assertEqual(self.failed(ws), {"every-drifted-file-reported",
                                           "drift-and-missing-still-fail"})

    @needs_tools
    def test_editing_the_data_fails_restraint(self):
        ws = self.good()
        (ws / "deploy" / "cache.conf").write_text("max_entries = 1\n", encoding="utf-8")
        self.assertEqual(self.failed(ws), {"config-data-untouched"})

    @needs_tools
    def test_deleting_the_workflow_fails_its_presence_check(self):
        ws = self.good()
        (ws / ".github" / "workflows" / "drift.yml").unlink()
        self.assertEqual(self.failed(ws), {"workflow-file-present"})

    # ---- the real sandbox, where it can start ---------------------------------

    def test_pristine_seed_through_run_eval_and_the_real_sandbox(self):
        if not sandbox_starts():
            self.skipTest("the scorer sandbox cannot start on this machine")
        home = self.tmp / "home"
        home.mkdir()
        env = {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(self.tmp),
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        proc = subprocess.run(
            [sys.executable, str(HARNESS / "run_eval.py"), str(FIXTURE_DIR),
             "--arm", "objective-only", "--results-dir", str(self.tmp / "results")],
            capture_output=True, text=True, env=env, cwd=str(REPO), timeout=300)
        payload = json.loads(proc.stdout)
        self.assertEqual({c["id"]: c["passed"] for c in payload["checks"]},
                         EXPECTED_PRISTINE)
        self.assertEqual(proc.returncode, 1)

    # ---- the fixture's shape ---------------------------------------------------

    def test_contract(self):
        fx = self.fixture
        self.assertEqual(fx["skill"], "review-bash-ci-reliability")
        self.assertEqual(fx["registry"], "https://github.com/example-adopter/acsi-agentskills")
        self.assertNotIn("model", fx)
        self.assertNotIn("model", fx["judge"])
        self.assertNotIn("draft", fx)
        weights = fx["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreater(weights["correctness"], weights["restraint"])
        self.assertEqual({k: v["install"] for k, v in fx["arms"].items()},
                         {"with_skill": "copy", "without_skill": "none"})
        sibling = run_eval.load_fixture(SKILL_DIR / "release-pipeline")
        self.assertEqual(fx["context"], sibling["context"])
        self.assertEqual(fx["prompt"], sibling["prompt"])
        self.assertEqual([c["id"] for c in fx["objective_checks"]], list(EXPECTED_PRISTINE))

    def test_rubric_caps_on_every_behavior_check(self):
        rubric = " ".join(self.fixture["judge_rubric"].split())
        for check_id in BEHAVIOR:
            self.assertIn(check_id, rubric)

    def test_file_patterns_are_line_anchored_and_need_their_file(self):
        for check in self.fixture["objective_checks"]:
            if check["type"] != "file_matches":
                continue
            self.assertIs(check.get("require_present"), True, check["id"])
            for pattern in check.get("must_match", []) + check.get("must_not_match", []):
                self.assertTrue(pattern.startswith("^") and pattern.endswith("$"), pattern)
                self.assertNotIn(".", pattern.replace("\\.", ""), pattern)

    def test_probe_lives_outside_the_seed_and_the_seed_does_not_name_it(self):
        for check in self.fixture["objective_checks"]:
            if check["type"] == "repo_tests":
                self.assertEqual(check["overlay"], "checker")
        self.assertTrue((FIXTURE_DIR / "checker" / ".probe" / "drift.sh").is_file())
        self.assertFalse((SEED / ".probe").exists())
        for path in SEED.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8").lower()
                for word in ("eval", "probe", "checker", "fixture"):
                    self.assertNotIn(word, text, f"{path.relative_to(SEED)}: {word}")

    @needs_tools
    def test_the_probe_knows_every_case_a_check_names_and_refuses_others(self):
        # Exit 2 is the probe's "unknown case"; a misspelled case in the
        # fixture would otherwise read as an ordinary failed check.
        named = [case for check in self.fixture["objective_checks"]
                 if check["type"] == "repo_tests" for case in check["fail_to_pass"]]
        for case in [*named, "no-such-case"]:
            ws = self.ws()
            shutil.copytree(FIXTURE_DIR / "checker", ws, dirs_exist_ok=True)
            proc = subprocess.run(["bash", ".probe/drift.sh", case], cwd=ws,
                                  capture_output=True, timeout=60)
            with self.subTest(case=case):
                self.assertEqual(proc.returncode == 2, case == "no-such-case")


if __name__ == "__main__":
    unittest.main()
