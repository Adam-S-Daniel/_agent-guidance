"""Delivery receipts are best-effort observations, independent of hook decisions."""
import ast
import concurrent.futures
import errno
import hashlib
import json
import os
import signal
import re
import shlex
import shutil
import threading
import time
import stat
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude/hooks/fleet-memory.sh"
BEGIN = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"
END = "<!-- END FLEET GUIDANCE -->"
def bounded_poll(predicate, timeout=10):
    """Poll a completion condition; the deadline only bounds a broken fixture."""
    deadline = time.monotonic() + timeout
    tick = threading.Event()
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("owned detached writer did not complete")
        tick.wait(0.001)


def helper_source():
    # The literal heredoc delimiter is a packaging token, not a code-shape check.
    return HOOK.read_text().split(": <<'FLEET_RECEIPT_PY'\n", 1)[1].split("\nFLEET_RECEIPT_PY\n", 1)[0]


def timed_run(command, *, capture_output=False, check=False, **kwargs):
    """Own the subprocess group so a timeout reaps the hook and its worker."""
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with subprocess.Popen(command, start_new_session=True, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
            raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


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
        self.bin = self.root / "receipt-bin"
        self.bin.mkdir()
        self.sentinel_bin = self.root / "sentinel-bin"
        self.sentinel_bin.mkdir()
        self.sentinel = self.sentinel_bin / "claude"
        self.sentinel.write_text("#!/bin/sh\nprintf called >> \"$RECEIPT_TEST_CLAUDE_CALLS\"\nexit 97\n")
        self.sentinel.chmod(0o755)
        self.calls = self.root / "claude-calls"
        self.python_shim(self.bin / "python3")
        self.env = dict(os.environ, HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude),
                        CODEX_HOME=str(self.codex), FLEET_GUIDANCE_PAYLOAD=str(self.payload),
                        FLEET_GUIDANCE_SKIP="0", FLEET_GUIDANCE_RECEIPT="1", PYTHONPATH=str(self.pythonpath),
                        PYTHONDONTWRITEBYTECODE="1", RECEIPT_TEST_CLAUDE_CALLS=str(self.calls),
                        TMPDIR=str(self.root),
                        PATH=str(self.sentinel_bin) + os.pathsep + str(self.bin) + os.pathsep + os.environ["PATH"])
        self.namespace = {"__name__": "receipt_test"}
        exec(compile(helper_source(), str(HOOK), "exec"), self.namespace)

    def tearDown(self):
        self.assertFalse(self.calls.exists(), "a harness reached the Claude sentinel")
        self.temp.cleanup()

    def python_shim(self, path, body=None, count=None):
        script = "#!/bin/sh\n"
        script += "trap 'printf \"done\\n\" >> \"$RECEIPT_TEST_COMPLETION\"' 0\n"
        if count:
            script += "printf '1\\n' >> " + shlex.quote(str(count)) + "\n"
        script += body or shlex.quote(sys.executable) + ' "$@"\n'
        path.write_text(script)
        path.chmod(0o755)

    def run_hook(self, args=(), enabled=True, injection=None, wait=True, hook=HOOK, bash="bash", **process_options):
        invocation = Path(tempfile.mkdtemp(prefix="invocation-", dir=self.root))
        pythonpath = invocation / "pythonpath"
        pythonpath.mkdir()
        (pythonpath / "sitecustomize.py").write_text(self.clock + (injection or ""))
        completion = invocation / "complete"
        env = dict(self.env, FLEET_GUIDANCE_RECEIPT="1" if enabled else "0",
                   PYTHONPATH=str(pythonpath), RECEIPT_TEST_COMPLETION=str(completion))
        if enabled is None:
            env.pop("FLEET_GUIDANCE_RECEIPT")
        # Keep a sentinel first even when a case prepends its own executable shim.
        env["PATH"] = str(self.sentinel_bin) + os.pathsep + env["PATH"]
        result = timed_run([bash, str(hook), *args], env=env,
                          capture_output=not process_options, check=False, cwd=self.root, **process_options)
        cloud = bool(args and args[0] == "--codex-cloud")
        expected = int(cloud) + int(enabled is not False and hook == HOOK)
        result.receipt_completion = completion
        result.receipt_expected = expected
        if wait:
            self.wait_receipts(result)
        return result

    def wait_receipts(self, result):
        bounded_poll(lambda: result.receipt_expected == 0 or
                     (result.receipt_completion.exists() and
                      len(result.receipt_completion.read_bytes().splitlines()) >= result.receipt_expected))

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

    def assert_identity(self, args=(), injection=None):
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
        self.assertEqual(baseline.stderr, result.stderr)
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
                    self.assert_identity(args, injection=injection)
        self.env["FLEET_GUIDANCE_SKIP"] = "0"
        self.assert_identity(("--invalid",), injection=injection)
        # Missing HOME must not change invalid-argument exit 2 at receipt time.
        self.env.pop("HOME")
        self.assert_identity(("--invalid",), injection=injection)

    def test_missing_python_preserves_shell_and_cloud_verdicts(self):
        self.python_shim(self.bin / "python3", body="exit 127\n")
        for args in ((), ("--codex-cloud",)):
            with self.subTest(args=args):
                self.reset()
                self.assert_identity(args)
                self.assertFalse(self.log(bool(args)).exists())

    def test_clock_helper_and_parent_permission_failures_are_isolated(self):
        injections = (
            "import time\ndef broken(): raise OSError('clock unavailable')\ntime.time = broken\n",
            "import os\nreal_open = os.open\ndef denied(path, flags, *args, **kwargs):\n"
            "    if path in ('.claude', '.codex'): raise PermissionError('denied')\n"
            "    return real_open(path, flags, *args, **kwargs)\nos.open = denied\n",
            "import builtins\nreal_import = builtins.__import__\ndef broken(name, *args, **kwargs):\n"
            "    if name == 'json': raise ImportError('helper unavailable')\n"
            "    return real_import(name, *args, **kwargs)\nbuiltins.__import__ = broken\n",
            "import hashlib\nreal_hash = hashlib.sha256\ndef broken(raw=b'', *args, **kwargs):\n"
            "    if raw.startswith(b'<!-- BEGIN FLEET GUIDANCE'): raise ValueError('hash unavailable')\n"
            "    return real_hash(raw, *args, **kwargs)\nhashlib.sha256 = broken\n",
        )
        for args in ((), ("--codex-cloud",)):
            for index, injection in enumerate(injections):
                with self.subTest(args=args, index=index):
                    self.reset()
                    self.assert_identity(args, injection=injection)
                    if index == 2:
                        self.assertFalse(self.log(bool(args)).exists())

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
                    self.assert_identity(args)
                    self.assertEqual(b"Untouched.\n", victim.read_bytes())
                    if kind == "parent_symlink":
                        target.unlink()
                        moved.rename(target)
                    self.reset()

    def test_syscall_failures_preserve_output_exit_silently(self):
        for args in ((), ("--codex-cloud",)):
            for failure in ("unwritable", "full", "short", "snapshot"):
                with self.subTest(args=args, failure=failure):
                    self.reset()
                    injection = '''import errno, os
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
if FAILURE == "snapshot":
    real_read = os.read
    def failed_read(fd, count): raise OSError("snapshot unavailable")
    os.read = failed_read
'''.replace("FAILURE", repr(failure))
                    # Cloud hashes its in-memory block and has no snapshot command.
                    self.assert_identity(args, injection=injection)
                    if failure == "snapshot" and not args:
                        self.assertFalse(self.log().exists())
        self.payload.unlink()
        self.assert_identity(("--codex-cloud",), injection=injection.replace("'snapshot'", "'full'"))

    def test_cap_skips_without_rewriting_and_no_personal_fields(self):
        self.log().write_bytes(b"x" * self.namespace["RECEIPT_LIMIT"])
        self.assert_identity()
        self.assertEqual(b"x" * self.namespace["RECEIPT_LIMIT"], self.log().read_bytes())
        self.log().unlink()
        self.run_hook(enabled=False)
        self.assertFalse(self.log().exists())
        result = self.run_hook(enabled=None)
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(lambda _: self.run_hook(), range(count)))
        for result in results:
            self.assertEqual(0, result.returncode)
            self.assertEqual(b"", result.stderr)
            self.assertIn(b"fleet-guidance: current", result.stdout)
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
                self.assert_identity()
                self.assertFalse(self.log().exists())

    def test_unwritable_stderr_does_not_change_cloud_or_normal_exit(self):
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
            # A failed caller stderr never interferes with guidance delivery.
            with open("/dev/full", "wb") as failed_stderr:
                result = self.run_hook(args, injection=injection,
                                       stdout=subprocess.PIPE, stderr=failed_stderr)
            self.assertEqual((baseline.stdout, baseline.returncode), (result.stdout, result.returncode))
            self.reset()
            result = self.run_hook(args, injection=injection,
                                   stdout=subprocess.PIPE, preexec_fn=lambda: os.close(2))
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

    def test_unsupported_primitives_silently_skip_all_modes(self):
        for args in ((), ("--workspace", str(self.root)), ("--codex-cloud",)):
            for primitive in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "geteuid", "fpathconf",
                              "supports_dir_fd", "supports_follow_symlinks"):
                with self.subTest(args=args, primitive=primitive):
                    self.reset()
                    injection = (f"import os\nos.{primitive} = set()\n" if primitive.startswith("supports_")
                                 else f"import os\ndel os.{primitive}\n")
                    self.assert_identity(args, injection=injection)
                    self.assertFalse(self.log(bool(args and args[0] == "--codex-cloud")).exists())
        # Windows may expose functions but lack descriptor-relative calls.
        for args in ((), ("--workspace", str(self.root)), ("--codex-cloud",)):
            self.reset()
            self.assert_identity(args, injection="import os\nos.supports_dir_fd = set()\n")
            self.assertFalse(self.log(bool(args and args[0] == "--codex-cloud")).exists())

    def test_unimplemented_platform_syscalls_silently_skip(self):
        for args in ((), ("--workspace", str(self.root)), ("--codex-cloud",)):
            for primitive in ("open", "stat", "fpathconf"):
                with self.subTest(args=args, primitive=primitive):
                    self.reset()
                    cloud = bool(args and args[0] == "--codex-cloud")
                    directory = self.codex if cloud else self.claude
                    moved = self.root / "actual-config"
                    if primitive == "stat":
                        directory.rename(moved)
                        directory.symlink_to(moved, target_is_directory=True)
                    injection = f"import os\noriginal = os.{primitive}\n" \
                        "def unsupported(*args, **kwargs):\n" \
                        f"    if {primitive == 'fpathconf'!r} or 'dir_fd' in kwargs: raise NotImplementedError()\n" \
                        "    return original(*args, **kwargs)\n" \
                        f"os.{primitive} = unsupported\n"
                    try:
                        self.assert_identity(args, injection=injection)
                        self.assertFalse(self.log(cloud).exists())
                    finally:
                        if primitive == "stat":
                            directory.unlink()
                            moved.rename(directory)
        # All optional clock failures are silent observations.
        self.reset()
        self.assert_identity(injection="import time\ndef fail(): raise NotImplementedError()\ntime.time = fail\n",
                             )

    def test_symlinked_parent_silently_skips_before_snapshot_and_digest(self):
        for args in ((), ("--workspace", str(self.root)), ("--codex-cloud",)):
            self.reset()
            cloud = bool(args and args[0] == "--codex-cloud")
            directory = self.codex if cloud else self.claude
            moved = self.root / "actual-config"
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
            injection = "import hashlib\nreal_hash = hashlib.sha256\n" \
                "def fail(raw=b'', *args, **kwargs):\n" \
                "    if raw.startswith(b'<!-- BEGIN FLEET GUIDANCE'): raise ValueError('receipt hash')\n" \
                "    return real_hash(raw, *args, **kwargs)\nhashlib.sha256 = fail\n"
            try:
                self.assert_identity(args, injection=injection)
                self.assertFalse((moved / "fleet-delivery.jsonl").exists())
            finally:
                directory.unlink()
                moved.rename(directory)

    def test_stamp_only_failed_snapshot_skips_entire_receipt(self):
        self.run_hook(enabled=False)
        for path in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
            path.write_bytes(path.read_bytes().replace(
                b"<!-- fleet-guidance-delivered: 0 -->\n", b""))
        # A deterministic git shim supplies a later stamp without a real repo.
        binary = self.root / "bin"
        binary.mkdir()
        git = binary / "git"
        git.write_text("#!/bin/sh\ncase \"$*\" in *rev-parse*) echo true;; *log*) echo 123;; esac\n")
        git.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        before = (self.claude / "CLAUDE.md").read_bytes()
        self.assert_identity(injection="import os\ndef fail(*args): raise OSError('snapshot')\nos.read = fail\n",
                             )
        after = (self.claude / "CLAUDE.md").read_bytes()
        self.assertNotEqual(before, after)
        self.assertIn(b"<!-- fleet-guidance-delivered: 123 -->", after)
        self.assertFalse(self.log().exists())

    def counting_python(self, body=None):
        count = self.root / "python-count"
        self.python_shim(self.bin / "python3", body=body, count=count)
        return count

    def test_one_receipt_python_launch_per_invocation(self):
        count = self.counting_python()
        git = self.bin / "git"
        git.write_text('#!/bin/sh\ncase "$*" in *rev-parse*) echo true;; *log*) echo 123;; esac\n')
        git.chmod(0o755)
        for args in ((), ("--workspace", str(self.root)), ("--codex-cloud",)):
            for state in ("fresh", "current", "stamp", "failure", "optout"):
                with self.subTest(args=args, state=state):
                    self.reset()
                    self.env["FLEET_GUIDANCE_SKIP"] = "0"
                    if state in ("current", "stamp"):
                        self.run_hook(args, enabled=False)
                    if state == "stamp":
                        for path in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
                            if path.is_file():
                                path.write_bytes(path.read_bytes().replace(
                                    b"<!-- fleet-guidance-delivered: 123 -->\n", b""))
                    count.unlink(missing_ok=True)
                    result = self.run_hook(args, enabled=state != "optout",
                                           injection="import os\nos._exit(1)\n" if state == "failure" else None)
                    if state == "stamp" and args != ("--codex-cloud",):
                        self.assertIn(b"<!-- fleet-guidance-delivered: 123 -->",
                                      (self.claude / "CLAUDE.md").read_bytes())
                        self.assertIsNotNone(self.records()[-1]["deliveries"][0]["sha256"])
                    expected = int(state != "optout") + int(args == ("--codex-cloud",))
                    self.assertEqual(expected, len(count.read_text().splitlines()) if count.exists() else 0)
                    self.assertEqual(b"", result.stderr)
        # Cloud delivery and observation each attempt their own interpreter.
        self.reset()
        count = self.counting_python("exit 127\n")
        count.unlink(missing_ok=True)
        self.assert_identity(("--codex-cloud",))
        # assert_identity invokes a receipt-disabled baseline and enabled run.
        self.assertEqual(3, len(count.read_text().splitlines()))

    def test_detached_worker_early_exit_preserves_delivery(self):
        self.counting_python("exit 1\n")
        self.assert_identity()
        self.assertFalse(self.log().exists())

    def test_detached_worker_rejects_invalid_and_oversized_metadata(self):
        valid = ("claude/CLAUDE.md", "current", "", "", "0")
        cases = (
            ("unknown", valid),
            ("hook", valid[:-1]),
            ("hook", ("example.com/operator", *valid[1:])),
            ("hook", (valid[0], "unknown", *valid[2:])),
            ("hook", (*valid[:2], "example.net/snapshot", "", "0")),
            ("hook", (*valid[:3], "x" * 4096, "1")),
            ("hook", (*valid[:3], "invalid", "1")),
            ("hook", (*valid[:4], "-1")),
            ("hook", (*valid[:4], "4194305")),
            ("hook", (*valid[:3], "0" * 64, "0")),
        )
        for mode, metadata in cases:
            with self.subTest(mode=mode, metadata_size=sum(map(len, metadata))):
                self.namespace["receipt_worker"](str(self.claude), mode, BEGIN, END, "", *metadata)
                self.assertFalse(self.log().exists())

    def test_detached_snapshots_are_private_managed_only_and_removed(self):
        for path in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
            path.write_bytes(b"Operator prefix.\nOperator suffix.\n")
        observed = self.root / "snapshot-observation"
        injection = ("import json, os, stat, sys\n"
                     "if sys.argv[0] == '-c':\n"
                     "    directory = sys.argv[6]\n"
                     "    metadata = {'directory': directory, 'mode': stat.S_IMODE(os.stat(directory).st_mode),\n"
                     "                'files': [open(os.path.join(directory, name), 'rb').read().decode()\n"
                     "                          for name in os.listdir(directory)]}\n"
                     f"    with open({str(observed)!r}, 'w') as stream: json.dump(metadata, stream)\n")
        self.run_hook(injection=injection)
        data = json.loads(observed.read_text())
        self.assertEqual(0o700, data["mode"])
        self.assertEqual(2, len(data["files"]))
        for content in data["files"]:
            self.assertTrue(content.startswith(BEGIN + "\n"))
            self.assertTrue(content.endswith(END + "\n"))
            self.assertNotIn("Operator prefix", content)
            self.assertNotIn("Operator suffix", content)
        self.assertFalse(Path(data["directory"]).exists())

    def test_snapshot_preparation_and_write_faults_preserve_delivery(self):
        mktemp = shutil.which("mktemp")
        self.assertIsNotNone(mktemp)
        shim = self.bin / "mktemp"
        for failure in ("prepare", "fresh_write", "stamp_write"):
            with self.subTest(failure=failure):
                self.reset()
                shim.unlink(missing_ok=True)
                if failure == "stamp_write":
                    self.run_hook(enabled=False)
                    for path in (self.claude / "CLAUDE.md", self.codex / "AGENTS.md"):
                        path.write_bytes(path.read_bytes().replace(b"<!-- fleet-guidance-delivered: 0 -->\n", b""))
                    git = self.bin / "git"
                    git.write_text('#!/bin/sh\ncase "$*" in *rev-parse*) echo true;; *log*) echo 123;; esac\n')
                    git.chmod(0o755)
                action = "exit 1\n" if failure == "prepare" else (
                    "directory=$(" + shlex.quote(mktemp) + ' "$@") || exit 1\n'
                    'ln -s /dev/full "$directory/0" || exit 1\n'
                    'printf "%s\\n" "$directory"\nexit 0\n')
                shim.write_text('#!/bin/sh\nif [ "$1" = -d ]; then\n' + action +
                                "fi\nexec " + shlex.quote(mktemp) + ' "$@"\n')
                shim.chmod(0o755)
                self.assert_identity()
                self.assertFalse(self.log().exists())
                if failure == "stamp_write":
                    self.assertIn(b"<!-- fleet-guidance-delivered: 123 -->", (self.claude / "CLAUDE.md").read_bytes())

    def test_worker_refuses_unsafe_snapshot_directory_before_read(self):
        snapshots = self.root / "private-snapshots"
        snapshots.mkdir(mode=0o700)
        source = snapshots / "0"
        source.write_bytes((BEGIN + "\nPayload\n" + END + "\n").encode())
        for kind in ("symlink", "mode", "foreign_owner"):
            with self.subTest(kind=kind):
                path = snapshots
                if kind == "symlink":
                    path = self.root / "linked-snapshots"
                    path.symlink_to(snapshots, target_is_directory=True)
                if kind == "mode":
                    snapshots.chmod(0o755)
                real_fstat = os.fstat
                def inspect(fd):
                    info = real_fstat(fd)
                    if kind == "foreign_owner" and stat.S_ISDIR(info.st_mode) and info.st_ino == snapshots.stat().st_ino:
                        fields = list(info)
                        fields[4] = os.geteuid() + 1
                        return os.stat_result(fields)
                    return info
                reader = mock.Mock(wraps=self.namespace["receipt_snapshot"])
                with mock.patch.dict(self.namespace, receipt_snapshot=reader), \
                        mock.patch.object(os, "fstat", side_effect=inspect):
                    self.namespace["receipt_worker"](str(self.claude), "hook", BEGIN, END, str(path),
                                                     "claude/CLAUDE.md", "written", str(path / "0"), "", "0")
                reader.assert_not_called()
                self.assertFalse(self.log().exists())
                self.assertTrue(source.exists())
                if kind == "symlink":
                    path.unlink()
                snapshots.chmod(0o700)

    def test_hook_returns_before_receipt_writer_is_released(self):
        gate = self.root / "release"
        ready = self.root / "ready"
        os.mkfifo(gate)
        injection = ("import sys, os\n"
                     "if sys.argv[0] == '-c':\n"
                     f"    open({str(ready)!r}, 'w').close()\n"
                     f"    with open({str(gate)!r}, 'rb') as stream: stream.read(1)\n")
        for args in ((), ("--codex-cloud",), ("--workspace", str(self.root))):
            with self.subTest(args=args):
                self.reset()
                ready.unlink(missing_ok=True)
                baseline = self.run_hook(args, enabled=False)
                self.reset()
                result = None
                try:
                    result = self.run_hook(args, injection=injection, wait=False)
                    # The hook is already reaped while its receipt is blocked.
                    self.assertEqual((baseline.stdout, baseline.stderr, baseline.returncode),
                                     (result.stdout, result.stderr, result.returncode))
                    bounded_poll(ready.exists)
                    self.assertFalse(self.log(args == ("--codex-cloud",)).exists())
                    completed = (len(result.receipt_completion.read_bytes().splitlines())
                                 if result.receipt_completion.exists() else 0)
                    self.assertLess(completed, result.receipt_expected)
                finally:
                    if result is not None:
                        def release():
                            try:
                                fd = os.open(gate, os.O_WRONLY | os.O_NONBLOCK)
                            except OSError as exc:
                                if exc.errno == errno.ENXIO:
                                    return False
                                raise
                            try:
                                os.write(fd, b"1")
                            finally:
                                os.close(fd)
                            return True
                        # The ready marker precedes open(); poll for its reader.
                        bounded_poll(release)
                        self.wait_receipts(result)
                self.assertTrue(self.log(args == ("--codex-cloud",)).exists())

    def test_hook_has_no_forbidden_bash4_lexical_tokens(self):
        source = HOOK.read_text()
        forbidden = {
            "coproc": r"\bcoproc\b",
            "named descriptor": r"(?:exec\s+)?\{[A-Za-z_][A-Za-z0-9_]*\}\s*[<>]",
            "case fallthrough": r";;&|;&",
            "case expansion": r"\$\{[^}]*[,^]{2}[^}]*\}",
            "associative array": r"\b(?:declare|typeset)\s+(?:-[^\s]*\s+)*-[^\s]*A\b",
            "array reader": r"\b(?:mapfile|readarray)\b",
            "printf assignment": r"\bprintf\s+-v\b",
        }
        for name, pattern in forbidden.items():
            with self.subTest(token=name):
                self.assertIsNone(re.search(pattern, source), name)

    def test_bash32_all_delivery_modes_equal_main(self):
        bash = os.environ.get("BASH32") or shutil.which("bash3.2")
        if not bash:
            self.skipTest("Bash 3.2 is not installed; no local executable was supplied via BASH32")
        version = timed_run([bash, "-c", 'printf "%s\\n" "$BASH_VERSION"'],
                            capture_output=True, check=True, cwd=self.root, env=self.env)
        self.assertTrue(version.stdout.startswith(b"3.2."), version.stdout)
        baseline = self.root / "origin-main-hook.sh"
        result = timed_run(["git", "show", "origin/main:.claude/hooks/fleet-memory.sh"],
                           capture_output=True, check=True, cwd=ROOT, env=self.env)
        baseline.write_bytes(result.stdout)
        for args, enabled in (((), True), (("--codex-cloud",), True), ((), False)):
            with self.subTest(args=args, receipt=enabled):
                self.reset()
                reference = self.run_hook(args, enabled=False, hook=baseline, bash=bash)
                expected = {path.name: path.read_bytes() for directory in (self.claude, self.codex)
                            for path in directory.iterdir() if path.name != "fleet-delivery.jsonl"}
                self.reset()
                actual = self.run_hook(args, enabled=enabled, bash=bash)
                self.assertEqual((reference.stdout, reference.stderr, reference.returncode),
                                 (actual.stdout, actual.stderr, actual.returncode))
                delivered = {path.name: path.read_bytes() for directory in (self.claude, self.codex)
                             for path in directory.iterdir() if path.name != "fleet-delivery.jsonl"}
                self.assertEqual(expected, delivered)

    def test_subprocess_timeout_kills_and_reaps_owned_group(self):
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.pid = 123
        process.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 10), (b"", b"")]
        with mock.patch.object(subprocess, "Popen", return_value=process), \
                mock.patch.object(os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                timed_run(["fixture"], capture_output=True)
        kill.assert_called_once_with(123, signal.SIGKILL)
        self.assertEqual([mock.call(timeout=10), mock.call(timeout=10)], process.communicate.call_args_list)

    def test_subprocess_timeouts_and_cleanup_are_structurally_required(self):
        tree = ast.parse(Path(__file__).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        subprocess_calls = [node for node in calls if isinstance(node.func, ast.Attribute)
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "subprocess" and
                            node.func.attr in ("run", "Popen", "call", "check_call", "check_output")]
        self.assertEqual(1, len(subprocess_calls))
        self.assertEqual("Popen", subprocess_calls[0].func.attr)
        self.assertTrue(any(keyword.arg == "start_new_session" and
                            isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                            for keyword in subprocess_calls[0].keywords))
        communicates = [node for node in calls if isinstance(node.func, ast.Attribute)
                        and node.func.attr == "communicate"]
        self.assertEqual(2, len(communicates))
        for call in communicates:
            self.assertTrue(any(keyword.arg == "timeout" for keyword in call.keywords))
        runner = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "timed_run")
        self.assertTrue(any(isinstance(node, ast.ExceptHandler) and any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and
            child.func.attr == "killpg" for child in ast.walk(node)) for node in ast.walk(runner)))
