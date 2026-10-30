#!/usr/bin/env python3
"""`scripts/hook_block_report.py`: the private report of blocked git hook runs.

Every event, weekly file and repository here is synthetic and invented,
written under a mkdtemp; `--now` is always passed. No network, no clock.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import hook_block_report as hbr  # noqa: E402

NOW_TEXT = "2026-10-28T20:30:00Z"
NOW = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc)
REPO = "Example-Owner/feed-repo"
MACHINE = "EXAMPLE-PC"


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.events = self.tmp / "events-checkout"
        self.events.mkdir()
        self.counter = 0

    def hook(self, when, repo=REPO, failed=("staged-index",), event="pre-push",
             machine=MACHINE, rid=None):
        self.counter += 1
        return {"v": 1, "id": rid or f"id-{self.counter}", "ts": stamp(when),
                "kind": "hook", "event": event, "repo": repo,
                "checks": ["staged-index", "push-to-default"],
                "failed": list(failed), "surface": "local", "root": "wsl",
                "machine": machine}

    def skill(self, when, machine=MACHINE):
        self.counter += 1
        return {"v": 1, "id": f"id-{self.counter}", "ts": stamp(when),
                "kind": "skill", "name": "example-skill", "machine": machine}

    def write_events(self, rows, machine=MACHINE, month="2026-10", raw=()):
        d = self.events / "events" / month
        d.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r) for r in rows] + list(raw)
        (d / f"{machine}.jsonl").write_text("\n".join(lines) + "\n")

    def weekly(self, name, doc):
        path = self.tmp / name
        path.write_text(json.dumps(doc))
        return path

    def fresh_local(self, hooks=True):
        sources = [{"scope": "transcripts", "sessions_scanned": 5,
                    "surface": "local"}]
        if hooks:
            sources.append({"scope": "hooks", "runs_scanned": 9,
                            "surface": "local"})
        return {"generated_at": "2026-10-28T16:37:18Z", "schema": 1,
                "sources": sources, "rows": []}

    def fresh_ci(self, rows=()):
        return {"generated_at": "2026-10-28T16:37:18Z", "schema": 1,
                "sources": [{"surface": "ci", "scope": "github",
                             "repos_scanned": 2}], "rows": list(rows)}

    def run_analyze(self, local="default", ci="default", days=7, stale=48):
        records, sources = hbr.load_events(self.events)
        return hbr.analyze(
            records, self.fresh_local() if local == "default" else local,
            self.fresh_ci() if ci == "default" else ci, NOW, days, stale,
            event_sources=sources)

    def types(self, report, kind):
        return [f for f in report["findings"] if f["type"] == kind]


class LocalAnalysisTests(Fixture):
    def test_hammering_repo_is_concentrated_and_steady(self):
        start = datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc)
        rows = [self.hook(start + timedelta(hours=h, minutes=m // 2, seconds=m % 2))
                for h in range(8) for m in range(60)]
        self.write_events(rows)
        report = self.run_analyze()
        conc = self.types(report, "concentration")
        self.assertEqual(len(conc), 1)
        self.assertEqual(conc[0]["severity"], "high")
        self.assertEqual(conc[0]["repo"], REPO)
        self.assertEqual(conc[0]["share"], 1.0)
        steady = self.types(report, "steady-repeat")
        self.assertEqual(len(steady), 1)
        s = steady[0]
        self.assertEqual((s["first_hour"], s["last_hour"]),
                         ("2026-10-28T04", "2026-10-28T11"))
        self.assertEqual((s["hours"], s["blocked"]), (8, 480))
        self.assertEqual(s["median_per_hour"], 60.0)
        self.assertEqual(s["checks"], ["staged-index"])
        self.assertIn("automated", s["summary"])
        self.assertEqual(report["local"]["blocked"], 480)

    def test_spread_blocks_raise_no_finding(self):
        rows = []
        for i in range(12):
            repo = f"Example-Owner/repo-{i % 4}"
            rows.append(self.hook(NOW - timedelta(hours=i, minutes=1), repo=repo))
        self.write_events(rows)
        report = self.run_analyze()
        self.assertEqual(report["local"]["blocked"], 12)
        self.assertEqual(self.types(report, "concentration"), [])
        self.assertEqual(self.types(report, "steady-repeat"), [])

    def test_below_min_blocks_is_not_concentration(self):
        self.write_events([self.hook(NOW - timedelta(hours=i)) for i in range(5)])
        self.assertEqual(self.types(self.run_analyze(), "concentration"), [])

    def test_gap_hour_breaks_steady_run(self):
        base = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        rows = [self.hook(base + timedelta(hours=h, minutes=m))
                for h in (0, 1, 3, 4) for m in range(5)]
        self.write_events(rows)
        self.assertEqual(self.types(self.run_analyze(), "steady-repeat"), [])

    def test_window_dedupe_and_malformed(self):
        old = NOW - timedelta(days=10)
        inside = NOW - timedelta(hours=2)
        dup = self.hook(inside, rid="same")
        rows = [self.hook(old), self.skill(old), dup, dict(dup),
                self.hook(inside, failed=()), self.skill(NOW - timedelta(hours=1))]
        raw = ["{not json", json.dumps([1]), json.dumps({**rows[0], "v": 2}),
               json.dumps({**rows[0], "id": "x", "ts": "garbage"}),
               json.dumps({**rows[0], "id": "y", "failed": "oops"})]
        self.write_events(rows, raw=raw)
        report = self.run_analyze()
        self.assertEqual(report["local"]["runs"], 2)
        self.assertEqual(report["local"]["blocked"], 1)
        self.assertEqual(report["local"]["malformed"], 5)
        src = [s for s in report["sources"] if s["name"].startswith("usage-events")]
        self.assertEqual(len(src), 1)
        self.assertEqual(src[0]["read"]["lines"], 11)
        self.assertEqual(src[0]["read"]["hook_runs_in_window"], 2)
        self.assertEqual(report["local"]["by_hour"], {hbr.hour_key(inside): 1})

    def test_old_records_count_for_freshness_only(self):
        newest = NOW - timedelta(days=3)
        self.write_events([self.hook(NOW - timedelta(days=10)), self.skill(newest)])
        report = self.run_analyze()
        self.assertEqual(report["local"]["runs"], 0)
        stale = self.types(report, "source-stale")
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["newest"], hbr.fmt_ts(newest))


class FreshnessTests(Fixture):
    def test_stale_events_file(self):
        self.write_events([self.hook(NOW - timedelta(hours=72))])
        stale = self.types(self.run_analyze(), "source-stale")
        self.assertEqual([f["source"] for f in stale],
                         [f"usage-events:{MACHINE}"])
        self.assertAlmostEqual(stale[0]["age_hours"], 72.0)
        self.assertEqual(stale[0]["path"],
                         f"usage-events/2026-10/{MACHINE}.jsonl")

    def test_fresh_events_file_has_no_finding(self):
        self.write_events([self.hook(NOW - timedelta(hours=1))])
        report = self.run_analyze()
        self.assertEqual(report["findings"], [])

    def test_no_event_files(self):
        report = self.run_analyze()
        missing = self.types(report, "source-missing")
        self.assertEqual([f["source"] for f in missing], ["usage-events"])

    def test_missing_weekly_ci(self):
        self.write_events([self.hook(NOW)])
        report = self.run_analyze(ci=None)
        missing = self.types(report, "source-missing")
        self.assertEqual([f["source"] for f in missing], ["weekly-ci"])

    def test_weekly_local_without_hooks_source(self):
        self.write_events([self.hook(NOW)])
        report = self.run_analyze(local=self.fresh_local(hooks=False))
        missing = self.types(report, "source-missing")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["detail"],
                         "no hooks source: hook counts unknown")

    def test_stale_weekly_files(self):
        self.write_events([self.hook(NOW)])
        local = {**self.fresh_local(), "generated_at": "2026-10-20T00:00:00Z"}
        ci = {**self.fresh_ci(), "generated_at": "2026-10-20T00:00:00Z"}
        report = self.run_analyze(local=local, ci=ci)
        self.assertEqual(
            sorted(f["source"] for f in self.types(report, "source-stale")),
            ["weekly-ci", "weekly-local"])


class CiTests(Fixture):
    def row(self, week, repo, blocked, invocations=20):
        return {"week": week, "kind": "hook", "name": "agent-checks",
                "repo": repo, "surface": "ci", "invocations": invocations,
                "sessions": None, "blocked": blocked, "overridden": 0,
                "outcome": None}

    def test_only_overlapping_weeks_count(self):
        self.write_events([self.hook(NOW)])
        rows = [
            self.row("2026-10-26", "Example-Owner/ci-repo", 9),
            self.row("2026-10-19", "Example-Owner/ci-repo", 9),
            self.row("2026-10-12", "Example-Owner/ci-repo", 500),
            self.row("2026-11-02", "Example-Owner/ci-repo", 500),
            self.row("2026-10-26", "Example-Owner/other", 1),
            {**self.row("2026-10-26", "Example-Owner/ci-repo", 99),
             "kind": "repo"},
        ]
        report = self.run_analyze(ci=self.fresh_ci(rows))
        self.assertEqual(report["ci"]["blocked_by_repo"],
                         {"Example-Owner/ci-repo": 18, "Example-Owner/other": 1})
        finding = self.types(report, "concentration-ci")
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0]["repo"], "Example-Owner/ci-repo")
        self.assertEqual(finding[0]["blocked"], 18)
        self.assertAlmostEqual(finding[0]["share"], 18 / 19)
        self.assertEqual(finding[0]["severity"], "high")

    def test_small_ci_total_is_quiet(self):
        self.write_events([self.hook(NOW)])
        rows = [self.row("2026-10-26", "Example-Owner/ci-repo", 9)]
        report = self.run_analyze(ci=self.fresh_ci(rows))
        self.assertEqual(self.types(report, "concentration-ci"), [])


class MainTests(Fixture):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = hbr.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_outputs_and_one_line_summary(self):
        start = datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc)
        self.write_events([self.hook(start + timedelta(hours=h, minutes=m))
                           for h in range(4) for m in range(10)])
        local = self.weekly("weekly-local.json", self.fresh_local())
        ci = self.weekly("weekly-ci.json", self.fresh_ci())
        jout, mout = self.tmp / "r.json", self.tmp / "r.md"
        code, out, _ = self.run_main(
            "--events", str(self.events), "--weekly-local", str(local),
            "--weekly-ci", str(ci), "--now", NOW_TEXT,
            "--json-out", str(jout), "--markdown-out", str(mout))
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [
            "hook block report: 40 runs, 40 blocked, 2 findings"])
        self.assertNotIn("Example-Owner", out)
        text = jout.read_text()
        self.assertTrue(text.endswith("\n"))
        report = json.loads(text)
        self.assertEqual(text, json.dumps(report, sort_keys=True, indent=2) + "\n")
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["window"]["days"], 7)
        md = mout.read_text()
        self.assertTrue(md.startswith("## Git hook blocks, 2026-10-21 to 2026-10-28"))
        for heading in ("### Findings", "### Local blocks by repo", "### By check",
                        "### By hour (UTC)", "### CI blocks by repo", "### Sources"):
            self.assertIn(heading, md)
        self.assertIn(REPO, md)

    def test_markdown_has_no_absolute_paths(self):
        self.write_events([self.hook(NOW - timedelta(hours=100))])
        code, out, _ = self.run_main(
            "--events", str(self.events), "--weekly-local",
            str(self.tmp / "absent.json"), "--now", NOW_TEXT)
        self.assertEqual(code, 0)
        self.assertNotIn(str(self.tmp), out)
        self.assertNotIn("/tmp", out)
        self.assertIn("usage-events/2026-10/", out)
        self.assertIn("source-missing", out)

    def test_invalid_weekly_json_is_a_finding(self):
        self.write_events([self.hook(NOW)])
        bad = self.tmp / "bad.json"
        bad.write_text("{nope")
        jout = self.tmp / "r.json"
        code, _, _ = self.run_main("--events", str(self.events),
                                   "--weekly-ci", str(bad), "--now", NOW_TEXT,
                                   "--json-out", str(jout))
        self.assertEqual(code, 0)
        sources = {f["source"] for f in json.loads(jout.read_text())["findings"]
                   if f["type"] == "source-missing"}
        self.assertIn("weekly-ci", sources)

    def test_missing_events_dir_is_usage_error(self):
        code, out, _ = self.run_main("--events", str(self.tmp / "nope"),
                                     "--now", NOW_TEXT)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_hours_list_is_capped(self):
        base = NOW - timedelta(days=6)
        self.write_events([self.hook(base + timedelta(hours=h)) for h in range(60)])
        md = hbr.render_markdown(self.run_analyze())
        self.assertIn("… 12 more hours", md)


if __name__ == "__main__":
    unittest.main()
