"""Every harness `claude` session is isolated from the account it runs under.

Measured on CLI 2.1.289 (no credential copied): a scratch HOME, a scratch
CLAUDE_CONFIG_DIR and `--bare` all lose the `/login`, so arms and the judge
keep the real HOME and are isolated by flags instead. `--setting-sources
project` alone still loads the account's claude.ai MCP connectors and writes a
transcript under `~/.claude/projects/`; `--strict-mcp-config` drops the
connectors, `--no-session-persistence` the transcript, and a session started
with it cannot be `--resume`d ("No conversation found").

The stand-in below records argv and env and emulates exactly those two CLI
behaviors: without `--no-session-persistence` it writes
`$HOME/.claude/projects/<munged cwd>/<session>.jsonl`, and `--resume` of a
session with no such file fails. HOME and XDG_STATE_HOME are temp dirs; no
network and no real `claude`.
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

import propose_skill_edit  # noqa: E402
import run_canary  # noqa: E402
import run_eval  # noqa: E402
from scorers import judge  # noqa: E402

FLAG = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
NO_PERSIST = "--no-session-persistence"
STRICT = "--strict-mcp-config"

STAND_IN = f"""#!{sys.executable}
import json, os, pathlib, re, sys
argv = sys.argv[1:]
with open(os.environ["ISO_LOG"], "a") as fh:
    fh.write(json.dumps({{"argv": argv, "cwd": os.getcwd(),
                         "flag": os.environ.get({FLAG!r})}}) + "\\n")
config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.environ["HOME"], ".claude")
project = pathlib.Path(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
session = "s1"
if "--resume" in argv:
    session = argv[argv.index("--resume") + 1]
    if not (project / (session + ".jsonl")).is_file():
        print(json.dumps({{"type": "result", "is_error": True,
                          "result": "No conversation found with session ID: " + session}}))
        sys.exit(1)
if {NO_PERSIST!r} not in argv:
    project.mkdir(parents=True, exist_ok=True)
    with open(project / (session + ".jsonl"), "a") as fh:
        fh.write("{{}}\\n")
print(json.dumps({{"type": "result", "is_error": False, "result": "{{}}",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": session,
                  "modelUsage": {{"fake-default-model": {{}}}}}}))
"""


# The PATH and mount table every arm here runs with, explicit: the arm's read
# fence is built from both, so no result depends on the host's (a WSL PATH
# under /mnt/c, its mountinfo). An empty mount table names no alias.
TEST_PATH = os.pathsep.join(dict.fromkeys(
    (str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin")))


def pairs(argv):
    return list(zip(argv, argv[1:]))


class IsolationFlagsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="isoflags-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state"
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        self.log = self.root / "calls.jsonl"
        stand_in = self.root / "claude"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.state),
            "CLAUDE_BIN": str(stand_in), "ISO_LOG": str(self.log),
            "PATH": TEST_PATH, run_eval.MOUNTINFO_ENV: str(mountinfo)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.projects = self.home / ".claude" / "projects"
        self.session_dir = self.projects / run_eval._munged_project_name(self.ws)

    def skill_arm(self, **extra):
        # A skill arm's env is an allowlist; the fixture `env:` block is how
        # a name like the stand-in's log path reaches it.
        return {"name": "without_skill", "timeout": 60,
                "env": {"ISO_LOG": str(self.log)}, **extra}

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def archived(self):
        root = self.state / "skills-evals" / "sessions"
        return sorted(p.relative_to(root).parts[1:] for p in root.rglob("*.jsonl")) \
            if root.exists() else []

    # -- skill arms ------------------------------------------------------

    def test_a_one_turn_arm_is_strict_and_writes_no_transcript(self):
        out = run_eval.run_agent(self.ws, "do it", self.skill_arm())
        self.assertNotIn("error", out, out)
        (call,) = self.calls()
        self.assertIn(("--setting-sources", "project"), pairs(call["argv"]))
        self.assertIn(STRICT, call["argv"])
        self.assertIn(NO_PERSIST, call["argv"])
        self.assertEqual(call["flag"], "1")
        self.assertFalse(self.projects.exists())
        self.assertEqual(self.archived(), [])

    def test_a_multi_turn_arm_persists_resumes_and_is_archived(self):
        out = run_eval.run_agent(self.ws, "do it", self.skill_arm(followups=["yes", "more"]))
        self.assertNotIn("error", out, out)
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertIn(STRICT, call["argv"])
            self.assertNotIn(NO_PERSIST, call["argv"])
            self.assertEqual(call["flag"], "1")
        self.assertEqual(calls[2]["argv"][-2:], ["--resume", "s1"])
        # The transcript left the profile for the harness's archive.
        self.assertFalse(self.session_dir.exists())
        self.assertEqual(self.archived(), [(self.session_dir.name, "s1.jsonl")])

    def test_a_failed_multi_turn_arm_is_archived_too(self):
        with mock.patch.object(run_eval, "normalize_cli_result",
                               side_effect=[{"result": "a", "session_id": "s1"},
                                            ValueError("bad shape")]):
            out = run_eval.run_agent(self.ws, "do it", self.skill_arm(followups=["yes"]))
        self.assertEqual(out["error"], "invalid_json")
        self.assertFalse(self.session_dir.exists())
        self.assertEqual(len(self.archived()), 1)

    def test_a_preexisting_project_dir_is_never_touched(self):
        self.session_dir.mkdir(parents=True)
        (self.session_dir / "theirs.jsonl").write_text("{}\n")
        out = run_eval.run_agent(self.ws, "do it", self.skill_arm(followups=["yes"]))
        self.assertNotIn("error", out, out)
        self.assertEqual(sorted(p.name for p in self.session_dir.iterdir()),
                         ["s1.jsonl", "theirs.jsonl"])
        self.assertEqual(self.archived(), [])

    def test_a_scratch_config_dir_is_left_for_the_scratch_cleanup(self):
        scratch = self.root / "arm-scratch"
        config = scratch / "config"
        config.mkdir(parents=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
               "CLAUDE_CONFIG_DIR": str(config), "ISO_LOG": str(self.log)}
        out = run_eval.run_agent(self.ws, "do it", {
            "name": "guidance", "timeout": 60, "followups": ["yes"],
            "env_override": env, "session_scratch": str(scratch)})
        self.assertNotIn("error", out, out)
        self.assertTrue((config / "projects" / self.session_dir.name
                         / "s1.jsonl").is_file())
        self.assertEqual(self.archived(), [])

    def test_the_munged_name_is_the_clis(self):
        # From the CLI's own init event (`memory_paths`) for this cwd.
        self.assertEqual(run_eval._munged_project_name(Path("/tmp/iso-ws.Yzsnc9")),
                         "-tmp-iso-ws-Yzsnc9")

    def test_the_munged_name_uses_the_resolved_workspace_path(self):
        # The child's cwd is the real path (getcwd resolves symlinks), so a
        # workspace reached through a symlinked parent is keyed by the
        # target. Temp dirs only.
        real_parent = self.root / "real-parent"
        (real_parent / "ws.1").mkdir(parents=True)
        link = self.root / "link-parent"
        link.symlink_to(real_parent, target_is_directory=True)
        via_link = link / "ws.1"
        resolved = os.path.realpath(real_parent / "ws.1")
        self.assertNotEqual(str(via_link), resolved)
        want = "".join(c if c.isascii() and c.isalnum() else "-" for c in resolved)
        self.assertEqual(run_eval._munged_project_name(via_link), want)
        # And end to end: the stand-in writes under the name the CLI would
        # use, and the arm archives exactly that directory.
        out = run_eval.run_agent(via_link, "do it", self.skill_arm(followups=["yes"]))
        self.assertNotIn("error", out, out)
        self.assertFalse((self.projects / want).exists())
        self.assertEqual(self.archived(), [(want, "s1.jsonl")])

    def test_a_name_past_the_cli_truncation_is_never_moved(self):
        long_dir = self.projects / ("-" + "a" * 220)
        long_dir.mkdir(parents=True)
        self.assertIsNone(run_eval._archive_session_dir(long_dir))
        self.assertTrue(long_dir.is_dir())

    # -- judge -----------------------------------------------------------

    def test_the_judge_loads_no_settings_no_connectors_and_keeps_no_transcript(self):
        judge._run_judge_cli("score this", model=None, timeout=60)
        (call,) = self.calls()
        self.assertIn(("--setting-sources", ""), pairs(call["argv"]))
        self.assertIn(STRICT, call["argv"])
        self.assertIn(NO_PERSIST, call["argv"])
        self.assertEqual(call["flag"], "1")
        self.assertFalse(self.projects.exists())

    # -- canary ----------------------------------------------------------

    def test_a_canary_leg_under_the_real_home_keeps_no_transcript(self):
        out = run_canary.run_leg(self.ws, "probe", "Read", model=None, timeout=60)
        self.assertNotIn("error", out, out)
        (call,) = self.calls()
        self.assertIn(STRICT, call["argv"])
        self.assertIn(NO_PERSIST, call["argv"])
        self.assertFalse(self.projects.exists())

    def test_a_canary_leg_with_its_own_config_dir_keeps_the_default(self):
        config = self.root / "scratch-config"
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
               "CLAUDE_CONFIG_DIR": str(config), "ISO_LOG": str(self.log)}
        out = run_canary.run_leg(self.ws, "probe", "Read", model=None,
                                 timeout=60, env=env)
        self.assertNotIn("error", out, out)
        (call,) = self.calls()
        self.assertIn(STRICT, call["argv"])
        self.assertNotIn(NO_PERSIST, call["argv"])

    # -- propose_skill_edit ----------------------------------------------

    def test_the_proposal_call_uses_the_judges_flags(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                {"type": "result", "is_error": False, "result": "ok"}), stderr="")

        class Guarded:
            def __enter__(self):
                return {"CLAUDE_BIN": "claude", FLAG: "1"}

            def __exit__(self, *exc):
                return False

        with mock.patch.object(propose_skill_edit, "guarded_environment", Guarded), \
             mock.patch.object(propose_skill_edit.subprocess, "run", fake_run):
            propose_skill_edit.Runner().propose("p", "m")
        cmd = captured["cmd"]
        self.assertIn(("--setting-sources", ""), pairs(cmd))
        self.assertIn(STRICT, cmd)
        self.assertIn(NO_PERSIST, cmd)
        self.assertEqual(judge.JUDGE_ISOLATION_FLAGS,
                         ("--setting-sources", "", STRICT, NO_PERSIST))


if __name__ == "__main__":
    unittest.main()
