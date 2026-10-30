"""A fixture's `followups:` are further user turns, sent with `--resume` in
the same workspace, identically in both arms (ADR 0009).

Hermetic: `subprocess.run` is replaced by a scripted fake, so no CLI, no
process and no network is used.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))

import local_eval  # noqa: E402
import run_eval  # noqa: E402
from cli_json import bounded_tool_trace  # noqa: E402

RENAME_DIR = ROOT / "evals" / "rename-pdfs"
# The PATH and mount table every arm here runs with, explicit: the arm's read
# fence is built from both, so no result depends on the host's (a WSL PATH
# under /mnt/c, its mountinfo). An empty mount table names no alias.
TEST_PATH = os.pathsep.join(dict.fromkeys(
    (str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin")))


def result(text: str, session: str | None = "sess-1", *, model="model-a",
           is_error=False, cost=0.5, turns=2, duration=100, tokens=10) -> dict:
    payload = {"type": "result", "is_error": is_error, "result": text,
               "total_cost_usd": cost, "num_turns": turns,
               "duration_ms": duration,
               "usage": {"input_tokens": tokens, "output_tokens": 1,
                         "server_tool_use": {"web_search_requests": 1},
                         "service_tier": "standard"},
               "modelUsage": {model: {"inputTokens": tokens, "costUSD": cost}}}
    if session is not None:
        payload["session_id"] = session
    return payload


class ScriptedCli:
    """Answers each `subprocess.run` call with the next scripted reply and
    records the argv, cwd and env it was handed."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd"),
                           "env": kwargs.get("env"),
                           "timeout": kwargs.get("timeout")})
        if not self.replies:
            raise AssertionError(f"unexpected CLI call: {cmd}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, subprocess.CompletedProcess):
            return reply
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply),
                                           stderr="")


def failed(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["fake"], 1, stdout="", stderr=stderr)


class RunAgentFollowupTests(unittest.TestCase):
    def setUp(self):
        # A TMPDIR of its own: the read rules list the harness directories
        # under TMPDIR, and a concurrent test (a `--jobs` worker) making or
        # removing one between two turns would change the flags compared.
        self.tmp = Path(tempfile.mkdtemp(prefix="followups-tmp-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.object(tempfile, "tempdir", str(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = Path(tempfile.mkdtemp(prefix="workspace-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)
        # A HOME of its own: the arm's read rules list what is in HOME.
        self.home = Path(tempfile.mkdtemp(prefix="followups-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        mountinfo = self.tmp / "mountinfo"
        mountinfo.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(run_eval.os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": TEST_PATH, run_eval.MOUNTINFO_ENV: str(mountinfo)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_agent(self, cli: ScriptedCli, followups=None, **arm):
        arm = {"name": "without_skill", "timeout": 30, "model": "model-a",
               "env": {"PROBE": "1"}, **arm}
        if followups is not None:
            arm["followups"] = followups
        with mock.patch.object(run_eval.subprocess, "run", cli), \
             mock.patch.dict(run_eval.os.environ, {"CLAUDE_BIN": "fake-claude"}):
            return run_eval.run_agent(self.workspace, "Rename the PDFs.", arm)

    # A multi-turn arm's first call: it must persist, or `--resume` finds
    # no conversation.
    @property
    def BASE_CMD(self):
        flags = run_eval.arm_isolation_flags(
            session_dir=self.home / ".claude" / "projects" /
            run_eval._munged_project_name(self.workspace),
            workspace=self.workspace, config_dir=self.home / ".claude")
        return ["fake-claude", "-p", "Rename the PDFs.", "--output-format",
                "json", "--verbose", "--permission-mode", "auto",
                "--setting-sources", "project", *flags,
                "--strict-mcp-config", "--model", "model-a"]

    # A one-turn arm writes no transcript.
    @property
    def ONE_TURN_CMD(self):
        base = self.BASE_CMD
        return [*base[:-2], "--no-session-persistence", *base[-2:]]

    # -- no followups: unchanged -----------------------------------------

    def test_without_followups_one_call_and_the_same_scoring_result(self):
        first = result("proposed six renames")
        for followups in (None, []):
            with self.subTest(followups=followups):
                cli = ScriptedCli(first)
                out = self.run_agent(cli, followups)
                self.assertEqual([c["cmd"] for c in cli.calls], [self.ONE_TURN_CMD])
                self.assertEqual(out, {
                    "transcript": "proposed six renames",
                    "usage": first["usage"], "cost_usd": 0.5, "num_turns": 2,
                    "duration_ms": 100, "raw": first,
                    "tool_trace": bounded_tool_trace([[]], complete=False)})

    def test_a_first_call_failure_is_unlabeled_and_sends_no_followup(self):
        cli = ScriptedCli(failed("boom"))
        out = self.run_agent(cli, ["yes"])
        self.assertEqual(out, {"error": "nonzero_exit", "detail": "boom",
                               "returncode": 1,
                               "tool_trace": bounded_tool_trace([[]], complete=False)})
        self.assertEqual(len(cli.calls), 1)

        cli = ScriptedCli(result("cannot", is_error=True))
        out = self.run_agent(cli, ["yes"])
        self.assertEqual(out["error"], "agent_error")
        self.assertEqual(out["detail"], "cannot")
        self.assertEqual(len(cli.calls), 1)

    # -- followups: same session, same workspace -------------------------

    def test_a_followup_resumes_the_session_with_the_same_flags(self):
        cli = ScriptedCli(result("proposed", "sess-1"),
                          result("renamed", "sess-1", model="model-b"))
        out = self.run_agent(cli, ["Yes, apply all (auto)."])
        self.assertNotIn("error", out)
        first, second = cli.calls
        self.assertEqual(first["cmd"], self.BASE_CMD)
        self.assertNotIn("--no-session-persistence", second["cmd"])
        expected = list(self.BASE_CMD)
        expected[2] = "Yes, apply all (auto)."
        self.assertEqual(second["cmd"], expected + ["--resume", "sess-1"])
        self.assertEqual(second["cwd"], first["cwd"])
        self.assertEqual(second["cwd"], self.workspace)
        self.assertEqual(second["env"], first["env"])
        self.assertEqual(second["env"]["PROBE"], "1")
        self.assertEqual(second["timeout"], 30)

    def test_each_followup_resumes_the_latest_session_id(self):
        cli = ScriptedCli(result("a", "s1"), result("b", "s2"), result("c", "s3"))
        out = self.run_agent(cli, ["one", "two"])
        self.assertNotIn("error", out)
        self.assertEqual([c["cmd"][-2:] for c in cli.calls[1:]],
                         [["--resume", "s1"], ["--resume", "s2"]])
        self.assertEqual([c["cmd"][2] for c in cli.calls],
                         ["Rename the PDFs.", "one", "two"])

    def test_the_transcript_and_totals_cover_every_turn(self):
        # The real shape (Claude Code 2.1.289, measured with a --resume
        # probe): the resumed result's `total_cost_usd` and `modelUsage` are
        # CUMULATIVE for the session, while `usage`, `num_turns` and
        # `duration_ms` cover that call alone.
        first = result("proposed", "s1", model="model-a", cost=0.5, turns=3,
                       duration=100, tokens=10)
        second = result("renamed", "s1", model="model-b", cost=0.25, turns=4,
                        duration=50, tokens=5)
        second["total_cost_usd"] = 0.75
        second["modelUsage"] = {
            "model-a": {"inputTokens": 10, "costUSD": 0.5},
            "model-b": {"inputTokens": 5, "costUSD": 0.25}}
        cli = ScriptedCli(first, second)
        out = self.run_agent(cli, ["go ahead"])
        self.assertEqual(out["transcript"],
                         "proposed" + run_eval.FOLLOWUP_MARKER.format(
                             text="go ahead") + "renamed")
        self.assertIn("go ahead", out["transcript"])
        # Cumulative fields: the last call's, never summed (0.5 + 0.75).
        self.assertEqual(out["cost_usd"], 0.75)
        # Per-call fields: summed.
        self.assertEqual(out["num_turns"], 7)
        self.assertEqual(out["duration_ms"], 150)
        self.assertEqual(out["usage"], {
            "input_tokens": 15, "output_tokens": 2,
            "server_tool_use": {"web_search_requests": 2},
            "service_tier": "standard"})
        raw = out["raw"]
        self.assertEqual(raw["result"], "renamed")
        self.assertEqual(len(raw["turns"]), 2)
        self.assertEqual(raw["turns"][0]["result"], "proposed")
        self.assertEqual(raw["total_cost_usd"], 0.75)
        self.assertEqual(raw["modelUsage"], second["modelUsage"])
        self.assertEqual(run_eval.models_used(raw), ["model-a", "model-b"])
        json.dumps(raw)  # raw.json must stay serializable

    def test_three_calls_take_the_last_cumulative_cost(self):
        replies = []
        for index, cumulative in enumerate((0.25, 0.5, 0.75), start=1):
            reply = result(f"r{index}", f"s{index}", cost=0.25)
            reply["total_cost_usd"] = cumulative
            replies.append(reply)
        out = self.run_agent(ScriptedCli(*replies), ["one", "two"])
        self.assertEqual(out["cost_usd"], 0.75)
        self.assertEqual(out["num_turns"], 6)

    # -- followup failures --------------------------------------------------

    def test_a_failed_followup_fails_the_arm_naming_the_turn(self):
        cases = (
            ("nonzero", failed("rate limited"), "nonzero_exit",
             "follow-up 1 of 1: rate limited"),
            ("agent_error", result("refused", is_error=True), "agent_error",
             "follow-up 1 of 1: refused"),
            ("timeout", subprocess.TimeoutExpired(["fake"], 30), "timeout",
             "follow-up 1 of 1: agent timed out after 30s"),
        )
        for label, reply, error, detail in cases:
            with self.subTest(label=label):
                cli = ScriptedCli(result("proposed"), reply)
                out = self.run_agent(cli, ["yes"])
                self.assertEqual(out["error"], error)
                self.assertEqual(out["detail"], detail)

    def test_a_failed_followup_never_echoes_the_array_it_printed(self):
        private = "/home/example/private-workspace"
        array = json.dumps([
            {"type": "system", "subtype": "init", "cwd": private},
            result("stopped at " + private) | {"subtype": "error_max_turns",
                                               "is_error": True}])
        cli = ScriptedCli(result("proposed"),
                          subprocess.CompletedProcess(["fake"], 1, stdout=array,
                                                      stderr=""))
        out = self.run_agent(cli, ["yes"])
        self.assertEqual(out["error"], "nonzero_exit")
        self.assertEqual(out["detail"], "follow-up 1 of 1: result subtype "
                                        "error_max_turns, is_error true")
        self.assertNotIn("private-workspace", json.dumps(out))
        cli = ScriptedCli(result("proposed"),
                          subprocess.CompletedProcess(["fake"], 0,
                                                      stdout=array[:40] + private,
                                                      stderr=""))
        out = self.run_agent(cli, ["yes"])
        self.assertEqual(out["error"], "invalid_json")
        self.assertTrue(out["detail"].startswith("follow-up 1 of 1: stdout is not valid JSON"))
        self.assertNotIn("private-workspace", out["detail"])

    def test_no_session_id_to_resume_is_an_error_before_any_followup(self):
        for session in (None, "", 7):
            with self.subTest(session=session):
                first = result("proposed", session=None)
                if session is not None:
                    first["session_id"] = session
                cli = ScriptedCli(first)
                out = self.run_agent(cli, ["yes"])
                self.assertEqual(out["error"], "invalid_json")
                self.assertIn("follow-up 1 of 1", out["detail"])
                self.assertIn("session_id", out["detail"])
                self.assertEqual(len(cli.calls), 1)


class ValidateFollowupsTests(unittest.TestCase):
    PATH = Path("evals/x/fixture.yaml")

    def test_absent_null_and_a_list_of_strings_pass(self):
        for fixture in ({}, {"followups": None}, {"followups": ["yes"]},
                        {"followups": ["yes", "and the rest"]}):
            with self.subTest(fixture=fixture):
                run_eval.validate_followups(fixture, self.PATH)

    def test_every_other_shape_is_a_named_configuration_error(self):
        for value in ("auto", [], [""], ["  "], ["yes", 3], [None], {"a": "b"},
                      True, 3):
            with self.subTest(value=value):
                with self.assertRaisesRegex(run_eval.guidance.GuidanceError,
                                            "`followups:` must be a non-empty "
                                            "list of non-blank strings"):
                    run_eval.validate_followups({"followups": value}, self.PATH)

    def test_main_refuses_a_bad_value_before_any_cli_call(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = tmp / "evals" / "demo"
        (eval_dir / "seed").mkdir(parents=True)
        (eval_dir / "fixture.yaml").write_text(
            "skill: demo\nregistry: https://github.com/example-adopter/"
            "acsi-agentskills\nmodel: model-a\nprompt: do it\n"
            "followups: auto\n", encoding="utf-8")
        argv = ["run_eval.py", str(eval_dir), "--arm", "without_skill",
                "--results-dir", str(tmp / "results"), "--no-judge"]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(run_eval.subprocess, "run",
                               side_effect=AssertionError("CLI invoked")), \
             redirect_stdout(out):
            rc = run_eval.main()
        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("`followups:` must be", out.getvalue())


class LocalEvalRefusesBadFollowupsTests(unittest.TestCase):
    def test_load_skill_fixture_refuses_a_bad_value_up_front(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "fixture.yaml").write_text(
            "skill: demo\nprompt: do it\nfollowups: auto\n", encoding="utf-8")
        with self.assertRaisesRegex(local_eval.Refused, "`followups:` must be"):
            local_eval.load_skill_fixture(tmp)


class GuidanceArmFollowupTests(unittest.TestCase):
    """The guidance subject's arm passes the fixture's `followups:` to
    `run_agent` too, with delivery, guard and agent patched."""

    def test_the_guidance_arm_forwards_followups(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        args = argparse.Namespace(results_dir=tmp / "results", model="m",
                                  timeout=30, no_judge=True,
                                  harness_version=None)
        ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
               "guidance_dir": tmp, "row": {}, "section": "s",
               "key": "guidance/s"}
        arm = {"name": "treatment", "mode": "section", "objective_checks": []}
        fixture = {"prompt": "do it", "followups": ["yes, go ahead"]}
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}
        guard = {"ok": True, "expected": True, "observed": True, "detail": "d"}
        seen = {}

        def fake_run_agent(workspace, prompt, arm_config):
            seen["followups"] = arm_config.get("followups")
            return {"transcript": "t", "usage": {}, "cost_usd": 0.0,
                    "num_turns": 1, "duration_ms": 1, "raw": {}}

        with mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", return_value=guard), \
             mock.patch.object(run_eval, "run_agent", fake_run_agent):
            run_eval._run_guidance_arm(arm, fixture, tmp / "no-seed", ctx,
                                       args, "T")
        self.assertEqual(seen["followups"], ["yes, go ahead"])


class RenamePdfsFollowupRegressionTests(unittest.TestCase):
    """rename-pdfs' skill asks for per-file confirmation (or `auto`), and one
    `claude -p` call has no user to give it: every with-skill trial in two
    N=3 runs proposed renames and applied none, flooring both arms at 4/8.
    The fixture's follow-up must reach BOTH arms, unchanged."""


    def test_both_arms_receive_the_same_followups(self):
        # The kit ships no rename-pdfs fixture (DECISIONS X9): an invented
        # one with the same shape, a prompt that needs a reply plus one
        # follow-up, stands in.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = tmp / "example-followup"
        (eval_dir / "seed").mkdir(parents=True)
        (eval_dir / "seed" / "README.md").write_text("example seed\n", encoding="utf-8")
        (eval_dir / "fixture.yaml").write_text(
            "skill: example-followup\n"
            "registry: https://github.com/example-adopter/acsi-agentskills\n"
            "prompt: Rename the files in inbox/ per my convention.\n"
            "followups:\n  - Yes, apply all of the proposed renames (auto).\n"
            "arms:\n  with_skill:\n    install: copy\n  without_skill:\n    install: none\n"
            "objective_checks: []\njudge_rubric: none\n", encoding="utf-8")
        fixture = run_eval.load_fixture(eval_dir)
        registry_dir = tmp / "registry"
        registry_dir.mkdir()
        skill_dir = registry_dir / "plugins/test-bundle/skills" / fixture["skill"]
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {fixture['skill']}\ndescription: Offline follow-up fixture.\n---\n",
            encoding="utf-8")
        registries = {"acsi-agentskills": {
            "name": "acsi-agentskills", "path": registry_dir,
            "url": fixture["registry"],
            "layout": "plugins/*/skills/*/SKILL.md"}}
        seen = {}

        def fake_run_agent(workspace, prompt, arm):
            seen[arm["name"]] = (prompt, arm.get("followups"))
            return {"transcript": "done", "usage": {}, "cost_usd": 0.0,
                    "num_turns": 1, "duration_ms": 1, "raw": {}}

        args = argparse.Namespace(model=None, timeout=30, no_judge=True,
                                  results_dir=tmp / "results", roster=None)
        with mock.patch.object(run_eval, "run_agent", fake_run_agent):
            for arm in ("with_skill", "without_skill"):
                run_eval._run_arm(arm, fixture, eval_dir / "seed",
                                  registries, args, "20261005T000000Z")
        self.assertEqual(set(seen), {"with_skill", "without_skill"})
        self.assertEqual(seen["with_skill"], seen["without_skill"])
        self.assertEqual(seen["with_skill"][1], fixture["followups"])


if __name__ == "__main__":
    unittest.main()
