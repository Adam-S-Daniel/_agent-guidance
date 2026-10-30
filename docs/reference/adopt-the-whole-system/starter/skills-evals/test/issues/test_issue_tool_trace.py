"""#89: a bounded, redacted tool-call trace beside raw.json.

raw.json keeps only the CLI's result objects, so a run's tool calls could not
be read back when ci-watcher-loops needed diagnosing. `tool_trace.json` keeps
one line per tool call and per tool result. These tests use a canned
`--verbose` message array; no process or network is used.

Every secret-looking string here is assembled at run time, so the source
file itself never carries one for a secret scanner to flag.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import cli_json  # noqa: E402
from cli_json import (REDACTED, bounded_tool_trace, redact, secret_values,  # noqa: E402
                      tool_events)
import run_eval  # noqa: E402

# A credential the arm's environment carries (eval.yml forwards
# ANTHROPIC_AUTH_TOKEN to the CLI), and one with a well-known token shape.
ENV_SECRET = "fake" + "-bearer-" + "0123456789abcdef"
SHAPED_SECRET = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
ASSIGNED_SECRET = "s3cr3t" + "Value" + "XYZ"

RESULT = {"type": "result", "is_error": False, "result": "done",
          "total_cost_usd": 0.25, "usage": {"input_tokens": 2},
          "num_turns": 3, "duration_ms": 8,
          "modelUsage": {"fake-default-model": {"inputTokens": 2}}}


def verbose_array(result: dict = RESULT) -> list:
    """A `--verbose` message array: init, a Bash call whose result is an
    error, a Read whose output is oversized and leaks secrets, and the
    result."""
    big = ("line\n" * 2000) + ENV_SECRET
    return [
        {"type": "system", "subtype": "init", "cwd": "/home/runner/private-workspace",
         "tools": ["Bash", "Read"]},
        {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
            {"type": "text", "text": "Watching the run."},
            {"type": "tool_use", "id": "toolu_01", "name": "Bash",
             "input": {"command": "gh run watch 123 --exit-status | tail -5; "
                                  "echo GH_TOKEN=" + ASSIGNED_SECRET + " "
                                  + "x" * 400,
                       "description": "watch"}}]}},
        {"type": "user", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_01", "is_error": True,
             "content": "Exit code 1\nauthorization: Bearer " + ENV_SECRET}]}},
        {"type": "assistant", "parent_tool_use_id": "toolu_sub", "message": {"content": [
            {"type": "tool_use", "id": "toolu_02", "name": "Read",
             "input": {"file_path": "/home/runner/private-workspace/.env"}}]}},
        {"type": "user", "parent_tool_use_id": "toolu_sub", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_02",
             "content": [{"type": "text", "text": "token " + SHAPED_SECRET + "\n" + big}]}]}},
        result,
    ]


def cli_reply(payload: object, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["fake-cli"], returncode=returncode,
                                       stdout=json.dumps(payload), stderr="")


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import arm_test_environment, install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()



class ToolEventTests(unittest.TestCase):
    def setUp(self):
        self.events = tool_events(verbose_array(), [ENV_SECRET])

    def test_one_event_per_call_and_result_in_order(self):
        self.assertEqual([(e["kind"], e["id"]) for e in self.events],
                         [("tool_use", "toolu_01"), ("tool_result", "toolu_01"),
                          ("tool_use", "toolu_02"), ("tool_result", "toolu_02")])
        self.assertEqual(self.events[0]["name"], "Bash")
        self.assertTrue(self.events[0]["input"].startswith("gh run watch 123"))
        self.assertFalse(self.events[0]["subagent"])
        self.assertTrue(self.events[2]["subagent"])

    def test_result_error_flag_and_output_length(self):
        self.assertIs(self.events[1]["is_error"], True)
        self.assertIs(self.events[3]["is_error"], False)
        self.assertTrue(self.events[1]["output"].startswith("Exit code 1"))
        self.assertGreater(self.events[3]["output_chars"], 10000)

    def test_inputs_and_outputs_are_truncated(self):
        self.assertGreater(self.events[0]["input_chars"], cli_json.TRACE_INPUT_CHARS)
        self.assertLessEqual(len(self.events[0]["input"]),
                             cli_json.TRACE_INPUT_CHARS + 3)
        self.assertLessEqual(len(self.events[3]["output"]),
                             cli_json.TRACE_OUTPUT_CHARS + 3)

    def test_secrets_and_paths_are_redacted(self):
        text = json.dumps(self.events)
        for secret in (ENV_SECRET, SHAPED_SECRET, ASSIGNED_SECRET,
                       "private-workspace"):
            self.assertNotIn(secret, text)
        self.assertIn("GH_TOKEN=<redacted>", self.events[0]["input"])
        self.assertEqual(self.events[2]["input"], "<path>")

    def test_a_single_result_object_has_no_events(self):
        self.assertEqual(tool_events(RESULT), [])
        self.assertEqual(tool_events(["junk", {"type": "assistant"}]), [])


class RedactionTests(unittest.TestCase):
    def test_env_values_named_as_credentials_are_collected(self):
        env = {"ANTHROPIC_AUTH_TOKEN": ENV_SECRET, "PATH": "/usr/bin:/bin",
               "GH_TOKEN": "short", "LANG": "C.UTF-8"}
        self.assertEqual(secret_values(env), [ENV_SECRET])

    def test_credential_shapes_are_redacted_without_a_known_value(self):
        for text in (SHAPED_SECRET, "API_KEY=" + ASSIGNED_SECRET,
                     '"password": "' + ASSIGNED_SECRET + '"',
                     "sk-" + "ant-" + "a" * 30):
            with self.subTest(text=text[:12]):
                self.assertIn("<redacted>", redact(text))
                self.assertNotIn(ASSIGNED_SECRET, redact(text))


def path_lines_then(secret: str, at: int = 4057) -> str:
    """grep/find-style absolute-path lines (about 3.9 KB, each of which
    redaction shrinks to `<path>`), then `secret` starting at char `at`, just
    inside the 4096-char window the first version redacted before cutting."""
    head, index = "", 0
    while len(head) + 200 < at:
        head += "/srv/" + "segment/" * 18 + f"file{index:03}.py\n"
        index += 1
    rest = at - len(head)
    head += "/srv/" + "a" * (rest - 6) + "\n"
    assert len(head) == at
    return head + secret + "\nmore output\n"


class FullStringRedactionTests(unittest.TestCase):
    """The first version redacted 4096 chars and then cut to 300; redaction
    shrank the head, so a secret cut at the window edge reached the kept
    300 chars unredacted (review of b4592cd)."""

    def _output(self, text: str, secrets: list[str]) -> str:
        message = {"type": "user", "parent_tool_use_id": None, "message": {
            "content": [{"type": "tool_result", "tool_use_id": "toolu_09",
                         "content": text}]}}
        return tool_events([message], secrets)[0]["output"]

    def test_env_value_at_the_window_edge_is_redacted(self):
        secret = "fake" + "-env-" + "0123456789abcdefghijklmnopqrstu"
        self.assertEqual(len(secret), 40)
        out = self._output(path_lines_then(secret), [secret])
        self.assertIn("<path>", out)
        for piece in (secret[:8], secret[8:16], secret[-8:]):
            self.assertNotIn(piece, out)
        self.assertIn(REDACTED, out)

    def test_token_shape_at_the_window_edge_is_redacted(self):
        # 15 of its chars inside the old window: too few for the gh*_ shape.
        out = self._output(path_lines_then(SHAPED_SECRET, at=4096 - 15), [])
        self.assertNotIn(SHAPED_SECRET[:8], out)
        self.assertIn(REDACTED, out)

    def test_oversized_text_keeps_nothing(self):
        big = "x" * (cli_json._REDACT_MAX_CHARS + 1) + SHAPED_SECRET
        out = self._output(big, [])
        self.assertNotIn("x", out.replace("chars", ""))
        self.assertIn("too large to redact", out)


class CredentialShapeTests(unittest.TestCase):
    """Each hardening shape is redacted without a known value."""

    VALUE = "Vv" + "9q" + "Zx4" + "Lm7" + "Pq2" + "Rt8"

    def assertRedacted(self, text: str, secret: str, marker: str = REDACTED):
        out = redact(text)
        self.assertNotIn(secret, out, out)
        self.assertIn(marker, out, out)
        return out

    def test_headers(self):
        for header in ("X-Api-Key", "x-api-key", "X-Auth-Token", "Private-Token",
                       "X-Goog-Api-Key", "Proxy-Authorization"):
            with self.subTest(header=header):
                self.assertRedacted(f'curl -H "{header}: {self.VALUE}" x',
                                    self.VALUE)
        out = self.assertRedacted("Authorization: Digest username=" + self.VALUE,
                                  self.VALUE)
        self.assertTrue(out.startswith("Authorization: "))

    def test_netrc(self):
        text = ("machine example.com login someuser password " + self.VALUE
                + "\nmachine example.net\n  login otheruser\n  password "
                + self.VALUE + "\n")
        out = self.assertRedacted(text, self.VALUE)
        self.assertNotIn("someuser", out)
        self.assertNotIn("otheruser", out)
        self.assertIn("machine example.net", out)

    def test_curl_user(self):
        for flag in ("-u ", "--user ", "--user=", "-u '"):
            with self.subTest(flag=flag):
                out = self.assertRedacted(
                    f"curl {flag}someone:{self.VALUE} https://example.com", self.VALUE)
                self.assertNotIn("someone", out)
        # -u without a colon is some other flag (git push -u, sort -u).
        self.assertEqual(redact("git push -u origin main"), "git push -u origin main")

    def test_url_userinfo(self):
        for url in (f"https://x-access-token:{self.VALUE}@github.com/o/r.git",
                    f"postgres://admin:{self.VALUE}@db.example.com:5432/x",
                    f"https://{self.VALUE}@example.com/"):
            with self.subTest(url=url[:12]):
                out = self.assertRedacted(url, self.VALUE)
                self.assertNotIn("x-access-token", out)
        self.assertIn("example.com", redact(f"https://u:{self.VALUE}@example.com/x"))

    def test_pat_env_names(self):
        for name in ("GH_PAT_X", "MY_PAT", "PAT_FOR_CI", "gh_pat"):
            with self.subTest(name=name):
                self.assertRedacted(f"{name}={self.VALUE}", self.VALUE)
        self.assertEqual(secret_values({"GH_PAT_X": self.VALUE,
                                        "PATH": "/usr/local/bin:/usr/bin"}),
                         [self.VALUE])
        self.assertNotIn(REDACTED, redact("PATH=/usr/bin SPATIAL=yes"))

    def test_private_key_blocks(self):
        body = "\n".join(["lQOYBF" + self.VALUE] * 3)
        for kind in ("PGP PRIVATE KEY BLOCK", "OPENSSH PRIVATE KEY",
                     "ENCRYPTED PRIVATE KEY", "RSA PRIVATE KEY"):
            with self.subTest(kind=kind):
                text = (f"before\n-----BEGIN {kind}-----\n{body}\n"
                        f"-----END {kind}-----\nafter")
                out = self.assertRedacted(text, self.VALUE)
                self.assertEqual(out, "before\n" + REDACTED + "\nafter")
        # No END line: everything from BEGIN on goes.
        out = redact("x\n-----BEGIN PRIVATE KEY-----\n" + body)
        self.assertEqual(out, "x\n" + REDACTED)

    def test_vendor_token_shapes(self):
        alnum = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        for token in ("gl" + "pat-" + alnum[:20],
                      "AI" + "za" + alnum[:35],
                      "rk" + "_live_" + alnum[:24],
                      "sk" + "_live_" + alnum[:24],
                      "sk" + "_test_" + alnum[:24],
                      "xa" + "pp-1-" + alnum[:20],
                      "xo" + "xb-" + alnum[:20],
                      "xo" + "xe-" + alnum[:20],
                      "gh" + "p_" + alnum,
                      "gh" + "s_" + alnum,
                      "github" + "_pat_" + alnum):
            with self.subTest(token=token[:6]):
                out = self.assertRedacted(f"value {token} end", token)
                self.assertEqual(out, f"value {REDACTED} end")

    def test_email_addresses(self):
        address = "someone." + "name" + "@" + "example.com"
        out = self.assertRedacted(f"Author: Some One <{address}>", address,
                                  cli_json.EMAIL)
        self.assertIn("<email>", out)
        self.assertEqual(redact("a@b c@1.2.3 @types/node"),
                         "a@b c@1.2.3 @types/node")

    def test_ordinary_output_is_kept(self):
        text = ("ok 12 tests passed\nkey: value\nmonkey patch applied\n"
                "sort -u list.txt\nhttps://example.com/path?q=1\n")
        self.assertEqual(redact(text), text)


class BoundTests(unittest.TestCase):
    def test_trace_is_capped_and_counts_what_it_dropped(self):
        calls = [tool_events(verbose_array(), [ENV_SECRET]) for _ in range(200)]
        trace = bounded_tool_trace(calls, max_bytes=4096)
        size = sum(len(json.dumps(e).encode("utf-8")) for e in trace["events"])
        self.assertLessEqual(size, 4096)
        self.assertGreater(trace["omitted_events"], 0)
        self.assertEqual(len(trace["events"]) + trace["omitted_events"], 800)
        self.assertEqual(trace["schema_version"], 1)
        self.assertEqual(trace["calls"], 200)

    def test_default_cap_holds_for_a_long_run(self):
        calls = [tool_events(verbose_array(), [ENV_SECRET]) for _ in range(500)]
        trace = bounded_tool_trace(calls)
        self.assertLessEqual(len(json.dumps(trace).encode("utf-8")),
                             cli_json.TRACE_MAX_BYTES + 1024)
        self.assertGreater(trace["omitted_events"], 0)


class RunAgentTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        home = self.workspace / "home"
        home.mkdir()
        self.env = {**arm_test_environment(), "HOME": str(home),
                    "TMPDIR": str(self.workspace), "LANG": "C.UTF-8",
                    "ANTHROPIC_AUTH_TOKEN": ENV_SECRET,
                    "CLAUDE_BIN": str(self.workspace / "no-cli")}

    def _agent(self, reply: subprocess.CompletedProcess) -> dict:
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch("subprocess.run", return_value=reply):
            return run_eval.run_agent(self.workspace, "prompt",
                                      {"name": "without_skill", "timeout": 5})

    def test_scoring_inputs_are_unchanged(self):
        traced = self._agent(cli_reply(verbose_array()))
        plain = self._agent(cli_reply(RESULT))
        self.assertEqual(traced["raw"], RESULT)
        for key in ("transcript", "usage", "cost_usd", "num_turns",
                    "duration_ms", "raw"):
            self.assertEqual(traced[key], plain[key], key)
        self.assertEqual(plain["tool_trace"]["events"], [])
        self.assertIs(plain["tool_trace"]["complete"], False)
        self.assertEqual(len(traced["tool_trace"]["events"]), 4)

    def test_the_child_envs_credential_is_redacted(self):
        trace = self._agent(cli_reply(verbose_array()))["tool_trace"]
        self.assertNotIn(ENV_SECRET, json.dumps(trace))

    def test_a_failed_run_still_keeps_its_trace(self):
        answer = self._agent(cli_reply(verbose_array(RESULT | {"is_error": True}), 1))
        self.assertEqual(answer["error"], "nonzero_exit")
        self.assertEqual(answer["tool_trace"]["events"][1]["is_error"], True)

    def test_written_beside_raw_json_and_raw_json_unchanged(self):
        answer = self._agent(cli_reply(verbose_array()))
        run_eval._write_summary(self.workspace, "fixture", "without_skill",
                                "20261005T000000Z", None, None, None, None,
                                answer["raw"], tool_trace=answer["tool_trace"])
        transcripts = (self.workspace / "fixture" / "20261005T000000Z"
                       / "without_skill" / "transcripts")
        self.assertEqual(json.loads((transcripts / "raw.json").read_text()), RESULT)
        saved = (transcripts / run_eval.TOOL_TRACE_NAME).read_text()
        self.assertEqual(json.loads(saved), answer["tool_trace"])
        for secret in (ENV_SECRET, SHAPED_SECRET, ASSIGNED_SECRET):
            self.assertNotIn(secret, saved)


if __name__ == "__main__":
    unittest.main()
