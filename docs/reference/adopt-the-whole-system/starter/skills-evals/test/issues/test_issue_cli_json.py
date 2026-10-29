"""The headless CLI may return one result object or a message array."""

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
sys.path.insert(0, str(ROOT / "scripts"))

from cli_json import failed_run_detail, normalize_cli_result  # noqa: E402
import run_canary  # noqa: E402
import run_eval  # noqa: E402
import propose_skill_edit  # noqa: E402
from scorers import judge  # noqa: E402


RESULT = {"type": "result", "is_error": False, "result": "done",
          "total_cost_usd": 0.25, "usage": {"input_tokens": 2},
          "num_turns": 1, "duration_ms": 8,
          "modelUsage": {"fake-default-model": {"inputTokens": 2}}}
MESSAGE = {"type": "assistant", "message": {"content": []}}
BACKGROUND_FIRST_RESULT = {
    "type": "result", "subtype": "success", "is_error": False,
    "result": "Root cause: set the required check on example.com.",
    "result_index": 0, "queued_turn_count": 0, "num_turns": 2,
    "total_cost_usd": 0.038031, "duration_ms": 3062, "duration_api_ms": 2900,
    "usage": {"input_tokens": 10, "output_tokens": 295, "service_tier": "standard",
              "server_tool_use": {"web_search_requests": 0},
              "iterations": [{"output_tokens": 295}]},
    "stop_reason": "end_turn",
    "terminal_reason": "completed",
    "modelUsage": {"fake-default-model": {"inputTokens": 10}},
    "subagent_stats": {"spawned": 1, "started_in_background": 1, "completed": 0}}
BACKGROUND_SECOND_RESULT = BACKGROUND_FIRST_RESULT | {
    "result": "The background agent confirmed the same root cause.",
    "result_index": 1, "num_turns": 1, "total_cost_usd": 0.05,
    "duration_ms": 1878, "duration_api_ms": 1800,
    "usage": {"input_tokens": 5, "output_tokens": 75, "service_tier": "standard",
              "server_tool_use": {"web_search_requests": 1},
              "iterations": [{"output_tokens": 75}]},
    "modelUsage": {"fake-default-model": {"inputTokens": 15}},
    "subagent_stats": {"spawned": 1, "started_in_background": 1, "completed": 1}}


def cli_reply(payload: object) -> subprocess.CompletedProcess:
    """A fake completed CLI invocation; no process or network is used."""
    return subprocess.CompletedProcess(args=["fake-cli"], returncode=0,
                                       stdout=json.dumps(payload), stderr="")


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class CliJsonShapeTests(unittest.TestCase):
    def test_dict_is_the_same_object(self):
        legacy = {"result": "done", "is_error": False, "usage": {"input_tokens": 2}}
        before = json.dumps(legacy, separators=(",", ":")).encode()
        self.assertIs(normalize_cli_result(legacy), legacy)
        self.assertEqual(json.dumps(legacy, separators=(",", ":")).encode(), before)

    def test_array_with_result_last(self):
        self.assertIs(normalize_cli_result([MESSAGE, RESULT]), RESULT)

    def test_array_with_result_not_last(self):
        self.assertIs(normalize_cli_result([RESULT, MESSAGE]), RESULT)

    def test_array_without_result(self):
        with self.assertRaisesRegex(ValueError, "found 0"):
            normalize_cli_result([MESSAGE])

    def test_follow_up_turn_after_background_agent_yields_two_results(self):
        # Claude Code 2.1.289, headless, `--output-format json`: the agent
        # launched a background subagent and answered; the subagent's
        # task-notification then started a second turn, so the array carried
        # two `type: result` objects (`result_index` 0 and 1). Shape copied
        # from a real failing trial (cms-stuck-pr-triage, without_skill),
        # content replaced by example.com stand-ins.
        first = BACKGROUND_FIRST_RESULT
        second = BACKGROUND_SECOND_RESULT
        merged = normalize_cli_result([MESSAGE, first, MESSAGE, second])
        self.assertEqual(merged["result"], first["result"] + "\n\n" + second["result"])
        # Per-turn fields are summed; cost and model usage are cumulative on
        # the CLI, so the last result's are kept.
        self.assertEqual(merged["num_turns"], 3)
        self.assertEqual(merged["duration_ms"], 3062 + 1878)
        self.assertEqual(merged["duration_api_ms"], 2900 + 1800)
        self.assertEqual(merged["usage"]["input_tokens"], 15)
        self.assertEqual(merged["usage"]["output_tokens"], 370)
        self.assertEqual(merged["usage"]["server_tool_use"], {"web_search_requests": 1})
        self.assertEqual(merged["usage"]["service_tier"], "standard")
        self.assertEqual(merged["usage"]["iterations"],
                         [{"output_tokens": 295}, {"output_tokens": 75}])
        self.assertEqual(merged["total_cost_usd"], 0.05)
        self.assertEqual(merged["modelUsage"], {"fake-default-model": {"inputTokens": 15}})
        self.assertEqual(merged["merged_results"], 2)
        self.assertFalse(merged["is_error"])
        # The inputs are left untouched.
        self.assertEqual(first["result"], "Root cause: set the required check on example.com.")
        self.assertNotIn("merged_results", second)
        self.assertEqual(second["usage"]["output_tokens"], 75)

    def test_any_error_among_several_results_stays_an_error(self):
        error = BACKGROUND_SECOND_RESULT | {"is_error": True, "result": "failed"}
        merged = normalize_cli_result([BACKGROUND_FIRST_RESULT, error])
        self.assertTrue(merged["is_error"])
        merged = normalize_cli_result([BACKGROUND_FIRST_RESULT | {"is_error": True},
                                       BACKGROUND_SECOND_RESULT])
        self.assertTrue(merged["is_error"])

    def test_array_with_error_result(self):
        error = RESULT | {"is_error": True, "result": "failed"}
        self.assertIs(normalize_cli_result([MESSAGE, error]), error)

    def test_empty_array(self):
        with self.assertRaisesRegex(ValueError, "found 0"):
            normalize_cli_result([])

    def test_non_dict_element_and_scalar_are_rejected_without_echoing_content(self):
        for payload in (["private payload", RESULT], [RESULT, "private payload"],
                        "private payload", None, True, 2):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(ValueError) as caught:
                    normalize_cli_result(payload)
                self.assertNotIn("private payload", str(caught.exception))


PRIVATE_CWD = "/home/example/private-workspace"
ERROR_TURN_ARRAY = [
    {"type": "system", "subtype": "init", "cwd": PRIVATE_CWD,
     "tools": ["Read", "mcp__example__lookup"]},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "input": {"file_path": PRIVATE_CWD + "/notes.txt"}}]}},
    BACKGROUND_FIRST_RESULT | {"subtype": "error_max_turns", "is_error": True,
                               "result": "ran out at " + PRIVATE_CWD},
]


class FailedRunDetailTests(unittest.TestCase):
    def test_array_stdout_is_never_echoed(self):
        detail = failed_run_detail(json.dumps(ERROR_TURN_ARRAY), "")
        self.assertEqual(detail, "result subtype error_max_turns, is_error true")
        self.assertNotIn("private-workspace", detail)

    def test_stderr_is_bounded_and_loses_absolute_paths(self):
        detail = failed_run_detail("ignored", f"boom at {PRIVATE_CWD}/x.py line 3\n")
        self.assertEqual(detail, "boom at <path> line 3")
        long = failed_run_detail("", "x" * 1000)
        self.assertEqual(long, "x" * 300 + "...")

    def test_stderr_redacts_quoted_urls_home_and_drive_paths(self):
        cases = {
            "ENOENT '/home/example/my project/a.txt' not found":
                "ENOENT '<path>' not found",
            'cannot open "/home/example/my project/a b.txt"': 'cannot open "<path>"',
            "loading file:///home/example/x/y.js failed": "loading <path> failed",
            "see ~/example/.config/x.json now": "see <path> now",
            "bad C:\\Users\\example\\x.txt here": "bad <path> here",
            "plain /usr/bin/env: missing": "plain <path>: missing",
            "a/b relative and https://example.com/x stay":
                "a/b relative and https://example.com/x stay",
        }
        for stderr, expected in cases.items():
            with self.subTest(stderr=stderr):
                self.assertEqual(failed_run_detail("", stderr), expected)

    def test_unparseable_or_unexpected_stdout_reports_only_its_length(self):
        for stdout in ('[{"cwd": "' + PRIVATE_CWD, "[]", "null", ""):
            with self.subTest(stdout=stdout):
                detail = failed_run_detail(stdout, " \n")
                self.assertEqual(detail, f"stdout {len(stdout)} chars")

    def test_subtype_is_only_echoed_when_it_looks_like_a_label(self):
        odd = ERROR_TURN_ARRAY[-1] | {"subtype": PRIVATE_CWD}
        self.assertNotIn("private-workspace", failed_run_detail(json.dumps([odd]), ""))


class CliJsonConsumersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        home = self.workspace / "home"
        home.mkdir()
        self.cli = self.workspace / "cli-stub"
        self.cli.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        self.cli.chmod(0o700)
        self.env = {"PATH": os.environ["PATH"], "HOME": str(home),
                    "TMPDIR": str(self.workspace), "LANG": "C.UTF-8",
                    "CLAUDE_BIN": str(self.cli)}

    def _agent(self, payload: object) -> dict:
        with mock.patch("subprocess.run", return_value=cli_reply(payload)):
            return run_eval.run_agent(self.workspace, "prompt",
                                      {"name": "without_skill", "timeout": 5})

    def _canary(self, payload: object) -> dict:
        with mock.patch("subprocess.run", return_value=cli_reply(payload)):
            return run_canary.run_leg(self.workspace, "prompt", "Read", model=None,
                                      timeout=5)

    def _judge(self, payload: object) -> tuple[str, list]:
        with mock.patch("subprocess.run", return_value=cli_reply(payload)):
            with judge.collecting_models() as models:
                text = judge._run_judge_cli("prompt", model=None, timeout=5)
        return text, models

    def _proposal(self, payload: object) -> str:
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch("subprocess.run", return_value=cli_reply(payload)):
            return propose_skill_edit.Runner().propose("prompt", "fake-default-model")

    def test_agent_dict_unchanged_and_array_result_last_or_earlier(self):
        for payload in (RESULT, [MESSAGE, RESULT], [RESULT, MESSAGE]):
            with self.subTest(payload=type(payload).__name__):
                answer = self._agent(payload)
                self.assertEqual(answer["transcript"], "done")
                self.assertEqual(answer["cost_usd"], 0.25)
                self.assertEqual(answer["usage"], {"input_tokens": 2})
                self.assertEqual(answer["raw"], RESULT)
                self.assertEqual(run_eval.models_used(answer["raw"]),
                                 ["fake-default-model"])

    def test_agent_array_error_and_invalid_shapes(self):
        error = RESULT | {"is_error": True, "result": "failed"}
        answer = self._agent([MESSAGE, error])
        self.assertEqual(answer["error"], "agent_error")
        self.assertEqual(answer["detail"], "failed")
        self.assertEqual(answer["raw"], error)
        for payload in ([], [MESSAGE], ["private payload", RESULT]):
            with self.subTest(payload=payload):
                answer = self._agent(payload)
                self.assertEqual(answer["error"], "invalid_json")
                self.assertNotIn("private payload", answer["detail"])

    def test_canary_reads_array_and_reports_invalid_shape(self):
        self.assertEqual(self._canary([MESSAGE, RESULT]), {"reply": "done"})
        self.assertEqual(self._canary([RESULT, MESSAGE]), {"reply": "done"})
        error = self._canary([MESSAGE])
        self.assertEqual(error["error"], "invalid_json")
        self.assertEqual(self._canary([MESSAGE, RESULT | {"is_error": True}])["error"],
                         "agent_error")

    def test_agent_scores_a_run_with_a_follow_up_turn(self):
        answer = self._agent([MESSAGE, BACKGROUND_FIRST_RESULT, MESSAGE,
                              BACKGROUND_SECOND_RESULT])
        self.assertNotIn("error", answer)
        self.assertEqual(answer["transcript"],
                         BACKGROUND_FIRST_RESULT["result"] + "\n\n"
                         + BACKGROUND_SECOND_RESULT["result"])
        self.assertEqual(answer["cost_usd"], 0.05)
        self.assertEqual(answer["num_turns"], 3)
        self.assertEqual(answer["usage"]["output_tokens"], 370)
        self.assertEqual(run_eval.models_used(answer["raw"]), ["fake-default-model"])

    def test_agent_asks_for_the_whole_message_array(self):
        with mock.patch("subprocess.run", return_value=cli_reply(RESULT)) as run:
            run_eval.run_agent(self.workspace, "prompt",
                               {"name": "without_skill", "timeout": 5})
        argv = run.call_args.args[0]
        self.assertIn("--verbose", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")

    def test_agent_nonzero_exit_with_array_stdout_does_not_echo_it(self):
        failed = subprocess.CompletedProcess(
            args=["fake-cli"], returncode=1, stdout=json.dumps(ERROR_TURN_ARRAY),
            stderr="")
        with mock.patch("subprocess.run", return_value=failed):
            answer = run_eval.run_agent(self.workspace, "prompt",
                                        {"name": "without_skill", "timeout": 5})
            canary = run_canary.run_leg(self.workspace, "prompt", "Read",
                                        model=None, timeout=5)
            with self.assertRaises(RuntimeError) as judged:
                judge._run_judge_cli("prompt", model=None, timeout=5)
        self.assertEqual(answer["error"], "nonzero_exit")
        self.assertEqual(answer["returncode"], 1)
        self.assertEqual(answer["detail"], "result subtype error_max_turns, is_error true")
        for text in (json.dumps(answer), json.dumps(canary), str(judged.exception)):
            self.assertNotIn("private-workspace", text)

    def test_judge_and_canary_invalid_json_echo_none_of_stdout(self):
        broken = subprocess.CompletedProcess(
            args=["fake-cli"], returncode=0, stdout='[{"cwd": "' + PRIVATE_CWD,
            stderr="")
        with mock.patch("subprocess.run", return_value=broken):
            canary = run_canary.run_leg(self.workspace, "prompt", "Read",
                                        model=None, timeout=5)
            with self.assertRaises(RuntimeError) as judged:
                judge._run_judge_cli("prompt", model=None, timeout=5)
        self.assertEqual(canary["error"], "invalid_json")
        self.assertNotIn("private-workspace", json.dumps(canary))
        self.assertNotIn("private-workspace", str(judged.exception))

    def test_truncated_stdout_is_an_error_that_echoes_none_of_it(self):
        truncated = '[{"type": "system", "subtype": "init", "cwd": "private payload", "ses'
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
                args=["fake-cli"], returncode=0, stdout=truncated, stderr="")):
            answer = run_eval.run_agent(self.workspace, "prompt",
                                        {"name": "without_skill", "timeout": 5})
        self.assertEqual(answer["error"], "invalid_json")
        self.assertNotIn("private payload", answer["detail"])
        self.assertIn(str(len(truncated)), answer["detail"])

    def test_judge_reads_array_and_records_result_model_usage(self):
        text, models = self._judge([MESSAGE, RESULT])
        self.assertEqual(text, "done")
        self.assertEqual(models, [{
            "modelUsage": RESULT["modelUsage"], "num_turns": 1,
            "usage": RESULT["usage"], "total_cost_usd": 0.25,
            "events": []}])
        self.assertEqual(self._judge([RESULT, MESSAGE])[0], "done")
        with self.assertRaisesRegex(RuntimeError, "invalid JSON") as caught:
            self._judge(["private payload", RESULT])
        self.assertNotIn("private payload", str(caught.exception))

    def test_proposal_reads_array_and_refuses_invalid_shape(self):
        self.assertEqual(self._proposal([MESSAGE, RESULT]), "done")
        self.assertEqual(self._proposal([RESULT, MESSAGE]), "done")
        with self.assertRaises(propose_skill_edit.Refusal) as caught:
            self._proposal(["private payload", RESULT])
        self.assertNotIn("private payload", str(caught.exception))

    def test_proposal_ignores_parent_cloud_environment(self):
        parent_env = {"AZURE_EXTENSION_DIR": "/x", "GOOGLE_FOO": "y"}
        with mock.patch.dict(os.environ, parent_env):
            self.assertEqual(self._proposal([MESSAGE, RESULT]), "done")
            self.assertEqual({name: os.environ[name] for name in parent_env}, parent_env)

    def test_proposal_needs_no_cli_on_path(self):
        empty_bin = self.workspace / "empty-bin"
        empty_bin.mkdir()
        with mock.patch.dict(self.env, {"PATH": str(empty_bin)}):
            self.assertEqual(self._proposal([MESSAGE, RESULT]), "done")

    def test_raw_transcript_file_contains_only_normalized_result(self):
        raw = self._agent([MESSAGE, RESULT])["raw"]
        run_eval._write_summary(self.workspace, "fixture", "without_skill",
                                "20261004T000000Z", None, None, None, None, raw)
        saved = json.loads((self.workspace / "fixture" / "20261004T000000Z"
                            / "without_skill" / "transcripts" / "raw.json").read_text())
        self.assertEqual(saved, RESULT)


if __name__ == "__main__":
    unittest.main()
