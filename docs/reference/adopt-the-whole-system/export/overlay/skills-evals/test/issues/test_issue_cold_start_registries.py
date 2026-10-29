"""Cold-start registry behavior the kit adds to skills-evals (DECISIONS
"Cold-start fixes (2026-10-29)").

- A registry entry may declare `may_be_empty: true`; the coverage census then
  resolves it with 0 skills. Every registry without the key keeps the
  refusal ("yields no skills").
- The kit ships exactly two registries, and only the private one may be empty.
- The coverage script exits 0 given only those two registries, the private
  one empty.

Invented data only. Deterministic: no network, clock or sleeps.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "harness"))
import eval_coverage  # noqa: E402
import run_eval  # noqa: E402

REGISTRIES_YML = REPO / "harness" / "registries.yml"
SCRIPT = REPO / "scripts" / "eval_coverage.py"
LAYOUT = "plugins/*/skills/*/SKILL.md"


def entry(path: Path, **extra) -> dict:
    return {"name": "acsi-test", "path": path, "layout": LAYOUT, **extra}


class EnumerateSkills(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)

    def test_a_declared_empty_registry_resolves_with_no_skills(self):
        self.assertEqual(eval_coverage.enumerate_skills(entry(self.path, may_be_empty=True)), [])

    def test_an_undeclared_empty_registry_is_still_refused(self):
        for extra in ({}, {"may_be_empty": False}):
            with self.subTest(extra=extra):
                with self.assertRaises(eval_coverage.CensusRefusal) as ctx:
                    eval_coverage.enumerate_skills(entry(self.path, **extra))
                self.assertIn("yields no skills", str(ctx.exception))

    def test_a_declared_registry_with_skills_still_lists_them(self):
        skill = self.path / "plugins" / "plug-a" / "skills" / "alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: alpha\ndescription: stand-in.\n---\n", encoding="utf-8")
        rows = eval_coverage.enumerate_skills(entry(self.path, may_be_empty=True))
        self.assertEqual([(r["bundle"], r["skill"]) for r in rows], [("plug-a", "alpha")])


class ShippedRegistries(unittest.TestCase):
    def test_exactly_the_two_acsi_registries_and_only_the_private_one_may_be_empty(self):
        doc = yaml.safe_load(REGISTRIES_YML.read_text(encoding="utf-8"))
        flags = {e["name"]: e.get("may_be_empty", False) for e in doc["registries"]}
        self.assertEqual(flags, {"acsi-agentskills": False, "acsi-agentskills-private": True})

    def test_the_flag_reaches_the_resolved_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            flags = [f"{name}={tmp}" for name in
                     ("acsi-agentskills", "acsi-agentskills-private")]
            resolved = run_eval.resolve_registries(flags, None, REPO)
        self.assertEqual({n: e["may_be_empty"] for n, e in resolved.items()},
                         {"acsi-agentskills": False, "acsi-agentskills-private": True})

    def test_a_non_boolean_flag_is_rejected(self):
        for bad in ('"yes"', "1"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "registries.yml"
                config.write_text(
                    "registries:\n  - name: a\n    url: https://example.com/o/a\n"
                    f"    layout: \"{LAYOUT}\"\n    may_be_empty: {bad}\n", encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    run_eval._load_registries_config(config)
                self.assertIn("may_be_empty", str(ctx.exception))


class CensusScript(unittest.TestCase):
    """The cold-start repro: both registries given, the private one empty."""

    def run_census(self, public_skill: bool):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, private = root / "public", root / "private"
            public.mkdir()
            private.mkdir()
            if public_skill:
                skill = public / "plugins" / "plug-a" / "skills" / "alpha" / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text("---\nname: alpha\ndescription: stand-in.\n---\n",
                                 encoding="utf-8")
            fixture = root / "evals" / "alpha" / "fixture.yaml"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(yaml.safe_dump({
                "skill": "alpha", "registry": "https://github.com/example-adopter/acsi-agentskills"}),
                encoding="utf-8")
            skips = root / "non-coverage.yml"
            skips.write_text("skips: []\n", encoding="utf-8")
            argv = [sys.executable, str(SCRIPT), "--json", "--check",
                    "--evals-dir", str(root / "evals"), "--non-coverage", str(skips),
                    "--registry", f"acsi-agentskills={public}",
                    "--registry", f"acsi-agentskills-private={private}"]
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(root), "LC_ALL": "C.UTF-8"}
            return subprocess.run(argv, capture_output=True, text=True, env=env,
                                  cwd=root, timeout=120)

    def test_an_empty_private_registry_does_not_stop_the_census(self):
        proc = self.run_census(public_skill=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        counts = {r["name"]: r["counts"]["total"] for r in doc["registries"]}
        self.assertEqual(counts, {"acsi-agentskills": 1, "acsi-agentskills-private": 0})

    def test_an_empty_public_registry_is_still_refused(self):
        proc = self.run_census(public_skill=False)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("yields no skills", proc.stderr)


if __name__ == "__main__":
    unittest.main()
