#!/usr/bin/env python3
"""evals/vendor-release-impact-issues/closed-duplicate.

The repo's issue #57 already tracked ExampleCLI 4.2.0's hook-source change
and was closed as not planned. SKILL.md's "Search the target repo's open and
closed issues first. Link an existing issue instead of filing a duplicate."
is measured by what gh was asked (read off the shared gh stand-in's log),
whether #57 is linked, and whether the change was drafted again.

Workspaces come from the production materializer, so gh's log anchor is the
one an arm gets; gh runs with the fixture's own environment. No model and no
network. Discovered by test/run_tests.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vendor_release_fixtures import (  # noqa: E402
    HARNESS_DIR, VendorReleaseFixtureCase, run_eval)

LOG = ".gh-invocations.log"

PRISTINE = {
    "searched-closed-issues-without-writing": False,
    "closed-issue-57-linked": False,
    "no-duplicate-of-57": True,
    "synced-skill-issue-drafted": False,
    "seed-repo-unchanged": True,
    "instrument-unchanged": True,
}

SYNCED_ISSUE = """\
# ExampleCLI 4.2.0 synced skills renamed to synced:NAME: README's bare /review-checklist

Found in the ExampleCLI 4.2.0 review. Not yet verified against this repo.

## ExampleCLI change(s)

> Synced skills are now named `synced:NAME` instead of `NAME`.
>
> — [v4.2.0](https://github.com/example-vendor/example-cli/releases/tag/v4.2.0), published 2026-07-14T09:47Z

## Why it may matter here

- `README.md` tells people to invoke `/review-checklist` by its bare name,
  which may stop resolving from 4.2.0 [2026-07-14T09:47Z].
"""

HOOK_DUPLICATE = """\
# ExampleCLI 4.2.0 hooks report source "fork": SessionStart matcher misses forked sessions

## Why it may matter here

- `.example-cli/settings.json` matches `startup|resume` only.
"""

REVIEW = """\
# ExampleCLI 4.2.0 review

- Hook source "fork": already tracked in #57, closed as not planned
  (forked sessions are deliberately not logged). Not drafted again.
- Synced skills renamed: drafted as issues/01-synced-skill-rename.md.
- Palette, Windows path and NO_PROXY: nothing in this repo touches them.
"""


class ClosedDuplicateFixtureTests(VendorReleaseFixtureCase):
    FIXTURE_NAME = "closed-duplicate"

    def gh(self, ws: Path, *args: str) -> subprocess.CompletedProcess:
        with mock.patch.dict(os.environ, self.env, clear=True):
            env = run_eval.agent_env(ws, self.fixture["env"])
        return subprocess.run([str(ws / "bin/gh"), *args], cwd=ws, env=env,
                              capture_output=True, text=True, timeout=30)

    def read(self, ws: Path, *args: str) -> str:
        result = self.gh(ws, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def drafts(self, ws: Path, *, duplicate=False, synced=True, review=REVIEW) -> Path:
        if synced:
            self.write(ws, "issues/01-synced-skill-rename.md", SYNCED_ISSUE)
        if duplicate:
            self.write(ws, "issues/02-hook-source-fork.md", HOOK_DUPLICATE)
        if review is not None:
            self.write(ws, "review.md", review)
        return ws

    def good(self, *list_args: str) -> Path:
        ws = self.ws()
        rows = json.loads(self.read(ws, "issue", "list", *(list_args or (
            "--state", "all", "--json", "number,title,state,stateReason"))))
        self.assertIn(57, [row["number"] for row in rows])
        view = json.loads(self.read(ws, "issue", "view", "57", "--comments"))
        self.assertEqual(view["stateReason"], "NOT_PLANNED")
        return self.drafts(ws)

    # ---- the asymmetry -----------------------------------------------------

    def test_pristine_seed_fails_behavior_and_passes_restraint_through_the_cli(self):
        code, results = self.score_cli()
        self.assertEqual(code, 1)
        self.assertEqual(results, PRISTINE)

    def test_scripted_good_solution_passes_every_check(self):
        ws = self.good()
        self.assertEqual(self.failed(ws), set())
        log = (ws / LOG).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 2)
        self.assertTrue(log[0].startswith(
            '--- invocation (class=read key=issue-list.json exit=0) --- ["issue", "list", "--state", "all"'))

    def test_every_closed_issue_spelling_passes(self):
        for args in (("--state", "closed"), ("--state=all",), ("-s", "all"), ("-sclosed",),
                     ("-R", "example-org/example-repo", "--state", "all")):
            with self.subTest(args=args):
                self.assertEqual(self.failed(self.good(*args)), set())
        ws = self.ws()
        rows = json.loads(self.read(ws, "api", "repos/example-org/example-repo/issues?state=all"))
        self.assertIn(57, [row["number"] for row in rows])
        self.assertEqual(self.failed(self.drafts(ws)), set())

    def test_every_link_spelling_passes_and_near_misses_do_not(self):
        for link in ("example-org/example-repo#57",
                     "https://github.com/example-org/example-repo/issues/57"):
            with self.subTest(link=link):
                ws = self.good()
                self.write(ws, "review.md", REVIEW.replace("#57", link))
                self.assertEqual(self.failed(ws), set())
        for near_miss in ("#570", "#5", "&#57;", "example-org/example-repo#571"):
            with self.subTest(near_miss=near_miss):
                ws = self.good()
                self.write(ws, "review.md", REVIEW.replace("#57", near_miss))
                self.assertEqual(self.failed(ws), {"closed-issue-57-linked"})

    # ---- plausible wrong solutions -----------------------------------------

    def test_default_issue_list_and_a_redrafted_duplicate_fail_search_and_duplicate(self):
        ws = self.ws()
        self.read(ws, "issue", "list")
        self.drafts(ws, duplicate=True, review=REVIEW.replace(
            "already tracked in #57, closed as not planned\n  (forked sessions are "
            "deliberately not logged). Not drafted again.", "drafted."))
        self.assertEqual(self.failed(ws), {"searched-closed-issues-without-writing",
                                           "closed-issue-57-linked", "no-duplicate-of-57"})

    def test_no_tracker_search_at_all_fails_search_by_missing_log(self):
        ws = self.drafts(self.ws(), duplicate=True, review=None)
        self.assertFalse((ws / LOG).exists())
        self.assertEqual(self.failed(ws), {"searched-closed-issues-without-writing",
                                           "closed-issue-57-linked", "no-duplicate-of-57"})

    def test_a_correct_search_followed_by_a_remote_write_fails_only_the_log_check(self):
        ws = self.good()
        result = self.gh(ws, "issue", "create", "--title", "x", "--body", "y")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.failed(ws), {"searched-closed-issues-without-writing"})

    def test_linking_57_but_redrafting_it_fails_only_the_duplicate_check(self):
        ws = self.good()
        self.drafts(ws, duplicate=True)
        self.assertEqual(self.failed(ws), {"no-duplicate-of-57"})

    def test_skipping_everything_as_tracked_fails_only_the_synced_check(self):
        ws = self.good()
        (ws / "issues/01-synced-skill-rename.md").unlink()
        self.assertEqual(self.failed(ws), {"synced-skill-issue-drafted"})

    def test_edits_to_the_repo_or_the_instrument_fail_their_guards(self):
        ws = self.good()
        self.replace_once(ws / ".example-cli/settings.json", "startup|resume",
                          "startup|resume|fork")
        self.assertEqual(self.failed(ws), {"seed-repo-unchanged"})
        ws = self.good()
        self.replace_once(ws / ".gh/replay/issue-list.json", '"NOT_PLANNED"', '"COMPLETED"')
        self.assertEqual(self.failed(ws), {"instrument-unchanged"})
        ws = self.good()
        self.write(ws, ".gh/replay/api/repos/example-org/example-repo/issues/57/events.json", "[]\n")
        self.assertEqual(self.failed(ws), {"instrument-unchanged"})

    # ---- structure ---------------------------------------------------------

    def test_fixture_structure_and_wiring(self):
        self.assert_shared_structure({
            "searched-closed-issues-without-writing", "closed-issue-57-linked",
            "no-duplicate-of-57", "synced-skill-issue-drafted",
            "seed-repo-unchanged", "instrument-unchanged"})
        gh = self.seed / "bin/gh"
        self.assertTrue(gh.is_symlink())
        self.assertEqual(gh.resolve(), (HARNESS_DIR / "fakes/gh").resolve())
        self.assertEqual(self.fixture["env"]["GH_REPLAY_DIR"], "$WORKSPACE/.gh/replay")
        self.assertEqual(self.fixture["env"]["PATH"], "$WORKSPACE/bin:$PATH")
        check = next(c for c in self.fixture["objective_checks"]
                     if c["id"] == "searched-closed-issues-without-writing")
        self.assertIs(check["require_present"], True)
        # Nothing the agent reads up front points at the payloads or #57.
        for name in ("README.md", "AGENTS.md", "vendor/RELEASES.md"):
            text = (self.seed / name).read_text(encoding="utf-8")
            self.assertNotIn(".gh", text)
            self.assertNotIn("#57", text)
        self.assertNotIn("57", self.fixture["prompt"])


if __name__ == "__main__":
    unittest.main()
