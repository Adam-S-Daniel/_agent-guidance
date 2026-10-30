"""Offline first-slice scaffolds for guidance section GAPs (#98)."""

from __future__ import annotations

import contextlib
import hashlib
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
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "harness"))

import guidance  # noqa: E402
import run_eval  # noqa: E402
import scaffold_real_work as scaffold  # noqa: E402


class GuidanceScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="issue-98-guidance-"))
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.upstream = self.temp / "_agent-guidance"
        (self.upstream / "agents-md").mkdir(parents=True)
        (self.upstream / "docs").mkdir()
        self.source = self.upstream / "agents-md" / "base.md"
        self.source.write_text(
            "Intro is not a section.\n\n## Coverage gap\n"
            "(Real incident, 2026-08-22: a real request.)\n"
            "```markdown\n## fake boundary\n````\n"
            "Claim from the section.\n\n## Later section\nDo not copy this.\n",
            encoding="utf-8")
        self.manifest = self.upstream / guidance.MANIFEST_REL
        self.dest = self.temp / "output"
        self.rows = [
            {"id": "coverage-gap", "heading": "Coverage gap",
             "file": "agents-md/base.md", "status": "gap"},
            {"id": "already-covered", "heading": "Later section",
             "file": "agents-md/base.md", "status": "covered"},
            {"id": "skipped-section", "heading": "Later section",
             "file": "agents-md/base.md", "status": "skipped"},
        ]
        self.write_rows()
        (self.upstream / "docs" / "guidance-impact.md").write_text(
            "# Impact history\n\n"
            "## 2026-08-25 — coverage-gap — added guard\nmatching history\n\n"
            "## 2026-08-26 — coverage-gap-extra — similar id\nwrong row\n\n"
            "## 2026-08-27 — other-gap — another section\nother history\n",
            encoding="utf-8")

    def write_rows(self, rows=None):
        self.manifest.write_text(yaml.safe_dump(self.rows if rows is None else rows),
                                 encoding="utf-8")

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = scaffold.main(["guidance", *map(str, args)])
        return code, output.getvalue()

    def test_all_gap_rows_only_and_no_gap_is_success_without_writes(self):
        self.write_rows(self.rows[1:])
        code, out = self.run_cli("--guidance", self.upstream, "--dest", self.dest)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out), {"created": 0, "skipped": 0})
        self.assertFalse(self.dest.exists())

        self.write_rows()
        created, skipped = scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertEqual((created, skipped), (1, 0))
        self.assertTrue((self.dest / "coverage-gap").is_dir())
        self.assertFalse((self.dest / "already-covered").exists())
        self.assertFalse((self.dest / "skipped-section").exists())

    def test_single_selection_and_non_gap_or_unknown_refusal(self):
        created, skipped = scaffold.scaffold_guidance(
            self.upstream, "coverage-gap", self.dest)
        self.assertEqual((created, skipped), (1, 0))
        for section_id in ("already-covered", "missing-section"):
            with self.subTest(section_id=section_id):
                with self.assertRaises(scaffold.ScaffoldError):
                    scaffold.scaffold_guidance(self.upstream, section_id, self.temp / section_id)

    def test_exact_markdown_extent_excludes_intro_and_next_heading(self):
        scaffold.scaffold_guidance(self.upstream, "coverage-gap", self.dest)
        section = (self.dest / "coverage-gap" / "section.md").read_text(encoding="utf-8")
        self.assertTrue(section.startswith("## Coverage gap\n"))
        self.assertNotIn("Intro is not a section", section)
        self.assertIn("## fake boundary", section)  # inside a fence, not a real h2
        self.assertNotIn("## Later section", section)

    def test_impact_history_matches_exact_id_and_absent_history_is_explicit(self):
        scaffold.scaffold_guidance(self.upstream, "coverage-gap", self.dest)
        impact = (self.dest / "coverage-gap" / "guidance-impact.md").read_text()
        self.assertIn("matching history", impact)
        self.assertNotIn("wrong row", impact)
        self.assertNotIn("other history", impact)

        self.rows[0]["id"] = "no-impact-history"
        self.write_rows(self.rows[:1])
        (self.upstream / "agents-md" / "base.md").write_text(
            "## Coverage gap\nbody\n", encoding="utf-8")
        scaffold.scaffold_guidance(self.upstream, None, self.dest)
        no_history = self.dest / "no-impact-history" / "guidance-impact.md"
        self.assertEqual(no_history.read_text(), "No matching guidance-impact history.\n")

    def test_draft_fixture_todos_tracking_and_incident_dates(self):
        scaffold.scaffold_guidance(self.upstream, "coverage-gap", self.dest)
        target = self.dest / "coverage-gap"
        fixture = yaml.safe_load((target / "fixture.yaml").read_text())
        fixture_text = (target / "fixture.yaml").read_text()
        self.assertIn("TODO: choose the instrument, reconstruct the seed, and author nonempty objective checks",
                      fixture_text)
        self.assertEqual((fixture["subject"], fixture["section"], fixture["draft"]),
                         ("guidance", "coverage-gap", True))
        self.assertEqual(fixture["prompt"],
                         "TODO: reconstruct the operator request without naming the rule")
        self.assertEqual(fixture["unprompted_rationale"],
                         "TODO: explain why the prompt does not name the rule")
        self.assertEqual(fixture["arms"]["with_guidance"],
                         {"mode": "section", "objective_checks": []})
        self.assertEqual(fixture["arms"]["without_guidance"],
                         {"mode": "none", "objective_checks": []})
        self.assertIn("TODO", (target / "seed" / "README.md").read_text())
        tracking = json.loads((target / "tracking.json").read_text())
        self.assertEqual(tracking["schema_version"], 1)
        self.assertEqual(tracking["status"], "needs-authoring")
        self.assertEqual(tracking["title"], "Guidance eval: Coverage gap")
        self.assertEqual(tracking["branch"], "scaffold/guidance-coverage-gap")
        self.assertEqual(tracking["marker"],
                         "<!-- skills-evals:guidance-scaffold:coverage-gap -->")
        self.assertIsNone(tracking["parent_issue"])
        self.assertIsNone(tracking["source_issue"])
        self.assertEqual(tracking["source"], {
            "file": "agents-md/base.md", "heading": "Coverage gap",
            "manifest": "agents-md/eval-coverage.yml"})
        self.assertEqual(tracking["incident_dates"], ["2026-08-22"])
        self.assertEqual(tracking["fixture"], "evals/guidance/coverage-gap/")
        self.assertTrue(any("actual claims" in item
                            for item in tracking["reviewer_instructions"]))
        self.assertTrue(any("does not name the rule" in item
                            for item in tracking["reviewer_instructions"]))

    def test_existing_target_and_fixture_are_preserved(self):
        target = self.dest / "coverage-gap"
        target.mkdir(parents=True)
        fixture = target / "fixture.yaml"
        fixture.write_text("human-edited draft\n", encoding="utf-8")
        created, skipped = scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertEqual((created, skipped), (0, 1))
        self.assertEqual(fixture.read_text(), "human-edited draft\n")

    def test_rerun_preserves_edits_inside_a_generated_target(self):
        scaffold.scaffold_guidance(self.upstream, None, self.dest)
        fixture = self.dest / "coverage-gap" / "fixture.yaml"
        fixture.write_text("human edited fixture\n", encoding="utf-8")
        created, skipped = scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertEqual((created, skipped), (0, 1))
        self.assertEqual(fixture.read_text(), "human edited fixture\n")

    def test_upstream_tree_fingerprints_are_unchanged(self):
        def fingerprints():
            files = sorted(path for path in self.upstream.rglob("*") if path.is_file())
            return {path.relative_to(self.upstream): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in files}

        before = fingerprints()
        scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertEqual(before, fingerprints())

    def test_invalid_ids_duplicates_statuses_and_bad_types_are_refused(self):
        invalid_sets = [
            [{**self.rows[0], "id": "../escape"}],
            [self.rows[0], self.rows[0]],
            [{**self.rows[0], "status": "pending"}],
            [{**self.rows[0], "heading": ["not", "text"]}],
            [{**self.rows[0], "file": ["not", "a", "path"]}],
        ]
        for rows in invalid_sets:
            with self.subTest(rows=rows):
                self.write_rows(rows)
                with self.assertRaises(scaffold.ScaffoldError):
                    scaffold.scaffold_guidance(self.upstream, None, self.dest)
                self.assertFalse(self.dest.exists())
        self.write_rows([self.rows[0], {**self.rows[1], "status": ["covered"]}])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())

    def test_symlink_escapes_destination_overlap_and_root_file_are_refused(self):
        outside = self.temp / "outside.md"
        outside.write_text("secret marker\n", encoding="utf-8")
        self.write_rows([{**self.rows[0], "file": "../outside.md"}])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())

        self.write_rows()
        for destination in (self.upstream, self.upstream / "nested", self.temp):
            with self.subTest(destination=destination):
                with self.assertRaises(scaffold.ScaffoldError):
                    scaffold.scaffold_guidance(self.upstream, None, destination)
        root_file = self.temp / "not-a-directory"
        root_file.write_text("occupied", encoding="utf-8")
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, root_file)

        link = self.temp / "linked-output"
        link.symlink_to(self.temp / "elsewhere", target_is_directory=True)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, link / "child")
        loop = self.temp / "loop"
        loop.symlink_to(loop)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, loop / "child")
        target_link_root = self.temp / "target-link-root"
        target_link_root.mkdir()
        (target_link_root / "coverage-gap").symlink_to(
            self.temp / "elsewhere", target_is_directory=True)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, target_link_root)

    def test_existing_directory_destination_symlinks_are_refused(self):
        elsewhere = self.temp / "elsewhere"
        elsewhere.mkdir()
        marker = elsewhere / "marker.txt"
        marker.write_text("preserve this directory\n", encoding="utf-8")
        link = self.temp / "linked-output"
        link.symlink_to(elsewhere, target_is_directory=True)

        for destination in (link, link / "child"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(
                        scaffold.ScaffoldError,
                        "destination path contains a symlink"):
                    scaffold.scaffold_guidance(self.upstream, None, destination)

        self.assertEqual(sorted(path.name for path in elsewhere.iterdir()), ["marker.txt"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve this directory\n")

    def test_existing_directory_target_symlink_is_refused(self):
        elsewhere = self.temp / "elsewhere"
        elsewhere.mkdir()
        marker = elsewhere / "marker.txt"
        marker.write_text("preserve this target\n", encoding="utf-8")
        self.dest.mkdir()
        target_link = self.dest / "coverage-gap"
        target_link.symlink_to(elsewhere, target_is_directory=True)

        with self.assertRaisesRegex(
                scaffold.ScaffoldError, "a target path contains a symlink"):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)

        self.assertTrue(target_link.is_symlink())
        self.assertEqual(sorted(path.name for path in elsewhere.iterdir()), ["marker.txt"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve this target\n")
        self.assertFalse((elsewhere / "fixture.yaml").exists())

    def test_manifest_section_and_impact_symlink_escapes_are_refused(self):
        outside_manifest = self.temp / "outside.yml"
        outside_manifest.write_text(yaml.safe_dump([self.rows[0]]), encoding="utf-8")
        outside_section = self.temp / "outside-section.md"
        outside_section.write_text("## Coverage gap\nprivate sentinel content\n",
                                   encoding="utf-8")
        outside_impact = self.temp / "outside-impact.md"
        outside_impact.write_text(
            "## 2026-08-25 — coverage-gap — external history\nprivate sentinel content\n",
            encoding="utf-8")
        original_manifest = self.manifest.read_bytes()
        self.manifest.unlink()
        self.manifest.symlink_to(outside_manifest)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())
        self.manifest.unlink()
        self.manifest.write_bytes(original_manifest)

        section_link = self.upstream / "agents-md" / "linked.md"
        section_link.symlink_to(outside_section)
        self.write_rows([{**self.rows[0], "file": "agents-md/linked.md"}])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())
        section_link.unlink()

        impact_path = self.upstream / "docs" / "guidance-impact.md"
        original_impact = impact_path.read_bytes()
        impact_path.unlink()
        impact_path.symlink_to(outside_impact)
        self.write_rows()
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())
        impact_path.unlink()
        impact_path.write_bytes(original_impact)

    def test_missing_and_duplicate_markdown_headings_refuse_before_writes(self):
        self.write_rows([{**self.rows[0], "heading": "Absent heading"}])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())
        self.source.write_text("## Coverage gap\none\n## Coverage gap\ntwo\n",
                               encoding="utf-8")
        self.write_rows(self.rows[:1])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse(self.dest.exists())

    def test_later_bad_gap_preflight_prevents_partial_batch(self):
        self.write_rows([self.rows[0], {**self.rows[0], "id": "bad-later",
                                        "heading": "Missing heading"}])
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.scaffold_guidance(self.upstream, None, self.dest)
        self.assertFalse((self.dest / "coverage-gap").exists())

    def test_output_failure_is_sanitized_and_partial_target_is_removed(self):
        original_write_text = Path.write_text

        def fail_fixture(path, *args, **kwargs):
            if path.name == "fixture.yaml":
                raise OSError("upstream content must not appear")
            return original_write_text(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", fail_fixture):
            code, output = self.run_cli("--guidance", self.upstream, "--dest", self.dest)
        self.assertEqual(code, 2)
        self.assertNotIn("upstream content", output)
        self.assertFalse((self.dest / "coverage-gap").exists())

    def test_cli_is_offline_and_objective_only_refuses_empty_checks(self):
        with contextlib.ExitStack() as stack:
            for sink in ("run", "Popen", "call", "check_call", "check_output"):
                stack.enter_context(mock.patch.object(
                    scaffold.subprocess, sink,
                    side_effect=AssertionError("subprocess attempted")))
            code, output = self.run_cli("--guidance", self.upstream, "--dest", self.dest)
        self.assertEqual(code, 0, output)
        fixture_dir = self.dest / "coverage-gap"
        args = ["run_eval.py", str(fixture_dir), "--arm", "objective-only",
                "--results-dir", str(self.temp / "results")]
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(stdout):
            code = run_eval.main()
        self.assertEqual(code, 2)
        self.assertIn("declares no top-level `objective_checks:`", stdout.getvalue())
        with self.assertRaises(run_eval.guidance.GuidanceError):
            run_eval.check_draft({"draft": True}, "with_guidance", False,
                                 fixture_dir / "fixture.yaml")


if __name__ == "__main__":
    unittest.main()
