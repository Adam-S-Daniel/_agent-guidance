"""No CLI session the harness starts may write auto-memory into the real HOME.

Observed 2026-10-05: a local rename-pdfs trial (skill arms run under the
operator's real HOME, where the `/login` credential lives) wrote `MEMORY.md`
and a fixture-derived note into `~/.claude/projects/-tmp-workspace-*/memory/`.
The CLI's switch is `CLAUDE_CODE_DISABLE_AUTO_MEMORY` (2.1.289: "1" turns it
off; a falsy value forces it on), so every session sink must SET it.

The stand-in below emulates that one CLI behavior: unless the variable is
truthy, it writes `$HOME/.claude/projects/<munged cwd>/memory/MEMORY.md`, as
the real CLI does when its agent saves a memory. HOME is a temp directory, so
nothing here touches the real profile; no network, no real `claude`.
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
sys.path.insert(0, str(ROOT / "scripts"))

import guidance  # noqa: E402
import local_eval  # noqa: E402
import run_canary  # noqa: E402
import run_eval  # noqa: E402
from scorers import judge  # noqa: E402

FLAG = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"

STAND_IN = f"""#!{sys.executable}
import json, os, pathlib
# stdin is never read: an inherited open pipe would block forever.
value = os.environ.get({FLAG!r}, "").strip().lower()
if value not in ("1", "true", "yes", "on"):
    munged = os.getcwd().replace("/", "-").replace(".", "-")
    memory = pathlib.Path(os.environ["HOME"], ".claude", "projects", munged, "memory")
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "MEMORY.md").write_text("- fixture note\\n")
print(json.dumps({{"type": "result", "is_error": False, "result": "{{}}",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": "s",
                  "modelUsage": {{"fake-default-model": {{}}}}}}))
"""


# The PATH and mount table every arm here runs with, explicit: the arm's read
# fence is built from both, so no result depends on the host's (a WSL PATH
# under /mnt/c, its mountinfo). An empty mount table names no alias.
TEST_PATH = os.pathsep.join(dict.fromkeys(
    (str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin")))


class AutoMemoryIsOffForEverySession(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="automem-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "operator-home"
        self.home.mkdir()
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        stand_in = self.root / "claude"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
        # The ambient environment tries to force auto-memory ON: the sinks
        # must override it, not merely forward whatever the parent had.
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home),
                                               "CLAUDE_BIN": str(stand_in),
                                               FLAG: "0", "PATH": TEST_PATH,
                                               run_eval.MOUNTINFO_ENV: str(mountinfo)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def memory_files(self):
        return sorted(str(p.relative_to(self.home))
                      for p in (self.home / ".claude").rglob("MEMORY.md"))

    def test_the_stand_in_writes_memory_when_the_flag_is_absent(self):
        # Proves the check below can fail: the same stand-in, spawned with the
        # ambient environment unchanged, writes into HOME.
        import subprocess
        subprocess.run([os.environ["CLAUDE_BIN"], "-p", "x"], cwd=self.ws,
                       capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL, check=True)
        self.assertEqual(len(self.memory_files()), 1)

    def test_skill_arm(self):
        result = run_eval.run_agent(self.ws, "do it", {
            "name": "without_skill", "timeout": 60,
            "env": {FLAG: "0"}})  # a fixture's `env:` cannot re-enable it
        self.assertNotIn("error", result, result)
        self.assertEqual(self.memory_files(), [])

    def test_guidance_arm_env_override(self):
        override = {"PATH": TEST_PATH, "HOME": str(self.home),
                    FLAG: "0"}
        result = run_eval.run_agent(self.ws, "do it", {
            "name": "guidance", "timeout": 60, "env_override": override})
        self.assertNotIn("error", result, result)
        self.assertEqual(self.memory_files(), [])
        self.assertEqual(override[FLAG], "0", "the caller's mapping was mutated")

    def test_judge(self):
        judge._run_judge_cli("score this", model=None, timeout=60)
        self.assertEqual(self.memory_files(), [])

    def test_canary_leg_inherited_env(self):
        result = run_canary.run_leg(self.ws, "probe", "Read", model=None, timeout=60)
        self.assertNotIn("error", result, result)
        self.assertEqual(self.memory_files(), [])

    def test_canary_leg_explicit_env(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home), FLAG: "no"}
        result = run_canary.run_leg(self.ws, "probe", "Read", model=None,
                                    timeout=60, env=env)
        self.assertNotIn("error", result, result)
        self.assertEqual(self.memory_files(), [])


class ForcedEnvIsSetNotInherited(unittest.TestCase):
    def test_the_forced_value_is_the_cli_off_switch(self):
        self.assertEqual(guidance.CLI_FORCED_ENV, {FLAG: "1"})

    def test_cli_child_env_overrides_and_copies(self):
        parent = {"PATH": "/bin", FLAG: "0"}
        env = guidance.cli_child_env(parent)
        self.assertEqual(env, {"PATH": "/bin", FLAG: "1"})
        self.assertEqual(parent[FLAG], "0")

    def test_cli_child_env_defaults_to_this_process(self):
        with mock.patch.dict(os.environ, {"AUTOMEM_PROBE": "x"}, clear=False):
            env = guidance.cli_child_env()
        self.assertEqual(env["AUTOMEM_PROBE"], "x")
        self.assertEqual(env[FLAG], "1")

    def test_local_eval_child_environment(self):
        for parent in ({}, {FLAG: "0"}, {"PATH": "/bin", "HOME": "/h"}):
            with self.subTest(parent=parent):
                self.assertEqual(local_eval.child_environment(parent)[FLAG], "1")


if __name__ == "__main__":
    unittest.main()
