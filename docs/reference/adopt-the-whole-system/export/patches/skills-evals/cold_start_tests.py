"""Tests adapted to the cold-start registry fixes (DECISIONS "Cold-start fixes
(2026-10-29)").

`cold_start_registries.py` drops the invented `example-site-platform` and
`example-site-a` registries from `harness/registries.yml`. The source's tests
use those two names as stand-ins for "a second public registry" and for the
flat and dot-dir layouts. Each site is moved to a registry the kit still
ships: `acsi-agentskills` for the public cases and `acsi-agentskills-private`
for the "override a registry by name" cases of TestIssue63. Every assertion
keeps its subject; the counts follow the new denominator.

What does not survive: the census-level checks that a flat `skills/*/SKILL.md`
and a dot-dir `.claude/skills/*/SKILL.md` registry resolve (they ran against
the two dropped registries). TestIssue63's `test_resolves_flat_skills_layout`
and `test_resolves_dotclaude_skills_layout` still run the harness's layout
resolver on both shapes, passing the layout directly.

Runs on the substituted tree. Every replacement states how many times it must
match, so a source change that moves the text fails the export.
"""

from __future__ import annotations

from pathlib import Path


def _sub(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    got = text.count(old)
    if got != count:
        raise SystemExit(f"cold_start_tests: {rel}: expected {count} occurrence(s), found {got}: {old[:70]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def apply(root: Path, ctx) -> None:
    # --- test/issues/test_issue_adr0012_delivery.py
    _sub(root, "test/issues/test_issue_adr0012_delivery.py",
         '        for name in ("example-site-platform", "example-site-a", "acsi-agentskills-private"):\n',
         '        for name in ("acsi-agentskills-private",):\n')

    # --- test/issues/test_issue_60.py: one public registry, so 5 public gaps
    t60 = "test/issues/test_issue_60.py"
    _sub(root, t60, "        self.assertEqual(len(gaps.public_gaps(census)), 7)\n",
         "        self.assertEqual(len(gaps.public_gaps(census)), 5)\n")
    _sub(root, t60, "            self.assertEqual(len(gaps.public_gaps(out)), 7)\n",
         "            self.assertEqual(len(gaps.public_gaps(out)), 5)\n")
    _sub(root, t60,
         "    def test_pr_only_resumes_and_unproven_issue_only_is_ignored(self):\n"
         "        census, sources = world(public_gaps=2)\n",
         "    def test_pr_only_resumes_and_unproven_issue_only_is_ignored(self):\n"
         "        census, sources = world(public_gaps=3)\n")
    _sub(root, t60, "        self.assertEqual(len(checkouts), 6)\n",
         "        self.assertEqual(len(checkouts), 4)\n")

    # --- test/issues/test_issue_64.py: a two-registry census
    t64 = "test/issues/test_issue_64.py"
    _sub(root, t64,
         '        self.add_skill("example-site-platform", "cms-covered")\n'
         '        self.add_skill("example-site-platform", "cms-gap")\n'
         '        self.add_skill("example-site-a", "site-skill")\n',
         '        self.add_skill("acsi-agentskills", "zeta", "plug-b")\n'
         '        self.add_skill("acsi-agentskills", "eta", "plug-b")\n')
    _sub(root, t64,
         '        self.add_fixture("cms-covered", "cms-covered", "example-site-platform")\n',
         '        self.add_fixture("zeta", "zeta", "acsi-agentskills")\n')
    _sub(root, t64,
         '        self.skip("example-site-platform", "cms-gap")\n'
         '        self.skip("example-site-a", "site-skill")\n',
         '        self.skip("acsi-agentskills", "eta")\n')
    _sub(root, t64,
         '             "epsilon": ("gap", 0, "plug-b")})\n',
         '             "epsilon": ("gap", 0, "plug-b"),\n'
         '             "zeta": ("covered", 1, "plug-b"),\n'
         '             "eta": ("gap", 0, "plug-b")})\n')
    _sub(root, t64,
         '        # flat and dot-dir layouts resolve too, with no bundle\n'
         '        cms = rows(doc, "example-site-platform")\n'
         '        self.assertEqual(cms["cms-covered"]["status"], "covered")\n'
         '        self.assertEqual(cms["cms-gap"]["status"], "gap")\n'
         '        self.assertIsNone(cms["cms-gap"]["bundle"])\n'
         '        self.assertEqual(rows(doc, "example-site-a")["site-skill"]["status"], "gap")\n',
         '')
    _sub(root, t64,
         '                         {"total": 5, "covered": 2, "skipped": 1, "gap": 2})\n'
         '        self.assertEqual(doc["totals"],\n'
         '                         {"total": 10, "covered": 4, "skipped": 1, "gap": 5})\n',
         '                         {"total": 7, "covered": 3, "skipped": 1, "gap": 3})\n'
         '        self.assertEqual(doc["totals"],\n'
         '                         {"total": 9, "covered": 4, "skipped": 1, "gap": 4})\n')
    _sub(root, t64,
         '        # same skill name in a different registry must not cover cms-gap\'s\n',
         '        # same skill name in a different registry must not cover eta\'s\n')
    _sub(root, t64,
         '        w.add_fixture("cms-gap", "cms-gap", "acsi-agentskills")  # wrong registry\n',
         '        w.add_fixture("eta-elsewhere", "eta", PRIVATE)  # wrong registry\n')
    _sub(root, t64,
         '        self.assertEqual(rows(doc, "example-site-platform")["cms-gap"]["status"], "gap")\n',
         '        self.assertEqual(rows(doc, "acsi-agentskills")["eta"]["status"], "gap")\n')
    _sub(root, t64, '        self.assertIn("| acsi-agentskills | 5 | 2 | 1 | 2 |", md)\n',
         '        self.assertIn("| acsi-agentskills | 7 | 3 | 1 | 3 |", md)\n')
    _sub(root, t64, '        self.assertIn("| **Total** | 10 | 4 | 1 | 5 |", md)\n',
         '        self.assertIn("| **Total** | 9 | 4 | 1 | 4 |", md)\n')
    _sub(root, t64,
         '        self.assert_refused(World(self).run(omit=("example-site-a",)),\n'
         '                            "has no --registry example-site-a=PATH")\n',
         '        self.assert_refused(World(self).run(omit=("acsi-agentskills",)),\n'
         '                            "has no --registry acsi-agentskills=PATH")\n')
    _sub(root, t64,
         '                         {"total": 10, "covered": 4, "skipped": 1, "gap": 5})\n'
         '        self.assertEqual(report["fixture_readiness"],\n',
         '                         {"total": 9, "covered": 4, "skipped": 1, "gap": 4})\n'
         '        self.assertEqual(report["fixture_readiness"],\n')
    _sub(root, t64, "        self.assertEqual(len(checkouts), 5)\n",
         "        self.assertEqual(len(checkouts), 3)\n")
    _sub(root, t64, '"4 covered · 1 skipped · 5 gap · 2026-10-08"',
         '"4 covered · 1 skipped · 4 gap · 2026-10-08"')
    # What is left names the one invented public registry that remains in use
    # as a stand-in; the public registry that stays is acsi-agentskills.
    t = (root / t64).read_text(encoding="utf-8")
    t = t.replace("example-site-platform", "acsi-agentskills").replace("cms-gap", "eta")
    (root / t64).write_text(t, encoding="utf-8")

    # --- test/run_tests.py: TestIssue63 and its review rounds
    rt = "test/run_tests.py"
    _sub(root, rt,
         "    plugins/*/skills/*/SKILL.md — example-site-platform's flat skills/*/SKILL.md and\n"
         "    example-site-a's .claude/skills/*/SKILL.md must resolve too, and an\n",
         "    plugins/*/skills/*/SKILL.md — a flat skills/*/SKILL.md and a\n"
         "    .claude/skills/*/SKILL.md layout must resolve too, and an\n")
    _sub(root, rt,
         "        # example-site-platform-shaped: skills/<skill>/SKILL.md. Two skills present —\n",
         "        # flat-shaped: skills/<skill>/SKILL.md. Two skills present —\n")
    _sub(root, rt,
         "        # example-site-a-shaped: .claude/skills/<skill>/SKILL.md. Two skills\n",
         "        # dot-dir-shaped: .claude/skills/<skill>/SKILL.md. Two skills\n")
    _sub(root, rt,
         '            registry = self._fake_registry(tmp, "skills/some-skill/SKILL.md")\n'
         '            registries = run_eval.resolve_registries(\n'
         '                [f"example-site-platform={registry}"], None, REPO_ROOT)\n'
         '            entry = run_eval.registry_for_url(\n'
         '                registries, "https://github.com/example-adopter/example-site-platform")\n'
         '            self.assertEqual(entry["path"], registry)\n'
         '            self.assertEqual(entry["layout"], "skills/*/SKILL.md")\n',
         '            registry = self._fake_registry(\n'
         '                tmp, "plugins/a-bundle/skills/some-skill/SKILL.md")\n'
         '            registries = run_eval.resolve_registries(\n'
         '                [f"acsi-agentskills-private={registry}"], None, REPO_ROOT)\n'
         '            entry = run_eval.registry_for_url(\n'
         '                registries, "https://github.com/example-adopter/acsi-agentskills-private")\n'
         '            self.assertEqual(entry["path"], registry)\n'
         '            self.assertEqual(entry["layout"], "plugins/*/skills/*/SKILL.md")\n')
    _sub(root, rt,
         '            registry = self._fake_registry(tmp, ".claude/skills/some-skill/SKILL.md")\n'
         '            env_value = f"example-site-a={registry}"\n'
         '            registries = run_eval.resolve_registries(None, env_value, REPO_ROOT)\n'
         '            entry = run_eval.registry_for_url(\n'
         '                registries, "https://github.com/example-adopter/example-site-a")\n',
         '            registry = self._fake_registry(\n'
         '                tmp, "plugins/a-bundle/skills/some-skill/SKILL.md")\n'
         '            env_value = f"acsi-agentskills-private={registry}"\n'
         '            registries = run_eval.resolve_registries(None, env_value, REPO_ROOT)\n'
         '            entry = run_eval.registry_for_url(\n'
         '                registries, "https://github.com/example-adopter/acsi-agentskills-private")\n')
    _sub(root, rt,
         '        entry = registries["example-site-platform"]\n'
         '        self.assertEqual(entry["source"], "sibling default")\n',
         '        entry = registries["acsi-agentskills-private"]\n'
         '        self.assertEqual(entry["source"], "sibling default")\n')
    _sub(root, rt,
         '(REPO_ROOT.parent / "example-site-platform").resolve())',
         '(REPO_ROOT.parent / "acsi-agentskills-private").resolve())')
    _sub(root, rt,
         '                None, "example-site-platform=/a,example-site-platform=/b", REPO_ROOT)\n'
         '        self.assertIn("example-site-platform", str(ctx.exception))\n',
         '                None, "acsi-agentskills-private=/a,acsi-agentskills-private=/b", REPO_ROOT)\n'
         '        self.assertIn("acsi-agentskills-private", str(ctx.exception))\n')
    _sub(root, rt,
         '        for name in ("acsi-agentskills", "example-site-platform", "example-site-a"):\n',
         '        for name in ("acsi-agentskills", "acsi-agentskills-private"):\n')
    _sub(root, rt,
         '            [f"example-site-platform={bad_path}"], None, REPO_ROOT)\n',
         '            [f"acsi-agentskills-private={bad_path}"], None, REPO_ROOT)\n')
    _sub(root, rt,
         '        msg = str(ctx.exception)\n'
         '        self.assertIn("example-site-platform", msg)\n'
         '        self.assertIn("--registry flag", msg)\n',
         '        msg = str(ctx.exception)\n'
         '        self.assertIn("acsi-agentskills-private", msg)\n'
         '        self.assertIn("--registry flag", msg)\n')
    _sub(root, rt,
         '            ["example-site-platform=../example-site-platform"], None, REPO_ROOT)\n'
         '        self.assertTrue(registries["example-site-platform"]["path"].is_absolute())\n',
         '            ["acsi-agentskills-private=../acsi-agentskills-private"], None, REPO_ROOT)\n'
         '        self.assertTrue(registries["acsi-agentskills-private"]["path"].is_absolute())\n')
    _sub(root, rt,
         '        self.assertEqual(names, {"acsi-agentskills", "example-site-platform", "example-site-a",\n'
         '                                 "acsi-agentskills-private"})\n',
         '        self.assertEqual(names, {"acsi-agentskills", "acsi-agentskills-private"})\n')
    _sub(root, rt,
         '                ["example-site-platform=/a", "example-site-platform=/b"], None, REPO_ROOT)\n'
         '        self.assertIn("example-site-platform", str(ctx.exception))\n',
         '                ["acsi-agentskills-private=/a", "acsi-agentskills-private=/b"], None, REPO_ROOT)\n'
         '        self.assertIn("acsi-agentskills-private", str(ctx.exception))\n')

    # --- test/issues/test_issue_70_retire.py: "another registry" is the private one
    t70 = "test/issues/test_issue_70_retire.py"
    _sub(root, t70,
         '                                 f"example-site-platform={self.make_flat(self.tmp / \'c\')}"],\n',
         '                                 f"acsi-agentskills-private={make_registry(self.tmp / \'c\', skills=((PLUGIN, SKILL),))}"],\n')
    _sub(root, t70,
         'registries=[f"example-site-platform={self.make_flat(self.tmp / \'e\')}"])',
         'registries=[f"acsi-agentskills-private={self.make_flat(self.tmp / \'e\')}"])')
