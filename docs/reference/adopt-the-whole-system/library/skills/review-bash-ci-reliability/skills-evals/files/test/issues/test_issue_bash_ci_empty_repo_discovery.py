#!/usr/bin/env python3
"""evals/review-bash-ci-reliability/empty-repo-discovery (SKILL.md item 3).

The seed's nightly scripts/audit-visibility.sh reads `gh repo list` through
process substitution under `set -euo pipefail`, then treats an empty array as
"nothing to audit" and exits 0, so a failed listing goes green. The behavior
and restraint checks run the final script through `repo_tests` against a
hidden stand-in gh in five modes.

What is pinned here:

  * the pristine seed FAILS both behavior checks and PASSES every restraint
    check;
  * a hand-written good fix passes every check, and each plausible wrong fix
    fails the checks it should, the skill's own SAFE snippet copied verbatim
    among them (it turns an empty listing into one empty name);
  * the arms' offline gh answers the seed's call, so the script passes in the
    workspace as it does in CI today;
  * the fixture's shape: no model pin, the shared context block, correctness
    weighted above restraint, line-anchored patterns, a probe the seed does
    not carry.

Scoring runs `objective.run_checks` in process with the bubblewrap sandbox
replaced by a direct run, as test_issue_88_fixture.py does. One test scores
the pristine seed through `run_eval.py` and the real sandbox, and skips where
the sandbox cannot start.
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
FIXTURE_DIR = SKILL_DIR / "empty-repo-discovery"
SEED = FIXTURE_DIR / "seed"
SCRIPT = Path("scripts") / "audit-visibility.sh"

sys.path.insert(0, str(HARNESS))
import run_eval  # noqa: E402
from scorers import commands, objective  # noqa: E402

EXPECTED_PRISTINE = {
    "workflow-yaml-parses": True,
    "workflow-file-present": True,
    "gh-failure-fails-the-job": False,
    "partial-listing-fails-the-job": False,
    "empty-org-still-green": True,
    "listed-repos-still-audited": True,
    "allowlist-untouched": True,
}
BEHAVIOR = {k for k, v in EXPECTED_PRISTINE.items() if not v}

SEED_LIST = ("mapfile -t REPOS < <(gh repo list \"$ORG\" --visibility public "
             "--json name --jq '.[].name' --limit 500)\n")
CAPTURE = ("repo_list=$(gh repo list \"$ORG\" --visibility public --json name "
           "--jq '.[].name' --limit 500) \\\n"
           "    || { echo \"ERROR: gh repo list failed\" >&2; exit 1; }\n")
GOOD_LIST = CAPTURE + 'REPOS=()\n[[ -z "$repo_list" ]] || mapfile -t REPOS <<< "$repo_list"\n'
# SKILL.md item 3's SAFE example, verbatim apart from the command.
SKILL_SNIPPET_LIST = CAPTURE + 'mapfile -t REPOS <<< "$repo_list"\n'
SEED_EMPTY_EXIT = ('    echo "No public repositories in ${ORG}; nothing to audit."\n'
                   '    exit 0\n')

TOOLS = ("bash", "grep")
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


class EmptyRepoDiscovery(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bash-ci-discovery-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(FIXTURE_DIR)

    def ws(self) -> Path:
        # copytree resolves the seed's bin/gh symlink, as the harness does.
        ws = self.tmp / f"ws-{len(list(self.tmp.iterdir()))}"
        shutil.copytree(SEED, ws)
        return ws

    def with_list(self, new: str) -> Path:
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_LIST, new)
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
        self.assertEqual(self.failed(self.with_list(GOOD_LIST)), set())

    @needs_tools
    def test_waiting_on_the_substitution_also_passes(self):
        # bash 4.4+ reports a process substitution's status through `wait $!`.
        ws = self.with_list(SEED_LIST + 'wait "$!" || { echo "ERROR: gh repo list failed" '
                                        '>&2; exit 1; }\n')
        self.assertEqual(self.failed(ws), set())

    # ---- plausible wrong fixes -----------------------------------------------

    @needs_tools
    def test_the_skills_safe_snippet_verbatim_flags_an_empty_name(self):
        self.assertEqual(self.failed(self.with_list(SKILL_SNIPPET_LIST)),
                         {"empty-org-still-green"})

    @needs_tools
    def test_failing_on_every_empty_result_fails_restraint(self):
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_EMPTY_EXIT,
                     '    echo "ERROR: no public repositories listed" >&2\n    exit 1\n')
        self.assertEqual(self.failed(ws), {"partial-listing-fails-the-job",
                                           "empty-org-still-green"})

    @needs_tools
    def test_checking_only_for_empty_misses_the_partial_listing(self):
        # A guard on the empty array alone cannot see a listing that failed
        # after printing some names.
        ws = self.ws()
        replace_once(ws / SCRIPT, SEED_EMPTY_EXIT,
                     '    gh repo list "$ORG" --limit 1 >/dev/null\n' + SEED_EMPTY_EXIT)
        self.assertEqual(self.failed(ws), {"partial-listing-fails-the-job"})

    @needs_tools
    def test_editing_the_allowlist_fails_restraint(self):
        ws = self.with_list(GOOD_LIST)
        (ws / "config" / "public-allowlist.txt").write_text(
            "docs-site\nwidgets\nlegacy-site\n", encoding="utf-8")
        self.assertEqual(self.failed(ws), {"allowlist-untouched"})

    # ---- the arms' offline gh ------------------------------------------------

    @needs_tools
    def test_seed_script_passes_against_the_arms_offline_gh(self):
        if not any(os.access(f"{d}/python3", os.X_OK) for d in ("/usr/bin", "/bin")):
            self.skipTest("the offline gh needs python3")
        ws = self.ws()
        (ws / ".git").mkdir()
        (ws / ".git" / "workspace-root").write_text(f"{ws}\n", encoding="utf-8")
        home = self.tmp / "home"
        home.mkdir()
        env = {"PATH": f"{ws / 'bin'}:/usr/bin:/bin", "HOME": str(home),
               "GH_REPLAY_DIR": str(ws / ".gh" / "replay"),
               "GH_REPO": "example-org/audit-tools", "GH_TOKEN": "", "LC_ALL": "C"}
        proc = subprocess.run(["bash", str(SCRIPT)], cwd=ws, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "audited 2 public repositories, 0 unexpected\n")
        self.assertEqual(self.fixture["env"]["PATH"], "$WORKSPACE/bin:$PATH")
        self.assertEqual(self.fixture["env"]["GH_REPLAY_DIR"], "$WORKSPACE/.gh/replay")
        self.assertEqual(os.readlink(SEED / "bin" / "gh"), "../../../../../harness/fakes/gh")

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
        probe = FIXTURE_DIR / "checker" / ".probe"
        self.assertTrue((probe / "audit.sh").is_file())
        self.assertTrue(os.access(probe / "bin" / "gh", os.X_OK))
        self.assertFalse((SEED / ".probe").exists())
        for path in SEED.rglob("*"):
            if path.is_file() and not path.is_symlink():
                text = path.read_text(encoding="utf-8").lower()
                for word in ("eval", "probe", "checker", "fixture", "stand-in", "fake"):
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
            proc = subprocess.run(["bash", ".probe/audit.sh", case], cwd=ws,
                                  capture_output=True, timeout=60)
            with self.subTest(case=case):
                self.assertEqual(proc.returncode == 2, case == "no-such-case")


if __name__ == "__main__":
    unittest.main()
