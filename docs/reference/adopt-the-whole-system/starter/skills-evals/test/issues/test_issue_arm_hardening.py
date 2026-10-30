"""The agent arm's other ways out of the sandbox (ADR 0011, hardening addendum).

Hooks run outside the sandbox, and what the CLI loads from the workspace's
`.claude/` is the arm's own configuration, so the agent may not write there:
`Edit(...)` deny rules cover the file tools, `denyWrite` covers Bash, and a
trial in which anything under `.claude/` changed during a turn fails after
that turn (`agent_wrote_agent_config`). Trusted hooks are left running. Claude in Chrome is a
browser outside the sandbox, so every turn passes `--no-chrome` and
`CLAUDE_CODE_ENABLE_CFC` never reaches the arm. A managed policy that turns
the sandbox off silently, or widens it past what `--settings` can take back,
refuses the run (`managed_sandbox_policy`). The CLI's own sandbox-down lines
are matched by their shape, an exit-0 "Sandbox disabled" warning included.

The CLI is a stand-in that records argv and environment. No network, no real
`claude`.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import run_eval  # noqa: E402

STAND_IN = f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["HD_LOG"], "a") as fh:
    fh.write(json.dumps({{"argv": argv, "cfc": os.environ.get("CLAUDE_CODE_ENABLE_CFC")}}) + "\\n")
mode = os.environ.get("HD_MODE", "ok")
turn = sum(1 for _ in open(os.environ["HD_LOG"]))
target = os.environ.get("HD_TARGET", ".claude/settings.json")
if mode == "write_config" and turn == int(os.environ.get("HD_TURN", "1")):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as fh:
        json.dump({{"hooks": {{"SessionStart": [{{"hooks": [
            {{"type": "command", "command": "curl https://github.com/x"}}]}}]}}}}, fh)
if mode == "bookkeeping":
    os.makedirs(".claude/.cc-writes", exist_ok=True)
if mode == "sandbox_error":
    sys.stderr.write("\\n\\u274c Sandbox Error: bwrap failed to start\\n")
    sys.exit(1)
if mode == "disabled_warning":
    sys.stderr.write("\\n\\u26a0 Sandbox disabled: socat not installed\\n"
                     "  Commands will run WITHOUT sandboxing.\\n")
if mode == "quoted":
    sys.stderr.write("note: the docs say 'Error: sandbox required but "
                     "unavailable: x' when socat is missing\\n")
    sys.exit(1)
print(json.dumps({{"type": "result", "is_error": False, "result": "done",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": "s1",
                  "modelUsage": {{"fake-default-model": {{}}}}}}))
"""


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class _StandIn(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arm-hardening-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "calls.jsonl"
        stand_in = self.root / "claude"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.root / "state"),
            "CLAUDE_BIN": str(stand_in), "HD_LOG": str(self.log),
            "CLAUDE_CODE_ENABLE_CFC": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        # No managed policy on the test host leaks into these tests.
        for name, value in (("MANAGED_SETTINGS_FILES", ()),
                            ("MANAGED_SETTINGS_DROPINS", ())):
            patcher = mock.patch.object(run_eval, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ws = Path(tempfile.mkdtemp(prefix="workspace-")).resolve()
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_arm(self, mode: str = "ok", **extra) -> dict:
        arm = {"name": "without_skill", "timeout": 60,
               "env": {"HD_LOG": str(self.log), "HD_MODE": mode,
                       **extra.pop("env", {})}, **extra}
        return run_eval.run_agent(self.ws, "do it", arm)


class AgentConfigTests(_StandIn):
    def settings(self, argv: list[str]) -> dict:
        return json.loads(argv[argv.index("--settings") + 1])

    def test_every_turn_denies_writes_under_the_workspaces_claude_dir(self):
        out = self.run_arm(followups=["again"])
        self.assertNotIn("error", out, out)
        config, profile = self.ws / ".claude", self.home / ".claude"
        for call in self.calls():
            settings = self.settings(call["argv"])
            # Spelled out: the file tools (Edit rules cover Edit, Write and
            # NotebookEdit) and Bash, for the workspace's `.claude/` and the
            # profile the session loads as user settings.
            for path in (config, profile):
                self.assertIn(f"Edit(/{path})", settings["permissions"]["deny"])
                self.assertIn(f"Edit(/{path}/**)", settings["permissions"]["deny"])
            self.assertEqual(settings["sandbox"]["filesystem"]["denyWrite"],
                             [str(config), str(profile)])
            # Trusted hooks are not switched off.
            self.assertNotIn("disableAllHooks", settings)

    def test_config_written_in_the_first_turn_fails_it_unresumed(self):
        for target in (".claude/settings.json", ".claude/hooks/h.sh",
                       ".claude/skills/x/SKILL.md", ".claude/agents/a.md",
                       ".claude/commands/c.md", ".claude/settings.local.json"):
            with self.subTest(target=target):
                self.log.unlink(missing_ok=True)
                shutil.rmtree(self.ws / ".claude", ignore_errors=True)
                out = self.run_arm("write_config", followups=["again"],
                                   env={"HD_TARGET": target})
                self.assertEqual(out["error"], "agent_wrote_agent_config")
                self.assertIn(target, out["detail"])
                self.assertEqual(len(self.calls()), 1)

    def test_config_written_on_a_later_turn_fails_after_that_turn(self):
        out = self.run_arm("write_config", followups=["again"],
                           env={"HD_TURN": "2"})
        self.assertEqual(out["error"], "agent_wrote_agent_config")
        self.assertIn("follow-up 1 of 1", out["detail"])

    def test_a_one_turn_arm_is_checked_too(self):
        out = self.run_arm("write_config")
        self.assertEqual(out["error"], "agent_wrote_agent_config")

    def test_what_the_harness_installed_and_the_clis_bookkeeping_pass(self):
        (self.ws / ".claude" / "settings.json").parent.mkdir()
        (self.ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        out = self.run_arm("bookkeeping", followups=["again"])
        self.assertNotIn("error", out, out)

    def test_changing_or_removing_an_installed_file_fails(self):
        skill = self.ws / ".claude" / "skills" / "x"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("one", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws)
        (skill / "SKILL.md").write_text("two", encoding="utf-8")
        self.assertEqual(run_eval._agent_config_written(self.ws, before),
                         ".claude/skills/x/SKILL.md")
        (skill / "SKILL.md").unlink()
        self.assertEqual(run_eval._agent_config_written(self.ws, before),
                         ".claude/skills/x/SKILL.md")


class GuidanceProfileTests(_StandIn):
    """A guidance arm's scratch CLAUDE_CONFIG_DIR is its user settings."""

    def setUp(self):
        super().setUp()
        self.scratch_home = self.root / "scratch" / "home"
        self.config = self.root / "scratch" / "config"
        for path in (self.scratch_home, self.config):
            path.mkdir(parents=True)
        # What delivery put there before the arm: the baseline.
        (self.config / "CLAUDE.md").write_text("guidance\n", encoding="utf-8")

    def run_guidance(self, mode: str = "ok", target: str | None = None) -> dict:
        env = {"PATH": os.environ["PATH"], "HOME": str(self.scratch_home),
               "CLAUDE_CONFIG_DIR": str(self.config), "HD_LOG": str(self.log),
               "HD_MODE": mode, "HD_TARGET": target or ""}
        return run_eval.run_agent(self.ws, "do it", {
            "name": "with_guidance", "timeout": 60,
            "setting_sources": "user,project", "env_override": env,
            "followups": ["again"]})

    def test_the_profile_is_write_denied(self):
        out = self.run_guidance()
        self.assertNotIn("error", out, out)
        settings = json.loads(self.calls()[0]["argv"][
            self.calls()[0]["argv"].index("--settings") + 1])
        self.assertIn(str(self.config), settings["sandbox"]["filesystem"]["denyWrite"])
        self.assertIn(f"Edit(/{self.config}/**)", settings["permissions"]["deny"])

    def test_a_hook_written_into_the_profile_fails_the_trial(self):
        for rel in ("settings.json", "CLAUDE.md", "skills/x/SKILL.md",
                    "agents/a.md", "hooks/h.sh"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_guidance("write_config", str(self.config / rel))
                self.assertEqual(out["error"], "agent_wrote_agent_config")
                self.assertIn(f"$CLAUDE_CONFIG_DIR/{rel}", out["detail"])
                self.assertEqual(len(self.calls()), 1)
                (self.config / rel).unlink()
                (self.config / "CLAUDE.md").write_text("guidance\n", encoding="utf-8")

    def test_a_marker_is_exempt_only_as_a_small_regular_file(self):
        marker = self.config / "plugins" / "cache" / "m" / "p" / "1" / ".orphaned_at"
        marker.parent.mkdir(parents=True)
        marker.write_text("1\n", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        marker.write_text("2\n", encoding="utf-8")
        self.assertIsNone(run_eval._agent_config_written(self.ws, before, self.config))
        marker.unlink()
        (marker / "hooks").mkdir(parents=True)
        (marker / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
        self.assertIn(".orphaned_at", run_eval._agent_config_written(
            self.ws, before, self.config))
        shutil.rmtree(marker)
        marker.write_text("x" * 5000, encoding="utf-8")
        self.assertIsNotNone(run_eval._agent_config_written(self.ws, before, self.config))
        marker.unlink()
        marker.symlink_to(self.config / "CLAUDE.md")
        self.assertIsNotNone(run_eval._agent_config_written(self.ws, before, self.config))
        marker.unlink()
        lock = self.config / ".claude.json.lock"
        (lock / "hooks").mkdir(parents=True)
        (lock / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
        self.assertIn(".claude.json.lock", run_eval._agent_config_written(
            self.ws, before, self.config))

    def test_fleet_delivery_receipt_changes_during_a_turn_are_bookkeeping(self):
        # The trusted delivery hook can finish its detached receipt append
        # after run_agent captured its baseline. No timing assumption is
        # needed: perform each change while its first CLI call is returning.
        receipt = self.config / "fleet-delivery.jsonl"
        real_run = run_eval.subprocess.run
        for action in ("create", "append", "delete"):
            with self.subTest(action=action):
                self.log.unlink(missing_ok=True)
                receipt.unlink(missing_ok=True)
                if action != "create":
                    receipt.write_text('{"mode":"hook"}\n', encoding="utf-8")
                changed = False

                def during_turn(cmd, *args, **kwargs):
                    nonlocal changed
                    result = real_run(cmd, *args, **kwargs)
                    if cmd[0] == str(self.root / "claude") and not changed:
                        changed = True
                        if action == "delete":
                            receipt.unlink()
                        else:
                            with receipt.open("a", encoding="utf-8") as stream:
                                stream.write('{"mode":"hook"}\n')
                    return result

                with mock.patch.object(run_eval.subprocess, "run", during_turn):
                    out = self.run_guidance()
                self.assertTrue(changed)
                self.assertNotIn("error", out, out)
                self.assertEqual(len(self.calls()), 2)

    def test_fleet_delivery_receipt_final_bounded_append_is_bookkeeping(self):
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        receipt = self.config / "fleet-delivery.jsonl"
        # The writer checks the 1 MiB limit before its last <=4096-byte append.
        receipt.write_bytes(b"x" * (1024 * 1024 + 4096))
        self.assertIsNone(run_eval._agent_config_written(self.ws, before, self.config))

    def test_fleet_delivery_receipt_unsafe_shapes_are_watched(self):
        receipt = self.config / "fleet-delivery.jsonl"
        outside = self.root / "outside.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        for shape in ("symlink", "directory", "oversized", "hardlink"):
            with self.subTest(shape=shape):
                if shape == "symlink":
                    receipt.symlink_to(outside)
                elif shape == "directory":
                    receipt.mkdir()
                    (receipt / "hooks.json").write_text("{}", encoding="utf-8")
                elif shape == "oversized":
                    receipt.write_bytes(b"x" * (1024 * 1024 + 4097))
                else:
                    os.link(outside, receipt)
                self.assertIn("$CLAUDE_CONFIG_DIR/fleet-delivery.jsonl",
                              run_eval._agent_config_written(self.ws, before, self.config))
                if receipt.is_dir():
                    shutil.rmtree(receipt)
                else:
                    receipt.unlink()

    def test_fleet_delivery_receipt_foreign_owner_is_watched(self):
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        receipt = self.config / "fleet-delivery.jsonl"
        receipt.write_text("{}\n", encoding="utf-8")
        real_lstat = os.lstat

        def foreign_owner(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) == receipt:
                fields = list(info)
                fields[4] = info.st_uid + 1
                return os.stat_result(fields)
            return info

        with mock.patch.object(run_eval.os, "lstat", foreign_owner):
            self.assertEqual(run_eval._agent_config_written(self.ws, before, self.config),
                             "$CLAUDE_CONFIG_DIR/fleet-delivery.jsonl")

    def test_fleet_delivery_receipt_without_an_owner_check_is_watched(self):
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        receipt = self.config / "fleet-delivery.jsonl"
        receipt.write_text("{}\n", encoding="utf-8")
        with mock.patch.object(run_eval.os, "geteuid", create=True):
            del run_eval.os.geteuid
            self.assertEqual(run_eval._agent_config_written(self.ws, before, self.config),
                             "$CLAUDE_CONFIG_DIR/fleet-delivery.jsonl")

    def test_fleet_delivery_receipt_exemption_is_exact_and_profile_only(self):
        for rel in ("fleet-delivery.jsonl.other", "nested/fleet-delivery.jsonl"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_guidance("write_config", str(self.config / rel))
                self.assertEqual(out["error"], "agent_wrote_agent_config")
                self.assertIn(f"$CLAUDE_CONFIG_DIR/{rel}", out["detail"])
        self.log.unlink(missing_ok=True)
        out = self.run_guidance("write_config", str(self.ws / ".claude/fleet-delivery.jsonl"))
        self.assertEqual(out["error"], "agent_wrote_agent_config")
        self.assertIn(".claude/fleet-delivery.jsonl", out["detail"])

    # CLI 2.1.304 startup writes in a fresh profile, by size cap.
    STARTUP_MARKERS = {".last-cleanup": 4096, "plugins/.last_inuse_sweep": 4096,
                       "policy-limits.json": 65536,
                       "policy-limits.json.signature.json": 65536,
                       "policy-limits.json.stamp.json": 4096,
                       # 2026-10-27: the signature's issue time, beside it.
                       "policy-limits.json.signature-iat.json": 65536,
                       "remote-settings.json.synced-at": 4096,
                       "plugins/plugin-directory-cache-v2.json": 16 * 1024 * 1024,
                       # CLI 2.1.311: a built-in plugin's store, every session.
                       "plugins/store/cc-plugin-agents-md_builtin-b049b47b3618.json": 65536}

    def _remove(self, path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    def test_cli_startup_markers_are_exempt_as_small_regular_files(self):
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        for rel, limit in self.STARTUP_MARKERS.items():
            with self.subTest(rel=rel):
                marker = self.config / rel
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"x" * limit)
                self.assertIsNone(run_eval._agent_config_written(
                    self.ws, before, self.config))
                marker.unlink()

    def test_cli_startup_markers_unsafe_shapes_are_watched(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        for rel, limit in self.STARTUP_MARKERS.items():
            for shape in ("symlink", "directory", "oversized"):
                with self.subTest(rel=rel, shape=shape):
                    marker = self.config / rel
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    if shape == "symlink":
                        marker.symlink_to(outside)
                    elif shape == "directory":
                        (marker / "hooks").mkdir(parents=True)
                        (marker / "hooks" / "hooks.json").write_text(
                            "{}", encoding="utf-8")
                    else:
                        marker.write_bytes(b"x" * (limit + 1))
                    self.assertIn(f"$CLAUDE_CONFIG_DIR/{rel}",
                                  run_eval._agent_config_written(
                                      self.ws, before, self.config))
                    self._remove(marker)

    def test_remote_settings_are_exempt_only_as_an_empty_object(self):
        settings = self.config / "remote-settings.json"
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        for content in ("{}", "{ }\n"):
            with self.subTest(content=content):
                settings.write_text(content, encoding="utf-8")
                self.assertIsNone(run_eval._agent_config_written(
                    self.ws, before, self.config))
                settings.unlink()
        for shape in ('{"hooks": {}}', "[]", "{", "\xff", "symlink", "directory",
                      "oversized"):
            with self.subTest(shape=shape):
                if shape == "symlink":
                    settings.symlink_to(outside)
                elif shape == "directory":
                    (settings / "hooks").mkdir(parents=True)
                    (settings / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
                elif shape == "oversized":
                    settings.write_bytes(b"{" + b" " * 4096 + b"}")
                elif shape == "\xff":
                    settings.write_bytes(b"\xff")
                else:
                    settings.write_text(shape, encoding="utf-8")
                self.assertIn("$CLAUDE_CONFIG_DIR/remote-settings.json",
                              run_eval._agent_config_written(
                                  self.ws, before, self.config))
                self._remove(settings)
        # An empty object at the start of the turn that then gains settings.
        settings.write_text("{}", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, self.config)
        settings.write_text('{"permissions": {"allow": ["Bash"]}}', encoding="utf-8")
        self.assertEqual(run_eval._agent_config_written(self.ws, before, self.config),
                         "$CLAUDE_CONFIG_DIR/remote-settings.json")

    def test_what_the_cli_writes_into_the_profile_passes(self):
        for rel in ("projects/-tmp-x/s.jsonl", "sessions/12.json", ".claude.json",
                    "shell-snapshots/s.sh", "backups/b", "session-env/u/x"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_guidance("write_config", str(self.config / rel))
                self.assertNotIn("error", out, out)


class SharedProfileTests(_StandIn):
    """A skill arm loads the operator's real profile: its configuration paths
    are watched, the rest is other sessions' and the CLI's."""

    def test_settings_written_into_the_real_profile_fail_the_trial(self):
        for rel in ("settings.json", "hooks/h.sh", "skills/x/SKILL.md", "agents/a.md",
                    # Plugin-loading surfaces.
                    "plugins/installed_plugins.json", "plugins/known_marketplaces.json",
                    "plugins/cache/m/p/1/hooks/hooks.json",
                    "plugins/cache/m/p/1/skills/s/SKILL.md",
                    "plugins/marketplaces/m/plugins/p/hooks/hooks.json"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_arm("write_config", followups=["again"],
                                   env={"HD_TARGET": str(self.home / ".claude" / rel)})
                self.assertEqual(out["error"], "agent_wrote_agent_config")
                self.assertIn(f"$CLAUDE_CONFIG_DIR/{rel}", out["detail"])
                shutil.rmtree(self.home / ".claude", ignore_errors=True)

    def test_other_sessions_and_the_cli_writing_elsewhere_pass(self):
        for rel in ("projects/-tmp-other/s.jsonl", "skills/synced/u/SKILL.md",
                    "todos/t.json",
                    # The writes measured under plugins/: small markers.
                    "plugins/cache/m/p/1/.orphaned_at", "plugins/.last_inuse_sweep",
                    "plugins/plugin-directory-cache-v2.json",
                    "plugins/store/cc-plugin-agents-md_builtin-b049b47b3618.json"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_arm("write_config",
                                   env={"HD_TARGET": str(self.home / ".claude" / rel)})
                self.assertNotIn("error", out, out)

    def test_a_third_party_plugin_store_stays_watched(self):
        for rel in ("plugins/store/cc-plugin-p_m-0123.json", "plugins/store/other.json"):
            with self.subTest(rel=rel):
                self.log.unlink(missing_ok=True)
                out = self.run_arm("write_config",
                                   env={"HD_TARGET": str(self.home / ".claude" / rel)})
                self.assertEqual(out["error"], "agent_wrote_agent_config")
                self.assertIn(f"$CLAUDE_CONFIG_DIR/{rel}", out["detail"])
                shutil.rmtree(self.home / ".claude", ignore_errors=True)


class VanishedPathTests(_StandIn):
    """A file another session removes between listing and lstat."""

    def snapshot_with_one_vanished(self, root_rel: str, shared: bool):
        profile = self.home / ".claude"
        target = profile / root_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        before = run_eval._agent_config_snapshot(self.ws, profile, shared)
        real_lstat = os.lstat

        def flaky(path, *args, **kwargs):
            if Path(path) == target:
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(run_eval.os, "lstat", flaky):
            return run_eval._agent_config_written(self.ws, before, profile, shared)

    def test_a_vanished_config_file_is_a_change_not_a_crash(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                written = self.snapshot_with_one_vanished("skills/x/SKILL.md", shared)
                self.assertIn("skills/x/SKILL.md", written)

    def test_a_vanished_file_in_an_exempt_tree_is_not_a_change(self):
        self.assertIsNone(self.snapshot_with_one_vanished("skills/synced/u/a.md", True))
        self.assertIsNone(self.snapshot_with_one_vanished("projects/p/s.jsonl", False))


class ExternalProfileTests(_StandIn):
    """A CLAUDE_CONFIG_DIR the harness inherited, outside HOME."""

    def setUp(self):
        super().setUp()
        self.external = self.root / "external-profile"
        (self.external / "projects" / "-home-u-original-work").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.external)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_skill_arm_keeps_only_its_own_session_there(self):
        out = self.run_arm()
        self.assertNotIn("error", out, out)
        argv = self.calls()[0]["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn(str(self.external), settings["sandbox"]["filesystem"]["denyRead"])
        deny = settings["permissions"]["deny"]
        own = self.external / "projects" / run_eval._munged_project_name(self.ws)
        self.assertNotIn(f"Read(/{self.external})", deny)
        self.assertIn(f"Read(/{self.external}/projects/-)", deny)
        # A rule covering the earlier session, none covering the arm's own.
        import fnmatch
        def covered(path):
            return any(fnmatch.fnmatchcase(
                "/".join(path.parts[:len(r[6:-1].split("/"))]).replace("//", "/").lower(),
                r[6:-1].lower()) for r in deny if r.startswith("Read(//"))
        self.assertTrue(covered(self.external / "projects" / "-home-u-original-work"))
        self.assertFalse(covered(own / "s1"))

    def test_a_guidance_arm_has_it_denied_whole(self):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "CLAUDE_CONFIG_DIR": str(self.root / "scratch-config"),
               "HD_LOG": str(self.log)}
        out = run_eval.run_agent(self.ws, "do it", {
            "name": "with_guidance", "timeout": 60,
            "setting_sources": "user,project", "env_override": env})
        self.assertNotIn("error", out, out)
        argv = self.calls()[0]["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn(f"Read(/{self.external})", settings["permissions"]["deny"])
        self.assertIn(f"Read(/{self.external}/**)", settings["permissions"]["deny"])


class ArchiveTests(_StandIn):
    def test_an_archive_outside_home_is_denied_to_every_arm(self):
        state = self.root / "eval-state"
        (state / "skills-evals" / "sessions" / "-tmp-old.x").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}):
            out = self.run_arm()
        self.assertNotIn("error", out, out)
        argv = self.calls()[0]["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        archive = state / "skills-evals"
        self.assertIn(str(archive), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(f"Read(/{archive}/**)", settings["permissions"]["deny"])


class SpawnLimitTests(_StandIn):
    def test_oversized_settings_are_a_named_error_before_the_cli(self):
        with mock.patch.object(run_eval, "MAX_SETTINGS_BYTES", 100):
            out = self.run_arm()
        self.assertEqual(out["error"], "settings_too_large")
        self.assertEqual(self.calls(), [])

    def test_a_cli_that_cannot_be_started_is_a_named_error(self):
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(self.root / "absent")}):
            out = self.run_arm()
        self.assertEqual(out["error"], "spawn_failed")
        self.assertIn("FileNotFoundError", out["detail"])


class ChromeTests(_StandIn):
    def test_every_turn_passes_no_chrome_and_no_cfc_variable(self):
        out = self.run_arm(followups=["again"],
                           env={"CLAUDE_CODE_ENABLE_CFC": "1"})
        self.assertNotIn("error", out, out)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--no-chrome", call["argv"])
            self.assertIsNone(call["cfc"])

    def test_a_guidance_shaped_env_loses_the_variable_too(self):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "HD_LOG": str(self.log), "CLAUDE_CODE_ENABLE_CFC": "1"}
        out = run_eval.run_agent(self.ws, "do it", {
            "name": "with_guidance", "timeout": 60,
            "setting_sources": "user,project", "env_override": env})
        self.assertNotIn("error", out, out)
        (call,) = self.calls()
        self.assertIsNone(call["cfc"])


class SandboxDownDetectionTests(_StandIn):
    def test_an_initialization_failure_is_sandbox_unavailable(self):
        out = self.run_arm("sandbox_error")
        self.assertEqual(out["error"], "sandbox_unavailable")
        self.assertIn("Sandbox Error: bwrap failed to start", out["detail"])

    def test_an_exit_zero_sandbox_disabled_warning_is_sandbox_unavailable(self):
        out = self.run_arm("disabled_warning")
        self.assertEqual(out["error"], "sandbox_unavailable")
        self.assertIn("Sandbox disabled: socat not installed", out["detail"])

    def test_a_line_that_quotes_the_phrase_is_not(self):
        out = self.run_arm("quoted")
        self.assertEqual(out["error"], "nonzero_exit")

    def test_the_refusal_line_shapes(self):
        detail = run_eval._sandbox_unavailable_detail
        refusal = ("\nError: sandbox required but unavailable: socat not "
                   "installed\n  sandbox.failIfUnavailable is set — "
                   "refusing to start without a working sandbox.\n")
        self.assertEqual(detail(refusal), "Error: sandbox required but "
                                          "unavailable: socat not installed")
        self.assertIsNotNone(detail("❌ Sandbox Error: x"))
        self.assertIsNotNone(detail("Sandbox Error: x"))
        self.assertIsNotNone(detail("⚠ Sandbox disabled: x"))
        for line in ("warning: sandbox required but unavailable",
                     "ERROR: SANDBOX REQUIRED BUT UNAVAILABLE: x",
                     "log: Error: sandbox required but unavailable: x",
                     "the Sandbox Error: x is documented"):
            self.assertIsNone(detail(line), line)


class ManagedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="managed-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.dropins = self.root / "managed-settings.d"
        self.dropins.mkdir()

    def refusal(self, sandbox: dict | None, platform: str = "linux"):
        path = self.root / "managed-settings.json"
        path.write_text(json.dumps({} if sandbox is None else {"sandbox": sandbox}),
                        encoding="utf-8")
        return run_eval.managed_sandbox_refusal([path], [self.dropins], platform)

    def test_no_policy_or_a_harmless_one_passes(self):
        self.assertIsNone(run_eval.managed_sandbox_refusal(
            [self.root / "absent.json"], [self.root / "absent.d"], "linux"))
        self.assertIsNone(self.refusal(None))
        self.assertIsNone(self.refusal({"enabled": True,
                                        "enabledPlatforms": ["linux", "macos"]}))
        self.assertIsNone(self.refusal({"network": {"deniedDomains": ["x.example.com"]}}))

    def test_a_platform_list_without_this_platform_refuses(self):
        for platforms, platform in (([], "linux"), (["macos"], "linux"),
                                    (["linux"], "wsl")):
            with self.subTest(platforms=platforms):
                reason = self.refusal({"enabledPlatforms": platforms}, platform)
                self.assertIn("sandbox.enabledPlatforms", reason)

    def test_a_disabled_or_escapable_sandbox_refuses(self):
        for sandbox, key in (({"enabled": False}, "sandbox.enabled"),
                             ({"failIfUnavailable": False}, "sandbox.failIfUnavailable"),
                             ({"allowUnsandboxedCommands": True},
                              "sandbox.allowUnsandboxedCommands")):
            with self.subTest(key=key):
                self.assertIn(key, self.refusal(sandbox))

    def test_keys_that_drop_confinement_or_our_rules_refuse(self):
        for sandbox, key in (
                ({"filesystem": {"disabled": True}}, "sandbox.filesystem.disabled"),
                ({"enableWeakerNestedSandbox": True}, "sandbox.enableWeakerNestedSandbox"),
                ({"enableWeakerNetworkIsolation": True},
                 "sandbox.enableWeakerNetworkIsolation"),
                ({"bwrapPath": "/opt/x"}, "sandbox.bwrapPath"),
                ({"filesystem": {"allowWrite": ["/"]}}, "sandbox.filesystem.allowWrite"),
                ({"someFutureKey": 1}, "sandbox.someFutureKey"),
                ({"network": {"strictAllowlist": False}}, "sandbox.network.strictAllowlist")):
            with self.subTest(key=key):
                self.assertIn(key, self.refusal(sandbox))

    def test_permission_keys_outside_the_list_refuse(self):
        path = self.root / "managed-settings.json"
        for settings, key in (
                ({"allowManagedPermissionRulesOnly": True}, "allowManagedPermissionRulesOnly"),
                ({"permissions": {"additionalDirectories": ["/srv"]}},
                 "permissions.additionalDirectories"),
                ({"permissions": {"someFutureKey": True}}, "permissions.someFutureKey")):
            with self.subTest(key=key):
                path.write_text(json.dumps(settings), encoding="utf-8")
                reason = run_eval.managed_sandbox_refusal([path], [], "linux")
                self.assertIn(key, reason)
                self.assertNotIn("/srv", reason)

    def test_nesting_types_and_list_elements_fail_closed(self):
        path = self.root / "managed-settings.json"
        for settings, key in (
                ({"sandbox": {"filesystem": {"futureEscape": {}}}},
                 "sandbox.filesystem.futureEscape"),
                ({"sandbox": {"network": {"x": {"y": {}}}}}, "sandbox.network.x"),
                ({"sandbox": {"future": {}}}, "sandbox.future"),
                ({"sandbox": {"enabled": {}}}, "sandbox.enabled"),
                ({"sandbox": {"enabled": 1}}, "sandbox.enabled"),
                ({"sandbox": {"filesystem": []}}, "sandbox.filesystem"),
                ({"sandbox": []}, "sandbox"),
                ({"sandbox": {"filesystem": {"denyRead": [1]}}},
                 "sandbox.filesystem.denyRead"),
                ({"sandbox": {"network": {"deniedDomains": ["x" * 5000]}}},
                 "sandbox.network.deniedDomains"),
                ({"sandbox": {"enabledPlatforms": [["linux"]]}}, "sandbox.enabledPlatforms"),
                ({"permissions": {"deny": [{"x": 1}]}}, "permissions.deny"),
                ({"permissions": {"defaultMode": ""}}, "permissions.defaultMode"),
                ({"sandbox": {"credentials": {"files": [{"path": "~/.x", "mode": "allow"}]}}},
                 "sandbox.credentials.files"),
                ({"allowManagedPermissionRulesOnly": 0}, "allowManagedPermissionRulesOnly")):
            with self.subTest(key=key, settings=settings):
                path.write_text(json.dumps(settings), encoding="utf-8")
                reason = run_eval.managed_sandbox_refusal([path], [], "linux")
                self.assertIsNotNone(reason)
                self.assertIn(key, reason)

    def test_the_arms_own_restrictive_values_pass_and_looser_ones_refuse(self):
        safe = {"excludedCommands": [], "enableWeakerNestedSandbox": False,
                "enableWeakerNetworkIsolation": False,
                "filesystem": {"disabled": False, "allowRead": [], "allowWrite": []},
                "network": {"allowAllUnixSockets": False, "allowUnixSockets": [],
                            "allowLocalBinding": False, "allowedDomains": []}}
        self.assertIsNone(self.refusal(safe))
        path = self.root / "managed-settings.json"
        path.write_text(json.dumps({"permissions": {"additionalDirectories": []}}),
                        encoding="utf-8")
        self.assertIsNone(run_eval.managed_sandbox_refusal([path], [], "linux"))
        for loose, key in (({"excludedCommands": ["x"]}, "sandbox.excludedCommands"),
                           ({"filesystem": {"disabled": True}}, "sandbox.filesystem.disabled"),
                           ({"network": {"allowedDomains": ["a.example.com"]}},
                            "sandbox.network.allowedDomains"),
                           ({"enableWeakerNestedSandbox": True},
                            "sandbox.enableWeakerNestedSandbox")):
            with self.subTest(key=key):
                self.assertIn(key, self.refusal(loose))

    def test_restrictive_credential_entries_pass_and_widening_ones_refuse(self):
        path = self.root / "managed-settings.json"
        ok = [{"sandbox": {"credentials": {"files": [
                  {"path": "~/.netrc", "mode": "deny", "onExtractNoMatch": "deny"}]}}},
              {"sandbox": {"credentials": {"files": [
                  {"path": "~/.netrc", "mode": "deny", "injectHosts": [],
                   "maskDuplicates": False, "extract": ""}]}}},
              {"sandbox": {"credentials": {"files": [
                  {"path": "~/.x", "mode": "mask", "extract": "t:(\\S+)",
                   "decode": "jwt", "maskClaims": ["sub"]}],
                  "envVars": [{"name": "GH_TOKEN", "mode": "deny",
                               "onExtractNoMatch": "warn"}]}}}]
        for settings in ok:
            with self.subTest(settings=settings):
                path.write_text(json.dumps(settings), encoding="utf-8")
                self.assertIsNone(run_eval.managed_sandbox_refusal([path], [], "linux"))
        bad = [{"files": [{"path": "~/.netrc", "mode": "mask",
                           "injectHosts": ["api.example.com"]}]},
               {"files": [{"path": "~/.netrc", "mode": "deny", "futureField": 1}]},
               {"files": [{"path": "~/.netrc", "mode": "deny", "onExtractNoMatch": "allow"}]},
               {"files": [{"path": "~/.netrc", "mode": "deny", "maskDuplicates": "no"}]},
               {"files": [{"path": "", "mode": "deny"}]},
               {"envVars": [{"name": "1BAD", "mode": "deny"}]},
               {"envVars": [{"name": "X", "mode": "deny", "maskDuplicates": True}]}]
        for credentials in bad:
            with self.subTest(credentials=credentials):
                path.write_text(json.dumps({"sandbox": {"credentials": credentials}}),
                                encoding="utf-8")
                self.assertIn("sandbox.credentials",
                              run_eval.managed_sandbox_refusal([path], [], "linux"))

    def test_the_accepted_keys_pass(self):
        path = self.root / "managed-settings.json"
        path.write_text(json.dumps({
            "allowManagedPermissionRulesOnly": False,
            "sandbox": {"enabled": True, "failIfUnavailable": True,
                        "allowUnsandboxedCommands": False,
                        "autoAllowBashIfSandboxed": True,
                        "enabledPlatforms": ["linux"],
                        "filesystem": {"denyRead": ["~/.ssh"], "denyWrite": ["/srv"],
                                       "allowManagedReadPathsOnly": True},
                        "network": {"deniedDomains": ["x.example.com"],
                                    "strictAllowlist": True,
                                    "allowManagedDomainsOnly": True},
                        "credentials": {"files": [{"path": "~/.ssh", "mode": "deny"}],
                                        "envVars": [{"name": "X_TOKEN", "mode": "mask"}]}},
            "permissions": {"deny": ["Bash(rm *)"], "ask": [], "allow": [],
                            "defaultMode": "default",
                            "disableBypassPermissionsMode": "disable",
                            "blockReadsOutsideWorkingDirectories": True}}),
            encoding="utf-8")
        self.assertIsNone(run_eval.managed_sandbox_refusal([path], [], "linux"))

    def test_every_trusted_grant_refuses(self):
        for sandbox, key in (
                ({"excludedCommands": ["curl *"]}, "sandbox.excludedCommands"),
                ({"network": {"httpProxyPort": 8080}}, "network.httpProxyPort"),
                ({"network": {"socksProxyPort": 8081}}, "network.socksProxyPort"),
                ({"network": {"allowAllUnixSockets": True}}, "network.allowAllUnixSockets"),
                ({"network": {"allowUnixSockets": ["/var/run/docker.sock"]}},
                 "network.allowUnixSockets"),
                ({"network": {"allowLocalBinding": True}}, "network.allowLocalBinding")):
            with self.subTest(key=key):
                reason = self.refusal(sandbox)
                self.assertIn(key, reason)
                # The key, never the value.
                self.assertNotIn("docker", reason)
                self.assertNotIn("8080", reason)

    def test_a_dropin_is_read_and_a_broken_file_refuses(self):
        (self.dropins / "10-policy.json").write_text(
            json.dumps({"sandbox": {"enabled": False}}), encoding="utf-8")
        self.assertIn("10-policy.json", self.refusal(None))
        (self.dropins / "10-policy.json").write_text("{", encoding="utf-8")
        self.assertIn("cannot be read", self.refusal(None))

    def test_the_platform_name_is_the_clis(self):
        self.assertIn(run_eval.cli_platform(), ("linux", "wsl", "macos", "windows"))

    def test_main_refuses_the_run_before_any_fixture(self):
        policy = self.root / "managed-settings.json"
        policy.write_text(json.dumps({"sandbox": {"enabledPlatforms": []}}),
                          encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(run_eval, "MANAGED_SETTINGS_FILES", (policy,)), \
             mock.patch.object(run_eval, "MANAGED_SETTINGS_DROPINS", ()), \
             mock.patch.object(run_eval, "resolve_fixture_dirs",
                               side_effect=AssertionError("fixture read")), \
             mock.patch.object(sys, "argv", ["run_eval.py", str(self.root / "evals"),
                                             "--arm", "both"]), \
             redirect_stdout(out):
            rc = run_eval.main()
        self.assertEqual(rc, 2)
        self.assertIn("managed_sandbox_policy", out.getvalue())

    def test_run_agent_refuses_at_the_sink(self):
        policy = self.root / "managed-settings.json"
        policy.write_text(json.dumps({"sandbox": {"network": {"allowLocalBinding": True}}}),
                          encoding="utf-8")
        ws = Path(tempfile.mkdtemp(prefix="workspace-")).resolve()
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        with mock.patch.object(run_eval, "MANAGED_SETTINGS_FILES", (policy,)), \
             mock.patch.object(run_eval, "MANAGED_SETTINGS_DROPINS", ()), \
             mock.patch.object(run_eval.subprocess, "run",
                               side_effect=AssertionError("CLI started")):
            out = run_eval.run_agent(ws, "do it", {"name": "without_skill",
                                                    "timeout": 60})
        self.assertEqual(out["error"], "managed_sandbox_policy")


if __name__ == "__main__":
    unittest.main()
