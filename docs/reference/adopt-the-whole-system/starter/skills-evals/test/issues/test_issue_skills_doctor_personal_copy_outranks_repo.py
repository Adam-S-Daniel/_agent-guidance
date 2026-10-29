#!/usr/bin/env python3
"""evals/skills-doctor/personal-copy-outranks-repo, and the contract every
skills-doctor fixture's test module shares.

The seed is a cloud session capture where the personal store holds an older,
hand-placed `release-notes` that outranks the repo's own copy. The skill's
answer names the personal copy as the one the session reads, attributes it
to a hand copy from the install record, and changes nothing.

`FixtureContract` below is what the three skills-doctor fixture modules
share (this one, `test_issue_skills_doctor_one_row_rejects_the_lock.py` and
`test_issue_skills_doctor_ssh_remote_is_durable.py`):

  * the fixture's shape: the skill, the registry, no model pin, the same
    deployed context as `bucketed-account-store`, and judge weights where
    correctness outweighs restraint;
  * every must_match / must_not_match pattern, as parsed YAML, is anchored
    at both ends and cannot run across a line, and every check carrying a
    must_not_match sets `require_present: true`;
  * the seed holds invented data only;
  * the pristine seed FAILS every behavior check and PASSES every restraint
    check; a scripted good result passes every check; one edit per check
    breaks only the checks it names; and whole plausible wrong answers fail
    the checks they should.

Every score is read from `harness/run_eval.py --arm objective-only`, the
entry point an eval run uses. The child gets an environment built here, and
every path a test creates lives under that test's own mkdtemp.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 -m pytest test/issues/test_issue_skills_doctor_personal_copy_outranks_repo.py -q`.
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

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
SKILL_DIR = REPO_ROOT / "evals" / "skills-doctor"
REFERENCE_FIXTURE = SKILL_DIR / "bucketed-account-store"

sys.path.insert(0, str(TEST_DIR / "issues"))
import test_issue_80_supersede as supersede_tests  # noqa: E402

run_eval = supersede_tests.run_eval
replace_once = supersede_tests.replace_once
anchoring_problems = supersede_tests.anchoring_problems

FINDINGS = "FINDINGS.md"

# A host-like token in a seed: anything ending in a common public suffix.
HOST = re.compile(r"\b[a-z0-9][a-z0-9.-]*\.(?:com|net|org|io|dev|ai)\b", re.IGNORECASE)
ALLOWED_HOSTS = re.compile(r"(?:^|\.)example\.(?:com|net)$", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class FixtureContract:
    """Mixin for one skills-doctor fixture's test case.

    A subclass sets EVAL_DIR, EXPECTED_PRISTINE (check id -> what the
    untouched seed scores, in file order), GOOD (FINDINGS.md text that
    passes everything), and implements `_mutations` and `_wrong_answers`.
    """

    EVAL_DIR: Path
    EXPECTED_PRISTINE: dict[str, bool]
    GOOD: str

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-doctor-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(self.EVAL_DIR)
        self.raw = yaml.safe_load((self.EVAL_DIR / "fixture.yaml").read_text(encoding="utf-8"))
        self._workspaces = 0

    # ---- helpers -------------------------------------------------------------

    def _ws(self) -> Path:
        self._workspaces += 1
        ws = self.tmp / f"ws-{self._workspaces}"
        shutil.copytree(self.EVAL_DIR / "seed", ws)
        return ws

    def _with_findings(self, text: str) -> Path:
        ws = self._ws()
        (ws / FINDINGS).write_text(text, encoding="utf-8")
        return ws

    def _good(self) -> Path:
        return self._with_findings(self.GOOD)

    def _score(self, ws: Path | None = None) -> tuple[int, dict[str, bool]]:
        home, child_tmp = self.tmp / "home", self.tmp / "child-tmp"
        home.mkdir(exist_ok=True)
        child_tmp.mkdir(exist_ok=True)
        env = {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(child_tmp),
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        extra = ["--workspace", str(ws)] if ws is not None else []
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(self.EVAL_DIR),
               "--arm", "objective-only",
               "--results-dir", str(self.tmp / "results"), *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"run_eval.py printed no JSON (exit {proc.returncode}):\n"
                      f"{proc.stdout}\n{proc.stderr}")
        results = {c["id"]: c["passed"] for c in payload["checks"]}
        self.assertEqual(len(results), len(payload["checks"]), "duplicate check id")
        self.assertEqual(proc.returncode, 0 if all(results.values()) else 1,
                         "the exit code must agree with the per-check results")
        return proc.returncode, results

    def _failed(self, ws: Path) -> set[str]:
        _, results = self._score(ws)
        return {check_id for check_id, passed in results.items() if not passed}

    def _mutations(self) -> dict[str, tuple[object, set[str]]]:
        """name -> (edit applied to the good result, the checks it must fail)."""
        raise NotImplementedError

    def _wrong_answers(self) -> dict[str, tuple[object, set[str]]]:
        """name -> (whole wrong result applied to a seed copy, checks it fails)."""
        raise NotImplementedError

    # ---- the fixture's shape -------------------------------------------------

    def test_fixture_names_the_skill_and_follows_the_roster(self):
        self.assertEqual(self.raw["skill"], "skills-doctor")
        self.assertEqual(self.raw["registry"],
                         "https://github.com/example-adopter/acsi-agentskills")
        self.assertNotIn("model", self.raw, "arms follow the roster (ADR 0013)")
        self.assertNotIn("model", self.raw["judge"], "the judge follows the arm (ADR 0013)")
        self.assertNotIn("draft", self.raw)
        self.assertEqual(self.raw["arms"], {"with_skill": {"install": "copy"},
                                            "without_skill": {"install": "none"}})

    def test_context_is_the_skills_shared_deployed_context(self):
        reference = yaml.safe_load(
            (REFERENCE_FIXTURE / "fixture.yaml").read_text(encoding="utf-8"))
        self.assertEqual(self.raw["context"], reference["context"])

    def test_correctness_outweighs_restraint(self):
        weights = self.raw["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertIn("restraint", weights)
        self.assertGreater(max(weights.values()), weights["restraint"])
        self.assertEqual(max(weights, key=weights.get), next(iter(weights)))
        for key in weights:
            self.assertIn(f"{key} —", self.raw["judge_rubric"], key)

    def test_every_pattern_is_anchored_to_one_line(self):
        seen = 0
        for check in self.raw["objective_checks"]:
            for key in ("must_match", "must_not_match"):
                for pattern in check.get(key) or []:
                    seen += 1
                    with self.subTest(check=check["id"], pattern=pattern):
                        self.assertEqual(anchoring_problems(pattern), [])
        self.assertGreater(seen, 0)

    def test_the_anchoring_lint_can_fail(self):
        for bad in ("Winning copy:", "^Verdict: OK", "^.*OK$", "^[^x]*OK$",
                    "(?i)^OK$", "^OK$|OK"):
            with self.subTest(pattern=bad):
                self.assertNotEqual(anchoring_problems(bad), [])

    def test_every_negative_check_fails_closed_on_a_missing_file(self):
        for check in self.raw["objective_checks"]:
            if check.get("must_not_match"):
                with self.subTest(check=check["id"]):
                    self.assertIs(check.get("require_present"), True)

    def test_seed_and_fixture_hold_invented_data_only(self):
        files = [self.EVAL_DIR / "fixture.yaml",
                 *(p for p in (self.EVAL_DIR / "seed").rglob("*") if p.is_file())]
        for path in files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(self.EVAL_DIR))):
                self.assertEqual(EMAIL.findall(text), [])
                hosts = {m.group(0) for m in HOST.finditer(text)}
                self.assertEqual(
                    sorted(h for h in hosts
                           if not ALLOWED_HOSTS.search(h) and h.lower() != "github.com"),
                    [])

    def test_skill_directory_stays_nested(self):
        self.assertFalse((SKILL_DIR / "fixture.yaml").exists(),
                         "a skill directory holds one flat fixture or nested ones (#66)")
        self.assertIn(self.EVAL_DIR.name, run_eval.nested_fixture_names(SKILL_DIR))

    # ---- the pristine seed: the asymmetry ------------------------------------

    def test_pristine_seed_fails_every_behavior_check_and_passes_every_restraint_check(self):
        code, results = self._score()
        self.assertEqual(code, 1)
        self.assertEqual(results, self.EXPECTED_PRISTINE)
        self.assertEqual(list(results), [c["id"] for c in self.raw["objective_checks"]])
        self.assertIn(False, results.values())
        self.assertIn(True, results.values())

    # ---- the good result, and one edit per check -----------------------------

    def test_good_result_passes_every_check(self):
        code, results = self._score(self._good())
        self.assertEqual({k for k, v in results.items() if not v}, set())
        self.assertEqual(code, 0)

    def test_each_edit_fails_exactly_the_checks_it_names(self):
        mutations = self._mutations()
        covered = set().union(*(expected for _, expected in mutations.values()))
        self.assertEqual(covered, set(self.EXPECTED_PRISTINE),
                         "every check has an edit that breaks it")
        for name, (edit, expected) in mutations.items():
            with self.subTest(mutation=name):
                ws = self._good()
                edit(ws)
                self.assertEqual(self._failed(ws), expected)

    def test_plausible_wrong_answers_fail_the_checks_they_should(self):
        wrong = self._wrong_answers()
        self.assertGreaterEqual(len(wrong), 2)
        for name, (build, expected) in wrong.items():
            with self.subTest(answer=name):
                ws = self._ws()
                build(ws)
                self.assertEqual(self._failed(ws), expected)


def findings_edit(old: str, new: str):
    """An edit that replaces `old` (present exactly once) in FINDINGS.md."""
    return lambda ws: replace_once(ws / FINDINGS, old, new)


# ---- this fixture -----------------------------------------------------------

EVAL_DIR = SKILL_DIR / "personal-copy-outranks-repo"
PERSONAL = Path("session") / "skills" / "release-notes" / "SKILL.md"
REPO_COPY = Path("repo") / "dot-claude" / "skills" / "release-notes" / "SKILL.md"

WINNER_LINE = "Winning copy: session/skills/release-notes/SKILL.md\n"
SOURCE_LINE = ("Source: placed by hand. The hook's install record names only "
               "writing-adrs, the lock does not declare release-notes, and there "
               "is no synced/ account store.\n")
SECTION_SENTENCE = "It lacks the Upgrade notes section that pull request 214 added."

GOOD_FINDINGS = f"""\
# Findings

skills-doctor: 1/1 expected present, 1 shadowed, 0 stale, 0 unattributed — DEGRADED

{WINNER_LINE}{SOURCE_LINE}
The personal store (`~/.claude/skills/`) outranks the repo's own
`.claude/skills/`, so the session reads the older personal copy of
release-notes rather than the one the repo committed. {SECTION_SENTENCE}

Nothing here was changed. To pick up the repo's copy, remove or rename
`~/.claude/skills/release-notes/` on the next session's container.
"""


class TestSkillsDoctorPersonalCopyOutranksRepo(FixtureContract, unittest.TestCase):
    EVAL_DIR = EVAL_DIR
    GOOD = GOOD_FINDINGS
    EXPECTED_PRISTINE = {
        "findings-written": False,
        "the-personal-copy-is-named-as-the-winner": False,
        "the-winner-is-attributed-to-a-hand-copy": False,
        "the-missing-section-is-named": False,
        "nothing-in-the-capture-was-repaired": True,
        "no-skill-was-added-to-the-personal-store": True,
    }

    def _mutations(self):
        def findings_elsewhere(ws):
            (ws / FINDINGS).rename(ws / "session" / FINDINGS)

        def personal_copy_overwritten(ws):
            shutil.copyfile(ws / REPO_COPY, ws / PERSONAL)

        def extra_skill_dropped_in(ws):
            shutil.copytree(ws / REPO_COPY.parent, ws / "session" / "skills" / "release-notes-repo")

        behavior = {"findings-written", "the-personal-copy-is-named-as-the-winner",
                    "the-winner-is-attributed-to-a-hand-copy",
                    "the-missing-section-is-named"}
        return {
            "findings_elsewhere": (findings_elsewhere, behavior),
            "repo_copy_named_winner": (
                findings_edit(WINNER_LINE,
                              "Winning copy: repo/dot-claude/skills/release-notes/SKILL.md\n"),
                {"the-personal-copy-is-named-as-the-winner"}),
            "both_copies_named_winner": (
                findings_edit(WINNER_LINE, WINNER_LINE +
                              "Winning copy: repo/dot-claude/skills/release-notes/SKILL.md\n"),
                {"the-personal-copy-is-named-as-the-winner"}),
            "source_blames_the_hook": (
                findings_edit(SOURCE_LINE, "Source: installed by the bootstrap hook.\n"),
                {"the-winner-is-attributed-to-a-hand-copy"}),
            "section_not_found": (
                findings_edit(SECTION_SENTENCE, "It is older than the repo's copy."),
                {"the-missing-section-is-named"}),
            "personal_copy_overwritten": (personal_copy_overwritten,
                                          {"nothing-in-the-capture-was-repaired"}),
            "extra_skill_dropped_in": (extra_skill_dropped_in,
                                       {"no-skill-was-added-to-the-personal-store"}),
        }

    def _wrong_answers(self):
        project_wins = """\
# Findings

Winning copy: repo/dot-claude/skills/release-notes/SKILL.md
Source: committed in the repo by pull request 214.

Project skills override user skills, the same way project settings override
user settings, so the session reads the repo's copy. The model is probably
ignoring the new section; re-running should help.
"""

        def delete_the_shadow(ws):
            (ws / FINDINGS).write_text(GOOD_FINDINGS, encoding="utf-8")
            shutil.rmtree(ws / PERSONAL.parent)

        def hook_blamed(ws):
            (ws / FINDINGS).write_text(
                GOOD_FINDINGS.replace(SOURCE_LINE,
                                      "Source: the bootstrap hook's install of the bundle.\n"),
                encoding="utf-8")

        return {
            "project_settings_intuition": (
                lambda ws: (ws / FINDINGS).write_text(project_wins, encoding="utf-8"),
                {"the-personal-copy-is-named-as-the-winner",
                 "the-winner-is-attributed-to-a-hand-copy",
                 "the-missing-section-is-named"}),
            "right_diagnosis_then_deleted_the_copy": (
                delete_the_shadow,
                {"nothing-in-the-capture-was-repaired",
                 "no-skill-was-added-to-the-personal-store"}),
            "right_winner_wrong_channel": (
                hook_blamed, {"the-winner-is-attributed-to-a-hand-copy"}),
        }


if __name__ == "__main__":
    unittest.main()
