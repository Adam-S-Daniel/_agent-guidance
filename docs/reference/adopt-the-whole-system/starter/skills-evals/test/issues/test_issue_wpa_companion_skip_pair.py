#!/usr/bin/env python3
"""evals/workflow-path-audit/companion-skip-pair.

The seed's required `unit-tests` check lives in a path-filtered tests.yml
beside tests-skip.yml, which reports the same check name under the inverse
`paths-ignore:`. The skill's answer: delete the companion and gate tests.yml
internally. The shared tests in test/workflow_path_audit_case.py pin the
pristine verdicts, the good result, one mutation per check and the fixture's
shape; this module supplies the fixture-specific halves.

Discovered and run by test/run_tests.py; run on its own with
`python3 -m pytest test/issues/test_issue_wpa_companion_skip_pair.py -q`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_path_audit_case import FixtureCase, FixtureTests, replace_once  # noqa: E402

WORKFLOWS = Path(".github") / "workflows"
TESTS = WORKFLOWS / "tests.yml"
COMPANION = WORKFLOWS / "tests-skip.yml"
DOCS_SITE = WORKFLOWS / "docs-site.yml"
RULESET = Path(".github") / "rulesets" / "main.json"

SEED_TESTS_ON = """on:
  pull_request:
    branches: [main]
    paths:
      - "src/**"
      - "test/**"
      - "package.json"
      - "package-lock.json"
      - ".github/workflows/tests.yml"
"""
UNFILTERED_ON = """on:
  pull_request:
    branches: [main]
"""

SEED_STEPS = """      - uses: actions/checkout@1af3b93b6815bc44a9784bd300feb67ff0d1eeb3
      - uses: actions/setup-node@2028fbc5c25fe9cf00d9f06a71cc4710d4507903
        with:
          node-version: 22
      - run: npm ci
      - run: npm test
"""
GATED_STEPS = """      - uses: actions/checkout@1af3b93b6815bc44a9784bd300feb67ff0d1eeb3
        with:
          fetch-depth: 0
      - name: Detect salient changes
        id: salient
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          set -euo pipefail
          changed=$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")
          if grep -qE '^(src/|test/|package(-lock)?\\.json$|\\.github/workflows/tests\\.yml$)' <<<"$changed"; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi
      - name: Skip, nothing salient changed
        if: steps.salient.outputs.run != 'true'
        run: echo "::notice::No salient paths changed; reporting success without running."
      - uses: actions/setup-node@2028fbc5c25fe9cf00d9f06a71cc4710d4507903
        if: steps.salient.outputs.run == 'true'
        with:
          node-version: 22
      - run: npm ci
        if: steps.salient.outputs.run == 'true'
      - run: npm test
        if: steps.salient.outputs.run == 'true'
"""

DOCS_PR_PATHS = """  pull_request:
    paths:
      - "docs/**"
"""


def _gate_tests(ws: Path) -> None:
    replace_once(ws / TESTS, SEED_TESTS_ON, UNFILTERED_ON)
    replace_once(ws / TESTS, SEED_STEPS, GATED_STEPS)


class TestCompanionSkipPair(FixtureTests, FixtureCase):
    FIXTURE = "companion-skip-pair"

    # Behavior checks fail on the seed; guards and restraint checks pass.
    EXPECTED_PRISTINE = {
        "required-check-gates-inside-one-workflow": False,
        "companion-skip-workflow-removed": False,
        "docs-change-still-reports-the-required-check": False,
        "source-change-skips-the-docs-site": True,
        "workflows-still-parse": True,
        "event-only-workflows-unfiltered": True,
        "ruleset-unchanged": True,
        "project-files-unchanged": True,
    }

    def apply_good(self, ws: Path) -> None:
        (ws / COMPANION).unlink()
        _gate_tests(ws)

    def mutations(self) -> dict:
        def companion_renamed_job_kept_file(ws):
            # Keeps the file but no longer reports the required name, so only
            # the removal check can see it.
            (ws / COMPANION).write_text(
                (self.seed / COMPANION).read_text(encoding="utf-8")
                .replace("  unit-tests:\n    name: unit-tests\n",
                         "  skipped:\n    name: skipped\n"),
                encoding="utf-8")

        def gate_dropped(ws):
            # No computed output: the work runs unconditionally. Routing is
            # unchanged, so only the early-skip check fails.
            replace_once(ws / TESTS, "        id: salient\n", "")

        def docs_site_filter_opened_up(ws):
            replace_once(ws / DOCS_SITE, DOCS_PR_PATHS, DOCS_PR_PATHS + '      - "src/**"\n')

        def docs_site_forgets_docs(ws):
            # The docs build no longer watches docs/, so the docs-only probe
            # fails on docs-site.yml while tests.yml still always fires.
            replace_once(ws / DOCS_SITE, DOCS_PR_PATHS,
                         DOCS_PR_PATHS.replace('      - "docs/**"\n', ""))
            replace_once(ws / DOCS_SITE, '      - "docs/**"\n', "")

        return {
            "required-check-gates-inside-one-workflow": gate_dropped,
            "companion-skip-workflow-removed": companion_renamed_job_kept_file,
            "docs-change-still-reports-the-required-check": docs_site_forgets_docs,
            "source-change-skips-the-docs-site": docs_site_filter_opened_up,
            "workflows-still-parse":
                lambda ws: (ws / WORKFLOWS / "broken.yml").write_text(
                    "on: [push\n", encoding="utf-8"),
            "event-only-workflows-unfiltered":
                lambda ws: replace_once(ws / WORKFLOWS / "nightly-sweep.yml",
                                        "  workflow_dispatch:\n",
                                        "  workflow_dispatch:\n"
                                        "    paths: [\"scripts/**\"]\n"),
            "ruleset-unchanged":
                lambda ws: replace_once(ws / RULESET,
                                        '"strict_required_status_checks_policy": true',
                                        '"strict_required_status_checks_policy": false'),
            "project-files-unchanged":
                lambda ws: replace_once(ws / "README.md", "runs the unit tests\n",
                                        "runs the unit tests (gated in CI)\n"),
        }

    def wrong_solutions(self) -> dict:
        def keep_the_pair_add_the_workflow_dispatch(ws):
            # Reads the pair as deliberate and only tidies around it.
            replace_once(ws / TESTS, SEED_TESTS_ON,
                         SEED_TESTS_ON + "  workflow_dispatch:\n")

        def delete_companion_keep_paths(ws):
            # Drops the companion but leaves the required check filtered: a
            # docs-only pull request now has no `unit-tests` at all.
            (ws / COMPANION).unlink()

        def unfilter_tests_keep_companion(ws):
            # tests.yml always runs and gates, but the companion still posts
            # a second `unit-tests` on every non-test pull request.
            _gate_tests(ws)

        def gate_by_dropping_the_requirement(ws):
            # Satisfies the trap by redefining it: no check is required.
            ruleset = json.loads((ws / RULESET).read_text(encoding="utf-8"))
            ruleset["rules"] = [r for r in ruleset["rules"]
                                if r["type"] != "required_status_checks"]
            (ws / RULESET).write_text(json.dumps(ruleset, indent=2) + "\n",
                                      encoding="utf-8")

        return {
            "keeps the pair": (keep_the_pair_add_the_workflow_dispatch, {
                "required-check-gates-inside-one-workflow",
                "companion-skip-workflow-removed",
                "docs-change-still-reports-the-required-check"}),
            "deletes the companion, keeps the filter": (delete_companion_keep_paths, {
                "required-check-gates-inside-one-workflow",
                "docs-change-still-reports-the-required-check"}),
            "gates tests.yml, keeps the companion": (unfilter_tests_keep_companion, {
                "required-check-gates-inside-one-workflow",
                "companion-skip-workflow-removed"}),
            "edits the ruleset": (gate_by_dropping_the_requirement, {
                # No check is required any more, so the early-skip check has
                # nothing to hold the filtered pair to; only the ruleset
                # guard and the routing replays still see the problem.
                "companion-skip-workflow-removed",
                "docs-change-still-reports-the-required-check",
                "ruleset-unchanged"}),
        }


if __name__ == "__main__":
    unittest.main()
