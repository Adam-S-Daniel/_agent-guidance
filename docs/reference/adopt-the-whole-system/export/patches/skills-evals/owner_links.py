"""Live output links the adopter's own docs, not the source's issue numbers.

Five scripts and one workflow write text the adopter's bots publish (pull
request bodies, impact entries, a scaffold's tracking.json) with a link to
an issue that tracked the work in the source repos. Two kinds:

* links to the source owner's issues (P2 citations, so substitution left
  them pointing at the source): `propose_guidance_edit.py`'s tracking issue,
  `propose_skill_edit.py`'s, `scaffold_real_work.py`'s guidance scaffold
  issues, and `routine-scaffold-gate.yml`'s real-work fixture issue;
* links built on the adopter's own repo with the source's issue numbers
  (`improve_gate.py`'s #71, `retire_skill.py`'s #70 and #124), which point
  at whatever issue the adopter happens to have under that number.

The status board the source's AGENTS.md and how-it-works.md point at (an
issue of the source owner's) becomes "your own status board, linked from
HANDOFF.md", which ships as a blank template (overlay). Each other link
becomes a link to the decision record in the adopter's own skills-evals
that holds the same rule (ADR 0005 for the improvement loop, ADR 0014 for
removals), or is dropped where the issue only said "part of", and the
scaffold's tracking fields are null. The tests that pinned the old text are
updated to the new and assert that no issue link is left. Runs on the
substituted tree, so the adopter's repo is `example-adopter/skills-evals`.
"""

from __future__ import annotations

from pathlib import Path

EVALS = "https://github.com/example-adopter/skills-evals"
ADR5 = f"{EVALS}/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md"
ADR14_F = ("https://github.com/{EVALS_REPO}/blob/main/docs/decisions/"
           "0014-removal-pull-requests-merge-without-review.md")

EDITS = {
    "AGENTS.md": [
        ("  stands, what is parked on the owner and why, and how to resume it. The live\n"
         "  operational state is the status board,\n"
         "  [skills-evals#126](https://github.com/Adam-S-Daniel/skills-evals/issues/126).\n",
         "  stands, what is parked on the owner and why, and how to resume it. Keep the\n"
         "  live operational state on a status-board issue of your own, linked from\n"
         "  `HANDOFF.md`'s \"Current state\".\n"),
    ],
    "docs/how-it-works.md": [
        ("operating record in [`HANDOFF.md`](../HANDOFF.md), and the live status board is\n"
         "[issue #126](https://github.com/Adam-S-Daniel/skills-evals/issues/126).\n",
         "operating record, with a link to your live status board, in\n"
         "[`HANDOFF.md`](../HANDOFF.md).\n"),
    ],
    "scripts/propose_guidance_edit.py": [
        ('#: The issue the PR body refers to; never with a closing keyword.\n'
         'TRACKING_ISSUE = "Adam-S-Daniel/skills-evals#122"\n',
         '#: The decision the PR body refers to (skills-evals ADR 0005); never an issue.\n'
         'TRACKING_ISSUE = "skills-evals ADR 0005"\n'),
    ],
    "test/issues/test_issue_propose_guidance_edit.py": [
        ('        self.assertIn("Part of Adam-S-Daniel/skills-evals#122", body)\n',
         '        self.assertIn("Part of skills-evals ADR 0005", body)\n'
         '        self.assertNotIn("/issues/", body)\n'),
    ],
    "scripts/propose_skill_edit.py": [
        ('             "Produced by skills-evals `scripts/propose_skill_edit.py` "\n'
         '             "([#71](https://github.com/Adam-S-Daniel/skills-evals/issues/71)). "\n',
         '             "Produced by skills-evals `scripts/propose_skill_edit.py` "\n'
         '             "(skills-evals ADR 0005). "\n'),
    ],
    "scripts/scaffold_real_work.py": [
        ('GUIDANCE_SOURCE_ISSUE = "https://github.com/Adam-S-Daniel/skills-evals/issues/98"\n'
         'GUIDANCE_PARENT_ISSUE = "https://github.com/Adam-S-Daniel/skills-evals/issues/96"\n',
         '# The tracking issues of a scaffold: none by default. The source repo named\n'
         '# its own two here; an adopter may set theirs.\n'
         'GUIDANCE_SOURCE_ISSUE = None\n'
         'GUIDANCE_PARENT_ISSUE = None\n'),
    ],
    "test/issues/test_issue_98.py": [
        ('        self.assertEqual(tracking["parent_issue"],\n'
         '                         "https://github.com/Adam-S-Daniel/skills-evals/issues/96")\n'
         '        self.assertEqual(tracking["source_issue"],\n'
         '                         "https://github.com/Adam-S-Daniel/skills-evals/issues/98")\n',
         '        self.assertIsNone(tracking["parent_issue"])\n'
         '        self.assertIsNone(tracking["source_issue"])\n'),
    ],
    ".github/workflows/routine-scaffold-gate.yml": [
        ('            echo "Every push to \\`${BRANCH}\\` turns auto-merge off; only a push that passes the gate again turns it back on."\n'
         '            echo\n'
         '            echo "Part of https://github.com/Adam-S-Daniel/skills-evals/issues/65."\n',
         '            echo "Every push to \\`${BRANCH}\\` turns auto-merge off; only a push that passes the gate again turns it back on."\n'),
    ],
    "test/issues/test_issue_real_work_scaffold.py": [
        ('        self.assertIn("Part of https://github.com/Adam-S-Daniel/skills-evals/issues/65", body)\n',
         '        self.assertIsNone(re.search(r"https://\\S+/issues/", body))\n'),
    ],
    "scripts/improve_gate.py": [
        ('            "rule accepted its proposed edit "\n'
         '            "([skills-evals#71](https://github.com/example-adopter/skills-evals/issues/71)).\\n"\n',
         '            "rule accepted its proposed edit "\n'
         f'            "([skills-evals ADR 0005]({ADR5})).\\n"\n'),
        ('            "not count toward the three human-reviewed loop pull requests of "\n'
         '            "[skills-evals#71](https://github.com/example-adopter/skills-evals/issues/71). "\n',
         '            "not count toward the three human-reviewed loop pull requests of "\n'
         f'            "[skills-evals ADR 0005]({ADR5}). "\n'),
        ('            "pull requests that must merge before any scheduled loop "\n'
         '            "([skills-evals#71](https://github.com/example-adopter/skills-evals/issues/71))."]\n',
         '            "pull requests that must merge before any scheduled loop "\n'
         f'            "([skills-evals ADR 0005]({ADR5}))."]\n'),
        ('        "Part of https://github.com/example-adopter/skills-evals/issues/71.",\n',
         f'        "Part of the improvement loop ({ADR5}).",\n'),
    ],
    "test/issues/test_issue_routine_improve.py": [
        ('                self.assertIn("Part of https://github.com/example-adopter/"\n'
         '                              "skills-evals/issues/71", body)\n',
         '                self.assertIn("Part of the improvement loop (https://github.com/"\n'
         '                              "example-adopter/skills-evals/blob/main/docs/"\n'
         '                              "decisions/0005-improvement-loop-reuses-skill-"\n'
         '                              "creator.md)", body)\n'
         '                self.assertNotIn("/issues/", body)\n'),
    ],
    "scripts/retire_skill.py": [
        ('            f"([skills-evals#70](https://github.com/{EVALS_REPO}/issues/70)): every cell "\n',
         f'            f"([skills-evals ADR 0014]({ADR14_F})): every cell "\n'),
        ('            f"[skills-evals#70](https://github.com/{EVALS_REPO}/issues/70) is "\n',
         f'            f"[skills-evals ADR 0014]({ADR14_F}) is "\n'),
        ('                     f"[skills-evals#124](https://github.com/{EVALS_REPO}/issues/124) is "\n',
         f'                     f"[skills-evals ADR 0014]({ADR14_F}) is "\n'),
    ],
    "test/issues/test_issue_70_retire.py": [
        ('            "([skills-evals#70](https://github.com/example-adopter/skills-evals/issues/70)): "\n',
         '            "([skills-evals ADR 0014](https://github.com/example-adopter/skills-evals/blob/main/"\n'
         '            "docs/decisions/0014-removal-pull-requests-merge-without-review.md)): "\n'),
    ],
}


def apply(root: Path, ctx) -> None:
    for rel, edits in EDITS.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                raise RuntimeError(f"{rel}: expected one occurrence of {old[:70]!r}, found {n}")
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
