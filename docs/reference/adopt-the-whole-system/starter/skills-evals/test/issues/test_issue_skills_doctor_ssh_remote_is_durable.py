#!/usr/bin/env python3
"""evals/skills-doctor/ssh-remote-is-durable.

The seed is a capture from a VM reached over SSH: entrypoint `ssh-remote`,
no remote session id, nothing forced. The operator reads it as a remote
session whose bootstrap hook failed. The skill's answer classifies it as
durable (the hook's test is a prefix, never a substring), finds delivery
healthy through the marketplace plugins, and installs nothing.

The shared contract (shape, anchoring, invented data, pristine asymmetry,
good result, one edit per check, whole wrong answers) is `FixtureContract` in
test_issue_skills_doctor_personal_copy_outranks_repo.py. Beyond it, this
module pins the seed facts the fixture rests on: the captured surface fails
all three of the hook's arms, and every locked skill is in the session's
listing under its bundle's namespace, from a plugin enabled at the lock's
commit.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 -m pytest test/issues/test_issue_skills_doctor_ssh_remote_is_durable.py -q`.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_issue_skills_doctor_personal_copy_outranks_repo as contract  # noqa: E402

FINDINGS = contract.FINDINGS
findings_edit = contract.findings_edit

EVAL_DIR = contract.SKILL_DIR / "ssh-remote-is-durable"
SEED = EVAL_DIR / "seed"
SESSION = Path("session")

SURFACE_LINE = "Surface: durable\n"
VERDICT_LINE = "Verdict: OK\n"
CHANNEL_SENTENCE = ("All three locked skills reach the session through the "
                    "marketplace plugins, enabled at the lock's commit.")

GOOD_FINDINGS = f"""\
# Findings

{SURFACE_LINE}{VERDICT_LINE}
No remote session id is set, SKILLS_BOOTSTRAP_FORCE is not set, and the
entrypoint `ssh-remote` does not begin with `remote`, so the bootstrap hook
skips this session by design. {CHANNEL_SENTENCE} An empty personal store
and an absent install record are what this surface should have.

Nothing here was changed, and nothing needs to be.
"""


def surface_reading(text: str) -> dict[str, str]:
    """The captured `entry=… remote=… force=… env=…` line as a mapping."""
    line = text.splitlines()[-1]
    return dict(field.split("=", 1) for field in line.split(" "))


class TestSkillsDoctorSshRemoteIsDurable(contract.FixtureContract, unittest.TestCase):
    EVAL_DIR = EVAL_DIR
    GOOD = GOOD_FINDINGS
    EXPECTED_PRISTINE = {
        "findings-written": False,
        "the-surface-is-durable": False,
        "delivery-is-ok": False,
        "the-delivering-channel-is-named": False,
        "nothing-in-the-capture-was-repaired": True,
        "no-skill-was-installed-by-hand": True,
    }

    def test_captured_surface_fails_all_three_of_the_hooks_arms(self):
        reading = surface_reading((SEED / SESSION / "surface.txt").read_text(encoding="utf-8"))
        self.assertEqual(reading["entry"], "ssh-remote")
        self.assertEqual(reading["remote"], "", "no remote session id")
        self.assertEqual(reading["force"], "", "not forced")
        self.assertFalse(reading["entry"].startswith("remote"), "the prefix arm")
        self.assertIn("remote", reading["entry"], "the substring reading is the trap")

    def test_every_locked_skill_is_delivered_by_an_enabled_plugin_at_the_lock(self):
        lock = json.loads((SEED / SESSION / "skills.lock").read_text(encoding="utf-8"))
        plugins = json.loads((SEED / SESSION / "plugin-list.json").read_text(encoding="utf-8"))
        listing = (SEED / SESSION / "skill-listing.txt").read_text(encoding="utf-8").split()
        enabled = {p["id"].split("@")[0] for p in plugins
                   if p["enabled"] and p["gitCommitSha"] == lock["ref"]}
        self.assertEqual(enabled, set(lock["bundles"]))
        for key in lock["skills"]:
            bundle, name = key.split("/")
            with self.subTest(skill=key):
                self.assertIn(f"{bundle}:{name}", listing)
        self.assertFalse((SEED / SESSION / "skills" / ".skills-bootstrap-installed.json").exists())

    def _mutations(self):
        def findings_elsewhere(ws):
            (ws / FINDINGS).rename(ws / SESSION / FINDINGS)

        def surface_capture_edited(ws):
            path = ws / SESSION / "surface.txt"
            path.write_text(path.read_text(encoding="utf-8").replace(
                "entry=ssh-remote", "entry=remote"), encoding="utf-8")

        def bundle_skill_copied_in(ws):
            target = ws / SESSION / "skills" / "writing-adrs"
            target.mkdir()
            (target / "SKILL.md").write_text("---\nname: writing-adrs\n---\n", encoding="utf-8")

        behavior = {"findings-written", "the-surface-is-durable", "delivery-is-ok",
                    "the-delivering-channel-is-named"}
        return {
            "findings_elsewhere": (findings_elsewhere, behavior),
            "surface_ephemeral": (findings_edit(SURFACE_LINE, "Surface: ephemeral\n"),
                                  {"the-surface-is-durable"}),
            "surface_both": (findings_edit(SURFACE_LINE, SURFACE_LINE + "Surface: ephemeral\n"),
                             {"the-surface-is-durable"}),
            "verdict_degraded": (findings_edit(VERDICT_LINE, "Verdict: DEGRADED\n"),
                                 {"delivery-is-ok"}),
            "channel_not_named": (
                findings_edit(CHANNEL_SENTENCE, "All three locked skills are in the listing."),
                {"the-delivering-channel-is-named"}),
            "surface_capture_edited": (surface_capture_edited,
                                       {"nothing-in-the-capture-was-repaired"}),
            "bundle_skill_copied_in": (bundle_skill_copied_in,
                                       {"no-skill-was-installed-by-hand"}),
        }

    def _wrong_answers(self):
        substring_reading = """\
# Findings

Surface: ephemeral
Verdict: DEGRADED

The entrypoint is ssh-remote, so this is a remote session and the bootstrap
hook should have installed the three locked skills into ~/.claude/skills/.
There is no install record, so the hook never ran. Re-run it with
SKILLS_BOOTSTRAP_FORCE=1.
"""
        right_verdict_wrong_surface = """\
# Findings

Surface: ephemeral
Verdict: OK

The hook did not run, but the marketplace plugins deliver all three skills.
"""

        def installed_them_too(ws):
            (ws / FINDINGS).write_text(GOOD_FINDINGS, encoding="utf-8")
            store = ws / SESSION / "skills"
            for name in ("writing-adrs", "debug-github-workflows", "workflow-path-audit"):
                (store / name).mkdir()
                (store / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n",
                                                       encoding="utf-8")
            shutil.copyfile(ws / SESSION / "skills.lock",
                            store / ".skills-bootstrap-installed.json")

        return {
            "substring_reading": (
                lambda ws: (ws / FINDINGS).write_text(substring_reading, encoding="utf-8"),
                {"the-surface-is-durable", "delivery-is-ok",
                 "the-delivering-channel-is-named"}),
            "right_verdict_wrong_surface": (
                lambda ws: (ws / FINDINGS).write_text(right_verdict_wrong_surface,
                                                      encoding="utf-8"),
                {"the-surface-is-durable"}),
            "right_diagnosis_then_installed_by_hand": (
                installed_them_too, {"no-skill-was-installed-by-hand"}),
        }


if __name__ == "__main__":
    unittest.main()
