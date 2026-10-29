#!/usr/bin/env python3
"""Issue #71: the checked-in `--trigger-eval-set` files (writing-adrs,
review-bash-ci-reliability).

`evals/writing-adrs/trigger-eval-set.json` is a reviewed skill-creator query
set for `scripts/propose_skill_edit.py --trigger-eval-set`. The
fixture-derived default gives writing-adrs one should-trigger query in train
per rotation, and with a fixed `--holdout` most combinations have none; this
set does not depend on the split.

What is pinned here:

  * the file is skill-creator's exact shape, balanced (10 should-trigger,
    10 should-not-trigger), with distinct queries and none equal to a
    writing-adrs fixture prompt, so no rotation or holdout drops a query;
  * under every rotation, with and without each fixed `--holdout`, both of
    skill-creator's splits hold both classes, at least two of each in train;
  * `--dry-run` with the file plans the full set and reports no problems;
  * `load_trigger_eval_set` refuses a file that is not exactly that shape,
    including a string label that `bool()` would read as should-trigger.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_71_trigger_set.py`.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
sys.path.insert(0, str(TEST_DIR / "issues"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import propose_skill_edit as pse  # noqa: E402
import test_issue_71 as t71  # noqa: E402

SKILL = "writing-adrs"
SET_PATH = REPO_ROOT / "evals" / SKILL / "trigger-eval-set.json"


def normalized(text: str) -> str:
    return " ".join(text.split())


class CheckedInSetTests(unittest.TestCase):

    skill = SKILL

    def setUp(self):
        self.set_path = REPO_ROOT / "evals" / self.skill / "trigger-eval-set.json"
        self.items = pse.load_trigger_eval_set(self.set_path)
        self.names = pse.skill_fixtures(self.skill)
        self.fixtures = pse.load_fixtures(self.skill, self.names)

    def test_set_is_balanced_and_distinct(self):
        positives = [i for i in self.items if i["should_trigger"]]
        negatives = [i for i in self.items if not i["should_trigger"]]
        self.assertEqual((len(positives), len(negatives)), (10, 10))
        queries = [normalized(i["query"]) for i in self.items]
        self.assertEqual(len(queries), len(set(queries)))

    def test_no_query_is_a_fixture_prompt(self):
        prompts = {normalized(str(f.get("prompt", ""))) for f in self.fixtures.values()}
        for item in self.items:
            self.assertNotIn(normalized(item["query"]), prompts)

    def test_every_rotation_and_holdout_keeps_both_classes_in_both_splits(self):
        planned = 0
        for holdout in [None] + self.names:
            for rotation in range(len(self.names)):
                with self.subTest(holdout=holdout, rotation=rotation):
                    train, validation = pse.split_fixtures(self.names, rotation, holdout)
                    items, source = pse.trigger_eval_set(
                        self.skill, self.fixtures, train, validation, self.set_path, holdout)
                    self.assertEqual(source, "file:trigger-eval-set.json")
                    self.assertEqual(len(items), len(self.items))
                    counts = pse.trigger_split_counts(items)
                    self.assertEqual(pse.trigger_set_problems(counts), [])
                    self.assertGreaterEqual(counts["train"]["should_trigger"], 2)
                    self.assertGreaterEqual(counts["train"]["should_not_trigger"], 2)
                    planned += 1
        self.assertEqual(planned, (len(self.names) + 1) * len(self.names))


class ReviewBashSetTests(CheckedInSetTests):
    """review-bash-ci-reliability's four fixtures share one prompt, so the
    fixture-derived default has no should-trigger query at all; the same
    pins hold for its checked-in set."""

    skill = "review-bash-ci-reliability"


class DryRunWithSetTests(t71.PipelineCase):
    """The live fixture set (not test_issue_71's frozen three), planned with
    the checked-in file: no refusal, no warning, no call, no write."""

    def setUp(self):
        live = pse.EVALS_DIR
        super().setUp()
        patcher = t71.mock.patch.object(pse, "EVALS_DIR", live)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reviewed_set = SET_PATH

    def test_fixed_holdout_dry_run_plans_the_whole_set(self):
        for holdout in ("supersede", "why-no-comment-trail"):
            for rotation in range(3):
                with self.subTest(holdout=holdout, rotation=rotation):
                    rc, out, err = self.run_main(t71.NoCallRunner(), "--dry-run",
                                                 "--holdout", holdout,
                                                 "--rotation", str(rotation))
                    self.assertEqual(rc, 0, err)
                    planned = json.loads(out)["trigger_eval_set"]
                    self.assertEqual(planned["source"], "file:trigger-eval-set.json")
                    self.assertEqual(planned["size"], 20)
                    self.assertEqual(planned["problems"], [])
                    self.assertNotIn("warning", err)
        self.assertFalse(self.results.exists())

    def test_malformed_set_is_refused_through_main(self):
        cases = {"string label": json.dumps([{"query": "q", "should_trigger": "false"}]),
                 "truncated json": "[{"}
        for name, text in cases.items():
            with self.subTest(name):
                self.reviewed_set = self.tmp / "malformed.json"
                self.reviewed_set.write_text(text, encoding="utf-8")
                rc, _, err = self.run_main(t71.NoCallRunner(), "--dry-run")
                self.assertEqual(rc, pse.EXIT_REFUSED, err)
                self.assertIn("refused: --trigger-eval-set", err)
                self.assertFalse(self.results.exists())


class LoaderTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(
            prefix="test-issue-71-set-",
            dir=t71.local_eval_tests.TestLocalEval._temp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, text: str) -> Path:
        path = self.tmp / "set.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_well_formed_set_loads_unchanged(self):
        items = [{"query": "add an ADR for the queue", "should_trigger": True},
                 {"query": "fix the flaky login test", "should_trigger": False}]
        self.assertEqual(pse.load_trigger_eval_set(self.write(json.dumps(items))), items)

    def test_malformed_sets_are_refused(self):
        ok = {"query": "add an ADR for the queue", "should_trigger": True}
        cases = {
            "not json": "[{",
            "not a list": json.dumps({"query": "q", "should_trigger": True}),
            "item not an object": json.dumps([ok, "fix the flaky test"]),
            "string label": json.dumps([ok, {"query": "q", "should_trigger": "false"}]),
            "integer label": json.dumps([ok, {"query": "q", "should_trigger": 0}]),
            "missing label": json.dumps([ok, {"query": "q"}]),
            "extra key": json.dumps([ok, dict(ok, note="x")]),
            "empty query": json.dumps([ok, {"query": "  ", "should_trigger": False}]),
            "query not a string": json.dumps([ok, {"query": 3, "should_trigger": False}]),
        }
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(pse.Refusal) as caught:
                    pse.load_trigger_eval_set(self.write(text))
                self.assertIn("--trigger-eval-set set.json", str(caught.exception))

    def test_missing_file_is_refused_not_a_traceback(self):
        with self.assertRaises(pse.Refusal):
            pse.load_trigger_eval_set(self.tmp / "absent.json")


if __name__ == "__main__":
    unittest.main()
