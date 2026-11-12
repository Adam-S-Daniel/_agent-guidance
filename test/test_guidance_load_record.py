"""Tests for .claude/hooks/guidance-load-record.sh, the per-session Stop hook
that records whether the full fleet guidance or only the repo stub loaded.

Every case runs the real hook under bash in a temp HOME with a fixed clock
(GUIDANCE_RECORD_NOW), a fake `curl` first on PATH that records each call and
answers from a scripted list, and no network. Fixtures use invented paths and
example.com only.
"""

import hashlib
import hmac
import http.server
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "guidance-load-record.sh"
NOW = "2026-10-31T12:00:00Z"
KEYS = ["v", "ts", "kind", "session", "surface", "remote", "cli", "state",
        "repos", "guidance_version", "check_expected", "check_reported",
        "registry_sha", "sources", "sources_dropped", "uploaded"]
FULL = "guidance-marker: fleet-full (transcript counts use this line; keep it)"
STUB = "guidance-marker: fleet-stub (transcript counts use this line; keep it)"
REPO = "Adam-S-Daniel/adam-agentskills-private"
CONTENTS = f"https://api.example.com/repos/{REPO}/contents/events/2026-10/cloud-guidance.jsonl"
PROBE = f"https://api.example.com/repos/{REPO}"
# The session-credential probe, refused: the private repo is not attached.
NOT_ATTACHED = {"status": 403, "body": {"message": "not permitted"}}

# The fake curl: appends {"argv": [...], "stdin": "...", "body": ...} to
# $FAKE_CURL_LOG, then pops the next {"status", "body"} from $FAKE_CURL_SCRIPT,
# writes the body to the -o file and prints the status, as `-w %{http_code}`
# would.
FAKE_CURL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    argv = sys.argv[1:]
    stdin = sys.stdin.read() if "--config" in argv else ""
    def opt(name):
        return argv[argv.index(name) + 1] if name in argv else None
    data = opt("--data-binary")
    body = None
    if data and data.startswith("@"):
        with open(data[1:], encoding="utf-8") as handle:
            body = json.load(handle)
    with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "stdin": stdin, "body": body,
                                 "sink_env": sorted(k for k in os.environ if k.startswith(("USAGE_SINK", "USAGE_WRITER_APP")))}) + "\\n")
    path = os.environ["FAKE_CURL_SCRIPT"]
    with open(path, encoding="utf-8") as handle:
        queue = json.load(handle)
    reply = queue.pop(0) if queue else {"status": 500, "body": {}}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(queue, handle)
    with open(opt("-o"), "w", encoding="utf-8") as handle:
        json.dump(reply["body"], handle)
    sys.stdout.write(str(reply["status"]))
    """)


def instructions(*files):
    return {"type": "attachment", "version": "2.1.312", "entrypoint": "remote_projects",
            "attachment": {"type": "instructions", "files": list(files)}}


def assistant(n):
    return {"type": "assistant", "version": "2.1.312", "entrypoint": "remote_projects",
            "message": {"id": f"msg_{n}", "role": "assistant", "content": []}}


def assistant_text(n, *texts, extra=()):
    """One assistant message whose content is text blocks, then any extra blocks."""
    content = [{"type": "text", "text": text} for text in texts] + list(extra)
    return {"type": "assistant", "version": "2.1.312", "entrypoint": "remote_projects",
            "message": {"id": f"msg_{n}", "role": "assistant", "content": content}}


def user():
    return {"type": "user", "version": "2.1.312", "entrypoint": "remote_projects",
            "message": {"role": "user", "content": "hello"}}


def memfile(path, kind, content):
    return {"path": path, "type": kind, "content": content}


class HookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grec-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(0o755)
        self.curl_log = self.tmp / "curl.log"
        self.curl_script = self.tmp / "curl-script.json"
        self.curl_script.write_text("[]", encoding="utf-8")
        self.transcript = self.tmp / "session.jsonl"
        self.state = self.home / ".local" / "state" / "fleet-guidance"
        self.tmpdir = self.tmp / "t"
        self.tmpdir.mkdir()
        self.env = {
            "TMPDIR": str(self.tmpdir),
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.home),
            "GUIDANCE_RECORD_NOW": NOW,
            # The API and git seams are read only under this test marker.
            "GUIDANCE_RECORD_TEST": "1",
            "GUIDANCE_RECORD_API": "https://api.example.com",
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_SCRIPT": str(self.curl_script),
        }

    def write_transcript(self, entries):
        self.transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def run_hook(self, session="session-one", env=None, event=None, stdin=None):
        payload = stdin if stdin is not None else json.dumps(event or {
            "session_id": session, "transcript_path": str(self.transcript),
            "hook_event_name": "Stop"})
        out = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True,
                             env=dict(self.env, **(env or {})), timeout=60)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        return out

    def records(self):
        log = self.state / "sessions.jsonl"
        if not log.exists():
            return []
        out = []
        for line in log.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            # `listing` is optional: present only when a skill listing was found.
            keys = KEYS[:-1] + ["listing"] + KEYS[-1:] if "listing" in record else KEYS
            self.assertEqual(list(record), keys, line)
            out.append(record)
        return out

    def calls(self):
        if not self.curl_log.exists():
            return []
        return [json.loads(l) for l in self.curl_log.read_text(encoding="utf-8").splitlines()]


class ClassificationTests(HookTestCase):
    def state_for(self, *files):
        self.write_transcript([user(), instructions(*files), assistant(1)])
        self.run_hook()
        [record] = self.records()
        return record["state"]

    def test_fleet_full_marker_is_full(self):
        self.assertEqual(self.state_for(
            memfile("/w/u/.claude/CLAUDE.md", "User", "intro\n" + FULL + "\nmore"),
            memfile("/w/repo-a/AGENTS.md", "Project", STUB)), "full")

    def test_legacy_full_signature_is_full(self):
        self.assertEqual(self.state_for(
            memfile("/w/u/.claude/CLAUDE.md", "User", "## Working in these repos\n- rule")), "full")

    def test_fleet_stub_marker_alone_is_stub_only(self):
        self.assertEqual(self.state_for(memfile("/w/repo-a/AGENTS.md", "Project", STUB)), "stub-only")

    def test_legacy_stub_signature_is_stub_only(self):
        self.assertEqual(self.state_for(memfile(
            "/w/repo-a/AGENTS.md", "Project",
            "## Fleet guidance is delivered once per session — not by this file")), "stub-only")

    def test_unrelated_files_are_neither(self):
        self.assertEqual(self.state_for(memfile("/w/repo-a/CLAUDE.md", "Project", "# Notes\n")), "neither")

    def test_marker_split_across_instruction_entries(self):
        self.write_transcript([instructions(memfile("/w/repo-a/AGENTS.md", "Project", STUB)),
                               instructions(memfile("/w/u/.claude/CLAUDE.md", "User", FULL))])
        self.run_hook()
        self.assertEqual(self.records()[0]["state"], "full")

    def test_no_instructions_waits_then_unknown_after_three_turns(self):
        self.write_transcript([user(), assistant(1), assistant(1), assistant(2)])
        self.run_hook()
        self.assertEqual(self.records(), [])
        self.assertFalse((self.state / "done").exists() and any((self.state / "done").iterdir()))
        self.write_transcript([user(), assistant(1), assistant(2), assistant(3)])
        self.run_hook()
        [record] = self.records()
        self.assertEqual((record["state"], record["repos"]), ("unknown", 0))

    def test_instructions_arriving_later_are_counted(self):
        self.write_transcript([user(), assistant(1)])
        self.run_hook()
        self.assertEqual(self.records(), [])
        self.write_transcript([user(), assistant(1),
                               instructions(memfile("/w/repo-a/AGENTS.md", "Project", STUB))])
        self.run_hook()
        self.assertEqual([r["state"] for r in self.records()], ["stub-only"])


class RecordTests(HookTestCase):
    def test_record_fields(self):
        cfg = self.home / ".claude"
        cfg.mkdir()
        (cfg / "CLAUDE.md").write_text(
            "mine\n<!-- BEGIN FLEET GUIDANCE -->\n<!-- fleet-guidance-version: 0a1b2c3d -->\n", encoding="utf-8")
        self.write_transcript([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", FULL),
            memfile("/w/repo-a/CLAUDE.md", "Project", "@AGENTS.md"),
            memfile("/w/repo-a/AGENTS.md", "Project", STUB),
            memfile("/w/repo-b/.claude/CLAUDE.md", "Project", "x"),
            memfile("/w/repo-c/docs/AGENTS.md", "Project", "x"),
            memfile("/w/repo-d/notes.md", "Project", "x"),
            memfile("/w/repo-e/AGENTS.md", "Local", "x"))])
        self.run_hook(session="session-one")
        [record] = self.records()
        self.assertEqual(record, {
            "v": 1, "ts": NOW, "kind": "guidance-load",
            "session": hashlib.sha256(b"session-one").hexdigest()[:16],
            "surface": "remote_projects", "remote": False, "cli": "2.1.312",
            "state": "full", "repos": 3, "guidance_version": "0a1b2c3d",
            "check_expected": False, "check_reported": "absent",
            "registry_sha": None, "sources": None, "sources_dropped": None, "uploaded": None})

    def test_record_holds_no_path_content_or_session_id(self):
        self.write_transcript([instructions(
            memfile("/w/secret-repo/AGENTS.md", "Project", STUB + "\nPRIVATE-CANARY-77"))])
        self.run_hook(session="session-canary-42")
        text = (self.state / "sessions.jsonl").read_text(encoding="utf-8")
        for needle in ("/w/", "secret-repo", "AGENTS.md", "PRIVATE-CANARY-77",
                       "guidance-marker", "session-canary-42", str(self.home)):
            self.assertNotIn(needle, text)

    def test_missing_version_and_metadata_are_null(self):
        self.transcript.write_text(json.dumps({"type": "attachment", "attachment": {
            "type": "instructions", "files": [memfile("/w/r/AGENTS.md", "Project", "x")]}}) + "\n",
            encoding="utf-8")
        self.run_hook()
        [record] = self.records()
        self.assertEqual((record["surface"], record["cli"], record["guidance_version"]), (None, None, None))

    def test_guidance_version_follows_claude_config_dir(self):
        cfg = self.tmp / "cfg"
        cfg.mkdir()
        (cfg / "CLAUDE.md").write_text("<!-- fleet-guidance-version: deadbeef -->\n", encoding="utf-8")
        bad = self.home / ".claude"
        bad.mkdir()
        (bad / "CLAUDE.md").write_text("<!-- fleet-guidance-version: 11111111 -->\n", encoding="utf-8")
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(env={"CLAUDE_CONFIG_DIR": str(cfg)})
        self.assertEqual(self.records()[0]["guidance_version"], "deadbeef")

    def test_malformed_version_line_is_null(self):
        cfg = self.home / ".claude"
        cfg.mkdir()
        (cfg / "CLAUDE.md").write_text("<!-- fleet-guidance-version: NOTHEX99 -->\n", encoding="utf-8")
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook()
        self.assertIsNone(self.records()[0]["guidance_version"])

    def test_xdg_state_home_is_honored(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(env={"XDG_STATE_HOME": str(self.tmp / "xdg")})
        self.assertTrue((self.tmp / "xdg" / "fleet-guidance" / "sessions.jsonl").is_file())
        self.assertFalse(self.state.exists())

    def test_remote_flag_from_either_variable(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(session="a", env={"CLAUDE_CODE_REMOTE": "true"})
        self.run_hook(session="b", env={"CLAUDE_CODE_REMOTE_SESSION_ID": "example-remote"})
        self.run_hook(session="c", env={"CLAUDE_CODE_REMOTE": "false"})
        self.assertEqual([r["remote"] for r in self.records()], [True, True, False])


# Invented 8-hex check values; the real one is the last 8 hex of a payload digest.
CHECK = "5ea40d61"
OTHER = "0badc0de"
CHECK_FILE = f"intro\nfleet-guidance check value: {CHECK}\nmore\n"


class CheckValueTests(HookTestCase):
    """check_expected and check_reported: a session's quote of the check value
    compared with the User-type memory file it loaded. Neither field holds a
    value string."""

    def record_for(self, entries):
        # A fresh session per call: the hook records each session once.
        self.sessions = getattr(self, "sessions", 0) + 1
        session = f"check-session-{self.sessions}"
        self.write_transcript(entries)
        self.run_hook(session=session)
        [record] = [r for r in self.records()
                    if r["session"] == hashlib.sha256(session.encode()).hexdigest()[:16]]
        return record

    def expected_for(self, *files):
        record = self.record_for([user(), instructions(*files), assistant(1)])
        self.assertEqual(record["check_reported"], "absent")
        return record["check_expected"]

    def reported_for(self, *texts, files=None, extra_entries=()):
        files = files if files is not None else [memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)]
        entries = [user(), instructions(*files)]
        entries += [assistant_text(n + 1, text) for n, text in enumerate(texts)]
        entries += list(extra_entries)
        return self.record_for(entries)["check_reported"]

    # check_expected -------------------------------------------------------

    def test_expected_true_for_a_user_file_with_the_line(self):
        self.assertIs(self.expected_for(memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)), True)

    def test_expected_false_when_no_loaded_file_has_the_line(self):
        self.assertIs(self.expected_for(memfile("/w/u/.claude/CLAUDE.md", "User", FULL)), False)

    def test_expected_false_for_a_line_in_a_project_file(self):
        # A repo file, base.md copy or stub included, cannot supply the value.
        self.assertIs(self.expected_for(
            memfile("/w/u/.claude/CLAUDE.md", "User", FULL),
            memfile("/w/repo-a/AGENTS.md", "Project", CHECK_FILE),
            memfile("/w/repo-a/CLAUDE.local.md", "Local", CHECK_FILE)), False)

    def test_expected_needs_the_whole_line(self):
        for name, text in {
            "stub prose": "ends with a line `fleet-guidance check value: <8 hex>`.\n",
            "inline quote": f"see `fleet-guidance check value: {CHECK}` here\n",
            "leading text": f"x fleet-guidance check value: {CHECK}\n",
            "trailing text": f"fleet-guidance check value: {CHECK} x\n",
            "nine hex": f"fleet-guidance check value: {CHECK}a\n",
            "seven hex": f"fleet-guidance check value: {CHECK[:7]}\n",
            "uppercase": f"fleet-guidance check value: {CHECK.upper().replace('E', 'F')}\n",
            "no value": "fleet-guidance check value: \n",
            "wrong label": f"fleet-guidance check: {CHECK}\n",
        }.items():
            with self.subTest(name=name):
                self.assertIs(self.expected_for(memfile("/w/u/.claude/CLAUDE.md", "User", text)), False)

    def test_expected_true_with_the_line_first_last_or_without_a_final_newline(self):
        for name, text in {
            "first": f"fleet-guidance check value: {CHECK}\nrest\n",
            "last, no newline": f"rest\nfleet-guidance check value: {CHECK}",
            "alone": f"fleet-guidance check value: {CHECK}",
        }.items():
            with self.subTest(name=name):
                self.assertIs(self.expected_for(memfile("/w/u/.claude/CLAUDE.md", "User", text)), True)

    def test_expected_is_null_when_the_state_is_unknown(self):
        record = self.record_for([user(), assistant(1), assistant(2), assistant(3)])
        self.assertEqual((record["state"], record["check_expected"], record["check_reported"]),
                         ("unknown", None, "absent"))

    # check_reported -------------------------------------------------------

    def test_reported_match(self):
        self.assertEqual(self.reported_for(f"fleet-guidance check: {CHECK}\n\nHello."), "match")

    def test_reported_match_is_found_anywhere_in_the_text(self):
        self.assertEqual(self.reported_for(f"Sure.\nfleet-guidance check: {CHECK}, then the answer."), "match")

    def test_reported_mismatch_for_a_different_value(self):
        self.assertEqual(self.reported_for(f"fleet-guidance check: {OTHER}"), "mismatch")

    def test_reported_mismatch_when_nothing_was_expected(self):
        # The session quoted a value no loaded User file carries.
        files = [memfile("/w/u/.claude/CLAUDE.md", "User", FULL)]
        self.assertEqual(self.reported_for(f"fleet-guidance check: {CHECK}", files=files), "mismatch")
        files = [memfile("/w/repo-a/AGENTS.md", "Project", CHECK_FILE)]
        self.assertEqual(self.reported_for(f"fleet-guidance check: {CHECK}", files=files), "mismatch")

    def test_reported_none(self):
        self.assertEqual(self.reported_for("fleet-guidance check: none\n"), "none")

    def test_reported_none_is_a_report_even_when_a_value_was_expected(self):
        # The session saw the line in memory but said it did not: recorded as
        # what it said, with check_expected true to show the contradiction.
        record = self.record_for([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)),
            assistant_text(1, "fleet-guidance check: none")])
        self.assertEqual((record["check_expected"], record["check_reported"]), (True, "none"))

    def test_reported_absent_without_any_assistant_text(self):
        self.assertEqual(self.reported_for(), "absent")
        self.assertEqual(self.reported_for("Hello, no report here."), "absent")

    def test_reported_absent_for_text_that_only_resembles_a_report(self):
        for name, text in {
            "placeholder": "fleet-guidance check: <the value>",
            "nine hex": f"fleet-guidance check: {CHECK}a",
            "seven hex": f"fleet-guidance check: {CHECK[:7]}",
            "uppercase hex": f"fleet-guidance check: {CHECK.upper().replace('E', 'F')}",
            "none prefix": "fleet-guidance check: nonexistent",
            "value label": f"fleet-guidance check value: {CHECK}",
            "verdict line": "fleet-guidance: current (v12345678, 100 bytes)",
        }.items():
            with self.subTest(name=name):
                self.assertEqual(self.reported_for(text), "absent")

    def test_reported_boundary_accepts_punctuation_after_the_value(self):
        for tail in (".", ",", ")", "`", "\n", " "):
            with self.subTest(tail=repr(tail)):
                self.assertEqual(self.reported_for(f"fleet-guidance check: {CHECK}{tail}"), "match")
                self.assertEqual(self.reported_for(f"fleet-guidance check: none{tail}"), "none")

    def test_reported_takes_the_first_report_in_transcript_order(self):
        self.assertEqual(self.reported_for("fleet-guidance check: none", f"fleet-guidance check: {CHECK}"), "none")
        self.assertEqual(self.reported_for(f"fleet-guidance check: {CHECK}", f"fleet-guidance check: {OTHER}"), "match")
        self.assertEqual(self.reported_for(f"fleet-guidance check: {OTHER}", f"fleet-guidance check: {CHECK}"), "mismatch")
        self.assertEqual(self.reported_for("No report here.", f"fleet-guidance check: {CHECK}"), "match")

    def test_reported_takes_the_first_match_inside_one_text(self):
        self.assertEqual(self.reported_for(
            f"fleet-guidance check: {OTHER} then fleet-guidance check: {CHECK}"), "mismatch")

    def test_reported_reads_every_text_block_of_a_message_in_order(self):
        record = self.record_for([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)),
            assistant_text(1, "thinking out loud", f"fleet-guidance check: {CHECK}",
                           f"fleet-guidance check: {OTHER}")])
        self.assertEqual(record["check_reported"], "match")

    def test_reported_ignores_blocks_that_are_not_assistant_text(self):
        tool_use = {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                    "input": {"command": f"echo fleet-guidance check: {CHECK}"}}
        thinking = {"type": "thinking", "thinking": f"fleet-guidance check: {CHECK}"}
        # Not a text block, whatever else it carries.
        document = {"type": "document", "text": f"fleet-guidance check: {CHECK}"}
        entries = [
            user(),
            instructions(memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)),
            # The user's own words, a tool result and a system entry do not count.
            {"type": "user", "message": {"role": "user", "content": f"fleet-guidance check: {CHECK}"}},
            {"type": "user", "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_1",
                "content": f"fleet-guidance check: {CHECK}"}]}},
            {"type": "system", "content": f"fleet-guidance check: {CHECK}"},
            assistant_text(1, extra=[tool_use, thinking, document]),
        ]
        self.assertEqual(self.record_for(entries)["check_reported"], "absent")

    def test_reported_reads_text_split_across_entries_of_one_message(self):
        # Claude Code writes the blocks of one API message as separate entries
        # that share the message id.
        first = assistant_text(1, "On it.")
        second = assistant_text(1, f"fleet-guidance check: {CHECK}")
        self.assertEqual(self.record_for([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)), first, second])["check_reported"], "match")

    def test_reported_accepts_plain_string_assistant_content(self):
        entry = {"type": "assistant", "message": {"id": "msg_1", "role": "assistant",
                                                    "content": f"fleet-guidance check: {CHECK}"}}
        self.assertEqual(self.record_for([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)), entry])["check_reported"], "match")

    def test_reported_survives_malformed_entries(self):
        good = assistant_text(2, f"fleet-guidance check: {CHECK}")
        odd = [
            {"type": "assistant"},
            {"type": "assistant", "message": "text"},
            {"type": "assistant", "message": {"content": None}},
            {"type": "assistant", "message": {"content": [None, "x", {"type": "text"},
                                                           {"type": "text", "text": 7}]}},
        ]
        self.write_transcript([user(), instructions(memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE))]
                              + odd + [good])
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.run_hook()
        self.assertEqual(self.records()[0]["check_reported"], "match")

    def test_reported_with_an_unknown_state_has_no_expected_value(self):
        entries = [user(), assistant_text(1, f"fleet-guidance check: {CHECK}"), assistant(2), assistant(3)]
        record = self.record_for(entries)
        self.assertEqual((record["state"], record["check_expected"], record["check_reported"]),
                         ("unknown", None, "mismatch"))
        record = self.record_for([user(), assistant_text(1, "fleet-guidance check: none"), assistant(2),
                                  assistant(3)])
        self.assertEqual((record["check_expected"], record["check_reported"]), (None, "none"))

    def test_a_report_arriving_after_the_record_is_not_picked_up(self):
        # The record is written once, at the first Stop that has instructions.
        files = [memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE)]
        self.write_transcript([user(), instructions(*files), assistant(1)])
        self.run_hook()
        self.write_transcript([user(), instructions(*files), assistant_text(1, f"fleet-guidance check: {CHECK}")])
        self.run_hook()
        [record] = self.records()
        self.assertEqual(record["check_reported"], "absent")

    # what the record holds ------------------------------------------------

    def test_record_holds_no_value_string(self):
        self.write_transcript([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE),
            memfile("/w/repo-a/AGENTS.md", "Project", STUB)),
            assistant_text(1, f"fleet-guidance check: {CHECK}")])
        self.run_hook()
        text = (self.state / "sessions.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(CHECK, text)
        self.assertNotIn("check value", text)
        [record] = self.records()
        self.assertEqual((record["check_expected"], record["check_reported"]), (True, "match"))
        self.assertIsInstance(record["check_expected"], bool)

    def test_record_shape_is_unchanged_apart_from_the_two_fields(self):
        self.assertEqual(KEYS[8:12], ["repos", "guidance_version", "check_expected", "check_reported"])
        record = self.record_for([user(), instructions(memfile("/w/r/AGENTS.md", "Project", STUB)), assistant(1)])
        self.assertEqual(record["v"], 1)
        self.assertEqual(list(record), KEYS)


def skill_listing(content, initial=True):
    return {"type": "attachment", "version": "2.1.312", "entrypoint": "remote_projects",
            "attachment": {"type": "skill_listing", "isInitial": initial, "content": content}}


class ListingTests(HookTestCase):
    def listing_for(self, *entries):
        self.write_transcript([user(), instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               *entries, assistant(1)])
        self.run_hook()
        [record] = self.records()
        return record.get("listing", "absent")

    def test_a_listing_with_every_description_is_full(self):
        self.assertEqual(self.listing_for(skill_listing(
            "- init: Initialize a file\n- plug:one: Does one thing\n- plug:two: Does another")), "full")

    def test_a_namespaced_name_without_a_description_is_truncated(self):
        self.assertEqual(self.listing_for(skill_listing(
            "- plug:one: Does one thing\n- plug:two\n- plug:three")), "truncated")

    def test_a_bare_name_without_a_description_is_a_cut(self):
        # Cloud registry skills are listed by bare name; err toward "truncated".
        self.assertEqual(self.listing_for(skill_listing("- init\n- plug:one: Does one thing")), "truncated")

    def test_no_listing_omits_the_field(self):
        self.assertEqual(self.listing_for(), "absent")

    def test_a_non_initial_listing_is_not_proof(self):
        self.assertEqual(self.listing_for(skill_listing("- plug:one: Does one thing", initial=False)), "absent")

    def test_a_listing_without_text_content_omits_the_field(self):
        self.assertEqual(self.listing_for(skill_listing(None)), "absent")


# Invented 40-hex commits; the real ones are what skills-bootstrap.sh fetched.
SHA_A = "a" * 40
SHA_B = "0123456789abcdef0123456789abcdef01234567"
GOOD = {"registry": "https://github.com/example/good.git", "ref": SHA_A}


class SourcesTests(HookTestCase):
    """sources and sources_dropped: the registry commits read from
    skills-bootstrap.sh's install record (_agent-guidance#398)."""

    def setUp(self):
        super().setUp()
        self.install_record = self.home / ".claude" / "skills" / ".skills-bootstrap-installed.json"
        self.install_record.parent.mkdir(parents=True)
        self.write_transcript([user(), instructions(memfile("/w/r/AGENTS.md", "Project", STUB)), assistant(1)])

    def record_for(self, session="session-one"):
        self.run_hook(session=session)
        [record] = [r for r in self.records()
                    if r["session"] == hashlib.sha256(session.encode()).hexdigest()[:16]]
        return record

    def sources_for(self, doc=None):
        if doc is not None:
            self.install_record.write_text(json.dumps(doc), encoding="utf-8")
        record = self.record_for()
        return record["sources"], record["sources_dropped"]

    def test_no_install_record_is_null(self):
        self.assertEqual(self.sources_for(), (None, None))

    def test_an_install_record_without_sources_is_null(self):
        # What skills-bootstrap.sh wrote before it recorded commits.
        self.assertEqual(self.sources_for({"version": 1, "skills": [
            {"name": "x", "registry": "https://github.com/example/good.git"}]}), (None, None))

    def test_sources_that_are_not_a_list_are_null(self):
        self.assertEqual(self.sources_for({"sources": GOOD}), (None, None))

    def test_an_empty_list_records_an_empty_list(self):
        self.assertEqual(self.sources_for({"sources": []}), ([], 0))

    def test_valid_sources_are_normalized_deduped_and_sorted(self):
        self.assertEqual(self.sources_for({"sources": [
            {"registry": "https://github.com/Example-Owner/zeta-skills.git", "ref": SHA_B},
            {"registry": "https://github.com/Example-Owner/alpha.skills", "ref": SHA_A},
            {"registry": "https://github.com/Example-Owner/zeta-skills", "ref": SHA_B},
        ]}), ([{"registry": "Example-Owner/alpha.skills", "ref": SHA_A},
               {"registry": "Example-Owner/zeta-skills", "ref": SHA_B}], 0))

    def test_invalid_items_are_dropped_and_counted(self):
        sources, dropped = self.sources_for({"sources": [
            GOOD,
            {"registry": "file:///tmp/example/registry", "ref": SHA_A},
            {"registry": "https://example.com/example/repo.git", "ref": SHA_A},
            {"registry": "http://github.com/example/repo.git", "ref": SHA_A},
            {"registry": "https://github.com/example/repo/extra", "ref": SHA_A},
            {"registry": "https://github.com/example/repo.git\n", "ref": SHA_A},
            {"registry": "https://github.com/-example/repo", "ref": SHA_A},
            {"registry": GOOD["registry"], "ref": SHA_A.upper()},
            {"registry": GOOD["registry"], "ref": SHA_A[:39]},
            {"registry": GOOD["registry"], "ref": SHA_A + "\n"},
            {"registry": GOOD["registry"]},
            {"registry": 7, "ref": SHA_A},
            GOOD["registry"],
            None,
        ]})
        self.assertEqual(sources, [{"registry": "example/good", "ref": SHA_A}])
        self.assertEqual(dropped, 13)

    def test_a_seventeenth_source_is_dropped_and_counted(self):
        items = [{"registry": "https://github.com/example/r%02d.git" % n, "ref": SHA_A} for n in range(17)]
        sources, dropped = self.sources_for({"sources": list(reversed(items))})
        self.assertEqual(sources, [{"registry": "example/r%02d" % n, "ref": SHA_A} for n in range(16)])
        self.assertEqual(dropped, 1)

    def test_unreadable_install_records_are_null_and_the_record_is_still_written(self):
        cases = {
            "malformed": b"{not json",
            "deeply nested": b"[" * 100000 + b"]" * 100000,
            "over 1 MiB": json.dumps({"sources": [GOOD], "pad": "x" * (1 << 20)}).encode(),
            "not utf-8": b'{"sources": [], "x": "\xff"}',
            "not an object": json.dumps([GOOD]).encode(),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                self.install_record.write_bytes(raw)
                record = self.record_for(session="sources-" + name)
                self.assertEqual((record["sources"], record["sources_dropped"]), (None, None))
                self.assertEqual(record["state"], "stub-only")

    def test_a_directory_at_the_path_is_null(self):
        self.install_record.mkdir()
        self.assertEqual(self.sources_for(), (None, None))

    def test_a_fifo_at_the_path_is_null_and_does_not_block(self):
        # A FIFO with no writer blocks a plain open() for read; run_hook's
        # timeout would fail the test if the hook opened it.
        os.mkfifo(self.install_record)
        self.assertEqual(self.sources_for(), (None, None))

    def test_a_symlink_at_the_path_is_null(self):
        target = self.tmp / "elsewhere.json"
        target.write_text(json.dumps({"sources": [GOOD]}), encoding="utf-8")
        self.install_record.symlink_to(target)
        self.assertEqual(self.sources_for(), (None, None))

    def test_registry_sha_stays_null_beside_sources(self):
        self.install_record.write_text(json.dumps({"sources": [GOOD]}), encoding="utf-8")
        record = self.record_for()
        self.assertIsNone(record["registry_sha"])
        self.assertEqual(record["sources"], [{"registry": "example/good", "ref": SHA_A}])


class OncePerSessionTests(HookTestCase):
    def test_second_stop_adds_nothing(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook()
        self.run_hook()
        self.assertEqual(len(self.records()), 1)
        flag = self.state / "done" / hashlib.sha256(b"session-one").hexdigest()
        self.assertTrue(flag.is_file())

    def test_done_session_exits_before_reading_the_transcript(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook()
        # A FIFO with no writer blocks any open() for read, so a hook that
        # read the transcript again would hang until the subprocess timeout.
        fifo = self.tmp / "blocking.jsonl"
        os.mkfifo(fifo)
        out = subprocess.run(["bash", str(HOOK)], capture_output=True, text=True, env=self.env, timeout=20,
                             input=json.dumps({"session_id": "session-one", "transcript_path": str(fifo),
                                               "hook_event_name": "Stop"}))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertEqual(len(self.records()), 1)

    def test_other_sessions_still_record(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(session="one")
        self.run_hook(session="two")
        self.assertEqual(len(self.records()), 2)

    def test_bad_input_is_silent(self):
        self.run_hook(stdin="not json")
        self.run_hook(stdin="")
        self.run_hook(event={"session_id": "x", "transcript_path": str(self.tmp / "missing.jsonl"),
                             "hook_event_name": "Stop"})
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(event={"session_id": "x", "transcript_path": str(self.transcript),
                             "hook_event_name": "SessionStart"})
        self.assertEqual(self.records(), [])

    def test_no_python_exits_zero_silently(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        out = subprocess.run([shutil.which("bash"), str(HOOK)], input="{}", capture_output=True,
                             text=True, env={"PATH": str(empty), "HOME": str(self.home)}, timeout=30)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertEqual(self.records(), [])


class UploadTests(HookTestCase):
    def setUp(self):
        super().setUp()
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        # Retired variables an old environment may still hold: never used.
        self.creds = {"USAGE_WRITER_APP_CLIENT_ID": "Iv1.example0client",
                      "USAGE_WRITER_APP_PEM": "example-retired-app-key-canary"}

    def script(self, replies):
        self.curl_script.write_text(json.dumps(replies), encoding="utf-8")

    def test_not_attempted_when_not_remote(self):
        self.run_hook(env=self.creds)
        self.assertEqual(self.calls(), [])
        self.assertIsNone(self.records()[0]["uploaded"])

    def test_no_attached_repo_records_false_and_makes_no_app_calls(self):
        self.script([NOT_ATTACHED] * 3)
        self.run_hook(session="a", env={"CLAUDE_CODE_REMOTE": "true"})
        self.run_hook(session="b", env={"CLAUDE_CODE_REMOTE": "true",
                                        "USAGE_WRITER_APP_CLIENT_ID": "Iv1.example0client"})
        self.run_hook(session="c", env=dict(self.creds, CLAUDE_CODE_REMOTE="true"))
        shape = [(c["argv"][c["argv"].index("-X") + 1], c["argv"][-1]) for c in self.calls()]
        self.assertEqual(shape, [("GET", PROBE)] * 3)
        self.assertEqual([r["uploaded"] for r in self.records()], [False, False, False])

    def bare_remote(self, existing=None, reject_first_push=False):
        """A local bare repo standing in for the private repo's
        persistent/usage-events branch."""
        bare = self.tmp / "remote.git"
        seed = self.tmp / "seed"
        env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1")
        def git(*args, cwd=None):
            subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)
        git("init", "-q", "--bare", str(bare))
        git("init", "-q", "-b", "persistent/usage-events", str(seed))
        (seed / "README.md").write_text("events\n", encoding="utf-8")
        if existing is not None:
            (seed / "events" / "2026-10").mkdir(parents=True)
            (seed / "events" / "2026-10" / "cloud-guidance.jsonl").write_text(existing, encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("-c", "user.name=Example", "-c", "user.email=example@example.com",
            "commit", "-q", "-m", "seed", cwd=seed)
        git("push", "-q", str(bare), "persistent/usage-events", cwd=seed)
        if reject_first_push:
            hook = bare / "hooks" / "pre-receive"
            flag = self.tmp / "rejected-once"
            hook.write_text(f'#!/bin/sh\n[ -e "{flag}" ] && exit 0\ntouch "{flag}"\nexit 1\n',
                            encoding="utf-8")
            hook.chmod(0o755)
        return bare

    def remote_file(self, bare):
        out = subprocess.run(["git", "--git-dir", str(bare), "show",
                              "persistent/usage-events:events/2026-10/cloud-guidance.jsonl"],
                             capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None

    def test_session_credential_appends_with_git_when_the_repo_is_attached(self):
        # A hosted session's proxy substitutes its own credential for any
        # Authorization header and refuses contents-API writes, but allows a
        # git push; this path sends no Authorization header and needs no App.
        earlier = '{"v":1,"kind":"guidance-load","session":"0000000000000000"}'
        bare = self.bare_remote(existing=earlier)  # no trailing newline on purpose
        self.script([{"status": 200, "body": {"permissions": {"push": True, "pull": True}}}])
        self.run_hook(env=dict(self.creds, CLAUDE_CODE_REMOTE="true", GUIDANCE_RECORD_GIT=str(bare)))
        [record] = self.records()
        self.assertIs(record["uploaded"], True)
        calls = self.calls()
        self.assertEqual([(c["argv"][c["argv"].index("-X") + 1], c["argv"][-1]) for c in calls],
                         [("GET", PROBE)])
        self.assertNotIn("Authorization", calls[0]["stdin"])
        lines = self.remote_file(bare).splitlines()
        self.assertEqual(lines[0], earlier)
        self.assertEqual(json.loads(lines[1]), {k: v for k, v in record.items() if k != "uploaded"})
        self.assertEqual(len(lines), 2)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_the_uploaded_line_carries_the_check_fields_and_no_value(self):
        self.write_transcript([user(), instructions(
            memfile("/w/u/.claude/CLAUDE.md", "User", CHECK_FILE),
            memfile("/w/r/AGENTS.md", "Project", STUB)),
            assistant_text(1, f"fleet-guidance check: {OTHER}")])
        bare = self.bare_remote()
        self.script([{"status": 200, "body": {"permissions": {"push": True}}}])
        self.run_hook(env={"CLAUDE_CODE_REMOTE": "true", "GUIDANCE_RECORD_GIT": str(bare)})
        [line] = self.remote_file(bare).splitlines()
        uploaded = json.loads(line)
        self.assertEqual((uploaded["check_expected"], uploaded["check_reported"]), (True, "mismatch"))
        self.assertNotIn("uploaded", uploaded)
        for value in (CHECK, OTHER):
            self.assertNotIn(value, line)

    def test_git_path_creates_the_month_file_and_retries_a_rejected_push(self):
        bare = self.bare_remote(reject_first_push=True)
        self.script([{"status": 200, "body": {"permissions": {"push": True}}}])
        self.run_hook(env={"CLAUDE_CODE_REMOTE": "true", "GUIDANCE_RECORD_GIT": str(bare)})
        self.assertIs(self.records()[0]["uploaded"], True)
        self.assertEqual(len(self.remote_file(bare).splitlines()), 1)
        self.assertTrue((self.tmp / "rejected-once").exists())

    def test_git_path_failure_records_false(self):
        self.script([{"status": 200, "body": {"permissions": {"push": True}}}])
        self.run_hook(env={"CLAUDE_CODE_REMOTE": "true", "GUIDANCE_RECORD_GIT": str(self.tmp / "missing.git")})
        self.assertIs(self.records()[0]["uploaded"], False)

    def test_read_only_session_credential_records_false_and_never_uses_an_app(self):
        self.script([{"status": 200, "body": {"permissions": {"push": False, "pull": True}}}])
        self.run_hook(env=dict(self.creds, CLAUDE_CODE_REMOTE="true"))
        self.assertIs(self.records()[0]["uploaded"], False)
        self.assertEqual([c["argv"][-1] for c in self.calls()], [PROBE])
        for c in self.calls():
            self.assertNotIn("Bearer", c["stdin"])
            self.assertEqual(c["sink_env"], [])    # the retired App variables are stripped too
            self.assertNotIn("example-retired-app-key-canary", " ".join(c["argv"]) + c["stdin"])
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_the_hook_source_has_no_app_path_left(self):
        text = HOOK.read_text(encoding="utf-8")
        source = text.split("<<'PY' || true\n", 1)[1]
        for gone in ("openssl", "app_jwt", "app_token", "def append(", "access_tokens",
                     'os.environ.get("USAGE_WRITER_APP'):
            self.assertNotIn(gone, source)


# ── phase 2: cloud skill records (ADR 0036) ─────────────────────────────────

# The lock-kind digest's shared test vector: skills-evals
# test/issues/test_issue_skill_digest.py commits the same two constants for the
# same tree, so a change here is a change to the digest every skills.lock names.
VECTOR_ALL_FILES = "681c4d1df407b995d4a9b9d2dbf56a701aed027ce41258fd2868092f1997b4f2"
VECTOR_GIT_IGNORED_SKIPPED = "800541b28e245722ce9d8d6a6e69223ae8359b7b818a64c344ef060af52e2096"
SHARD_KEYS = {"v", "id", "ts", "kind", "surface", "session", "cli", "name", "base_kind",
              "registry", "ref", "digest", "digest_null_reason"}
CLOUD_SESSION = "cloud-session-canary-1"
CLOUD_SID = hashlib.sha256(CLOUD_SESSION.encode()).hexdigest()
USE_TS = "2099-01-01T00:00:00.000Z"
PUSH_OK = {"status": 200, "body": {"permissions": {"push": True, "pull": True}}}
REAL_GIT = shutil.which("git")

# The fake git: logs argv to $FAKE_GIT_LOG, then runs the real git, except
# that FAKE_GIT_MODE=fail-cat-file fails every cat-file, and hang-network
# makes clone, fetch and push hang with a child holding the pipe open, so only
# a process-group kill ends them.
FAKE_GIT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, subprocess, sys, time
    argv = sys.argv[1:]
    with open(os.environ["FAKE_GIT_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\\n")
    if "GIT_CONFIG_NOSYSTEM" in os.environ:
        with open(os.environ["FAKE_GIT_LOG"] + ".nosystem", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\\n")
    if any(k.startswith(("USAGE_SINK", "USAGE_WRITER_APP")) for k in os.environ):
        with open(os.environ["FAKE_GIT_LOG"] + ".sinkenv", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\\n")
    mode = os.environ.get("FAKE_GIT_MODE", "")
    if mode == "fail-cat-file" and "cat-file" in argv:
        sys.exit(1)
    if mode == "fail-shard-cat-file" and "cat-file" in argv and any("/cloud-skills/" in a for a in argv):
        sys.exit(1)
    if mode == "refuse-clone" and "clone" in argv:
        sys.exit(128)
    if mode == "hang-network" and any(a in argv for a in ("clone", "fetch", "push")):
        child = subprocess.Popen(["sleep", "300"])
        with open(os.environ["FAKE_GIT_LOG"] + ".children", "a", encoding="utf-8") as handle:
            handle.write("%d\\n" % child.pid)
        time.sleep(300)
    os.execv(os.environ["FAKE_GIT_REAL"], ["git"] + argv)
    """)

# The fake curl for the time-bound test: answers the probe with push true,
# but only after using all but 0.02 s of its --max-time.
SLOW_CURL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys, time
    argv = sys.argv[1:]
    sys.stdin.read()
    time.sleep(max(0, float(argv[argv.index("--max-time") + 1]) - 0.02))
    with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as handle:
        json.dump({"permissions": {"push": True}}, handle)
    sys.stdout.write("200")
    """)


def fixture_git(*args, cwd):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.com", GIT_COMMITTER_EMAIL="fixture@example.com")
    return subprocess.run([REAL_GIT, *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout


VECTOR_FILES = {
    ".gitignore": b"__pycache__/\n*.log\n",
    "SKILL.md": b"---\nname: vector\n---\n# Vector\n",
    "a/b/c.txt": b"nested\n",
    "has-nul-byte.bin": b"before\x00after",
    "données.md": "café\n".encode("utf-8"),
    "empty.txt": b"",
    "__pycache__/x.pyc": b"\x00compiled",
    "build/keep.log": b"kept though ignored\n",
}


def write_vector(root):
    for relpath, data in VECTOR_FILES.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def build_vector_repo(work, relpath):
    """skills-evals' build_vector_tree recipe, with the tree at `relpath`
    inside the git work tree `work`: `add .`, then the force-added ignored file."""
    root = write_vector(work / relpath)
    fixture_git("init", "-q", cwd=work)
    fixture_git("add", ".", cwd=root)
    fixture_git("add", "-f", "build/keep.log", cwd=root)
    fixture_git("commit", "-q", "-m", "vector", cwd=work)
    return root


def skill_use(call, skill, ts=USE_TS, session=CLOUD_SESSION):
    return {"type": "assistant", "sessionId": session, "timestamp": ts, "version": "2.1.312",
            "uuid": "a-" + call, "message": {"id": "msg_" + call, "role": "assistant", "content": [
                {"type": "tool_use", "id": call, "name": "Skill", "input": {"skill": skill}}]}}


def skill_result(call, session=CLOUD_SESSION):
    return {"type": "user", "sessionId": session, "timestamp": USE_TS, "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": call,
                                     "content": "Launching skill"}]}}


def base_dir(base, call=None, parent=None, session=CLOUD_SESSION):
    entry = {"type": "user", "isMeta": True, "sessionId": session, "timestamp": USE_TS,
             "message": {"role": "user", "content": [
                 {"type": "text", "text": f"Base directory for this skill: {base}\n\n# Body"}]}}
    if call:
        entry["sourceToolUseID"] = call
    if parent:
        entry["parentUuid"] = parent
    return entry


def typed(uuid, command, ts=USE_TS):
    return {"type": "user", "sessionId": CLOUD_SESSION, "timestamp": ts, "uuid": uuid,
            "version": "2.1.312", "message": {
                "role": "user", "content": f"<command-name>/{command}</command-name>"}}


def later_turn(n):
    return {"type": "assistant", "sessionId": CLOUD_SESSION, "timestamp": USE_TS,
            "message": {"id": f"msg_later_{n}", "role": "assistant",
                        "content": [{"type": "text", "text": "done"}]}}


class CloudTestCase(HookTestCase):
    def setUp(self):
        super().setUp()
        git = self.bin / "git"
        git.write_text(FAKE_GIT, encoding="utf-8")
        git.chmod(0o755)
        self.git_log = self.tmp / "git.log"
        self.env.update(FAKE_GIT_LOG=str(self.git_log), FAKE_GIT_REAL=REAL_GIT,
                        GIT_CONFIG_GLOBAL=os.devnull)
        self.bare = self.bare_remote()
        self.cloud_env = {"CLAUDE_CODE_REMOTE": "true", "GUIDANCE_RECORD_GIT": str(self.bare)}
        self.cloud = self.state / "cloud-skills"
        self.skills = self.home / ".claude" / "skills"
        self.skills.mkdir(parents=True)

    def bare_remote(self, rejections=0):
        bare = self.tmp / "remote.git"
        seed = self.tmp / "seed"
        fixture_git("init", "-q", "--bare", str(bare), cwd=self.tmp)
        fixture_git("init", "-q", "-b", "persistent/usage-events", str(seed), cwd=self.tmp)
        (seed / "README.md").write_text("events\n", encoding="utf-8")
        (seed / "events" / "2026-10").mkdir(parents=True)
        (seed / "events" / "2026-10" / "cloud-guidance.jsonl").write_text('{"v":1}\n', encoding="utf-8")
        (seed / "events" / "2026-10" / "a-machine.jsonl").write_text('{"v":1}\n', encoding="utf-8")
        fixture_git("add", "-A", cwd=seed)
        fixture_git("commit", "-q", "-m", "seed", cwd=seed)
        fixture_git("push", "-q", str(bare), "persistent/usage-events", cwd=seed)
        return bare

    def reject_pushes(self, count):
        counter = self.tmp / "rejections"
        counter.write_text("0", encoding="utf-8")
        hook = self.bare / "hooks" / "pre-receive"
        hook.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            n=$(cat "{counter}")
            [ "$n" -ge {count} ] && exit 0
            echo $((n + 1)) > "{counter}"
            exit 1
            """), encoding="utf-8")
        hook.chmod(0o755)
        return counter

    def seed_shard(self, text, month="2099-01"):
        work = self.tmp / "seed-shard"
        fixture_git("clone", "-q", "-b", "persistent/usage-events", str(self.bare), str(work), cwd=self.tmp)
        path = work / "events" / month / "cloud-skills" / f"{CLOUD_SID[:16]}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        fixture_git("add", "-A", cwd=work)
        fixture_git("commit", "-q", "-m", "shard", cwd=work)
        fixture_git("push", "-q", "origin", "persistent/usage-events", cwd=work)

    def done_phase1(self, session=CLOUD_SESSION):
        flag = self.state / "done" / hashlib.sha256(session.encode()).hexdigest()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()

    def script(self, replies):
        self.curl_script.write_text(json.dumps(replies), encoding="utf-8")

    def stop(self, env=None, session=CLOUD_SESSION, now=None):
        merged = dict(self.cloud_env, **(env or {}))
        if now:
            merged["GUIDANCE_RECORD_NOW"] = now
        return self.run_hook(session=session, env=merged)

    def remote_text(self, path):
        out = subprocess.run([REAL_GIT, "--git-dir", str(self.bare), "show",
                              f"persistent/usage-events:{path}"], capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None

    def remote_files(self):
        return set(subprocess.run([REAL_GIT, "--git-dir", str(self.bare), "ls-tree", "-r", "--name-only",
                                   "persistent/usage-events"], capture_output=True, text=True,
                                  check=True).stdout.split())

    def shard(self, month="2099-01"):
        text = self.remote_text(f"events/{month}/cloud-skills/{CLOUD_SID[:16]}.jsonl")
        return [json.loads(l) for l in text.splitlines()] if text is not None else []

    def pending(self):
        path = self.cloud / f"{CLOUD_SID}.pending.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def state_doc(self):
        return json.loads((self.cloud / f"{CLOUD_SID}.state.json").read_text())

    def git_calls(self):
        if not self.git_log.exists():
            return []
        return [json.loads(l) for l in self.git_log.read_text(encoding="utf-8").splitlines()]

    def commits(self):
        return int(subprocess.run([REAL_GIT, "--git-dir", str(self.bare), "rev-list", "--count",
                                   "persistent/usage-events"], capture_output=True, text=True,
                                  check=True).stdout)

    def use_with_base(self, call, skill, base, ts=USE_TS):
        return [skill_use(call, skill, ts), skill_result(call), base_dir(base, call=call)]


class CloudVectorTests(CloudTestCase):
    """The hook's digest is the #536 function: bound to the shared vector."""

    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.script([PUSH_OK])

    def test_user_skills_copy_has_the_all_files_digest_and_its_install_record(self):
        write_vector(self.skills / "vector")
        (self.skills / ".skills-bootstrap-installed.json").write_text(json.dumps({"installed": [
            {"name": "vector", "registry": "https://github.com/example/skills.git", "bundle": "b",
             "digest": "sha256:" + VECTOR_ALL_FILES, "ref": SHA_B}]}), encoding="utf-8")
        self.write_transcript(self.use_with_base("toolu_v1", "example:vector", self.skills / "vector"))
        self.stop()
        [line] = self.shard()
        self.assertEqual(line, {
            "v": 1, "id": "toolu_v1", "ts": "2099-01-01T00:00:00Z", "kind": "skill",
            "surface": "cloud", "session": CLOUD_SID[:16], "cli": "2.1.312",
            "name": "example:vector", "base_kind": "user-skills", "registry": "example/skills",
            "ref": SHA_B, "digest": VECTOR_ALL_FILES, "digest_null_reason": None})

    def test_repo_copy_skips_git_ignored_files_and_keeps_no_remote_url(self):
        checkout = self.tmp / "checkout"
        checkout.mkdir()
        root = build_vector_repo(checkout, ".claude/skills/vector")
        fixture_git("remote", "add", "origin", "http://user:tok-canary-9@127.0.0.1:1/git/o/r", cwd=checkout)
        self.write_transcript(self.use_with_base("toolu_v2", "vector", root))
        self.stop()
        [line] = self.shard()
        self.assertEqual((line["base_kind"], line["digest"], line["registry"], line["ref"]),
                         ("repo", VECTOR_GIT_IGNORED_SKIPPED, "o/r", None))
        text = self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl")
        for needle in ("tok-canary-9", "user:", "127.0.0.1", str(checkout)):
            self.assertNotIn(needle, text)

    def test_any_checkout_is_a_repo_copy(self):
        checkout = self.tmp / "elsewhere"
        checkout.mkdir()
        root = build_vector_repo(checkout, "skills/vector")
        self.write_transcript(self.use_with_base("toolu_v3", "vector", root))
        self.stop()
        [line] = self.shard()
        self.assertEqual((line["base_kind"], line["digest"], line["registry"]),
                         ("repo", VECTOR_GIT_IGNORED_SKIPPED, None))

    def test_a_copy_changed_after_the_use_has_no_digest(self):
        write_vector(self.skills / "vector")
        self.write_transcript(self.use_with_base("toolu_v4", "vector", self.skills / "vector",
                                                 ts="2000-01-01T00:00:00.000Z"))
        self.stop()
        [line] = self.shard("2000-01")
        self.assertEqual((line["digest"], line["digest_null_reason"]), (None, "changed-since-load"))

    def test_the_key_set_is_exactly_the_readers(self):
        # skills-evals' reader skips a whole line with any other key.
        write_vector(self.skills / "vector")
        self.write_transcript(self.use_with_base("toolu_k1", "vector", self.skills / "vector")
                              + [skill_use("toolu_k2", "no such/skill"), skill_result("toolu_k2"),
                                 skill_use("toolu_k3", "missing"), skill_result("toolu_k3")])
        self.stop()
        lines = self.shard()
        self.assertEqual([l["id"] for l in lines], ["toolu_k1", "toolu_k2", "toolu_k3"])
        for line in lines:
            self.assertEqual(set(line), SHARD_KEYS)
            self.assertEqual(line["session"], CLOUD_SID[:16])
            self.assertEqual(line["ts"][:7], "2099-01")
            self.assertTrue(line["ts"].endswith("Z"))
            self.assertIs(line["v"], 1)
        self.assertEqual((lines[1]["name"], lines[1]["digest_null_reason"]), ("invalid", "bad-name"))
        self.assertEqual(lines[2]["digest_null_reason"], "no-base-dir")


class CloudStorageTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        write_vector(self.skills / "vector")

    def test_every_other_file_on_the_branch_survives(self):
        self.done_phase1()
        self.script([PUSH_OK])
        before = self.remote_files()
        self.write_transcript(self.use_with_base("toolu_s1", "vector", self.skills / "vector"))
        self.stop()
        self.assertEqual(self.remote_files(),
                         before | {f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl"})
        self.assertEqual(self.remote_text("events/2026-10/a-machine.jsonl"), '{"v":1}\n')
        self.assertEqual(self.pending(), [])
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_each_use_goes_to_the_month_of_its_own_timestamp(self):
        self.done_phase1()
        self.script([PUSH_OK])
        self.write_transcript(
            [skill_use("toolu_m1", "a", ts="2099-01-31T23:59:59.000Z"), skill_result("toolu_m1"),
             skill_use("toolu_m2", "b", ts="2099-02-01T00:00:00.000Z"), skill_result("toolu_m2")])
        self.stop(now="2099-03-05T00:00:00Z")
        self.assertEqual([l["id"] for l in self.shard("2099-01")], ["toolu_m1"])
        self.assertEqual([l["id"] for l in self.shard("2099-02")], ["toolu_m2"])
        self.assertEqual(self.commits(), 2)

    def test_rejected_pushes_rebuild_on_the_new_tip_until_one_lands(self):
        self.done_phase1()
        self.script([PUSH_OK])
        counter = self.reject_pushes(2)
        self.write_transcript(self.use_with_base("toolu_r1", "vector", self.skills / "vector"))
        self.stop()
        self.assertEqual(counter.read_text().strip(), "2")
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_r1"])
        self.assertIn("events/2026-10/a-machine.jsonl", self.remote_files())
        self.assertEqual(sum(1 for c in self.git_calls() if "fetch" in c), 2)
        self.assertEqual(self.pending(), [])

    def test_an_existing_shard_is_extended_not_replaced_and_ids_are_not_repeated(self):
        self.done_phase1()
        self.script([PUSH_OK, PUSH_OK])
        self.write_transcript([skill_use("toolu_e1", "a"), skill_result("toolu_e1")])
        self.stop()
        self.write_transcript([skill_use("toolu_e1", "a"), skill_result("toolu_e1"),
                               skill_use("toolu_e2", "b"), skill_result("toolu_e2")])
        self.stop()
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_e1", "toolu_e2"])

    def test_a_failed_read_of_an_existing_shard_never_overwrites_it(self):
        self.done_phase1()
        self.script([PUSH_OK, PUSH_OK])
        self.write_transcript([skill_use("toolu_f1", "a"), skill_result("toolu_f1")])
        self.stop()
        before = self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl")
        head = self.commits()
        self.write_transcript([skill_use("toolu_f1", "a"), skill_result("toolu_f1"),
                               skill_use("toolu_f2", "b"), skill_result("toolu_f2")])
        self.stop(env={"FAKE_GIT_MODE": "fail-cat-file"})
        self.assertEqual(self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl"), before)
        self.assertEqual(self.commits(), head)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_f2"])
        self.assertEqual(self.state_doc()["fails"], 1)

    def test_phase_1_and_phase_2_share_one_commit(self):
        self.script([PUSH_OK])
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_c1", "a"), skill_result("toolu_c1")])
        self.stop()
        self.assertEqual(self.commits(), 2)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.remote_text("events/2026-10/cloud-guidance.jsonl").splitlines()), 2)
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_c1"])
        self.assertIs(self.records()[0]["uploaded"], True)
        # A later Stop pushes phase 2 alone.
        self.script([PUSH_OK])
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(skill_use("toolu_c2", "b")) + "\n" + json.dumps(skill_result("toolu_c2")) + "\n")
        self.stop()
        self.assertEqual(self.commits(), 3)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.remote_text("events/2026-10/cloud-guidance.jsonl").splitlines()), 2)

    def test_stale_temp_dirs_of_this_user_are_swept(self):
        self.done_phase1()
        self.script([PUSH_OK])
        old, fresh = self.tmpdir / "guidance-record.old", self.tmpdir / "guidance-record.fresh"
        old.mkdir()
        fresh.mkdir()
        os.utime(old, (1_000_000_000, 1_000_000_000))
        self.write_transcript([skill_use("toolu_t1", "a"), skill_result("toolu_t1")])
        self.stop()
        self.assertEqual(list(self.tmpdir.iterdir()), [fresh])

    def test_a_symlinked_tmpdir_still_uploads(self):
        # A symlinked temp root (macOS /tmp) is resolved once, not refused.
        self.script([PUSH_OK])
        link = self.tmp / "tlink"
        link.symlink_to(self.tmpdir)
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_l1", "a"), skill_result("toolu_l1")])
        self.stop(env={"TMPDIR": str(link)})
        self.assertIs(self.records()[0]["uploaded"], True)
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_l1"])
        self.assertEqual(self.pending(), [])
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_a_phase_2_failure_does_not_cost_phase_1_its_record(self):
        # The shard exists and cannot be read, so the combined commit aborts;
        # phase 1's file is then pushed alone.
        self.seed_shard('{"id":"toolu_old"}\n')
        before = self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl")
        self.script([PUSH_OK])
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_l2", "a"), skill_result("toolu_l2")])
        self.stop(env={"FAKE_GIT_MODE": "fail-shard-cat-file"})
        self.assertIs(self.records()[0]["uploaded"], True)
        self.assertEqual(len(self.remote_text("events/2026-10/cloud-guidance.jsonl").splitlines()), 2)
        self.assertEqual(self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl"), before)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_l2"])
        self.assertEqual(self.state_doc()["fails"], 1)

    def test_rebuilding_after_rejections_runs_until_the_deadline(self):
        # More rejections than any fixed retry count the ADR once named.
        self.done_phase1()
        self.script([PUSH_OK])
        counter = self.reject_pushes(7)
        self.write_transcript([skill_use("toolu_l3", "a"), skill_result("toolu_l3")])
        self.stop()
        self.assertEqual(counter.read_text().strip(), "7")
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_l3"])


class CloudWatermarkTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.script([PUSH_OK] * 6)

    def ids(self):
        return [l["id"] for l in self.shard()]

    def test_a_torn_last_line_waits_for_the_next_stop(self):
        whole = [skill_use("toolu_w1", "a"), skill_result("toolu_w1")]
        torn = json.dumps(skill_use("toolu_w2", "b"))
        self.transcript.write_text("".join(json.dumps(e) + "\n" for e in whole) + torn[:40],
                                   encoding="utf-8")
        self.stop()
        self.assertEqual(self.ids(), ["toolu_w1"])
        self.write_transcript(whole + [skill_use("toolu_w2", "b"), skill_result("toolu_w2")])
        self.stop()
        self.assertEqual(self.ids(), ["toolu_w1", "toolu_w2"])

    def test_a_replaced_or_truncated_transcript_is_read_again_without_repeats(self):
        entries = [skill_use("toolu_w3", "a"), skill_result("toolu_w3")]
        self.write_transcript(entries)
        self.stop()
        replacement = self.tmp / "replacement.jsonl"
        replacement.write_text("".join(json.dumps(e) + "\n" for e in entries
                                       + [skill_use("toolu_w4", "b"), skill_result("toolu_w4")]))
        os.replace(replacement, self.transcript)
        self.stop()
        self.write_transcript([skill_use("toolu_w5", "c"), skill_result("toolu_w5")])  # shorter
        self.stop()
        self.assertEqual(self.ids(), ["toolu_w3", "toolu_w4", "toolu_w5"])

    def test_an_undated_use_is_skipped(self):
        self.write_transcript([skill_use("toolu_d1", "a", ts="not a time"), skill_result("toolu_d1"),
                               skill_use("toolu_d2", "b", ts="1999-12-31T00:00:00.000Z"), skill_result("toolu_d2"),
                               skill_use("toolu_d3", "c"), skill_result("toolu_d3")])
        self.stop()
        self.assertEqual(self.ids(), ["toolu_d3"])
        self.assertEqual(self.state_doc()["seen"], ["toolu_d3"])

    def test_entries_of_another_session_are_ignored(self):
        self.write_transcript([skill_use("toolu_o1", "a", session="other"),
                               skill_result("toolu_o1", session="other"),
                               skill_use("toolu_o2", "b"), skill_result("toolu_o2")])
        self.stop()
        self.assertEqual(self.ids(), ["toolu_o2"])

    def test_an_unsettled_use_holds_the_watermark_until_it_settles(self):
        write_vector(self.skills / "vector")
        self.write_transcript([skill_use("toolu_u1", "vector")])
        self.stop()
        self.assertEqual((self.ids(), self.pending()), ([], []))
        self.write_transcript([skill_use("toolu_u1", "vector"),
                               base_dir(self.skills / "vector", call="toolu_u1")])
        self.stop()
        self.assertEqual([l["digest"] for l in self.shard()], [VECTOR_ALL_FILES])
        # Settled by a later turn alone: no base directory.
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(skill_use("toolu_u2", "other")) + "\n")
        self.stop()
        self.assertEqual(self.ids(), ["toolu_u1"])
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(later_turn(1)) + "\n")
        self.stop()
        self.assertEqual([(l["id"], l["digest_null_reason"]) for l in self.shard()],
                         [("toolu_u1", None), ("toolu_u2", "no-base-dir")])

    def test_at_most_fifty_uses_per_stop_and_the_rest_next_stop(self):
        entries = []
        for n in range(51):
            entries += [skill_use(f"toolu_n{n:02d}", "a"), skill_result(f"toolu_n{n:02d}")]
        self.write_transcript(entries)
        self.stop()
        self.assertEqual(len(self.ids()), 50)
        self.stop()
        self.assertEqual(self.ids(), [f"toolu_n{n:02d}" for n in range(51)])

    def test_subagent_transcripts_are_read_with_their_own_watermark(self):
        self.write_transcript([skill_use("toolu_p1", "a"), skill_result("toolu_p1")])
        sub = self.tmp / CLOUD_SESSION / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-1.jsonl").write_text(json.dumps(skill_use("toolu_p2", "b")) + "\n"
                                           + json.dumps(skill_result("toolu_p2")) + "\n")
        self.stop()
        self.assertEqual(self.ids(), ["toolu_p1", "toolu_p2"])
        self.assertEqual(set(self.state_doc()["files"]), {"main", "sub:agent-1.jsonl"})

    def test_typed_commands(self):
        write_vector(self.skills / "vector")
        (self.skills / ".skills-bootstrap-installed.json").write_text(json.dumps({"installed": [
            {"name": "listed", "registry": "https://github.com/example/skills", "digest": "0" * 64}]}))
        self.write_transcript([
            typed("11111111-aaaa-4aaa-8aaa-000000000001", "vector"),
            base_dir(self.skills / "vector", parent="11111111-aaaa-4aaa-8aaa-000000000001"),
            typed("11111111-aaaa-4aaa-8aaa-000000000002", "clear"),
            typed("11111111-aaaa-4aaa-8aaa-000000000003", "example:listed"),
            typed("11111111-aaaa-4aaa-8aaa-000000000004", "loop"),
            later_turn(1)])
        self.stop()
        self.assertEqual([(l["name"], l["digest"], l["digest_null_reason"], l["base_kind"]) for l in self.shard()], [
            ("vector", VECTOR_ALL_FILES, None, "user-skills"),
            ("example:listed", None, "no-base-dir", "unknown"),
            ("loop", None, "builtin", "builtin")])
        # Only recorded ids count as seen: the dropped /clear does not.
        self.assertEqual(self.state_doc()["seen"], [l["id"] for l in self.shard()])


class CloudRetryTests(CloudTestCase):
    T0 = "2026-10-31T12:00:00Z"

    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.write_transcript([skill_use("toolu_b1", "a"), skill_result("toolu_b1")])

    def at(self, seconds):
        import datetime
        base = datetime.datetime.strptime(self.T0, "%Y-%m-%dT%H:%M:%SZ")
        return (base + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def probes(self):
        return len(self.calls())

    def test_a_failure_backs_off_two_minutes_then_doubles(self):
        self.script([{"status": 500, "body": {}}] * 5)
        self.stop(now=self.at(0))
        self.assertEqual(self.probes(), 1)
        self.stop(now=self.at(119))
        self.assertEqual(self.probes(), 1)
        self.stop(now=self.at(121))
        self.assertEqual(self.probes(), 2)
        self.stop(now=self.at(121 + 239))
        self.assertEqual(self.probes(), 2)
        self.stop(now=self.at(121 + 241))
        self.assertEqual(self.probes(), 3)
        self.assertFalse(self.state_doc()["nocred"])
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_b1"])

    def test_success_then_the_next_stop_uploads_again(self):
        self.script([PUSH_OK, PUSH_OK])
        self.stop()
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(skill_use("toolu_b2", "b")) + "\n" + json.dumps(skill_result("toolu_b2")) + "\n")
        self.stop()
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_b1", "toolu_b2"])
        self.assertEqual(self.probes(), 2)
        self.stop()  # nothing pending: no call
        self.assertEqual(self.probes(), 2)

    def test_a_definite_no_stops_uploads_for_the_session(self):
        for name, reply in {"404": {"status": 404, "body": {}}, "403": NOT_ATTACHED,
                            "push false": {"status": 200, "body": {"permissions": {"push": False}}}}.items():
            with self.subTest(name=name):
                shutil.rmtree(self.cloud, ignore_errors=True)
                self.curl_log.unlink(missing_ok=True)
                self.script([reply, PUSH_OK])
                self.stop()
                self.assertTrue(self.state_doc()["nocred"])
                with open(self.transcript, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(skill_use("toolu_x" + name[:2].strip(), "b")) + "\n"
                                 + json.dumps(later_turn(9)) + "\n")
                self.stop(now=self.at(10_000))
                self.assertEqual(self.probes(), 1)
                self.assertEqual(self.shard(), [])

    def test_phase_2_network_calls_are_capped_at_six_seconds(self):
        self.script([PUSH_OK])
        self.stop()
        [call] = self.calls()
        self.assertLessEqual(int(call["argv"][call["argv"].index("--max-time") + 1]), 6)
        self.assertNotIn("Authorization", call["stdin"])


class CloudSeamTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.write_transcript([skill_use("toolu_q1", "a"), skill_result("toolu_q1")])

    def max_time(self, env):
        self.script([{"status": 500, "body": {}}])
        shutil.rmtree(self.cloud, ignore_errors=True)
        self.curl_log.unlink(missing_ok=True)
        self.stop(env=env)
        [call] = self.calls()
        return call["argv"][call["argv"].index("--max-time") + 1]

    def test_the_budget_scale_needs_the_test_marker_and_only_shrinks(self):
        self.assertIn(self.max_time({"GUIDANCE_RECORD_TEST": "0", "GUIDANCE_RECORD_TEST_SCALE": "0.1"}),
                      ("5", "6"))
        self.assertIn(self.max_time({"GUIDANCE_RECORD_TEST": "1", "GUIDANCE_RECORD_TEST_SCALE": "5"}), ("5", "6"))
        scaled = float(self.max_time({"GUIDANCE_RECORD_TEST": "1", "GUIDANCE_RECORD_TEST_SCALE": "0.1"}))
        self.assertTrue(0.4 < scaled <= 0.6, scaled)

    def test_leaked_api_and_git_seams_are_ignored_without_the_marker(self):
        # A real session that happens to carry these variables must still talk
        # to GitHub and the real private repo (both faked here: no network).
        self.script([PUSH_OK])
        self.stop(env={"GUIDANCE_RECORD_TEST": "0", "FAKE_GIT_MODE": "refuse-clone"})
        [call] = self.calls()
        self.assertEqual(call["argv"][-1], f"https://api.github.com/repos/{REPO}")
        [clone] = [c for c in self.git_calls() if "clone" in c]
        self.assertIn(f"https://github.com/{REPO}", clone)
        self.assertNotIn(str(self.bare), clone)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_q1"])

    def test_git_keeps_the_system_config(self):
        # The hosted proxy may rely on system git config (an insteadOf rule, a
        # CA); the measured cloud probe ran with it.
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_q1"])
        self.assertTrue(self.git_calls())
        self.assertFalse(pathlib.Path(str(self.git_log) + ".nosystem").exists())


class CloudLockTests(CloudTestCase):
    def test_a_held_lock_skips_phase_2_and_phase_1_still_records(self):
        import fcntl
        self.script([PUSH_OK])
        self.cloud.mkdir(parents=True, mode=0o700)
        fd = os.open(self.cloud / f"{CLOUD_SID}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_h1", "a"), skill_result("toolu_h1")])
        self.stop()
        [record] = self.records()
        self.assertIs(record["uploaded"], True)
        self.assertFalse((self.cloud / f"{CLOUD_SID}.pending.jsonl").exists())
        self.assertEqual(self.shard(), [])
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_h1"])

    def test_state_files_are_private(self):
        self.done_phase1()
        self.script([{"status": 500, "body": {}}])
        self.write_transcript([skill_use("toolu_h2", "a"), skill_result("toolu_h2")])
        self.stop()
        self.assertEqual(stat_mode(self.cloud), 0o700)
        for path in self.cloud.iterdir():
            self.assertEqual(stat_mode(path), 0o600, path.name)

    def test_a_symlinked_state_dir_is_refused(self):
        self.done_phase1()
        elsewhere = self.tmp / "elsewhere-state"
        elsewhere.mkdir()
        self.state.mkdir(parents=True, exist_ok=True)
        self.cloud.symlink_to(elsewhere)
        self.write_transcript([skill_use("toolu_h3", "a"), skill_result("toolu_h3")])
        self.stop()
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.calls(), [])


def alive(pid):
    """True when `pid` runs and is not a zombie. Only probes (signal 0), and
    only a real child pid."""
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


def stat_mode(path):
    return os.lstat(path).st_mode & 0o777


class CloudSafetyTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.script([PUSH_OK])

    def reason_for(self, base, name="vector"):
        self.write_transcript(self.use_with_base("toolu_z1", name, base))
        self.stop()
        [line] = self.shard()
        return line["digest_null_reason"]

    def test_a_link_to_outside_the_roots_is_not_read(self):
        outside = self.tmp / "outside" / "vector"
        write_vector(outside)
        (outside / "SKILL.md").chmod(0)       # read, it would be `gone`
        (self.skills / "vector").symlink_to(outside)
        self.assertEqual(self.reason_for(self.skills / "vector"), "outside-known-roots")

    def test_a_base_dir_named_otherwise_is_no_base_dir(self):
        write_vector(self.skills / "other")
        self.assertEqual(self.reason_for(self.skills / "other"), "no-base-dir")

    def test_a_fifo_in_the_tree_is_refused_without_blocking(self):
        write_vector(self.skills / "vector")
        os.mkfifo(self.skills / "vector" / "pipe")
        self.assertEqual(self.reason_for(self.skills / "vector"), "refused-by-registry-rules")

    def test_a_link_inside_the_tree_is_refused(self):
        write_vector(self.skills / "vector")
        (self.skills / "vector" / "link").symlink_to(self.tmp)
        self.assertEqual(self.reason_for(self.skills / "vector"), "refused-by-registry-rules")

    def test_a_tree_past_two_thousand_files_is_too_large(self):
        root = self.skills / "vector"
        root.mkdir()
        for n in range(2001):
            (root / f"f{n}").write_bytes(b"")
        self.assertEqual(self.reason_for(root), "too-large")

    def test_a_relative_or_missing_base_dir(self):
        self.assertEqual(self.reason_for("relative/vector"), "outside-known-roots")

    def test_plugin_cache_copy_names_its_marketplace_and_commit(self):
        plugins = self.home / ".claude" / "plugins"
        version = plugins / "cache" / "mkt" / "plug" / "1.0.0"
        write_vector(version / "skills" / "vector")
        (plugins / "known_marketplaces.json").write_text(json.dumps(
            {"mkt": {"source": {"source": "github", "repo": "example/market"}}}))
        (plugins / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"plug@mkt": [{"installPath": str(version), "gitCommitSha": SHA_A}]}}))
        self.write_transcript(self.use_with_base("toolu_z2", "plug:vector", version / "skills" / "vector"))
        self.stop()
        [line] = self.shard()
        self.assertEqual((line["base_kind"], line["registry"], line["ref"], line["digest"]),
                         ("plugin-cache", "example/market", SHA_A, VECTOR_ALL_FILES))

    def test_the_shard_holds_no_path_title_or_session_id(self):
        write_vector(self.skills / "vector")
        self.write_transcript(self.use_with_base("toolu_z3", "vector", self.skills / "vector") + [
            {"type": "summary", "sessionId": CLOUD_SESSION, "summary": "TITLE-CANARY-5"}])
        self.stop()
        text = self.remote_text(f"events/2099-01/cloud-skills/{CLOUD_SID[:16]}.jsonl")
        for needle in (str(self.home), str(self.tmp), "skills/vector", "TITLE-CANARY-5",
                       CLOUD_SESSION, "# Body", "Launching skill"):
            self.assertNotIn(needle, text)


class CloudGateTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.script([PUSH_OK] * 3)
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_g1", "a"), skill_result("toolu_g1")])

    def test_a_local_session_does_no_phase_2_work(self):
        self.run_hook(session=CLOUD_SESSION)
        self.assertEqual((self.calls(), self.git_calls()), ([], []))
        self.assertFalse(self.cloud.exists())
        self.assertEqual(len(self.records()), 1)

    def test_remote_session_id_alone_does_no_phase_2_work(self):
        self.done_phase1()
        self.run_hook(session=CLOUD_SESSION, env={"CLAUDE_CODE_REMOTE_SESSION_ID": "example-remote",
                                                  "GUIDANCE_RECORD_GIT": str(self.bare)})
        self.assertEqual((self.calls(), self.git_calls()), ([], []))
        self.assertFalse(self.cloud.exists())
        self.assertFalse((self.state / "sessions.jsonl").exists())

    def test_phase_2_still_runs_after_phase_1_is_done(self):
        self.stop()
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(skill_use("toolu_g2", "b")) + "\n" + json.dumps(skill_result("toolu_g2")) + "\n")
        self.stop()
        self.assertEqual(len(self.records()), 1)
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_g1", "toolu_g2"])

    def test_phase_1_and_phase_2_stay_under_the_stop_timeout_when_every_call_hangs(self):
        # Every curl uses its whole --max-time; every clone, fetch and push
        # hangs with a child holding its pipe. The hook must still end inside
        # the 20-second Stop timeout. Every budget is scaled by SCALE (the
        # test-only seam), so the bound checked is 20 s * SCALE.
        scale = 0.12
        (self.bin / "curl").write_text(SLOW_CURL, encoding="utf-8")
        import time
        began = time.monotonic()
        self.stop(env={"FAKE_GIT_MODE": "hang-network", "GUIDANCE_RECORD_TEST": "1",
                       "GUIDANCE_RECORD_TEST_SCALE": str(scale)})
        self.assertLess(time.monotonic() - began, 20 * scale)
        # It did wait on the hung calls, rather than skip them.
        self.assertTrue(any("clone" in c for c in self.git_calls()))
        # The timeout killed the whole process group, the hung child included.
        children = [int(p) for p in pathlib.Path(str(self.git_log) + ".children").read_text().split()]
        self.assertTrue(children)
        for pid in children:
            self.assertFalse(alive(pid), pid)
        self.assertIs(self.records()[0]["uploaded"], False)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_g1"])



# ── the S3 sink (ADR 0037) ──────────────────────────────────────────────────

SINK_ENV = {"USAGE_SINK_BUCKET": "example-usage-sink", "USAGE_SINK_REGION": "us-east-1",
            "USAGE_SINK_ACCESS_KEY_ID": "AKIAEXAMPLEEXAMPLE00",
            "USAGE_SINK_SECRET_ACCESS_KEY": "example-sink-canary-value"}
SINK_KEY = re.compile(r"^drop/\d{4}-\d{2}-\d{2}/[0-9a-f]{32}\.json$")
SINK_BODY_KEYS = ["v", "kind", "sent", "guidance", "skills"]


def hook_module():
    """The hook's embedded Python, loaded as a module: main() does not run."""
    text = HOOK.read_text(encoding="utf-8")
    source = text.split("<<'PY' || true\n", 1)[1].rsplit("\nPY\n", 1)[0]
    namespace = {"__name__": "guidance_hook"}
    exec(compile(source, str(HOOK), "exec"), namespace)
    return namespace


def expected_signature(method, path, headers, signed_names, secret, region):
    """SigV4 recomputed independently of the hook, from the request the
    server received."""
    canonical_headers = "".join("%s:%s\n" % (n, " ".join(headers[n].split())) for n in signed_names)
    canonical = "\n".join([method, path, "", canonical_headers, ";".join(signed_names),
                           headers["x-amz-content-sha256"]])
    stamp = headers["x-amz-date"]
    scope = f"{stamp[:8]}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret).encode()
    for part in (stamp[:8], region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


class FakeS3(http.server.BaseHTTPRequestHandler):
    """Answers each PUT from the server's scripted statuses (200 once they
    run out) and records the request."""

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.requests.append({"path": self.path, "body": body,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
        status = self.server.statuses.pop(0) if self.server.statuses else 200
        # A scripted "403-proxy" is a 403 without S3's request id header.
        from_s3 = status != "403-proxy"
        self.send_response(403 if status == "403-proxy" else status)
        if from_s3:
            self.send_header("x-amz-request-id", "EXAMPLEREQUESTID0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class SinkTestCase(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.server = http.server.HTTPServer(("127.0.0.1", 0), FakeS3)
        self.server.requests, self.server.statuses = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.endpoint = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.cloud_env.update(SINK_ENV, USAGE_SINK_ENDPOINT=self.endpoint)

    def puts(self):
        return self.server.requests

    def object_key(self, request):
        prefix = "/" + SINK_ENV["USAGE_SINK_BUCKET"] + "/"
        self.assertTrue(request["path"].startswith(prefix), request["path"])
        return request["path"][len(prefix):]

    def body(self, request):
        doc = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(list(doc), SINK_BODY_KEYS)
        return doc

    def add_use(self, call, skill="b"):
        with open(self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(skill_use(call, skill)) + "\n" + json.dumps(skill_result(call)) + "\n")


class SinkSignatureTests(unittest.TestCase):
    def test_sigv4_matches_aws_published_s3_examples(self):
        # AWS's S3 "Signature Calculations for the Authorization Header"
        # examples (example credentials, 2013-05-24, us-east-1).
        sign = hook_module()["sigv4_authorization"]
        aws_id, aws_word = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        payload = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
        self.assertEqual(payload, "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072")
        put = sign("PUT", "/test%24file.text", "", {
            "Host": "examplebucket.s3.amazonaws.com", "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": "20130524T000000Z", "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": payload}, payload, "us-east-1", "s3", aws_id, aws_word)
        self.assertEqual(put, "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/"
                              "aws4_request, SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;"
                              "x-amz-storage-class, Signature="
                              "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")
        empty = hashlib.sha256(b"").hexdigest()
        get = sign("GET", "/test.txt", "", {
            "Host": "examplebucket.s3.amazonaws.com", "Range": "bytes=0-9",
            "x-amz-content-sha256": empty, "x-amz-date": "20130524T000000Z"},
            empty, "us-east-1", "s3", aws_id, aws_word)
        self.assertTrue(get.endswith(
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), get)


class SinkRouteTests(SinkTestCase):
    def setUp(self):
        super().setUp()
        self.script([PUSH_OK] * 3)
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_k1", "a"), skill_result("toolu_k1")])

    def test_the_sink_is_chosen_only_when_all_four_variables_are_set(self):
        for name in SINK_ENV:
            for missing in (None, ""):
                with self.subTest(name=name, value=missing):
                    shutil.rmtree(self.state, ignore_errors=True)
                    self.curl_log.unlink(missing_ok=True)
                    self.script([PUSH_OK])
                    env = dict(self.cloud_env)
                    if missing is None:
                        env.pop(name)
                    else:
                        env[name] = missing
                    self.run_hook(session=CLOUD_SESSION, env=env)
                    self.assertEqual(self.puts(), [])
                    self.assertEqual(len(self.calls()), 1)       # the git route's probe
        shutil.rmtree(self.state, ignore_errors=True)
        self.curl_log.unlink(missing_ok=True)
        self.git_log.unlink(missing_ok=True)
        before = self.commits()
        self.stop()
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual((self.calls(), self.commits()), ([], before))
        self.assertFalse([c for c in self.git_calls() if {"clone", "push", "fetch"} & set(c)])

    def test_one_object_carries_both_phases_with_exactly_the_spec_keys(self):
        self.stop()
        [put] = self.puts()
        doc = self.body(put)
        self.assertEqual((doc["v"], doc["kind"], doc["sent"]), (1, "usage-drop", NOW))
        [record] = self.records()
        self.assertIs(record["uploaded"], True)
        # The guidance item is the git route's line: the record without `uploaded`,
        # byte for byte.
        line = json.dumps({k: v for k, v in record.items() if k != "uploaded"}, separators=(",", ":"))
        self.assertIn(b'"guidance":[' + line.encode() + b"]", put["body"])
        self.assertEqual(doc["guidance"], [json.loads(line)])
        [skill] = doc["skills"]
        self.assertEqual(set(skill), SHARD_KEYS)
        self.assertEqual((skill["id"], skill["session"]), ("toolu_k1", CLOUD_SID[:16]))
        self.assertEqual(self.pending(), [])

    def test_the_request_has_a_valid_key_headers_and_signature(self):
        self.stop()
        [put] = self.puts()
        key = self.object_key(put)
        self.assertRegex(key, SINK_KEY)
        self.assertTrue(key.startswith("drop/2026-10-31/"))
        headers = put["headers"]
        self.assertEqual(headers["if-none-match"], "*")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(put["body"]).hexdigest())
        self.assertEqual(headers["x-amz-date"], "20261031T120000Z")
        found = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/20261031/us-east-1/s3/aws4_request, "
                             r"SignedHeaders=([a-z0-9;-]+), Signature=([0-9a-f]{64})",
                             headers["authorization"])
        self.assertIsNotNone(found, headers["authorization"])
        self.assertEqual(found.group(1), SINK_ENV["USAGE_SINK_ACCESS_KEY_ID"])
        names = found.group(2).split(";")
        self.assertEqual(names, ["content-type", "host", "if-none-match", "x-amz-content-sha256",
                                 "x-amz-date"])
        self.assertEqual(found.group(3), expected_signature(
            "PUT", put["path"], headers, names, SINK_ENV["USAGE_SINK_SECRET_ACCESS_KEY"], "us-east-1"))

    def test_the_secret_is_never_written_or_printed(self):
        out = self.stop()
        self.assertEqual((out.stdout, out.stderr), ("", ""))
        secret = SINK_ENV["USAGE_SINK_SECRET_ACCESS_KEY"].encode()
        for path in self.tmp.rglob("*"):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(secret, path.read_bytes(), path)
        self.assertNotIn(secret, self.puts()[0]["body"])
        self.assertNotIn(secret.decode(), json.dumps(self.puts()[0]["headers"]))

    def test_phase_1_alone_sends_an_empty_skills_list(self):
        self.run_hook(session=CLOUD_SESSION, env=dict(
            {k: v for k, v in self.cloud_env.items() if k != "CLAUDE_CODE_REMOTE"},
            CLAUDE_CODE_REMOTE_SESSION_ID="example-remote"))
        [put] = self.puts()
        doc = self.body(put)
        self.assertEqual((len(doc["guidance"]), doc["skills"]), (1, []))
        self.assertIs(self.records()[0]["uploaded"], True)

    def test_a_later_stop_sends_phase_2_alone(self):
        self.stop()
        self.add_use("toolu_k2")
        self.stop()
        self.assertEqual(len(self.puts()), 2)
        doc = self.body(self.puts()[1])
        self.assertEqual(doc["guidance"], [])
        self.assertEqual([s["id"] for s in doc["skills"]], ["toolu_k2"])
        self.assertEqual(len(self.records()), 1)


class SinkProxyModeTests(SinkTestCase):
    """USAGE_SINK_SIGNING=proxy: the agent proxy signs, so no key is here."""

    PROXY_ENV = {"USAGE_SINK_BUCKET": "example-usage-sink", "USAGE_SINK_REGION": "us-east-1",
                 "USAGE_SINK_SIGNING": "proxy"}

    def setUp(self):
        super().setUp()
        for name in ("USAGE_SINK_ACCESS_KEY_ID", "USAGE_SINK_SECRET_ACCESS_KEY"):
            self.cloud_env.pop(name)
        self.cloud_env.update(self.PROXY_ENV)
        self.script([PUSH_OK] * 3)
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB)),
                               skill_use("toolu_k1", "a"), skill_result("toolu_k1")])

    def test_the_request_is_unsigned_but_carries_the_other_headers(self):
        self.stop()
        [put] = self.puts()
        headers = put["headers"]
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-amz-date", headers)
        self.assertEqual(headers["if-none-match"], "*")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(put["body"]).hexdigest())
        self.assertRegex(self.object_key(put), SINK_KEY)
        self.assertIs(self.records()[0]["uploaded"], True)
        self.assertEqual(self.calls(), [])               # no GitHub call at all

    def test_only_bucket_region_and_signing_are_needed(self):
        for name in ("USAGE_SINK_BUCKET", "USAGE_SINK_REGION"):
            with self.subTest(missing=name):
                shutil.rmtree(self.state, ignore_errors=True)
                self.curl_log.unlink(missing_ok=True)
                self.script([PUSH_OK])
                before = len(self.puts())
                self.run_hook(session=CLOUD_SESSION,
                              env={k: v for k, v in self.cloud_env.items() if k != name})
                self.assertEqual(len(self.puts()), before)
                self.assertEqual(len(self.calls()), 1)    # fell to the git route's probe
        shutil.rmtree(self.state, ignore_errors=True)
        self.curl_log.unlink(missing_ok=True)
        self.stop()
        self.assertEqual(len(self.puts()), 1)

    def test_proxy_mode_wins_over_static_keys(self):
        self.cloud_env.update(SINK_ENV, USAGE_SINK_SIGNING="proxy")
        self.stop()
        [put] = self.puts()
        self.assertNotIn("authorization", put["headers"])
        self.assertNotIn("x-amz-date", put["headers"])

    def test_any_other_signing_value_uses_static_keys_or_git(self):
        for value in ("", "Proxy", "proxy ", "static", "1"):
            with self.subTest(value=value):
                shutil.rmtree(self.state, ignore_errors=True)
                self.curl_log.unlink(missing_ok=True)
                self.script([PUSH_OK])
                before = len(self.puts())
                self.run_hook(session=CLOUD_SESSION, env=dict(self.cloud_env, USAGE_SINK_SIGNING=value))
                self.assertEqual(len(self.puts()), before)   # no keys: the git route
                self.assertEqual(len(self.calls()), 1)
                shutil.rmtree(self.state, ignore_errors=True)
                self.run_hook(session=CLOUD_SESSION, env=dict(self.cloud_env, USAGE_SINK_SIGNING=value,
                                                              **{k: v for k, v in SINK_ENV.items()}))
                put = self.puts()[-1]
                self.assertTrue(put["headers"]["authorization"].startswith("AWS4-HMAC-SHA256 "))
                self.assertIn("x-amz-date", put["headers"])

    def test_status_handling_is_unchanged(self):
        self.server.statuses = [412, 200]
        self.stop()
        keys = [self.object_key(p) for p in self.puts()]
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])
        for put in self.puts():
            self.assertNotIn("authorization", put["headers"])
        self.assertIs(self.records()[0]["uploaded"], True)

    def test_a_malformed_bucket_sends_nothing(self):
        self.cloud_env["USAGE_SINK_BUCKET"] = "example.usage.sink"
        self.stop()
        self.assertEqual(self.puts(), [])


class SinkStatusTests(SinkTestCase):
    T0 = NOW

    def setUp(self):
        super().setUp()
        self.done_phase1()
        self.write_transcript([skill_use("toolu_z1", "a"), skill_result("toolu_z1")])

    def at(self, seconds):
        import datetime
        base = datetime.datetime.strptime(self.T0, "%Y-%m-%dT%H:%M:%SZ")
        return (base + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_200_clears_the_pending_lines(self):
        self.stop()
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.state_doc()["fails"], 0)
        self.stop()                                           # nothing pending: no request
        self.assertEqual(len(self.puts()), 1)

    def test_412_retries_once_under_a_new_key(self):
        self.server.statuses = [412, 200]
        self.stop()
        first, second = self.puts()
        self.assertNotEqual(self.object_key(first), self.object_key(second))
        self.assertRegex(self.object_key(second), SINK_KEY)
        self.assertEqual(self.pending(), [])

    def test_a_second_412_is_a_failure_for_the_backoff(self):
        self.server.statuses = [412, 412]
        self.stop()
        self.assertEqual(len(self.puts()), 2)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1"])
        self.assertEqual(self.state_doc()["fails"], 1)

    def test_403_stops_tries_for_the_session_and_keeps_the_lines(self):
        self.server.statuses = [403]
        self.stop()
        self.assertTrue(self.state_doc()["nocred"])
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1"])
        self.add_use("toolu_z2")
        self.stop(now=self.at(10_000))
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1", "toolu_z2"])

    def test_a_5xx_backs_off_two_minutes_then_doubles(self):
        self.server.statuses = [503, 500, 502]
        self.stop(now=self.at(0))
        self.assertEqual(len(self.puts()), 1)
        self.stop(now=self.at(119))
        self.assertEqual(len(self.puts()), 1)
        self.stop(now=self.at(121))
        self.assertEqual(len(self.puts()), 2)
        self.stop(now=self.at(121 + 239))
        self.assertEqual(len(self.puts()), 2)
        self.stop(now=self.at(121 + 241))
        self.assertEqual(len(self.puts()), 3)
        self.assertFalse(self.state_doc()["nocred"])
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1"])

    def test_no_answer_is_a_failure_for_the_backoff(self):
        self.cloud_env["USAGE_SINK_ENDPOINT"] = "http://127.0.0.1:9"     # nothing listens
        self.stop()
        self.assertEqual(self.state_doc()["fails"], 1)
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1"])

    def test_a_malformed_bucket_sends_nothing_and_is_a_definite_no(self):
        self.cloud_env["USAGE_SINK_BUCKET"] = "example.usage.sink"
        self.stop()
        self.assertEqual(self.puts(), [])
        self.assertTrue(self.state_doc()["nocred"])
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_z1"])

    def test_a_large_backlog_is_split_into_objects_within_the_limit(self):
        self.stop()                                           # sets up the state dir
        pad = "x" * 900
        lines = [json.dumps({"v": 1, "id": "toolu_big%04d" % n, "ts": "2099-01-01T00:00:00Z",
                             "kind": "skill", "name": pad}, separators=(",", ":")) for n in range(400)]
        (self.cloud / f"{CLOUD_SID}.pending.jsonl").write_text("".join(l + "\n" for l in lines))
        self.server.requests.clear()
        self.stop()
        self.assertGreater(len(self.puts()), 1)
        sent = []
        for put in self.puts():
            self.assertLessEqual(len(put["body"]), 262144)
            sent += [s["id"] for s in self.body(put)["skills"]]
        self.assertEqual(sent, ["toolu_big%04d" % n for n in range(400)])
        self.assertEqual(self.pending(), [])


class SinkHardeningTests(SinkTestCase):
    """The fixes from the 2026-11-03 review of the sink (M2, M7, L1-L3)."""

    T0 = NOW

    def at(self, seconds):
        import datetime
        base = datetime.datetime.strptime(self.T0, "%Y-%m-%dT%H:%M:%SZ")
        return (base + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def phase1_transcript(self, entrypoint="remote_projects", version="2.1.312"):
        entry = instructions(memfile("/w/r/AGENTS.md", "Project", STUB))
        entry.update(entrypoint=entrypoint, version=version)
        use = skill_use("toolu_h1", "a")
        use["version"] = version
        self.write_transcript([entry, use, skill_result("toolu_h1")])

    def held(self):
        return self.cloud / f"{CLOUD_SID}.guidance.pending"

    def test_a_403_without_the_s3_request_id_is_an_ordinary_failure(self):
        self.done_phase1()
        self.write_transcript([skill_use("toolu_p1", "a"), skill_result("toolu_p1")])
        self.server.statuses = ["403-proxy", 200]
        self.stop(now=self.at(0))
        state = self.state_doc()
        self.assertEqual((state["nocred"], state["fails"]), (False, 1))
        self.assertEqual([p["id"] for p in self.pending()], ["toolu_p1"])
        self.stop(now=self.at(121))                           # backed off, then tried again
        self.assertEqual(len(self.puts()), 2)
        self.assertEqual(self.pending(), [])

    def test_surface_and_cli_the_reader_would_refuse_are_sent_as_null(self):
        self.phase1_transcript(entrypoint="remote projects", version="2.1.312 (Claude Code)")
        self.stop()
        doc = self.body(self.puts()[0])
        [guidance] = doc["guidance"]
        self.assertEqual((guidance["surface"], guidance["cli"]), (None, None))
        self.assertEqual(doc["skills"][0]["cli"], None)
        [record] = self.records()
        self.assertEqual((record["surface"], record["cli"]), (None, None))

    def test_the_git_route_sends_the_same_clamped_line(self):
        for name in SINK_ENV:
            self.cloud_env.pop(name)
        self.script([PUSH_OK])
        self.phase1_transcript(entrypoint="remote projects", version="2.1.312 (Claude Code)")
        self.stop()
        [record] = self.records()
        line = json.dumps({k: v for k, v in record.items() if k != "uploaded"}, separators=(",", ":"))
        self.assertEqual(self.remote_text("events/2026-10/cloud-guidance.jsonl").splitlines()[-1], line)
        self.assertIn('"surface":null', line)
        self.assertIn('"cli":null', line)

    def test_valid_surface_and_cli_are_kept(self):
        self.phase1_transcript()
        self.stop()
        [guidance] = self.body(self.puts()[0])["guidance"]
        self.assertEqual((guidance["surface"], guidance["cli"]), ("remote_projects", "2.1.312"))

    def test_the_key_pattern_takes_ascii_digits_only(self):
        hook = hook_module()
        good = "drop/2026-11-03/" + "0" * 32 + ".json"
        self.assertIsNotNone(hook["SINK_KEY_RE"].fullmatch(good))
        self.assertIsNone(hook["SINK_KEY_RE"].fullmatch(
            "drop/٢٠٢٦-١١-٠٣/" + "0" * 32 + ".json"))
        self.assertIsNone(hook["SINK_SENT_RE"].fullmatch("٢٠٢٦-11-03T00:00:00Z"))

    def test_phase_1_that_did_not_land_is_held_and_sent_again(self):
        self.phase1_transcript()
        self.server.statuses = [503]
        self.stop(now=self.at(0))
        [record] = self.records()
        self.assertIs(record["uploaded"], False)
        line = json.dumps({k: v for k, v in record.items() if k != "uploaded"}, separators=(",", ":"))
        self.assertEqual(self.held().read_text(), line + "\n")
        self.stop(now=self.at(60))                            # still backing off
        self.assertEqual(len(self.puts()), 1)
        self.stop(now=self.at(121))
        retry = self.puts()[1]
        self.assertIn(b'"guidance":[' + line.encode() + b"]", retry["body"])
        self.assertEqual([s["id"] for s in self.body(retry)["skills"]], ["toolu_h1"])
        self.assertFalse(self.held().exists())
        self.assertEqual(self.pending(), [])
        self.stop(now=self.at(500))                           # nothing left: no request
        self.assertEqual(len(self.puts()), 2)
        self.assertEqual(len(self.records()), 1)

    def test_a_held_phase_1_line_waits_out_a_definite_no(self):
        self.phase1_transcript()
        self.server.statuses = [403]
        self.stop(now=self.at(0))
        self.assertTrue(self.held().exists())
        self.stop(now=self.at(10_000))
        self.assertEqual(len(self.puts()), 1)

    def test_no_subprocess_sees_the_sink_variables(self):
        sinkenv = pathlib.Path(str(self.git_log) + ".sinkenv")
        # A retired App credential an old environment still holds is stripped too.
        self.cloud_env.update(USAGE_WRITER_APP_CLIENT_ID="Iv1.example0client",
                              USAGE_WRITER_APP_PEM="example-retired-app-key-canary")
        # A partial configuration: the git route runs, with the secret still set.
        self.cloud_env["USAGE_SINK_BUCKET"] = ""
        self.script([PUSH_OK])
        root = build_vector_repo(self.tmp / "checkout", "skills/vector")
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))]
                              + self.use_with_base("toolu_v1", "vector", root))
        self.stop()
        self.assertEqual(self.puts(), [])
        self.assertTrue(self.calls())
        self.assertEqual([c["sink_env"] for c in self.calls()], [[]] * len(self.calls()))
        self.assertTrue(any("clone" in c for c in self.git_calls()))
        self.assertTrue(any("--show-toplevel" in c for c in self.git_calls()))
        self.assertFalse(sinkenv.exists())
        # The full configuration: the S3 route, while phase 2 still runs git locally.
        shutil.rmtree(self.state, ignore_errors=True)
        self.git_log.unlink()
        self.cloud_env["USAGE_SINK_BUCKET"] = SINK_ENV["USAGE_SINK_BUCKET"]
        self.stop()
        self.assertEqual(len(self.puts()), 1)
        self.assertTrue(any("--show-toplevel" in c for c in self.git_calls()))
        self.assertFalse(sinkenv.exists())


def run_log_line(ts="2026-10-31T11:59:00Z", event="pre-commit", repo="example-org/example-repo",
                 checks=("rules-dir", "secret-file"), failed=(), runner="0123456789ab"):
    """One agent-checks run-log line as templates/agent-checks/agent-checks
    writes it (compact JSON, that key order)."""
    return json.dumps({"v": 1, "ts": ts, "event": event, "repo": repo, "checks": list(checks),
                       "failed": list(failed), "runner": runner}, separators=(",", ":"))


def hook_upload_line(raw, sid, **fields):
    """The phase-3 line the hook must upload for the run-log line `raw`."""
    doc = json.loads(raw)
    out = {"v": 1, "id": hashlib.sha256(raw.encode()).hexdigest()[:16], "ts": doc["ts"],
           "kind": "hook", "surface": "cloud", "session": sid, "event": doc["event"],
           "repo": doc["repo"], "checks": doc["checks"], "failed": doc["failed"],
           "runner": doc["runner"]}
    out.update(fields)
    return json.dumps(out, separators=(",", ":"))


class HookRunLineTests(unittest.TestCase):
    """Phase 3's validation, on the hook's own helper."""

    def setUp(self):
        self.mod = hook_module()
        self.sid = "0123456789abcdef"

    def line(self, raw):
        return self.mod["hook_line"](raw.encode(), self.sid)

    def test_a_valid_line_becomes_the_spec_line(self):
        raw = run_log_line(failed=("secret-file",))
        ident, month, text = self.line(raw)
        self.assertEqual((month, text), ("2026-10", hook_upload_line(raw, self.sid)))
        self.assertEqual(ident, hashlib.sha256(raw.encode()).hexdigest()[:16])
        self.assertEqual(list(json.loads(text)), ["v", "id", "ts", "kind", "surface", "session",
                                                  "event", "repo", "checks", "failed", "runner"])

    def test_null_repo_and_runner_are_kept(self):
        raw = run_log_line(repo=None, runner=None)
        doc = json.loads(self.line(raw)[2])
        self.assertEqual((doc["repo"], doc["runner"]), (None, None))

    def test_malformed_lines_are_skipped(self):
        bad = [
            "not json", "[]", "{}", run_log_line().replace('"v":1', '"v":2'),
            run_log_line(ts="yesterday"), run_log_line(ts="1999-01-01T00:00:00Z"),
            run_log_line(event="ci"), run_log_line(event="post-commit"),
            run_log_line(checks=("Not_Kebab",)), run_log_line(checks=("a b",)),
            run_log_line(checks=("a",), failed=("b",)),          # failed not within checks
            run_log_line(checks=tuple("c%d" % i for i in range(65))),
            run_log_line(repo="not a repo"), run_log_line(repo="a/b/c"),
            run_log_line(runner="XYZ"), run_log_line(runner="0123456789abc"),
            json.dumps({"v": 1, "ts": "2026-10-31T11:59:00Z", "event": "pre-commit",
                        "repo": None, "checks": "rules-dir", "failed": [], "runner": None}),
        ]
        for raw in bad:
            with self.subTest(raw=raw[:60]):
                self.assertIsNone(self.line(raw))

    def test_a_boolean_version_is_not_version_one(self):
        self.assertIsNone(self.line(run_log_line().replace('"v":1', '"v":true')))
        self.assertIsNone(self.line(run_log_line().replace('"v":1', '"v":1.0')))

    def test_batches_with_hooks_stay_within_the_object_limit(self):
        mod = hook_module()
        mod["S3_HOOKS_DEFAULT"] = True
        for pad in range(900, 1100):
            items = [("h%d" % i, "2026-10", '{"kind":"hook","pad":"%s"}' % ("x" * pad))
                     for i in range(300)]
            for head, batch in mod["sink_batches"]('{"kind":"guidance-load"}', items):
                texts = [t for _i, t in batch]
                body = mod["sink_body"]("2026-10-31T12:00:00Z", head, [], texts)
                self.assertLessEqual(len(body), 262144, pad)

    def test_no_other_key_travels(self):
        doc = json.loads(run_log_line())
        doc.update(path="/home/someone/file", message="secret text")
        out = self.line(json.dumps(doc))[2]
        self.assertNotIn("someone", out)
        self.assertNotIn("secret text", out)


class HookRunGitTests(CloudTestCase):
    """Phase 3 on the git route: events/<month>/cloud-hooks/<session>.jsonl."""

    def setUp(self):
        super().setUp()
        self.runs = self.home / ".local" / "state" / "agent-checks" / "runs.jsonl"
        self.runs.parent.mkdir(parents=True)
        self.sid = CLOUD_SID[:16]
        self.done_phase1()
        self.write_transcript([user()])

    def write_runs(self, *lines, mode="w"):
        with open(self.runs, mode, encoding="utf-8") as handle:
            handle.write("".join(l + "\n" for l in lines))

    def remote_hooks(self, month="2026-10"):
        text = self.remote_text(f"events/{month}/cloud-hooks/{self.sid}.jsonl")
        return text.splitlines() if text is not None else []

    def test_new_run_lines_are_uploaded_with_the_spec_keys(self):
        a = run_log_line()
        b = run_log_line(ts="2026-10-31T11:59:30Z", event="pre-push", checks=("rules-dir",), failed=("rules-dir",))
        self.write_runs(a, "garbage", b, run_log_line(checks=("x",), failed=("y",)))
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid), hook_upload_line(b, self.sid)])
        self.assertEqual(self.pending(), [])

    def test_hook_lines_alone_are_not_called_skill_events(self):
        self.write_runs(run_log_line())
        self.script([PUSH_OK])
        self.stop()
        subject = subprocess.run([REAL_GIT, "--git-dir", str(self.bare), "log", "-1", "--format=%s",
                                  "persistent/usage-events"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, "cloud hook runs")

    def test_a_month_boundary_goes_to_its_own_shard(self):
        a = run_log_line(ts="2026-11-01T00:00:01Z")
        self.write_runs(a)
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(self.remote_hooks("2026-11"), [hook_upload_line(a, self.sid)])
        self.assertEqual(self.remote_hooks("2026-10"), [])

    def test_only_new_lines_go_next_time_and_a_replay_adds_nothing(self):
        a = run_log_line()
        self.write_runs(a)
        self.script([PUSH_OK])
        self.stop()
        head = self.commits()
        self.stop()                                   # nothing new: no commit, no call
        self.assertEqual(self.commits(), head)
        b = run_log_line(ts="2026-10-31T12:00:00Z", event="pre-push")
        self.write_runs(b, mode="a")
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid), hook_upload_line(b, self.sid)])
        # Losing the offset and the seen ids re-reads the log: the shard's own
        # ids keep it from growing.
        doc = self.state_doc()
        for key in ("hlog", "hseen", "hn"):
            doc.pop(key)
        (self.cloud / f"{CLOUD_SID}.state.json").write_text(json.dumps(doc), encoding="utf-8")
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(len(self.remote_hooks()), 2)

    def test_a_partial_last_line_waits_for_its_newline(self):
        a, b = run_log_line(), run_log_line(ts="2026-10-31T12:00:00Z")
        self.write_runs(a)
        with open(self.runs, "a", encoding="utf-8") as handle:
            handle.write(b[:20])
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid)])
        with open(self.runs, "a", encoding="utf-8") as handle:
            handle.write(b[20:] + "\n")
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(len(self.remote_hooks()), 2)

    def test_a_shrunk_log_is_read_again_from_the_start(self):
        a = run_log_line()
        self.write_runs(a, run_log_line(ts="2026-10-31T12:00:00Z"))
        self.script([PUSH_OK])
        self.stop()
        c = run_log_line(ts="2026-10-31T12:30:00Z", event="pre-push")
        self.write_runs(c)
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(self.remote_hooks()[-1], hook_upload_line(c, self.sid))

    def test_a_missing_or_off_log_changes_nothing(self):
        self.runs.unlink(missing_ok=True)
        self.stop()
        self.assertEqual((self.calls(), self.commits()), ([], 1))
        self.assertNotIn("hlog", self.state_doc())
        self.write_runs(run_log_line())
        self.stop(env={"AGENT_CHECKS_LOG": "off"})
        self.assertEqual((self.calls(), self.commits()), ([], 1))

    def test_the_log_path_comes_from_agent_checks_log(self):
        other = self.tmp / "other-runs.jsonl"
        a = run_log_line()
        other.write_text(a + "\n", encoding="utf-8")
        self.script([PUSH_OK])
        self.stop(env={"AGENT_CHECKS_LOG": str(other)})
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid)])

    def test_not_remote_means_no_phase_3(self):
        self.write_runs(run_log_line())
        self.run_hook(session=CLOUD_SESSION, env={"CLAUDE_CODE_REMOTE_SESSION_ID": "x",
                                                  "GUIDANCE_RECORD_GIT": str(self.bare)})
        self.assertFalse(self.cloud.exists())

    def test_one_stop_records_at_most_fifty_lines(self):
        runs = [run_log_line(ts="2026-10-31T12:%02d:%02dZ" % (i // 60, i % 60)) for i in range(60)]
        self.write_runs(*runs)
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(len(self.remote_hooks()), 50)
        self.script([PUSH_OK])
        self.stop()
        self.assertEqual(len(self.remote_hooks()), 60)

    def test_hook_lines_share_phase_2s_commit(self):
        a = run_log_line()
        self.write_runs(a)
        self.write_transcript([skill_use("toolu_h1", "a"), skill_result("toolu_h1")])
        self.script([PUSH_OK])
        head = self.commits()
        self.stop()
        self.assertEqual(self.commits(), head + 1)
        self.assertEqual([l["id"] for l in self.shard()], ["toolu_h1"])
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid)])

    def test_a_failed_push_keeps_the_lines_pending(self):
        a = run_log_line()
        self.write_runs(a)
        self.script([PUSH_OK])
        self.stop(env={"FAKE_GIT_MODE": "refuse-clone"})
        self.assertEqual([p["id"] for p in self.pending()], [json.loads(hook_upload_line(a, self.sid))["id"]])
        self.script([PUSH_OK])
        self.stop(now="2026-10-31T13:00:00Z")  # past the backoff
        self.assertEqual(self.remote_hooks(), [hook_upload_line(a, self.sid)])
        self.assertEqual(self.pending(), [])


class HookRunSinkTests(SinkTestCase):
    """Phase 3 on the S3 route: the `hooks` key is off until switched on."""

    def setUp(self):
        super().setUp()
        self.runs = self.home / ".local" / "state" / "agent-checks" / "runs.jsonl"
        self.runs.parent.mkdir(parents=True)
        self.sid = CLOUD_SID[:16]
        self.line = run_log_line()
        self.runs.write_text(self.line + "\n", encoding="utf-8")
        self.write_transcript([skill_use("toolu_s1", "a"), skill_result("toolu_s1")])
        self.done_phase1()

    def test_the_envelope_has_no_hooks_key_by_default(self):
        self.stop()
        [put] = self.puts()
        self.assertEqual(list(self.body(put)), SINK_BODY_KEYS)
        self.assertEqual(self.pending(), [])          # nothing was held for later either
        self.assertNotIn("hseen", self.state_doc())

    def test_no_hook_line_is_sent_when_only_hooks_are_pending(self):
        self.write_transcript([user()])
        self.stop()
        self.assertEqual(self.puts(), [])

    def test_the_switch_adds_the_hooks_key_with_the_exact_lines(self):
        self.stop(env={"USAGE_SINK_HOOKS": "1"})
        [put] = self.puts()
        doc = json.loads(put["body"].decode("utf-8"))
        self.assertEqual(list(doc), SINK_BODY_KEYS + ["hooks"])
        self.assertEqual(doc["hooks"], [json.loads(hook_upload_line(self.line, self.sid))])
        self.assertEqual([s["id"] for s in doc["skills"]], ["toolu_s1"])
        self.assertEqual(self.pending(), [])

    def test_hooks_alone_still_make_an_object_when_switched_on(self):
        self.write_transcript([user()])
        self.stop(env={"USAGE_SINK_HOOKS": "1"})
        [put] = self.puts()
        doc = self.body_any(put)
        self.assertEqual((doc["skills"], len(doc["hooks"])), ([], 1))

    def body_any(self, request):
        return json.loads(request["body"].decode("utf-8"))

    def test_lines_held_before_the_switch_never_leave_while_it_is_off(self):
        self.stop(env={"USAGE_SINK_HOOKS": "1", "USAGE_SINK_ENDPOINT": "http://127.0.0.1:9"})
        self.assertEqual(len(self.pending()), 2)      # the skill and the hook line, both held
        self.stop()
        for put in self.puts():
            self.assertNotIn("hooks", self.body_any(put))
        self.assertTrue([p for p in self.pending() if p["kind"] == "hook"])


class ClockSeamTests(HookTestCase):
    def test_the_clock_seam_needs_the_test_marker(self):
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.run_hook(env={"GUIDANCE_RECORD_TEST": "0", "GUIDANCE_RECORD_NOW": "2001-01-01T00:00:00Z"})
        [record] = self.records()
        self.assertNotEqual(record["ts"], "2001-01-01T00:00:00Z")
        self.run_hook(session="session-two", env={"GUIDANCE_RECORD_NOW": "2001-01-01T00:00:00Z"})
        self.assertEqual(self.records()[1]["ts"], "2001-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
