"""dashboard/build.py: every snapshot dataset comes from a read or the owner's config.

Invented data only (example-owner, thread-000N). Deterministic: every build
passes --now.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DASH = REPO / "dashboard"
spec = importlib.util.spec_from_file_location("dashboard_build", DASH / "build.py")
build = importlib.util.module_from_spec(spec)
sys.modules["dashboard_build"] = build
spec.loader.exec_module(build)

NOW = datetime(2026, 1, 6, 13, 0, tzinfo=timezone.utc)
SITE = {"thread_url": "https://claude.ai/code/project/p?thread=", "thread_titles": {}}


def summary(arm, passed, total, **extra):
    return {"arm": arm, "n": 3, "models_used": ["claude-sonnet-9-9"],
            "aggregate": {"objective": {"mean_passed": passed, "mean_total": total}, "cost_usd": {"sum": 1.0}},
            **extra}


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def make_results(tmp_path):
    """A results branch with one paired run, one bare run, one guidance run and one usage file."""
    er = tmp_path / "results"
    paired = er / "routine-results" / "20260105T120000Z-aaaaaa" / "writing-adrs" / "ts"
    write(paired / "with_skill" / "summary.json", summary("with_skill", 9, 10))
    write(paired / "without_skill" / "summary.json", summary("without_skill", 6, 10))
    (paired / "report.md").write_text("report")
    bare = er / "routine-results" / "20260105T130000Z-bbbbbb" / "writing-adrs" / "ts"
    write(bare / "without_skill" / "summary.json", summary("without_skill", 5, 10))
    (bare / "report.md").write_text("report")
    guide = er / "routine-results" / "20260105T140000Z-cccccc" / "guidance" / "links" / "ts"
    one = {"objective_checks": [{"passed": True}, {"passed": False}], "models_used": ["claude-opus-9-9"],
           "section": "links", "fixture_name": "handoff"}
    write(guide / "with_guidance_full" / "summary.json", {"arm": "with_guidance_full", **one})
    write(guide / "with_guidance_full_minus_section" / "summary.json",
          {"arm": "with_guidance_full_minus_section", **one,
           "objective_checks": [{"passed": False}, {"passed": False}]})
    (guide / "report.md").write_text("report")
    write(er / "usage" / "weekly-local.json", {
        "schema": 1, "generated_at": "2026-01-05T00:00:00Z",
        "sources": [{"surface": "local", "scope": "transcripts", "from": "a", "to": "b", "sessions_scanned": 4},
                    {"surface": "local", "scope": "hooks", "from": "a", "to": "b", "runs_scanned": 9}],
        "rows": [{"week": "2026-01-05", "kind": "hook", "name": "all", "invocations": 7},
                 {"week": "2026-01-05", "kind": "skill", "name": "writing-adrs", "invocations": 2}]})
    return er




def routine(**kw):
    return {"name": "R", "what": "w", "schedule": "s", "note": "", "link": None, **kw}


class DashboardBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.results = make_results(self.tmp)

    def test_eval_rows_pairs_bare_and_guidance(self):
        rows, bare = build.eval_rows(str(self.results), "https://example.com/r/")
        by_skill = {r["skill"]: r for r in rows}
        self.assertEqual((by_skill["writing-adrs"]["with_skill"], by_skill["writing-adrs"]["gain"]), (9, 3))
        self.assertEqual((by_skill["guidance/links"]["with_skill"], by_skill["guidance/links"]["without_skill"]), (1, 0))
        self.assertEqual(by_skill["guidance/links"]["fixture"], "guidance/links/handoff")
        self.assertEqual([b["bare"] for b in bare], [5])
        # The bare run sits beside the newest paired run of the same test and model.
        self.assertEqual((bare[0]["with_share"], bare[0]["without_share"]), (0.9, 0.6))
        self.assertTrue(by_skill["writing-adrs"]["report_url"].startswith("https://example.com/r/routine-results/"))

    def test_empty_results_get_placeholder_rows(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        rows, bare = build.eval_rows(str(empty), "x/")
        self.assertEqual(([r["run_id"] for r in rows], [r["run_id"] for r in bare]), (["none"], ["none"]))
        rows, sources = build.usage(str(empty))
        self.assertEqual((rows[0]["kind"], sources[0]["file"]), ("none", "none"))

    def test_usage_keeps_the_weekly_total_apart(self):
        rows, sources = build.usage(str(self.results))
        self.assertEqual({r["kind"] for r in rows}, {"hook_all", "skill"})

    def test_usage_sources_carry_each_producers_read_count_and_unit(self):
        _, sources = build.usage(str(self.results))
        self.assertEqual([(s["scanned"], s["scanned_unit"]) for s in sources], [(4, "sessions"), (9, "git hook runs")])
        self.assertEqual(len({s["key"] for s in sources}), 2)

    def test_usage_refuses_an_unknown_schema(self):
        write(self.tmp / "u" / "usage" / "weekly-x.json", {"schema": 2})
        with self.assertRaises(SystemExit):
            build.usage(str(self.tmp / "u"))

    def test_threads_say_waiting_on_you_and_take_title_overrides(self):
        site = {**SITE, "thread_titles": {"t2": "Renamed"}}
        rows = build.threads([
            {"thread_id": "t1", "title": "A", "state": "blocked", "last_activity_at": "2026-01-05T00:00:00Z"},
            {"thread_id": "t2", "title": "B", "state": "done", "last_activity_at": "2026-01-06T00:00:00Z"}], site)
        self.assertEqual([r["title"] for r in rows], ["Renamed", "A"])
        self.assertEqual(rows[1]["state"], "Waiting on you")
        self.assertEqual(rows[1]["link"], SITE["thread_url"] + "t1")

    def test_routine_health_comes_from_the_last_run(self):
        trig = {"kind": "trigger", "id": "t"}
        cases = [
            (routine(source=trig), {"t": {"last_run": {"status": "ROUTINE_RUN_STATUS_FAILED", "fired_at": "2026-01-06T12:00:00Z"}}}, {}, "Failing", "Failed"),
            (routine(source=trig, expect_every_hours=1), {"t": {"last_run": {"status": "ROUTINE_RUN_STATUS_SUCCEEDED", "fired_at": "2026-01-06T09:00:00Z"}}}, {}, "Late", "Succeeded"),
            (routine(source=trig, expect_every_hours=1), {"t": {"last_run": {"status": "ROUTINE_RUN_STATUS_SUCCEEDED", "fired_at": "2026-01-06T12:30:00Z"}}}, {}, "OK", "Succeeded"),
            (routine(source=trig), {"t": {"enabled": False}}, {}, "Paused", None),
            (routine(source={"kind": "none"}, retired=True), {}, {}, "Retired", "Idle"),
            (routine(source={"kind": "workflow", "repo": "o/r", "workflow": "w.yml"}), {},
             {"o/r:w.yml": [{"created_at": "2026-01-05T00:00:00Z", "status": "completed", "conclusion": "success"},
                            {"created_at": "2026-01-06T00:00:00Z", "status": "completed", "conclusion": "cancelled"}]},
             "Check", "Cancelled"),
        ]
        for cfg, triggers, runs, health, result in cases:
            with self.subTest(health=health):
                row = build.routines([cfg], triggers, runs, NOW)[0]
                self.assertEqual((row["health"], row["last_result"]), (health, result))

    def test_get_trigger_output_is_accepted_whole(self):
        full = {"t": {"trigger": {"enabled": True, "next_run_at": "2026-01-06T14:00:00.5Z",
                                  "last_run": {"status": "ROUTINE_RUN_STATUS_PENDING", "fired_at": "2026-01-06T12:59:00.1Z"}}}}
        row = build.routines([routine(source={"kind": "trigger", "id": "t"})], full, {}, NOW)[0]
        self.assertEqual((row["last_result"], row["last_run"], row["next_run"]),
                         ("Running", "2026-01-06T12:59:00Z", "2026-01-06T14:00:00Z"))

    def test_next_cron(self):
        for expr, want in [("0 7 * * 2", "2026-01-13T07:00:00Z"),  # NOW is Tuesday 13:00, so next Tuesday
                           ("53 6 * * *", "2026-01-07T06:53:00Z"),
                           ("20 * * * *", "2026-01-06T13:20:00Z"),
                           ("0 7 1 * *", None)]:  # day-of-month schedules are not computed
            with self.subTest(expr=expr):
                self.assertEqual(build.next_cron(expr, NOW), want)

    def test_checkins_keep_this_projects_pending_one_shots(self):
        rows = build.checkins([
            {"id": "a", "name": "Later", "run_once_at": "2026-01-07T00:00:00Z", "hearth_channel_id": "c1"},
            {"id": "b", "name": "Past", "run_once_at": "2026-01-05T00:00:00Z", "hearth_channel_id": "c1"},
            {"id": "c", "name": "Other project", "run_once_at": "2026-01-07T00:00:00Z", "hearth_channel_id": "c2"},
        ], "c1", NOW)
        self.assertEqual([r["id"] for r in rows], ["a"])
        self.assertEqual(build.checkins([], "c1", NOW)[0]["id"], "none")

    def test_waiting_collects_every_source_and_drops_what_is_done(self):
        snap = {"cleanup": [
            {"id": "C1", "status": "Not started", "waiting_on_you": "Needs the laptop", "waiting_since": "2026-01-02T00:00:00Z"},
            {"id": "C2", "status": "Done", "waiting_on_you": "Needs the laptop"},
            {"id": "C3", "status": "Not started", "waiting_on_you": "Needs the laptop", "waiting_since": "2026-01-01T00:00:00Z"}],
            "waiting_manual": [{"item": "Merged already", "link": "https://github.com/example-owner/r/pull/1"},
                               {"item": "Still open", "link": "https://github.com/example-owner/r/pull/2"}]}
        needs = [{"title": "Decide", "html_url": "https://github.com/example-owner/r/pull/3", "state": "open",
                  "repository_url": "https://api.github.com/repos/example-owner/r", "labels": [{"name": "needs-owner"}]},
                 {"title": "Held", "html_url": "https://github.com/example-owner/r/issues/4", "state": "open",
                  "repository_url": "https://api.github.com/repos/example-owner/r", "labels": [{"name": "on-hold"}]}]
        reviews = [{"repo": "example-owner/r", "name": "PR", "environment": "env", "head_sha": "new", "job_url": "https://example.com/new"},
                   {"repo": "example-owner/r", "name": "PR", "environment": "env", "head_sha": "old", "job_url": "https://example.com/old"}]
        threads = [{"thread_id": "t1", "title": "Blocked one", "state": "blocked"},
                   {"thread_id": "t2", "title": "Resolved", "state": "blocked", "resolved": True}]
        rows = build.waiting(snap, needs, reviews, {"example-owner/r": ["new"]}, threads,
                             {"https://github.com/example-owner/r/pull/1": "merged"}, SITE)
        self.assertEqual([r["item"] for r in rows],
                         ["Decide", "Approve the env review for PR", "Blocked one", "Cleanup items C1, C3", "Still open"])
        self.assertEqual(rows[0]["where"], "example-owner/r")
        self.assertEqual(rows[1]["link"], "https://example.com/new")
        self.assertEqual(rows[3]["since"], "2026-01-01T00:00:00Z")
        self.assertTrue(all(r["id"] for r in rows))

    def test_waiting_has_a_placeholder_when_nothing_waits(self):
        rows = build.waiting({"cleanup": []}, [], [], {}, [], {}, SITE)
        self.assertEqual([(r["id"], r["item"]) for r in rows], [("none", "Nothing waiting on you")])

    def test_loop_stage_status_follows_its_gaps(self):
        cfg = {"home_thread_id": "t0", "grid": [],
               "stages": [{"order": "1", "kind": "k", "stage": "a", "what": "w", "gaps": ["G1", "G2"]},
                          {"order": "2", "kind": "k", "stage": "b", "what": "w", "gaps": ["G1"]},
                          {"order": "3", "kind": "k", "stage": "c", "what": "w", "gaps": [], "override": "Off"}],
               "lanes": [{"lane": "L", "thread": "T", "thread_id": "t1", "does": "d", "latest": "x"}]}
        status = {"gaps": [{"gap": "G1", "title": "x", "status": "done", "pr": "https://example.com/1"},
                           {"gap": "G2", "title": "y", "status": "open"}]}
        stages, grid, lanes, gaps = build.loop(cfg, status, SITE)
        self.assertEqual([s["status"] for s in stages], ["In progress", "Done", "Off"])
        self.assertEqual((stages[1]["link"], stages[2]["link"]), ("https://example.com/1", SITE["thread_url"] + "t0"))
        self.assertEqual(lanes[0]["link"], SITE["thread_url"] + "t1")

    def test_roster_and_budget_are_read_from_the_repo(self):
        rows = build.roster({"why": {}, "dropped": [
            {"id": "claude-gone-1", "model": "Gone 1", "role": "Tested", "status": "Dropped"}]})
        roles = [r["role"] for r in rows]
        self.assertEqual((roles.count("Judge"), roles.count("Quick check")), (1, 1))
        self.assertIn("Tested", roles)
        self.assertEqual(rows[-1]["id"], "claude-gone-1")
        self.assertEqual(build.model_name("claude-sonnet-5-5"), "Sonnet 5.5")
        self.assertEqual([r["setting"] for r in build.budget()], build.BUDGET_KEYS)

    def test_example_config_builds_every_dataset_the_page_defines(self):
        out = self.tmp / "ds"
        build.main(["--results", str(self.results), "--inputs", str(DASH / "inputs.example"),
                    "--config", str(DASH / "config.example"), "--out", str(out), "--now", NOW.isoformat()])
        built = {p.stem for p in out.glob("*.json")}
        defined = json.loads((DASH / "page" / "datasets.json").read_text())
        self.assertEqual(built, {k for k, v in defined.items() if v["source"]["kind"] == "file"})
        for name in built:
            self.assertTrue(json.loads((out / f"{name}.json").read_text()), name)
        waiting = json.loads((out / "waiting.json").read_text())
        self.assertNotIn("Paused idea", [r["item"] for r in waiting])  # labeled on-hold

    def test_builds_from_fully_empty_inputs(self):
        # Cold start: a new adopter has no thread list, no dumps and no
        # loop_status.json. The example config builds anyway, and the two
        # datasets that would be empty carry one placeholder row.
        empty_results, empty_inputs, out = self.tmp / "r0", self.tmp / "i0", self.tmp / "ds0"
        empty_results.mkdir()
        empty_inputs.mkdir()
        build.main(["--results", str(empty_results), "--inputs", str(empty_inputs),
                    "--config", str(DASH / "config.example"), "--out", str(out), "--now", NOW.isoformat()])
        threads = json.loads((out / "threads.json").read_text())
        gaps = json.loads((out / "loop_gaps.json").read_text())
        self.assertEqual([r["id"] for r in threads], ["none"])
        self.assertEqual([r["gap"] for r in gaps], ["none"])
        for name in {p.stem for p in out.glob("*.json")}:
            self.assertTrue(json.loads((out / f"{name}.json").read_text()), name)
        page = (DASH / "page" / "index.html").read_text()
        self.assertIn("threads: 'id', loop_gaps: 'gap'", page)  # the page hides both placeholders

    def test_the_page_names_no_owner(self):
        page = (DASH / "page" / "index.html").read_text()
        defined = json.loads((DASH / "page" / "datasets.json").read_text())
        words = json.dumps({k: [v.get("title"), v.get("description")] for k, v in defined.items()})
        self.assertIn("Waiting on you", page)
        for text in (page, words):
            for name in ("Waiting on the owner", "the owner's", "the owner "):
                self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
