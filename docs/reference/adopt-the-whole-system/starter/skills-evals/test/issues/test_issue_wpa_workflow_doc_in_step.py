#!/usr/bin/env python3
"""evals/workflow-path-audit/workflow-doc-in-step.

The seed's tests.yml and docs-site.yml run on every change, and AGENTS.md's
"CI workflows" table says `all paths` for both. The skill's step 5: bring the
table that exists back in step with the new filters, and create no second
doc. The shared tests in test/workflow_path_audit_case.py pin the pristine
verdicts, the good result, one mutation per check and the fixture's shape;
this module supplies the fixture-specific halves and the anchoring property
of the fixture's line patterns, reusing test_issue_80_supersede's
`anchoring_problems` rather than a second copy of it.

Discovered and run by test/run_tests.py; run on its own with
`python3 -m pytest test/issues/test_issue_wpa_workflow_doc_in_step.py -q`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_path_audit_case import FixtureCase, FixtureTests, replace_once  # noqa: E402
import test_issue_80_supersede as supersede_tests  # noqa: E402

WORKFLOWS = Path(".github") / "workflows"
TESTS = WORKFLOWS / "tests.yml"
DOCS_SITE = WORKFLOWS / "docs-site.yml"
AGENTS = Path("AGENTS.md")

SEED_TESTS_ON = """on:
  pull_request:
  push:
    branches: [main]
"""
TESTS_PATHS = """    paths:
      - "src/**"
      - "test/**"
      - "package*.json"
      - ".github/workflows/tests.yml"
"""
GOOD_TESTS_ON = ("on:\n  pull_request:\n" + TESTS_PATHS
                 + "  push:\n    branches: [main]\n" + TESTS_PATHS)
DOCS_PATHS = """    paths:
      - "docs/**"
      - "mkdocs.yml"
      - ".github/workflows/docs-site.yml"
"""
GOOD_DOCS_ON = ("on:\n  pull_request:\n" + DOCS_PATHS
                + "  push:\n    branches: [main]\n" + DOCS_PATHS)

SEED_TESTS_ROW = "| `tests.yml` | pull_request, push to main | all paths |\n"
SEED_DOCS_ROW = "| `docs-site.yml` | pull_request, push to main | all paths |\n"
GOOD_TESTS_ROW = ("| `tests.yml` | pull_request, push to main | `src/**`, `test/**`, "
                  "`package*.json`, `.github/workflows/tests.yml` |\n")
GOOD_DOCS_ROW = ("| `docs-site.yml` | pull_request, push to main | `docs/**`, "
                 "`mkdocs.yml`, `.github/workflows/docs-site.yml` |\n")


def _good_filters(ws: Path) -> None:
    replace_once(ws / TESTS, SEED_TESTS_ON, GOOD_TESTS_ON)
    replace_once(ws / DOCS_SITE, SEED_TESTS_ON, GOOD_DOCS_ON)


def _good_table(ws: Path) -> None:
    replace_once(ws / AGENTS, SEED_TESTS_ROW, GOOD_TESTS_ROW)
    replace_once(ws / AGENTS, SEED_DOCS_ROW, GOOD_DOCS_ROW)


def _add_to_both_blocks(path: Path, after: str, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(after) != 2:
        raise AssertionError(f"{path.name}: expected two of {after!r}")
    path.write_text(text.replace(after, after + line), encoding="utf-8")


class TestWorkflowDocInStep(FixtureTests, FixtureCase):
    FIXTURE = "workflow-doc-in-step"

    # Behavior checks fail on the seed; guards and restraint checks pass.
    EXPECTED_PRISTINE = {
        "docs-change-builds-only-the-docs": False,
        "source-change-runs-only-the-tests": False,
        "lockfile-change-runs-only-the-tests": False,
        "agents-md-change-runs-nothing": False,
        "tests-row-names-its-paths": False,
        "docs-site-row-names-its-paths": False,
        "agents-md-keeps-the-rest": True,
        "workflows-still-parse": True,
        "event-only-workflows-unfiltered": True,
        "no-second-workflow-doc": True,
    }

    def apply_good(self, ws: Path) -> None:
        _good_filters(ws)
        _good_table(ws)

    def mutations(self) -> dict:
        return {
            "docs-change-builds-only-the-docs":
                lambda ws: _add_to_both_blocks(ws / TESTS, '      - "test/**"\n',
                                               '      - "docs/**"\n'),
            "source-change-runs-only-the-tests":
                lambda ws: _add_to_both_blocks(ws / DOCS_SITE, '      - "docs/**"\n',
                                               '      - "src/**"\n'),
            "lockfile-change-runs-only-the-tests":
                lambda ws: (ws / TESTS).write_text(
                    (ws / TESTS).read_text(encoding="utf-8")
                    .replace('"package*.json"', '"package.json"'), encoding="utf-8"),
            # A single `*` stops at `/`, so this matches top-level Markdown
            # only and leaves the docs probe alone.
            "agents-md-change-runs-nothing":
                lambda ws: _add_to_both_blocks(ws / TESTS, '      - "test/**"\n',
                                               '      - "*.md"\n'),
            "tests-row-names-its-paths":
                lambda ws: replace_once(ws / AGENTS, GOOD_TESTS_ROW, SEED_TESTS_ROW),
            "docs-site-row-names-its-paths":
                lambda ws: replace_once(ws / AGENTS, GOOD_DOCS_ROW, SEED_DOCS_ROW),
            "agents-md-keeps-the-rest":
                lambda ws: replace_once(ws / AGENTS, "## Conventions\n", "## Style\n"),
            "workflows-still-parse":
                lambda ws: (ws / WORKFLOWS / "broken.yml").write_text(
                    "on: [push\n", encoding="utf-8"),
            "event-only-workflows-unfiltered":
                lambda ws: replace_once(ws / WORKFLOWS / "nightly-sweep.yml",
                                        "  workflow_dispatch:\n",
                                        "  workflow_dispatch:\n"
                                        "    paths: [\"scripts/**\"]\n"),
            "no-second-workflow-doc":
                lambda ws: (ws / "docs" / "workflows.md").write_text(
                    "# CI workflows\n\nSee AGENTS.md.\n", encoding="utf-8"),
        }

    def wrong_solutions(self) -> dict:
        def filters_only(ws):
            # Right filters; the table still says `all paths`.
            _good_filters(ws)

        def new_doc_instead(ws):
            # Right filters, recorded in a doc the repo never had.
            _good_filters(ws)
            (ws / "WORKFLOWS.md").write_text(
                "# Workflows\n\n- tests.yml: src/, test/, package*.json\n"
                "- docs-site.yml: docs/, mkdocs.yml\n", encoding="utf-8")

        def tests_ignore_only_the_docs(ws):
            # A negative list that excludes the docs and nothing else: an
            # AGENTS.md or README.md edit still runs the whole test suite.
            replace_once(ws / TESTS, SEED_TESTS_ON, SEED_TESTS_ON.replace(
                "  pull_request:\n",
                '  pull_request:\n    paths-ignore: ["docs/**", "mkdocs.yml"]\n')
                .replace("    branches: [main]\n",
                         '    branches: [main]\n    paths-ignore: ["docs/**", "mkdocs.yml"]\n'))
            replace_once(ws / DOCS_SITE, SEED_TESTS_ON, GOOD_DOCS_ON)
            _good_table(ws)

        return {
            "updates the filters only": (filters_only, {
                "tests-row-names-its-paths", "docs-site-row-names-its-paths"}),
            "writes a new doc instead": (new_doc_instead, {
                "tests-row-names-its-paths", "docs-site-row-names-its-paths",
                "no-second-workflow-doc"}),
            "ignores only the docs": (tests_ignore_only_the_docs, {
                "agents-md-change-runs-nothing"}),
        }

    # ---- the line patterns ---------------------------------------------------

    def _patterns(self) -> list[tuple[str, str]]:
        doc = yaml.safe_load((self.eval_dir / "fixture.yaml").read_text(encoding="utf-8"))
        return [(f"{check['id']}.{key}", pattern)
                for check in doc["objective_checks"]
                for key in ("must_match", "must_not_match")
                for pattern in check.get(key) or []]

    def test_every_pattern_is_anchored_in_each_alternative_and_line_local(self):
        patterns = self._patterns()
        self.assertEqual({label.split(".")[0] for label, _ in patterns},
                         {c["id"] for c in self.checks.values()
                          if c["type"] == "file_matches"})
        for label, pattern in patterns:
            with self.subTest(pattern=label):
                self.assertEqual(supersede_tests.anchoring_problems(pattern), [], pattern)
                self.assertNotIn("\n", pattern)

    def test_every_file_check_fails_closed_on_a_missing_agents_md(self):
        for check in self.checks.values():
            if check["type"] == "file_matches":
                with self.subTest(check=check["id"]):
                    self.assertIs(check.get("require_present"), True)
        ws = self.good()
        (ws / AGENTS).unlink()
        self.assertEqual(self.failed(ws), {"tests-row-names-its-paths",
                                           "docs-site-row-names-its-paths",
                                           "agents-md-keeps-the-rest"})

    def test_reasonable_spellings_of_the_rows_pass(self):
        for label, tests_row, docs_row in (
                ("bare paths, no backticks",
                 "| tests.yml | pull_request, push to main | src/, test/, package*.json |\n",
                 "| docs-site.yml | pull_request, push to main | docs/, mkdocs.yml |\n"),
                ("a full workflow path in the first cell, trailing space",
                 "| `.github/workflows/tests.yml` | pull_request, push to main "
                 "| `src/**`, `test/**`, `package-lock.json` | \n",
                 "| `.github/workflows/docs-site.yml` | pull_request, push to main "
                 "| `docs/**`, `mkdocs.yml` |\n")):
            with self.subTest(spelling=label):
                ws = self.ws()
                _good_filters(ws)
                replace_once(ws / AGENTS, SEED_TESTS_ROW, tests_row)
                replace_once(ws / AGENTS, SEED_DOCS_ROW, docs_row)
                self.assertEqual(self.failed(ws), set())

    def test_mkdocs_alone_does_not_pass_for_the_docs_directory(self):
        # `mkdocs.yml` contains "docs" but not the docs/ directory; the
        # lookbehind keeps `mkdocs/` or `site-docs/` from passing as docs/.
        ws = self.good()
        replace_once(ws / AGENTS, GOOD_DOCS_ROW,
                     "| `docs-site.yml` | pull_request, push to main | `mkdocs.yml`, "
                     "`site-docs/**` |\n")
        self.assertEqual(self.failed(ws), {"docs-site-row-names-its-paths"})


if __name__ == "__main__":
    unittest.main()
