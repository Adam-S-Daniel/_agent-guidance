#!/usr/bin/env python3
"""evals/skills-doctor/one-row-rejects-the-lock.

The seed is a cloud session capture where one row of `skills.lock` carries an
uppercase digest, which the bootstrap hook's lock reader refuses, so the whole
lock is refused and none of its four skills installs. The skill's answer
counts every locked skill as affected, names the row, names the case as the
defect, and leaves the lock alone.

The shared contract (shape, anchoring, invented data, pristine asymmetry,
good result, one edit per check, whole wrong answers) is `FixtureContract` in
test_issue_skills_doctor_personal_copy_outranks_repo.py. Beyond it, this
module pins the seed fact the fixture rests on: the hook's own digest pattern
refuses this row and accepts the other three.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 -m pytest test/issues/test_issue_skills_doctor_one_row_rejects_the_lock.py -q`.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_issue_skills_doctor_personal_copy_outranks_repo as contract  # noqa: E402

FINDINGS = contract.FINDINGS
findings_edit = contract.findings_edit

EVAL_DIR = contract.SKILL_DIR / "one-row-rejects-the-lock"
LOCK = Path("session") / "skills.lock"
BAD_ROW = "acsi-coding-anywhere/workflow-path-audit"
# The hook's reader, `.claude/hooks/skills-bootstrap.sh` in acsi-agentskills,
# and `check_provenance.py` beside the skill both match a digest with this.
HOOK_DIGEST = re.compile(r"(?:sha256:)?([0-9a-f]{64})")

AFFECTED_LINE = "Skills affected: 4 of 4\n"
CAUSE_LINE = f"Cause: the {BAD_ROW} row in session/skills.lock.\n"
CASE_SENTENCE = "Its digest is written in uppercase hex, which the hook's lock reader refuses."

GOOD_FINDINGS = f"""\
# Findings

{AFFECTED_LINE}{CAUSE_LINE}
{CASE_SENTENCE} The refusal is of the whole lock and happens before the
install loop, so one bad row kept all four skills out of the session, which
is what the DEGRADED line's "could not read" says.

Nothing here was changed. Regenerate the lock with
scripts/generate_skills_lock.py rather than editing the row by hand.
"""


def lowercase_the_lock(ws: Path) -> None:
    path = ws / LOCK
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["skills"][BAD_ROW] = doc["skills"][BAD_ROW].lower()
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


class TestSkillsDoctorOneRowRejectsTheLock(contract.FixtureContract, unittest.TestCase):
    EVAL_DIR = EVAL_DIR
    GOOD = GOOD_FINDINGS
    EXPECTED_PRISTINE = {
        "findings-written": False,
        "every-locked-skill-is-affected": False,
        "the-refused-row-is-named": False,
        "the-case-is-the-defect": False,
        "the-lock-was-not-repaired": True,
    }

    def test_seed_lock_is_json_and_only_the_named_row_is_refused(self):
        doc = json.loads((EVAL_DIR / "seed" / LOCK).read_text(encoding="utf-8"))
        refused = sorted(key for key, digest in doc["skills"].items()
                         if not HOOK_DIGEST.fullmatch(digest))
        self.assertEqual(refused, [BAD_ROW])
        self.assertEqual(len(doc["skills"]), 4, "the 4 of 4 the fixture asserts")
        self.assertTrue(HOOK_DIGEST.fullmatch(doc["skills"][BAD_ROW].lower()),
                        "the row differs from an accepted one by case alone")

    def _mutations(self):
        def findings_elsewhere(ws):
            (ws / FINDINGS).rename(ws / "session" / FINDINGS)

        behavior = {"findings-written", "every-locked-skill-is-affected",
                    "the-refused-row-is-named", "the-case-is-the-defect"}
        return {
            "findings_elsewhere": (findings_elsewhere, behavior),
            "one_of_four": (findings_edit(AFFECTED_LINE, "Skills affected: 1 of 4\n"),
                            {"every-locked-skill-is-affected"}),
            "both_counts": (findings_edit(AFFECTED_LINE,
                                          AFFECTED_LINE + "Skills affected: 1 of 4\n"),
                            {"every-locked-skill-is-affected"}),
            "cause_names_the_fetch": (
                findings_edit(CAUSE_LINE, "Cause: the registry fetch failed.\n"),
                {"the-refused-row-is-named"}),
            "case_not_named": (findings_edit(CASE_SENTENCE, "Its digest is malformed."),
                               {"the-case-is-the-defect"}),
            "lock_repaired": (lowercase_the_lock, {"the-lock-was-not-repaired"}),
        }

    def _wrong_answers(self):
        only_that_skill = f"""\
# Findings

Skills affected: 1 of 4
Cause: the {BAD_ROW} row has an uppercase digest.

The other three skills must have failed for a separate reason, probably the
registry fetch.
"""
        network = """\
# Findings

Skills affected: 4 of 4
Cause: none in the lock; the lock is valid JSON.

The hook could not fetch the registry, most likely a network or proxy
failure on the container. Retry with a fresh session.
"""

        def fixed_it(ws):
            (ws / FINDINGS).write_text(GOOD_FINDINGS, encoding="utf-8")
            lowercase_the_lock(ws)

        return {
            "one_bad_row_one_bad_skill": (
                lambda ws: (ws / FINDINGS).write_text(only_that_skill, encoding="utf-8"),
                {"every-locked-skill-is-affected"}),
            "blames_the_network": (
                lambda ws: (ws / FINDINGS).write_text(network, encoding="utf-8"),
                {"the-refused-row-is-named", "the-case-is-the-defect"}),
            "right_diagnosis_then_lowercased_the_digest": (
                fixed_it, {"the-lock-was-not-repaired"}),
        }


if __name__ == "__main__":
    unittest.main()
