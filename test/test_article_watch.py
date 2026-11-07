"""Tests for scripts/article_watch.py (the Anthropic article watch).

Run through test/run-tests.sh's article_watch group. No network: every fire
goes to a recording stub.
"""

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("article_watch", ROOT / "scripts" / "article_watch.py")
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)

FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/trig_example/fire"
NOW = "2026-11-07T12:00:00+00:00"


def feed(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><guid isPermaLink=\"true\">{g or l}</guid></item>"
        for t, l, g in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


OLD = ("Building effective agents", "https://www.anthropic.com/engineering/old-agents", None)
OTHER = ("Prompt caching notes", "https://www.anthropic.com/engineering/caching", None)
NEW = ("How our AGENTIC harness works", "https://www.anthropic.com/engineering/new-harness", None)


class Stub:
    def __init__(self, status="200"):
        self.calls = []
        self.status = status

    def __call__(self, url, bearer, text):
        self.calls.append((url, bearer, text))
        return self.status, "https://claude.ai/code/session_example"


def seeded(*items):
    state, rc, _ = aw.run(feed(*items), None, reseed=False, fire_url=FIRE_URL, bearer="b",
                          fire=Stub(), now=NOW)
    assert rc == 0
    return state


class ArticleWatch(unittest.TestCase):
    def go(self, feed_text, state, stub=None, fire_url=FIRE_URL, bearer="b", reseed=False):
        stub = stub or Stub()
        new_state, rc, log = aw.run(feed_text, state, reseed=reseed, fire_url=fire_url,
                                    bearer=bearer, fire=stub, now=NOW)
        return new_state, rc, log, stub

    def test_first_run_seeds_and_fires_nothing(self):
        state, rc, log, stub = self.go(feed(OLD, OTHER), None)
        self.assertEqual(rc, 0)
        self.assertEqual(stub.calls, [])
        self.assertEqual(set(state["items"]), {OLD[1], OTHER[1]})

    def test_new_matching_item_fires_once_with_the_prompt(self):
        state, rc, log, stub = self.go(feed(NEW, OLD, OTHER), seeded(OLD, OTHER))
        self.assertEqual(rc, 0)
        self.assertEqual(stub.calls, [(FIRE_URL, "b", f"See how much lift you can get out of {NEW[1]}")])
        self.assertEqual(state["items"][NEW[1]]["session"], "https://claude.ai/code/session_example")
        _, _, _, again = self.go(feed(NEW, OLD, OTHER), state)
        self.assertEqual(again.calls, [])

    def test_match_is_case_insensitive_and_non_matches_are_recorded(self):
        other_new = ("Evals at scale", "https://www.anthropic.com/engineering/evals", None)
        state, rc, _, stub = self.go(feed(NEW, other_new), seeded(OLD))
        self.assertEqual(len(stub.calls), 1)
        self.assertIn(other_new[1], state["items"])
        self.assertNotIn("fired_at", state["items"][other_new[1]])

    def test_failed_fire_is_not_recorded_so_it_retries(self):
        state, rc, log, stub = self.go(feed(NEW, OLD), seeded(OLD), stub=Stub(status="500"))
        self.assertEqual(rc, 1)
        self.assertNotIn(NEW[1], state["items"])
        self.assertTrue(any("HTTP 500" in line for line in log))

    def test_new_guid_for_a_seen_link_does_not_refire(self):
        moved = (OLD[0], OLD[1], "urn:new-guid-scheme")
        _, _, _, stub = self.go(feed(moved), seeded(OLD))
        self.assertEqual(stub.calls, [])

    def test_too_many_new_matches_fires_none_and_fails(self):
        many = [(f"Agent post {i}", f"https://www.anthropic.com/engineering/p{i}", None)
                for i in range(aw.MAX_FIRES_PER_RUN + 1)]
        start = seeded(OLD)
        state, rc, log, stub = self.go(feed(*many, OLD), start)
        self.assertEqual(rc, 1)
        self.assertEqual(stub.calls, [])
        self.assertEqual(state, start)

    def test_reseed_records_everything_and_fires_nothing(self):
        state, rc, _, stub = self.go(feed(NEW, OLD), seeded(OLD), reseed=True)
        self.assertEqual(rc, 0)
        self.assertEqual(stub.calls, [])
        self.assertIn(NEW[1], state["items"])

    def test_foreign_link_is_skipped_not_fired(self):
        bad = ("Agent tips", "https://example.com/agent-tips", None)
        state, rc, log, stub = self.go(feed(bad), seeded(OLD))
        self.assertEqual(stub.calls, [])
        self.assertIn("skipped", state["items"][bad[1]])

    def test_missing_config_fails_names_it_and_keeps_the_match_pending(self):
        for url, bearer, needle in [("", "b", aw.FIRE_URL_VAR), (FIRE_URL, "", aw.BEARER_SETTING),
                                    ("https://example.com/fire", "b", aw.FIRE_URL_VAR)]:
            state, rc, log, stub = self.go(feed(NEW, OTHER), seeded(OLD), fire_url=url, bearer=bearer)
            self.assertEqual(rc, 1)
            self.assertEqual(stub.calls, [])
            self.assertTrue(any(needle in line for line in log), log)
            self.assertNotIn(NEW[1], state["items"])
            self.assertIn(OTHER[1], state["items"])

    def test_missing_config_still_seeds_on_first_run(self):
        state, rc, _, stub = self.go(feed(OLD), None, fire_url="", bearer="")
        self.assertEqual(rc, 1)
        self.assertIn(OLD[1], state["items"])

    def test_empty_feed_fails_and_keeps_state(self):
        start = seeded(OLD)
        state, rc, _, _ = self.go(feed(), start)
        self.assertEqual(rc, 1)
        self.assertEqual(state, start)

    def test_main_round_trip_reads_and_writes_the_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp, "feed.xml")
            s = pathlib.Path(tmp, "state.json")
            f.write_text(feed(OLD), encoding="utf-8")
            env = {"FIRE_URL": FIRE_URL, "FIRE_BEARER": "b", "GITHUB_STEP_SUMMARY": ""}
            old_env = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                stub = Stub()
                self.assertEqual(aw.main(["--feed", str(f), "--state", str(s)], fire=stub), 0)
                f.write_text(feed(NEW, OLD), encoding="utf-8")
                self.assertEqual(aw.main(["--feed", str(f), "--state", str(s)], fire=stub), 0)
                self.assertEqual(len(stub.calls), 1)
                self.assertIn(NEW[1], json.loads(s.read_text())["items"])
                f.write_text("<rss><channel>", encoding="utf-8")
                self.assertEqual(aw.main(["--feed", str(f), "--state", str(s)], fire=stub), 1)
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
