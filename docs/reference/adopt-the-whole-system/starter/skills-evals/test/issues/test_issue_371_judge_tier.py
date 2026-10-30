#!/usr/bin/env python3
"""The judge is chosen per arm, one tier above it (#371 items 3 and 4).

For an arm model in tier T the judge is the newest roster ARM in the lowest
tier above T, and the roster's own `judge` when no arm sits above T. A judge
is never the model it grades and never in that model's tier: a roster that
cannot give one is a selection error and nothing runs. A fixture's
`judge.model` pin still wins, a `judge.mode: pairwise` fixture always gets the
roster's `judge`, and every summary an arm writes records the choice in
`judge_selection`.

The tier ladder is the committed `evals/roster-policy.yml`, read through
`harness/roster.py`; the rosters are canned. Hermetic: the CLI is
test/fake-claude, directly or through test_issue_66's scripted stand-in. No
test sleeps, reads the clock or touches the network.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
FAKE_CLAUDE = TEST_DIR / "fake-claude"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_issue_66 import (TS, _HarnessCase, _child_env,  # noqa: E402
                           _write_fixture)

sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import ingest_routine_results as ingest  # noqa: E402
import judge_policy  # noqa: E402
import roster as roster_module  # noqa: E402
import run_eval  # noqa: E402

SONNET_OLD, SONNET, OPUS_OLD, OPUS = (
    "claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-5", "claude-opus-5-5")
HAIKU, FABLE = "claude-haiku-4-5-20251001", "claude-fable-5-1"
SKIPPED = {"status": "skipped", "reason": "objective-decided"}
SELECTION_KEYS = {"model", "rule", "arm_tier", "judge_tier"}
#: The agent step that passes both of the fixture's checks; a bare one
#: passes one of two (test_issue_371_conditional.py).
GAIN, BARE = {"write": "marker.txt"}, {}


def roster_doc(arms: list[str], judge: str | None = FABLE) -> dict:
    doc = {"arms": [{"id": arm, "reason": "canned"} for arm in arms]}
    if judge is not None:
        doc["judge"] = {"id": judge, "reason": "canned",
                        "is_arm": judge in arms}
    return doc


#: The roster committed on 2026-10-10, as the brief quotes it.
TODAY = roster_doc([SONNET_OLD, SONNET, OPUS])
RUNGS = [["haiku"], ["sonnet"], ["opus"], ["fable", "mythos"]]


def fixture_doc(**judge) -> dict:
    doc = {"skill": "a-skill", "prompt": "x", "judge_rubric": "y"}
    if judge:
        doc["judge"] = judge
    return doc


class _RosterCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def roster_file(self, document: dict) -> Path:
        path = self.tmp / "canned-roster.yml"
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return path

    def select(self, document: dict, *, model: str | None = None,
               fixture: dict | None = None, no_judge: bool = False) -> tuple:
        """(agent, judge, error, judge_selection) for one canned roster."""
        args = argparse.Namespace(model=model, no_judge=no_judge,
                                  roster=self.roster_file(document))
        return run_eval.select_judged_models(fixture or fixture_doc(), args)


class TierAboveArmTests(_RosterCase):

    def test_a_sonnet_arm_gets_the_newest_opus_arm(self):
        for arm in (SONNET_OLD, SONNET):
            with self.subTest(arm=arm):
                agent, judge, error, selection = self.select(TODAY, model=arm)
                self.assertIsNone(error)
                self.assertEqual((agent, judge), (arm, OPUS))
                self.assertEqual(selection, {
                    "model": OPUS, "rule": "tier-above-arm",
                    "arm_tier": "sonnet", "judge_tier": "opus"})

    def test_the_default_arm_is_judged_by_the_tier_above_it(self):
        agent, judge, error, _ = self.select(TODAY)
        self.assertEqual((agent, judge, error), (SONNET_OLD, OPUS, None))

    def test_newest_is_decided_by_the_id_not_by_the_order_listed(self):
        for arms in ([SONNET, OPUS_OLD, OPUS], [SONNET, OPUS, OPUS_OLD],
                     [SONNET, "claude-opus-4-10", "claude-opus-4-9"]):
            with self.subTest(arms=arms):
                _, judge, error, _ = self.select(roster_doc(arms), model=SONNET)
                self.assertIsNone(error)
                self.assertEqual(judge, max(
                    arms[1:], key=roster_module._version_key))
        self.assertEqual(self.select(roster_doc(
            [SONNET, "claude-opus-4-9", "claude-opus-4-10"]),
            model=SONNET)[1], "claude-opus-4-10")

    def test_the_judge_comes_from_the_lowest_tier_above_not_the_top_one(self):
        _, judge, error, selection = self.select(
            roster_doc([HAIKU, SONNET_OLD, SONNET, OPUS]), model=HAIKU)
        self.assertIsNone(error)
        self.assertEqual(judge, SONNET)
        self.assertEqual((selection["arm_tier"], selection["judge_tier"]),
                         ("haiku", "sonnet"))

    def test_an_arm_the_roster_does_not_list_is_still_judged_by_its_tier(self):
        """`--model` picks the arm; the rule reads that model's own tier."""
        _, judge, error, _ = self.select(TODAY, model="claude-sonnet-4-6")
        self.assertEqual((judge, error), (OPUS, None))

    def test_a_roster_with_no_judge_still_serves_an_arm_with_one_above_it(self):
        _, judge, error, _ = self.select(roster_doc([SONNET, OPUS], None),
                                         model=SONNET)
        self.assertEqual((judge, error), (OPUS, None))


class RosterJudgeTests(_RosterCase):

    def test_an_opus_arm_gets_the_roster_judge(self):
        agent, judge, error, selection = self.select(TODAY, model=OPUS)
        self.assertIsNone(error)
        self.assertEqual((agent, judge), (OPUS, FABLE))
        self.assertEqual(selection, {
            "model": FABLE, "rule": "roster-judge",
            "arm_tier": "opus", "judge_tier": "fable/mythos"})

    def test_a_top_arm_whose_roster_judge_is_itself_fails_closed(self):
        agent, judge, error, selection = self.select(
            roster_doc([SONNET, OPUS], OPUS), model=OPUS)
        self.assertEqual((agent, judge, selection), (None, None, None))
        self.assertIn("judge", error)
        self.assertIn("canned-roster.yml", error)
        self.assertNotIn(str(self.tmp), error)
        # Its own refusal, not the tier one below it (review round 1).
        self.assertIn("is that same model", error)
        self.assertIn("must not grade its own run", error)
        self.assertNotIn("which is not above it", error)

    def test_a_roster_judge_in_the_arms_own_tier_fails_closed(self):
        for judge_id in (OPUS_OLD, "claude-opus-4-8"):
            with self.subTest(judge=judge_id):
                agent, judge, error, _ = self.select(
                    roster_doc([SONNET, OPUS], judge_id), model=OPUS)
                self.assertEqual((agent, judge), (None, None))
                self.assertIn("tier", error)

    def test_a_roster_judge_below_the_arm_fails_closed(self):
        _, judge, error, _ = self.select(roster_doc([OPUS], SONNET), model=OPUS)
        self.assertIsNone(judge)
        self.assertIn("tier", error)

    def test_a_top_arm_with_no_roster_judge_fails_closed(self):
        _, judge, error, _ = self.select(roster_doc([OPUS], None), model=OPUS)
        self.assertIsNone(judge)
        self.assertIn("judge", error)

    def test_a_model_the_ladder_cannot_place_fails_closed(self):
        cases = {"arm": (TODAY, "some-unranked-model"),
                 "roster judge": (roster_doc([OPUS], "some-unranked-model"),
                                  OPUS)}
        for label, (document, arm) in cases.items():
            with self.subTest(unranked=label):
                agent, judge, error, _ = self.select(document, model=arm)
                self.assertEqual((agent, judge), (None, None))
                self.assertIn("tier", error)

    def test_an_unreadable_ladder_fails_closed(self):
        with mock.patch.object(judge_policy, "TIER_POLICY",
                               self.tmp / "no-such-policy.yml"):
            agent, judge, error, _ = self.select(TODAY, model=SONNET)
        self.assertEqual((agent, judge), (None, None))
        self.assertIn("no-such-policy.yml", error)
        self.assertNotIn(str(self.tmp), error)

    def test_an_unreadable_ladder_refuses_a_pairwise_fixture(self):
        """The roster judge cannot be checked against the arm's tier, so it is
        not served with both tiers null."""
        with mock.patch.object(judge_policy, "TIER_POLICY",
                               self.tmp / "no-such-policy.yml"):
            agent, judge, error, selection = self.select(
                TODAY, model=SONNET, fixture=fixture_doc(mode="pairwise"))
        self.assertEqual((agent, judge, selection), (None, None, None))
        self.assertIn("no-such-policy.yml", error)
        self.assertNotIn(str(self.tmp), error)


class NewestInTierTests(_RosterCase):
    """Which arm is the newest in the tier above (review round 1, finding 1).

    Two ids are ordered only when their names differ in nothing but numbers,
    a `-YYYYMMDD` snapshot date read as the date of the id before it. Where
    the names differ in a word (peer families, or an older id shape beside a
    newer one) the newest is unknown and the run fails closed.
    """

    def judge_over(self, arm: str, *above: str) -> tuple:
        """(judge, error) for `arm` on a roster of it and `above`, in the
        order given and reversed: the answer must not depend on the order."""
        answers = []
        for arms in ([arm, *above], [arm, *reversed(above)]):
            _, judge, error, _ = self.select(roster_doc(arms), model=arm)
            answers.append((judge, error is not None))
        self.assertEqual(answers[0], answers[1], above)
        _, judge, error, _ = self.select(roster_doc([arm, *above]), model=arm)
        return judge, error

    def test_peer_families_on_one_rung_are_not_ordered_by_their_words(self):
        """`mythos` sorts after `fable` as a string, so it used to win
        whatever the numbers said."""
        for peers in (("claude-fable-6", "claude-mythos-5"),
                      ("claude-fable-5-1", "claude-mythos-1"),
                      ("claude-fable-5", "claude-mythos-6")):
            with self.subTest(peers=peers):
                judge, error = self.judge_over(OPUS, *peers)
                self.assertIsNone(judge)
                self.assertIn("cannot be ordered", error)
                self.assertIn("fable/mythos", error)
                for peer in peers:
                    self.assertIn(f"`{peer}`", error)
                self.assertIn("canned-roster.yml", error)
                self.assertIn("Pin `judge.model:`", error)

    def test_a_trailing_word_beside_the_plain_id_is_not_ordered(self):
        """`zip` stopped at the shorter key, so `-fast` read as a number."""
        for suffix in ("fast", "preview", "latest"):
            with self.subTest(suffix=suffix):
                judge, error = self.judge_over(SONNET, OPUS, f"{OPUS}-{suffix}")
                self.assertIsNone(judge)
                self.assertIn("cannot be ordered", error)
                self.assertIn(f"`{OPUS}-{suffix}`", error)

    def test_two_spellings_of_one_version_are_not_ordered(self):
        judge, error = self.judge_over(SONNET, "claude-opus-05", "claude-opus-5")
        self.assertIsNone(judge)
        self.assertIn("cannot be ordered", error)
        self.assertIn("`claude-opus-05`", error)
        self.assertIn("`claude-opus-5`", error)

    def test_a_further_number_still_orders(self):
        self.assertEqual(self.judge_over(SONNET, "claude-opus-5", OPUS),
                         (OPUS, None))

    def test_one_family_of_a_peer_rung_is_ordered_among_itself(self):
        judge, error = self.judge_over(OPUS, "claude-mythos-5", "claude-mythos-6")
        self.assertEqual((judge, error), ("claude-mythos-6", None))
        judge, error = self.judge_over(OPUS, "claude-mythos-5")
        self.assertEqual((judge, error), ("claude-mythos-5", None))

    def test_an_older_id_shape_does_not_outrank_a_newer_one(self):
        """`claude-3-opus-20240229` used to beat `claude-opus-5-5`: a number
        compared above the family word."""
        judge, error = self.judge_over(SONNET, "claude-3-opus-20240229", OPUS)
        self.assertIsNone(judge)
        self.assertIn("cannot be ordered", error)
        self.assertIn("`claude-3-opus-20240229`", error)
        judge, error = self.judge_over(
            HAIKU, "claude-3-sonnet-20240229", "claude-3-5-sonnet-20241022")
        self.assertIsNone(judge)
        self.assertIn("cannot be ordered", error)

    def test_the_older_id_shape_is_ordered_among_itself(self):
        judge, error = self.judge_over(
            HAIKU, "claude-3-5-sonnet-20241022", "claude-3-7-sonnet-20250219")
        self.assertEqual((judge, error), ("claude-3-7-sonnet-20250219", None))
        judge, error = self.judge_over(
            HAIKU, "claude-3-5-sonnet-20240620", "claude-3-5-sonnet-20241022")
        self.assertEqual((judge, error), ("claude-3-5-sonnet-20241022", None))

    def test_a_snapshot_date_is_not_a_version_number(self):
        """`claude-opus-4-20250514` used to beat `claude-opus-4-1`: its date
        compared as a minor version."""
        for older, newer in (
                ("claude-opus-4-20250514", "claude-opus-4-1"),
                ("claude-opus-4-20250514", "claude-opus-4-1-20250805"),
                ("claude-opus-4", "claude-opus-4-1-20250805"),
                ("claude-opus-4-9-20260101", "claude-opus-4-10"),
                ("claude-opus-4-5-20251101", "claude-opus-5")):
            with self.subTest(older=older, newer=newer):
                self.assertEqual(self.judge_over(SONNET, older, newer),
                                 (newer, None))

    def test_a_snapshot_beside_its_own_base_is_the_same_model(self):
        """Either names the model; the dated id is the one taken."""
        self.assertEqual(
            self.judge_over(SONNET, OPUS, f"{OPUS}-20260301"),
            (f"{OPUS}-20260301", None))
        self.assertEqual(
            self.judge_over(SONNET, f"{OPUS}-20260301", f"{OPUS}-20260915"),
            (f"{OPUS}-20260915", None))

    def test_a_longer_version_is_newer_than_its_prefix(self):
        self.assertEqual(self.judge_over(SONNET, OPUS_OLD, OPUS), (OPUS, None))
        self.assertEqual(self.judge_over(HAIKU, SONNET_OLD, SONNET),
                         (SONNET, None))

    def test_only_the_lowest_tier_above_has_to_be_ordered(self):
        """Unordered peers two tiers up do not stop a Sonnet arm."""
        judge, error = self.judge_over(
            SONNET, OPUS, "claude-fable-6", "claude-mythos-5")
        self.assertEqual((judge, error), (OPUS, None))

    def test_every_id_the_committed_roster_holds_is_ordered_in_its_tier(self):
        """Arms, judge, preflight and `catalogue_seen`: no two ids the
        committed roster puts in one tier are left unordered, unless they
        are peer families."""
        document = yaml.safe_load(
            run_eval.TRUSTED_ROSTER.read_text(encoding="utf-8"))
        rungs, problem = judge_policy.read_rungs()
        self.assertIsNone(problem)
        ids = [entry["id"] for entry in document["arms"]]
        ids += [document["judge"]["id"], document["preflight"]["id"]]
        ids += [entry["id"] for entry in document["catalogue_seen"]]
        by_family: dict = {}
        for model_id in ids:
            family = roster_module.family_of(model_id, rungs)
            self.assertIsNotNone(family, model_id)
            by_family.setdefault(family, set()).add(model_id)
        self.assertGreaterEqual(len(by_family), 3)
        for family, members in sorted(by_family.items()):
            with self.subTest(family=family):
                newest, unordered = judge_policy.newest_of(sorted(members))
                self.assertIsNone(unordered)
                self.assertIn(newest, members)

    def test_the_order_reuses_roster_py_and_parses_no_name_itself(self):
        with mock.patch.object(judge_policy.roster, "_version_key",
                               wraps=roster_module._version_key) as key:
            newest, unordered = judge_policy.newest_of([OPUS_OLD, OPUS])
        self.assertEqual((newest, unordered), (OPUS, None))
        self.assertTrue(key.called)
        self.assertIs(judge_policy.roster.SNAPSHOT_SUFFIX,
                      roster_module.SNAPSHOT_SUFFIX)

    def test_a_roster_arm_the_ladder_cannot_place_is_never_the_judge(self):
        """Review round 1, finding 3: an unplaced arm is not "above"."""
        for arms in ([SONNET, "claude-zzz-9"], ["claude-zzz-9", SONNET],
                     [SONNET, "some-unranked-model", "claude-zzz-9"]):
            with self.subTest(arms=arms):
                _, judge, error, selection = self.select(roster_doc(arms),
                                                         model=SONNET)
                self.assertIsNone(error)
                self.assertEqual(judge, FABLE)
                self.assertEqual(selection["rule"], "roster-judge")
        _, judge, error, _ = self.select(
            roster_doc([SONNET, "claude-zzz-9", OPUS]), model=SONNET)
        self.assertEqual((judge, error), (OPUS, None))
        _, judge, error, _ = self.select(
            roster_doc([SONNET, "claude-zzz-9"], None), model=SONNET)
        self.assertIsNone(judge)
        self.assertIn("no roster arm sits above", error)


class NeverItselfNeverItsTierTests(_RosterCase):

    ROSTERS = [
        TODAY,
        roster_doc([SONNET_OLD, SONNET, OPUS], OPUS),
        roster_doc([HAIKU, SONNET, OPUS_OLD, OPUS], FABLE),
        roster_doc([SONNET], SONNET),
        roster_doc([SONNET], OPUS),
        roster_doc([OPUS, FABLE], "claude-mythos-5"),
        roster_doc([FABLE], "claude-fable-5"),
        roster_doc([HAIKU], None),
    ]

    def test_no_canned_roster_yields_a_judge_equal_to_or_level_with_its_arm(self):
        chosen = 0
        for index, document in enumerate(self.ROSTERS):
            for arm in [entry["id"] for entry in document["arms"]]:
                with self.subTest(roster=index, arm=arm):
                    agent, judge, error, selection = self.select(document,
                                                                 model=arm)
                    if error is not None:
                        self.assertEqual((agent, judge, selection),
                                         (None, None, None))
                        continue
                    chosen += 1
                    self.assertNotEqual(judge, arm)
                    self.assertGreater(roster_module.rung_of(judge, RUNGS),
                                       roster_module.rung_of(arm, RUNGS))
                    self.assertEqual(selection["model"], judge)
        self.assertGreaterEqual(chosen, 8)

    def test_every_arm_of_the_committed_roster_gets_a_judge_above_it(self):
        """The roster the harness runs on, whatever it names this week."""
        document = yaml.safe_load(
            run_eval.TRUSTED_ROSTER.read_text(encoding="utf-8"))
        rungs, problem = judge_policy.read_rungs()
        self.assertIsNone(problem)
        arms = [entry["id"] for entry in document["arms"]]
        self.assertTrue(arms)
        for arm in arms:
            with self.subTest(arm=arm):
                args = argparse.Namespace(model=arm, no_judge=False, roster=None)
                with mock.patch.dict("os.environ", clear=False) as env:
                    env.pop("EVAL_ROSTER", None)
                    _, judge, error, selection = run_eval.select_judged_models(
                        fixture_doc(), args)
                self.assertIsNone(error)
                self.assertGreater(roster_module.rung_of(judge, rungs),
                                   roster_module.rung_of(arm, rungs))
                self.assertIn(selection["rule"],
                              ("tier-above-arm", "roster-judge"))


class PinAndPairwiseTests(_RosterCase):

    def test_a_fixture_pin_wins_over_the_rule(self):
        for arm in (SONNET, OPUS):
            with self.subTest(arm=arm):
                _, judge, error, selection = self.select(
                    TODAY, model=arm, fixture=fixture_doc(model="claude-opus-4-8"))
                self.assertIsNone(error)
                self.assertEqual(judge, "claude-opus-4-8")
                self.assertEqual(selection["rule"], "fixture-pin")
                self.assertEqual(selection["model"], "claude-opus-4-8")
                self.assertEqual(selection["judge_tier"], "opus")

    def test_a_pinned_judge_beside_an_arm_from_the_roster_is_still_a_pin(self):
        agent, judge, error, selection = self.select(
            TODAY, fixture=fixture_doc(model=FABLE))
        self.assertEqual((agent, judge, error), (SONNET_OLD, FABLE, None))
        self.assertEqual(selection, {
            "model": FABLE, "rule": "fixture-pin",
            "arm_tier": "sonnet", "judge_tier": "fable/mythos"})

    def test_a_pin_the_ladder_cannot_place_records_null_tiers(self):
        fixture = {**fixture_doc(model="fake-judge-model"),
                   "model": "fake-agent-model"}
        args = argparse.Namespace(model=None, no_judge=False,
                                  roster=self.tmp / "absent.yml")
        agent, judge, error, selection = run_eval.select_judged_models(
            fixture, args)
        self.assertEqual((agent, judge, error),
                         ("fake-agent-model", "fake-judge-model", None))
        self.assertEqual(selection, {
            "model": "fake-judge-model", "rule": "fixture-pin",
            "arm_tier": None, "judge_tier": None})

    def test_a_pairwise_fixture_gets_the_roster_judge_whatever_the_arm(self):
        for mode in ("pairwise", " Pairwise "):
            for arm in (SONNET_OLD, SONNET, OPUS):
                with self.subTest(mode=mode, arm=arm):
                    _, judge, error, selection = self.select(
                        TODAY, model=arm, fixture=fixture_doc(mode=mode))
                    self.assertIsNone(error)
                    self.assertEqual(judge, FABLE)
                    self.assertEqual(selection["rule"], "roster-judge")
                    self.assertEqual(selection["judge_tier"], "fable/mythos")

    def test_an_absolute_mode_is_not_pairwise(self):
        for mode in ("absolute", "Absolute", ""):
            with self.subTest(mode=mode):
                _, judge, _, _ = self.select(TODAY, model=SONNET,
                                             fixture=fixture_doc(mode=mode))
                self.assertEqual(judge, OPUS)

    def test_a_pairwise_fixture_still_refuses_a_roster_judge_that_is_an_arm(self):
        _, judge, error, _ = self.select(
            roster_doc([SONNET, OPUS], OPUS), model=SONNET,
            fixture=fixture_doc(mode="pairwise"))
        self.assertIsNone(judge)
        self.assertIn("also an arm", error)

    def test_no_judge_selects_none_and_reads_no_ladder(self):
        with mock.patch.object(judge_policy, "read_rungs",
                               side_effect=AssertionError("ladder read")):
            agent, judge, error, selection = self.select(
                TODAY, model=SONNET, no_judge=True)
        self.assertEqual((agent, judge, error, selection),
                         (SONNET, None, None, None))

    def test_a_pairwise_fixture_is_never_judged_by_its_own_arm(self):
        """Review round 1, finding 2: an arm from `--model` or the fixture's
        `model:` pin that is the roster's judge, though not a roster arm."""
        pairwise = fixture_doc(mode="pairwise")
        for label, kwargs in (
                ("--model", {"model": FABLE, "fixture": pairwise}),
                ("fixture pin", {"fixture": {**pairwise, "model": FABLE}})):
            with self.subTest(arm_from=label):
                agent, judge, error, selection = self.select(TODAY, **kwargs)
                self.assertEqual((agent, judge, selection), (None, None, None))
                self.assertIn("must not grade its own run", error)
                self.assertIn(f"`{FABLE}`", error)
                self.assertIn("canned-roster.yml", error)
                self.assertNotIn(str(self.tmp), error)

    def test_a_pairwise_fixture_is_never_judged_from_its_arms_own_tier(self):
        for arm in ("claude-fable-5", "claude-mythos-5"):
            with self.subTest(arm=arm):
                agent, judge, error, selection = self.select(
                    TODAY, model=arm, fixture=fixture_doc(mode="pairwise"))
                self.assertEqual((agent, judge, selection), (None, None, None))
                self.assertIn("fable/mythos tier", error)
                self.assertIn("canned-roster.yml", error)
                self.assertNotIn("must not grade its own run", error)

    def test_a_pairwise_arm_the_ladder_cannot_place_keeps_the_roster_judge(self):
        """Not the same model, and no tier to compare: served, tier null."""
        _, judge, error, selection = self.select(
            TODAY, model="some-unranked-model",
            fixture=fixture_doc(mode="pairwise"))
        self.assertEqual((judge, error), (FABLE, None))
        self.assertEqual(selection, {
            "model": FABLE, "rule": "roster-judge",
            "arm_tier": None, "judge_tier": "fable/mythos"})

    def test_no_judge_on_an_unpinned_fixture_selects_no_judge(self):
        """Review round 1, finding 3: the roster is read for the arm, and
        then nothing chooses, records or refuses a judge."""
        cases = {"today": (TODAY, None, SONNET_OLD),
                 "a top arm with nothing above it": (
                     roster_doc([FABLE], None), None, FABLE),
                 "a judge that is the arm": (
                     roster_doc([OPUS], OPUS), None, OPUS),
                 "pairwise on the judge itself": (TODAY, FABLE, FABLE)}
        for label, (document, model, arm) in cases.items():
            with self.subTest(case=label), mock.patch.object(
                    judge_policy, "read_rungs",
                    side_effect=AssertionError("ladder read")), \
                    mock.patch.object(
                        judge_policy, "judge_for_arm",
                        side_effect=AssertionError("judge chosen")):
                fixture = fixture_doc(mode="pairwise") if model else None
                self.assertEqual(
                    self.select(document, model=model, fixture=fixture,
                                no_judge=True),
                    (arm, None, None, None))

    def test_select_models_keeps_its_three_values(self):
        args = argparse.Namespace(model=OPUS, no_judge=False,
                                  roster=self.roster_file(TODAY))
        self.assertEqual(run_eval.select_models(fixture_doc(), args),
                         (OPUS, FABLE, None))


class PureHelperTests(unittest.TestCase):

    def test_the_helper_reads_tiers_through_roster_py(self):
        with mock.patch.object(judge_policy.roster, "rung_of",
                               wraps=roster_module.rung_of) as rung_of:
            selection, error = judge_policy.judge_for_arm(
                SONNET, [SONNET, OPUS], FABLE, RUNGS)
        self.assertIsNone(error)
        self.assertEqual(selection["model"], OPUS)
        self.assertTrue(rung_of.called)

    def test_peers_on_one_rung_are_one_tier(self):
        """`fable` and `mythos` rank identically: neither judges the other."""
        selection, error = judge_policy.judge_for_arm(
            FABLE, [FABLE], "claude-mythos-5", RUNGS)
        self.assertIsNone(selection)
        self.assertIn("tier", error)

    def test_the_committed_ladder_reads(self):
        rungs, problem = judge_policy.read_rungs()
        self.assertIsNone(problem)
        self.assertEqual(rungs, roster_module.tier_rungs(
            roster_module.load_policy(judge_policy.TIER_POLICY)))


# ---------------------------------------------------------------------------
# What a run records


def _unpin(fixture_dir: Path) -> Path:
    """`_write_fixture` pins both models; the rule needs neither pinned."""
    path = fixture_dir / "fixture.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    del doc["model"], doc["judge"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return fixture_dir


class RecordedSelectionTests(_HarnessCase):
    """`--arm both` runs of an unpinned fixture on a canned roster."""

    SKILL = "tier-skill"

    def setUp(self):
        super().setUp()
        self.fixture_dir = _unpin(_write_fixture(self.evals / self.SKILL,
                                                 self.SKILL))
        self.registry = self._registry(self.SKILL)
        self.roster = self.tmp / "canned-roster.yml"
        self.roster.write_text(yaml.safe_dump(TODAY), encoding="utf-8")

    def scripted(self, agents: list, judges: int, *flags: str):
        self._script(agents=agents, judges=[{"overall": 7.0}] * judges)
        return self._run(self.fixture_dir, "--arm", "both",
                         "--registry", f"acsi-agentskills={self.registry}",
                         "--roster", str(self.roster), "--timestamp", TS, *flags)

    def summaries(self) -> dict:
        return {p.relative_to(self.results).as_posix():
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(self.results.rglob("summary.json"))}

    def assert_ingestable(self, found: dict) -> None:
        for rel, doc in found.items():
            with self.subTest(summary=rel):
                ingest.check_summary(doc, rel, ingest.parse_result_path(rel))

    def test_a_judged_pair_is_judged_by_the_model_the_rule_chose(self):
        """A tie is judged. The model the judge CLI was started with, the
        model its result says served it, the judge's usage record and
        `judge_selection` all name the tier above the arm."""
        log = self.tmp / "argv.log"
        mode = "agent_and_judge"
        doc_path = self.fixture_dir / "fixture.yaml"
        doc = yaml.safe_load(doc_path.read_text(encoding="utf-8"))
        doc["env"] = {"FAKE_CLAUDE_MODE": mode, "FAKE_CLAUDE_ARGV_LOG": str(log)}
        doc_path.write_text(yaml.safe_dump(doc, sort_keys=False),
                            encoding="utf-8")
        for arm, judge, rule in ((SONNET, OPUS, "tier-above-arm"),
                                 (OPUS, FABLE, "roster-judge")):
            with self.subTest(arm=arm):
                results = self.tmp / f"results-{rule}"
                log.write_text("", encoding="utf-8")
                cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"),
                       str(self.fixture_dir), "--arm", "both", "--model", arm,
                       "--registry", f"acsi-agentskills={self.registry}",
                       "--roster", str(self.roster), "--results-dir",
                       str(results), "--timeout", "60", "--timestamp", TS]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=str(REPO_ROOT),
                    timeout=600, env=_child_env(
                        self.tmp, CLAUDE_BIN=str(FAKE_CLAUDE),
                        FAKE_CLAUDE_MODE=mode, FAKE_CLAUDE_FULL_USAGE="1",
                        FAKE_CLAUDE_ARGV_LOG=str(log)))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argvs = [json.loads(line)["argv"] for line in
                         log.read_text(encoding="utf-8").splitlines()]
                models = [argv[argv.index("--model") + 1] for argv in argvs
                          if "--version" not in argv]
                self.assertEqual(sorted(models), sorted([arm, arm, judge, judge]))
                for name in ("with_skill", "without_skill"):
                    summary = json.loads((results / self.SKILL / TS / name
                                          / "summary.json").read_text("utf-8"))
                    self.assertEqual(summary["judge_selection"]["model"], judge)
                    self.assertEqual(summary["judge_selection"]["rule"], rule)
                    self.assertEqual(set(summary["judge_selection"]),
                                     SELECTION_KEYS)
                    self.assertEqual(summary["judge_models_used"], [judge])
                    self.assertEqual(summary["models_used"], [arm])
                    measured = summary["usage"]["measured"]
                    self.assertEqual(list(measured["judge"]["models"]), [judge])
                    self.assertEqual(list(measured["agent"]["models"]), [arm])
                    rel = f"{self.SKILL}/{TS}/{name}/summary.json"
                    ingest.check_summary(summary, rel,
                                         ingest.parse_result_path(rel))

    def test_a_skipped_pair_still_names_the_judge_it_would_have_had(self):
        proc = self.scripted([GAIN, BARE], 0, "--model", SONNET)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._calls("judges"), [])
        found = self.summaries()
        self.assertEqual(len(found), 2)
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assertEqual(summary["judge"], SKIPPED)
                self.assertEqual(summary["judge_models_used"], [])
                self.assertEqual(summary["judge_selection"], {
                    "model": OPUS, "rule": "tier-above-arm",
                    "arm_tier": "sonnet", "judge_tier": "opus"})
        self.assert_ingestable(found)

    def test_every_trial_and_each_arm_aggregate_record_the_selection(self):
        proc = self.scripted([BARE] * 4, 4, "--model", OPUS, "--trials", "2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        found = self.summaries()
        self.assertEqual(len(found), 6)
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assertEqual(summary["judge_selection"], {
                    "model": FABLE, "rule": "roster-judge",
                    "arm_tier": "opus", "judge_tier": "fable/mythos"})
        self.assert_ingestable(found)

    def test_no_judge_records_a_null_selection(self):
        proc = self.scripted([BARE, BARE], 0, "--model", SONNET, "--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        found = self.summaries()
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assertIn("judge_selection", summary)
                self.assertIsNone(summary["judge_selection"])
        self.assert_ingestable(found)

    def test_a_roster_that_gives_no_judge_runs_nothing(self):
        self.roster.write_text(yaml.safe_dump(roster_doc([SONNET, OPUS], OPUS)),
                               encoding="utf-8")
        proc = self.scripted([], 0, "--model", OPUS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(self._calls(), [])
        found = self.summaries()
        self.assertEqual(len(found), 2)
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assertIn("judge", summary["error"]["detail"])
                self.assertIsNone(summary["judge_selection"])
                self.assertEqual(summary["judge_models_used"], [])

    def test_a_fixture_pin_is_recorded_as_one(self):
        fixture = _write_fixture(self.evals / "pinned-skill", "pinned-skill")
        registry = self._registry("pinned-skill")
        self._script(agents=[BARE, BARE], judges=[{"overall": 7.0}] * 2)
        proc = self._run(fixture, "--arm", "both", "--registry",
                         f"acsi-agentskills={registry}", "--timestamp", TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for rel, summary in self.summaries().items():
            with self.subTest(summary=rel):
                self.assertEqual(summary["judge_selection"], {
                    "model": "fake-judge-model", "rule": "fixture-pin",
                    "arm_tier": None, "judge_tier": None})


class IngesterTests(unittest.TestCase):
    """The routine's ingester rejects a summary key it does not know, so it
    must know this one and hold it to the shape the harness writes."""

    GOOD = {"model": OPUS, "rule": "tier-above-arm",
            "arm_tier": "sonnet", "judge_tier": "opus"}

    def summary(self, **changes) -> dict:
        doc = {"skill": "a-skill", "arm": "with_skill", "timestamp": TS,
               "error": None, "agent": None, "objective_checks": None,
               "judge": None, "n": 1,
               "harness": {"name": "h", "version": None,
                           "permission_mode": None},
               "models_used": [], "judge_models_used": [],
               "judge_selection": copy.deepcopy(self.GOOD)}
        doc.update(changes)
        return doc

    def check(self, doc: dict) -> None:
        rel = f"a-skill/{TS}/with_skill/summary.json"
        ingest.check_summary(doc, rel, ingest.parse_result_path(rel))

    def test_the_shapes_the_harness_writes_are_accepted(self):
        for label, value in {
                "tier rule": self.GOOD,
                "roster judge": {**self.GOOD, "model": FABLE,
                                 "rule": "roster-judge",
                                 "judge_tier": "fable/mythos"},
                "pin with no tier": {"model": "fake-judge-model",
                                     "rule": "fixture-pin", "arm_tier": None,
                                     "judge_tier": None},
                "no judge": None}.items():
            with self.subTest(shape=label):
                self.check(self.summary(judge_selection=value))

    def test_a_summary_written_before_the_field_is_still_accepted(self):
        doc = self.summary()
        del doc["judge_selection"]
        self.check(doc)

    def test_other_shapes_are_rejected(self):
        bad = {
            "unknown rule": {**self.GOOD, "rule": "strongest"},
            "missing key": {k: v for k, v in self.GOOD.items()
                            if k != "judge_tier"},
            "extra key": {**self.GOOD, "reason": "x"},
            "model with a space": {**self.GOOD, "model": "claude opus"},
            "model null": {**self.GOOD, "model": None},
            "tier not a string": {**self.GOOD, "arm_tier": 2},
            "tier too long": {**self.GOOD, "judge_tier": "t" * 65},
            "a list": [OPUS],
            "a string": OPUS,
        }
        for label, value in bad.items():
            with self.subTest(shape=label):
                with self.assertRaises(ingest.Rejected):
                    self.check(self.summary(judge_selection=value))


class FixtureModelPinTests(unittest.TestCase):
    """A fixture follows the roster unless it says why not.

    `model:` and `judge.model:` pins go stale when the roster moves on (twelve
    fixtures kept the retired `claude-sonnet-5` and a below-arm Opus judge
    until 2026-10-27). So no pin is accepted without a non-empty
    `model_pin_reason:` (beside `model:`) / `judge.model_pin_reason:` (beside
    `judge.model:`), not even one naming a current roster id: the check never
    reads the roster, so a roster pull request that drops a model can never
    turn it red, and an unreasoned fixture always follows the roster.
    """

    @staticmethod
    def unreasoned_pins(evals_dir: Path) -> list:
        """`path: key=id` for every pin without its reason key."""
        found = []
        for path in sorted(evals_dir.rglob("fixture.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            judge = doc.get("judge") if isinstance(doc.get("judge"), dict) else {}
            for label, pin, reason in (
                    ("model", doc.get("model"), doc.get("model_pin_reason")),
                    ("judge.model", judge.get("model"), judge.get("model_pin_reason"))):
                if pin is None:
                    continue
                if isinstance(reason, str) and reason.strip():
                    continue
                found.append(f"{path.relative_to(evals_dir).as_posix()}: "
                             f"{label}={pin}")
        return found

    def write(self, root: Path, name: str, text: str) -> None:
        target = root / name / "fixture.yaml"
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding="utf-8")

    def test_every_committed_fixture_pin_is_reasoned(self):
        self.assertEqual(
            self.unreasoned_pins(REPO_ROOT / "evals"), [],
            "a fixture pins a model without saying why: delete the pin so it "
            "follows the roster, or add model_pin_reason: / "
            "judge.model_pin_reason: saying why it must stay")

    def test_any_unreasoned_pin_fails_even_a_current_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "stale", "model: old-1\njudge:\n  model: old-2\n")
            self.write(root, "current",
                       "model: model-a-2\njudge:\n  model: model-b-1\n")
            self.write(root, "reasoned",
                       "model: old-1\nmodel_pin_reason: needs the old tokenizer\n"
                       "judge:\n  model: old-2\n  model_pin_reason: ' '\n")
            self.write(root, "unpinned", "judge:\n  mode: pairwise\n")
            self.assertEqual(
                self.unreasoned_pins(root),
                ["current/fixture.yaml: model=model-a-2",
                 "current/fixture.yaml: judge.model=model-b-1",
                 "reasoned/fixture.yaml: judge.model=old-2",
                 "stale/fixture.yaml: model=old-1",
                 "stale/fixture.yaml: judge.model=old-2"])


if __name__ == "__main__":
    unittest.main()
