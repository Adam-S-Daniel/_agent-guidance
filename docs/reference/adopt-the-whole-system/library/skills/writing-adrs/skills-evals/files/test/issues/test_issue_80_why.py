#!/usr/bin/env python3
"""evals/writing-adrs/why-no-comment-trail: the fourth writing-adrs fixture.

The seed is a repo whose decision log has two accepted ADRs and whose
export script ends every CSV line with CRLF, with nothing anywhere saying
why. The prompt asks why, and relays the answer and two rejected
alternatives. The skill's answer is to record that as ADR 0003, add its
index row, and point the script at it, leaving the behavior alone.

What is pinned here, and how:

  * the pristine seed FAILS every behavior check and PASSES every restraint
    check, read from the harness's own command line;
  * one hand-written good result passes every check, and for each check a
    minimally wrong result fails exactly that check;
  * a result that does not do the task (the reason only in a code comment,
    the required words only in headings or inside an HTML comment, a seed
    ADR pasted under the new number) does not pass the check it aims at;
  * every must_match / must_not_match pattern is anchored at both ends in
    each top-level alternative and line-local, with the supersede fixture's
    own checker;
  * the seed carries no part of the answer, and the prompt is distinct
    from every sibling's, so scripts/propose_skill_edit.py's
    fixture-derived trigger set has a should-trigger query in both splits
    on every rotation.

Every score below is read from `harness/run_eval.py --arm objective-only`,
the same way the supersede test reads it. Discovered and run by
test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_80_why.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
sys.path.insert(0, str(TEST_DIR / "issues"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import test_issue_80_supersede as supersede_tests  # noqa: E402
import propose_skill_edit as pse  # noqa: E402

run_eval = supersede_tests.run_eval
objective = supersede_tests.objective
replace_once = supersede_tests.replace_once
anchoring_problems = supersede_tests.anchoring_problems

ADRS_DIR = REPO_ROOT / "evals" / "writing-adrs"
EVAL_DIR = ADRS_DIR / "why-no-comment-trail"
SEED = EVAL_DIR / "seed"
SIBLINGS = ("bootstrap", "existing-convention", "supersede")

DECISIONS = Path("docs") / "decisions"
ADR_0001 = DECISIONS / "0001-export-after-the-ledger-closes.md"
ADR_0003 = DECISIONS / "0003-write-the-export-with-crlf-line-endings.md"
INDEX = DECISIONS / "README.md"
SCRIPT = Path("scripts") / "export-ledger.sh"

EXPECTED_PRISTINE = {
    "new-adr-0003-is-a-house-format-entry": False,
    "adr-0003-records-the-reason": False,
    "adr-0003-records-the-rejected-alternatives": False,
    "index-gained-a-row-for-0003": False,
    "index-rows-for-0001-and-0002-kept": True,
    "decision-log-links-resolve": True,
    "script-comment-points-at-0003": False,
    "script-adr-pointers-resolve": True,
    "script-still-writes-crlf": True,
    "exactly-one-new-adr-file": False,
    "nothing-else-touched": True,
}

GOOD_ADR_0003 = """\
# 0003. Write the export with CRLF line endings

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

The bank's reconciliation importer reads our export each morning. It is
the bank's tool, we cannot change it, and it rejects any file whose lines
end in a bare LF.

## Decision

Write every line of the export, header included, with CRLF line endings.

## Consequences

The bank accepts every file as written, with no manual step. The file
looks odd to Unix tools, and anyone editing the script has to keep both
printf lines as they are.

## Alternatives considered

Exporting with LF and having finance convert each file before upload was
rejected: twice someone forgot, and the month-end import failed.

Doing the same in our upload job was rejected: that job also uploads other
exports that have to keep LF endings.
"""

SEED_ROW_0002 = ("| [0002](0002-write-amounts-as-integer-cents.md) | Write amounts "
                 "as integer cents, not decimal strings | Accepted |\n")
GOOD_ROW_0003 = ("| [0003](0003-write-the-export-with-crlf-line-endings.md) | "
                 "Write the export with CRLF line endings | Accepted |\n")

SEED_BLOCK_OPEN = "{\n  printf '%s\\r\\n' \"date,account,amount_cents,memo\"\n"
GOOD_POINTER = "# Line endings: docs/decisions/0003-write-the-export-with-crlf-line-endings.md\n"


class TestIssue80Why(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adr-why-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fixture = run_eval.load_fixture(EVAL_DIR)
        self.checks = {c["id"]: c for c in self.fixture["objective_checks"]}
        self._workspaces = 0

    # ---- helpers (the supersede test's, pointed at this fixture) -------------

    def _ws(self) -> Path:
        """A fresh copy of the pristine seed, under this test's own tmp."""
        self._workspaces += 1
        ws = self.tmp / f"ws-{self._workspaces}"
        shutil.copytree(SEED, ws)
        return ws

    def _score(self, ws: Path | None = None) -> tuple[int, dict[str, bool]]:
        """(exit code, {check id: passed}) from the harness's command line,
        in an environment built here (HOME and TMPDIR under this test's tmp)."""
        home, child_tmp = self.tmp / "home", self.tmp / "child-tmp"
        home.mkdir(exist_ok=True)
        child_tmp.mkdir(exist_ok=True)
        env = {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(child_tmp),
               "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        extra = ["--workspace", str(ws)] if ws is not None else []
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(EVAL_DIR),
               "--arm", "objective-only",
               "--results-dir", str(self.tmp / "results"), *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"run_eval.py printed no JSON (exit {proc.returncode}):\n"
                      f"{proc.stdout}\n{proc.stderr}")
        self.assertEqual(payload["arm"], "objective-only")
        results = {c["id"]: c["passed"] for c in payload["checks"]}
        self.assertEqual(len(results), len(payload["checks"]), "duplicate check id")
        self.assertEqual(proc.returncode, 0 if all(results.values()) else 1,
                         "the exit code must agree with the per-check results")
        return proc.returncode, results

    def _failed(self, ws: Path) -> set[str]:
        code, results = self._score(ws)
        failed = {check_id for check_id, passed in results.items() if not passed}
        self.assertEqual(code, 1 if failed else 0)
        return failed

    def _good(self) -> Path:
        ws = self._ws()
        (ws / ADR_0003).write_text(GOOD_ADR_0003, encoding="utf-8")
        replace_once(ws / INDEX, SEED_ROW_0002, SEED_ROW_0002 + GOOD_ROW_0003)
        replace_once(ws / SCRIPT, SEED_BLOCK_OPEN, GOOD_POINTER + SEED_BLOCK_OPEN)
        return ws

    def _assert_passes_everything(self, ws: Path) -> None:
        code, results = self._score(ws)
        self.assertEqual({k for k, v in results.items() if not v}, set())
        self.assertEqual(list(results), list(EXPECTED_PRISTINE))
        self.assertEqual(code, 0)

    # ---- the pristine seed: the asymmetry ------------------------------------

    def test_pristine_seed_fails_every_behavior_check_and_passes_every_restraint_check(self):
        code, results = self._score()
        self.assertEqual(code, 1)
        self.assertEqual(results, EXPECTED_PRISTINE)
        self.assertEqual(list(results), [c["id"] for c in self.fixture["objective_checks"]])

    def test_scoring_a_seed_copy_as_a_workspace_agrees_with_the_default_run(self):
        self.assertEqual(self._score(self._ws()), self._score())

    # ---- the good result, and one mutation per check -------------------------

    def test_hand_written_good_result_passes_every_check(self):
        self._assert_passes_everything(self._good())

    def _mutations(self) -> dict:
        """For each check id, an edit to the good result that breaks only it."""

        def no_date_line(ws):
            replace_once(ws / ADR_0003, "- **Date:** 2026-10-05\n", "")

        def never_names_the_importer(ws):
            replace_once(ws / ADR_0003, "The bank's reconciliation importer reads",
                         "The bank's reconciliation service reads")

        def never_names_the_upload_job(ws):
            replace_once(ws / ADR_0003, "Doing the same in our upload job",
                         "Doing the same in our nightly job")
            replace_once(ws / ADR_0003, "also uploads other\nexports",
                         "also sends other\nexports")
            replace_once(ws / ADR_0003, "each file before upload",
                         "each file before sending")

        def no_index_row_for_0003(ws):
            replace_once(ws / INDEX, GOOD_ROW_0003, "")

        def index_row_0001_changed(ws):
            replace_once(ws / INDEX, "| Export after the ledger closes, not at midnight |",
                         "| Export after the ledger closes |")

        def index_row_0003_names_a_missing_file(ws):
            replace_once(ws / INDEX, "(0003-write-the-export-with-crlf-line-endings.md)",
                         "(0003-crlf.md)")

        def script_pointer_missing(ws):
            replace_once(ws / SCRIPT, GOOD_POINTER, "# Line endings: see the decision log\n")

        def script_pointer_names_a_missing_file(ws):
            replace_once(ws / SCRIPT, GOOD_POINTER, GOOD_POINTER
                         + "# Also: docs/decisions/0004-bank-importer.md\n")

        def script_switched_to_lf(ws):
            replace_once(ws / SCRIPT, "      printf '%s\\r\\n' \"$line\"\n",
                         "      printf '%s\\n' \"$line\"\n")

        def second_new_adr(ws):
            (ws / DECISIONS / "0004-take-the-day-as-an-argument.md").write_text(
                GOOD_ADR_0003.replace("# 0003.", "# 0004."), encoding="utf-8")

        def changelog_edited(ws):
            replace_once(ws / "CHANGELOG.md", "## 1.4.0 - 2026-03-02\n",
                         "## 1.4.0 - 2026-03-02\n\n- Added ADR 0003.\n")

        return {
            "new-adr-0003-is-a-house-format-entry": no_date_line,
            "adr-0003-records-the-reason": never_names_the_importer,
            "adr-0003-records-the-rejected-alternatives": never_names_the_upload_job,
            "index-gained-a-row-for-0003": no_index_row_for_0003,
            "index-rows-for-0001-and-0002-kept": index_row_0001_changed,
            "decision-log-links-resolve": index_row_0003_names_a_missing_file,
            "script-comment-points-at-0003": script_pointer_missing,
            "script-adr-pointers-resolve": script_pointer_names_a_missing_file,
            "script-still-writes-crlf": script_switched_to_lf,
            "exactly-one-new-adr-file": second_new_adr,
            "nothing-else-touched": changelog_edited,
        }

    def test_every_check_has_a_mutation(self):
        self.assertEqual(sorted(self._mutations()), sorted(self.checks))

    def test_each_minimally_wrong_result_fails_exactly_its_own_check(self):
        for check_id, mutate in self._mutations().items():
            with self.subTest(check=check_id):
                ws = self._good()
                mutate(ws)
                self.assertEqual(self._failed(ws), {check_id})

    def test_each_required_word_of_the_new_adr_is_required(self):
        for label, old, new, check_id in (
                ("CRLF", "with CRLF line endings.\n\n## Consequences",
                 "with Windows line endings.\n\n## Consequences",
                 "adr-0003-records-the-reason"),
                ("convert", "having finance convert each file",
                 "having finance fix each file",
                 "adr-0003-records-the-rejected-alternatives")):
            with self.subTest(word=label):
                ws = self._good()
                replace_once(ws / ADR_0003, old, new)
                self.assertEqual(self._failed(ws), {check_id})

    def test_other_ways_of_naming_the_line_ending_pass(self):
        for spelling in ("`\\r\\n`", "carriage-return line feed", "Carriage return"):
            with self.subTest(spelling=spelling):
                ws = self._good()
                replace_once(ws / ADR_0003, "with CRLF line endings.\n\n## Consequences",
                             f"with {spelling} line endings.\n\n## Consequences")
                self._assert_passes_everything(ws)

    def test_the_new_adr_status_proposed_passes(self):
        ws = self._good()
        replace_once(ws / ADR_0003, "- **Status:** Accepted\n", "- **Status:** Proposed\n")
        self._assert_passes_everything(ws)

    # ---- results that do not do the task --------------------------------------

    def test_reason_only_in_a_code_comment_fails_every_adr_check(self):
        ws = self._ws()
        replace_once(ws / SCRIPT, SEED_BLOCK_OPEN,
                     "# CRLF: the bank's importer rejects bare LF; finance converting\n"
                     "# by hand and converting in the upload job were both ruled out.\n"
                     + SEED_BLOCK_OPEN)
        self.assertEqual(self._failed(ws), {
            "new-adr-0003-is-a-house-format-entry", "adr-0003-records-the-reason",
            "adr-0003-records-the-rejected-alternatives", "index-gained-a-row-for-0003",
            "script-comment-points-at-0003", "exactly-one-new-adr-file"})

    def test_required_words_only_in_headings_do_not_count(self):
        ws = self._good()
        (ws / ADR_0003).write_text(
            "# 0003. Write CRLF for the importer; no converter, no upload step\n\n"
            "- **Status:** Accepted\n- **Date:** 2026-10-05\n\n"
            "## Context\n\nIt matters.\n\n## Decision\n\nDo it.\n\n"
            "## Consequences\n\nFine.\n\n## Alternatives considered\n\nNone.\n",
            encoding="utf-8")
        self.assertEqual(self._failed(ws), {"adr-0003-records-the-reason",
                                            "adr-0003-records-the-rejected-alternatives"})

    def test_required_words_inside_an_html_comment_do_not_count(self):
        ws = self._good()
        replace_once(ws / ADR_0003, "## Context\n", "<!-- CRLF importer -->\n## Context\n")
        self.assertEqual(self._failed(ws), {
            "new-adr-0003-is-a-house-format-entry", "adr-0003-records-the-reason",
            "adr-0003-records-the-rejected-alternatives"})

    def test_a_seed_adr_pasted_under_the_new_number_is_refused(self):
        ws = self._good()
        text = (SEED / ADR_0001).read_text(encoding="utf-8").replace("# 0001.", "# 0003.")
        (ws / ADR_0003).write_text(text, encoding="utf-8")
        self.assertIn("new-adr-0003-is-a-house-format-entry", self._failed(ws))

    def test_an_empty_file_with_the_right_name_fails_every_adr_check(self):
        ws = self._good()
        (ws / ADR_0003).write_text("", encoding="utf-8")
        self.assertEqual(self._failed(ws), {
            "new-adr-0003-is-a-house-format-entry", "adr-0003-records-the-reason",
            "adr-0003-records-the-rejected-alternatives"})

    def test_every_check_with_a_must_not_match_requires_the_file(self):
        carrying = [c for c in self.checks.values() if c.get("must_not_match")]
        self.assertTrue(carrying)
        for check in carrying:
            with self.subTest(check=check["id"]):
                self.assertIs(check.get("require_present"), True)
                self.assertTrue(check.get("must_match"))

    # ---- the anchoring property, over the fixture as parsed YAML -------------

    def test_every_pattern_is_anchored_in_each_alternative_and_line_local(self):
        doc = yaml.safe_load((EVAL_DIR / "fixture.yaml").read_text(encoding="utf-8"))
        patterns = [(f"{c['id']}.{key}", p) for c in doc["objective_checks"]
                    for key in ("must_match", "must_not_match")
                    for p in c.get(key) or []]
        self.assertEqual({label.split(".")[0] for label, _ in patterns},
                         {c["id"] for c in self.checks.values()
                          if c["type"] == "file_matches"})
        for label, pattern in patterns:
            with self.subTest(pattern=label):
                self.assertEqual(anchoring_problems(pattern), [], pattern)
                self.assertNotIn("\n", pattern)

    # ---- the fixture file itself ----------------------------------------------

    def test_only_existing_check_types_and_keys_are_used(self):
        for check in self.checks.values():
            with self.subTest(check=check["id"]):
                self.assertIn(check["type"], objective.CHECKS)
                allowed = (objective._CHECK_META_KEYS
                           | objective._CHECK_ALLOWED_KEYS.get(check["type"], set()))
                self.assertEqual(set(check) - allowed, set())

    def test_models_and_arms_are_pinned_as_the_siblings_pin_them(self):
        for name in SIBLINGS:
            with self.subTest(sibling=name):
                other = run_eval.load_fixture(ADRS_DIR / name)
                for key in ("skill", "registry", "arms"):
                    self.assertEqual(self.fixture[key], other[key])
                # Every writing-adrs fixture follows the roster.
                self.assertNotIn("model", other)
                self.assertNotIn("model", other["judge"])
                self.assertNotIn("model", self.fixture)
                self.assertNotIn("model", self.fixture["judge"])

    def test_judge_weights_sum_to_one_and_name_the_rubrics_dimensions(self):
        weights = self.fixture["judge"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        named = re.findall(r"\((\d)\) ([a-z0-9-]+) —", self.fixture["judge_rubric"])
        self.assertEqual([name for _, name in named], list(weights))

    def test_the_prompt_is_a_why_question_distinct_from_every_sibling(self):
        prompt = " ".join(self.fixture["prompt"].split())
        self.assertTrue(prompt.startswith("Why does scripts/export-ledger.sh "))
        for name in SIBLINGS:
            other = " ".join(run_eval.load_fixture(ADRS_DIR / name)["prompt"].split())
            self.assertNotEqual(prompt, other)
            self.assertFalse(prompt.startswith("Record the "))

    # ---- the seed carries none of the answer -----------------------------------

    def test_the_seed_is_exactly_these_files(self):
        files = sorted(str(p.relative_to(SEED)) for p in SEED.rglob("*") if p.is_file())
        self.assertEqual(files, [
            "AGENTS.md", "CHANGELOG.md", "README.md",
            "docs/decisions/0001-export-after-the-ledger-closes.md",
            "docs/decisions/0002-write-amounts-as-integer-cents.md",
            "docs/decisions/README.md", "scripts/export-ledger.sh"])

    def test_the_seed_never_explains_the_line_endings(self):
        # The only trace of the choice is the two printf lines themselves.
        hits = []
        for path in sorted(p for p in SEED.rglob("*") if p.is_file()):
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.search(r"crlf|\\r|carriage|line ending|import|conver|upload|"
                             r"priya|finance|0003", line, re.IGNORECASE):
                    hits.append((str(path.relative_to(SEED)), line.strip()))
        self.assertEqual(hits, [
            ("scripts/export-ledger.sh", "printf '%s\\r\\n' \"date,account,amount_cents,memo\""),
            ("scripts/export-ledger.sh", "printf '%s\\r\\n' \"$line\"")])

    def test_the_seed_script_is_executable(self):
        self.assertTrue(os.access(SEED / SCRIPT, os.X_OK))


class TestWritingAdrsTriggerSplit(unittest.TestCase):
    """The 2026-10-05 watched improvement-loop trial had one distinct
    should-trigger query; with this fixture every rotation has a
    should-trigger query in both of skill-creator's splits."""

    SKILL = "writing-adrs"

    def test_every_rotation_has_a_positive_in_train_and_held_out(self):
        names = pse.skill_fixtures(self.SKILL)
        self.assertIn("why-no-comment-trail", names)
        fixtures = pse.load_fixtures(self.SKILL, names)
        for rotation in range(len(names)):
            with self.subTest(rotation=rotation):
                train, validation = pse.split_fixtures(names, rotation)
                items, source = pse.trigger_eval_set(
                    self.SKILL, fixtures, train, validation, None)
                self.assertEqual(source, "fixtures")
                counts = pse.trigger_split_counts(items)
                self.assertEqual(pse.trigger_set_problems(counts), [])
                self.assertGreaterEqual(counts["train"]["should_trigger"], 1)
                self.assertGreaterEqual(counts["held_out"]["should_trigger"], 1)


if __name__ == "__main__":
    unittest.main()
