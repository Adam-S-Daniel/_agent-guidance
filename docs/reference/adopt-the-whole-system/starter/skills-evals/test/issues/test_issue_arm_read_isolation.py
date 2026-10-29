"""Every agent arm runs with its reads fenced in (ADR 0011, addendum: reads).

The answer key is on this machine too: this checkout's `checker/` and
`solution.patch`, the sibling clones beside it, and on a workstation the
transcripts under HOME. Every agent arm's `--settings` denies them to
sandboxed commands (`sandbox.filesystem.denyRead`) and to the Read, Grep and
Glob tools (`Read(//<abs>/**)` deny rules), re-opening only git's global
configuration and PATH toolchains under HOME to commands. A workspace under
a denied path is refused with `workspace_read_denied`.

Temp directories stand in for HOME, the clone and the checkouts; the CLI is
a stand-in that records argv. No network, no real `claude`.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import run_eval  # noqa: E402
from scorers import bash_ast  # noqa: E402

FAKE_REGISTRY = ROOT / "test" / "fixtures" / "fake_registry"

STAND_IN = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["RD_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("RD_PATH_LOG"):
    with open(os.environ["RD_PATH_LOG"], "a") as fh:
        fh.write(os.environ.get("PATH", "") + "\\n")
print(json.dumps({{"type": "result", "is_error": False, "result": "done",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": "s1",
                  "modelUsage": {{"fake-default-model": {{}}}}}}))
"""


def settings_of(argv: list[str]) -> dict:
    (index,) = [i for i, arg in enumerate(argv) if arg == "--settings"]
    return json.loads(argv[index + 1])


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.email=ci@example.com", "-c", "user.name=ci",
                    *args], cwd=cwd, check=True, capture_output=True)


def read_rule_paths(settings: dict) -> set[str]:
    """The absolute patterns the Read deny rules name, without `/**`."""
    out = set()
    for rule in settings["permissions"]["deny"]:
        if rule.startswith("Edit("):
            continue
        assert rule.startswith("Read(//") and rule.endswith(")"), rule
        out.add(rule[len("Read(/"):-1].removesuffix("/**"))
    return out


def _matches(pattern: str, path: Path) -> bool:
    """One gitignore-style pattern against one path: `*` stays inside a
    segment. The paths here carry no escaped characters."""
    parts = pattern.split("/")
    # The CLI's matcher ignores case (measured), so this one does too.
    return (len(parts) == len(path.parts) and all(
        fnmatch.fnmatchcase(seg.lower(), pat.lower())
        for seg, pat in zip(path.parts[1:], parts[1:])))


def covers(patterns, target: Path) -> bool:
    """A pattern names the target or one of its ancestors."""
    target = target.resolve()
    return any(_matches(p, candidate) for p in patterns
               for candidate in (target, *target.parents))


def covers_name(patterns, path: Path) -> bool:
    """A pattern names `path` itself, unresolved (a symlink's own name)."""
    return any(_matches(p, path) for p in patterns)


class _TempLayout(unittest.TestCase):
    """HOME, a clone holding a linked worktree, its sibling, two checkouts."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="arm-reads-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.repos = self.home / "repos"
        self.clone = self.repos / "skills-evals"
        self.sibling = self.repos / "example-site-platform"
        for path in (self.clone, self.sibling):
            path.mkdir(parents=True)
        git("init", "-q", cwd=self.clone)
        (self.clone / "README.md").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=self.clone)
        git("commit", "-q", "-m", "init", cwd=self.clone)
        self.worktree = self.clone / ".claude" / "worktrees" / "wt"
        git("worktree", "add", "-q", str(self.worktree), cwd=self.clone)
        self.registry = self.root / "elsewhere" / "registry"
        self.guidance = self.root / "elsewhere" / "guidance"
        for path in (self.registry, self.guidance):
            path.mkdir(parents=True)
        self.tmp = self.root / "tmp"
        self.workspace = self.tmp / "workspace-x"
        self.workspace.mkdir(parents=True)
        self.state = self.root / "state"
        # All isolation tests use mount metadata fixtures, never host mounts.
        self.mountinfo = "1 0 8:1 / / rw - ext4 /dev/example rw\n"
        original_read = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == Path("/proc/self/mountinfo"):
                return self.mountinfo
            return original_read(path, *args, **kwargs)

        mount_reader = mock.patch.object(Path, "read_text", read_text)
        mount_reader.start()
        self.addCleanup(mount_reader.stop)
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SKILLS_EVALS_MOUNTINFO", None)

    def settings(self, path_env: str = "", **kwargs) -> dict:
        kwargs.setdefault("profiles", [self.home / ".claude"])
        kwargs.setdefault("tmp_root", self.tmp)
        return run_eval.arm_sandbox_settings(
            [self.registry, self.guidance], home=self.home, path_env=path_env,
            harness_root=self.worktree, **kwargs)


class WorktreeResolutionTests(_TempLayout):
    def test_context_checkout_fences_external_git_metadata(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                checkout = self.root / f"context-{linked}"
                metadata = self.root / f"context-history-{linked}"
                checkout.mkdir()
                git("init", "-q", f"--separate-git-dir={metadata}", cwd=checkout)
                git("commit", "-q", "-m", "init", "--allow-empty", cwd=checkout)
                source = checkout
                if linked:
                    source = self.root / "external-linked"
                    git("worktree", "add", "--detach", "-q", str(source), cwd=checkout)
                repositories = run_eval.ContextRepositories({"example/context": source}, None)
                settings = run_eval.arm_sandbox_settings(
                    repositories.used_paths(), home=self.home, path_env="",
                    harness_root=self.worktree, profiles=[], tmp_root=self.tmp)
                fs = settings["sandbox"]["filesystem"]
                for target in (metadata / "objects", metadata / "config"):
                    self.assertTrue(any(run_eval._within(target, Path(p)) for p in fs["denyRead"]))
                    self.assertTrue(any(run_eval._within(target, Path(p)) for p in fs["denyWrite"]))
                    self.assertTrue(covers(read_rule_paths(settings), target))
                    edits = [rule[len("Edit(/"):-1].removesuffix("/**")
                             for rule in settings["permissions"]["deny"] if rule.startswith("Edit(/")]
                    self.assertTrue(covers(edits, target))

    def test_context_git_metadata_discovery_fails_closed(self):
        checkout = self.root / "broken-context"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: missing\n")
        with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
            run_eval.ContextRepositories({"example/context": checkout}, None).used_paths()
        self.assertEqual(caught.exception.code, "context_git_metadata_unknown")

    def test_a_worktree_resolves_to_the_main_clone(self):
        self.assertEqual(run_eval.harness_clone_root(self.worktree), self.clone)
        self.assertEqual(run_eval.harness_clone_root(self.clone), self.clone)

    def test_ambient_git_variables_cannot_redirect_the_clone_lookup(self):
        # harness_repo strips every GIT_* variable: an inherited GIT_DIR
        # naming another repository must not move the deny roots.
        other = self.root / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                          "GIT_WORK_TREE": str(other)}):
            self.assertEqual(run_eval.harness_clone_root(self.worktree), self.clone)
            self.assertEqual(run_eval.harness_git_common_dir(self.worktree),
                             self.clone / ".git")

    def test_a_directory_outside_git_is_its_own_root(self):
        self.assertEqual(run_eval.harness_clone_root(self.registry), self.registry)

    def test_a_separate_git_dir_resolves_to_the_checkout_not_the_metadata(self):
        checkout, metadata = self.root / "sep" / "checkout", self.root / "meta" / "git"
        checkout.mkdir(parents=True)
        metadata.parent.mkdir(parents=True)
        git("init", "-q", f"--separate-git-dir={metadata}", cwd=checkout)
        (checkout / "f").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=checkout)
        git("commit", "-q", "-m", "init", cwd=checkout)
        linked = checkout / ".claude" / "worktrees" / "wt"
        git("worktree", "add", "-q", str(linked), cwd=checkout)
        self.assertEqual(run_eval.harness_clone_root(linked), checkout)
        self.assertEqual(run_eval.harness_clone_root(checkout), checkout)
        # The history lives in the metadata directory: denied as well.
        denied = [path for _, path in run_eval.arm_read_denied(
            home=self.home, harness_root=linked, profiles=[])]
        self.assertIn(checkout, denied)
        self.assertIn(checkout.parent, denied)
        self.assertIn(metadata, denied)

    def test_core_worktree_wins_over_a_git_directory_named_dot_git(self):
        # Metadata in `<meta>/.git`, its work tree configured elsewhere: the
        # parent of `.git` is not the checkout.
        meta, checkout = self.root / "cw" / "meta", self.root / "cw" / "checkout"
        for path in (meta, checkout):
            path.mkdir(parents=True)
        git("init", "-q", cwd=meta)
        gitdir = meta / ".git"
        git(f"--git-dir={gitdir}", "config", "core.worktree", str(checkout), cwd=meta)
        (checkout / "f").write_text("x\n", encoding="utf-8")
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "add", "-A", cwd=checkout)
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "commit", "-q", "-m", "i",
            cwd=checkout)
        linked = self.root / "cw" / "linked"
        git(f"--git-dir={gitdir}", "worktree", "add", "-q", str(linked), cwd=meta)
        self.assertEqual(run_eval.harness_clone_root(linked), checkout)

    def test_core_worktree_through_an_include_is_honored(self):
        meta, checkout = self.root / "inc" / "meta", self.root / "inc" / "checkout"
        for path in (meta, checkout):
            path.mkdir(parents=True)
        git("init", "-q", cwd=meta)
        gitdir = meta / ".git"
        (meta / "extra.cfg").write_text(f"[core]\n\tworktree = {checkout}\n",
                                        encoding="utf-8")
        git(f"--git-dir={gitdir}", "config", "include.path", "../extra.cfg", cwd=meta)
        (checkout / "f").write_text("x\n", encoding="utf-8")
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "add", "-A", cwd=checkout)
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "commit", "-q", "-m", "i",
            cwd=checkout)
        linked = self.root / "inc" / "linked"
        git(f"--git-dir={gitdir}", "worktree", "add", "-q", str(linked), cwd=meta)
        self.assertEqual(run_eval.harness_clone_root(linked), checkout)
        # An include git cannot read hides what it would set: unknown.
        git(f"--git-dir={gitdir}", "config", "--add", "include.path", "../missing.cfg",
            cwd=meta)
        self.assertIsNone(run_eval.harness_clone_root(linked))

    def test_an_include_condition_holding_a_space_is_parsed_whole(self):
        # `includeIf` conditions may hold spaces; the listing is read
        # NUL-separated, so the path is not cut at the first space.
        meta, checkout = self.root / "sp" / "meta", self.root / "sp" / "checkout"
        for path in (meta, checkout):
            path.mkdir(parents=True)
        git("init", "-q", cwd=meta)
        gitdir = meta / ".git"
        extra = meta / "extra.cfg"
        extra.write_text(f"[core]\n\tworktree = {checkout}\n", encoding="utf-8")
        git(f"--git-dir={gitdir}", "config", "includeIf.gitdir:**/[ r]*.path",
            str(extra), cwd=meta)
        git(f"--git-dir={gitdir}", "config", "core.worktree", str(checkout), cwd=meta)
        (checkout / "f").write_text("x\n", encoding="utf-8")
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "add", "-A", cwd=checkout)
        git(f"--git-dir={gitdir}", f"--work-tree={checkout}", "commit", "-q", "-m", "i",
            cwd=checkout)
        linked = self.root / "sp" / "linked"
        git(f"--git-dir={gitdir}", "worktree", "add", "-q", str(linked), cwd=meta)
        self.assertEqual(run_eval.harness_clone_root(linked), checkout)

    def test_an_undeterminable_main_checkout_is_a_named_error(self):
        # `--separate-git-dir` metadata not named `.git`, no core.worktree,
        # and a linked worktree outside the main checkout: git keeps no path
        # back to the checkout, so the arm fails rather than guess.
        checkout, metadata = self.root / "u" / "checkout", self.root / "u" / "meta" / "git"
        checkout.mkdir(parents=True)
        metadata.parent.mkdir(parents=True)
        git("init", "-q", f"--separate-git-dir={metadata}", cwd=checkout)
        (checkout / "f").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=checkout)
        git("commit", "-q", "-m", "init", cwd=checkout)
        outside = self.root / "u" / "outside-wt"
        git("worktree", "add", "-q", str(outside), cwd=checkout)
        self.assertIsNone(run_eval.harness_clone_root(outside))
        with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
            run_eval.arm_read_denied(home=self.home, harness_root=outside, profiles=[])
        self.assertEqual(caught.exception.code, "harness_clone_unknown")

    def test_this_harness_resolves_to_a_clone_holding_it(self):
        clone = run_eval.HARNESS_CLONE_ROOT
        self.assertTrue(run_eval._within(run_eval.HARNESS_ROOT, clone))
        self.assertNotIn("worktrees", clone.parts)



class HostMountReadDenyTests(_TempLayout):
    def test_inherited_profile_alias_keeps_the_whole_alias_classification(self):
        alias = self.root / "mirror"
        (alias / "usr" / "bin").mkdir(parents=True)
        self.mountinfo += f"2 1 8:1 / {alias} rw - ext4 /dev/example rw\n"
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(alias)}):
            denied = run_eval.arm_read_denied(
                home=self.home, harness_root=self.worktree,
                mountinfo=self.mountinfo)
            self.assertEqual(dict((p, label) for label, p in denied)[alias],
                             run_eval.MOUNT_ALIAS_LABEL)
            settings = self.settings(path_env=f"{alias}/usr/bin:/usr/bin",
                                     profiles=run_eval.profile_dirs(self.home))
        rules = settings["permissions"]["deny"]
        self.assertIn(f"Read(/{alias})", rules)
        self.assertIn(f"Read(/{alias}/**)", rules)
        self.assertEqual([r for r in rules if r.startswith(f"Read(/{alias}/")],
                         [f"Read(/{alias}/**)"])
        self.assertEqual(run_eval.path_without_aliases(
            f"{alias}/usr/bin:/usr/bin",
            [p for label, p in denied if label == run_eval.MOUNT_ALIAS_LABEL]),
            "/usr/bin")

    def test_a_home_mount_alias_gets_no_home_or_profile_exception(self):
        (self.home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
        self.mountinfo += f"2 1 0:30 / {self.home} rw - drvfs C: rw\n"
        settings = self.settings(workspace=self.workspace)
        self.assertIn(f"Read(/{self.home})", settings["permissions"]["deny"])
        self.assertIn(f"Read(/{self.home}/**)", settings["permissions"]["deny"])
        self.assertEqual(settings["sandbox"]["filesystem"]["allowRead"], [])

    def test_alias_path_symlink_parent_is_resolved_before_dotdot(self):
        alias = self.root / "mirror"
        (alias / "child").mkdir(parents=True)
        (alias / "bin").mkdir()
        linux = self.root / "linux"
        linux.mkdir()
        (linux / "link").symlink_to(alias / "child")
        entry = f"{linux}/link/../bin"
        self.assertEqual(Path(entry).resolve(), alias / "bin")
        self.assertEqual(run_eval.path_without_aliases(
            f"{entry}:/usr/bin", [alias]), "/usr/bin")

    def test_toolchain_preflight_resolves_original_symlink_parent_spelling(self):
        alias = self.root / "mirror"
        (alias / "child").mkdir(parents=True)
        (alias / "bin").mkdir()
        for name in run_eval.ARM_REQUIRED_TOOLS:
            executable = alias / "bin" / name
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        linux = self.root / "linux"
        linux.mkdir()
        (linux / "link").symlink_to(alias / "child")
        with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
            run_eval.arm_path_or_error(f"{linux}/link/../bin", [alias])
        self.assertEqual(caught.exception.code, "toolchain_under_alias")

    def test_wsl_root_alias_and_windows_drives_deny_all_of_mnt(self):
        self.mountinfo += (
            "2 1 8:1 / /mnt/wslg/distro rw - ext4 /dev/example rw\n"
            "3 1 0:30 / /mnt/d rw - 9p D:\\134 rw,aname=drvfs\n")
        settings = self.settings(workspace=self.workspace)
        self.assertIn("/mnt", settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(read_rule_paths(settings),
                               Path("/mnt/wslg/distro/home/answer.patch")))
        self.assertTrue(covers(read_rule_paths(settings), Path("/mnt/d/repos/x")))
        self.assertFalse(covers(read_rule_paths(settings), self.workspace / "f"))

    def test_mountinfo_with_only_canonical_self_binds_adds_no_alias(self):
        baseline = self.settings()
        self.mountinfo += (
            "2 1 8:1 /tmp /tmp rw - ext4 /dev/example rw\n"
            "3 1 8:1 /snap /snap rw - ext4 /dev/example rw\n")
        self.assertEqual(self.settings(), baseline)
        self.assertNotIn("/mnt", baseline["sandbox"]["filesystem"]["denyRead"])

    def test_home_self_bind_on_root_filesystem_adds_no_alias(self):
        baseline = self.settings()
        self.mountinfo += (
            f"2 1 8:1 {self.home} {self.home} rw - ext4 /dev/example rw\n")
        self.assertEqual(self.settings(), baseline)
        self.assertNotIn("/", baseline["sandbox"]["filesystem"]["denyRead"])

    def test_same_device_with_a_different_source_name_is_an_alias(self):
        alias = self.root / "device-alias"
        alias.mkdir()
        self.mountinfo += f"2 1 8:1 / {alias} rw - ext4 /dev/root rw\n"
        self.assertIn(str(alias), self.settings()["sandbox"]["filesystem"]["denyRead"])

    def test_windows_drive_with_custom_automount_root_is_denied(self):
        alias = self.root / "windows-drive"
        alias.mkdir()
        self.mountinfo += f"2 1 0:30 / {alias} rw - drvfs D: rw\n"
        settings = self.settings()
        self.assertIn(str(alias), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(read_rule_paths(settings), alias / "repos" / "answer"))

    def test_root_filesystem_alias_outside_mnt_is_denied(self):
        alias = self.root / "mirror"
        alias.mkdir()
        self.mountinfo += f"2 1 8:1 / {alias} rw - ext4 /dev/example rw\n"
        settings = self.settings()
        self.assertIn(str(alias), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(read_rule_paths(settings), alias / "home" / "answer"))

    def test_separate_home_filesystem_alias_uses_longest_mount_prefix(self):
        alias = self.root / "home-mirror"
        alias.mkdir()
        self.mountinfo += (
            f"2 1 8:2 / {self.home} rw - ext4 /dev/home-example rw\n"
            f"3 1 8:2 / {alias} rw - ext4 /dev/home-example rw\n")
        settings = self.settings()
        self.assertIn(str(alias), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(read_rule_paths(settings), alias / "repos" / "answer"))
        self.assertNotIn("/tmp", settings["sandbox"]["filesystem"]["denyRead"])

    def test_mountinfo_escaped_mountpoint_is_decoded(self):
        alias = self.root / "mirror space"
        alias.mkdir()
        escaped = str(alias).replace(" ", r"\040")
        self.mountinfo += f"2 1 8:1 / {escaped} rw - ext4 /dev/example rw\n"
        settings = self.settings()
        self.assertIn(str(alias), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(f"Read(/{alias}/**)", settings["permissions"]["deny"])

    def test_non_linux_does_not_read_mountinfo_or_add_host_fences(self):
        self.mountinfo = "invalid mountinfo that must not be read"
        with mock.patch.object(run_eval.sys, "platform", "darwin"):
            settings = self.settings()
        self.assertNotIn("/mnt", settings["sandbox"]["filesystem"]["denyRead"])
        self.assertNotIn("/run", settings["sandbox"]["filesystem"]["denyRead"])

    def test_malformed_mountinfo_refuses_an_unfenced_linux_arm(self):
        self.mountinfo = "malformed\n"
        with self.assertRaises(run_eval.ArmReadIsolationError):
            self.settings()

    def wsl_mounts(self, drive: Path) -> None:
        """WSL's own shape: WSLg's view of the distro, the C: drive, and a
        Windows drive at `drive` (a custom automount root) whose directories
        this test can create."""
        self.mountinfo += (
            "2 1 8:1 / /mnt/wslg/distro rw - ext4 /dev/example rw\n"
            "3 1 0:30 / /mnt/c rw - 9p C:\\134 rw,aname=drvfs\n"
            f"4 1 0:31 / {drive} rw - drvfs D: rw\n")

    def windows_path(self, drive: Path, count: int = 30) -> list[Path]:
        """`count` Windows-side PATH directories, the way WSL appends them."""
        out = []
        for index in range(count):
            directory = drive / "Program Files" / f"Example Tool {index}" / "bin"
            directory.mkdir(parents=True)
            (directory.parent / "lib").mkdir()
            out.append(directory)
        return out

    def test_an_alias_is_denied_whole_with_no_rule_or_carve_out_inside_it(self):
        drive = self.root / "drive-d"
        drive.mkdir()
        self.wsl_mounts(drive)
        windows = self.windows_path(drive, 3)
        settings = self.settings(os.pathsep.join(
            [*map(str, windows), "/mnt/c/Windows/system32", "/usr/bin"]))
        filesystem = settings["sandbox"]["filesystem"]
        for alias in (Path("/mnt"), drive):
            self.assertIn(str(alias), filesystem["denyRead"])
            inside = [rule for rule in settings["permissions"]["deny"]
                      if rule.startswith(f"Read(/{alias}/")]
            self.assertEqual(inside, [f"Read(/{alias}/**)"], alias)
            self.assertIn(f"Read(/{alias})", settings["permissions"]["deny"])
            self.assertEqual([p for p in filesystem["allowRead"]
                              if run_eval._within(Path(p), alias)], [], alias)
        self.assertTrue(covers(read_rule_paths(settings), windows[0] / "tool"))

    def test_settings_stay_well_under_the_cap_with_wsl_mounts_and_windows_path(self):
        drive = self.root / "drive-d"
        drive.mkdir()
        self.wsl_mounts(drive)
        windows = self.windows_path(drive)
        linux = [self.home / ".local" / "bin", Path("/usr/bin"), Path("/bin")]
        linux[0].mkdir(parents=True)
        projects = self.home / ".claude" / "projects"
        own = projects / "-tmp-workspace-abc"
        own.mkdir(parents=True)
        path_env = os.pathsep.join(map(str, [*linux, *windows,
                                             *(f"/mnt/c/Example {i}" for i in range(30))]))
        settings = self.settings(path_env, session_dir=own, workspace=self.workspace)
        size = len(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode())
        self.assertLess(size, run_eval.MAX_SETTINGS_BYTES // 4, size)
        # The Windows half of PATH adds nothing at all: the same settings
        # as the Linux half alone.
        self.assertEqual(settings, self.settings(os.pathsep.join(map(str, linux)),
                                                 session_dir=own,
                                                 workspace=self.workspace))

    def test_alias_path_entries_are_stripped_in_order(self):
        drive = self.root / "drive-d"
        (drive / "bin").mkdir(parents=True)
        into_alias = self.root / "linux-bin"
        into_alias.symlink_to(drive / "bin")
        path_env = os.pathsep.join(["/usr/bin", "/mnt/c/Windows", str(drive / "bin"),
                                    str(into_alias), "relative", "/bin",
                                    "/mnt/wslg/distro/usr/bin"])
        self.assertEqual(
            run_eval.path_without_aliases(path_env, [Path("/mnt"), drive]),
            os.pathsep.join(["/usr/bin", "relative", "/bin"]))
        self.assertEqual(run_eval.path_without_aliases(path_env, []), path_env)

    def test_mountinfo_is_a_parameter_and_an_empty_table_names_no_alias(self):
        self.mountinfo = "malformed host table that must not be read\n"
        drive = self.root / "drive-d"
        drive.mkdir()
        injected = f"1 0 8:1 / / rw - ext4 /dev/example rw\n2 1 0:31 / {drive} rw - drvfs D: rw\n"
        settings = self.settings(mountinfo=injected)
        self.assertIn(str(drive), settings["sandbox"]["filesystem"]["denyRead"])
        denied = run_eval.arm_read_denied(home=self.home, harness_root=self.worktree,
                                          profiles=[], mountinfo="")
        self.assertNotIn(run_eval.MOUNT_ALIAS_LABEL, [label for label, _ in denied])

    def test_the_mountinfo_seam_names_a_fixture_file(self):
        drive = self.root / "drive-d"
        drive.mkdir()
        table = self.root / "mountinfo"
        table.write_text(f"1 0 8:1 / / rw - ext4 /dev/example rw\n"
                         f"2 1 0:31 / {drive} rw - drvfs D: rw\n", encoding="utf-8")
        self.mountinfo = "malformed host table that must not be read\n"
        with mock.patch.dict(os.environ, {run_eval.MOUNTINFO_ENV: str(table)}):
            self.assertIn(str(drive),
                          self.settings()["sandbox"]["filesystem"]["denyRead"])


class CarveOutSafetyTests(_TempLayout):
    """A Linux PATH carve-out never opens a deny root, an answer-key
    location or a profile: not the directory itself, not one holding it,
    not one inside it."""

    def test_a_carve_out_into_or_around_a_guarded_path_is_refused(self):
        external = self.home / "tools" / "profile"
        results = self.home / "results"
        alias = self.home / "mirror"
        for path in (external, results / "bin", alias / "usr" / "bin",
                     self.home / ".claude" / "local" / "bin",
                     self.home / ".claude" / "local" / "lib",
                     self.clone / "bin", self.sibling / "bin"):
            path.mkdir(parents=True, exist_ok=True)
        self.mountinfo += f"2 1 8:1 / {alias} rw - ext4 /dev/example rw\n"
        refused = [self.home / ".claude" / "local" / "bin",  # inside a profile
                   self.home / ".claude",                    # the profile itself
                   self.home / "tools",                      # holds a profile
                   external,
                   results / "bin", results,                 # results
                   alias / "usr" / "bin", alias,             # a deny root
                   self.clone / "bin", self.sibling / "bin",  # clones
                   self.worktree, self.repos]
        safe = self.home / ".local" / "bin"
        safe.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "state")}):
            (self.home / "state" / "skills-evals" / "bin").mkdir(parents=True)
            refused += [self.home / "state" / "skills-evals" / "bin",
                        self.home / "state"]               # the archive
            allow = self.settings(
                os.pathsep.join(map(str, [*refused, safe])),
                profiles=[self.home / ".claude", external],
                outputs=[results])["sandbox"]["filesystem"]["allowRead"]
        self.assertEqual(allow, [str(safe)])


class ReadDenySettingsTests(_TempLayout):
    def test_commands_and_file_tools_are_denied_every_checkout_and_home(self):
        settings = self.settings()
        deny_read = settings["sandbox"]["filesystem"]["denyRead"]
        rules = read_rule_paths(settings)
        for path in (self.worktree, self.clone, self.repos, self.registry,
                     self.guidance, self.home):
            with self.subTest(path=path):
                self.assertIn(str(path), deny_read)
                self.assertIn(str(path), rules)
                self.assertIn(f"Read(/{path}/**)", settings["permissions"]["deny"])
        # The sibling clone is denied through the parent that holds it.
        self.assertTrue(covers(deny_read, self.sibling / "main.py"))
        self.assertTrue(covers(rules, self.sibling / "main.py"))

    def test_the_whole_settings_object_spelled_out(self):
        (self.home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
        archive = self.state / "skills-evals"
        (archive / "sessions").mkdir(parents=True)
        results = self.root / "results"
        results.mkdir()
        leftover = self.tmp / "skills-evals-with_guidance-old"
        leftover.mkdir()
        settings = self.settings(outputs=[results])
        rules = []
        for path in (self.worktree, self.clone, self.repos, self.registry,
                     self.guidance, results, archive, self.home):
            rules += [f"Read(/{path})", f"Read(/{path}/**)"]
        rules += [f"Read(/{self.tmp}/{prefix}*)" for prefix in (
            "workspace-", "skills-evals-", "guidance-bridge-canary-", "propagation-",
            "scoring-seed-", "deps-python-", "deps-bundler-", "deps-cache-", "objective-repo-tests-",
            "objective-command-", "local-eval-guard-", "sink-mutation-",
            "trusted-git-", "scaffold-", "scaffold-context-", "claude-probe-home-",
            "skill-edit-guard-", "propose-skill-edit-", "scoring-guidance-",
            "scoring-skill-", "mine-real-work-", ".scaffold-", ".gh-label-",
            ".gh-timeline-", "usage-census-", "session-miner-", "lesson-classify-")]
        self.assertEqual(settings["sandbox"]["filesystem"], {
            "denyRead": [str(self.worktree), str(self.clone), str(self.repos),
                         str(self.registry), str(self.guidance), str(results),
                         str(archive), str(self.home), str(leftover),
                         str(self.workspace)],
            "allowRead": [str(self.home / ".gitconfig")],
            "denyWrite": [str(self.registry), str(self.guidance)]})
        rules += [rule for path in (self.registry, self.guidance)
                  for rule in (f"Edit(/{path})", f"Edit(/{path}/**)")]
        self.assertEqual(settings["permissions"], {"deny": rules})

    def test_the_workspace_is_not_denied(self):
        settings = self.settings(workspace=self.workspace)
        self.assertFalse(covers(settings["sandbox"]["filesystem"]["denyRead"],
                                self.workspace))
        self.assertFalse(covers(read_rule_paths(settings), self.workspace))
        run_eval.check_workspace_readable(
            self.workspace, run_eval.arm_read_denied(
                [self.registry], home=self.home, harness_root=self.worktree))

    def test_a_workspace_under_a_denied_path_is_a_named_error(self):
        for parent, label in ((self.home / "tmp", "HOME"),
                              (self.registry, "a registry or guidance checkout"),
                              (self.repos, "the directory holding the harness clone")):
            with self.subTest(parent=parent):
                workspace = parent / "workspace-y"
                workspace.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
                    run_eval.check_workspace_readable(
                        workspace, run_eval.arm_read_denied(
                            [self.registry], home=self.home,
                            harness_root=self.worktree))
                self.assertIn(label, str(caught.exception))
                # The kind of path, never the path: the detail is published.
                self.assertNotIn(str(self.root), str(caught.exception))

    def test_git_config_and_path_toolchains_under_home_are_reopened(self):
        (self.home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
        local_bin, local_lib = self.home / ".local" / "bin", self.home / ".local" / "lib"
        home_bin, home_lib = self.home / "bin", self.home / "lib"
        in_clone = self.clone / "bin"
        for path in (local_bin, local_lib, home_bin, home_lib, in_clone):
            path.mkdir(parents=True, exist_ok=True)
        path_env = os.pathsep.join(map(str, (local_bin, home_bin, in_clone,
                                             self.home, "/usr/bin", "relative")))
        allow = self.settings(path_env)["sandbox"]["filesystem"]["allowRead"]
        self.assertEqual(allow, [str(self.home / ".gitconfig"), str(local_bin),
                                 str(local_lib), str(home_bin)])

    def test_a_carve_out_around_a_checkout_is_never_emitted(self):
        # A PATH entry that holds the clone would re-open it.
        allow = self.settings(str(self.repos))["sandbox"]["filesystem"]["allowRead"]
        self.assertEqual(allow, [])

    def test_the_arms_own_session_directory_stays_readable_to_the_read_tool(self):
        projects = self.home / ".claude" / "projects"
        own_name = "-tmp-workspace-abc"
        earlier = ["-home-u-repos-example-site-platform", "-tmp-llm-judge-1",
                   "-tmp-workspace-abd", "-tmp-work", f"{own_name}-longer"]
        for name in earlier:
            (projects / name).mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (self.home / ".claude.json").write_text("{}", encoding="utf-8")
        (self.home / ".bash_history").write_text("", encoding="utf-8")
        settings = self.settings(session_dir=projects / own_name)
        rules = read_rule_paths(settings)
        self.assertNotIn(str(self.home), rules)
        for path in (*(projects / name / "s.jsonl" for name in earlier),
                     self.home / ".claude" / "settings.json",
                     self.home / ".claude.json", self.home / ".bash_history",
                     self.repos / "skills-evals" / "README.md"):
            self.assertTrue(covers(rules, path), path)
        own = projects / own_name / "s1" / "tool-results" / "out.txt"
        self.assertFalse(covers(rules, own))
        # Commands still lose all of HOME and the profile.
        self.assertIn(str(self.home), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(str(self.home / ".claude"),
                      settings["sandbox"]["filesystem"]["denyRead"])

    def test_a_session_created_after_the_settings_is_denied_too(self):
        # Structural, not a list of what existed: every other name, now or
        # later, at every level down to the arm's own session directory.
        projects = self.home / ".claude" / "projects"
        projects.mkdir(parents=True)
        own_name = "-tmp-workspace-abc"
        rules = read_rule_paths(self.settings(session_dir=projects / own_name))
        later = ["-tmp-workspace-other", "-tmp-workspace-abd", "-tmp-workspace-ab",
                 f"{own_name}x", "-Tmp-workspace-zzz", "-home-u-repos-x", "x", "-"]
        for name in later:
            self.assertTrue(covers(rules, projects / name / "s.jsonl"), name)
        for path in (self.home / "later-file", self.home / ".claude" / "later",
                     self.home / ".claudex" / "f", self.home / ".c" / "f"):
            self.assertTrue(covers(rules, path), path)
        self.assertFalse(covers(rules, projects / own_name / "t" / "out.txt"))

    def test_the_complement_never_negates_a_class(self):
        # CLI 2.1.292 matched `[!c]` and `[^c]` as classes holding `c`, and
        # ignores case (measured): every class leaves out both cases.
        for pattern in run_eval._complement_patterns("-tmp-wX"):
            self.assertNotIn("[!", pattern)
            self.assertNotIn("[^", pattern)
        cls = run_eval._class_without("w")
        self.assertNotIn("w", cls)
        self.assertNotIn("W", cls)
        self.assertTrue(cls.endswith("-]"))

    def test_a_clone_straight_under_home_keeps_the_home_carve_out(self):
        clone = self.home / "skills-evals"
        clone.mkdir()
        git("init", "-q", cwd=clone)
        (clone / "f").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=clone)
        git("commit", "-q", "-m", "init", cwd=clone)
        projects = self.home / ".claude" / "projects"
        projects.mkdir(parents=True)
        own = projects / "-tmp-workspace-abc"
        settings = run_eval.arm_sandbox_settings(
            [], home=self.home, path_env="", harness_root=clone,
            session_dir=own, profiles=[self.home / ".claude"])
        rules = read_rule_paths(settings)
        self.assertNotIn(str(self.home), rules)
        self.assertFalse(covers(rules, own / "s1" / "tool-results" / "out.txt"))
        self.assertTrue(covers(rules, clone / "f"))
        self.assertIn(str(self.home), settings["sandbox"]["filesystem"]["denyRead"])

    def test_an_existing_name_that_differs_only_in_case_before_leaving(self):
        # The matcher ignores case, so the patterns leave the kept name
        # where the case-folded names part; an entry leaving it there at a
        # character outside the class is named literally.
        projects = self.home / ".claude" / "projects"
        odd = projects / "-TMP-workspace-abc12!"
        odd.mkdir(parents=True)
        rules = read_rule_paths(self.settings(
            session_dir=projects / "-tmp-workspace-abc123"))
        self.assertTrue(covers(rules, odd / "s.jsonl"))
        self.assertFalse(covers(rules, projects / "-tmp-workspace-abc123" / "x"))

    def test_the_archive_and_the_results_are_denied_wherever_they_are(self):
        archive = self.state / "skills-evals" / "sessions" / "-tmp-old.abc"
        archive.mkdir(parents=True)
        results = self.root / "elsewhere" / "results"
        results.mkdir(parents=True)
        settings = self.settings(outputs=[results])
        rules = read_rule_paths(settings)
        for path in (archive / "s.jsonl", results / "t1" / "raw.json"):
            self.assertTrue(covers(rules, path), path)
            self.assertTrue(covers(settings["sandbox"]["filesystem"]["denyRead"], path))

    def test_a_workspace_under_the_results_dir_is_a_named_error(self):
        results = self.tmp
        with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
            run_eval.check_workspace_readable(self.workspace, run_eval.arm_read_denied(
                home=self.home, harness_root=self.worktree, profiles=[],
                outputs=[results]))
        self.assertIn("a harness output directory", str(caught.exception))

    def test_other_harness_directories_under_tmpdir_are_denied_now_and_later(self):
        own = self.tmp / "workspace-abc12345"
        own.mkdir()
        for name in ("workspace-old00001", "skills-evals-with_guidance-x1",
                     "deps-cache-zz", "unrelated-dir"):
            (self.tmp / name).mkdir()
        settings = self.settings(workspace=own)
        rules = read_rule_paths(settings)
        for name in ("workspace-old00001", "skills-evals-with_guidance-x1",
                     "deps-cache-zz", "workspace-later999", "workspace-abc12346",
                     "workspace-abc12345x", "scoring-seed-later"):
            self.assertTrue(covers(rules, self.tmp / name / "f"), name)
        self.assertFalse(covers(rules, own / "f"))
        self.assertFalse(covers(rules, self.tmp / "unrelated-dir" / "f"))
        deny_read = settings["sandbox"]["filesystem"]["denyRead"]
        self.assertIn(str(self.tmp / "workspace-old00001"), deny_read)
        self.assertNotIn(str(own), deny_read)

    def test_workspace_git_private_metadata_is_denied_now_and_later(self):
        # workspace_git keeps each workspace's trusted Git metadata in a
        # private directory under TMPDIR: another arm's (or this arm's own)
        # must not be read through the sandbox, nor created later.
        sys.path.insert(0, str(ROOT / "harness"))
        import workspace_git
        with mock.patch.object(tempfile, "tempdir", str(self.tmp)):
            private = workspace_git._private_dir()
        self.assertTrue(private.name.startswith(run_eval.HARNESS_TEMP_PREFIXES))
        settings = self.settings(workspace=self.workspace)
        rules = read_rule_paths(settings)
        self.assertIn(str(private), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(rules, private / "objects" / "x"))
        self.assertTrue(covers(rules, self.tmp / (private.name[:-1] + "z") / "HEAD"))
        self.assertFalse(covers(rules, self.workspace / "f"))

    def test_a_guidance_arms_own_scratch_is_kept(self):
        scratch = self.tmp / "skills-evals-with_guidance-q1w2e3r4"
        (scratch / "ws").mkdir(parents=True)
        (self.tmp / "skills-evals-without_guidance-zz").mkdir()
        rules = read_rule_paths(self.settings(workspace=scratch / "ws"))
        self.assertFalse(covers(rules, scratch / "ws" / "f"))
        self.assertFalse(covers(rules, scratch / "config" / "projects" / "x"))
        self.assertTrue(covers(rules, self.tmp / "skills-evals-without_guidance-zz" / "f"))

    def test_a_name_outside_the_class_is_named_literally(self):
        projects = self.home / ".claude" / "projects"
        (projects / "(odd) name").mkdir(parents=True)
        rules = read_rule_paths(self.settings(session_dir=projects / "-tmp-x"))
        self.assertTrue(covers(rules, projects / "(odd) name" / "s.jsonl"))

    def test_a_link_out_of_home_to_a_path_the_arm_needs_is_spared(self):
        # It leads nowhere secret, and a wildcard rule naming it would deny
        # /usr/bin to every command; so no rule names it, and the rest of
        # HOME is still denied.
        projects = self.home / ".claude" / "projects"
        projects.mkdir(parents=True)
        (self.home / "tools").symlink_to("/usr/bin")
        rules = read_rule_paths(self.settings(session_dir=projects / "-tmp-x"))
        self.assertFalse(covers_name(rules, self.home / "tools"))
        for path in (self.home / "toolsx", self.home / "tool", self.home / "t",
                     self.home / ".cargo" / "f", self.home / "secret"):
            self.assertTrue(covers(rules, path), path)

    def test_a_github_runner_home_is_not_refused(self):
        # ubuntu-24.04 runner images copy /etc/skel/.ghcup, a link to
        # /usr/local/.ghcup, into HOME (actions/runner-images'
        # install-haskell.sh); here the link leads to a PATH directory's
        # parent. Every arm failed `read_rules_unsafe` on it (PR #345's CI).
        ghcup = self.root / "usr-local" / ".ghcup"
        (ghcup / "bin").mkdir(parents=True)
        (self.home / ".ghcup").symlink_to(ghcup)
        for name in (".cargo", ".rustup", ".dotnet", ".nvm", "work"):
            (self.home / name).mkdir()
        projects = self.home / ".claude" / "projects"
        own = projects / "-tmp-workspace-abc"
        own.mkdir(parents=True)
        (projects / "-tmp-workspace-old").mkdir()
        settings = self.settings(path_env=str(ghcup / "bin"), session_dir=own)
        rules = read_rule_paths(settings)
        self.assertFalse(covers_name(rules, self.home / ".ghcup"))
        self.assertFalse(covers(rules, own / "s1" / "tool-results" / "out.txt"))
        for path in (self.home / ".cargo" / "f", self.home / ".rustup" / "f",
                     self.home / ".dotnet" / "f", self.home / ".nvm" / "f",
                     self.home / "work" / "f", self.home / ".ghcupx" / "f",
                     self.home / ".ghcu" / "f", self.home / ".g" / "f",
                     projects / "-tmp-workspace-old" / "s.jsonl"):
            self.assertTrue(covers(rules, path), path)
        self.assertIn(str(self.home), settings["sandbox"]["filesystem"]["denyRead"])

    def test_an_existing_case_variant_of_a_spared_link_is_refused(self):
        # The matcher ignores case, so sparing .ghcup also leaves the
        # separate .GHCUP directory readable. Denying it literally would
        # walk the spared link and deny the PATH directory too.
        ghcup = self.root / "usr-local" / ".ghcup"
        (ghcup / "bin").mkdir(parents=True)
        (self.home / ".ghcup").symlink_to(ghcup)
        (self.home / ".GHCUP").mkdir()
        own = self.home / ".claude" / "projects" / "-tmp-workspace-abc"
        own.mkdir(parents=True)
        with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
            self.settings(path_env=str(ghcup / "bin"), session_dir=own)
        self.assertEqual(caught.exception.code, "read_rules_unsafe")

    def test_a_link_both_needed_and_denied_is_refused(self):
        # Spared, the Read tool would reach a denied path through it: a
        # checkout's PATH directory, TMPDIR (other arms' workspaces), or a
        # directory around HOME.
        projects = self.home / ".claude" / "projects"
        projects.mkdir(parents=True)
        (self.registry / "bin").mkdir()
        cases = (("reg", self.registry / "bin", str(self.registry / "bin")),
                 ("tmp", self.tmp, ""),
                 ("up", self.root, ""))
        for name, target, path_env in cases:
            with self.subTest(name=name):
                link = self.home / name
                link.symlink_to(target)
                try:
                    with self.assertRaises(run_eval.ArmReadIsolationError) as caught:
                        self.settings(path_env=path_env, workspace=self.workspace,
                                      session_dir=projects / "-tmp-x")
                    self.assertEqual(caught.exception.code, "read_rules_unsafe")
                finally:
                    link.unlink()

    def test_spared_names_leave_every_other_name_denied(self):
        patterns = run_eval._complement_patterns("-tmp-x", [".ghcup", ".gh"])
        names = ["-tmp-x", ".ghcup", ".gh"]
        for name in ("-tmp-y", ".ghcupx", ".ghc", ".g", ".x", "x", ".GHCUPX", "-tmp-xx"):
            self.assertTrue(any(fnmatch.fnmatchcase(name.lower(), p.lower())
                                for p in patterns), name)
        for name in names + [".GHCUP"]:
            self.assertFalse(any(fnmatch.fnmatchcase(name.lower(), p.lower())
                                 for p in patterns), name)

    def test_no_wildcard_rule_reaches_below_its_match(self):
        # `<wildcard>/**` made the sandbox walk every file below a match.
        (self.home / "tmpdata").mkdir()
        (self.home / "inside").symlink_to(self.home / "tmpdata")
        (self.home / "away").symlink_to(self.registry)
        projects = self.home / ".claude" / "projects"
        projects.mkdir(parents=True)
        deny = self.settings(session_dir=projects / "-tmp-x")["permissions"]["deny"]
        for rule in deny:
            if rule.endswith("/**)"):
                self.assertNotIn("*", rule.removesuffix("/**)"), rule)
        rules = read_rule_paths({"permissions": {"deny": deny}})
        self.assertTrue(covers(rules, self.home / "tmpdata" / "f"))
        self.assertTrue(covers(rules, self.home / "inside"))

    def test_a_session_directory_outside_home_leaves_home_denied_whole(self):
        rules = read_rule_paths(self.settings(session_dir=self.root / "cfg" / "x"))
        self.assertIn(str(self.home), rules)


class RunAgentReadIsolationTests(_TempLayout):
    def setUp(self):
        super().setUp()
        self.log = self.root / "calls.jsonl"
        stand_in = self.root / "claude"
        stand_in.write_text(STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
        # PATH explicit; the mount table is `self.mountinfo` (`_TempLayout`).
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.root / "state"),
            "CLAUDE_BIN": str(stand_in), "RD_LOG": str(self.log),
            "PATH": os.pathsep.join(["/usr/local/bin", "/usr/bin", "/bin"])})
        patcher.start()
        self.addCleanup(patcher.stop)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def arm(self, **extra) -> dict:
        return {"name": "without_skill", "timeout": 60,
                "env": {"RD_LOG": str(self.log)},
                "read_denied": [self.registry, self.guidance], **extra}

    def assert_reads_fenced(self, argv: list[str]) -> None:
        settings = settings_of(argv)
        deny_read = settings["sandbox"]["filesystem"]["denyRead"]
        for path in (self.registry, self.guidance, self.home,
                     run_eval.HARNESS_CLONE_ROOT, run_eval.HARNESS_CLONE_ROOT.parent):
            self.assertIn(str(path), deny_read)
        self.assertTrue(covers(read_rule_paths(settings), self.registry / "x"))
        self.assertTrue(covers(read_rule_paths(settings),
                               run_eval.HARNESS_ROOT / "evals"))

    def test_mountinfo_decode_and_value_errors_are_named_arm_refusals(self):
        for error in (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
                      ValueError("invalid path")):
            with self.subTest(error=type(error).__name__):
                original = Path.read_text
                def read_text(path, *args, **kwargs):
                    if path == Path("/proc/self/mountinfo"):
                        raise error
                    return original(path, *args, **kwargs)
                with mock.patch.object(Path, "read_text", read_text):
                    out = run_eval.run_agent(self.workspace, "do it", self.arm())
                self.assertEqual(out["error"], "read_rules_unsafe")
                self.assertEqual(self.calls(), [])

    def test_a_with_skill_arm_gets_the_skill_copied_and_the_registry_denied(self):
        arm = self.arm(name="with_skill", skill="other-skill", registry=FAKE_REGISTRY,
                       read_denied=[FAKE_REGISTRY])
        self.assertIsNone(run_eval.install_skill(self.workspace, arm))
        out = run_eval.run_agent(self.workspace, "do it", arm)
        self.assertNotIn("error", out, out)
        (argv,) = self.calls()
        settings = settings_of(argv)
        self.assertIn(str(FAKE_REGISTRY.resolve()),
                      settings["sandbox"]["filesystem"]["denyRead"])
        # The skill is in the workspace before the agent starts, so the
        # registry is not needed at run time.
        self.assertTrue((self.workspace / ".claude" / "skills" / "other-skill" /
                         "SKILL.md").is_file())

    def test_a_without_skill_arm_and_its_followups_are_fenced(self):
        out = run_eval.run_agent(self.workspace, "do it",
                                 self.arm(followups=["again"]))
        self.assertNotIn("error", out, out)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        for argv in calls:
            self.assert_reads_fenced(argv)

    def test_a_guidance_shaped_arm_is_fenced_against_the_real_home(self):
        scratch_home = self.root / "tmp" / "scratch-home"
        scratch_home.mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(scratch_home),
               "CLAUDE_CONFIG_DIR": str(self.root / "tmp" / "config"),
               "RD_LOG": str(self.log)}
        out = run_eval.run_agent(self.workspace, "do it", {
            "name": "with_guidance", "timeout": 60, "setting_sources": "user,project",
            "env_override": env, "read_denied": [self.guidance]})
        self.assertNotIn("error", out, out)
        (argv,) = self.calls()
        settings = settings_of(argv)
        self.assertIn(str(self.home), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(str(self.home), read_rule_paths(settings))
        self.assertIn(str(scratch_home), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(covers(read_rule_paths(settings), scratch_home / "private"))

    def alias_with_tools(self) -> Path:
        """A Windows drive (custom automount root) holding a `bin` with
        stand-ins for the tools an arm needs."""
        drive = self.root / "drive-c"
        (drive / "bin").mkdir(parents=True)
        for tool in ("bash", "git"):
            (drive / "bin" / tool).write_text("#!/bin/sh\n", encoding="utf-8")
            (drive / "bin" / tool).chmod(0o755)
        self.mountinfo += f"2 1 0:30 / {drive} rw - drvfs C: rw\n"
        return drive

    def test_inherited_profile_at_a_root_alias_strips_the_arms_path(self):
        alias = self.root / "mirror"
        (alias / "usr" / "bin").mkdir(parents=True)
        self.mountinfo += f"2 1 8:1 / {alias} rw - ext4 /dev/example rw\n"
        path_log = self.root / "path.log"
        linux = "/usr/local/bin:/usr/bin:/bin"
        with mock.patch.dict(os.environ, {
                "CLAUDE_CONFIG_DIR": str(alias),
                "PATH": f"{alias}/usr/bin:{linux}"}):
            out = run_eval.run_agent(self.workspace, "do it", self.arm(
                env={"RD_LOG": str(self.log), "RD_PATH_LOG": str(path_log)}))
        self.assertNotIn("error", out, out)
        self.assertEqual(path_log.read_text().splitlines(), [linux])
        (argv,) = self.calls()
        settings = settings_of(argv)
        self.assertIn(str(alias), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(f"Read(/{alias}/**)", settings["permissions"]["deny"])
        self.assertEqual([p for p in settings["sandbox"]["filesystem"]["allowRead"]
                          if run_eval._within(Path(p), alias)], [])

    def test_path_entries_under_an_alias_never_reach_the_arm(self):
        drive = self.alias_with_tools()
        self.mountinfo += "3 1 0:31 / /mnt/c rw - 9p C:\\134 rw,aname=drvfs\n"
        path_log = self.root / "path.log"
        linux = os.pathsep.join(["/usr/local/bin", "/usr/bin", "/bin"])
        with mock.patch.dict(os.environ, {
                "PATH": os.pathsep.join([str(drive / "bin"), linux,
                                         "/mnt/c/Windows/system32"])}):
            out = run_eval.run_agent(self.workspace, "do it", self.arm(
                env={"RD_LOG": str(self.log), "RD_PATH_LOG": str(path_log)}))
        self.assertNotIn("error", out, out)
        self.assertEqual(path_log.read_text().splitlines(), [linux])
        (argv,) = self.calls()
        settings = settings_of(argv)
        self.assertIn(str(drive), settings["sandbox"]["filesystem"]["denyRead"])
        self.assertEqual([p for p in settings["sandbox"]["filesystem"]["allowRead"]
                          if run_eval._within(Path(p), drive)], [])

    def test_a_guidance_arms_alias_path_entries_are_stripped_too(self):
        drive = self.alias_with_tools()
        path_log = self.root / "path.log"
        (self.root / "tmp" / "scratch-home").mkdir()
        env = {"PATH": os.pathsep.join([str(drive / "bin"), "/usr/bin", "/bin"]),
               "HOME": str(self.root / "tmp" / "scratch-home"),
               "CLAUDE_CONFIG_DIR": str(self.root / "tmp" / "config"),
               "RD_LOG": str(self.log), "RD_PATH_LOG": str(path_log)}
        out = run_eval.run_agent(self.workspace, "do it", {
            "name": "with_guidance", "timeout": 60, "setting_sources": "user,project",
            "env_override": env, "read_denied": [self.guidance]})
        self.assertNotIn("error", out, out)
        self.assertEqual(path_log.read_text().splitlines(),
                         [os.pathsep.join(["/usr/bin", "/bin"])])
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(drive / "bin"),
                         "the caller's mapping was mutated")

    def test_a_tool_found_only_under_an_alias_fails_the_arm_by_name(self):
        drive = self.alias_with_tools()
        with mock.patch.dict(os.environ, {"PATH": str(drive / "bin")}):
            out = run_eval.run_agent(self.workspace, "do it", self.arm())
        self.assertEqual(out["error"], "toolchain_under_alias")
        self.assertIn("bash", out["detail"])
        self.assertNotIn(str(drive), out["detail"])
        self.assertEqual(self.calls(), [])

    def test_a_workspace_under_home_fails_before_the_cli_starts(self):
        workspace = self.home / "workspace-z"
        workspace.mkdir()
        out = run_eval.run_agent(workspace, "do it", self.arm())
        self.assertEqual(out["error"], "workspace_read_denied")
        self.assertIn("HOME", out["detail"])
        self.assertEqual(self.calls(), [])


class ReviewedTempPrefixTests(_TempLayout):
    PREFIXES = (
        "workspace-", "skills-evals-", "guidance-bridge-canary-", "propagation-",
        "scoring-seed-", "deps-python-", "deps-bundler-", "deps-cache-", "objective-repo-tests-",
        "objective-command-", "local-eval-guard-", "sink-mutation-", "trusted-git-",
        "scaffold-", "scaffold-context-", "claude-probe-home-", "skill-edit-guard-",
        "propose-skill-edit-", "scoring-guidance-", "scoring-skill-", "mine-real-work-",
        ".scaffold-", ".gh-label-", ".gh-timeline-", "usage-census-",
        "session-miner-", "lesson-classify-")

    def test_all_reviewed_tmpdir_prefixes_are_denied_to_commands_and_file_tools(self):
        for prefix in self.PREFIXES:
            (self.tmp / (prefix + "old")).mkdir()
        settings = self.settings(workspace=self.workspace)
        for prefix in self.PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertIn(str(self.tmp / (prefix + "old")),
                              settings["sandbox"]["filesystem"]["denyRead"])
                self.assertTrue(covers(read_rule_paths(settings),
                                       self.tmp / (prefix + "future") / "answer"))
        self.assertFalse(covers(read_rule_paths(settings), self.workspace / "own"))

    def test_tmpdir_allocations_have_reviewed_prefixes(self):
        # Parse the allocation, including f-string prefixes and constants.
        # An explicit parent may be TMPDIR; only the session archive's
        # dynamic prefixes are exempt, because the archive is denied whole.
        # TemporaryFile has no named store for an arm to open.
        sources = [*(ROOT / "harness").rglob("*.py"),
                   *(ROOT / "scripts").rglob("*.py"), ROOT / "harness/fakes/gh"]
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "tempfile"
                        and node.func.attr in ("mkdtemp", "TemporaryDirectory",
                                               "mkstemp", "NamedTemporaryFile")):
                    continue
                keywords = {k.arg: k.value for k in node.keywords}
                if (source == ROOT / "harness/run_eval.py"
                        and ast.unparse(keywords.get("dir", ast.Constant(None))) == "archive"):
                    self.assertEqual(ast.unparse(keywords["prefix"]),
                                     "f'{session_dir.name}.'")
                    continue
                prefix = keywords.get("prefix")
                if isinstance(prefix, ast.Constant):
                    text = prefix.value
                elif isinstance(prefix, ast.JoinedStr) and isinstance(
                        prefix.values[0], ast.Constant):
                    text = prefix.values[0].value
                elif isinstance(prefix, ast.JoinedStr) and isinstance(
                        prefix.values[0], ast.FormattedValue):
                    self.assertEqual(ast.unparse(prefix.values[0].value),
                                     "ARM_WORKSPACE_PREFIX")
                    text = run_eval.ARM_WORKSPACE_PREFIX
                elif isinstance(prefix, ast.Name) and prefix.id == "WORKSPACE_PREFIX":
                    text = run_eval.WORKSPACE_PREFIX
                elif isinstance(prefix, ast.Attribute) and ast.unparse(prefix) == "run_eval.WORKSPACE_PREFIX":
                    text = run_eval.WORKSPACE_PREFIX
                else:
                    self.fail(f"{source.relative_to(ROOT)}:{node.lineno}: "
                              "unclassified TMPDIR allocation")
                self.assertTrue(any(text.startswith(p) for p in self.PREFIXES),
                                (str(source), node.lineno, text))

    @unittest.skipUnless(bash_ast.parser_importable(),
                         "the pinned parser is not installed here")
    def test_shell_census_scratch_uses_a_denied_prefix(self):
        from scorers.bash_ast import parse_bash
        source = (ROOT / "scripts/publish_usage_census.sh").read_bytes()
        root = parse_bash(source)
        commands = []
        def visit(node):
            if node.type == "command":
                name = node.child_by_field_name("name")
                if name is not None and source[name.start_byte:name.end_byte] == b"mktemp":
                    commands.append(node)
            for child in node.children:
                visit(child)
        visit(root)
        self.assertEqual(len(commands), 1)
        arguments = commands[0].children_by_field_name("argument")
        self.assertEqual([source[a.start_byte:a.end_byte] for a in arguments],
                         [b"-d", b'"${TMPDIR:-/tmp}/usage-census-XXXXXXXX"'])
        self.assertIn("usage-census-", run_eval.HARNESS_TEMP_PREFIXES)


class RunCheckoutsWiringTests(unittest.TestCase):
    def test_run_checkouts_lists_registries_and_guidance(self):
        registries = {"a": {"path": Path("/x/a")}, "b": {"path": Path("/x/b")}}
        self.assertEqual(
            run_eval.run_checkouts(argparse.Namespace(), registries=registries,
                                   guidance_dir=Path("/x/g")),
            [Path("/x/a"), Path("/x/b"), Path("/x/g")])

    def test_run_checkouts_resolves_the_other_kind_from_the_flags(self):
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            for key in ("SKILLS_EVALS_REGISTRIES", "AGENTSKILLS_DIR",
                        "AGENT_GUIDANCE_DIR"):
                env.pop(key, None)
            paths = run_eval.run_checkouts(argparse.Namespace(
                registry=["agentskills=/x/skills"], guidance="/x/guidance"))
        self.assertIn(Path("/x/skills"), paths)
        self.assertEqual(paths[-1], Path("/x/guidance"))

    def test_both_arm_builders_hand_run_agent_the_checkouts(self):
        # Parsed, not grepped: the `arm_config = {...}` literal in each builder
        # carries a "read_denied" key whose value calls run_checkouts.
        tree = ast.parse((ROOT / "harness" / "run_eval.py").read_text(encoding="utf-8"))
        found = {}
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef) or func.name not in (
                    "_run_arm", "_run_guidance_arm"):
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                        and any(isinstance(t, ast.Name) and t.id == "arm_config"
                                for t in node.targets)):
                    for key, value in zip(node.value.keys, node.value.values):
                        if isinstance(key, ast.Constant) and key.value == "read_denied":
                            found[func.name] = (isinstance(value, ast.Call)
                                                and getattr(value.func, "id", None)
                                                == "run_checkouts")
        self.assertEqual(found, {"_run_arm": True, "_run_guidance_arm": True})

    def test_both_arm_builders_hand_run_agent_the_results_dir(self):
        tree = ast.parse((ROOT / "harness" / "run_eval.py").read_text(encoding="utf-8"))
        found = {}
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef) or func.name not in (
                    "_run_arm", "_run_guidance_arm"):
                continue
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                        and any(isinstance(t, ast.Name) and t.id == "arm_config"
                                for t in node.targets)):
                    for key, value in zip(node.value.keys, node.value.values):
                        if isinstance(key, ast.Constant) and key.value == "read_denied_outputs":
                            found[func.name] = (isinstance(value, ast.Call)
                                                and getattr(value.func, "id", None)
                                                == "run_outputs")
        self.assertEqual(found, {"_run_arm": True, "_run_guidance_arm": True})
        self.assertEqual(run_eval.run_outputs(argparse.Namespace(results_dir=Path("r"))),
                         [Path("r").resolve()])

    def test_run_outputs_adds_every_read_deny_dir(self):
        # A wrapper's own output tree (local_eval's earlier trials,
        # propose_skill_edit's baseline run and patch) sits beside this run's
        # --results-dir: `--read-deny` names it.
        self.assertEqual(
            run_eval.run_outputs(argparse.Namespace(
                results_dir=Path("/x/out/t2"),
                read_deny=[Path("/x/out"), Path("/y/records")])),
            [Path("/x/out/t2"), Path("/x/out"), Path("/y/records")])


if __name__ == "__main__":
    unittest.main()
