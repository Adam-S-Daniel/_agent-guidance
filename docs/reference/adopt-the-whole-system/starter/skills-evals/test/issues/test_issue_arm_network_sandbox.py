"""Every agent arm runs behind Claude Code's network sandbox (ADR 0011).

A real-work seed is a public repository's pre-fix tree, so an arm that can
reach GitHub can read the merged fix. Every agent arm (skill, guidance, every
follow-up turn) gets `--settings <sandbox JSON>` and `--disallowedTools
WebFetch,WebSearch`; the judge does not. Nothing is written into the
workspace, so `seed_guard` and the scoring checks see exactly what they saw
before. A CLI that cannot start the sandbox fails the arm with
`sandbox_unavailable`.

The stand-in below records argv; no network and no real `claude`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import run_eval  # noqa: E402
import seed_prep  # noqa: E402
from scorers import judge, objective  # noqa: E402

FAKE_REGISTRY = ROOT / "test" / "fixtures" / "fake_registry"

STAND_IN = f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["SBX_LOG"], "a") as fh:
    fh.write(json.dumps({{"argv": argv}}) + "\\n")
mode = os.environ.get("SBX_MODE", "ok")
if mode == "unavailable":
    sys.stderr.write(
        "\\nError: sandbox required but unavailable: sandbox is enabled but "
        "dependencies are missing: socat not installed\\n"
        "  sandbox.failIfUnavailable is set \\u2014 refusing to start without a "
        "working sandbox.\\n")
    sys.exit(1)
if mode == "other_failure":
    sys.stderr.write("boom\\n")
    sys.exit(1)
print(json.dumps({{"type": "result", "is_error": False, "result": "done",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": "s1",
                  "modelUsage": {{"fake-default-model": {{}}}}}}))
"""

REQUIRED_DENIED = {"github.com", "*.github.com", "githubusercontent.com",
                   "*.githubusercontent.com", "codeload.github.com",
                   "example-site-a", "*.example-site-a", "example-site-b",
                   "*.example-site-b", "cdn.jsdelivr.net"}


def flag_value(argv: list[str], flag: str) -> str:
    indexes = [i for i, arg in enumerate(argv) if arg == flag]
    if len(indexes) != 1:
        raise AssertionError(f"{flag} appears {len(indexes)} times in {argv}")
    return argv[indexes[0] + 1]


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class ArmSandboxSettingsTests(unittest.TestCase):
    def test_the_settings_deny_github_and_fail_closed_with_no_escape(self):
        # Spelled out, not rebuilt from the helper: a widening key or an
        # allowed domain added to the settings must fail here. The filesystem
        # block is the read fence (test_issue_arm_read_isolation pins it).
        flags = run_eval.arm_isolation_flags()
        settings = json.loads(flag_value(flags, "--settings"))
        self.assertEqual(set(settings), {"sandbox", "permissions"})
        sandbox = dict(settings["sandbox"])
        self.assertEqual(set(sandbox.pop("filesystem")),
                         {"denyRead", "allowRead", "denyWrite"})
        self.assertEqual(sandbox, {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "excludedCommands": [],
            "network": {
                "strictAllowlist": True,
                "allowedDomains": [],
                "deniedDomains": [
                    "github.com", "*.github.com", "codeload.github.com",
                    "githubusercontent.com", "*.githubusercontent.com",
                    "example-site-a", "*.example-site-a", "example-site-b",
                    "*.example-site-b", "cdn.jsdelivr.net"],
                "allowAllUnixSockets": False,
                "allowUnixSockets": [],
                "allowLocalBinding": False,
            },
        })
        self.assertLessEqual(REQUIRED_DENIED, set(sandbox["network"]["deniedDomains"]))
        self.assertEqual(set(settings["permissions"]), {"deny"})
        self.assertTrue(all(rule.startswith("Read(//")
                            for rule in settings["permissions"]["deny"]))
        self.assertEqual(flags[2:], ["--disallowedTools", "WebFetch,WebSearch",
                                     "--no-chrome"])

    def test_the_web_tools_are_removed(self):
        tools = flag_value(run_eval.arm_isolation_flags(), "--disallowedTools")
        self.assertEqual(tools.split(","), ["WebFetch", "WebSearch"])


class _StandInBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arm-sandbox-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "calls.jsonl"
        stand_in = self.root / "claude"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.root / "state"),
            "CLAUDE_BIN": str(stand_in), "SBX_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line)["argv"] for line in self.log.read_text().splitlines()]

    def arm(self, mode: str = "ok", **extra) -> dict:
        return {"name": "without_skill", "timeout": 60,
                "env": {"SBX_LOG": str(self.log), "SBX_MODE": mode}, **extra}

    def assert_sandboxed(self, argv: list[str]) -> None:
        settings = json.loads(flag_value(argv, "--settings"))
        self.assertEqual(settings["sandbox"]["network"],
                         run_eval.arm_sandbox_settings()["sandbox"]["network"])
        self.assertIs(settings["sandbox"]["failIfUnavailable"], True)
        self.assertIn(str(self.home.resolve()),
                      settings["sandbox"]["filesystem"]["denyRead"])
        self.assertEqual(flag_value(argv, "--disallowedTools"), "WebFetch,WebSearch")


class RunAgentSandboxTests(_StandInBase):
    def setUp(self):
        super().setUp()
        self.seed = self.root / "seed"
        self.seed.mkdir()
        (self.seed / "calc.py").write_text("x = 1\n", encoding="utf-8")
        self.ws = run_eval.materialize_workspace(self.seed, {})
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_a_without_skill_arm_is_sandboxed(self):
        out = run_eval.run_agent(self.ws, "do it", self.arm())
        self.assertNotIn("error", out, out)
        (argv,) = self.calls()
        self.assert_sandboxed(argv)

    def test_a_with_skill_arm_is_sandboxed(self):
        out = run_eval.run_agent(self.ws, "do it", self.arm(
            name="with_skill", skill="other-skill", registry=FAKE_REGISTRY))
        self.assertNotIn("error", out, out)
        (argv,) = self.calls()
        self.assert_sandboxed(argv)

    def test_a_guidance_shaped_arm_is_sandboxed(self):
        # `_run_guidance_arm` hands run_agent its own setting sources and
        # environment; the sandbox flags do not depend on either.
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "SBX_LOG": str(self.log), "SBX_MODE": "ok"}
        out = run_eval.run_agent(self.ws, "do it", {
            "name": "with_guidance", "timeout": 60,
            "setting_sources": "user,project", "env_override": env})
        self.assertNotIn("error", out, out)
        (argv,) = self.calls()
        self.assertIn("user,project", argv)
        self.assert_sandboxed(argv)

    def test_every_followup_turn_is_sandboxed(self):
        out = run_eval.run_agent(self.ws, "do it",
                                 self.arm(followups=["and again", "once more"]))
        self.assertNotIn("error", out, out)
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        for argv in calls:
            self.assert_sandboxed(argv)
        self.assertIn("--resume", calls[1])

    def test_a_sandbox_that_cannot_start_is_a_named_error(self):
        out = run_eval.run_agent(self.ws, "do it", self.arm("unavailable"))
        self.assertEqual(out["error"], "sandbox_unavailable")
        self.assertIn("sandbox required but unavailable", out["detail"])
        self.assertIn("socat not installed", out["detail"])
        self.assertEqual(out["returncode"], 1)

    def test_another_cli_failure_stays_nonzero_exit(self):
        out = run_eval.run_agent(self.ws, "do it", self.arm("other_failure"))
        self.assertEqual(out["error"], "nonzero_exit")

    def test_the_harness_writes_no_settings_into_the_workspace(self):
        self.assertFalse(os.path.lexists(self.ws / ".claude"))
        run_eval.run_agent(self.ws, "do it", self.arm())
        self.assertFalse(os.path.lexists(self.ws / ".claude"))
        tracked = run_eval._git("ls-files", cwd=self.ws).stdout.split()
        self.assertEqual(tracked, ["calc.py"])
        fixture = {"objective_checks": [
            {"id": "settings-untouched", "type": "files_unchanged",
             "paths": [".claude/*", ".claude/*/*", "*"]}]}
        (result,) = objective.run_checks(fixture, str(self.ws), str(self.seed))
        self.assertTrue(result["passed"], result)

    def test_a_with_skill_workspace_holds_only_the_skill_under_claude(self):
        arm = self.arm(name="with_skill", skill="other-skill", registry=FAKE_REGISTRY)
        self.assertIsNone(run_eval.install_skill(self.ws, arm))
        run_eval.run_agent(self.ws, "do it", arm)
        self.assertEqual(os.listdir(self.ws / ".claude"), ["skills"])

    def test_seed_guard_keeps_refusing_any_claude_settings(self):
        # No exception was carved out for the sandbox: delivery is a flag.
        ws = self.root / "planted"
        (ws / ".claude").mkdir(parents=True)
        (ws / ".claude" / "settings.json").write_text(
            json.dumps(run_eval.arm_sandbox_settings()), encoding="utf-8")
        error = seed_prep.seed_guard(ws, {"strip_agent_context": True})
        self.assertEqual(error["error"], "seed_not_stripped")
        self.assertIn(".claude is present", error["detail"])


class JudgeIsNotSandboxedTests(_StandInBase):
    def test_the_judge_gets_no_arm_sandbox_flags(self):
        judge._run_judge_cli("score this", model=None, timeout=60)
        (argv,) = self.calls()
        self.assertNotIn("--settings", argv)
        self.assertNotIn("--disallowedTools", argv)


if __name__ == "__main__":
    unittest.main()
