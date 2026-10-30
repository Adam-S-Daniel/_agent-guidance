"""Subject-agnostic real-work fixtures: the subject named at run time, the
`draft:` key, and the answer-leak lint with its `interface_strings:`
exemption list.

Owner decision Q4 (the owner, 2026-10-06, "Subject-agnostic"): a real-work
fixture runs under whichever skill or guidance treatment the run names, so it
is not copied once per subject. Offline: no network, no CLI, no agent call;
every run here is `--arm objective-only` or is refused before any arm starts.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import answer_leak  # noqa: E402
import guidance  # noqa: E402
import run_eval  # noqa: E402
from scorers import commands  # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
HIDDEN_TEST = ("import unittest\n\nimport calc\n\n\n"
               "class CalcTests(unittest.TestCase):\n"
               "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n")
ADD = "test_calc.CalcTests.test_add"
# `write_fixture(key=DROP)` leaves a default key out of the fixture.
DROP = object()
# A snapshot's recorded provenance: cms-platform#677's real times, as YAML
# strings (`issue_last_edited_at` null: never edited).
PROVENANCE = {"issue_created_at": "2026-10-05T15:59:07Z",
              "issue_last_edited_at": None,
              "first_commit_at": "2026-10-05T17:50:08Z"}


class _Fixture(unittest.TestCase):
    """A subject-agnostic toy fixture under `<root>/evals/real-work/toy-1/`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="real-work-subject-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.fixture_dir = self.root / "evals" / "real-work" / "toy-1"
        (self.fixture_dir / "seed").mkdir(parents=True)
        (self.fixture_dir / "seed" / "calc.py").write_text(BUGGY, encoding="utf-8")
        (self.fixture_dir / "checker").mkdir()
        (self.fixture_dir / "checker" / "test_calc.py").write_text(
            HIDDEN_TEST, encoding="utf-8")
        # Every network-namespace probe off, so a run reads the same on any host.
        patcher = mock.patch("scorers.commands._sandbox_prefix", return_value=([], "unavailable"))
        patcher.start()
        self.addCleanup(patcher.stop)
        sandbox = mock.patch.object(commands, "_run_sandboxed",
                                    side_effect=lambda prefix, *args: commands._run_command(*args))
        sandbox.start()
        self.addCleanup(sandbox.stop)

    def write_fixture(self, **changes):
        fixture = {"subject": "any", "draft": True, "prompt": "Fix add().",
                   "objective_checks": [{
                       "id": "hidden-tests", "type": "repo_tests",
                       "overlay": "checker",
                       "argv": ["python3", "-m", "unittest"],
                       "fail_to_pass": [ADD]}]}
        fixture.update(changes)
        fixture = {key: value for key, value in fixture.items() if value is not DROP}
        (self.fixture_dir / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")

    def fixed_workspace(self) -> Path:
        workspace = self.root / "fixed"
        shutil.copytree(self.fixture_dir / "seed", workspace)
        (workspace / "calc.py").write_text(FIXED, encoding="utf-8")
        return workspace

    def main(self, *argv, eval_dir=None):
        out = io.StringIO()
        args = ["run_eval.py", str(eval_dir or self.fixture_dir),
                "--results-dir", str(self.root / "results"), *map(str, argv)]
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(out):
            code = run_eval.main()
        return code, out.getvalue()


class SubjectAtRunTimeTests(_Fixture):
    def test_objective_only_scores_a_subject_agnostic_fixture_red_then_green(self):
        self.write_fixture()
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 1, out)
        report = json.loads(out)
        self.assertEqual(report["subject"], "any")
        self.assertIn("fail_to_pass=0/1", report["checks"][0]["detail"])
        code, out = self.main("--arm", "objective-only",
                              "--workspace", self.fixed_workspace())
        self.assertEqual(code, 0, out)
        self.assertIn("fail_to_pass=1/1", json.loads(out)["checks"][0]["detail"])
        self.assertFalse((self.root / "results").exists(),
                         "objective-only writes no results")

    def test_skill_flag_runs_it_as_that_skill(self):
        self.write_fixture()
        code, out = self.main("--arm", "objective-only", "--skill", "toy-skill")
        self.assertEqual(code, 1, out)
        self.assertEqual(json.loads(out)["skill"], "toy-skill")
        # Found by discovery under evals/real-work/, it is named after its own
        # directory rather than refused for not sitting under evals/toy-skill/.
        code, out = self.main("--arm", "objective-only", "--skill", "toy-skill",
                              eval_dir=self.fixture_dir.parent)
        self.assertEqual(code, 1, out)
        self.assertEqual(json.loads(out)["skill"], "toy-skill")

    def test_apply_runtime_subject_maps_each_flag(self):
        path = Path("fixture.yaml")
        base = {"subject": "any", "prompt": "p"}
        as_skill = run_eval.apply_runtime_subject(base, "toy", None, "with_skill", path)
        self.assertEqual((as_skill["subject"], as_skill["skill"]), ("skill", "toy"))
        as_guidance = run_eval.apply_runtime_subject(base, None, "security", "both", path)
        self.assertEqual((as_guidance["subject"], as_guidance["section"]),
                         ("guidance", "security"))
        self.assertEqual(base, {"subject": "any", "prompt": "p"}, "input not mutated")
        self.assertIs(run_eval.apply_runtime_subject(base, None, None, "objective-only", path),
                      base)

    def test_refusals_are_configuration_errors(self):
        path = Path("fixture.yaml")
        cases = [
            ({"subject": "any"}, "toy", "security", "both", "both --skill and --section"),
            ({"subject": "any"}, None, None, "with_skill", "names no subject"),
            ({"subject": "any", "skill": "x"}, "toy", None, "with_skill", "names no `skill:`"),
            ({"subject": "any", "section": "x"}, None, "s", "both", "names no `skill:`"),
            ({"skill": "x"}, "toy", None, "with_skill", "fixes its subject"),
            ({"subject": "guidance", "section": "s"}, None, "t", "both", "fixes its subject"),
            ({"subject": "any"}, "", None, "with_skill", "nonblank"),
            ({"subject": "any"}, None, " ", "both", "nonblank"),
        ]
        for fixture, skill, section, arm, message in cases:
            with self.subTest(fixture=fixture, skill=skill, section=section):
                with self.assertRaises(guidance.GuidanceError) as caught:
                    run_eval.apply_runtime_subject(fixture, skill, section, arm, path)
                self.assertIn(message, str(caught.exception))

    def test_section_flag_names_the_results_after_the_fixture(self):
        # Two real-work fixtures run under one section must not share
        # results/guidance/<section>/<timestamp>/, so a `subject: any` fixture
        # adds its own directory name, as it does under --skill.
        self.assertEqual(run_eval.guidance_results_key("security", None),
                         "guidance/security")
        self.assertEqual(run_eval.guidance_results_key("security", "toy-1"),
                         "guidance/security/toy-1")
        self.write_fixture(draft=DROP)
        seen = []
        with mock.patch.object(run_eval, "_run_guidance",
                               side_effect=lambda args, fixture, name=None:
                               seen.append((fixture["section"], name)) or 0):
            code, out = self.main("--arm", "both", "--section", "security")
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, [("security", "toy-1")])
        # A fixture that fixes its own guidance subject keeps today's path.
        self.write_fixture(subject="guidance", section="security", draft=DROP)
        seen.clear()
        with mock.patch.object(run_eval, "_run_guidance",
                               side_effect=lambda args, fixture, name=None:
                               seen.append((fixture["section"], name)) or 0):
            self.main("--arm", "both")
        self.assertEqual(seen, [("security", None)])

    def test_an_agent_arm_without_a_subject_is_refused_before_any_arm(self):
        self.write_fixture(draft=DROP)
        code, out = self.main("--arm", "with_skill")
        self.assertEqual(code, 2, out)
        self.assertIn("names no subject", out)

    def test_a_fixed_subject_fixture_refuses_the_flag(self):
        self.write_fixture(subject=DROP, skill="toy-skill")
        code, out = self.main("--arm", "objective-only", "--skill", "other")
        self.assertEqual(code, 2, out)
        self.assertIn("fixes its subject", out)


class DraftTests(_Fixture):
    def test_a_draft_runs_objective_only_but_no_agent_arm(self):
        self.write_fixture()
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 1, out)  # scored, red: not refused
        code, out = self.main("--arm", "with_skill", "--skill", "toy-skill")
        self.assertEqual(code, 2, out)
        self.assertIn("draft: true", out)

    def test_check_draft(self):
        path = Path("fixture.yaml")
        run_eval.check_draft({"draft": True}, "objective-only", False, path)
        run_eval.check_draft({"draft": True}, "with_skill", True, path)
        run_eval.check_draft({"draft": False}, "with_skill", False, path)
        run_eval.check_draft({}, "both", False, path)
        with self.assertRaisesRegex(guidance.GuidanceError, "draft: true"):
            run_eval.check_draft({"draft": True}, "both", False, path)
        for bad in ("yes", 1, None, []):
            with self.subTest(bad=bad), self.assertRaisesRegex(
                    guidance.GuidanceError, "true or false"):
                run_eval.check_draft({"draft": bad}, "objective-only", False, path)

    def test_eval_workflows_never_pass_allow_draft(self):
        for name in ("eval.yml", "routine-eval-fire.yml"):
            text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
            # Nor a subject: a `subject: any` fixture dispatched there is
            # refused by run_eval before any agent call.
            for flag in ("--allow-draft", "--skill", "--section"):
                self.assertNotIn(flag, text, f"{name} passes {flag}")


class AnswerLeakTests(_Fixture):
    PATCH = ("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
             "-    return a - b\n+    return a + b  # refusing to report on a partial read\n")

    def test_added_lines_skip_headers_and_context(self):
        self.assertEqual(answer_leak.added_lines(self.PATCH),
                         ["    return a + b  # refusing to report on a partial read"])

    def test_a_four_word_run_from_the_answer_is_a_leak(self):
        answer = "\n".join(answer_leak.added_lines(self.PATCH))
        self.assertEqual(answer_leak.leaked_runs("Fix add(). It must say: Return A + B!", answer),
                         [])  # three words of the answer, `+` is not a word
        self.assertEqual(
            answer_leak.leaked_runs("Print REFUSING to report on, then stop.", answer),
            ["refusing to report on"])

    def test_a_run_spanning_two_added_lines_is_not_a_hit(self):
        # Matched within one added line: two unrelated lines never join.
        answer = "    x = report on\n    a partial read = 1"
        self.assertEqual(answer_leak.leaked_runs("report on a partial read", answer), [])
        self.assertEqual(answer_leak.leaked_runs(
            "report on a partial read", "report on a partial read"),
            ["report on a partial", "on a partial read"])

    def test_declared_interface_strings_are_exempt(self):
        answer = "\n".join(answer_leak.added_lines(self.PATCH))
        prompt = "The error must contain `refusing to report on a partial read`."
        self.assertEqual(len(answer_leak.leaked_runs(prompt, answer)), 4)
        self.assertEqual(answer_leak.leaked_runs(
            prompt, answer, ["refusing to report on a partial read"]), [])
        # An exemption covers its own words only, not a longer quotation.
        self.assertEqual(answer_leak.leaked_runs(
            "Write: return a b refusing to report", answer,
            ["refusing to report on a partial read"]),
            ["return a b refusing", "a b refusing to", "b refusing to report"])

    def test_text_the_issue_held_before_the_fix_is_not_a_leak(self):
        # The diff quoting the issue is not the issue quoting the diff: a run
        # the issue already held before the pull request's first commit is
        # exempt; a run only the diff and the task text share is not.
        answer = "\n".join(answer_leak.added_lines(self.PATCH))
        before = "Bug: the report keeps refusing to report on stale data."
        prompt = before + "\nAdd a check refusing to report on a partial read."
        self.assertEqual(answer_leak.leaked_runs(prompt, answer, preexisting=before),
                         ["to report on a", "report on a partial", "on a partial read"])
        self.assertEqual(answer_leak.leaked_runs(before, answer, preexisting=before), [])
        # Matched as runs of words, so rewrapping or recasing the issue text
        # does not make it new.
        self.assertEqual(answer_leak.leaked_runs(
            "REFUSING to\nreport on", answer, preexisting=before), [])

    def test_the_issue_snapshot_is_a_file_in_the_fixture_outside_the_seed(self):
        directory = self.fixture_dir
        (directory / "issue-before-fix.txt").write_text("Bug text\n", encoding="utf-8")
        path = directory / "fixture.yaml"
        fixture = {"issue_before_fix": "issue-before-fix.txt", **PROVENANCE}
        answer_leak.validate_fixture(fixture, path)
        self.assertEqual(answer_leak.preexisting_text(fixture, directory), "Bug text\n")
        self.assertEqual(answer_leak.preexisting_text({}, directory), "")
        (directory / "seed" / "issue.txt").write_text("x", encoding="utf-8")
        (directory / "link.txt").symlink_to(directory / "issue-before-fix.txt")
        for bad in ("missing.txt", "../outside.txt", "/etc/hostname", "seed/issue.txt",
                    "link.txt", "checker", "", 3):
            with self.subTest(bad=bad), self.assertRaises(guidance.GuidanceError):
                answer_leak.validate_fixture({"issue_before_fix": bad, **PROVENANCE}, path)

    def test_the_snapshot_size_cap_is_256_kib(self):
        path = self.fixture_dir / "fixture.yaml"
        snapshot = self.fixture_dir / "issue-before-fix.txt"
        fixture = {"issue_before_fix": "issue-before-fix.txt", **PROVENANCE}
        snapshot.write_bytes(b"a" * (256 * 1024))
        answer_leak.validate_fixture(fixture, path)
        self.write_fixture(issue_before_fix="issue-before-fix.txt", **PROVENANCE)
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 1, out)  # accepted at load, then scored red
        snapshot.write_bytes(b"a" * (256 * 1024 + 1))
        with self.assertRaisesRegex(guidance.GuidanceError, "262144 bytes"):
            answer_leak.validate_fixture(fixture, path)
        self.write_fixture(issue_before_fix="issue-before-fix.txt", **PROVENANCE)
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 2, out)
        self.assertIn("issue_before_fix", out)

    def test_a_snapshot_that_is_not_utf8_is_refused_at_load(self):
        (self.fixture_dir / "issue-before-fix.txt").write_bytes(b"Bug \xff text\n")
        with self.assertRaisesRegex(guidance.GuidanceError, "UTF-8"):
            answer_leak.validate_fixture(
                {"issue_before_fix": "issue-before-fix.txt", **PROVENANCE},
                self.fixture_dir / "fixture.yaml")
        self.write_fixture(issue_before_fix="issue-before-fix.txt", **PROVENANCE)
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 2, out)

    def test_the_snapshot_provenance_is_checked_at_load(self):
        # "Pre-existing" rests on three recorded times: the issue existed,
        # and was last edited (or never), no later than the first commit.
        (self.fixture_dir / "issue-before-fix.txt").write_text("Bug\n", encoding="utf-8")
        path = self.fixture_dir / "fixture.yaml"
        base = {"issue_before_fix": "issue-before-fix.txt"}
        good = [
            PROVENANCE,
            {**PROVENANCE, "issue_last_edited_at": "2026-10-05T16:30:00Z"},
            {**PROVENANCE, "issue_last_edited_at": "2026-10-05T17:50:08Z"},
            {**PROVENANCE, "issue_created_at": "2026-10-05T17:50:08Z"},
        ]
        for provenance in good:
            with self.subTest(good=provenance):
                answer_leak.validate_fixture({**base, **provenance}, path)
        missing = [{k: v for k, v in PROVENANCE.items() if k != key} for key in PROVENANCE]
        bad = missing + [
            {**PROVENANCE, "issue_created_at": "2026-10-05"},
            {**PROVENANCE, "issue_created_at": "2026-10-05T15:59:07+02:00"},
            {**PROVENANCE, "issue_created_at": "yesterday"},
            {**PROVENANCE, "issue_created_at": None},
            {**PROVENANCE, "first_commit_at": 1759680608},
            {**PROVENANCE, "issue_created_at": "2026-10-05T18:00:00Z"},
            {**PROVENANCE, "issue_last_edited_at": "2026-10-05T17:50:09Z"},
            {**PROVENANCE, "issue_last_edited_at": "2026-10-05T15:00:00Z"},
            {**PROVENANCE, "issue_last_edited_at": ""},
        ]
        for provenance in bad:
            with self.subTest(bad=provenance), self.assertRaises(guidance.GuidanceError):
                answer_leak.validate_fixture({**base, **provenance}, path)
        # The keys mean nothing without a snapshot, so they are refused alone.
        with self.assertRaisesRegex(guidance.GuidanceError, "issue_before_fix"):
            answer_leak.validate_fixture(dict(PROVENANCE), path)
        self.write_fixture(issue_before_fix="issue-before-fix.txt",
                           **{**PROVENANCE, "issue_created_at": "2026-10-06T00:00:00Z"})
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 2, out)
        self.assertIn("issue_created_at", out)

    def test_a_bad_issue_snapshot_is_refused_by_run_eval_before_scoring(self):
        self.write_fixture(issue_before_fix="missing.txt", **PROVENANCE)
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 2, out)
        self.assertIn("issue_before_fix", out)

    def test_interface_strings_are_validated_at_fixture_load(self):
        path = Path("fixture.yaml")
        answer_leak.validate_fixture({}, path)
        answer_leak.validate_fixture({"interface_strings": ["fetch-failed"]}, path)
        for bad in ("fetch-failed", [], [""], ["  "], [3], ["x" * 201], ["a"] * 33):
            with self.subTest(bad=bad), self.assertRaises(guidance.GuidanceError):
                answer_leak.validate_fixture({"interface_strings": bad}, path)

    def test_a_bad_list_is_refused_by_run_eval_before_scoring(self):
        self.write_fixture(interface_strings="fetch-failed")
        code, out = self.main("--arm", "objective-only")
        self.assertEqual(code, 2, out)
        self.assertIn("interface_strings", out)


if __name__ == "__main__":
    unittest.main()
