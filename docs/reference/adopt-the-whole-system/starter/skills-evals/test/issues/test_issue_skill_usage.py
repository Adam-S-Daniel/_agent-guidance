#!/usr/bin/env python3
"""Skill usage census: `scripts/model_usage_census.py --weekly-out`,
`scripts/publish_usage_census.sh`'s second file (`usage/weekly-local.json`),
and `harness/skill_usage.py`, which owns that file's schema.

The output is published on a public branch, so most of these tests are about
what may NOT become a key. Every transcript is synthetic and written under a
mkdtemp with an explicit mtime; the publisher runs against a local bare
repository over `file://`; `now` is always passed in. No network, no clock.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import model_usage_census as census  # noqa: E402
import skill_usage  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "test"))
import example_evals  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)   # ISO week 2026-W36
NOW_TEXT = "2026-09-04T12:00:00Z"
ALLOW = frozenset({"workflow-path-audit", "finding-unknowns"})
REGISTRY_URL = "https://github.com/example-adopter/acsi-agentskills"
SECRET_PATH = "/home/example/repos/private-client-work"
ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abcd1234"

#: Values a transcript could carry where a skill name is expected. None may
#: ever be a published key.
HOSTILE = (
    "../../home/example/.ssh/id_rsa",
    SECRET_PATH,
    "workflow-path-audit\n",
    "workflow-path-audit\nfinding-unknowns",
    ARN,
    "x" * 300,
    "Workflow-Path-Audit",
    "finding unknowns",
    "",
    "/",
)


def write_transcript(path: Path, entries: list, mtime: datetime = NOW) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                    encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def skill_call(skill, call_id="toolu_01", when="2026-09-03T10:00:00Z",
               model=None, message_id=None):
    message = {"role": "assistant",
               "content": [{"type": "tool_use", "id": call_id, "name": "Skill",
                            "input": {"skill": skill}}]}
    if model is not None:
        message["model"] = model
    if message_id is not None:
        message["id"] = message_id
    return {"type": "assistant", "timestamp": when, "message": message}


def typed(text, when="2026-09-03T10:00:00Z", as_items=False):
    content = [{"type": "text", "text": text}] if as_items else text
    return {"type": "user", "timestamp": when, "cwd": SECRET_PATH,
            "message": {"role": "user", "content": content}}


class CountingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-usage-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()

    def skills(self, *transcripts, allowlist=ALLOW, weeks=8):
        for index, entries in enumerate(transcripts):
            write_transcript(self.projects / "-home-example-proj" / f"s{index}.jsonl",
                             entries)
        self.tally = census.census_all(self.projects, NOW, weeks, allowlist)[1]
        return self.tally["invocations"]

    def test_a_skill_tool_call_is_counted_in_its_week(self):
        self.assertEqual(self.skills([skill_call("workflow-path-audit")]),
                         {"workflow-path-audit": {"2026-W36": 1}})

    def test_one_tool_use_id_counts_once_per_transcript(self):
        entries = [skill_call("workflow-path-audit", "toolu_01"),
                   skill_call("workflow-path-audit", "toolu_01"),
                   skill_call("workflow-path-audit", "toolu_02")]
        self.assertEqual(self.skills(entries),
                         {"workflow-path-audit": {"2026-W36": 2}})

    def test_the_same_id_in_two_transcripts_counts_in_each(self):
        entries = [skill_call("finding-unknowns", "toolu_01")]
        self.assertEqual(self.skills(entries, entries),
                         {"finding-unknowns": {"2026-W36": 2}})
        self.assertEqual(self.tally["sessions"], {"finding-unknowns": {"2026-W36": 2}})

    def test_sessions_count_distinct_transcripts_not_calls(self):
        busy = [skill_call("workflow-path-audit", f"toolu_{i}") for i in range(3)]
        idle = [typed("no command here")]
        self.assertEqual(self.skills(busy, idle),
                         {"workflow-path-audit": {"2026-W36": 3}})
        self.assertEqual(self.tally["sessions"], {"workflow-path-audit": {"2026-W36": 1}})
        self.assertEqual(self.tally["sessions_scanned"], 2)

    def test_a_typed_slash_command_for_a_skill_is_counted(self):
        text = ("<command-message>finding-unknowns</command-message>\n"
                "<command-name>/finding-unknowns</command-name>")
        self.assertEqual(self.skills([typed(text), typed(text, as_items=True)]),
                         {"finding-unknowns": {"2026-W36": 2}})

    def test_a_built_in_command_is_not_counted_at_all(self):
        self.assertEqual(self.skills([typed("<command-name>/clear</command-name>")]),
                         {})

    def test_a_tag_inside_a_tool_result_is_not_a_typed_command(self):
        entry = {"type": "user", "timestamp": "2026-09-03T10:00:00Z",
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "toolu_01",
                      "content": "<command-name>/finding-unknowns</command-name>"}]}}
        self.assertEqual(self.skills([entry]), {})

    def test_a_plugin_prefix_is_stripped(self):
        entries = [skill_call("acsi-coding-anywhere:workflow-path-audit"),
                   typed("<command-name>/acsi-anything-anywhere:finding-unknowns"
                         "</command-name>")]
        self.assertEqual(self.skills(entries),
                         {"workflow-path-audit": {"2026-W36": 1},
                          "finding-unknowns": {"2026-W36": 1}})

    def test_a_skill_outside_the_allowlist_counts_as_other(self):
        self.assertEqual(self.skills([skill_call("zz-unlisted-skill")]),
                         {"other": {"2026-W36": 1}})

    def test_hostile_names_never_become_keys(self):
        entries = [skill_call(value, f"toolu_{i}") for i, value in enumerate(HOSTILE)]
        entries += [skill_call({"skill": "workflow-path-audit"}, "toolu_dict"),
                    skill_call(7, "toolu_int")]
        entries += [typed(f"<command-name>/{value}</command-name>") for value in HOSTILE]
        counts = self.skills(entries)
        self.assertEqual(counts, {"other": {"2026-W36": len(HOSTILE) + 2}})
        blob = json.dumps(census.build_weekly_document(self.tally, NOW))
        for leaked in ("123456789012", "private-client-work", "id_rsa", "xxxxxxxx"):
            self.assertNotIn(leaked, blob)

    def test_an_allowlisted_name_still_has_to_look_like_a_skill(self):
        hostile_allowlist = frozenset(HOSTILE) | {"a" * 65}
        for value in sorted(hostile_allowlist):
            with self.subTest(value=value[:40]):
                self.assertEqual(census.skill_key(value, hostile_allowlist), "other")

    def test_only_weeks_inside_the_window_are_counted(self):
        entries = [skill_call("workflow-path-audit", "toolu_old",
                              when="2026-07-01T10:00:00Z"),       # W27: outside 8 weeks
                   skill_call("workflow-path-audit", "toolu_in",
                              when="2026-07-14T10:00:00Z"),       # W29: oldest week in
                   skill_call("workflow-path-audit", "toolu_future",
                              when="2026-09-08T10:00:00Z")]       # W37: after now
        self.assertEqual(self.skills(entries),
                         {"workflow-path-audit": {"2026-W29": 1}})

    def test_a_transcript_last_written_before_the_window_is_not_read(self):
        write_transcript(self.projects / "p" / "old.jsonl",
                         [skill_call("workflow-path-audit")],
                         mtime=NOW - timedelta(weeks=20))
        tally = census.census_all(self.projects, NOW, 8, ALLOW)[1]
        self.assertEqual(tally["invocations"], {})
        self.assertEqual(tally["sessions_scanned"], 0)

    def test_model_counts_are_unchanged_by_skill_counting(self):
        entries = [skill_call("workflow-path-audit", "toolu_01",
                              model="claude-opus-5", message_id="msg_01"),
                   skill_call("finding-unknowns", "toolu_02",
                              model="claude-opus-5", message_id="msg_01")]
        write_transcript(self.projects / "p" / "s.jsonl", entries)
        models, skills = census.census_all(self.projects, NOW, 8, ALLOW)
        self.assertEqual(models, census.census_counts(self.projects, NOW, 8))
        self.assertEqual(models, {"claude-opus-5": {"2026-W36": 1}})
        self.assertEqual(sorted(skills["invocations"]),
                         ["finding-unknowns", "workflow-path-audit"])

    def test_no_allowlist_counts_no_skills(self):
        self.assertEqual(self.skills([skill_call("workflow-path-audit")],
                                     allowlist=None), {})

    def test_the_weekly_document_has_the_published_shape(self):
        tally = {"invocations": {"workflow-path-audit": {"2026-W36": 3, "2026-W30": 1},
                                 "other": {"2026-W36": 2}},
                 "sessions": {"workflow-path-audit": {"2026-W36": 2, "2026-W30": 1},
                              "other": {"2026-W36": 1}},
                 "sessions_scanned": 5}
        doc = census.build_weekly_document(tally, NOW)
        self.assertEqual(sorted(doc), ["generated_at", "rows", "schema", "sources"])
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["generated_at"], NOW_TEXT)
        self.assertEqual(doc["sources"], [{
            "surface": "local", "scope": "transcripts",
            "from": "2026-07-13T00:00:00Z",          # Monday of 2026-W29
            "to": NOW_TEXT, "sessions_scanned": 5}])
        empty = {"repo": None, "surface": "local", "blocked": None,
                 "overridden": None, "outcome": None, "kind": "skill"}
        self.assertEqual(doc["rows"], [
            {**empty, "week": "2026-07-20", "name": "workflow-path-audit",
             "invocations": 1, "sessions": 1},
            {**empty, "week": "2026-08-31", "name": "other",
             "invocations": 2, "sessions": 1},
            {**empty, "week": "2026-08-31", "name": "workflow-path-audit",
             "invocations": 3, "sessions": 2}])
        for entry in doc["rows"]:
            self.assertEqual(tuple(sorted(entry)), tuple(sorted(skill_usage.ROW_FIELDS)))


def make_allowlist_root(root: Path) -> Path:
    """A synthetic repo root: two skill fixtures (one a draft), one subject
    fixture, and a non-coverage file."""
    evals = root / "evals"
    for name, extra in (("workflow-path-audit", ""), ("zz-draft-only", "draft: true\n")):
        (evals / name).mkdir(parents=True)
        (evals / name / "fixture.yaml").write_text(
            f"skill: {name}\nregistry: {REGISTRY_URL}\n{extra}", encoding="utf-8")
    (evals / "guidance").mkdir()
    (evals / "guidance" / "fixture.yaml").write_text("subject: guidance\n",
                                                     encoding="utf-8")
    (evals / "non-coverage.yml").write_text(
        "skips:\n"
        "  - registry: acsi-agentskills\n"
        "    skill: zz-skipped-skill\n"
        "    decision: defer\n"
        "    reason: invented for a test\n", encoding="utf-8")
    return root


class AllowlistTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-allow-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_fixtures_and_non_coverage_rows_make_the_allowlist(self):
        root = make_allowlist_root(self.tmp / "repo")
        self.assertEqual(census.skill_allowlist(root),
                         {"workflow-path-audit", "zz-skipped-skill"})

    def test_the_committed_evals_tree_loads(self):
        allow = census.skill_allowlist(example_evals.checkout())
        self.assertIn("workflow-path-audit", allow)
        self.assertTrue(all(census.SKILL_KEY_RE.fullmatch(name) for name in allow))

    def test_an_unreadable_allowlist_fails_closed(self):
        projects = self.tmp / "projects"
        write_transcript(projects / "p" / "s.jsonl", [skill_call("workflow-path-audit")])
        out, weekly_out = self.tmp / "latest.json", self.tmp / "weekly-local.json"
        argv = ["model_usage_census.py", "--projects", str(projects), "--out", str(out),
                "--weekly-out", str(weekly_out), "--now", NOW_TEXT,
                "--skill-allowlist-root", str(self.tmp / "no-repo-here")]
        err = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stderr", err):
            self.assertEqual(census.main(), 2)
        self.assertFalse(out.exists())
        self.assertFalse(weekly_out.exists())
        self.assertNotIn(str(self.tmp), err.getvalue())


class CliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects = self.tmp / "projects"
        write_transcript(self.projects / "-home-example-proj" / "s.jsonl", [
            skill_call("workflow-path-audit", model="claude-opus-5", message_id="m1"),
            skill_call("zz-unlisted-skill", "toolu_02", model="claude-opus-5",
                       message_id="m2"),
            typed("<command-name>/finding-unknowns</command-name>"),
        ])

    def run_main(self, *extra):
        out = self.tmp / f"latest{len(extra)}.json"
        argv = ["model_usage_census.py", "--projects", str(self.projects),
                "--out", str(out), "--now", NOW_TEXT, *extra]
        printed = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(printed):
            self.assertEqual(census.main(), 0)
        return out.read_bytes(), printed.getvalue()

    def test_without_weekly_out_the_model_output_is_byte_identical(self):
        plain, plain_status = self.run_main()
        weekly_out = self.tmp / "weekly-local.json"
        with_skills, status = self.run_main("--weekly-out", str(weekly_out),
                                            "--hook-logs", "",
                                            "--skill-allowlist-root",
                                            str(example_evals.checkout()))
        self.assertEqual(plain, with_skills)
        self.assertNotIn("invocations", plain_status)
        self.assertIn("3 skill invocations", status)
        doc = json.loads(weekly_out.read_text(encoding="utf-8"))
        self.assertEqual([(r["name"], r["invocations"], r["sessions"]) for r in doc["rows"]],
                         [("finding-unknowns", 1, 1), ("other", 1, 1),
                          ("workflow-path-audit", 1, 1)])
        self.assertEqual(skill_usage.load(weekly_out), doc)
        for leaked in ("zz-unlisted-skill", "private-client-work", "s.jsonl"):
            self.assertNotIn(leaked, weekly_out.read_text(encoding="utf-8"))
            self.assertNotIn(leaked, status)


class PublisherTests(unittest.TestCase):
    """scripts/publish_usage_census.sh through the same seams
    test/run_tests.py's TestPublishUsageCensus uses: a bare origin over
    file://, SKILLS_EVALS_URL, CENSUS_PROJECTS and CENSUS_NOW."""

    SCRIPT = REPO_ROOT / "scripts" / "publish_usage_census.sh"
    BRANCH = "persistent/eval-results"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-publish-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "home").mkdir()
        self.origin = self.tmp / "origin.git"
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp / "home"),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0", "SKILLS_EVALS_URL": self.origin.as_uri(),
                    "CENSUS_NOW": NOW_TEXT, "USAGE_HOOK_LOGS": "",
                    "USAGE_EVENTS_URL": (self.tmp / "events.git").as_uri()}
        self.git("init", "--bare", "--quiet", str(self.tmp / "events.git"))
        self.git("config", "maintenance.auto", "false", cwd=self.tmp / "events.git")
        self.seed()
        self.projects = self.tmp / "projects"
        write_transcript(self.projects / "-home-example-proj" / "s.jsonl", [
            skill_call("workflow-path-audit", model="claude-opus-5", message_id="m1"),
            skill_call(ARN, "toolu_02", model="claude-opus-5", message_id="m2")])

    def git(self, *args, cwd=None):
        env = dict(self.env, GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                   GIT_AUTHOR_EMAIL="fixture@example.com",
                   GIT_COMMITTER_EMAIL="fixture@example.com")
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def seed(self):
        self.git("init", "--bare", "--quiet", "-b", "main", str(self.origin))
        self.git("config", "maintenance.auto", "false", cwd=self.origin)
        seed = make_allowlist_root(self.tmp / "seed")
        self.git("init", "--quiet", "-b", "main", cwd=seed)
        (seed / "scripts").mkdir()
        for script in ("model_usage_census.py", "eval_coverage.py"):
            shutil.copy(REPO_ROOT / "scripts" / script, seed / "scripts")
        shutil.copytree(REPO_ROOT / "harness", seed / "harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(REPO_ROOT / "evals" / "roster-policy.yml", seed / "evals")
        self.git("add", ".", cwd=seed)
        self.git("commit", "--quiet", "-m", "seed", cwd=seed)
        self.git("push", "--quiet", self.origin.as_uri(), "main", cwd=seed)
        self.git("checkout", "--quiet", "--orphan", self.BRANCH, cwd=seed)
        self.git("rm", "-rfq", ".", cwd=seed)
        (seed / "roster.txt").write_text("unrelated\n", encoding="utf-8")
        self.git("add", "roster.txt", cwd=seed)
        self.git("commit", "--quiet", "-m", "results seed", cwd=seed)
        self.git("push", "--quiet", self.origin.as_uri(), self.BRANCH, cwd=seed)

    def run_script(self, *args):
        return subprocess.run(["bash", str(self.SCRIPT), *args],
                              env=dict(self.env, CENSUS_PROJECTS=str(self.projects)),
                              capture_output=True, text=True, timeout=120)

    def head(self):
        return self.git("rev-parse", self.BRANCH, cwd=self.origin)

    def test_a_dry_run_pushes_nothing(self):
        before = self.head()
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 skill invocations", proc.stdout)
        self.assertIn("dry run", proc.stdout)
        self.assertEqual(self.head(), before)

    def test_a_run_commits_exactly_the_two_files(self):
        before = self.head()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = self.head()
        self.assertEqual(self.git("diff", "--name-only", before, after, cwd=self.origin),
                         "usage/latest.json\nusage/weekly-local.json")
        doc = json.loads(self.git("show", f"{self.BRANCH}:usage/weekly-local.json",
                                  cwd=self.origin))
        self.assertEqual([(r["name"], r["invocations"]) for r in doc["rows"]],
                         [("other", 1), ("workflow-path-audit", 1)])
        printed = proc.stdout + proc.stderr
        for forbidden in ("workflow-path-audit", "123456789012", str(self.tmp)):
            self.assertNotIn(forbidden, printed)

    def test_a_change_to_skills_alone_is_published(self):
        """Same clock and the same model counts: latest.json's bytes do not
        move, so only weekly-local.json is staged, and the guard must allow it."""
        self.assertEqual(self.run_script().returncode, 0)
        first = self.head()
        write_transcript(self.projects / "-home-example-proj" / "t.jsonl",
                         [skill_call("workflow-path-audit", "toolu_09")])
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.git("diff", "--name-only", first, self.head(),
                                  cwd=self.origin), "usage/weekly-local.json")
        proc = self.run_script()
        self.assertIn("unchanged", proc.stdout)


def published_row(week, name, invocations, kind="skill", surface="local", repo=None):
    return {"week": week, "kind": kind, "name": name, "repo": repo,
            "surface": surface, "invocations": invocations, "sessions": 1,
            "blocked": None, "overridden": None, "outcome": None}


class SkillUsageReaderTests(unittest.TestCase):

    # NOW is in 2026-W36 (Monday 2026-08-31); four weeks back reach W33
    # (Monday 2026-08-10). W32 (2026-08-03) is outside.
    DOC = {"generated_at": "2026-09-04T06:00:00Z", "schema": 1,
           "sources": [{"surface": "local", "scope": "transcripts",
                        "from": "2026-07-13T00:00:00Z", "to": "2026-09-04T06:00:00Z",
                        "sessions_scanned": 9}],
           "rows": [published_row("2026-08-03", "workflow-path-audit", 100),
                    published_row("2026-08-10", "workflow-path-audit", 3),
                    published_row("2026-08-31", "workflow-path-audit", 2),
                    published_row("2026-08-31", "workflow-path-audit", 4,
                                  surface="cloud", repo="example/repo"),
                    published_row("2026-08-31", "workflow-path-audit", 50, kind="hook")]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-reader-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, value, text=None):
        path = self.tmp / "weekly-local.json"
        path.write_text(text if text is not None else json.dumps(value),
                        encoding="utf-8")
        return path

    def test_load_returns_a_well_formed_document(self):
        self.assertEqual(skill_usage.load(self.write(self.DOC)), self.DOC)

    def test_load_returns_none_for_missing_unparseable_or_misshapen(self):
        self.assertIsNone(skill_usage.load(self.tmp / "absent.json"))
        self.assertIsNone(skill_usage.load(self.write(None, text="{torn")))
        row = self.DOC["rows"][0]
        for bad in ([], {**self.DOC, "rows": {}}, {**self.DOC, "generated_at": "soon"},
                    {**self.DOC, "schema": 2}, {**self.DOC, "sources": "local"},
                    {**self.DOC, "rows": [{**row, "invocations": True}]},
                    {**self.DOC, "rows": [{**row, "invocations": -1}]},
                    {**self.DOC, "rows": [{**row, "name": None}]},
                    {**self.DOC, "rows": [3]}):
            with self.subTest(bad=bad):
                self.assertIsNone(skill_usage.load(self.write(bad)))

    def test_is_fresh_within_fourteen_days_only(self):
        made = datetime(2026, 9, 4, 6, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(skill_usage.is_fresh(self.DOC, made + timedelta(days=14)))
        self.assertFalse(skill_usage.is_fresh(self.DOC, made + timedelta(days=14, seconds=1)))
        self.assertFalse(skill_usage.is_fresh(self.DOC, made - timedelta(seconds=1)))
        self.assertFalse(skill_usage.is_fresh(None, made))

    def test_weight_sums_the_last_four_weeks(self):
        # W36..W33 across surfaces and repos (2 + 4 + 3); the hook row and
        # W32's 100 do not count.
        self.assertEqual(skill_usage.weight(self.DOC, "workflow-path-audit", NOW), 9)
        self.assertEqual(skill_usage.weight(self.DOC, "workflow-path-audit", NOW,
                                            weeks=5), 109)

    def test_weight_is_zero_for_a_skill_absent_from_a_fresh_census(self):
        self.assertEqual(skill_usage.weight(self.DOC, "finding-unknowns", NOW), 0)

    def test_weight_is_unknown_without_a_fresh_census(self):
        self.assertIsNone(skill_usage.weight(None, "workflow-path-audit", NOW))
        stale = NOW + timedelta(days=15)
        self.assertIsNone(skill_usage.weight(self.DOC, "workflow-path-audit", stale))
        self.assertIsNone(skill_usage.weight(self.DOC, "finding-unknowns", stale))


if __name__ == "__main__":
    unittest.main()
