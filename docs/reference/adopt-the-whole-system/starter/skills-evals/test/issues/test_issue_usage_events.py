#!/usr/bin/env python3
"""Private usage events and hook rows: `scripts/model_usage_census.py
--events-out/--hook-logs` and the private half of
`scripts/publish_usage_census.sh`.

The events file holds timestamps, session ids, titles and repositories, so
it goes only to the private `persistent/usage-events` branch; the public
weekly file gets counts. Every transcript and hook log here is synthetic and
invented, written under a mkdtemp; git remotes are local bare repositories
over `file://`; `now` is always passed in. No network, no clock.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "test" / "issues"))
import model_usage_census as census  # noqa: E402
import test_issue_skill_usage  # noqa: E402
from test_issue_skill_usage import (  # noqa: E402
    ALLOW, NOW, NOW_TEXT, skill_call, typed, write_transcript)


SESSION = "00000000-1111-4222-8333-444444444444"


def with_session(entry, cwd=None, branch="feature/example", uuid=None):
    entry = dict(entry, sessionId=SESSION, gitBranch=branch)
    if cwd is not None:
        entry["cwd"] = cwd
    if uuid is not None:
        entry["uuid"] = uuid
    return entry


def git(*args, cwd=None):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.com",
               GIT_COMMITTER_EMAIL="fixture@example.com")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class EventTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-events-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()

    def events(self, *transcripts, allowlist=ALLOW):
        for index, entries in enumerate(transcripts):
            write_transcript(self.projects / "-home-example-proj" / f"s{index}.jsonl",
                             entries)
        found: list = []
        census.census_all(self.projects, NOW, 8, allowlist, found)
        return found

    def test_a_skill_call_becomes_one_record_with_every_field(self):
        records = self.events([with_session(skill_call(
            "acsi-coding-anywhere:workflow-path-audit", "toolu_01"))])
        self.assertEqual(records, [{
            "v": 1, "id": "toolu_01", "ts": "2026-09-03T10:00:00.000Z", "kind": "skill",
            "name": "acsi-coding-anywhere:workflow-path-audit", "surface": "local",
            "root": "wsl", "machine": census.machine_name(), "session_id": SESSION,
            "session_title": None, "cwd_repo": None, "branch": "feature/example"}])

    def test_a_typed_command_uses_the_entry_uuid_and_drops_the_slash(self):
        records = self.events([with_session(
            typed("<command-name>/acsi-anything-anywhere:finding-unknowns</command-name>"),
            uuid="aaaaaaaa-0000-4000-8000-000000000001")])
        self.assertEqual([(r["id"], r["name"]) for r in records],
                         [("aaaaaaaa-0000-4000-8000-000000000001",
                           "acsi-anything-anywhere:finding-unknowns")])

    def test_an_unlisted_skill_keeps_its_raw_name_but_a_built_in_is_skipped(self):
        records = self.events([skill_call("zz-private-skill"),
                               typed("<command-name>/clear</command-name>")])
        self.assertEqual([r["name"] for r in records], ["zz-private-skill"])

    def test_names_are_strings_only_and_capped(self):
        records = self.events([skill_call(7, "toolu_a"), skill_call("", "toolu_b"),
                               skill_call("y" * 500, "toolu_c")])
        self.assertEqual([len(r["name"]) for r in records], [census.EVENT_TEXT_CAP])

    def test_no_records_without_an_events_list_or_outside_the_window(self):
        write_transcript(self.projects / "p" / "s.jsonl", [
            skill_call("workflow-path-audit", "toolu_old", when="2026-07-01T10:00:00Z")])
        found: list = []
        census.census_all(self.projects, NOW, 8, ALLOW, found)
        self.assertEqual(found, [])
        self.assertEqual(census.census_all(self.projects, NOW, 8, ALLOW)[1]["invocations"],
                         {})

    def test_the_session_title_is_the_latest_summary_or_custom_title(self):
        records = self.events(
            [{"type": "summary", "summary": "First invented title", "leafUuid": "x"},
             skill_call("workflow-path-audit", "toolu_01"),
             {"type": "custom-title", "customTitle": "Renamed invented session"},
             {"type": "summary", "summary": None}],
            [skill_call("workflow-path-audit", "toolu_02")])
        self.assertEqual(sorted((r["id"], r["session_title"]) for r in records),
                         [("toolu_01", "Renamed invented session"), ("toolu_02", None)])

    def test_cwd_repo_reads_a_github_origin_and_caches_per_directory(self):
        work = self.tmp / "widgets"
        work.mkdir()
        git("init", "--quiet", cwd=work)
        git("remote", "add", "origin", "https://github.com/example-owner/widgets.git",
            cwd=work)
        cache: dict = {}
        self.assertEqual(census.cwd_repo(str(work), cache), "example-owner/widgets")
        git("remote", "set-url", "origin", "https://example.com/elsewhere.git", cwd=work)
        self.assertEqual(census.cwd_repo(str(work), cache), "example-owner/widgets")
        self.assertIsNone(census.cwd_repo(str(work), {}))
        self.assertIsNone(census.cwd_repo(str(self.tmp / "gone"), {}))
        self.assertIsNone(census.cwd_repo("relative/path", {}))
        self.assertIsNone(census.cwd_repo(None, {}))
        records = self.events([with_session(skill_call("workflow-path-audit"),
                                            cwd=str(work))])
        self.assertIsNone(records[0]["cwd_repo"])
        git("remote", "set-url", "origin", "git@github.com:example-owner/widgets.git",
            cwd=work)
        records = self.events([with_session(skill_call("workflow-path-audit"),
                                            cwd=str(work))])
        self.assertEqual(records[0]["cwd_repo"], "example-owner/widgets")

    def test_only_github_owner_name_shapes_become_a_repo(self):
        good = {"https://github.com/example-owner/widgets": "example-owner/widgets",
                "https://example-user@github.com/example-owner/w.git":
                    "example-owner/w",
                "ssh://git@github.com/example-owner/w.b_c-d": "example-owner/w.b_c-d"}
        for url, repo in good.items():
            self.assertEqual(census.github_repo(url), repo, url)
        for url in ("https://github.com.example.com/a/b", "https://example.com/a/b",
                    "https://github.com/a/b/c", "https://github.com/-a/b",
                    "https://github.com/a/..", "https://github.com/a b/c",
                    "https://github.com/a/b\n", None, 7):
            self.assertIsNone(census.github_repo(url), url)

    def test_the_events_file_is_sorted_and_the_public_files_do_not_change(self):
        write_transcript(self.projects / "-home-example-proj" / "s.jsonl", [
            with_session(skill_call("workflow-path-audit", "toolu_late",
                                    when="2026-09-03T12:00:00Z")),
            with_session(skill_call("finding-unknowns", "toolu_early",
                                    when="2026-09-01T09:00:00Z"))])
        outputs = {}
        for label, extra in (("plain", []), ("events", ["--events-out",
                                                        str(self.tmp / "events.jsonl")])):
            out, weekly = self.tmp / f"{label}.json", self.tmp / f"{label}-weekly.json"
            argv = ["model_usage_census.py", "--projects", str(self.projects),
                    "--out", str(out), "--weekly-out", str(weekly), "--now", NOW_TEXT,
                    "--hook-logs", "", *extra]
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(census.main(), 0)
            outputs[label] = (out.read_bytes(), weekly.read_bytes())
        self.assertEqual(outputs["plain"], outputs["events"])
        lines = (self.tmp / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["id"] for line in lines],
                         ["toolu_early", "toolu_late"])
        self.assertNotIn(SESSION.encode(), outputs["events"][1])


class MultiRootTests(unittest.TestCase):
    """Several transcript roots: WSL, Windows (seen from WSL) and Cowork."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-roots-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wsl = self.tmp / "wsl" / ".claude" / "projects"
        self.windows = self.tmp / "win" / ".claude" / "projects"
        self.cowork = (self.tmp / "Claude-Data" / "claude_example" /
                       "local-agent-mode-sessions" / "s1" / ".claude" / "projects")

    def entries(self, call="toolu_01", message_id="msg_01", uuid="u-01"):
        return [skill_call("workflow-path-audit", call, model="claude-opus-5",
                           message_id=message_id),
                dict(typed("<command-name>/finding-unknowns</command-name>"), uuid=uuid)]

    def run_census(self, *roots):
        events: list = []
        models, tally = census.census_all(list(roots), NOW, 8, ALLOW, events)
        return models, tally["invocations"], events

    def test_a_session_under_two_roots_counts_once(self):
        write_transcript(self.wsl / "p" / "a.jsonl", self.entries())
        write_transcript(self.windows / "p" / "a.jsonl", self.entries())
        models, skills, events = self.run_census(self.wsl, self.windows)
        self.assertEqual(models, {"claude-opus-5": {"2026-W36": 1}})
        self.assertEqual(skills, {"workflow-path-audit": {"2026-W36": 1},
                                  "finding-unknowns": {"2026-W36": 1}})
        self.assertEqual({e["root"] for e in events}, {"wsl"})

    def test_different_sessions_in_each_root_all_count(self):
        write_transcript(self.wsl / "p" / "a.jsonl", self.entries())
        write_transcript(self.windows / "p" / "b.jsonl",
                         self.entries("toolu_02", "msg_02", "u-02"))
        models, skills, _ = self.run_census(self.wsl, self.windows)
        self.assertEqual(models, {"claude-opus-5": {"2026-W36": 2}})
        self.assertEqual(skills["workflow-path-audit"], {"2026-W36": 2})

    def test_a_cowork_session_stored_twice_counts_once(self):
        write_transcript(self.cowork / "p" / "one.jsonl", self.entries())
        write_transcript(self.cowork / "q" / "two.jsonl", self.entries())
        models, skills, events = self.run_census(self.cowork)
        self.assertEqual(models, {"claude-opus-5": {"2026-W36": 1}})
        self.assertEqual(skills["finding-unknowns"], {"2026-W36": 1})
        self.assertEqual([e["root"] for e in events], ["cowork", "cowork"])

    def test_a_resumed_session_in_one_ordinary_root_still_counts_twice(self):
        write_transcript(self.wsl / "p" / "a.jsonl", self.entries())
        write_transcript(self.wsl / "p" / "b.jsonl", self.entries())
        models, skills, _ = self.run_census(self.wsl)
        self.assertEqual(models, {"claude-opus-5": {"2026-W36": 2}})
        self.assertEqual(skills["workflow-path-audit"], {"2026-W36": 2})

    def test_root_kinds(self):
        self.assertEqual(census.root_kind("/home/example/.claude/projects"), "wsl")
        self.assertEqual(census.root_kind("/mnt/c/Users/example/.claude/projects"),
                         "windows")
        self.assertEqual(census.root_kind(
            "/mnt/c/Users/example/AppData/Local/Claude-Data/claude_x/"
            "local-agent-mode-sessions/s/.claude/projects"), "cowork")
        self.assertEqual(census.root_kind("/mnt/data/projects"), "wsl")

    def test_old_plugin_prefixes_reduce_to_the_skill(self):
        write_transcript(self.wsl / "p" / "a.jsonl", [
            skill_call("acsi:workflow-path-audit", "toolu_a"),
            skill_call("acsi-local:finding-unknowns", "toolu_b")])
        _, skills, events = self.run_census(self.wsl)
        self.assertEqual(skills, {"workflow-path-audit": {"2026-W36": 1},
                                  "finding-unknowns": {"2026-W36": 1}})
        self.assertEqual(sorted(e["name"] for e in events),
                         ["acsi-local:finding-unknowns", "acsi:workflow-path-audit"])

    def test_the_cli_takes_repeated_and_separated_roots(self):
        write_transcript(self.wsl / "p" / "a.jsonl", self.entries())
        write_transcript(self.windows / "p" / "b.jsonl",
                         self.entries("toolu_02", "msg_02", "u-02"))
        write_transcript(self.cowork / "p" / "c.jsonl",
                         self.entries("toolu_03", "msg_03", "u-03"))
        out = self.tmp / "latest.json"
        argv = ["model_usage_census.py", "--projects",
                os.pathsep.join([str(self.wsl), str(self.tmp / "absent")]),
                "--projects", str(self.windows), "--projects", str(self.cowork),
                "--out", str(out), "--now", NOW_TEXT]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            self.assertEqual(census.main(), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["counts"],
                         {"claude-opus-5": {"2026-W36": 3}})
        argv[2:3] = [str(self.tmp / "absent")]
        argv = argv[:3] + argv[7:]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(census.main(), 2)


PUBLIC = frozenset({"example-adopter/skills-evals", "example-owner/public-widgets"})


def hook_line(ts="2026-09-03T10:00:00Z", event="pre-commit",
              repo="example-owner/public-widgets", checks=("lint-a", "lint-b"),
              failed=(), **extra):
    as_list = (lambda v: list(v) if isinstance(v, tuple) else v)
    return json.dumps({"v": 1, "ts": ts, "event": event, "repo": repo,
                       "checks": as_list(checks), "failed": as_list(failed), **extra})


class HookTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-hooks-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def log(self, *lines, name="runs.jsonl"):
        path = self.tmp / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def rows(self, *paths, events=None):
        self.hooks = census.hook_tally(paths, NOW, 8, PUBLIC, events, "example-host")
        tally = {"invocations": {}, "sessions": {}, "sessions_scanned": 0}
        self.doc = census.build_weekly_document(tally, NOW, 8, self.hooks)
        return [(r["week"], r["name"], r["repo"], r["invocations"], r["blocked"])
                for r in self.doc["rows"]]

    def test_runs_and_failures_per_check_with_an_all_row(self):
        rows = self.rows(self.log(
            hook_line(),
            hook_line(failed=["lint-b"]),
            hook_line(ts="2026-09-03T11:00:00Z", event="pre-push",
                      repo="example-adopter/skills-evals", checks=["lint-a"])))
        self.assertEqual(rows, [
            ("2026-08-31", "all", None, 3, 1),
            ("2026-08-31", "lint-a", "example-adopter/skills-evals", 1, 0),
            ("2026-08-31", "lint-a", "example-owner/public-widgets", 2, 0),
            ("2026-08-31", "lint-b", "example-owner/public-widgets", 2, 1)])
        for row in self.doc["rows"]:
            self.assertEqual(row["kind"], "hook")
            self.assertIsNone(row["sessions"])
            self.assertIsNone(row["overridden"])
        self.assertEqual(self.doc["sources"][1]["scope"], "hooks")
        self.assertEqual(self.doc["sources"][1]["runs_scanned"], 3)

    def test_unlisted_ids_and_repos_become_other_and_null_repo_stays_null(self):
        rows = self.rows(self.log(
            hook_line(repo="example-owner/private-thing",
                      checks=["Bad_ID", "x" * 42, "all"], failed=["Bad_ID"]),
            hook_line(repo=None, checks=["lint-a"])))
        self.assertEqual(rows, [
            ("2026-08-31", "all", None, 2, 1),
            ("2026-08-31", "lint-a", None, 1, 0),
            ("2026-08-31", "other", "other", 1, 1)])
        self.assertNotIn("private-thing", json.dumps(self.doc))

    def test_runs_are_bucketed_by_week_inside_the_window_only(self):
        rows = self.rows(self.log(
            hook_line(ts="2026-08-30T23:59:59Z"),          # Sunday of W35
            hook_line(ts="2026-08-31T00:00:00Z"),          # Monday of W36
            hook_line(ts="2026-07-01T00:00:00Z"),          # before the window
            hook_line(ts="2026-09-08T00:00:00Z")))         # after now
        self.assertEqual([(w, n, i) for w, n, _, i, _ in rows if n == "all"],
                         [("2026-08-24", "all", 1), ("2026-08-31", "all", 1)])

    def test_hostile_lines_are_skipped(self):
        hostile = ["{torn", "[]", "null", json.dumps({"v": 1}),
                   hook_line(v=2), hook_line(v=True), hook_line(event="post-merge"),
                   hook_line(ts="yesterday"), hook_line(ts=7),
                   hook_line(checks="lint-a"), hook_line(checks=[7]),
                   hook_line(checks=[""]), hook_line(failed=None),
                   hook_line(checks=["c"] * 201), hook_line(repo=7),
                   hook_line(repo="r" * 201), "\u0000" * 10]
        rows = self.rows(self.log(*hostile, hook_line()))
        self.assertEqual(rows[0], ("2026-08-31", "all", None, 1, 0))

    def test_missing_logs_mean_unknown_and_duplicates_count_once(self):
        self.assertIsNone(census.hook_tally([str(self.tmp / "absent")], NOW, 8, PUBLIC))
        tally = {"invocations": {}, "sessions": {}, "sessions_scanned": 0}
        self.assertEqual(len(census.build_weekly_document(tally, NOW, 8, None)["sources"]),
                         1)
        events: list = []
        line = hook_line(failed=["lint-a"])
        rows = self.rows(self.log(line), self.log(line, name="copy.jsonl"),
                         str(self.tmp / "absent"), events=events)
        self.assertEqual(rows[0], ("2026-08-31", "all", None, 1, 1))
        self.assertEqual(events, [{
            "v": 1, "id": hashlib.sha256(f"example-host\n{line}".encode()).hexdigest(),
            "ts": "2026-09-03T10:00:00.000Z", "kind": "hook", "event": "pre-commit",
            "repo": "example-owner/public-widgets", "checks": ["lint-a", "lint-b"],
            "failed": ["lint-a"], "surface": "local", "root": "wsl",
            "machine": "example-host"}])

    def test_public_repos_are_the_non_private_registries_plus_two(self):
        repos = census.public_repos()
        self.assertIn("example-adopter/acsi-agentskills", repos)
        self.assertIn("example-adopter/_agent-guidance", repos)
        self.assertIn("example-adopter/skills-evals", repos)
        self.assertNotIn("example-adopter/acsi-agentskills-private", repos)

    def test_the_cli_reads_hook_logs_and_skill_weight_ignores_hook_rows(self):
        projects = self.tmp / "projects"
        write_transcript(projects / "p" / "s.jsonl", [skill_call("workflow-path-audit")])
        log = self.log(hook_line(repo="example-adopter/skills-evals",
                                 checks=["workflow-path-audit"]))
        weekly, events = self.tmp / "weekly.json", self.tmp / "events.jsonl"
        argv = ["model_usage_census.py", "--projects", str(projects),
                "--out", str(self.tmp / "latest.json"), "--weekly-out", str(weekly),
                "--events-out", str(events), "--now", NOW_TEXT,
                "--hook-logs", os.pathsep.join([str(self.tmp / "absent"), log]),
                "--skill-allowlist-root", str(test_issue_skill_usage.example_evals.checkout())]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            self.assertEqual(census.main(), 0)
        doc = census.skill_usage.load(weekly)
        self.assertEqual(sorted((r["kind"], r["name"]) for r in doc["rows"]),
                         [("hook", "all"), ("hook", "workflow-path-audit"),
                          ("skill", "workflow-path-audit")])
        self.assertEqual(census.skill_usage.weight(doc, "workflow-path-audit", NOW), 1)
        kinds = [json.loads(line)["kind"]
                 for line in events.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted(kinds), ["hook", "skill"])

    def test_the_default_log_follows_agent_checks(self):
        with mock.patch.dict(os.environ, {"AGENT_CHECKS_LOG": "/x/runs.jsonl"}):
            self.assertEqual(census.default_hook_log(), "/x/runs.jsonl")
        with mock.patch.dict(os.environ, {"AGENT_CHECKS_LOG": "", "XDG_STATE_HOME": "/s"}):
            self.assertEqual(census.default_hook_log(), "/s/agent-checks/runs.jsonl")


class MergeTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-merge-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = self.tmp / "store"
        self.store.mkdir()

    def write(self, *records):
        path = self.tmp / "events.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def record(self, event_id, ts, machine="example-host"):
        return {"v": 1, "id": event_id, "ts": ts, "kind": "skill", "machine": machine}

    def test_records_merge_by_id_into_month_and_machine_files_sorted_by_ts(self):
        target = self.store / "events" / "2026-09" / "example-host.jsonl"
        target.parent.mkdir(parents=True)
        foreign = '{"id": "kept", "ts": "2026-09-02T00:00:00.000Z", "note": "by hand"}'
        target.write_text(foreign + "\nnot json\n", encoding="utf-8")
        events = self.write(self.record("b", "2026-09-03T00:00:00.000Z"),
                            self.record("kept", "2026-09-09T00:00:00.000Z"),
                            self.record("a", "2026-09-01T00:00:00.000Z"),
                            self.record("c", "2026-08-31T23:00:00.000Z"),
                            self.record("d", "2026-09-01T00:00:00.000Z", "other-host"))
        self.assertEqual(census.merge_events(events, self.store), 4)
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "not json")          # no ts sorts first, kept
        self.assertEqual([json.loads(line)["id"] for line in lines[1:]], ["a", "kept", "b"])
        self.assertIn(foreign, lines)
        self.assertTrue((self.store / "events" / "2026-08" / "example-host.jsonl").is_file())
        self.assertTrue((self.store / "events" / "2026-09" / "other-host.jsonl").is_file())
        before = target.read_bytes()
        self.assertEqual(census.merge_events(events, self.store), 0)
        self.assertEqual(target.read_bytes(), before)

    def test_malformed_records_and_escaping_paths_are_refused(self):
        for bad in (self.record("a", "2026-09-01T00:00:00Z", "../x"),
                    self.record("a", "yesterday"), {"id": 7, "ts": "2026-09-01",
                                                    "machine": "h"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                census.merge_events(self.write(bad), self.store)
        (self.store / "events").mkdir()
        (self.tmp / "lessons").mkdir()
        (self.store / "events" / "2026-09").symlink_to(self.tmp / "lessons")
        with self.assertRaises(ValueError):
            census.merge_events(self.write(self.record("a", "2026-09-01T00:00:00Z")),
                                self.store)
        self.assertEqual(list((self.tmp / "lessons").iterdir()), [])


class PrivatePublishTests(unittest.TestCase):
    """The private half of scripts/publish_usage_census.sh, through the same
    seams as test_issue_skill_usage's PublisherTests (whose setUp, seed and
    helpers this borrows), plus USAGE_EVENTS_URL: a local bare repository."""

    # Reached through its module, never bound by name here: a TestCase class
    # in this module's namespace is collected a second time, and
    # test/run_tests.py's suite-coverage check rejects the extra copies.
    _base = test_issue_skill_usage.PublisherTests.__dict__
    SCRIPT, BRANCH = _base["SCRIPT"], _base["BRANCH"]
    EVENTS = "persistent/usage-events"
    setUp, git, seed, run_script, head = (
        _base["setUp"], _base["git"], _base["seed"],
        _base["run_script"], _base["head"])
    del _base

    def events_url(self):
        return self.env["USAGE_EVENTS_URL"]

    def events_origin(self):
        return self.tmp / "events.git"

    def events_head(self):
        return subprocess.run(["git", "rev-parse", "--verify", "-q", self.EVENTS],
                              cwd=self.events_origin(), capture_output=True,
                              text=True).stdout.strip()

    def tree(self):
        return self.git("ls-tree", "-r", "--name-only", self.EVENTS,
                        cwd=self.events_origin()).splitlines()

    def records(self):
        path = f"events/2026-09/{census.machine_name()}.jsonl"
        text = self.git("show", f"{self.EVENTS}:{path}", cwd=self.events_origin())
        return [json.loads(line) for line in text.splitlines()]

    def test_a_missing_branch_is_created_with_a_readme_and_the_records(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("published; private events: 2 new of 2, branch created", proc.stdout)
        self.assertEqual(sorted(self.tree()),
                         ["README.md", f"events/2026-09/{census.machine_name()}.jsonl"])
        readme = self.git("show", f"{self.EVENTS}:README.md", cwd=self.events_origin())
        for phrase in ("Private", "last 8 weeks", "No ruleset"):
            self.assertIn(phrase, readme)
        self.assertEqual([r["id"] for r in self.records()], ["toolu_01", "toolu_02"])
        self.assertEqual(self.git("log", "-1", "--format=%s", self.EVENTS,
                                  cwd=self.events_origin()), "usage events [skip ci]")
        printed = proc.stdout + proc.stderr
        for forbidden in ("toolu_01", "workflow-path-audit", "123456789012",
                          census.machine_name(), str(self.tmp)):
            self.assertNotIn(forbidden, printed)

    def test_a_second_run_is_idempotent_and_a_third_adds_only_new_ids(self):
        self.assertEqual(self.run_script().returncode, 0)
        first = self.events_head()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("private events: 0 new of 2", proc.stdout)
        self.assertEqual(self.events_head(), first)
        write_transcript(self.projects / "-home-example-proj" / "t.jsonl",
                         [skill_call("finding-unknowns", "toolu_09",
                                     when="2026-09-02T10:00:00Z")])
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("private events: 1 new of 3", proc.stdout)
        self.assertEqual([r["id"] for r in self.records()],
                         ["toolu_09", "toolu_01", "toolu_02"])
        self.assertEqual(self.git("rev-list", "--count", self.EVENTS,
                                  cwd=self.events_origin()), "2")

    def test_a_dry_run_pushes_no_events(self):
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 new of 2, dry run", proc.stdout)
        self.assertEqual(self.events_head(), "")

    def test_a_private_failure_does_not_block_the_public_publish(self):
        before = self.head()
        self.env["USAGE_EVENTS_URL"] = (self.tmp / "no-such-repo.git").as_uri()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotEqual(self.head(), before)
        self.assertIn("; published; private events: not published", proc.stdout)
        self.assertEqual(proc.stderr.strip(),
                         "usage census: private events not published (reach)")

    def seed_events_branch(self, link=False):
        seed = self.tmp / "events-seed"
        self.git("init", "--quiet", "-b", self.EVENTS, str(seed))
        (seed / "lessons").mkdir()
        (seed / "lessons" / "note.md").write_text("invented\n", encoding="utf-8")
        if link:
            (seed / "events").mkdir()
            (seed / "events" / "2026-09").symlink_to("../lessons")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "--quiet", "-m", "seed", cwd=seed)
        self.git("push", "--quiet", self.events_url(), self.EVENTS, cwd=seed)

    def assert_refused(self, proc, step, before_events, before_public):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr.strip(),
                         f"usage census: private events not published ({step})")
        self.assertEqual(self.events_head(), before_events)
        self.assertNotEqual(self.head(), before_public)

    def test_an_existing_branch_is_kept_and_lessons_is_never_touched(self):
        self.seed_events_branch()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("private events: 2 new of 2", proc.stdout)
        self.assertNotIn("branch created", proc.stdout)
        self.assertEqual(sorted(self.tree()),
                         [f"events/2026-09/{census.machine_name()}.jsonl",
                          "lessons/note.md"])

    def test_a_linked_events_directory_is_refused(self):
        self.seed_events_branch(link=True)
        before = self.events_head(), self.head()
        self.assert_refused(self.run_script(), "merge", *before)

    def test_a_staged_path_outside_events_is_refused(self):
        """The guard after the merge: a stand-in interpreter that, for the
        merge step only, also writes under lessons/."""
        self.seed_events_branch()
        wrapper = self.tmp / "python-wrapper"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "${1:-}" = "-" ] && [ -d "${4:-}/.git" ]; then\n'
            '  echo invented > "$4/lessons/extra.md"\n'
            "fi\n"
            f'exec {sys.executable} "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        self.env["PYTHON"] = str(wrapper)
        before = self.events_head(), self.head()
        self.assert_refused(self.run_script(), "unexpected staged paths", *before)

    def test_wsl_adds_windows_and_cowork_roots_and_windows_hook_logs(self):
        users = self.tmp / "users"
        home = Path(self.env["HOME"])
        entries = [skill_call("workflow-path-audit", "toolu_a", model="claude-opus-5",
                              message_id="m_a")]
        write_transcript(home / ".claude" / "projects" / "p" / "a.jsonl", entries)
        write_transcript(users / "example" / ".claude" / "projects" / "p" / "a.jsonl",
                         entries + [skill_call("finding-unknowns", "toolu_b")])
        cowork = (users / "example" / "AppData" / "Local" / "Claude-Data" / "claude_x" /
                  "local-agent-mode-sessions" / "s1" / "deep" / ".claude" / "projects")
        for copy in ("one", "two"):
            write_transcript(cowork / copy / "c.jsonl",
                             [skill_call("workflow-path-audit", "toolu_c")])
        log = users / "example" / ".local" / "state" / "agent-checks" / "runs.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(hook_line() + "\n", encoding="utf-8")
        env = {k: v for k, v in self.env.items() if k != "USAGE_HOOK_LOGS"}
        env.update(WSL_DISTRO_NAME="Example", WSL_USERS_DIR=str(users))
        proc = subprocess.run(["bash", str(self.SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 models, 1 assistant turns, 3 skill invocations, 1 hook runs",
                      proc.stdout)
        # One record per id, whichever root saw it first. The stand-in Windows
        # root is not under /mnt/<letter>, so it reads as `wsl` here;
        # MultiRootTests.test_root_kinds covers that rule.
        roots = {r["id"]: r["root"] for r in self.records()}
        self.assertEqual(len(self.records()), 4)
        self.assertEqual({k: v for k, v in roots.items() if k.startswith("toolu")},
                         {"toolu_a": "wsl", "toolu_b": "wsl", "toolu_c": "cowork"})
        hooks = [r for r in self.records() if r["kind"] == "hook"]
        self.assertEqual([r["root"] for r in hooks], ["wsl"])


if __name__ == "__main__":
    unittest.main()
