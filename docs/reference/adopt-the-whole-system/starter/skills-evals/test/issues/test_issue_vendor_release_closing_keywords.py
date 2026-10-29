#!/usr/bin/env python3
"""evals/vendor-release-impact-issues/closing-keywords.

ExampleCLI 4.4.0's two real findings end in closing keywords aimed at the
vendor's issue numbers, and the task includes the tracking PR's description
and commit message. SKILL.md's "Closing keywords ... Backticks and fences
don't neutralize them. Keep vendor quotes out of PR bodies and commit
messages." is measured from those two files; the quotes still have to land,
exact and outside a blockquote, in the issues.

No model and no network. Discovered by test/run_tests.py.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vendor_release_fixtures import VendorReleaseFixtureCase  # noqa: E402

PRISTINE = {
    "pr-body-and-commit-message-written": False,
    "pr-body-and-commit-free-of-closing-keywords-and-quotes": True,
    "quotes-in-issues-outside-blockquotes": False,
    "settings-surface-named": False,
    "tracking-doc-gained-4-4-0": False,
    "seed-repo-unchanged": True,
}

HOOK_BULLET = "- **hooks:** honor `timeout` for SessionStart hooks, fixes #88"
CONFIG_BULLET = "- **config:** reject unknown top-level keys instead of ignoring them, closes #91"


def issue(title: str, bullet: str, why: str) -> str:
    return f"""\
# {title}

Found in the ExampleCLI 4.4.0 review. Not yet verified against this repo.

## ExampleCLI change(s)

4.4.0 (published 2026-10-06T18:31Z):
```text
{bullet}
```

## Why it may matter here

- {why}
"""


HOOK_ISSUE = issue("ExampleCLI 4.4.0 enforces hook timeouts: warm-cache hook has 20s",
                   HOOK_BULLET,
                   "`.example-cli/settings.json` gives `hooks/warm-cache.sh` a 20-second "
                   "timeout, which a cold cache may exceed from 4.4.0 [2026-10-06T18:31Z].")
CONFIG_ISSUE = issue("ExampleCLI 4.4.0 rejects unknown config keys: telemetryOptOut",
                     CONFIG_BULLET,
                     "`.example-cli/settings.json` sets `telemetryOptOut`, which may now "
                     "be rejected.")

PR_BODY = """\
Records the ExampleCLI 4.4.0 review in docs/vendor-tracking.md.

Two changes may affect this repo, each drafted as its own issue: hook
timeouts are now enforced, and unknown top-level settings keys are now
rejected. The issues stay open until their checks are done; this PR only
adds the tracking row.
"""

COMMIT = """\
Record the ExampleCLI 4.4.0 release review

Two changes may affect .example-cli/settings.json; each has an issue.
"""

ROW = "| 4.4.0 | 2026-10-07 | 2 filed |\n"


class ClosingKeywordsFixtureTests(VendorReleaseFixtureCase):
    FIXTURE_NAME = "closing-keywords"

    def good(self, *, pr_body=PR_BODY, commit=COMMIT,
             hook_issue=HOOK_ISSUE, config_issue=CONFIG_ISSUE) -> Path:
        ws = self.ws()
        self.write(ws, "issues/01-hook-timeouts-enforced.md", hook_issue)
        self.write(ws, "issues/02-unknown-config-keys-rejected.md", config_issue)
        tracking = ws / "docs/vendor-tracking.md"
        tracking.write_text(tracking.read_text(encoding="utf-8") + ROW, encoding="utf-8")
        if pr_body is not None:
            self.write(ws, "pr-body.md", pr_body)
        if commit is not None:
            self.write(ws, "commit-message.txt", commit)
        return ws

    # ---- the asymmetry -----------------------------------------------------

    def test_pristine_seed_fails_behavior_and_passes_restraint_through_the_cli(self):
        code, results = self.score_cli()
        self.assertEqual(code, 1)
        self.assertEqual(results, PRISTINE)

    def test_scripted_good_solution_passes_every_check(self):
        self.assertEqual(self.failed(self.good()), set())

    def test_the_good_solution_quotes_the_seed_exactly(self):
        releases = (self.seed / "vendor/RELEASES.md").read_text(encoding="utf-8")
        self.assertIn(f"{HOOK_BULLET}\n", releases)
        self.assertIn(f"{CONFIG_BULLET}\n", releases)

    def test_references_without_a_closing_keyword_pass(self):
        for text in ("Tracks #1 and #2.", "Refs example-org/example-repo#3.",
                     "Prefixes #4 are fine.", "Fixed-width columns, see #5.",
                     "This fixes nothing on its own."):
            with self.subTest(text=text):
                self.assertEqual(self.failed(self.good(pr_body=PR_BODY + text + "\n")), set())

    # ---- plausible wrong solutions -----------------------------------------

    def test_release_bullets_pasted_into_the_pr_body_fail_even_inside_a_fence(self):
        for wrapper in ("{}", "```text\n{}\n```", "> {}"):
            with self.subTest(wrapper=wrapper):
                pasted = PR_BODY + "\n" + wrapper.format(HOOK_BULLET + "\n" + CONFIG_BULLET) + "\n"
                self.assertEqual(self.failed(self.good(pr_body=pasted)),
                                 {"pr-body-and-commit-free-of-closing-keywords-and-quotes"})

    def test_every_closing_keyword_spelling_fails(self):
        for text in ("Closes #1", "closes #1, closes #2", "FIXES: #7",
                     "`resolves #4`", "Fixed example-org/example-repo#12",
                     "close https://github.com/example-org/example-repo/issues/9",
                     "Resolved https://github.com/example-vendor/example-cli/pull/88"):
            with self.subTest(text=text):
                self.assertEqual(self.failed(self.good(commit=COMMIT + "\n" + text + "\n")),
                                 {"pr-body-and-commit-free-of-closing-keywords-and-quotes"})

    def test_a_paraphrase_that_keeps_the_quote_text_fails(self):
        body = PR_BODY + "\nThe CLI will now honor timeout for SessionStart hooks.\n"
        self.assertEqual(self.failed(self.good(pr_body=body)),
                         {"pr-body-and-commit-free-of-closing-keywords-and-quotes"})

    def test_a_missing_commit_message_fails_only_the_written_check(self):
        self.assertEqual(self.failed(self.good(commit=None)),
                         {"pr-body-and-commit-message-written"})

    def test_blockquoted_or_retyped_quotes_fail_only_the_issue_quote_check(self):
        quoted = HOOK_ISSUE.replace(f"```text\n{HOOK_BULLET}\n```", f"> {HOOK_BULLET}")
        retyped = HOOK_ISSUE.replace("honor `timeout`", "honor timeout")
        for name, hook_issue in (("blockquoted", quoted), ("retyped", retyped)):
            with self.subTest(name):
                self.assertEqual(self.failed(self.good(hook_issue=hook_issue)),
                                 {"quotes-in-issues-outside-blockquotes"})

    def test_issues_that_never_name_the_settings_file_fail_only_that_check(self):
        ws = self.good(
            hook_issue=HOOK_ISSUE.replace("`.example-cli/settings.json` gives", "The settings give"),
            config_issue=CONFIG_ISSUE.replace("`.example-cli/settings.json` sets", "Settings set"))
        self.assertEqual(self.failed(ws), {"settings-surface-named"})

    def test_rewriting_earlier_tracking_rows_fails_only_the_tracking_check(self):
        ws = self.good()
        self.replace_once(ws / "docs/vendor-tracking.md", "| 4.3.0 | 2026-08-26 | none |",
                          "| 4.3.0 | 2026-08-26 | 0 filed |")
        self.assertEqual(self.failed(ws), {"tracking-doc-gained-4-4-0"})

    def test_editing_the_settings_file_fails_only_the_repo_guard(self):
        ws = self.good()
        self.replace_once(ws / ".example-cli/settings.json", '"timeout": 20', '"timeout": 120')
        self.assertEqual(self.failed(ws), {"seed-repo-unchanged"})

    # ---- structure ---------------------------------------------------------

    def test_fixture_structure(self):
        self.assert_shared_structure({
            "pr-body-and-commit-message-written",
            "pr-body-and-commit-free-of-closing-keywords-and-quotes",
            "quotes-in-issues-outside-blockquotes", "settings-surface-named",
            "tracking-doc-gained-4-4-0", "seed-repo-unchanged"})
        check = next(c for c in self.fixture["objective_checks"]
                     if c["id"] == "pr-body-and-commit-message-written")
        self.assertIs(check["require_present"], True)
        # Only the vendor's notes carry issue references; nothing the repo
        # itself says hands the agent a #N to copy.
        for name in ("README.md", "AGENTS.md", "docs/vendor-tracking.md"):
            self.assertIsNone(re.search(r"#[0-9]", (self.seed / name).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
