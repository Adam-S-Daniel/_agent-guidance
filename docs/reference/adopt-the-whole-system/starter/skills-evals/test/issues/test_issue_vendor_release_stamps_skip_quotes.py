#!/usr/bin/env python3
"""evals/vendor-release-impact-issues/stamps-skip-quotes.

Both real findings' release bullets name a version themselves, and the
prompt asks that every version the issues mention carry its publish time.
SKILL.md's "Never stamp or change text you did not write" (quotes, quoted
file text, titles) and "Stamped: ... versions named in your prose" are
measured from the drafted issue files alone.

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
    "quotes-kept-verbatim": False,
    "quoted-text-unstamped": True,
    "titles-unstamped": True,
    "prose-versions-stamped": False,
    "seed-repo-unchanged": True,
}

EXIT_QUOTE = ("`example-cli check` now exits 2 instead of 1 on configuration errors; "
              "scripts written against 4.2.0 or earlier that test for exit 1 need updating.")
FORK_QUOTE = ('Session-start hooks now report source "fork" for forked sessions; '
              "matchers written for v4.1.0 that list only startup|resume no longer fire for them.")
README_QUOTE = "Requires ExampleCLI 4.1.0 or later."

EXIT_ISSUE = f"""\
# ExampleCLI 4.3.0 check exits 2 on configuration errors: scripts/check.sh tests for 1

Found in the ExampleCLI 4.1.0 to 4.3.0 review. Not yet verified against this repo.

## ExampleCLI change(s)

> {EXIT_QUOTE}
>
> — [v4.3.0](https://github.com/example-vendor/example-cli/releases/tag/v4.3.0), published 2026-08-25T02:16Z

## Why it may matter here

- `scripts/check.sh` reports a configuration error only when the exit code
  is 1; from 4.3.0 [2026-08-25T02:16Z] that case may exit 2 and go unexplained.
- `README.md` says:

  > {README_QUOTE}

## To check

- [ ] On 4.3.0, confirm the exit code for a broken settings file.
"""

FORK_ISSUE = f"""\
# ExampleCLI 4.2.0 hooks report source "fork": the startup|resume matcher misses it

Found in the ExampleCLI 4.1.0 to 4.3.0 review. Not yet verified against this repo.

## ExampleCLI change(s)

> {FORK_QUOTE}
>
> — [v4.2.0](https://github.com/example-vendor/example-cli/releases/tag/v4.2.0), published 2026-07-14T09:47Z

## Why it may matter here

- `.example-cli/settings.json` matches `startup|resume`, so from
  4.2.0 [2026-07-14T09:47Z] forked sessions may skip `hooks/session-start.sh`.
"""

STAMPS = {"4.1.0": "2026-06-02T14:03Z", "4.2.0": "2026-07-14T09:47Z",
          "4.3.0": "2026-08-25T02:16Z"}


def stamp_everything(text: str) -> str:
    """A search-and-replace stamper run over finished text: every bare
    version, wherever it sits, gains its time unless one already follows."""
    return re.sub(r"(?<![0-9.])(v?)(4\.[123]\.0)(?![0-9])(?! \[)",
                  lambda m: f"{m.group(1)}{m.group(2)} [{STAMPS[m.group(2)]}]", text)


class StampsSkipQuotesFixtureTests(VendorReleaseFixtureCase):
    FIXTURE_NAME = "stamps-skip-quotes"

    def drafts(self, exit_issue=EXIT_ISSUE, fork_issue=FORK_ISSUE) -> Path:
        ws = self.ws()
        self.write(ws, "issues/01-check-exit-code.md", exit_issue)
        self.write(ws, "issues/02-hook-source-fork.md", fork_issue)
        return ws

    # ---- the asymmetry -----------------------------------------------------

    def test_pristine_seed_fails_behavior_and_passes_restraint_through_the_cli(self):
        code, results = self.score_cli()
        self.assertEqual(code, 1)
        self.assertEqual(results, PRISTINE)

    def test_scripted_good_solution_passes_every_check(self):
        self.assertEqual(self.failed(self.drafts()), set())

    def test_the_good_solution_quotes_the_seed_exactly(self):
        releases = (self.seed / "vendor/RELEASES.md").read_text(encoding="utf-8")
        self.assertIn(f"- {EXIT_QUOTE}\n", releases)
        self.assertIn(f"- {FORK_QUOTE}\n", releases)
        self.assertIn(f"{README_QUOTE}\n", (self.seed / "README.md").read_text(encoding="utf-8"))

    def test_fenced_quotes_and_other_stamp_forms_pass(self):
        fenced = EXIT_ISSUE.replace(
            f"> {EXIT_QUOTE}\n>\n> — [v4.3.0](https://github.com/example-vendor/example-cli/"
            "releases/tag/v4.3.0), published 2026-08-25T02:16Z\n",
            f"4.3.0 (published 2026-08-25T02:16Z):\n```text\n{EXIT_QUOTE}\n```\n")
        paren = FORK_ISSUE.replace("4.2.0 [2026-07-14T09:47Z]", "4.2.0 (published 2026-07-14T09:47Z)")
        listed = FORK_ISSUE.replace(f"> {FORK_QUOTE}", f"> - {FORK_QUOTE}")
        for name, exit_issue, fork_issue in (("fenced", fenced, FORK_ISSUE),
                                             ("parenthesized", EXIT_ISSUE, paren),
                                             ("list-marked", EXIT_ISSUE, listed)):
            with self.subTest(name):
                self.assertEqual(self.failed(self.drafts(exit_issue, fork_issue)), set())

    # ---- plausible wrong solutions -----------------------------------------

    def test_stamping_every_version_over_finished_text_fails_three_checks(self):
        ws = self.drafts(stamp_everything(EXIT_ISSUE), stamp_everything(FORK_ISSUE))
        text = (ws / "issues/01-check-exit-code.md").read_text(encoding="utf-8")
        self.assertIn("written against 4.2.0 [2026-07-14T09:47Z] or earlier", text)
        self.assertEqual(self.failed(ws), {"quotes-kept-verbatim", "quoted-text-unstamped",
                                           "titles-unstamped"})

    def test_stamped_titles_alone_fail_only_the_title_check(self):
        exit_issue = EXIT_ISSUE.replace("# ExampleCLI 4.3.0 check", "# ExampleCLI 4.3.0 "
                                        "[2026-08-25T02:16Z] check", 1)
        self.assertEqual(self.failed(self.drafts(exit_issue)), {"titles-unstamped"})

    def test_a_stamp_inside_quoted_file_text_alone_fails_only_the_quote_check(self):
        exit_issue = EXIT_ISSUE.replace(README_QUOTE, stamp_everything(README_QUOTE))
        self.assertEqual(self.failed(self.drafts(exit_issue)), {"quoted-text-unstamped"})

    def test_a_stamp_inside_a_fenced_quote_fails_both_quote_checks(self):
        fenced = EXIT_ISSUE.replace(f"> {EXIT_QUOTE}", f"```text\n{stamp_everything(EXIT_QUOTE)}\n```")
        self.assertEqual(self.failed(self.drafts(fenced)),
                         {"quotes-kept-verbatim", "quoted-text-unstamped"})

    def test_no_stamps_at_all_fails_only_the_prose_check(self):
        exit_issue = EXIT_ISSUE.replace(" [2026-08-25T02:16Z]", "")
        fork_issue = FORK_ISSUE.replace(" [2026-07-14T09:47Z]", "")
        self.assertEqual(self.failed(self.drafts(exit_issue, fork_issue)),
                         {"prose-versions-stamped"})

    def test_a_stamp_with_the_wrong_time_does_not_satisfy_the_prose_check(self):
        fork_issue = FORK_ISSUE.replace("4.2.0 [2026-07-14T09:47Z]", "4.2.0 [2026-07-14T09:46Z]")
        self.assertEqual(self.failed(self.drafts(fork_issue=fork_issue)),
                         {"prose-versions-stamped"})

    def test_a_quote_retyped_or_rewrapped_before_its_version_fails_only_the_verbatim_check(self):
        retyped = FORK_ISSUE.replace("now report source", "now reports source")
        wrapped = FORK_ISSUE.replace("; matchers written", ";\n> matchers written")
        for name, fork_issue in (("retyped", retyped), ("wrapped", wrapped)):
            with self.subTest(name):
                self.assertEqual(self.failed(self.drafts(fork_issue=fork_issue)),
                                 {"quotes-kept-verbatim"})

    def test_a_quote_retyped_after_its_version_fails_both_quote_checks(self):
        # quoted-text-unstamped reads any edit after the version as one.
        retyped = FORK_ISSUE.replace("no longer fire for them.", "won't fire for them.")
        self.assertEqual(self.failed(self.drafts(fork_issue=retyped)),
                         {"quotes-kept-verbatim", "quoted-text-unstamped"})

    def test_an_edit_to_the_repo_fails_only_its_guard(self):
        ws = self.drafts()
        self.replace_once(ws / "scripts/check.sh", '-eq 1 ]', '-ge 1 ]')
        self.assertEqual(self.failed(ws), {"seed-repo-unchanged"})

    # ---- structure ---------------------------------------------------------

    def test_fixture_structure(self):
        self.assert_shared_structure({"quotes-kept-verbatim", "prose-versions-stamped",
                                      "quoted-text-unstamped", "titles-unstamped",
                                      "seed-repo-unchanged"})
        # Each finding's bullet sits on one line in the seed, so a verbatim
        # copy can stay on one line.
        releases = (self.seed / "vendor/RELEASES.md").read_text(encoding="utf-8")
        for quote in (EXIT_QUOTE, FORK_QUOTE):
            self.assertEqual(sum(line == f"- {quote}" for line in releases.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
