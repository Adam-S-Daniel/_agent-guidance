"""Tests for .claude/hooks/guidance-load-record.sh, the per-session Stop hook
that records whether the full fleet guidance or only the repo stub loaded.

Every case runs the real hook under bash in a temp HOME with a fixed clock
(GUIDANCE_RECORD_NOW), a fake `curl` first on PATH that records each call and
answers from a scripted list, and no network. Fixtures use invented paths and
example.com only.
"""

import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "guidance-load-record.sh"
NOW = "2026-10-31T12:00:00Z"
KEYS = ["v", "ts", "kind", "session", "surface", "remote", "cli", "state",
        "repos", "guidance_version", "registry_sha", "uploaded"]
FULL = "guidance-marker: fleet-full (transcript counts use this line; keep it)"
STUB = "guidance-marker: fleet-stub (transcript counts use this line; keep it)"
REPO = "Adam-S-Daniel/adam-agentskills-private"
CONTENTS = f"https://api.example.com/repos/{REPO}/contents/events/2026-10/cloud-guidance.jsonl"

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
        handle.write(json.dumps({"argv": argv, "stdin": stdin, "body": body}) + "\\n")
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
            pairs = json.loads(line, object_pairs_hook=lambda p: p)
            self.assertEqual([k for k, _ in pairs], KEYS, line)
            out.append(dict(pairs))
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
            "registry_sha": None, "uploaded": None})

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


@unittest.skipIf(shutil.which("openssl") is None, "openssl is required to sign the App JWT")
class UploadTests(HookTestCase):
    @classmethod
    def setUpClass(cls):
        cls.keydir = tempfile.mkdtemp(prefix="grec-key-")
        key = pathlib.Path(cls.keydir) / "app.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(key)], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout",
                        "-out", str(pathlib.Path(cls.keydir) / "app.pub")], check=True, capture_output=True)
        cls.pem = key.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.keydir, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.write_transcript([instructions(memfile("/w/r/AGENTS.md", "Project", STUB))])
        self.creds = {"USAGE_WRITER_APP_CLIENT_ID": "Iv1.example0client",
                      "USAGE_WRITER_APP_PEM": self.pem}

    def script(self, replies):
        self.curl_script.write_text(json.dumps(replies), encoding="utf-8")

    def test_not_attempted_when_not_remote(self):
        self.run_hook(env=self.creds)
        self.assertEqual(self.calls(), [])
        self.assertIsNone(self.records()[0]["uploaded"])

    def test_not_attempted_without_credentials(self):
        self.run_hook(session="a", env={"CLAUDE_CODE_REMOTE": "true"})
        self.run_hook(session="b", env={"CLAUDE_CODE_REMOTE": "true",
                                        "USAGE_WRITER_APP_CLIENT_ID": "Iv1.example0client"})
        self.run_hook(session="c", env={"CLAUDE_CODE_REMOTE": "true", "USAGE_WRITER_APP_PEM": self.pem})
        self.assertEqual(self.calls(), [])
        self.assertEqual([r["uploaded"] for r in self.records()], [None, None, None])

    def test_append_with_one_retry_on_conflict(self):
        earlier = '{"v":1,"kind":"guidance-load","session":"0000000000000000"}\n'
        between = '{"v":1,"kind":"guidance-load","session":"1111111111111111"}\n'
        self.script([
            {"status": 200, "body": {"id": 4242}},
            {"status": 201, "body": {"token": "ghs_EXAMPLEINSTALLATIONTOKEN"}},
            {"status": 200, "body": {"sha": "sha-one", "encoding": "base64",
                                     "content": base64.b64encode(earlier.encode()).decode()}},
            {"status": 409, "body": {"message": "conflict"}},
            {"status": 200, "body": {"sha": "sha-two", "encoding": "base64",
                                     "content": base64.b64encode((earlier + between).encode()).decode()}},
            {"status": 201, "body": {"content": {}}},
        ])
        self.run_hook(env=dict(self.creds, CLAUDE_CODE_REMOTE="true"))
        [record] = self.records()
        self.assertIs(record["uploaded"], True)
        calls = self.calls()
        shape = [(c["argv"][c["argv"].index("-X") + 1], c["argv"][-1]) for c in calls]
        self.assertEqual(shape, [
            ("GET", f"https://api.example.com/repos/{REPO}/installation"),
            ("POST", "https://api.example.com/app/installations/4242/access_tokens"),
            ("GET", CONTENTS + "?ref=persistent/usage-events"),
            ("PUT", CONTENTS),
            ("GET", CONTENTS + "?ref=persistent/usage-events"),
            ("PUT", CONTENTS),
        ])
        self.assertEqual(calls[1]["body"], {"repositories": ["adam-agentskills-private"],
                                            "permissions": {"contents": "write"}})
        final = calls[5]["body"]
        self.assertEqual((final["sha"], final["branch"]), ("sha-two", "persistent/usage-events"))
        uploaded = base64.b64decode(final["content"]).decode()
        self.assertTrue(uploaded.startswith(earlier + between))
        line = json.loads(uploaded.splitlines()[-1])
        self.assertNotIn("uploaded", line)
        self.assertEqual({k: v for k, v in record.items() if k != "uploaded"}, line)
        for c in calls:
            self.assertIn("--max-time", c["argv"])
            self.assertLessEqual(int(c["argv"][c["argv"].index("--max-time") + 1]), 10)

        # The JWT, the token and the PEM never reach argv; they ride stdin.
        jwt = calls[0]["stdin"].split("Bearer ", 1)[1].split('"', 1)[0]
        self.assertEqual(jwt.count("."), 2)
        self.assertIn("Bearer ghs_EXAMPLEINSTALLATIONTOKEN", calls[2]["stdin"])
        pem_body = self.pem.splitlines()[1]
        for c in calls:
            argv = " ".join(c["argv"])
            for secret in (jwt, "ghs_EXAMPLEINSTALLATIONTOKEN", pem_body, "PRIVATE KEY"):
                self.assertNotIn(secret, argv)
        claims = json.loads(base64.urlsafe_b64decode(jwt.split(".")[1] + "=="))
        self.assertEqual(claims["exp"] - claims["iat"], 600)
        self.assertEqual(claims["iss"], "Iv1.example0client")

        # The signature verifies against the App key's public half.
        head, body, sig = jwt.split(".")
        sigfile = self.tmp / "sig"
        sigfile.write_bytes(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
        check = subprocess.run(["openssl", "dgst", "-sha256", "-verify",
                                str(pathlib.Path(self.keydir) / "app.pub"), "-signature", str(sigfile)],
                               input=f"{head}.{body}".encode(), capture_output=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        # Nothing, the temp key included, is left behind.
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_new_month_file_is_created_without_sha(self):
        self.script([
            {"status": 200, "body": {"id": 7}},
            {"status": 201, "body": {"token": "ghs_EXAMPLEINSTALLATIONTOKEN"}},
            {"status": 404, "body": {"message": "Not Found"}},
            {"status": 201, "body": {"content": {}}},
        ])
        self.run_hook(env=dict(self.creds, CLAUDE_CODE_REMOTE_SESSION_ID="example-remote"))
        self.assertIs(self.records()[0]["uploaded"], True)
        put = self.calls()[3]["body"]
        self.assertNotIn("sha", put)
        self.assertEqual(len(base64.b64decode(put["content"]).decode().splitlines()), 1)

    def test_a_base64_encoded_key_works_too(self):
        self.script([
            {"status": 200, "body": {"id": 7}},
            {"status": 201, "body": {"token": "ghs_EXAMPLEINSTALLATIONTOKEN"}},
            {"status": 404, "body": {"message": "Not Found"}},
            {"status": 201, "body": {"content": {}}},
        ])
        encoded = base64.b64encode(self.pem.encode()).decode()
        self.run_hook(env=dict(self.creds, USAGE_WRITER_APP_PEM=encoded, CLAUDE_CODE_REMOTE="true"))
        self.assertIs(self.records()[0]["uploaded"], True)
        self.assertEqual(len(self.calls()), 4)

    def test_second_conflict_gives_up(self):
        self.script([
            {"status": 200, "body": {"id": 7}},
            {"status": 201, "body": {"token": "ghs_EXAMPLEINSTALLATIONTOKEN"}},
            {"status": 404, "body": {}},
            {"status": 422, "body": {}},
            {"status": 404, "body": {}},
            {"status": 409, "body": {}},
        ])
        self.run_hook(env=dict(self.creds, CLAUDE_CODE_REMOTE="true"))
        self.assertIs(self.records()[0]["uploaded"], False)
        self.assertEqual(len(self.calls()), 6)

    def test_failed_installation_lookup_records_false_and_marks_done(self):
        self.script([{"status": 404, "body": {"message": "Not Found"}}])
        env = dict(self.creds, CLAUDE_CODE_REMOTE="true")
        self.run_hook(env=env)
        self.run_hook(env=env)
        [record] = self.records()
        self.assertIs(record["uploaded"], False)
        self.assertEqual(len(self.calls()), 1)

    def test_bad_pem_records_false_without_calling_the_api(self):
        self.run_hook(env={"CLAUDE_CODE_REMOTE": "true", "USAGE_WRITER_APP_CLIENT_ID": "Iv1.example0client",
                           "USAGE_WRITER_APP_PEM": "not a key"})
        self.assertIs(self.records()[0]["uploaded"], False)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
