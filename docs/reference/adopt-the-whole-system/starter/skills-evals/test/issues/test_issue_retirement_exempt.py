"""The automatic retirement never removes a core item (the adoption kit's content tiers).

evals/retirement-exempt.yml names the core skills and guidance sections.
compute_gates.finalize() turns a core item's `retire` verdict into
`retire-exempt` before it picks the one retirement per evaluation, and
retire_skill.py's plan() and guidance_plan() open nothing for a core item,
while a retire verdict for any other item still acts.

Hermetic: hand-written gates documents, the fakes of test_issue_70_retire and
test_issue_124_guidance_gates, local git only; no network, no clock.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(ROOT / "test" / "issues"))

import compute_gates  # noqa: E402
import retire_skill  # noqa: E402
import test_issue_70_retire as r70  # noqa: E402
import test_issue_124_guidance_gates as g124  # noqa: E402

NONE = {"skills": set(), "guidance": set()}


def exempt(skills=(), guidance=()):
    return mock.patch.object(compute_gates, "load_exempt",
                             return_value={"skills": set(skills), "guidance": set(guidance)})


class FinalizeTests(unittest.TestCase):
    def verdicts(self):
        return {"core-skill": {"verdict": "retire", "runs_used": [1, 2, 3]},
                "plain-skill": {"verdict": "retire", "runs_used": [1]},
                "kept-skill": {"verdict": "keep", "runs_used": [1]}}

    def test_a_core_retire_is_exempt_and_leaves_the_one_retirement_to_another(self):
        skills = self.verdicts()
        compute_gates.finalize(skills, {"core-skill", "kept-skill"})
        self.assertEqual(skills["core-skill"]["verdict"], "retire-exempt")
        self.assertEqual(skills["core-skill"]["raw_verdict"], "retire")
        self.assertIn("retirement-exempt.yml", skills["core-skill"]["reason"])
        self.assertEqual(skills["plain-skill"]["verdict"], "retire")
        self.assertEqual(skills["kept-skill"]["verdict"], "keep")

    def test_with_no_exempt_item_the_one_with_most_runs_retires(self):
        skills = self.verdicts()
        compute_gates.finalize(skills)
        self.assertEqual(skills["core-skill"]["verdict"], "retire")
        self.assertEqual(skills["plain-skill"]["verdict"], "retire-pending")

    def test_the_committed_list_names_core_items(self):
        listed = compute_gates.load_exempt(ROOT / "evals")
        self.assertIn("workflow-path-audit", listed["skills"])
        self.assertIn("git-push-does-not-mean-commit-exists", listed["guidance"])

    def test_an_absent_file_exempts_nothing_and_a_malformed_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(compute_gates.load_exempt(Path(tmp)), NONE)
            (Path(tmp) / "retirement-exempt.yml").write_text("skills: one\nguidance: []\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                compute_gates.load_exempt(Path(tmp))


class SkillRetirementTests(r70.Base):
    def test_a_core_skill_with_a_retire_verdict_opens_nothing(self):
        with exempt(skills=[r70.SKILL]):
            code, out, err, fake = self.run_main(r70.gates_doc("proposal"))
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes(), [])
        self.assertEqual(fake.pushes, [])
        self.assertUntouched()

    def test_a_skill_that_is_not_core_still_files_its_issue(self):
        with exempt():
            code, out, err, fake = self.run_main(r70.gates_doc("proposal"))
        self.assertEqual(code, 0, err)
        writes = fake.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], ["issue", "create"])

    def test_the_plan_names_why(self):
        with exempt(skills=[r70.SKILL]):
            plan = retire_skill.plan(r70.gates_doc("proposal"), {}, r70.FakeGitHub().gh)
        self.assertEqual(plan["action"], "none")
        self.assertIn("core item", plan["reason"])


class GuidanceRetirementTests(unittest.TestCase):
    def test_a_core_section_with_a_retire_verdict_opens_nothing(self):
        fake = g124.FakeGh()
        with exempt(guidance=["bravo"]):
            plan = retire_skill.guidance_plan(g124.guidance_gates(), None, fake)
        self.assertEqual(plan["action"], "none")
        self.assertIn("core item", plan["reason"])
        self.assertEqual(fake.calls, [])

    def test_a_section_that_is_not_core_still_files_its_issue(self):
        with exempt(guidance=["alpha"]):
            plan = retire_skill.guidance_plan(g124.guidance_gates(), None, g124.FakeGh())
        self.assertEqual(plan["action"], "create-issue")


if __name__ == "__main__":
    unittest.main()
