"""Delivery receipts are best-effort observations, independent of hook decisions."""
import ast
import errno
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude/hooks/fleet-memory.sh"
BEGIN = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"
END = "<!-- END FLEET GUIDANCE -->"
WARNING = b"fleet-guidance: receipt unavailable\n"


def helper_source():
    # The literal heredoc delimiter is a packaging token, not a code-shape check.
    return HOOK.read_text().split("read -r -d '' RECEIPT_PY <<'PY' || :\n", 1)[1].split("\nPY\n", 1)[0]


class DeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.claude = self.home / ".claude"
        self.codex = self.home / ".codex"
        self.claude.mkdir(parents=True)
        self.codex.mkdir()
        self.payload = self.root / "payload.md"
        self.payload.write_bytes(b"# Fleet guidance\n\nReceipt canary.\n")
        self.pythonpath = self.root / "pythonpath"
        self.pythonpath.mkdir()
        self.clock = "import time\ntime.time = lambda: 1234\n"
        (self.pythonpath / "sitecustomize.py").write_text(self.clock)
        self.env = dict(os.environ, HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude),
                        CODEX_HOME=str(self.codex), FLEET_GUIDANCE_PAYLOAD=str(self.payload),
                        FLEET_GUIDANCE_SKIP="0", FLEET_GUIDANCE_RECEIPT="1", PYTHONPATH=str(self.pythonpath),
                        PYTHONDONTWRITEBYTECODE="1")
        self.namespace = {"__name__": "receipt_test"}
        exec(compile(helper_source(), str(HOOK), "exec"), self.namespace)

    def tearDown(self):
        self.temp.cleanup()

    def run_hook(self, args=(), enabled=True, injection=None):
        env = dict(self.env, FLEET_GUIDANCE_RECEIPT="1" if enabled else "0")
        if injection:
            pythonpath = self.root / "pythonpath"
            pythonpath.mkdir(exist_ok=True)
            (pythonpath / "sitecustomize.py").write_text(self.clock + injection)
            env["PYTHONPATH"] = str(pythonpath)
        return subprocess.run(["bash", str(HOOK), *args], env=env,
                              capture_output=True, check=False, cwd=self.root)

    def log(self, cloud=False):
        return (self.codex if cloud else self.claude) / "fleet-delivery.jsonl"

    def records(self, cloud=False):
        return [json.loads(line) for line in self.log(cloud).read_bytes().splitlines()]

    def reset(self):
        for directory in (self.claude, self.codex):
            for item in directory.iterdir():
                if item.is_dir() and not item.is_symlink():
                    item.rmdir()
                else:
                    item.unlink()

    def assert_identity(self, args=(), injection=None, warning=False):
        surfaces = (self.claude / "CLAUDE.md", self.codex / "AGENTS.md", self.codex / "AGENTS.override.md")
        before = {p: p.read_bytes() for p in surfaces if p.is_file()}
        baseline = self.run_hook(args, enabled=False)
        after = {p: p.read_bytes() for p in surfaces if p.is_file()}
        for p in after:
            if p not in before:
                p.unlink()
        for p, content in before.items():
            p.write_bytes(content)
        result = self.run_hook(args, injection=injection)
        self.assertEqual((baseline.stdout, baseline.returncode), (result.stdout, result.returncode))
        self.assertEqual(after, {p: p.read_bytes() for p in after})
        self.assertEqual(baseline.stderr + (WARNING if warning else b""), result.stderr)
        return result

    def test_installed_snapshot_and_current_identity(self):
        for args in ((), ("--codex-cloud",)):
            with self.subTest(args=args):
                self.reset()
                (self.claude / "CLAUDE.md").write_bytes(b"Operator prefix.\n")
                (self.codex / "AGENTS.md").write_bytes(b"Operator prefix.\n")
                self.assert_identity(args)
                data = self.records(bool(args))[-1]
                self.assertEqual("fleet-memory", data["hook"])
                self.assertEqual("delivery", data["load_reason"])
                self.assertEqual(1234, data["ts"])
                for delivery in data["deliveries"]:
                    path = self.home / ("." + delivery["file_path"])
                    raw = path.read_bytes()
                    block = raw[raw.index(BEGIN.encode()): raw.index(END.encode()) + len(END.encode()) + 1]
                    self.assertEqual(hashlib.sha256(block).hexdigest(), delivery["sha256"])
                    self.assertEqual(len(block), delivery["bytes"])
                    self.assertEqual("User", delivery["memory_type"])
                self.assert_identity(args)
                for delivery in self.records(bool(args))[-1]["deliveries"]:
                    self.assertEqual("current", delivery["outcome"])
                    self.assertIsNone(delivery["sha256"])
                    self.assertEqual(0, delivery["bytes"])

    def test_kept_newer_has_no_delivered_bytes(self):
        self.run_hook()
        for file in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
            content = file.read_text().replace("fleet-guidance-delivered: 0", "fleet-guidance-delivered: 99999999999999999999")
            content = content.replace(hashlib.sha256(self.payload.read_bytes()).hexdigest()[:8], "abcdef01")
            file.write_text(content)
        self.assert_identity()
        for delivery in self.records()[-1]["deliveries"]:
            self.assertEqual("kept", delivery["outcome"])
            self.assertIsNone(delivery["sha256"])
            self.assertEqual(0, delivery["bytes"])

    def test_stamp_refresh_snapshot(self):
        self.run_hook()
        for file in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
            file.write_text(file.read_text().replace("fleet-guidance-delivered: 0 -->\n", ""))
        # A standalone untracked payload has stamp 0; a deterministic stub
        # gives the same version a newer stamp, without using wall-clock time.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        git = bin_dir / "git"
        git.write_text('#!/bin/sh\ncase "$*" in *rev-parse*) echo true;; *status*) :;; *log*) echo 1;; esac\n')
        git.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.assert_identity()
        for delivery in self.records()[-1]["deliveries"]:
            self.assertEqual("current", delivery["outcome"])
            raw = (self.home / ("." + delivery["file_path"])).read_bytes()
            block = raw[raw.index(BEGIN.encode()): raw.index(END.encode()) + len(END.encode()) + 1]
            self.assertEqual(hashlib.sha256(block).hexdigest(), delivery["sha256"])
            self.assertEqual(len(block), delivery["bytes"])

    def test_skip_degraded_and_invalid_args_identity(self):
        for args in ((), ("--codex-cloud",)):
            for installed in (False, True):
                with self.subTest(args=args, installed=installed):
                    self.reset()
                    self.env["FLEET_GUIDANCE_SKIP"] = "0"
                    if installed:
                        self.run_hook(args)
                    self.env["FLEET_GUIDANCE_SKIP"] = "1"
                    self.assert_identity(args)
                    for record in self.records(bool(args))[-1]["deliveries"]:
                        self.assertEqual("skipped", record["outcome"])
                        self.assertIsNone(record["sha256"])
            self.env["FLEET_GUIDANCE_SKIP"] = "0"
            self.payload.unlink()
            self.assert_identity(args)
            self.assertEqual("degraded", self.records(bool(args))[-1]["deliveries"][0]["outcome"])
            self.payload.write_bytes(b"# Fleet guidance\n")
        self.assert_identity(("--invalid",))

    def test_workspace_identity(self):
        workspace = self.root / "workspace"
        child = workspace / "repo"
        (child / ".git").mkdir(parents=True)
        (child / "AGENTS.md").write_bytes(b"# Repo\n")
        self.assert_identity(("--workspace", str(workspace)))
        self.assertEqual("workspace", self.records()[-1]["mode"])

    def test_override_and_partial_delivery_identity(self):
        override = self.codex / "AGENTS.override.md"
        override.write_bytes(b"# Personal instructions\n")
        self.assert_identity(("--codex-cloud",))
        self.assertEqual("codex/AGENTS.override.md", self.records(True)[-1]["deliveries"][0]["file_path"])
        self.reset()
        (self.codex / "AGENTS.md").mkdir()
        self.assert_identity()
        deliveries = self.records()[-1]["deliveries"]
        self.assertEqual(["written", "degraded"], [item["outcome"] for item in deliveries])
        self.assertIsNotNone(deliveries[0]["sha256"])
        self.assertIsNone(deliveries[1]["sha256"])

    def test_observation_failure_identity_across_every_verdict(self):
        injection = '''import errno, os
real_open = os.open
def denied(path, flags, *args, **kwargs):
    if path == "fleet-delivery.jsonl": raise OSError(errno.ENOSPC, "full")
    return real_open(path, flags, *args, **kwargs)
os.open = denied
'''
        for args in ((), ("--codex-cloud",)):
            for state in ("installed", "current", "kept", "skipped", "degraded"):
                with self.subTest(args=args, state=state):
                    if args and state == "kept":
                        continue  # Cloud has no freshest-checkout arbitration.
                    self.reset()
                    self.env["FLEET_GUIDANCE_SKIP"] = "0"
                    self.payload.write_bytes(b"# Guidance\n")
                    if state in ("current", "kept", "skipped"):
                        self.run_hook(args, enabled=False)
                    if state == "kept":
                        for file in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
                            raw = file.read_text().replace("fleet-guidance-delivered: 0", "fleet-guidance-delivered: 99999999999999999999")
                            file.write_text(raw.replace(hashlib.sha256(self.payload.read_bytes()).hexdigest()[:8], "abcdef01"))
                    if state == "skipped": self.env["FLEET_GUIDANCE_SKIP"] = "1"
                    if state == "degraded": self.payload.unlink()
                    self.assert_identity(args, injection=injection, warning=True)
        self.env["FLEET_GUIDANCE_SKIP"] = "0"
        self.assert_identity(("--invalid",), injection=injection, warning=True)
        # Missing HOME must not change invalid-argument exit 2 at receipt time.
        self.env.pop("HOME")
        self.assert_identity(("--invalid",), injection=injection, warning=True)

    def test_missing_python_preserves_shell_and_cloud_verdicts(self):
        binary = self.root / "bin"
        binary.mkdir()
        python = binary / "python3"
        python.write_text("#!/bin/sh\nexit 127\n")
        python.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        for args in ((), ("--codex-cloud",)):
            with self.subTest(args=args):
                self.reset()
                self.assert_identity(args, warning=True)
                self.assertFalse(self.log(bool(args)).exists())

    def test_clock_helper_and_parent_permission_failures_are_isolated(self):
        injections = (
            "import time\ndef broken(): raise OSError('clock unavailable')\ntime.time = broken\n",
            "import os\nreal_open = os.open\ndef denied(path, flags, *args, **kwargs):\n"
            "    if path in ('.claude', '.codex'): raise PermissionError('denied')\n"
            "    return real_open(path, flags, *args, **kwargs)\nos.open = denied\n",
            "import ast, builtins\nreal_exec = builtins.exec\ndef broken(code, *args, **kwargs):\n"
            "    if isinstance(code, str) and any(isinstance(node, ast.FunctionDef) and node.name == 'receipt_write' for node in ast.walk(ast.parse(code))): raise ValueError('helper unavailable')\n"
            "    return real_exec(code, *args, **kwargs)\nbuiltins.exec = broken\n",
            "import hashlib\nreal_hash = hashlib.sha256\ndef broken(raw=b'', *args, **kwargs):\n"
            "    if raw.startswith(b'<!-- BEGIN FLEET GUIDANCE'): raise ValueError('hash unavailable')\n"
            "    return real_hash(raw, *args, **kwargs)\nhashlib.sha256 = broken\n",
        )
        for args in ((), ("--codex-cloud",)):
            for index, injection in enumerate(injections):
                with self.subTest(args=args, index=index):
                    self.reset()
                    # Normal Python -c has no explicit exec helper boundary.
                    self.assert_identity(args, injection=injection, warning=index != 2 or bool(args))

    def test_receipt_filesystem_refusals_preserve_delivery(self):
        for args in ((), ("--codex-cloud",)):
            for kind in ("symlink", "fifo", "hardlink", "directory", "parent_symlink"):
                with self.subTest(args=args, kind=kind):
                    self.reset()
                    log = self.log(bool(args))
                    victim = self.root / "victim"
                    victim.write_bytes(b"Untouched.\n")
                    if kind == "symlink":
                        log.symlink_to(victim)
                    elif kind == "fifo":
                        os.mkfifo(log)
                    elif kind == "hardlink":
                        os.link(victim, log)
                    elif kind == "directory":
                        log.mkdir()
                    else:
                        target = self.codex if args else self.claude
                        moved = self.root / ("moved-codex" if args else "moved-claude")
                        target.rename(moved)
                        target.symlink_to(moved, target_is_directory=True)
                    self.assert_identity(args, warning=True)
                    self.assertEqual(b"Untouched.\n", victim.read_bytes())
                    if kind == "parent_symlink":
                        target.unlink()
                        moved.rename(target)
                    self.reset()

    def test_syscall_failures_preserve_output_exit_and_single_warning(self):
        for args in ((), ("--codex-cloud",)):
            for failure in ("unwritable", "full", "short", "snapshot"):
                with self.subTest(args=args, failure=failure):
                    self.reset()
                    injection = '''import errno, os, sys
original_open, original_write = os.open, os.write
fds = set()
def guarded_open(path, flags, *args, **kwargs):
    if path == "fleet-delivery.jsonl" and FAILURE == "unwritable":
        raise PermissionError(errno.EACCES, "denied")
    fd = original_open(path, flags, *args, **kwargs)
    if path == "fleet-delivery.jsonl": fds.add(fd)
    return fd
def guarded_write(fd, data):
    if fd in fds:
        if FAILURE == "full": raise OSError(errno.ENOSPC, "full")
        if FAILURE == "short": return original_write(fd, data[:1])
    return original_write(fd, data)
os.open, os.write = guarded_open, guarded_write
if FAILURE == "snapshot" and len(sys.argv) > 1 and sys.argv[1] == "snapshot":
    os._exit(1)
'''.replace("FAILURE", repr(failure))
                    # Cloud hashes its in-memory block and has no snapshot command.
                    self.assert_identity(args, injection=injection, warning=failure != "snapshot" or not args)
                    if failure == "snapshot" and not args:
                        self.assertFalse(self.log().exists())
        self.payload.unlink()
        self.assert_identity(("--codex-cloud",), injection=injection.replace("'snapshot'", "'full'"), warning=True)

    def test_cap_skips_without_rewriting_and_no_personal_fields(self):
        self.log().write_bytes(b"x" * self.namespace["RECEIPT_LIMIT"])
        self.assert_identity()
        self.assertEqual(b"x" * self.namespace["RECEIPT_LIMIT"], self.log().read_bytes())
        self.log().unlink()
        self.run_hook(enabled=False)
        self.assertFalse(self.log().exists())
        env = dict(self.env)
        env.pop("FLEET_GUIDANCE_RECEIPT")
        result = subprocess.run(["bash", str(HOOK)], env=env, cwd=self.root,
                                capture_output=True, check=False)
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"", result.stderr)
        self.assertTrue(self.log().exists())
        line = self.log().read_bytes()
        self.assertNotIn(str(self.home).encode(), line)
        self.assertNotIn(b"Receipt canary", line)
        self.assertLess(len(line), os.pathconf(self.claude, "PC_PIPE_BUF"))

    def test_concurrent_processes_append_whole_lines(self):
        self.run_hook()
        self.log().unlink()
        count = 24
        processes = [subprocess.Popen(["bash", str(HOOK)], env=self.env, cwd=self.root,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(count)]
        for process in processes:
            stdout, stderr = process.communicate()
            self.assertEqual(0, process.returncode)
            self.assertEqual(b"", stderr)
            self.assertIn(b"fleet-guidance: current", stdout)
        lines = self.log().read_bytes().splitlines(keepends=True)
        self.assertEqual(count, len(lines))
        for line in lines:
            self.assertTrue(line.endswith(b"\n"))
            self.assertEqual(2, len(json.loads(line)["deliveries"]))

    def test_append_one_syscall_short_write_not_retried(self):
        write = self.namespace["receipt_write"]
        original_write = os.write
        calls = []
        def short(fd, raw):
            calls.append(raw)
            return original_write(fd, raw[:1])
        with mock.patch.object(os, "write", side_effect=short):
            with self.assertRaises(OSError):
                write(str(self.claude), "hook", [])
        self.assertEqual(1, len(calls))
        self.assertEqual(b"{", self.log().read_bytes())

    def test_leaf_swap_after_directory_open_never_follows_or_blocks(self):
        original_open = os.open
        victim = self.root / "victim"
        victim.write_bytes(b"Untouched.\n")
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                def swap(path, flags, *args, **kwargs):
                    if path == "fleet-delivery.jsonl":
                        if kind == "fifo": self.assertTrue(flags & os.O_NONBLOCK)
                        if kind == "symlink": self.log().symlink_to(victim)
                        else: os.mkfifo(self.log())
                    return original_open(path, flags, *args, **kwargs)
                with mock.patch.object(os, "open", side_effect=swap):
                    with self.assertRaises(OSError):
                        self.namespace["receipt_write"](str(self.claude), "hook", [])
                self.assertEqual(b"Untouched.\n", victim.read_bytes())
                self.log().unlink()

    def test_snapshot_refuses_replaced_symlink_fifo_and_hardlink(self):
        source = self.root / "assembled"
        victim = self.root / "victim"
        victim.write_bytes((BEGIN + "\nPayload\n" + END + "\n").encode())
        real_open = os.open
        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                source.write_bytes(victim.read_bytes())
                def swap(path, flags, *args, **kwargs):
                    if str(path) == str(source):
                        if kind == "fifo": self.assertTrue(flags & os.O_NONBLOCK)
                        source.unlink()
                        if kind == "symlink": source.symlink_to(victim)
                        elif kind == "fifo": os.mkfifo(source)
                        else: os.link(victim, source)
                    return real_open(path, flags, *args, **kwargs)
                with mock.patch.object(os, "open", side_effect=swap):
                    with self.assertRaises(OSError):
                        self.namespace["receipt_snapshot"](str(source), BEGIN, END)
                source.unlink()

    def test_snapshot_is_bounded_to_descriptor_size(self):
        source = self.root / "assembled"
        block = (BEGIN + "\nPayload\n" + END + "\n").encode()
        source.write_bytes(block)
        original_read = os.read
        def grow(fd, count):
            self.assertLessEqual(count, len(block))
            with source.open("ab") as stream:
                stream.write(b"Extra personal content.\n")
            return original_read(fd, count)
        with mock.patch.object(os, "read", side_effect=grow):
            digest, count = self.namespace["receipt_snapshot"](str(source), BEGIN, END)
        self.assertEqual((hashlib.sha256(block).hexdigest(), len(block)), (digest, count))

    def test_snapshot_descriptor_guards_run_before_read(self):
        source = self.root / "assembled"
        block = (BEGIN + "\nPayload\n" + END + "\n").encode()
        source.write_bytes(block)
        snapshot = self.namespace["receipt_snapshot"]
        real_fstat, real_read, real_write = os.fstat, os.read, os.write
        for label, field, value in (
            ("nonregular", 0, stat.S_IFIFO | 0o600),
            ("multiple_links", 3, 2),
            ("foreign_owner", 4, os.geteuid() + 1),
            ("oversized", 6, 4194305),
        ):
            with self.subTest(label=label):
                def changed(fd):
                    fields = list(real_fstat(fd))
                    fields[field] = value
                    return os.stat_result(fields)
                with mock.patch.object(os, "fstat", side_effect=changed), \
                        mock.patch.object(os, "read", wraps=real_read) as read:
                    with self.assertRaises(OSError):
                        snapshot(str(source), BEGIN, END)
                    read.assert_not_called()
                if label != "oversized":
                    # The writer must refuse an already-open unsafe descriptor,
                    # even when the open itself succeeded on a regular file.
                    with mock.patch.object(os, "fstat", side_effect=changed), \
                            mock.patch.object(os, "write", wraps=real_write) as write:
                        with self.assertRaises(OSError):
                            self.namespace["receipt_write"](str(self.claude), "hook", [])
                        write.assert_not_called()
                    self.assertEqual(b"", self.log().read_bytes())
                    self.log().unlink()
        with mock.patch.object(os, "read", return_value=b"") as read:
            with self.assertRaises(OSError):
                snapshot(str(source), BEGIN, END)
            read.assert_called_once()
        self.assertEqual((hashlib.sha256(block).hexdigest(), len(block)),
                         snapshot(str(source), BEGIN, END))

    def test_ambiguous_snapshot_skips_receipt_without_changing_delivery(self):
        for marker in (BEGIN, END):
            with self.subTest(marker=marker):
                self.reset()
                self.payload.write_bytes(("# Guidance\n" + marker + "\n").encode())
                self.assert_identity(warning=True)
                self.assertFalse(self.log().exists())

    def test_warning_failure_does_not_change_cloud_or_normal_exit(self):
        injection = '''import errno, os
real_open = os.open
def denied(path, flags, *args, **kwargs):
    if path == "fleet-delivery.jsonl": raise OSError(errno.ENOSPC, "full")
    return real_open(path, flags, *args, **kwargs)
os.open = denied
'''
        for args in ((), ("--codex-cloud",)):
            self.reset()
            baseline = self.run_hook(args, enabled=False)
            self.reset()
            pythonpath = self.root / "pythonpath"
            pythonpath.mkdir(exist_ok=True)
            (pythonpath / "sitecustomize.py").write_text(self.clock + injection)
            env = dict(self.env, PYTHONPATH=str(pythonpath))
            # /dev/full rejects the warning itself, not the guidance write.
            with open("/dev/full", "wb") as failed_stderr:
                result = subprocess.run(["bash", str(HOOK), *args], env=env,
                                        stdout=subprocess.PIPE, stderr=failed_stderr, cwd=self.root)
            self.assertEqual((baseline.stdout, baseline.returncode), (result.stdout, result.returncode))
            self.reset()
            result = subprocess.run(["bash", str(HOOK), *args], env=env,
                                    stdout=subprocess.PIPE, preexec_fn=lambda: os.close(2), cwd=self.root)
            self.assertEqual((baseline.stdout, baseline.returncode), (result.stdout, result.returncode))

    def test_parent_swap_keeps_validated_directory_descriptor(self):
        original_open = os.open
        moved = self.root / "moved"
        outside = self.root / "outside"
        outside.mkdir()
        def swap(path, flags, *args, **kwargs):
            if path == "fleet-delivery.jsonl":
                self.claude.rename(moved)
                self.claude.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=swap):
            self.namespace["receipt_write"](str(self.claude), "hook", [])
        self.assertTrue((moved / "fleet-delivery.jsonl").is_file())
        self.assertEqual([], list(outside.iterdir()))

    def test_descriptor_owner_and_pipe_buf_refusals(self):
        real_fstat = os.fstat
        def foreign(fd):
            info = real_fstat(fd)
            fields = list(info)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        for patch in (mock.patch.object(os, "fstat", side_effect=foreign),
                      mock.patch.object(os, "fpathconf", return_value=10)):
            with patch:
                with self.assertRaises(OSError):
                    self.namespace["receipt_write"](str(self.claude), "hook", [])
            self.assertEqual(b"", self.log().read_bytes())

    def test_write_uses_append_without_truncation(self):
        original_open = os.open
        flags_seen = []
        def inspect(path, flags, *args, **kwargs):
            if path == "fleet-delivery.jsonl": flags_seen.append(flags)
            return original_open(path, flags, *args, **kwargs)
        self.log().write_bytes(b"previous\n")
        with mock.patch.object(os, "open", side_effect=inspect):
            self.namespace["receipt_write"](str(self.claude), "hook", [])
        self.assertTrue(self.log().read_bytes().startswith(b"previous\n"))
        self.assertEqual(1, len(flags_seen))
        self.assertTrue(flags_seen[0] & os.O_APPEND)
        self.assertFalse(flags_seen[0] & os.O_TRUNC)
        # Python code-shape checks inspect the actual AST, never source lines.
        tree = ast.parse(helper_source())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "receipt_write")
        writes = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "os" and node.func.attr == "write"]
        self.assertEqual(1, len(writes))
