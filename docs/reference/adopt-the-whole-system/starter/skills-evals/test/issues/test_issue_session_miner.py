#!/usr/bin/env python3
"""scripts/mine_sessions.py and scripts/run_session_miner.sh (ADR 0015).

Mining runs only on the owner's own computer; this suite never reads a real
transcript. Every transcript here is invented: fictional people, example.com
addresses, made-up session ids. Hermetic: temp dirs, a fake runner in place of
`gh` and `claude`, a fixed `--now`, local bare repos for the shell script.
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import mine_sessions as ms  # noqa: E402

NOW = "2026-10-20T12:00:00Z"
OLD = "2026-10-18T09:00:00Z"
HOME_CWD = "/home/fictional-dev/repos/example-repo"
DENY_TERM = "Northwind Merger"


def ts(minute: int) -> str:
    return f"2026-10-18T09:{minute:02d}:00Z"


def user(text, minute=0, **extra):
    return {"type": "user", "cwd": HOME_CWD, "entrypoint": "cli", "timestamp": ts(minute),
            "uuid": f"u{minute}", "message": {"role": "user", "content": text}, **extra}


def assistant(blocks, minute=0):
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return {"type": "assistant", "cwd": HOME_CWD, "timestamp": ts(minute), "uuid": f"a{minute}",
            "message": {"role": "assistant", "content": blocks}}


def tool_use(tid, name, minute, **inp):
    return assistant([{"type": "tool_use", "id": tid, "name": name, "input": inp}], minute)


def tool_result(tid, text, minute, error=False):
    return {"type": "user", "cwd": HOME_CWD, "timestamp": ts(minute), "uuid": f"r{minute}",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}


def write_session(root: Path, sid: str, entries, slug="-home-fictional-dev-repos-example-repo") -> Path:
    path = root / slug / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


SID = "11111111-2222-4333-8444-555555555555"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "projects"
        self.root.mkdir()
        self.lessons = self.tmp / "lessons"
        self.state = self.tmp / "state.json"
        self.deny = self.tmp / "denylist.txt"
        self.deny.write_text("# invented test denylist\nnorthwind\\s+merger\n", encoding="utf-8")
        self.redactor = ms.Redactor(ms.load_denylist(self.deny), ["fictional-dev"])
        self.manifest = self.tmp / "eval-coverage.yml"
        self.manifest.write_text(
            "- id: a-successful-git-push-does-not-mean-your-commit-exists\n"
            "  heading: A successful `git push` does not mean your commit exists\n"
            "- id: security\n  heading: Security\n", encoding="utf-8")
        self.registry = self.tmp / "registry"
        (self.registry / "plugins/p/skills/writing-adrs").mkdir(parents=True)
        (self.registry / "plugins/p/skills/writing-adrs/SKILL.md").write_text("x", encoding="utf-8")

    def ctx(self, **kw):
        return ms.Context(self.redactor, ms.load_guidance(self.manifest),
                          ms.load_registry_skills([self.registry]), **kw)

    def extract(self, entries, **kw):
        return ms.extract(entries, SID, "wsl", self.ctx(**kw))

    def signals(self, entries, **kw):
        return [r["signal"] for r in self.extract(entries, **kw)]

    def run_main(self, *argv, runner=None):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ms.main(list(argv), **({"runner": runner} if runner else {}))
        return code, out.getvalue()

    def mine_cli(self, *extra):
        return self.run_main("mine", "--root", f"wsl={self.root}", "--lessons-dir", str(self.lessons),
                             "--state", str(self.state), "--denylist", str(self.deny),
                             "--no-local-names", "--now", NOW, "--guidance-manifest", str(self.manifest),
                             "--registry", str(self.registry), *extra)

    def records(self):
        return ms._read_all_records(self.lessons)


class TestSignals(Base):
    def test_rule_correction_redirection(self):
        recs = self.extract([
            user("Set up the release workflow.", 0),
            assistant("Done.", 1),
            user("No, that's wrong: the tag goes on main.", 2),
            assistant("Fixed.", 3),
            user("Actually, focus on the changelog first.", 4),
            assistant("OK.", 5),
            user("From now on, always run the tests before you push.", 6),
            assistant("Will do.", 7),
        ])
        self.assertEqual([r["signal"] for r in recs], ["correction", "redirection", "rule"])

    def test_first_message_is_never_a_correction_but_can_state_a_rule(self):
        self.assertEqual(self.signals([user("No rush. Fix the typo.", 0), assistant("Done.", 1)]), [])
        self.assertEqual(self.signals([user("Never commit to main directly.", 0), assistant("OK.", 1)]),
                         ["rule"])

    def test_interrupted_takes_the_next_message_as_excerpt(self):
        recs = self.extract([
            user("Refactor the parser.", 0),
            tool_use("t1", "Bash", 1, command="rm -rf build"),
            user("[Request interrupted by user for tool use]", 2),
            user("Do not delete the build dir, it holds cached fixtures.", 3),
            assistant("Understood.", 4),
        ])
        inter = [r for r in recs if r["signal"] == "interrupted"]
        self.assertEqual(len(inter), 1)
        self.assertIn("cached fixtures", inter[0]["excerpt"])

    def test_repeated_tool_failure_needs_min_repeat_of_the_same_command_word(self):
        entries = [user("Run the suite.", 0)]
        for i in range(3):
            entries += [tool_use(f"t{i}", "Bash", 1 + 2 * i, command="pytest -q"),
                        tool_result(f"t{i}", "ModuleNotFoundError: yaml", 2 + 2 * i, error=True)]
        entries += [tool_use("t9", "Bash", 20, command="ls"), tool_result("t9", "x", 21, error=True),
                    assistant("Blocked.", 22)]
        recs = [r for r in self.extract(entries) if r["signal"] == "repeated-tool-failure"]
        self.assertEqual(len(recs), 1)
        self.assertIn("Bash:pytest failed 3x", recs[0]["excerpt"])
        self.assertEqual(self.signals(entries, min_repeat=4).count("repeated-tool-failure"), 0)

    def test_red_ci_after_push(self):
        entries = [user("Ship it.", 0),
                   tool_use("p", "Bash", 1, command="git push -u origin feature"),
                   tool_result("p", "ok", 2),
                   tool_use("c", "Bash", 3, command="gh pr checks 12"),
                   tool_result("c", "Some checks were not successful\n0 cancelled, 1 failing", 4, error=True),
                   assistant("CI is red; looking.", 5)]
        self.assertIn("red-ci-after-push", self.signals(entries))

    def test_green_ci_and_ci_without_push_are_not_red(self):
        green = [user("Ship it.", 0), tool_use("p", "Bash", 1, command="git push"),
                 tool_result("p", "ok", 2), tool_use("c", "Bash", 3, command="gh pr checks 12"),
                 tool_result("c", "All checks were successful\n0 cancelled, 0 failing, 8 successful", 4),
                 assistant("Green.", 5)]
        self.assertNotIn("red-ci-after-push", self.signals(green))
        no_push = [user("Check CI.", 0), tool_use("c", "Bash", 3, command="gh pr checks 12"),
                   tool_result("c", "1 failing", 4), assistant("Red.", 5)]
        self.assertNotIn("red-ci-after-push", self.signals(no_push))

    def test_abandoned_endings(self):
        cases = {
            "ended on an unanswered user message": [user("Go.", 0), assistant("Done.", 1), user("And the docs?", 2)],
            "ended on a tool call": [user("Go.", 0), tool_use("t", "Bash", 1, command="make")],
            "ended on a tool result": [user("Go.", 0), tool_use("t", "Bash", 1, command="make"),
                                       tool_result("t", "ok", 2)],
            "ended on a question to the user": [user("Go.", 0), assistant("Merge it now?", 1)],
        }
        for reason, entries in cases.items():
            recs = [r for r in self.extract(entries) if r["signal"] == "abandoned"]
            self.assertEqual([r["excerpt"] for r in recs], [reason], reason)
        self.assertNotIn("abandoned", self.signals([user("Go.", 0), assistant("All done.", 1)]))
        recs = ms.extract([user("Go.", 0), assistant("All done.", 1)], SID, "desktop", self.ctx(),
                          needs_action=True)
        self.assertEqual([r["signal"] for r in recs], ["abandoned"])

    def test_skill_and_guidance_usage_and_subject_attribution(self):
        recs = self.extract([
            user("Write an ADR for the cache choice.", 0),
            tool_use("s", "Skill", 1, skill="acsi-anything-anywhere:writing-adrs"),
            assistant("Per A successful `git push` does not mean your commit exists, I verified.", 2),
            user("No, the ADR needs a Consequences section.", 3),
            assistant("Added.", 4),
        ])
        by = {r["signal"]: r for r in recs}
        self.assertEqual(by["skill-invoked"]["subject"], {"kind": "skill", "name": "writing-adrs"})
        self.assertEqual(by["guidance-cited"]["subject"]["name"],
                         "a-successful-git-push-does-not-mean-your-commit-exists")
        self.assertEqual(by["correction"]["subject"], {"kind": "skill", "name": "writing-adrs"})

    def test_short_guidance_headings_do_not_match(self):
        recs = self.extract([user("Go.", 0), assistant("Security matters here.", 1)])
        self.assertNotIn("guidance-cited", [r["signal"] for r in recs])

    def test_memory_and_none_subjects(self):
        recs = self.extract([user("Go.", 0), assistant("Done.", 1),
                             user("Remember that I prefer short commit subjects.", 2),
                             assistant("Noted.", 3), user("No, wrong file.", 4), assistant("Fixed.", 5)])
        subj = {r["signal"]: r["subject"]["kind"] for r in recs}
        self.assertEqual(subj, {"rule": "memory", "correction": "none"})

    def test_record_shape_and_stable_ids(self):
        entries = [user("Go.", 0), assistant("Done.", 1), user("No, revert that.", 2), assistant("Ok.", 3)]
        a, b = self.extract(entries), self.extract(entries)
        self.assertEqual(a, b)
        self.assertEqual(set(a[0]), {"schema_version", "record_id", "session", "date", "source",
                                     "repo", "signal", "excerpt", "subject"})
        self.assertEqual(a[0]["repo"], "example-repo")
        self.assertEqual(a[0]["date"], "2026-10-18")
        self.assertEqual(a[0]["session"], ms.short_hash(SID))


class TestRedaction(Base):
    SECRET = ("No, mail jane.roe@example.com, token ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2" +
              " password=hunter2hunter2 from /home/fictional-dev/x and C:\\Users\\fictional-dev\\y "
              f"session {SID} local_0123abcd about the {DENY_TERM}.")

    def test_excerpt_scrubs_every_class(self):
        text = ms.excerpt(self.SECRET, self.redactor, limit=1000)
        for leaked in ("jane.roe", "ghp_", "hunter2", "fictional-dev", SID, "local_0123abcd",
                       "Northwind"):
            self.assertNotIn(leaked, text)
        for placeholder in ("<email>", "<token>", "/home/<user>", "<id>", "<redacted>"):
            self.assertIn(placeholder, text)
        self.assertTrue(self.redactor.clean(text))
        self.assertFalse(self.redactor.clean(self.SECRET))

    def test_excerpt_is_short(self):
        self.assertLessEqual(len(ms.excerpt("word " * 500, self.redactor)), ms.EXCERPT_CHARS)

    def test_mined_output_holds_no_secret_path_or_raw_id(self):
        write_session(self.root, SID, [user("Go.", 0), assistant("Done.", 1), user(self.SECRET, 2),
                                       assistant("Ok.", 3)])
        self.assertEqual(self.mine_cli()[0], 0)
        blob = "".join(p.read_text(encoding="utf-8") for p in self.lessons.rglob("*.jsonl"))
        self.assertIn("correction", blob)
        for leaked in ("jane.roe", "hunter2", "fictional-dev", SID, "Northwind", str(self.root)):
            self.assertNotIn(leaked, blob)

    def test_denylist_is_required_and_never_quoted(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            ms.main(["mine", "--lessons-dir", str(self.lessons), "--state", str(self.state)])
        self.assertEqual(raised.exception.code, 2)
        bad = self.tmp / "bad.txt"
        bad.write_text("secret-term-(unclosed\n", encoding="utf-8")
        with self.assertRaises(ms.DenylistError) as err:
            ms.load_denylist(bad)
        self.assertNotIn("secret-term", str(err.exception))
        self.assertIn("#1", str(err.exception))


class TestNoiseAndIncremental(Base):
    def session(self, cwd, first="Fix the bug.", entrypoint="cli"):
        return [{**user(first, 0), "cwd": cwd, "entrypoint": entrypoint}, assistant("Done.", 1),
                {**user("No, wrong branch.", 2), "cwd": cwd}, assistant("Ok.", 3)]

    def test_noise_classes_are_dropped_and_real_work_kept(self):
        noise = {
            "a1": ("/tmp/llm-judge-x1/work", "cli"),
            "a2": ("/tmp/propose-skill-edit-abc/trigger-1", "sdk-cli"),
            "a3": ("/home/fictional-dev/repos/GHA-bench/workspaces/cell-4", "sdk-cli"),
            "a4": ("/home/fictional-dev/repos/compare-agent-scripting-by-language-v3/cell", "sdk-cli"),
            "a5": ("/home/fictional-dev/sprint-eval/skills-evals-main", "sdk-cli"),
            "a6": ("/tmp/lesson-classify-abc", "sdk-cli"),
            "a7": ("/home/fictional-dev/repos/x/.claude/worktrees/opus-bench-2", "sdk-cli"),
        }
        for sid, (cwd, ep) in noise.items():
            write_session(self.root, sid, self.session(cwd, entrypoint=ep), slug=sid)
        write_session(self.root, "w1", self.session(HOME_CWD, "<scheduled-task name=\"handle-ready-"
                                                    "for-review-issues-fleetwide\">"), slug="w1")
        write_session(self.root, "g1", self.session(HOME_CWD, "You are grading an AI coding agent's "
                                                    "work against a rubric."), slug="g1")
        keep = {
            "k1": ("/home/fictional-dev/repos/example-repo", "sdk-cli"),  # a claude --bg daemon
            "k2": ("/home/fictional-dev/repos/compare-agent-scripting-by-language", "cli"),
            "k3": ("E:\\repos\\example-owner\\example-repo", "cli"),
        }
        for sid, (cwd, ep) in keep.items():
            write_session(self.root, sid, self.session(cwd, entrypoint=ep), slug=sid)
        sub = self.root / "k1" / "k1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-x.jsonl").write_text(json.dumps(user("No, nope.", 0)) + "\n", encoding="utf-8")
        code, out = self.mine_cli()
        self.assertEqual(code, 0)
        self.assertIn("3 sessions read, 9 noise", out)
        sessions = {r["session"] for r in self.records()}
        self.assertEqual(sessions, {ms.short_hash(s) for s in keep})
        repos = {r["repo"] for r in self.records()}
        self.assertEqual(repos, {"example-repo", "compare-agent-scripting-by-language"})

    def test_desktop_scheduled_sessions_are_dropped(self):
        desktop = self.tmp / "Claude"
        meta = desktop / "claude-code-sessions" / "acct" / "org"
        meta.mkdir(parents=True)
        (meta / "local_aaaa.json").write_text(json.dumps(
            {"cliSessionId": "d1", "scheduledTaskId": "task-1"}), encoding="utf-8")
        (meta / "local_bbbb.json").write_text(json.dumps(
            {"cliSessionId": "d2", "postTurnSummary": {"needs_action": True}}), encoding="utf-8")
        write_session(self.root, "d1", self.session(HOME_CWD), slug="d1")
        write_session(self.root, "d2", [user("Go.", 0), assistant("All done.", 1)], slug="d2")
        cowork = desktop / "local-agent-mode-sessions" / "acct" / "org" / "short" / ".claude" / "projects" / "session"
        write_session(cowork, "c1", self.session(HOME_CWD), slug=".")
        code, out = self.mine_cli("--desktop", str(desktop))
        self.assertIn("2 sessions read, 1 noise", out)
        by_session = {(r["session"], r["signal"]) for r in self.records()}
        self.assertIn((ms.short_hash("d2"), "abandoned"), by_session)
        self.assertIn((ms.short_hash("c1"), "correction"), by_session)

    def test_incremental_settling_and_growth(self):
        path = write_session(self.root, SID, self.session(HOME_CWD))
        recent = [{**e, "timestamp": "2026-10-20T11:00:00Z"} for e in self.session(HOME_CWD)]
        write_session(self.root, "fresh", recent, slug="fresh")
        _, out = self.mine_cli()
        self.assertIn("1 sessions read, 0 noise, 1 settling, 0 unchanged", out)
        _, out = self.mine_cli()
        self.assertIn("0 sessions read, 0 noise, 1 settling, 1 unchanged, 0 new records", out)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(user("Stop, that's not what I asked.", 10)) + "\n")
            f.write(json.dumps(assistant("Sorry.", 11)) + "\n")
        _, out = self.mine_cli()
        self.assertIn("1 sessions read", out)
        self.assertIn("1 new records", out)
        ids = [r["record_id"] for r in self.records()]
        self.assertEqual(len(ids), len(set(ids)))
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn(SID, json.dumps(state))

    def test_state_out_leaves_the_old_state_alone(self):
        write_session(self.root, SID, self.session(HOME_CWD))
        new = self.tmp / "new-state.json"
        self.mine_cli("--state-out", str(new))
        self.assertFalse(self.state.exists())
        self.assertEqual(len(json.loads(new.read_text())["sessions"]), 1)

    def test_normalize_cwd(self):
        self.assertEqual(ms.normalize_cwd("C:\\Users\\fictional-dev\\repos\\x"), "~/repos/x")
        self.assertEqual(ms.normalize_cwd("E:\\repos\\o\\r"), "/mnt/e/repos/o/r")
        self.assertEqual(ms.normalize_cwd("/mnt/c/Users/fictional-dev/repos"), "~/repos")
        self.assertEqual(ms.repo_of("~/repos/r/.claude/worktrees/feature"), "r")
        self.assertEqual(ms.repo_of("~"), "~")


def rec(signal, subject=("none", ""), excerpt="No, wrong.", session="s1", date="2026-10-18", rid=None):
    return {"schema_version": 1, "record_id": rid or ms.short_hash(signal, *subject, excerpt, session),
            "session": session, "date": date, "source": "wsl", "repo": "example-repo", "signal": signal,
            "excerpt": excerpt, "subject": {"kind": subject[0], "name": subject[1]}}


SKILL = ("skill", "writing-adrs")
GUIDE = ("guidance", "security")


class TestRoute(Base):
    def kinds(self, records):
        return {(p["subject"], p["kind"]) for p in ms.route(records, self.redactor)}

    def test_kind_table(self):
        got = self.kinds([
            rec("correction", SKILL), rec("rule", SKILL, "Always write the status line."),
            rec("rule", GUIDE, "From now on keep secrets out of logs."), rec("abandoned", GUIDE, "ended on a tool call"),
            rec("rule", ("memory", "memory"), "Remember that I like tables."),
            rec("rule", excerpt="Going forward, short answers."),
            rec("skill-invoked", SKILL, "writing-adrs"),
        ])
        self.assertEqual(got, {("skill:writing-adrs", "fixture-candidate"), ("skill:writing-adrs", "skill-edit"),
                               ("guidance:security", "guidance-edit"), ("guidance:security", "fixture-candidate"),
                               ("memory", "memory-note"), ("none", "memory-note")})

    def test_new_skill_needs_three_sessions(self):
        two = [rec("correction", session=f"s{i}", excerpt=f"No, wrong {i}.") for i in range(2)]
        self.assertEqual(self.kinds(two), set())
        three = two + [rec("correction", session="s9", excerpt="No, wrong again.")]
        self.assertEqual(self.kinds(three), {("none", "new-skill")})

    def test_checkable_lessons_become_agent_checks_first(self):
        cases = {
            "Never commit `.env.local` files.": "check_paths",
            "You forgot docs/skill-impact.md again.": "require `docs/skill-impact.md`",
            "Don't use `--squash` here.": "added line containing `--squash`",
            "Branch names need the `persistent/` prefix.": "the `persistent/` rule",
        }
        for text, sketch in cases.items():
            props = ms.route([rec("rule", SKILL, text), rec("correction", SKILL, "No, the other one.")],
                             self.redactor)
            check = [p for p in props if p["kind"] == "agent-check"]
            self.assertEqual(len(check), 1, text)
            self.assertIn(sketch, check[0]["check_sketch"], text)
            self.assertEqual({p["kind"] for p in props}, {"agent-check", "fixture-candidate"}, text)
        props = ms.route([rec("rule", SKILL, "Always explain the trade-off first.")], self.redactor)
        self.assertEqual([p["kind"] for p in props], ["skill-edit"])
        self.assertNotIn("check_sketch", props[0])

    def test_proposal_schema_and_files(self):
        records = [rec("correction", SKILL, f"No, case {i} <email>.", session=f"s{i}",
                       date=f"2026-10-1{i}") for i in range(7)]
        props = ms.route(records, self.redactor)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual(set(p), {"schema_version", "id", "subject", "kind", "evidence_count",
                                  "first_seen", "last_seen", "examples", "repos"})
        self.assertEqual((p["evidence_count"], p["first_seen"], p["last_seen"]), (7, "2026-10-10", "2026-10-16"))
        self.assertEqual(len(p["examples"]), ms.MAX_EXAMPLES)
        self.assertTrue(all(isinstance(e, str) for e in p["examples"]))
        self.assertIn("[correction]", p["examples"][0])
        self.assertEqual(p["repos"], ["example-repo"])
        self.assertEqual(ms.route(list(reversed(records)), self.redactor), props)
        day = self.lessons / "proposals" / "2026-10-20"
        ms.write_proposals(day, props)
        self.assertEqual([f.name for f in day.iterdir()], ["skill-writing-adrs__fixture-candidate.json"])
        self.assertEqual(ms.read_proposals(day), props)

    def test_route_cli_reads_all_records_and_dedupes(self):
        (self.lessons / "records").mkdir(parents=True)
        r = rec("correction", SKILL)
        for day in ("2026-10-18", "2026-10-19"):
            (self.lessons / "records" / f"{day}.jsonl").write_text(json.dumps(r) + "\n", encoding="utf-8")
        code, out = self.run_main("route", "--lessons-dir", str(self.lessons), "--denylist", str(self.deny),
                                  "--no-local-names", "--now", NOW)
        self.assertEqual(code, 0)
        self.assertIn("1 fixture-candidate", out)
        props = ms.read_proposals(self.lessons / "proposals" / "2026-10-20")
        self.assertEqual(props[0]["evidence_count"], 1)

    def test_classify_is_off_by_default_and_mocked_when_on(self):
        calls = []

        def runner(args, cwd, stdin):
            calls.append((args, cwd))
            return 0, "agent-check\n"
        (self.lessons / "records").mkdir(parents=True)
        (self.lessons / "records" / "2026-10-18.jsonl").write_text(
            json.dumps(rec("correction", SKILL)) + "\n", encoding="utf-8")
        base = ["route", "--lessons-dir", str(self.lessons), "--denylist", str(self.deny),
                "--no-local-names", "--now", NOW]
        self.run_main(*base, runner=runner)
        self.assertEqual(calls, [])
        self.run_main(*base, "--classify", runner=runner)
        self.assertEqual(len(calls), 1)
        args, cwd = calls[0]
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertTrue(cwd.startswith("/tmp/lesson-classify-"))
        self.assertEqual(ms.noise_reason(ms.normalize_cwd(cwd), "sdk-cli", "x"), "tmp")
        props = ms.read_proposals(self.lessons / "proposals" / "2026-10-20")
        self.assertEqual(props[0]["kind"], "agent-check")
        self.assertIn("check_sketch", props[0])

    def test_classify_ignores_an_answer_that_is_not_one_kind(self):
        props = ms.route([rec("correction", SKILL)], self.redactor)
        ms.classify(props, lambda a, c, s: (0, "I think this is a skill-edit because"))
        self.assertEqual(props[0]["kind"], "fixture-candidate")
        ms.classify(props, lambda a, c, s: (1, "skill-edit"))
        self.assertEqual(props[0]["kind"], "fixture-candidate")


class TestFileIssues(Base):
    def setUp(self):
        super().setUp()
        self.props = ms.route([rec("correction", SKILL), rec("rule", GUIDE, "From now on, short.")],
                              self.redactor)
        self.day = self.lessons / "proposals" / "2026-10-20"
        ms.write_proposals(self.day, self.props)

    def test_dry_run_is_the_default_and_calls_nothing(self):
        def runner(*a):
            raise AssertionError("dry run must not run gh")
        code, out = self.run_main("file-issues", "--proposals", str(self.day), "--denylist", str(self.deny),
                                  "--no-local-names", runner=runner)
        self.assertEqual(code, 0)
        self.assertIn("2 would_file", out)
        self.assertIn("(dry run)", out)

    def test_apply_files_new_skips_existing_and_blocks_unclean(self):
        calls = []
        existing = self.props[0]["id"]

        def runner(args, cwd, stdin):
            calls.append((list(args), stdin))
            if args[:3] == ["gh", "label", "list"]:
                return 0, "lesson-proposal\n"
            if args[:3] == ["gh", "issue", "list"]:
                return 0, (f"Lesson: x [lesson {existing}]\n" if existing in " ".join(args) else "")
            return 0, ""
        bad = dict(self.props[1], id="badbadbadbad", examples=["mail jane.roe@example.com"])
        counts = ms.file_issues(self.props + [bad], "2026-10-20", self.redactor, "o/r", True, runner,
                                out=io.StringIO())
        self.assertEqual(counts, {"filed": 1, "existing": 1, "blocked": 1, "would_file": 0})
        creates = [c for c in calls if c[0][:3] == ["gh", "issue", "create"]]
        self.assertEqual(len(creates), 1)
        args, body = creates[0]
        self.assertIn("lesson-proposal", args)
        self.assertIn(f"[lesson {self.props[1]['id']}]", args[args.index("--title") + 1])
        self.assertIn("Written by session-miner", body)
        self.assertIn("lessons/proposals/2026-10-20/", body)
        label_creates = [c[0] for c in calls if c[0][:3] == ["gh", "label", "create"]]
        self.assertEqual([a[3] for a in label_creates], ["agent-authored"])
        self.assertFalse(any("jane.roe" in " ".join(c[0]) + (c[1] or "") for c in calls))


class TestLocate(Base):
    def test_locate_finds_the_transcript_by_hash(self):
        path = write_session(self.root, SID, [user("Go.", 0)])
        code, out = self.run_main("locate", ms.short_hash(SID), "--root", f"wsl={self.root}")
        self.assertEqual((code, out.strip()), (0, str(path)))
        self.assertEqual(self.run_main("locate", "0" * 16, "--root", f"wsl={self.root}")[0], 1)


class TestScratchIsDeniedToEvalArms(unittest.TestCase):
    """The script's clone holds real lesson records, and --classify's cwd sees
    a proposal: eval arms must never read either (harness/run_eval.py)."""

    def test_shell_scratch_and_classify_prefixes_are_denied(self):
        sys.path.insert(0, str(ROOT / "harness"))
        import run_eval
        from scorers import bash_ast
        if not bash_ast.parser_importable():
            self.skipTest("the pinned parser is not installed here")
        source = (ROOT / "scripts/run_session_miner.sh").read_bytes()
        found = []

        def visit(node):
            if node.type == "command":
                name = node.child_by_field_name("name")
                if name is not None and source[name.start_byte:name.end_byte] == b"mktemp":
                    found.append([source[a.start_byte:a.end_byte]
                                  for a in node.children_by_field_name("argument")])
            for child in node.children:
                visit(child)
        visit(bash_ast.parse_bash(source))
        self.assertEqual(found, [[b"-d", b'"${TMPDIR:-/tmp}/session-miner-XXXXXXXX"']])
        for prefix in ("session-miner-", "lesson-classify-"):
            self.assertIn(prefix, run_eval.HARNESS_TEMP_PREFIXES)


class TestRunSessionMiner(unittest.TestCase):
    """scripts/run_session_miner.sh end to end against local bare repos."""

    SCRIPT = ROOT / "scripts" / "run_session_miner.sh"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com"}
        self.se = self._bare("se.git", {"scripts/mine_sessions.py": (ROOT / "scripts/mine_sessions.py").read_text()})
        self.priv = self._bare("priv.git", {"README.md": "private\n"})
        self.projects = self.tmp / "projects"
        write_session(self.projects, SID, [user("Go.", 0), assistant("Done.", 1),
                                           user("No, that's wrong.", 2), assistant("Ok.", 3)])
        self.deny = self.tmp / "deny.txt"
        self.deny.write_text("northwind\n", encoding="utf-8")
        self.state = self.home / "state.json"
        self.run_env = dict(self.env, SKILLS_EVALS_URL=self.se.as_uri(), LESSONS_REPO_URL=self.priv.as_uri(),
                            MINER_ROOTS=f"wsl={self.projects}", MINER_NOW=NOW, MINER_STATE=str(self.state),
                            PRIVACY_DENYLIST_FILE=str(self.deny), GUIDANCE_MANIFEST=str(self.tmp / "none.yml"),
                            MINER_REGISTRIES="")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def _bare(self, name, files):
        bare = self.tmp / name
        self.git("init", "--bare", "--quiet", "-b", "main", str(bare))
        seed = self.tmp / (name + "-seed")
        seed.mkdir()
        self.git("init", "--quiet", "-b", "main", cwd=seed)
        for rel, text in files.items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(text, encoding="utf-8")
        self.git("add", ".", cwd=seed)
        self.git("commit", "--quiet", "-m", "seed", cwd=seed)
        self.git("push", "--quiet", bare.as_uri(), "main", cwd=seed)
        return bare

    def run_script(self, *args, env=None):
        return subprocess.run(["bash", str(self.SCRIPT), *args], env=env or self.run_env,
                              capture_output=True, text=True)

    def test_publishes_to_the_lessons_branch_then_saves_state(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 sessions read", proc.stdout)
        self.assertTrue(proc.stdout.rstrip().endswith("committed"))
        files = self.git("--git-dir", str(self.priv), "ls-tree", "-r", "--name-only", "persistent/lessons")
        # One unattributed correction in one session clusters into no proposal.
        self.assertEqual(files.split(), ["lessons/records/2026-10-20.jsonl"])
        self.assertEqual(self.git("--git-dir", str(self.priv), "ls-tree", "--name-only", "main"), "README.md")
        self.assertTrue(self.state.is_file())
        again = self.run_script()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("0 sessions read", again.stdout)
        self.assertIn("unchanged", again.stdout)

    def test_dry_run_pushes_nothing_and_keeps_state(self):
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dry run", proc.stdout)
        self.assertFalse(self.state.exists())
        heads = self.git("--git-dir", str(self.priv), "branch", "--list")
        self.assertNotIn("persistent/lessons", heads)

    def test_refuses_without_a_denylist(self):
        env = dict(self.run_env)
        del env["PRIVACY_DENYLIST_FILE"]
        proc = self.run_script(env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("nothing mined", proc.stderr)


if __name__ == "__main__":
    unittest.main()
