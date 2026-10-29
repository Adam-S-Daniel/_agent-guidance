#!/usr/bin/env python3
"""Issue #89: count-driven offline reads and opt-in recorded dispatches."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
GH_SOURCE = REPO_ROOT / "harness/fakes/gh"


class TestGhTimeline(unittest.TestCase):
    REPO = "example-org/example-site"
    ERROR = b"gh: invalid local timeline configuration or state\n"
    WORKFLOW_ERROR = b"gh: invalid local workflow configuration or response\n"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gh-timeline-test-"))
        self.owned_root = self.root.resolve()
        self.addCleanup(self._cleanup)
        self.ws = self.root / "workspace"
        (self.ws / "bin").mkdir(parents=True)
        (self.ws / ".git").mkdir()
        # Source-only executable copies inherit no configuration, credentials,
        # objects, refs or remotes. The anchor is the sole .git entry.
        (self.ws / ".git/workspace-root").write_text(str(self.ws) + "\n")
        self.binary = self.ws / "bin/gh"
        shutil.copy2(GH_SOURCE, self.binary)
        self.binary.chmod(0o755)
        self.replay = self.ws / ".gh/replay"
        (self.replay / "timeline").mkdir(parents=True)
        self.state = self.ws / ".gh-timeline-state.json"
        self.log = self.ws / ".gh-invocations.log"
        self.sentinel = self.root / "sentinel"
        self.sentinel.mkdir()
        self.calls = self.root / "claude-calls"
        self.calls.write_bytes(b"")
        stub = self.sentinel / "claude"
        stub.write_text('#!/bin/sh\nprintf "called\\n" >> "$C52_CLAUDE_SENTINEL_LOG"\nexit 97\n')
        stub.chmod(0o755)

    def _cleanup(self):
        self.assertEqual(self.root.resolve(), self.owned_root)
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()))
        self.assertTrue(self.root.name.startswith("gh-timeline-test-"))
        self.assertEqual(self.calls.read_bytes(), b"", "sentinel was invoked")
        shutil.rmtree(self.root)

    def _json(self, path, document):
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")

    def _timeline(self, timelines=None):
        if timelines is None:
            timelines = {"run-list.json": ["timeline/first.json", "timeline/last.json"]}
        self._json(self.replay / "timeline.json", {"timelines": timelines})
        for names in timelines.values():
            if isinstance(names, list):
                for i, name in enumerate(names):
                    if isinstance(name, str) and name.startswith("timeline/") and ".." not in name:
                        path = self.replay / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes((f'{{ "databaseId": 7001, "step": {i + 1} }}\r\n').encode())

    def _policy(self, document=None):
        self._json(self.replay / "write-policy.json", document if document is not None else
                   {"workflow_run": [{"repo": self.REPO, "workflow": "deploy-preview"}]})

    def _env(self, **extra):
        return {"PATH": str(self.sentinel) + os.pathsep + os.environ.get("PATH", os.defpath),
                "HOME": str(self.root), "LANG": "C.UTF-8", "GH_REPLAY_DIR": str(self.replay),
                "GH_REPO": self.REPO, "GH_TOKEN": "", "GITHUB_TOKEN": "",
                "C52_CLAUDE_SENTINEL_LOG": str(self.calls), **extra}

    def _call(self, args, *, cwd=None):
        proc = subprocess.run([str(self.binary), *args], cwd=cwd or self.ws,
                              env=self._env(), capture_output=True, timeout=10)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertEqual(self.calls.read_bytes(), b"")
        return proc

    def _namespace(self):
        namespace = {"__name__": "gh_timeline_test", "__file__": str(self.binary)}
        exec(compile(self.binary.read_text(), str(self.binary), "exec"), namespace)
        return namespace

    def _reject(self, args=("run", "list"), *, message=None):
        before = self.state.read_bytes() if self.state.is_file() and not self.state.is_symlink() else None
        proc = self._call(list(args))
        self.assertEqual((proc.returncode, proc.stdout), (1, b""))
        self.assertEqual(proc.stderr, message or self.ERROR)
        self.assertIn("exit=1)", self.log.read_text().splitlines()[-1])
        after = self.state.read_bytes() if self.state.is_file() and not self.state.is_symlink() else None
        self.assertEqual(after, before)

    def test_each_advancing_read_preserves_bytes_and_records_its_count(self):
        cases = [("run-list.json", ["run", "list"]),
                 ("run-view-7001.json", ["run", "view", "7001"]),
                 ("run-view-7001.log", ["run", "view", "7001", "--log"]),
                 ("pr-checks.json", ["pr", "checks"]),
                 ("pr-checks-12.json", ["pr", "checks", "12"])]
        self._timeline({key: [f"timeline/{i}-first.json", f"timeline/{i}-last.json"]
                        for i, (key, _) in enumerate(cases)})
        for suffix in ("first", "last"):
            path = self.replay / f"timeline/2-{suffix}.json"
            path.write_bytes(b"\xff" + path.read_bytes())
        for i, (key, args) in enumerate(cases):
            for count, suffix in [(1, "first"), (2, "last")]:
                proc = self._call(args)
                self.assertEqual((proc.returncode, proc.stderr), (0, b""))
                self.assertEqual(proc.stdout, (self.replay / f"timeline/{i}-{suffix}.json").read_bytes())
                self.assertEqual(self.log.read_text().splitlines()[-1],
                    f"--- invocation (class=read key={key} exit=0 count={count}) --- " + json.dumps(args))
        self.assertEqual(json.loads(self.state.read_text()),
                         {"counts": {key: 2 for key, _ in cases}})

    def test_exhaustion_repeats_last_payload_but_counts_every_call(self):
        self._timeline()
        expected = ["first", "last", "last", "last", "last"]
        for count, name in enumerate(expected, 1):
            self.assertEqual(self._call(["run", "list"]).stdout,
                             (self.replay / f"timeline/{name}.json").read_bytes())
            self.assertIn(f"count={count})", self.log.read_text().splitlines()[-1])
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 5}})

    def test_normalized_keys_have_independent_counters_and_ignore_cwd(self):
        self._timeline({"run-list.json": ["timeline/list-1.json", "timeline/list-2.json"],
                        "run-view-7001.json": ["timeline/view-1.json", "timeline/view-2.json"],
                        "run-view-7002.json": ["timeline/other-1.json", "timeline/other-2.json"]})
        self.assertEqual(self._call(["run", "list", "--repo", self.REPO]).stdout,
                         (self.replay / "timeline/list-1.json").read_bytes())
        for number in ("7001", "7002"):
            self.assertIn(b'"step": 1', self._call(["run", "view", number]).stdout)
        self.assertEqual(self._call(["--repo=" + self.REPO, "run", "list", "--json", "status"],
                                   cwd=self.root).stdout,
                         (self.replay / "timeline/list-2.json").read_bytes())
        self.assertEqual(json.loads(self.state.read_text()),
                         {"counts": {"run-list.json": 2, "run-view-7001.json": 1, "run-view-7002.json": 1}})
        self.assertFalse((self.root / ".gh-timeline-state.json").exists())

    def test_unconfigured_reads_do_not_advance_a_timeline(self):
        self._timeline()
        (self.replay / "pr-view-12.json").write_bytes(b'{"number":12}\n')
        for args, expected_code in ((["pr", "view", "12"], 0),
                                    (["run", "view", "7001"], 1), ([], 0)):
            self.assertEqual(self._call(args).returncode, expected_code)
        self.assertFalse(self.state.exists())
        self.assertNotIn("count=", self.log.read_text())
        self.assertEqual(self._call(["run", "list"]).stdout,
                         (self.replay / "timeline/first.json").read_bytes())

    def test_counts_advance_without_consulting_clock_or_sleep(self):
        self._timeline()
        namespace = self._namespace()
        output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.dict(os.environ, self._env(), clear=True), \
             mock.patch("time.time", side_effect=AssertionError("clock consulted")), \
             mock.patch("time.monotonic", side_effect=AssertionError("clock consulted")), \
             mock.patch("time.sleep", side_effect=AssertionError("sleep consulted")), \
             contextlib.redirect_stdout(output):
            for _ in range(3):
                self.assertEqual(namespace["main"](["run", "list"]), 0)
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 3}})
        self.assertEqual(output.buffer.getvalue(),
                         (self.replay / "timeline/first.json").read_bytes()
                         + (self.replay / "timeline/last.json").read_bytes() * 2)

    def test_no_timeline_ignores_stale_state_and_preserves_legacy_bytes(self):
        self.state.write_bytes(b"unparseable ignored state")
        (self.replay / "run-list.json").write_bytes(b'[ { "status": "in_progress" } ]\n')
        args = ["run", "list", "--json", "status"]
        for _ in range(2):
            proc = self._call(args)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr),
                             (0, (self.replay / "run-list.json").read_bytes(), b""))
        self.assertEqual(self.log.read_bytes(),
            (('--- invocation (class=read key=run-list.json exit=0) --- ' + json.dumps(args) + '\n') * 2).encode())
        self.assertEqual(self.state.read_bytes(), b"unparseable ignored state")
        self.assertFalse((self.ws / ".gh-timeline-state.lock").exists())

    def test_all_existing_consumer_payloads_keep_their_bytes_and_legacy_logs(self):
        # The kit ships none of the consumer fixtures (DECISIONS X9); the
        # invented seed the fake's own tests drive stands in for them.
        names = ("fake-gh",)
        expected_count = 0
        for name in names:
            replay = REPO_ROOT / "test" / "fixtures" / name / "seed/.gh/replay"
            self.assertTrue(replay.is_dir())
            self.assertTrue(any(path.is_file() for path in replay.rglob("*")))
            for path in sorted(replay.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(replay).as_posix()
                if relative == "write-policy.json":
                    continue
                expected_count += 1
                with self.subTest(fixture=name, payload=relative):
                    stem = relative.rsplit(".", 1)[0]
                    args = ["api", stem[4:]] if stem.startswith("api/") else stem.split("-")
                    if path.suffix == ".log":
                        args.append("--log")
                    env = self._env(GH_REPLAY_DIR=str(replay))
                    proc = subprocess.run([str(self.binary), *args], cwd=self.ws, env=env,
                                          capture_output=True, timeout=10)
                    self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, path.read_bytes(), b""))
                    key = relative if path.suffix != ".txt" else stem + ".json"
                    self.assertEqual(self.log.read_text().splitlines()[-1],
                        f"--- invocation (class=read key={key} exit=0) --- " + json.dumps(args))
        self.assertEqual(len(self.log.read_text().splitlines()), expected_count)
        self.assertFalse(self.state.exists())

    def test_malformed_timeline_schema_fails_closed(self):
        documents = [[], {}, {"timelines": []}, {"timelines": {}, "extra": True}]
        for key, names in [("pr-view-12.json", ["timeline/first.json"]),
                           ("run-list.log", ["timeline/first.json"]),
                           ("run-view-07001.json", ["timeline/first.json"]),
                           ("run-view-0.json", ["timeline/first.json"]),
                           ("run-view-" + "9" * 21 + ".json", ["timeline/first.json"]),
                           ("pr-checks-0.json", ["timeline/first.json"]),
                           ("run-list.json", []), ("run-list.json", "x"),
                           ("run-list.json", [None]), ("run-list.json", [True])]:
            documents.append({"timelines": {key: names}})
        for document in documents:
            self._json(self.replay / "timeline.json", document)
            self._reject()
        for raw in (b"private malformed contents", b"\xff",
                    b'{"timelines":{},"timelines":{}}',
                    b'{"timelines":{"run-list.json":[],"run-list.json":[]}}',
                    b"[" * 10000 + b"0" + b"]" * 10000):
            (self.replay / "timeline.json").write_bytes(raw)
            self._reject()
        self.assertFalse(self.state.exists())

    def test_timeline_references_are_explicit_readable_local_regular_files(self):
        self._timeline()
        outside = self.root / "outside.json"
        outside.write_bytes(b"private outside bytes")
        for name in (str(outside), "../outside.json", "timeline/../../outside.json", "",
                     "./timeline/first.json", "timeline//first.json", "timeline/./first.json",
                     "timeline\\first.json", "timeline/missing.json", "timeline", "timeline/\x00.json"):
            self._json(self.replay / "timeline.json", {"timelines": {"run-list.json": [name]}})
            self._reject()
        self._json(self.replay / "timeline.json", {"timelines": {"run-list.json": ["timeline/link.json"]}})
        link = self.replay / "timeline/link.json"
        for target in (outside, self.replay / "timeline/first.json", self.root / "missing"):
            link.symlink_to(target)
            self._reject()
            link.unlink()
        os.mkfifo(link)
        self._reject()
        link.unlink()
        self.assertEqual(outside.read_bytes(), b"private outside bytes")

    def test_malformed_state_fails_closed_including_on_unlisted_reads(self):
        self._timeline()
        (self.replay / "pr-view-12.json").write_bytes(b'{"number":12}\n')
        states = [[], {}, {"counts": []}, {"counts": {}, "extra": True},
                  {"counts": {"run-view-7001.json": 1}}]
        states += [{"counts": {"run-list.json": value}}
                   for value in (True, 0, -1, 1.0, "1", None)]
        for state in states:
            self._json(self.state, state)
            self._reject()
            self._reject(["pr", "view", "12"])
        for raw in (b"private invalid state", b"\xff", b'{"counts":{},"counts":{}}',
                    b'{"counts":{"run-list.json":1,"run-list.json":2}}'):
            self.state.write_bytes(raw)
            self._reject()

    def test_config_state_and_lock_links_and_nonfiles_fail_closed(self):
        self._timeline()
        outside = self.root / "outside"
        outside.write_bytes(b"unchanged")
        for path in (self.replay / "timeline.json", self.state, self.ws / ".gh-timeline-state.lock"):
            if path.exists():
                path.unlink()
            for target in (outside, self.root / "missing", self.ws / "internal-missing"):
                path.symlink_to(target)
                self._reject()
                path.unlink()
            internal = path.parent / (path.name + ".internal")
            internal.write_bytes(b'{"timelines":{}}' if path.name == "timeline.json"
                                 else b'{"counts":{}}' if path == self.state else b"")
            path.symlink_to(internal)
            self._reject()
            path.unlink()
            internal.unlink()
            for kind in ("directory", "fifo"):
                path.mkdir() if kind == "directory" else os.mkfifo(path)
                self._reject()
                path.rmdir() if kind == "directory" else path.unlink()
            self._timeline()
        self.assertEqual(outside.read_bytes(), b"unchanged")
        self.assertFalse((self.ws / "internal-missing").exists())

    def test_atomic_state_write_failure_logs_once_and_cleans_temporary_file(self):
        self._timeline()
        self._json(self.state, {"counts": {"run-list.json": 1}})
        before = self.state.read_bytes()
        namespace = self._namespace()
        error = io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), \
             mock.patch("os.replace", side_effect=OSError("private details")), \
             contextlib.redirect_stderr(error):
            self.assertEqual(namespace["main"](["run", "list"]), 1)
        self.assertEqual(error.getvalue().encode(), self.ERROR)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertIn("exit=1)", self.log.read_text())
        self.assertEqual(set(self.ws.glob(".gh-timeline-*")),
                         {self.state, self.ws / ".gh-timeline-state.lock"})

    def test_lock_failure_and_replaced_inode_fail_closed(self):
        self._timeline()
        namespace = self._namespace()
        original = fcntl.flock
        lock_path = self.ws / ".gh-timeline-state.lock"
        def replaced_lock(descriptor, operation):
            original(descriptor, operation)
            lock_path.unlink()
            lock_path.write_bytes(b"replacement inode")
        for effect in (OSError("private lock details"), replaced_lock):
            error = io.StringIO()
            with mock.patch.dict(os.environ, self._env(), clear=True), \
                 mock.patch("fcntl.flock", side_effect=effect), contextlib.redirect_stderr(error):
                self.assertEqual(namespace["main"](["run", "list"]), 1)
            self.assertEqual(error.getvalue().encode(), self.ERROR)
            self.assertFalse(self.state.exists())

    def test_failed_output_corrects_count_record_without_truncating_a_sibling(self):
        self._timeline()
        namespace = self._namespace()
        def failing_output(payload):
            namespace["log_invocation"](["pr", "list"], "unknown", "pr-list.json", 1)
            return 1
        namespace["emit_bytes"] = failing_output
        with mock.patch.dict(os.environ, self._env(), clear=True), \
             mock.patch("os._exit", side_effect=SystemExit) as exit_process:
            with self.assertRaises(SystemExit):
                namespace["main"](["run", "list"])
        exit_process.assert_called_once_with(1)
        self.assertEqual(self.log.read_text().splitlines(), [
            '--- invocation (class=read key=run-list.json exit=1 count=1) --- ["run", "list"]',
            '--- invocation (class=unknown key=pr-list.json exit=1) --- ["pr", "list"]'])
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 1}})

    def test_output_write_failure_returns_one_and_keeps_the_consumed_count(self):
        self._timeline()
        namespace = self._namespace()
        output = mock.Mock()
        output.buffer.write.side_effect = OSError("private output details")
        with mock.patch.dict(os.environ, self._env(), clear=True), \
             mock.patch("sys.stdout", output), \
             mock.patch("os._exit", side_effect=SystemExit) as exit_process:
            with self.assertRaises(SystemExit):
                namespace["main"](["run", "list"])
        exit_process.assert_called_once_with(1)
        self.assertIn("exit=1 count=1)", self.log.read_text())
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 1}})

    def _instrument(self, wrappers):
        # Insert test-only IPC by parsing the executable's syntax tree.
        tree = ast.parse(self.binary.read_text())
        index = next(i for i, node in enumerate(tree.body)
                     if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                     and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")
        hooks = ast.parse(textwrap.dedent('''
            import select as _test_select
            def _test_signal():
                os.write(int(os.environ["TEST_READY_FD"]), b"x")
            def _test_pause():
                _test_signal()
                release = int(os.environ["TEST_RELEASE_FD"])
                if not _test_select.select([release], [], [], 10)[0]:
                    raise RuntimeError("test release timed out")
                if os.read(release, 1) != b"x":
                    raise RuntimeError("test release closed")
        ''') + textwrap.dedent(wrappers))
        tree.body[index:index] = hooks.body
        self.binary.write_text("#!/usr/bin/env python3\n" + ast.unparse(tree) + "\n")

    def _paused_call(self, args):
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        env = self._env(TEST_READY_FD=str(ready_write), TEST_RELEASE_FD=str(release_read))
        proc = subprocess.Popen([str(self.binary), *args], cwd=self.ws, env=env,
                                pass_fds=(ready_write, release_read),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(ready_write)
        os.close(release_read)
        def cleanup():
            if proc.poll() is None:
                proc.kill()  # Only the child created by this helper.
            proc.communicate(timeout=10)
            os.close(ready_read)
            os.close(release_write)
        self.addCleanup(cleanup)
        return proc, ready_read, release_write

    def _ready(self, running):
        self.assertTrue(select.select([running[1]], [], [], 10)[0], "child never reached handshake")
        self.assertEqual(os.read(running[1], 1), b"x")

    def _release(self, running):
        os.write(running[2], b"x")

    def _finished(self, running, *, code=0, payload=None):
        stdout, stderr = running[0].communicate(timeout=10)
        self.assertEqual(running[0].returncode, code, stderr)
        self.assertEqual(stderr, b"" if code == 0 else self.ERROR)
        self.assertEqual(stdout, payload or b"")

    def test_concurrent_interleave_serializes_fresh_counts_state_and_log(self):
        self._timeline()
        self._instrument('''
            _original_flock = fcntl.flock
            def _flock(*args):
                _test_signal()
                return _original_flock(*args)
            fcntl.flock = _flock
            _original_state = load_timeline_state
            def load_timeline_state(*args):
                result = _original_state(*args)
                _test_pause()
                return result
        ''')
        first = self._paused_call(["run", "list"])
        self._ready(first)  # Reaching flock.
        self._ready(first)  # Fresh state read while holding the lock.
        second = self._paused_call(["run", "list"])
        self._ready(second)  # The second process has reached the same flock.
        with open(self.ws / ".gh-timeline-state.lock", "r+b") as lock:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._release(first)
        self._finished(first, payload=(self.replay / "timeline/first.json").read_bytes())
        self._ready(second)  # It reads the first process's committed count.
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 1}})
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self._release(second)
        self._finished(second, payload=(self.replay / "timeline/last.json").read_bytes())
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 2}})
        self.assertEqual(self.log.read_text().splitlines(), [
            f'--- invocation (class=read key=run-list.json exit=0 count={count}) --- ["run", "list"]'
            for count in (1, 2)])
        with open(self.ws / ".gh-timeline-state.lock", "r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_exclusive_lock_covers_atomic_replacement_and_count_log(self):
        self._timeline()
        self._instrument('''
            _original_replace = os.replace
            def _replace(*args):
                _test_pause()
                return _original_replace(*args)
            os.replace = _replace
            _original_log = log_invocation
            def log_invocation(*args):
                if len(args) == 5:
                    _test_pause()
                return _original_log(*args)
        ''')
        running = self._paused_call(["run", "list"])
        for stage in ("atomic replacement", "count record"):
            self._ready(running)
            with open(self.ws / ".gh-timeline-state.lock", "r+b") as lock:
                with self.assertRaises(BlockingIOError, msg=stage):
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._release(running)
        self._finished(running, payload=(self.replay / "timeline/first.json").read_bytes())
        self.assertEqual(json.loads(self.state.read_text()), {"counts": {"run-list.json": 1}})
        self.assertIn("exit=0 count=1)", self.log.read_text())
        with open(self.ws / ".gh-timeline-state.lock", "r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_timeline_and_state_are_revalidated_after_startup(self):
        self._timeline()
        self._instrument('''
            _original_load = load_timeline
            _loads = 0
            def load_timeline(*args):
                global _loads
                _loads += 1
                result = _original_load(*args)
                if _loads == 1:
                    _test_pause()
                return result
        ''')
        for changed in ("configuration", "state"):
            self._timeline()
            self.state.unlink(missing_ok=True)
            running = self._paused_call(["run", "list"])
            self._ready(running)
            if changed == "configuration":
                self._json(self.replay / "timeline.json", {"timelines": {}})
            else:
                self.state.write_bytes(b"private invalid state")
            self._release(running)
            self._finished(running, code=1)
        self.assertNotIn("count=", self.log.read_text())

    # Real gh >= 2.87.0 with stdout not a TTY prints the run's html_url and a
    # newline (cli/cli pkg/cmd/workflow/run/run.go), not the JSON it recorded.
    RUN_URL = "https://example.com/example-org/example-site/actions/runs/7001"

    def test_workflow_dispatch_is_opt_in_prints_the_run_url_and_supports_repo_spellings(self):
        self._policy()
        payload = ('{ "databaseId": 7001, "url": "%s" }\r\n' % self.RUN_URL).encode()
        (self.replay / "workflow-run-deploy-preview.json").write_bytes(payload)
        for flags in (["--repo", self.REPO], ["--repo=" + self.REPO], ["-R", self.REPO],
                      ["-R" + self.REPO], ["-R=" + self.REPO],
                      ["--repo", "other-org/other-site", "--repo", self.REPO]):
            args = ["workflow", "run", "deploy-preview", *flags]
            proc = self._call(args)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr),
                             (0, self.RUN_URL.encode() + b"\n", b""))
            self.assertEqual(self.log.read_text().splitlines()[-1],
                '--- invocation (class=write key=workflow-run-deploy-preview.json exit=0) --- ' + json.dumps(args))
        self._timeline({"run-list.json": ["timeline/list.json"], "run-view-7001.json": ["timeline/view.json"]})
        for args in (["run", "list"], ["run", "view", "7001"]):
            self.assertEqual(json.loads(self._call(args).stdout)["databaseId"], 7001)
        self.assertFalse((self.ws / ".gh-label-state.json").exists())

    def test_dispatch_without_a_recorded_url_prints_nothing_like_a_204(self):
        # An older gh, or a server that returns no run details, prints nothing
        # on stdout and still exits 0; the dispatch is accepted and logged.
        self._policy()
        self._json(self.replay / "workflow-run-deploy-preview.json", {"databaseId": 7001})
        args = ["workflow", "run", "deploy-preview", "--repo", self.REPO]
        proc = self._call(args)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        self.assertEqual(self.log.read_text().splitlines()[-1],
            '--- invocation (class=write key=workflow-run-deploy-preview.json exit=0) --- ' + json.dumps(args))

    def test_dispatch_url_must_be_the_recorded_run_own_url(self):
        self._policy()
        args = ["workflow", "run", "deploy-preview", "--repo", self.REPO]
        for url in (None, True, "", "7001", "http://example.com/example-org/example-site/actions/runs/7001",
                    "https://example.com/example-org/example-site/actions/runs/7002",
                    "https://example.com/example-org/example-site/actions/runs/7001/job/1",
                    "https://example.com/example-org/example-site/actions/runs/7001\nsecond line",
                    "https://example.com/example-org/example-site/pull/7001",
                    "https://example.com/runs/7001"):
            self._json(self.replay / "workflow-run-deploy-preview.json", {"databaseId": 7001, "url": url})
            self._reject(args, message=self.WORKFLOW_ERROR)

    def test_ungranted_dispatch_refuses_without_looking_up_a_payload(self):
        (self.replay / "workflow-run-deploy-preview.json").write_bytes(b"private invalid dispatch response")
        for document in (None, {"pr_edit_add_label": []}, {"workflow_run": []}):
            if document is not None:
                self._policy(document)
            args = ["workflow", "run", "deploy-preview", "--repo", self.REPO]
            proc = self._call(args)
            self.assertEqual((proc.returncode, proc.stdout), (1, b""))
            self.assertEqual(proc.stderr,
                b'failed to run "gh workflow run": HTTP 403: Resource not accessible by personal access token '
                b'(https://api.github.com/repos/example-org/example-site/actions/workflows/deploy-preview/dispatches)\n'
                b'gh: the token available here has read-only scopes.\n')
            self.assertEqual(self.log.read_text().splitlines()[-1],
                '--- invocation (class=write key=workflow-run-deploy-preview.json exit=1) --- ' + json.dumps(args))

    def test_workflow_grants_require_exact_target_explicit_repo_and_no_extra_flags(self):
        self._policy()
        self._json(self.replay / "workflow-run-deploy-preview.json", {"databaseId": 7001})
        valid = ["workflow", "run", "deploy-preview", "--repo", self.REPO]
        cases = [valid[:3], valid[:2] + ["other"] + valid[3:],
                 valid[:4] + ["other-org/other-site"], valid + ["extra"],
                 valid + ["--ref", "preview"], valid + ["--field", "name=value"],
                 valid + ["--json"], valid + ["--help"], valid + ["--", "--ref", "preview"],
                 valid + ["--repo"], valid + ["--repo="],
                 ["workflow", "enable", "deploy-preview", "--repo", self.REPO]]
        for args in cases:
            proc = self._call(args)
            self.assertEqual((proc.returncode, proc.stdout), (1, b""))
            if args[:2] == ["workflow", "run"]:
                self.assertEqual(proc.stderr, self.DISPATCH_REFUSAL)
                record = self.log.read_text().splitlines()[-1]
                self.assertTrue(record.startswith("--- invocation (class=write key=workflow-run-"), record)
                self.assertTrue(record.endswith(" exit=1) --- " + json.dumps(args)), record)
            else:
                self.assertIn(b"HTTP 403", proc.stderr)

    # Once any dispatch is granted, the token-scope 403 would be false: the
    # same token dispatches the granted form. skills-evals#89: agents read
    # it as a credential problem and stopped before dispatching.
    DISPATCH_REFUSAL = (
        b"gh: workflow dispatch refused: this environment permits only the command lines below, "
        b"exactly as written (--repo given explicitly, no other flags such as --ref):\n"
        b"  gh workflow run deploy-preview --repo example-org/example-site\n")

    def test_ungranted_dispatch_lists_every_grant_without_token_blame(self):
        self._policy({"workflow_run": [{"repo": self.REPO, "workflow": "deploy-preview.yml"},
                                       {"repo": self.REPO, "workflow": "deploy-preview"}]})
        (self.replay / "workflow-run-deploy-preview.json").write_bytes(b"private invalid dispatch response")
        for args in (["workflow", "run", "deploy-preview"], ["workflow", "run", "deploy"],
                     ["workflow", "run", "deploy-preview", "--repo", self.REPO, "--ref", "master"]):
            proc = self._call(args)
            self.assertEqual((proc.returncode, proc.stdout), (1, b""))
            self.assertEqual(proc.stderr, self.DISPATCH_REFUSAL + b"  gh workflow run deploy-preview.yml "
                             b"--repo example-org/example-site\n")
            self.assertNotIn(b"scopes", proc.stderr)
            self.assertIn("class=write", self.log.read_text().splitlines()[-1])
            self.assertIn("exit=1)", self.log.read_text().splitlines()[-1])

    def test_invalid_workflow_policy_is_not_a_grant(self):
        self._json(self.replay / "workflow-run-deploy-preview.json", {"databaseId": 7001})
        rows = [{"workflow_run": {}}, {"workflow_run": [None]},
                {"workflow_run": [{"repo": self.REPO, "workflow": "deploy-preview"}] * 2}]
        for field, value in (("repo", "../x"), ("repo", ""), ("repo", True),
                             ("workflow", "../deploy-preview"), ("workflow", ""),
                             ("workflow", "deploy preview"), ("workflow", True)):
            rows.append({"workflow_run": [dict(repo=self.REPO, workflow="deploy-preview") | {field: value}]})
        rows.append({"workflow_run": [{"repo": self.REPO, "workflow": "deploy-preview", "extra": 1}]})
        for row in rows:
            self._policy(row)
            self._reject(["workflow", "run", "deploy-preview", "--repo", self.REPO], message=self.WORKFLOW_ERROR)

    def test_permitted_dispatch_requires_a_positive_json_run_id(self):
        self._policy()
        path = self.replay / "workflow-run-deploy-preview.json"
        args = ["workflow", "run", "deploy-preview", "--repo", self.REPO]
        for document in ([], {}, {"databaseId": True}, {"databaseId": 0}, {"databaseId": -1},
                         {"databaseId": "7001"}, {"databaseId": 7001.0}):
            self._json(path, document)
            self._reject(args, message=self.WORKFLOW_ERROR)
        for raw in (b"private malformed response", b"\xff", b'{"databaseId":7001,"databaseId":7002}'):
            path.write_bytes(raw)
            self._reject(args, message=self.WORKFLOW_ERROR)
        path.unlink()
        self._reject(args, message=self.WORKFLOW_ERROR)
        os.mkfifo(path)
        self._reject(args, message=self.WORKFLOW_ERROR)
        path.unlink()
        outside = self.root / "outside-dispatch"
        self._json(outside, {"databaseId": 7001})
        path.symlink_to(outside)
        self._reject(args, message=self.WORKFLOW_ERROR)
        path.unlink()
        internal = self.replay / "internal-dispatch.json"
        self._json(internal, {"databaseId": 7001})
        path.symlink_to(internal)
        self._reject(args, message=self.WORKFLOW_ERROR)

    def test_workflow_and_label_grants_coexist_without_changing_label_state_schema(self):
        labels = [{"repo": self.REPO, "number": 12, "label": "preview"}]
        self._policy({"pr_edit_add_label": labels,
                      "workflow_run": [{"repo": self.REPO, "workflow": "deploy-preview"}]})
        self._json(self.replay / "pr-view-12.json", {"number": 12, "state": "OPEN", "labels": []})
        self._json(self.replay / "workflow-run-deploy-preview.json", {"databaseId": 7001})
        self.assertEqual(self._call(["pr", "edit", "12", "--repo", self.REPO, "--add-label", "preview"]).returncode, 0)
        self.assertEqual(self._call(["workflow", "run", "deploy-preview", "--repo", self.REPO]).returncode, 0)
        self.assertEqual(json.loads((self.ws / ".gh-label-state.json").read_text()), {"pr_edit_add_label": labels})
        self.assertEqual(json.loads(self._call(["pr", "view", "12"]).stdout)["labels"], [{"name": "preview"}])
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
