"""Mandatory scorer filesystem boundaries, with offline unit seams and live probes."""
from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import run_eval
from scorers import commands, objective, repo_tests


class FakeProbe:
    """A `subprocess.Popen` stand-in that prints one line and exits 0."""

    def __init__(self, out: bytes):
        self.out = out
        self.returncode = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, timeout=None):
        return self.out, b""

    def kill(self):
        pass


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scorer-sandbox-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        self.env_root = self.root / "environment"
        self.env_root.mkdir()
        self.env = commands._environment(self.env_root, self.ws)

    def test_missing_bubblewrap_never_executes_workspace_code(self):
        with mock.patch.object(commands, "_bubblewrap", return_value=None, create=True), \
                mock.patch.object(commands, "_run_command", return_value=0) as run:
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertEqual((passed, detail), (False, "scorer_sandbox_unavailable exit=-1"))
        run.assert_not_called()

    def test_failed_probes_never_execute_workspace_code(self):
        with mock.patch.object(commands, "_bubblewrap", return_value="/usr/bin/bwrap", create=True), \
                mock.patch.object(commands.subprocess, "run", return_value=SimpleNamespace(returncode=17)), \
                mock.patch.object(commands, "_run_command", return_value=0) as run:
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertFalse(passed)
        self.assertIn("scorer_sandbox_unavailable exit=17", detail)
        run.assert_not_called()

    def test_network_isolation_failure_is_closed_without_retry(self):
        with mock.patch.object(commands, "_bubblewrap", return_value="/usr/bin/bwrap"), \
                mock.patch.object(commands.subprocess, "run", side_effect=[
                    SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]) as probe:
            with self.assertRaisesRegex(commands.ScorerSandboxUnavailable,
                                        "scorer_sandbox_unavailable exit=1"):
                commands._sandbox_prefix(self.ws, self.env_root, self.env)
        self.assertEqual(probe.call_count, 1)
        self.assertIn("--unshare-net", probe.call_args.args[0])

    def test_external_python_mounts_only_binary_and_language_libraries(self):
        prefix = self.root / "python-install"
        binary = prefix / "bin" / "python3"
        binary.parent.mkdir(parents=True)
        binary.write_text("runtime")
        library = prefix / "lib" / "python3.12"
        library.mkdir(parents=True)
        shared = prefix / "lib" / "libpython3.12.so.1.0"
        shared.write_text("runtime")
        (prefix / "lib" / "unrelated").mkdir()
        with mock.patch.object(commands.sys, "executable", str(binary)), \
                mock.patch.object(commands, "_harness_runtime", return_value=None):
            paths = commands._runtime_mounts(self.ws, [])
        self.assertEqual(set(paths), {binary, library, shared})
        self.assertNotIn(prefix, paths)
        with mock.patch.object(commands.sys, "executable", str(binary)), \
                mock.patch.object(commands, "_harness_runtime", return_value=None):
            self.assertEqual(commands._runtime_mounts(self.ws, [prefix]), [])

    def test_external_ruby_mounts_its_library_symlinks_and_compiled_prefix(self):
        # A relocated Ruby (the cloud image's /opt/ruby-<v>) asks the loader
        # for `libruby.so.3.3`, a symlink, and keeps its compiled-in prefix
        # (a second directory) in RUNPATH and $LOAD_PATH: both must be bound
        # by name, or `ruby` exits 127 inside the sandbox.
        prefix = self.root / "ruby-install"
        binary = prefix / "bin" / "ruby"
        binary.parent.mkdir(parents=True)
        binary.write_text("runtime")
        (prefix / "lib" / "ruby").mkdir(parents=True)
        shared = prefix / "lib" / "libruby.so.3.3.6"
        shared.write_text("runtime")
        (prefix / "lib" / "libruby.so.3.3").symlink_to("libruby.so.3.3.6")
        compiled = self.root / "ruby-compiled"
        (compiled / "bin").mkdir(parents=True)
        (compiled / "bin" / "ruby").write_text("runtime")
        (compiled / "lib" / "ruby").mkdir(parents=True)
        (compiled / "lib" / "libruby.so.3.3").write_text("runtime")
        with mock.patch.object(commands.sys, "executable", "/usr/bin/python3"), \
                mock.patch.object(commands, "_harness_runtime",
                                  side_effect=lambda name, *a: str(binary) if name == "ruby" else None), \
                mock.patch.object(commands, "_ruby_prefix", return_value=compiled):
            paths = commands._runtime_mounts(self.ws, [])
            mounts = commands._sandbox_mounts(self.ws, self.env_root, [])
        self.assertEqual(set(paths), {binary, prefix / "lib" / "ruby", shared,
                                      prefix / "lib" / "libruby.so.3.3",
                                      compiled / "bin" / "ruby", compiled / "lib" / "ruby",
                                      compiled / "lib" / "libruby.so.3.3"})
        triples = [mounts[i:i + 3] for i in range(len(mounts))]
        self.assertIn(["--ro-bind", str(shared), str(prefix / "lib" / "libruby.so.3.3")], triples)

    def test_ruby_prefix_probe_fails_closed(self):
        binary = self.root / "ruby"
        binary.write_text("#!/bin/sh\nprintf '%s' \"$RUBY_PREFIX_OUT\"\n")
        binary.chmod(0o700)
        with mock.patch.object(commands, "SYSTEM_PATH", ("/usr/bin", "/bin")):
            # Not a prefix with a ruby in it, a relative path, and a denied root.
            for value in ("/nonexistent-prefix", "relative/prefix"):
                with mock.patch.dict(os.environ, {"RUBY_PREFIX_OUT": value}):
                    self.assertIsNone(commands._ruby_prefix(binary, self.ws, []))
        compiled = self.root / "compiled"
        (compiled / "bin").mkdir(parents=True)
        (compiled / "bin" / "ruby").write_text("runtime")
        with mock.patch.object(commands.subprocess, "Popen",
                               side_effect=lambda *a, **k: FakeProbe(str(compiled).encode())):
            self.assertEqual(commands._ruby_prefix(binary, self.ws, []), compiled)
            self.assertIsNone(commands._ruby_prefix(binary, self.ws, [compiled]))

    def test_successful_prefix_contains_every_mandatory_namespace_flag(self):
        with mock.patch.object(commands, "_bubblewrap", return_value="/usr/bin/bwrap"), \
                mock.patch.object(commands.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            prefix, network = commands._sandbox_prefix(self.ws, self.env_root, self.env)
        self.assertEqual(network, "isolated")
        for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                     "--die-with-parent", "--new-session", "--proc", "--dev"):
            self.assertIn(flag, prefix)

    def test_seam_includes_registry_guidance_outputs_and_external_profile(self):
        args = SimpleNamespace(registry=["agentskills=" + str(self.root / "registry")],
                               guidance=str(self.root / "guidance"),
                               results_dir=self.root / "results", read_deny=[self.root / "wrapper"],
                               context_repos={"example-adopter/example-site-platform": self.root / "context"})
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "profile")}):
            roots = run_eval.scorer_read_denied(args, harness_root=self.ws)
        for name in ("registry", "guidance", "results", "wrapper", "profile", "context"):
            self.assertIn(self.root / name, roots)

    def test_empty_root_allowlist_precedes_workspace_carve_out(self):
        prefix = commands._sandbox_mounts(self.ws, self.env_root, [self.root])
        self.assertNotIn(["--ro-bind", "/", "/"],
                         [prefix[i:i+3] for i in range(len(prefix))])
        self.assertLess(prefix.index("--tmpfs"), prefix.index("--bind"))
        self.assertEqual(prefix[prefix.index("--tmpfs") + 1], "/tmp")
        self.assertIn(["--bind", str(self.ws), str(self.ws)],
                      [prefix[i:i+3] for i in range(len(prefix))])

    def test_host_runtime_under_home_or_denied_root_is_not_exposed(self):
        runtime = self.root / "host-bin" / "node"
        runtime.parent.mkdir()
        runtime.write_text("#!/bin/sh\nexit 0\n")
        runtime.chmod(0o700)
        for denied in ([self.root], [Path.home()]):
            candidate = runtime if denied == [self.root] else Path.home() / ".local/bin/node"
            with self.subTest(denied=denied), \
                    mock.patch.object(commands, "_fixed_interpreter", return_value=None), \
                    mock.patch.object(commands.shutil, "which", return_value=str(candidate)), \
                    mock.patch.object(commands.os, "access", return_value=True):
                self.assertIsNone(commands._harness_node(self.ws, denied))

    def test_conflicting_denials_and_broad_workspace_fail_closed(self):
        for workspace, environment, denied in (
                (self.ws, self.env_root, [self.ws]),
                (self.ws, self.env_root, [self.ws / "private"]),
                (self.ws, self.env_root, [self.env_root]),
                (self.ws, self.env_root, [self.env_root / "private"]),
                (Path("/"), self.env_root, [])):
            with self.subTest(workspace=workspace, denied=denied):
                with self.assertRaises(commands.ScorerSandboxUnavailable):
                    commands._sandbox_mounts(workspace, environment, denied)

    def test_default_denials_include_harness_and_real_home(self):
        denied = commands._read_denied(None)
        self.assertIn(REPO, denied)
        self.assertIn(Path.home().resolve(), denied)

    def test_trusted_bubblewrap_cannot_resolve_into_workspace(self):
        planted = self.ws / "bwrap"
        planted.write_text("#!/bin/sh\nexit 0\n")
        planted.chmod(0o700)
        with mock.patch.object(commands.shutil, "which", return_value=str(planted)), \
                mock.patch.object(commands, "BWRAP_PATHS", ()):
            self.assertIsNone(commands._bubblewrap(self.ws))

    def test_original_workspace_path_cannot_supply_bubblewrap(self):
        final = self.root / "original-final"
        final.mkdir()
        planted = final / "bwrap"
        planted.write_text("#!/bin/sh\nexit 0\n")
        planted.chmod(0o700)
        with mock.patch.dict(os.environ, {"PATH": str(final)}), \
                mock.patch.object(commands, "BWRAP_PATHS", ()):
            self.assertIsNone(commands._bubblewrap(self.ws, [final]))
        with mock.patch.object(commands, "BWRAP_PATHS", (str(planted),)):
            self.assertIsNone(commands._bubblewrap(self.ws, [final]))

    def test_later_sandbox_start_failure_is_named_without_output(self):
        with mock.patch.object(commands, "_sandbox_prefix", return_value=(["/usr/bin/bwrap", "--"], "isolated")), \
                mock.patch.object(commands, "_run_command", return_value=1):
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertFalse(passed)
        self.assertIn("scorer_sandbox_unavailable exit=1", detail)
        self.assertNotIn(str(self.root), detail)

    def test_child_pid_json_does_not_prove_sandbox_setup(self):
        def failed_after_fork(argv, workspace, env, stdout, stderr, timeout_s, *, pass_fds):
            os.write(pass_fds[0], b'{"child-pid": 123}')
            return 1
        with mock.patch.object(commands, "_sandbox_prefix", return_value=(["/usr/bin/bwrap", "--"], "isolated")), \
                mock.patch.object(commands, "_run_command", side_effect=failed_after_fork):
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertEqual((passed, detail), (False, "scorer_sandbox_unavailable exit=1"))

    def test_timeout_before_sandbox_marker_is_unavailable(self):
        def stalled_setup(argv, workspace, env, stdout, stderr, timeout_s, *, pass_fds):
            os.write(pass_fds[0], b'{"child-pid": 123}')
            raise subprocess.TimeoutExpired("private sentinel", timeout_s)
        with mock.patch.object(commands, "_sandbox_prefix", return_value=(["/usr/bin/bwrap", "--"], "isolated")), \
                mock.patch.object(commands, "_run_command", side_effect=stalled_setup):
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertEqual((passed, detail), (False, "scorer_sandbox_unavailable exit=-1"))

    def test_timeout_after_sandbox_marker_remains_command_timeout(self):
        def stalled_command(argv, workspace, env, stdout, stderr, timeout_s, *, pass_fds):
            os.write(pass_fds[0], b'scorer-sandbox-started\n')
            raise subprocess.TimeoutExpired("private sentinel", timeout_s)
        with mock.patch.object(commands, "_sandbox_prefix", return_value=(["/usr/bin/bwrap", "--"], "isolated")), \
                mock.patch.object(commands, "_run_command", side_effect=stalled_command):
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertFalse(passed)
        self.assertIn("command_timeout network=isolated", detail)
        self.assertNotIn("private sentinel", detail)

    def test_nonexecuting_fixtures_do_not_probe_git_clone(self):
        fixture = {"objective_checks": [{"type": "files_exist", "id": "files", "paths": ["x"]}]}
        with mock.patch.object(run_eval, "_git", side_effect=AssertionError("no scoring process")) as git:
            self.assertEqual(run_eval.scorer_read_denied(SimpleNamespace(), fixture=fixture), [])
        git.assert_not_called()

    def test_probe_exception_text_is_suppressed(self):
        with mock.patch.object(commands, "_bubblewrap", return_value="/usr/bin/bwrap"), \
                mock.patch.object(commands.subprocess, "run", side_effect=OSError("private sentinel")):
            passed, detail = commands.command_succeeds(str(self.ws), [], ["python3", "-c", "pass"])
        self.assertFalse(passed)
        self.assertIn("scorer_sandbox_unavailable", detail)
        self.assertNotIn("private sentinel", detail)

    def test_read_denials_route_only_to_executing_checks(self):
        checks = [{"id": "cmd", "type": "command_succeeds", "argv": ["python3", "-c", "pass"]},
                  {"id": "files", "type": "files_exist", "paths": ["x"]}]
        denied = [self.root / "private"]
        with mock.patch.dict(objective.CHECKS, {"command_succeeds": mock.Mock(return_value=(True, "ok")),
                                              "files_exist": mock.Mock(return_value=(True, "ok"))}):
            objective.run_checks({"objective_checks": checks}, str(self.ws), str(self.ws), read_denied=denied)
            self.assertEqual(objective.CHECKS["command_succeeds"].call_args.kwargs["read_denied"],
                             [*denied, self.ws.parent])
            self.assertNotIn("read_denied", objective.CHECKS["files_exist"].call_args.kwargs)
        checks[0]["read_denied"] = []
        with self.assertRaises(ValueError):
            objective.run_checks({"objective_checks": checks}, str(self.ws), str(self.ws))

    def test_all_run_eval_scoring_calls_receive_the_seam(self):
        calls = [node for node in ast.walk(ast.parse((REPO / "harness/run_eval.py").read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "objective"
                 and node.func.attr == "run_checks"]
        self.assertEqual(len(calls), 5)
        for call in calls:
            keyword = next((k for k in call.keywords if k.arg == "read_denied"), None)
            self.assertIsNotNone(keyword)
            self.assertIsInstance(keyword.value, ast.Call)
            self.assertEqual(keyword.value.func.id, "scorer_read_denied")

    def test_seam_uses_common_git_directory_for_worktree_clone_parent(self):
        checkout = self.root / "repos" / "harness"
        private = checkout / ".git" / "worktrees" / "worktree"
        private.mkdir(parents=True)
        (private / "commondir").write_text("../..\n", encoding="utf-8")
        worktree = self.root / "elsewhere" / "worktree"
        worktree.mkdir(parents=True)
        # A linked worktree's `.git` is a file, which the workspace Git helper
        # refuses; the trusted harness checkout is read as data instead.
        (worktree / ".git").write_text(f"gitdir: {private}\n", encoding="utf-8")
        args = SimpleNamespace(results_dir=self.root / "outputs")
        for kind in (None, "command_succeeds", "repo_tests"):
            fixture = None if kind is None else {"objective_checks": [{"type": kind}]}
            with self.subTest(kind=kind), mock.patch.object(
                    run_eval, "_git", side_effect=AssertionError("no Git process")):
                denied = run_eval.scorer_read_denied(args, harness_root=worktree, fixture=fixture)
            self.assertIn(checkout / ".git", denied)
            self.assertIn(checkout.parent, denied)
            self.assertIn(worktree, denied)
            self.assertIn(args.results_dir.resolve(), denied)
            self.assertIn(run_eval.session_archive_dir().parent.resolve(), denied)


class FixedInterpreterTests(unittest.TestCase):
    """Symlinked interpreters are resolved, and only inside the system mounts."""

    def layout(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "usr/bin").mkdir(parents=True)
        (tmp / "alternatives").mkdir()
        (tmp / "elsewhere").mkdir()
        return tmp

    def executable(self, path):
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_symlink_chain_through_an_unmounted_directory_resolves_inside(self):
        tmp = self.layout()
        target = self.executable(tmp / "usr/bin/python3.13")
        # Like /usr/bin/python3 -> /etc/alternatives/python3 -> /usr/bin/python3.13.
        (tmp / "alternatives/python3").symlink_to(target)
        (tmp / "usr/bin/python3").symlink_to(tmp / "alternatives/python3")
        with mock.patch.object(commands, "SYSTEM_MOUNTS", (str(tmp / "usr"),)), \
                mock.patch.dict(commands.INTERPRETERS,
                                {"python3": (str(tmp / "usr/bin/python3"),)}):
            self.assertEqual(commands._fixed_interpreter("python3"), str(target))

    def test_target_outside_the_system_mounts_is_skipped(self):
        tmp = self.layout()
        outside = self.executable(tmp / "elsewhere/python3")
        (tmp / "usr/bin/python3").symlink_to(outside)
        inside = self.executable(tmp / "usr/bin/fallback")
        with mock.patch.object(commands, "SYSTEM_MOUNTS", (str(tmp / "usr"),)):
            with mock.patch.dict(commands.INTERPRETERS,
                                 {"python3": (str(tmp / "usr/bin/python3"),)}):
                self.assertIsNone(commands._fixed_interpreter("python3"))
            with mock.patch.dict(commands.INTERPRETERS,
                                 {"python3": (str(tmp / "usr/bin/python3"), str(inside))}):
                self.assertEqual(commands._fixed_interpreter("python3"), str(inside))


class LiveSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bwrap = shutil.which("bwrap") or str(Path.home() / ".local/bin/bwrap")
        try:
            probe = subprocess.run([bwrap, "--ro-bind", "/", "/", "--unshare-pid", "--proc", "/proc",
                                    "--dev", "/dev", "--die-with-parent", "--", "/usr/bin/true"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("live bubblewrap filesystem/PID namespaces unavailable")
        if probe.returncode:
            raise unittest.SkipTest("live bubblewrap filesystem/PID namespaces unavailable")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scorer-live-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.denied = self.root / "private"
        self.denied.mkdir()
        (self.denied / "sentinel").write_text("private sentinel")

    def command(self, code, *args, denied=None):
        return commands.command_succeeds(str(self.ws), [], ["python3", "-c", code, *map(str, args)],
                                         read_denied=[self.denied] if denied is None else denied)

    def test_denied_read_and_proc_root_bypass_fail_while_workspace_is_writable(self):
        code = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "assert not p.exists(); assert not pathlib.Path('/proc/1/root'+str(p)).exists(); "
                "pathlib.Path('local-result').write_text('ok')")
        passed, detail = self.command(code, self.denied / "sentinel")
        self.assertTrue(passed, detail)
        self.assertEqual((self.ws / "local-result").read_text(), "ok")
        self.assertNotIn("private sentinel", detail)

    def test_host_aliases_home_and_siblings_are_absent(self):
        sibling = self.root / "sibling-clone"
        sibling.mkdir()
        (sibling / "private").write_text("private")
        paths = ["/mnt/wslg", "/mnt/c", "/run", "/run/docker.sock",
                 Path.home(), sibling, REPO / "evals/real-work/example-site-platform-221/checker",
                 REPO / "evals/real-work/example-site-platform-221/solution.patch"]
        for path in paths:
            with self.subTest(path=str(path)):
                passed, detail = self.command(
                    "import pathlib,sys; assert not pathlib.Path(sys.argv[1]).exists()", path)
                self.assertTrue(passed, detail)

    def test_host_unix_socket_cannot_be_connected(self):
        socket_path = self.root / "host.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(socket_path))
            listener.listen(1)
            code = ("import socket,sys; s=socket.socket(socket.AF_UNIX); "
                    "\ntry: s.connect(sys.argv[1])\n"
                    "except OSError: pass\nelse: raise AssertionError('host socket reachable')\n")
            passed, detail = self.command(code, socket_path)
        self.assertTrue(passed, detail)

    def test_denied_directory_and_file_in_allowed_bind_are_masked(self):
        allowed = self.root / "runtime"
        denied = allowed / "checkout"
        nested = denied / "outputs"
        nested.mkdir(parents=True)
        (nested / "secret").write_text("private")
        output = allowed / "transcript"
        output.write_text("private")
        alias = self.root / "runtime-alias"
        alias.symlink_to(allowed, target_is_directory=True)
        with mock.patch.object(commands, "SYSTEM_MOUNTS",
                               (*commands.SYSTEM_MOUNTS, str(allowed), str(alias))):
            code = ("from pathlib import Path; import sys; "
                    "assert all(not (Path(p)/'outputs/secret').exists() for p in sys.argv[1:3]); "
                    "\nfor p in sys.argv[3:]:\n"
                    " try: assert Path(p).read_bytes() == b''\n"
                    " except OSError: pass\n")
            passed, detail = self.command(code, denied, alias / "checkout", output,
                                         alias / "transcript", denied=[denied, nested, output])
        self.assertTrue(passed, detail)

    def test_absolute_outside_write_fails(self):
        outside = self.root / "unmounted-directory" / "outside"
        passed, detail = self.command("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('bad')", outside)
        self.assertFalse(passed)
        self.assertIn("command_nonzero", detail)
        self.assertFalse(outside.exists())

    def test_workspace_carve_out_under_denied_ancestor_keeps_dependencies(self):
        for directory in ("node_modules", ".fixture-python"):
            (self.ws / directory).mkdir()
            (self.ws / directory / "installed").write_text("ok")
        passed, detail = self.command("import pathlib; assert pathlib.Path('node_modules/installed').read_text()=='ok'; "
                                     "assert pathlib.Path('.fixture-python/installed').read_text()=='ok'; "
                                     "pathlib.Path('result').write_text('ok')", denied=[self.root])
        self.assertTrue(passed, detail)

    def test_hidden_checker_copied_but_original_fixture_and_workspace_denied(self):
        fixture = self.root / "fixture"
        seed = fixture / "seed"
        seed.mkdir(parents=True)
        (fixture / "solution.patch").write_text("private sentinel")
        checker = fixture / "checker"
        checker.mkdir()
        code = ("from pathlib import Path\n"
                f"assert not Path({str(fixture / 'solution.patch')!r}).exists()\n"
                f"assert not Path({str(checker / 'hidden.py')!r}).exists()\n"
                f"assert not Path({str(self.ws / 'original')!r}).exists()\n"
                "assert Path('hidden.py').is_file()\nPath('scoring-write').write_text('ok')\n")
        (checker / "hidden.py").write_text(code)
        (self.ws / "original").write_text("agent final")
        passed, detail = repo_tests.repo_tests(str(self.ws), [], overlay="checker", argv=["python3", "hidden.py"],
                                              fail_to_pass=["selected"], seed=str(seed), read_denied=[self.denied])
        self.assertTrue(passed, detail)
        self.assertFalse((self.ws / "scoring-write").exists())

    def test_actual_setup_failure_after_successful_probe_is_unavailable(self):
        prepare = commands._sandbox_prefix
        def failed_mount(*args, **kwargs):
            prefix, network = prepare(*args, **kwargs)
            return [*prefix[:-1], "--ro-bind", str(self.root / "missing-source"),
                    "/sandbox-missing-source", "--"], network
        with mock.patch.object(commands, "_sandbox_prefix", side_effect=failed_mount):
            passed, detail = self.command("pass")
        self.assertEqual((passed, detail), (False, "scorer_sandbox_unavailable exit=1"))
        self.assertNotIn(str(self.root), detail)

    def test_real_command_exit_one_remains_command_nonzero(self):
        passed, detail = self.command("raise SystemExit(1)")
        self.assertFalse(passed)
        self.assertIn("command_nonzero exit=1", detail)

    def test_bootstrap_does_not_import_workspace_sitecustomize(self):
        (self.ws / "sitecustomize.py").write_text(
            "from pathlib import Path\nPath('premature-startup-import').write_text('bad')\n")
        passed, detail = commands.command_succeeds(str(self.ws), [], ["sh", "-c", "exit 0"],
                                                  read_denied=[self.denied])
        self.assertTrue(passed, detail)
        self.assertFalse((self.ws / "premature-startup-import").exists())

    def test_real_node_modules_import_survives_sandbox(self):
        if commands._fixed_interpreter("node") is None:
            self.skipTest("system node unavailable for live module import")
        package = self.ws / "node_modules" / "fixture-package"
        package.mkdir(parents=True)
        (package / "index.js").write_text("module.exports = 42;\n")
        passed, detail = commands.command_succeeds(
            str(self.ws), [], ["node", "-e", "if (require('fixture-package') !== 42) process.exit(1)"],
            read_denied=[self.root])
        self.assertTrue(passed, detail)

    def test_copied_dependencies_work_but_original_dependency_symlink_is_hidden(self):
        if commands._fixed_interpreter("node") is None:
            self.skipTest("system node unavailable for live module import")
        original = self.denied / "node_modules"
        package = original / "fixture-package"
        package.mkdir(parents=True)
        (package / "index.js").write_text("module.exports = 42;\n")
        dependencies = self.ws / "node_modules"
        dependencies.symlink_to(original, target_is_directory=True)
        argv = ["node", "-e", "if (require('fixture-package') !== 42) process.exit(1)"]
        passed, detail = commands.command_succeeds(str(self.ws), [], argv,
                                                  read_denied=[self.root])
        self.assertFalse(passed)
        self.assertIn("command_nonzero", detail)
        dependencies.unlink()
        shutil.copytree(original, dependencies, symlinks=True)
        argv[2] += "; if (require('node:fs').existsSync(process.argv[1])) process.exit(1)"
        argv.append(str(original))
        passed, detail = commands.command_succeeds(str(self.ws), [], argv,
                                                  read_denied=[self.root])
        self.assertTrue(passed, detail)

    def test_real_fixture_python_import_survives_original_workspace_denial(self):
        fixture = self.root / "fixture"
        seed = fixture / "seed"
        seed.mkdir(parents=True)
        checker = fixture / "checker"
        checker.mkdir()
        (checker / "hidden.py").write_text("import fixture_package\nassert fixture_package.answer == 42\n")
        venv = self.ws / ".fixture-python"
        (venv / "bin").mkdir(parents=True)
        python = Path(commands._fixed_interpreter("python3"))
        # The selected interpreter's version decides its deterministic venv path.
        version = subprocess.run([str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=2, check=True).stdout.strip()
        site = venv / "lib" / f"python{version}" / "site-packages"
        site.mkdir(parents=True)
        (site / "fixture_package.py").write_text("answer = 42\n")
        (venv / "bin" / "python3").symlink_to(python)
        (venv / "pyvenv.cfg").write_text(f"home = {python.parent}\ninclude-system-site-packages = false\n")
        passed, detail = repo_tests.repo_tests(
            str(self.ws), [], overlay="checker", argv=["python3", "hidden.py"],
            fail_to_pass=["selected"], seed=str(seed), _python_deps=True)
        self.assertTrue(passed, detail)

    def test_agent_helper_cannot_read_fixture_solution_from_hidden_test(self):
        fixture = self.root / "fixture"
        seed = fixture / "seed"
        seed.mkdir(parents=True)
        (fixture / "solution.patch").write_text("private sentinel")
        checker = fixture / "checker"
        checker.mkdir()
        (checker / "hidden.py").write_text("import helper\nassert helper.answer() == 'private sentinel'\n")
        (self.ws / "helper.py").write_text("from pathlib import Path\ndef answer():\n"
                                          f"    return Path({str(fixture / 'solution.patch')!r}).read_text()\n")
        passed, detail = repo_tests.repo_tests(str(self.ws), [], overlay="checker", argv=["python3", "hidden.py"],
                                              fail_to_pass=["selected"], seed=str(seed))
        self.assertFalse(passed)
        self.assertIn("exit=1", detail)
        self.assertNotIn("private sentinel", detail)
        self.assertNotIn(str(self.root), detail)


class CiProvisioningTests(unittest.TestCase):
    """The runner image ships no bwrap; without it every command check fails closed."""

    def test_test_job_provisions_and_probes_bubblewrap_before_the_suite(self):
        import yaml
        workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["suite"]["steps"]
        names = [step.get("name") for step in steps]
        suite = names.index("Run tests")
        provision = names.index("Provide bubblewrap for the scorer sandbox")
        self.assertLess(provision, suite)
        step = steps[provision]
        self.assertEqual(step.get("if"), steps[suite].get("if"))
        script = step["run"].split()
        for token in ("bubblewrap", "apparmor_parser", "--unshare-net", "--unshare-pid"):
            self.assertIn(token, script)
        self.assertNotIn("${{", step["run"])


if __name__ == "__main__":
    unittest.main()
