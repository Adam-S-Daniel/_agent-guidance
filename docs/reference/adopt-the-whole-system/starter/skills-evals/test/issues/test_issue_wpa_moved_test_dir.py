#!/usr/bin/env python3
"""evals/workflow-path-audit/moved-test-dir.

The seed's e2e.yml still filters on `e2e/**` after the specs moved to
tests/e2e/, omits the server script its Playwright config starts, unit.yml
omits its own workflow file, and lint.yml filters on `.github/**`. The shared
tests in test/workflow_path_audit_case.py pin the pristine verdicts, the good
result, one mutation per check and the fixture's shape; this module supplies
the fixture-specific halves.

Discovered and run by test/run_tests.py; run on its own with
`python3 -m pytest test/issues/test_issue_wpa_moved_test_dir.py -q`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_path_audit_case import FixtureCase, FixtureTests, replace_once  # noqa: E402

WORKFLOWS = Path(".github") / "workflows"
E2E = WORKFLOWS / "e2e.yml"
UNIT = WORKFLOWS / "unit.yml"
LINT = WORKFLOWS / "lint.yml"

SEED_E2E_PATHS = """      - "e2e/**"
      - "src/**"
      - "playwright.config.js"
      - "package*.json"
"""
GOOD_E2E_PATHS = """      - "tests/e2e/**"
      - "src/**"
      - "scripts/start-test-server.sh"
      - "playwright.config.js"
      - "package*.json"
      - ".github/workflows/e2e.yml"
"""
SEED_UNIT_PATHS = """      - "src/**"
      - "tests/unit/**"
      - "package*.json"
"""
GOOD_UNIT_PATHS = SEED_UNIT_PATHS + '      - ".github/workflows/unit.yml"\n'
SEED_LINT_GITHUB = '      - ".github/**"\n'
GOOD_LINT_SELF = '      - ".github/workflows/lint.yml"\n'


def _replace_all(path: Path, old: str, new: str, count: int) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise AssertionError(f"{path.name}: expected {count} of {old!r}, "
                             f"found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _move_specs_back(ws: Path) -> None:
    (ws / "e2e").mkdir()
    (ws / "tests" / "e2e" / "home.spec.js").rename(ws / "e2e" / "home.spec.js")


class TestMovedTestDir(FixtureTests, FixtureCase):
    FIXTURE = "moved-test-dir"

    # Behavior checks fail on the seed; guards and restraint checks pass.
    EXPECTED_PRISTINE = {
        "moved-spec-runs-e2e": False,
        "server-script-runs-e2e": False,
        "own-workflow-edit-runs-itself": False,
        "sibling-workflow-edit-runs-only-itself": False,
        "unit-test-change-skips-e2e": True,
        "prose-change-runs-nothing": True,
        "lockfile-change-runs-every-installer": True,
        "workflows-still-parse": True,
        "event-only-workflows-unfiltered": True,
        "project-files-unchanged": True,
    }

    def apply_good(self, ws: Path) -> None:
        replace_once(ws / E2E, SEED_E2E_PATHS, GOOD_E2E_PATHS)
        _replace_all(ws / UNIT, SEED_UNIT_PATHS, GOOD_UNIT_PATHS, 2)
        replace_once(ws / LINT, SEED_LINT_GITHUB, GOOD_LINT_SELF)

    def mutations(self) -> dict:
        def e2e_keeps_the_old_directory(ws):
            replace_once(ws / E2E, '      - "tests/e2e/**"\n', '      - "e2e/**"\n')

        def e2e_forgets_the_server_script(ws):
            replace_once(ws / E2E, '      - "scripts/start-test-server.sh"\n', "")

        def unit_forgets_its_own_file(ws):
            _replace_all(ws / UNIT, GOOD_UNIT_PATHS, SEED_UNIT_PATHS, 2)

        def lint_watches_every_workflow(ws):
            replace_once(ws / LINT, GOOD_LINT_SELF, SEED_LINT_GITHUB)

        def e2e_widened_to_all_tests(ws):
            replace_once(ws / E2E, '      - "tests/e2e/**"\n', '      - "tests/**"\n')

        def unit_watches_docs(ws):
            _replace_all(ws / UNIT, GOOD_UNIT_PATHS,
                         GOOD_UNIT_PATHS + '      - "docs/**"\n', 2)

        def e2e_names_only_the_manifest(ws):
            replace_once(ws / E2E, '      - "package*.json"\n', '      - "package.json"\n')

        return {
            "moved-spec-runs-e2e": e2e_keeps_the_old_directory,
            "server-script-runs-e2e": e2e_forgets_the_server_script,
            "own-workflow-edit-runs-itself": unit_forgets_its_own_file,
            "sibling-workflow-edit-runs-only-itself": lint_watches_every_workflow,
            "unit-test-change-skips-e2e": e2e_widened_to_all_tests,
            "prose-change-runs-nothing": unit_watches_docs,
            "lockfile-change-runs-every-installer": e2e_names_only_the_manifest,
            "workflows-still-parse":
                lambda ws: (ws / WORKFLOWS / "broken.yml").write_text(
                    "on: [push\n", encoding="utf-8"),
            "event-only-workflows-unfiltered":
                lambda ws: replace_once(ws / WORKFLOWS / "nightly-e2e.yml",
                                        "  workflow_dispatch:\n",
                                        "  workflow_dispatch:\n"
                                        "    paths: [\"tests/e2e/**\"]\n"),
            # The filters stay right; only the files moved.
            "project-files-unchanged": _move_specs_back,
        }

    def wrong_solutions(self) -> dict:
        def fix_only_the_stale_directory(ws):
            # The visible fix: point e2e.yml at tests/e2e/ and stop.
            replace_once(ws / E2E, '      - "e2e/**"\n', '      - "tests/e2e/**"\n')

        def move_the_specs_back(ws):
            # Makes the stale filter true again instead of fixing it.
            _move_specs_back(ws)

        def catch_all_github_and_tests(ws):
            # Broad globs that route every probe "somewhere": the sibling
            # workflow edits and unit-test changes now fire the wrong ones.
            replace_once(ws / E2E, SEED_E2E_PATHS, """      - "tests/**"
      - "src/**"
      - "scripts/**"
      - "playwright.config.js"
      - "package*.json"
      - ".github/**"
""")
            _replace_all(ws / UNIT, SEED_UNIT_PATHS,
                         SEED_UNIT_PATHS + '      - ".github/**"\n', 2)

        return {
            "fixes only the stale directory": (fix_only_the_stale_directory, {
                "server-script-runs-e2e",
                "own-workflow-edit-runs-itself",
                "sibling-workflow-edit-runs-only-itself"}),
            "moves the specs back": (move_the_specs_back, {
                "moved-spec-runs-e2e",
                "server-script-runs-e2e",
                "own-workflow-edit-runs-itself",
                "sibling-workflow-edit-runs-only-itself",
                "project-files-unchanged"}),
            "broad globs": (catch_all_github_and_tests, {
                "own-workflow-edit-runs-itself",
                "sibling-workflow-edit-runs-only-itself",
                "unit-test-change-skips-e2e"}),
        }

    def test_the_seed_config_is_what_makes_the_filter_stale(self):
        # The premise, pinned: the specs and testDir are under tests/e2e/,
        # nothing is under e2e/, and the config, not the workflow, names the
        # server script.
        config = (self.seed / "playwright.config.js").read_text(encoding="utf-8")
        self.assertIn('testDir: "./tests/e2e"', config)
        self.assertIn("bash scripts/start-test-server.sh", config)
        self.assertTrue((self.seed / "tests" / "e2e" / "home.spec.js").is_file())
        self.assertFalse((self.seed / "e2e").exists())
        self.assertNotIn("start-test-server", (self.seed / E2E).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
