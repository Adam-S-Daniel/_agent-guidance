#!/usr/bin/env python3
"""Issue #97 — a `guidance` subject with five delivery modes and a per-arm
delivery guard, plus the per-issue test discovery this file is itself the
first user of.

Hermetic, like the rest of the suite: CLAUDE_BIN points at a test-owned
stand-in, no network, no wall-clock, and — asserted below — never a
write into the real ~/.claude/CLAUDE.md.

This module is discovered and run by test/run_tests.py (see
`build_suite`/`DISCOVERY_DIR` there); it is also runnable on its own with
`python3 test/issues/test_issue_97.py`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
FAKE_CLAUDE = TEST_DIR / "fake-claude"
ISSUES_DIR = TEST_DIR / "issues"
DELIVERY_DIR = REPO_ROOT / "evals" / "guidance" / "_delivery"
CANARY_DIR = REPO_ROOT / "evals" / "guidance-bridge-canary"
EVAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(HARNESS_DIR))
import guidance  # noqa: E402
import run_canary  # noqa: E402
import run_eval  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import make_badge  # noqa: E402

# A child `python3 test/run_tests.py` sets this, so the two discovery tests
# below (the only ones that shell out to the whole suite) skip inside it
# instead of forking the suite again, forever.
CHILD_ENV = "SKILLS_EVALS_SUITE_CHILD"

# The file test/run_tests.py fingerprints around the WHOLE run: the fleet
# hook's `$HOME/.claude/CLAUDE.md`, or whatever `$SKILLS_EVALS_USER_MEMORY`
# names instead. The override exists for exactly one reason — so the guard
# itself can be driven red without anyone writing the operator's real memory
# file — and `test_the_run_wide_user_memory_guard_names_its_override` pins
# that this spelling is the one run_tests.py reads.
MEMORY_ENV = "SKILLS_EVALS_USER_MEMORY"

# What run_tests.py reads to discover somewhere other than the real
# test/issues/. The two planting tests below use it to plant their probe in a
# SCRATCH dir instead: a concurrently running child suite (a parallel run,
# #182) discovers whatever lives under the real test/issues/, so a probe
# planted there would fail that OTHER worker's run too.
# `test_the_discovery_override_names_its_env_var` pins that this spelling is
# the one run_tests.py reads.
DISCOVERY_ENV = "SKILLS_EVALS_DISCOVERY_DIR"

_MEMORY_BEFORE: str | None = None

sys.path.insert(0, str(TEST_DIR))
from arm_test_env import install_arm_test_environment  # noqa: E402
from lockfile_pins import lock_problem  # noqa: E402
import example_evals  # noqa: E402


def _watched_user_memory() -> Path:
    override = os.environ.get(MEMORY_ENV)
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".claude" / "CLAUDE.md"


def _memory_fingerprint(path: Path) -> str:
    """`absent`, or the md5 of the bytes. The CONTENT is never reported: this
    file is the operator's own memory, and a failure names the path and the
    two digests, nothing else."""
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


@install_arm_test_environment
def setUpModule() -> None:
    """Second net under test/run_tests.py's run-wide guard.

    The run-wide one is the real guarantee — it spans build_suite() and the
    runner, so a write from ANY module is caught. This pair narrows the blast
    radius to a module when the suite is run some other way (`python3
    test/issues/test_issue_97.py`, a `-k` selection, an IDE), which is how the
    56 KB `~/.claude/CLAUDE.md` that a revert-the-guard mutation destroyed
    would have been noticed at the module boundary rather than never.
    """
    global _MEMORY_BEFORE
    _MEMORY_BEFORE = _memory_fingerprint(_watched_user_memory())


def tearDownModule() -> None:
    try:
        path = _watched_user_memory()
        after = _memory_fingerprint(path)
        if after != _MEMORY_BEFORE:
            raise AssertionError(
                f"{path} changed while this module ran ({_MEMORY_BEFORE} -> "
                f"{after}) — no test in this file may write the fleet's user "
                "memory; every arm gets a scratch config dir")
    finally:
        unittest.doModuleCleanups()


# A guidance checkout the payload tests build from scratch: base.md with a
# fenced `## ` that is NOT a heading, a `###` child that belongs to its
# parent's extent, and a sections/ file that is its own single `##`.
FIXTURE_BASE_MD = """\
# AGENTS.md

Intro paragraph, everything before the first `##`.

## Alpha

Alpha body.

## Bravo

Bravo body, which shows a fenced block:

```markdown
## Not A Heading

This line lives inside a fence and must never end Bravo's extent.
```

### Bravo child

The `###` child belongs to Bravo's extent.

## Charlie

Charlie body.
"""

FIXTURE_SECTION_MD = """\
## Delta

Delta body, an opt-in language section in its own file.
"""

FIXTURE_STUB_MD = """\
# AGENTS.md

## Fleet guidance is delivered once per session — not by this file

The stub.
"""

FIXTURE_MANIFEST = [
    {"id": "alpha", "heading": "Alpha", "file": "agents-md/base.md", "status": "gap"},
    {"id": "bravo", "heading": "Bravo", "file": "agents-md/base.md", "status": "gap"},
    {"id": "charlie", "heading": "Charlie", "file": "agents-md/base.md", "status": "gap"},
    {"id": "section-delta", "heading": "Delta",
     "file": "agents-md/sections/delta.md", "status": "gap"},
]


def make_guidance_checkout(root: Path) -> Path:
    """A minimal `_agent-guidance` checkout: manifest, base.md, stub.md, a
    sections/ file, and the REAL fleet-memory.sh copied from the sibling
    checkout when one exists (tests that need the real hook skip without it).
    """
    (root / "agents-md" / "sections").mkdir(parents=True)
    (root / "agents-md" / "base.md").write_text(FIXTURE_BASE_MD, encoding="utf-8")
    (root / "agents-md" / "stub.md").write_text(FIXTURE_STUB_MD, encoding="utf-8")
    (root / "agents-md" / "sections" / "delta.md").write_text(
        FIXTURE_SECTION_MD, encoding="utf-8")
    (root / "agents-md" / "eval-coverage.yml").write_text(
        yaml.safe_dump(FIXTURE_MANIFEST, sort_keys=False), encoding="utf-8")
    hook_dir = root / ".claude" / "hooks"
    hook_dir.mkdir(parents=True)
    real_hook = REAL_GUIDANCE_DIR / ".claude" / "hooks" / "fleet-memory.sh"
    if real_hook.is_file():
        shutil.copy2(real_hook, hook_dir / "fleet-memory.sh")
        (hook_dir / "fleet-guidance.md").write_text(
            "# shipped payload\n", encoding="utf-8")
    return root


# The sibling checkout the harness itself defaults to. Present in CI (eval.yml
# checks it out side by side) and on a dev box; tests that need the REAL hook
# or the REAL manifest skip with a printed reason when it is not.
REAL_GUIDANCE_DIR = (REPO_ROOT / ".." / "_agent-guidance").resolve()


def _harness_default(path: Path, functions: tuple[str, ...], receiver: str,
                     key: str) -> int:
    """The integer default a harness function falls back to, READ OUT OF THE
    SOURCE: the literal in `<receiver>.get("<key>", <int>)` inside each named
    function. Every site must agree, and at least one must exist, so a budget
    test built on it cannot go on counting a number the harness stopped
    using, or pass vacuously when the call site moves.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in functions):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and ast.unparse(call.func.value) == receiver
                    and len(call.args) == 2
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == key
                    and isinstance(call.args[1], ast.Constant)
                    and isinstance(call.args[1].value, int)):
                found.add(call.args[1].value)
    assert len(found) == 1, (
        f"{path.name}: expected exactly one default for "
        f"`{receiver}.get({key!r}, <int>)` in {functions}, found "
        f"{sorted(found)} — has the call site's shape changed?")
    return found.pop()


def _signature_default(path: Path, function: str, argument: str) -> int:
    """The integer default of one keyword parameter, parsed from the source."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == function)
    args = fn.args
    pairs = list(zip(reversed(args.args), reversed(args.defaults)))
    pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults)
              if d is not None]
    for arg, default in pairs:
        if arg.arg == argument:
            assert (isinstance(default, ast.Constant)
                    and isinstance(default.value, int)), (function, argument)
            return default.value
    raise AssertionError(f"{function}() has no defaulted `{argument}` parameter")


class TestIssue97(unittest.TestCase):
    """Every new assertion for #97 lives in this one class."""

    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="issue97-cli-")
        self.addCleanup(self.temp.cleanup)
        self.cli = Path(self.temp.name) / "cli-stub"
        self.cli.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        self.cli.chmod(0o700)

    # ------------------------------------------------------------------
    # Per-issue test discovery (the structural half of this PR)
    #
    # Every fixture PR used to append its tests to the bottom of
    # test/run_tests.py, so every fixture PR conflicted with every other at
    # the same four append points. run_tests.py now DISCOVERS
    # test/issues/test_issue_*.py in addition to its own classes, and this
    # file is the first module to arrive that way. The two tests below are
    # the pin: a planted module with one failing test must make the runner
    # exit 1, and removing it must put it back to 0. They shell out to
    # `python3 test/run_tests.py`, so they skip inside a child run (or they
    # would fork the suite forever).
    #
    # The child is NARROWED, never the whole suite: a SCRATCH discovery dir
    # (`$SKILLS_EVALS_DISCOVERY_DIR`) holding only the probe module, and a
    # `-k` that selects only it (plus, for the zero-exit pin, one cheap
    # run_tests.py class). A child that ran everything took 24-29 minutes on
    # a loaded runner, four times per run, and PR #251's `test` job lost two
    # of them to the 900 s subprocess timeout. The proofs need run_tests.py's
    # real entrypoint, discovery, `-k` and memory guard — not 3,000 other
    # tests — so the timeout below is a safety net, not what keeps these green.
    # ------------------------------------------------------------------

    PLANTED = ISSUES_DIR / "test_issue_zz_discovery_probe.py"
    PLANTED_SOURCE = (
        "import unittest\n\n\n"
        "class DiscoveryProbe(unittest.TestCase):\n"
        "    def test_planted_failure(self):\n"
        "        self.fail('discovery-probe: planted by TestIssue97')\n"
    )
    # A discovered module whose one test passes and touches nothing.
    CLEAN_SOURCE = (
        "import unittest\n\n\n"
        "class CleanProbe(unittest.TestCase):\n"
        "    def test_touches_nothing(self):\n"
        "        pass\n"
    )

    def _run_suite(self, env_extra: dict | None = None,
                   args: tuple[str, ...] = ()
                   ) -> subprocess.CompletedProcess:
        """A reviewed suite spawner that stands down itself. The runner
        verifies every member of its sanctioned sink inventory.

        S-B-a-2. Round 2 guarded the one test that forked; round 3 pinned two
        file globs; round 4 measured three helper locations those globs never
        saw — `test/r4forkhelper.py`, `harness/r4harnessfork.py` and a PACKAGE
        at `test/issues/r4helpers/__init__.py` — each leaving both pins green
        and each running the tree away. The guard belongs HERE, at the
        spawner, because then a caller cannot fork the suite without passing
        through it, whatever file the caller lives in and whatever it is
        called. A new guarded forking test now needs no guard of its own.

        The callers keep their own `self._skip_in_child()`: it is the same
        answer one frame earlier, with a printed reason, and it costs nothing.

        `args` is the child's command line (`-k PATTERN ...`). Every caller
        passes one: an argument-less child is the WHOLE suite.
        """
        if os.environ.get(CHILD_ENV):
            reason = ("child suite run — the one suite spawner does not "
                      "re-fork the suite from inside itself")
            print(reason)
            raise unittest.SkipTest(reason)
        # The caller may add a throwaway-memory path, but may never clear the
        # marker that makes a recursively launched suite stand down.
        env = dict(os.environ, CLAUDE_BIN=str(self.cli))
        env.update(env_extra or {})
        env[CHILD_ENV] = "1"
        return subprocess.run(
            [sys.executable, str(TEST_DIR / "run_tests.py"), *args],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
            timeout=900)

    def test_child_suite_uses_a_test_owned_cli(self):
        self._skip_in_child()
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": "/missing/cli"}), \
                mock.patch.object(subprocess, "run") as run:
            self._run_suite()
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["CLAUDE_BIN"], str(self.cli))
        self.assertTrue(os.access(self.cli, os.X_OK))

    @staticmethod
    def _ran_counts(output: str) -> list[int]:
        return [int(n) for n in re.findall(r"^Ran (\d+) tests?", output,
                                           flags=re.MULTILINE)]

    def _scratch_discovery_dir(self, prefix: str,
                               **modules: str) -> Path:
        """A tempdir for a child run to discover INSTEAD of the real
        test/issues/ (#182): a concurrently running child suite spawned by
        any OTHER parallel worker discovers whatever lives under the real
        directory, so planting there would fail that other worker's run too.

        Holds exactly the modules passed (`name=source`, written as
        `<name>.py`) and none of the real ones: the child imports and runs a
        handful of tests, not the ~3,000 the real directory would add.
        """
        scratch = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        for name, source in modules.items():
            (scratch / f"{name}.py").write_text(source, encoding="utf-8")
        return scratch

    def _skip_in_child(self) -> None:
        if os.environ.get(CHILD_ENV):
            reason = ("child suite run — the discovery pin does not re-fork "
                      "the suite from inside itself")
            print(reason)
            self.skipTest(reason)

    def test_planted_issue_module_is_discovered_and_fails_the_runner(self):
        self._skip_in_child()
        self.assertFalse(self.PLANTED.exists(),
                         f"{self.PLANTED} is left over from an earlier run")
        # Planted into a SCRATCH discovery dir (#182), never the real
        # test/issues/ — see _scratch_discovery_dir. $SKILLS_EVALS_DISCOVERY_DIR
        # is what makes the child's build_suite() look there instead, and
        # `-k` narrows the child to the planted module alone.
        scratch = self._scratch_discovery_dir(
            "issue97-discovery-probe-",
            **{self.PLANTED.stem: self.PLANTED_SOURCE})
        proc = self._run_suite(env_extra={DISCOVERY_ENV: str(scratch)},
                               args=("-k", self.PLANTED.stem))
        output = proc.stdout + proc.stderr
        self.assertEqual(
            proc.returncode, 1,
            "a planted test/issues/test_issue_*.py with one failing test must "
            f"make `python3 test/run_tests.py` exit 1; got {proc.returncode}\n"
            f"{output[-3000:]}")
        self.assertIn("discovery-probe: planted by TestIssue97", output,
                      "the planted module's failure must be reported by name")
        counts = self._ran_counts(output)
        self.assertEqual(counts, [1],
                         "the runner must print ONE total, and it must be the "
                         f"planted module's single test; got {counts}")

    def test_removing_the_planted_module_puts_the_runner_back_to_zero(self):
        self._skip_in_child()
        self.assertFalse(self.PLANTED.exists(),
                         f"{self.PLANTED} is left over from an earlier run")
        # The same scratch discovery dir with a PASSING module in place of
        # the failing one, and the selection widened by one cheap test of
        # run_tests.py's own, so the single total still has to span the
        # runner's own classes AND a discovered module.
        clean = "test_issue_zz_discovery_clean"
        scratch = self._scratch_discovery_dir(
            "issue97-discovery-clean-", **{clean: self.CLEAN_SOURCE})
        own = "TestParallelJobs.test_parse_argv_jobs_defaults_to_one"
        proc = self._run_suite(env_extra={DISCOVERY_ENV: str(scratch)},
                               args=("-k", clean, "-k", own))
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0,
                         "with no planted failure the run must exit 0\n"
                         f"{output[-3000:]}")
        counts = self._ran_counts(output)
        self.assertEqual(counts, [2],
                         "one printed total, spanning run_tests.py's own "
                         "class AND the discovered module (2 tests); got "
                         f"{counts}\n{output[-3000:]}")

    def test_this_module_is_reachable_through_the_discovery_pattern(self):
        # The discovery contract in one assertion: this file lives in the
        # discovered subtree and matches the pattern the runner globs, so a
        # future fixture PR can add test/issues/test_issue_<n>.py instead of
        # appending to run_tests.py.
        self.assertEqual(Path(__file__).resolve().parent, ISSUES_DIR)
        discovered = sorted(p.name for p in ISSUES_DIR.glob("test_issue_*.py"))
        self.assertIn("test_issue_97.py", discovered)

    # ------------------------------------------------------------------
    # Item 2 — payload assembly (pure)
    # ------------------------------------------------------------------

    def _checkout(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="guidance-checkout-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return make_guidance_checkout(tmp)

    def _row(self, guidance_dir: Path, section_id: str) -> dict:
        return guidance.find_row(guidance.load_manifest(guidance_dir),
                                 section_id, guidance_dir)

    def test_section_extent_stops_at_the_next_h2_and_keeps_its_h3_child(self):
        root = self._checkout()
        payload = guidance.assemble(root, self._row(root, "bravo"), "section")
        self.assertIn("## Bravo", payload)
        self.assertIn("### Bravo child", payload,
                      "a `###` child belongs to its parent's extent")
        self.assertNotIn("## Charlie", payload,
                         "the extent stops at the line before the next `##`")
        self.assertNotIn("## Alpha", payload)

    def test_a_fenced_h2_never_ends_an_extent(self):
        # The reason this is a real markdown parse and not a regex: `## Not A
        # Heading` sits inside a fenced block in Bravo's body, and a line scan
        # would end Bravo there and lose everything after it.
        root = self._checkout()
        payload = guidance.assemble(root, self._row(root, "bravo"), "section")
        self.assertIn("## Not A Heading", payload,
                      "the fenced line is part of Bravo's body, not a heading")
        self.assertIn("### Bravo child", payload,
                      "content AFTER the fence must still be in the extent")
        headings = [s["heading"] for s in guidance.h2_extents(FIXTURE_BASE_MD)]
        self.assertEqual(headings, ["Alpha", "Bravo", "Charlie"],
                         "a `## ` inside a fence is not a heading")

    def test_section_payload_prepends_the_files_intro(self):
        root = self._checkout()
        payload = guidance.assemble(root, self._row(root, "alpha"), "section")
        self.assertTrue(payload.startswith("# AGENTS.md\n"))
        self.assertIn("Intro paragraph, everything before the first `##`.", payload)
        self.assertIn("## Alpha", payload)
        self.assertNotIn("## Bravo", payload)

    def test_full_minus_section_is_full_minus_exactly_the_extent(self):
        root = self._checkout()
        for section_id in ("alpha", "bravo", "charlie", "section-delta"):
            with self.subTest(section=section_id):
                row = self._row(root, section_id)
                full = guidance.assemble(root, row, "full")
                minus = guidance.assemble(root, row, "full-minus-section")
                extent = guidance._extent_of(
                    guidance.corpus(root, row), row["heading"], "corpus")
                length = extent["end"] - extent["start"]
                self.assertGreater(length, 0)
                self.assertEqual(len(full) - length, len(minus))
                self.assertNotIn(f"## {row['heading']}", minus)

    def test_full_is_the_corpus_verbatim_and_stub_is_the_stub(self):
        root = self._checkout()
        row = self._row(root, "alpha")
        self.assertEqual(guidance.assemble(root, row, "full"), FIXTURE_BASE_MD)
        self.assertEqual(guidance.assemble(root, row, "stub"), FIXTURE_STUB_MD)

    def test_a_sections_file_is_its_own_single_h2(self):
        root = self._checkout()
        row = self._row(root, "section-delta")
        self.assertEqual(guidance.assemble(root, row, "section"), FIXTURE_SECTION_MD)
        # `full` for an opt-in language section is base.md WITH it; the
        # ablation arm is therefore base.md alone, which is the only reading
        # under which the ablation pair differs at all.
        self.assertEqual(guidance.assemble(root, row, "full"),
                         FIXTURE_BASE_MD + FIXTURE_SECTION_MD)
        self.assertEqual(guidance.assemble(root, row, "full-minus-section"),
                         FIXTURE_BASE_MD)

    def test_mode_none_delivers_no_guidance_but_does_deliver_a_decoy(self):
        # The control delivers no GUIDANCE — and it is not delivered nothing.
        # It carries a DECOY: the token it is handed, alone, in an otherwise
        # empty block. That is what lets its guard ask a question with a wrong
        # answer ("does this arm read its own scratch user memory?") instead
        # of the vacuous "no magic word?", which a `none` arm answered
        # identically whether it was clean or contaminated.
        root = self._checkout()
        row = self._row(root, "alpha")
        self.assertEqual(guidance.assemble(root, row, "none", token=None), "",
                         "with no token there is nothing to deliver at all")
        self.assertEqual(guidance.assemble(root, row, "none", token="TOK-1"),
                         guidance.token_paragraph("TOK-1"))
        self.assertNotIn("magic word is TOK-1",
                         guidance.assemble(root, row, "none", token=None))

    def test_the_decoy_generator_is_independent_of_the_treatment_one(self):
        # If `new_decoy_token` were `new_token` under another name, a test that
        # pins one would collapse the other into it and the control arm would
        # be handed the TREATMENT token — reporting itself contaminated on
        # every run.
        with mock.patch.object(guidance, "new_token", lambda: "PINNED-0000"):
            self.assertNotEqual(guidance.new_decoy_token(), "PINNED-0000")
        self.assertNotEqual(guidance.new_decoy_token(), guidance.new_decoy_token())

    def test_every_non_none_payload_carries_the_magic_word_paragraph(self):
        root = self._checkout()
        row = self._row(root, "bravo")
        for mode in ("stub", "section", "full", "full-minus-section"):
            with self.subTest(mode=mode):
                payload = guidance.assemble(root, row, mode, token="ZZZZZZZZ-1234")
                self.assertTrue(payload.endswith(
                    "\nThe magic word is ZZZZZZZZ-1234.\n"),
                    f"{mode} payload must end in the magic-word paragraph")
                self.assertEqual(
                    payload.count("ZZZZZZZZ-1234"), 1,
                    "exactly one occurrence, so a probe reply naming it is "
                    "unambiguous")

    def test_tokens_are_fresh_per_run(self):
        # A token an earlier run could reproduce would let a stale, real
        # ~/.claude/CLAUDE.md satisfy this run's guard — the exact
        # contamination the guard exists to catch.
        tokens = {guidance.new_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        for token in tokens:
            self.assertRegex(token, r"^[A-Z]{8}-\d{4}$")

    def test_unknown_section_id_names_the_manifest(self):
        root = self._checkout()
        with self.assertRaises(guidance.GuidanceError) as ctx:
            self._row(root, "no-such-section")
        message = str(ctx.exception)
        self.assertIn("no-such-section", message)
        self.assertIn("eval-coverage.yml", message,
                      "the message must name the manifest, which is the file "
                      "to go and look at")
        self.assertIn("alpha", message, "and list the ids it does know")

    def test_an_empty_payload_is_refused_rather_than_delivered_as_a_token(self):
        # N-a. An empty payload plus the magic-word paragraph gives the arm
        # nothing but the token — and passes the delivery guard, because the
        # guard looks for the token. A truncated `agents-md/stub.md` is the
        # reachable cause; the lone-CR file the review named is NOT (see the
        # test below).
        root = self._checkout()
        row = self._row(root, "alpha")
        (root / guidance.STUB_REL).write_text("   \n\n", encoding="utf-8")
        with self.assertRaises(guidance.GuidanceError) as ctx:
            guidance.assemble(root, row, "stub", token="TOK-1")
        self.assertIn("empty", str(ctx.exception))
        self.assertIn("alpha", str(ctx.exception))

    def test_an_empty_payload_exits_2_through_main(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-emptypayload-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        (root / guidance.STUB_REL).write_text("\n", encoding="utf-8")
        eval_dir = self._guidance_fixture(
            tmp, arms={"with_guidance_stub": {"mode": "stub"},
                       "without_guidance": {"mode": "none"}})
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance_stub",
                                  "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("empty", out)
        self.assertNotIn("Traceback", out)

    def test_a_lone_cr_file_never_reaches_the_line_arithmetic(self):
        # The round-1 review's stated trigger for N-a, measured: `_read` uses
        # `Path.read_text()`, whose universal-newline translation turns a lone
        # \r into \n before either the markdown parse or `text.split("\n")`
        # sees it. So the parse and the arithmetic cannot disagree about line
        # endings through the production path, and the empty-payload refusal
        # above is a floor over the whole class rather than a fix for this
        # cause. Pinned so a future `newline=""` read is caught.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-cr-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "x.md").write_bytes(b"# T\r\r## A\r\rbody\r")
        text = guidance._read(tmp, Path("x.md"))
        self.assertNotIn("\r", text)
        extents = guidance.h2_extents(text)
        self.assertEqual([e["heading"] for e in extents], ["A"])
        self.assertIn("body", text[extents[0]["start"]:extents[0]["end"]])

    def test_a_fixture_env_may_not_repoint_the_per_arm_isolation(self):
        # N-h. The fixture's `env:` block is applied AFTER HOME, TMPDIR and
        # CLAUDE_CONFIG_DIR are set, so a fixture naming one could point an
        # arm at the operator's real config dir.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-envnames-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        paths = {n: tmp / n for n in ("ws", "home", "tmp", "config")}
        for path in paths.values():
            path.mkdir(parents=True)
        kwargs = dict(workspace=paths["ws"], home=paths["home"],
                      tmpdir=paths["tmp"], config_dir=paths["config"])
        for name in guidance.ISOLATION_NAMES:
            with self.subTest(name=name):
                with self.assertRaises(guidance.GuidanceError) as ctx:
                    guidance.agent_env(**kwargs, env_spec={name: "/etc"})
                self.assertIn(name, str(ctx.exception))
        env = guidance.agent_env(**kwargs, env_spec={"FOO": "bar"})
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["HOME"], str(paths["home"]))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(paths["config"]))

    # BOTH reasons the guard cannot settle an ambient read, pinned by their
    # operative words in ALL THREE places the residual paragraph is written
    # out. The first reason alone is the one the round-2 remedy left standing
    # in DESIGN, and README gave none: the two-token case is the NARROWER of
    # the two, and the case that actually scores clean most often — a
    # contaminating source with no token of its own, the real base.md
    # included — has nothing to do with ambiguity. Each document says it in
    # its own words; these are the words all three must share.
    RESIDUAL_REASONS = (
        ("may report either",
         "a probe whose context carries two magic words may report either"),
        ("no token of its own",
         "a contaminating source carrying no token of its own is invisible "
         "to a token guard at all"),
    )

    RESIDUAL_DOCS = ("harness/guidance.py", "README.md", "DESIGN.md")

    def _residual_texts(self) -> dict:
        return {
            "harness/guidance.py": guidance.__doc__ or "",
            "README.md": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            "DESIGN.md": (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8"),
        }

    def test_all_three_residual_paragraphs_name_both_reasons_the_guard_cannot_settle(self):
        texts = self._residual_texts()
        self.assertIn(
            "THE RESIDUAL the guard does NOT settle",
            " ".join(texts["harness/guidance.py"].split()),
            "harness/guidance.py must still carry the residual paragraph — "
            "this assertion must not pass vacuously")
        for label in self.RESIDUAL_DOCS:
            folded = " ".join(texts[label].split())
            for phrase, reason in self.RESIDUAL_REASONS:
                with self.subTest(doc=label, reason=reason):
                    # assertTrue, not assertIn: assertIn's default message
                    # would dump the whole document ahead of the sentence
                    # that explains the failure.
                    self.assertTrue(
                        phrase in folded,
                        f"{label} does not carry {phrase!r}. The residual "
                        f"paragraph must give BOTH reasons the guard cannot "
                        f"settle an ambient read, and this is the missing "
                        f"one: {reason}.")

    # The S-A clause, in the three places the residual paragraph is written
    # out. Pinned by its operative words in all three, because a reader who
    # meets the plural prompt and not the reason for it will "simplify" it
    # back to the singular.
    PLURAL_RATIONALE = "the plural prompt exists for"

    def test_all_three_residual_paragraphs_say_why_the_prompt_is_plural(self):
        for label, text in (
                ("harness/guidance.py", guidance.__doc__ or ""),
                ("README.md",
                 (REPO_ROOT / "README.md").read_text(encoding="utf-8")),
                ("DESIGN.md",
                 (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8"))):
            with self.subTest(doc=label):
                folded = " ".join(text.split())
                self.assertIn(
                    self.PLURAL_RATIONALE, folded,
                    f"{label} must say that the decoy is what makes a "
                    "contaminated control's context carry two magic words, "
                    "and that a one-word answer from such a control is the "
                    "case the plural guard prompt exists for")

    def test_the_extent_docstring_says_the_unit_differs_from_the_js(self):
        # N-b. The arithmetic matches check-guidance-coverage.js; the UNIT
        # does not (characters here, Buffer.byteLength there). Pin the clause
        # so the next reader does not "fix" one side to match the other.
        doc = guidance.h2_extents.__doc__
        self.assertIn("byteLength", doc)
        self.assertIn("CHARACTER", doc)
        self.assertIn("test_extents_agree_with_the_real_manifests_generated_bytes",
                      doc)

    def test_unknown_mode_is_rejected_by_name(self):
        root = self._checkout()
        with self.assertRaises(guidance.GuidanceError) as ctx:
            guidance.assemble(root, self._row(root, "alpha"), "sekshun")
        self.assertIn("sekshun", str(ctx.exception))

    def test_missing_checkout_names_the_flag(self):
        missing = Path(tempfile.mkdtemp()) / "not-a-checkout"
        self.addCleanup(shutil.rmtree, missing.parent, ignore_errors=True)
        with self.assertRaises(guidance.GuidanceError) as ctx:
            guidance.require_guidance_dir(missing)
        self.assertIn("--guidance", str(ctx.exception))
        self.assertIn("AGENT_GUIDANCE_DIR", str(ctx.exception))

    def test_guidance_dir_resolution_order(self):
        base = REPO_ROOT
        self.assertEqual(
            guidance.resolve_guidance_dir("/tmp/flag", "/tmp/env", base),
            Path("/tmp/flag"))
        self.assertEqual(
            guidance.resolve_guidance_dir(None, "/tmp/env", base),
            Path("/tmp/env"))
        self.assertEqual(
            guidance.resolve_guidance_dir(None, None, base),
            (base / ".." / "_agent-guidance").resolve())

    def test_extents_agree_with_the_real_manifests_generated_bytes(self):
        # The cross-repo join: _agent-guidance generates each row's `bytes`
        # with its own scripts/check-guidance-coverage.js, and this harness
        # slices the same extent to build a payload. If the two ever disagree,
        # a `section` arm delivers something other than the bytes the
        # retirement gate weighed.
        if not (REAL_GUIDANCE_DIR / guidance.MANIFEST_REL).is_file():
            reason = (f"no _agent-guidance checkout at {REAL_GUIDANCE_DIR} — "
                      "skipping the cross-repo extent/bytes agreement check")
            print(reason)
            self.skipTest(reason)
        manifest = guidance.load_manifest(REAL_GUIDANCE_DIR)
        self.assertTrue(manifest)
        for row in manifest:
            if row.get("bytes") is None:
                continue
            with self.subTest(section=row["id"]):
                text = (REAL_GUIDANCE_DIR / row["file"]).read_text(encoding="utf-8")
                extent = guidance._extent_of(text, row["heading"], row["file"])
                self.assertEqual(
                    len(text[extent["start"]:extent["end"]].encode("utf-8")),
                    row["bytes"],
                    f"extent for {row['id']} disagrees with the manifest's "
                    "generated byte count")

    def test_the_guard_probe_is_the_canary_probe(self):
        # The guard reuses run_canary.run_leg; it must also reuse the canary's
        # PROMPT and tool controls. Without the controls the model forages
        # with Read/Glob and "finds" the token in a file on disk, which makes
        # a contaminated arm look delivered — the one failure this guard
        # exists to catch.
        fixture = yaml.safe_load((CANARY_DIR / "fixture.yaml").read_text(encoding="utf-8"))
        self.assertEqual(" ".join(fixture["prompt"].split()),
                         " ".join(guidance.GUARD_PROMPT.split()))
        self.assertEqual(fixture["disallowed_tools"], guidance.GUARD_DISALLOWED_TOOLS)

    # ------------------------------------------------------------------
    # Item 3 — delivery, the production way (the real hook)
    # ------------------------------------------------------------------

    def _skip_without_real_hook(self, root: Path) -> None:
        if not (root / guidance.HOOK_REL).is_file():
            reason = (f"no fleet-memory.sh under {REAL_GUIDANCE_DIR} — "
                      "skipping the real-hook delivery checks")
            print(reason)
            self.skipTest(reason)

    def test_the_real_hook_writes_the_marked_block_into_the_scratch_config_dir(self):
        root = self._checkout()
        self._skip_without_real_hook(root)
        scratch = Path(tempfile.mkdtemp(prefix="guidance-deliver-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        home = scratch / "home"
        config = scratch / "config"
        home.mkdir()
        # A pre-existing user memory under this arm's OWN scratch HOME: the
        # hook is a guest in that file and must not touch it when
        # CLAUDE_CONFIG_DIR points elsewhere.
        (home / ".claude").mkdir()
        own_memory = home / ".claude" / "CLAUDE.md"
        own_memory.write_text("# my own memory\n\nkeep me\n", encoding="utf-8")
        before = own_memory.read_bytes()

        payload = guidance.assemble(root, self._row(root, "alpha"), "section",
                                    token="AAAAAAAA-1111")
        info = guidance.deliver(root, scratch=scratch, dest_dir=config,
                                home=home, payload=payload)

        delivered = (config / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(guidance.BEGIN_MARK, delivered)
        self.assertIn("<!-- END FLEET GUIDANCE -->", delivered)
        self.assertIn("fleet-guidance-version:", delivered,
                      "the hook's own version line must be in the block — "
                      "this is the real hook's output, not an imitation")
        self.assertIn("## Alpha", delivered)
        self.assertIn("The magic word is AAAAAAAA-1111.", delivered)
        self.assertTrue(info["installed"])
        self.assertEqual(info["bytes"], len(payload.encode("utf-8")))
        self.assertIn("fleet-guidance:", info["verdict"])
        self.assertNotIn("DEGRADED", info["verdict"])
        self.assertEqual(own_memory.read_bytes(), before,
                         "the hook must not touch a CLAUDE.md outside the "
                         "config dir it was pointed at")

    def test_mode_none_runs_no_hook_and_leaves_an_empty_config_dir(self):
        root = self._checkout()
        scratch = Path(tempfile.mkdtemp(prefix="guidance-deliver-none-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        home = scratch / "home"
        config = scratch / "config"
        home.mkdir()
        config.mkdir()
        info = guidance.deliver(root, scratch=scratch, dest_dir=config,
                                home=home, payload="")
        self.assertEqual(info["bytes"], 0)
        self.assertIsNone(info["verdict"])
        self.assertFalse((config / "CLAUDE.md").exists())

    def _stand_in_home(self) -> Path:
        """A temp directory standing in for the operator's HOME, carrying a
        user memory file — so the refusal branch can be exercised against
        something shaped exactly like the real `~/.claude` and made of
        nothing real.
        """
        home = Path(tempfile.mkdtemp(prefix="guidance-standin-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / ".claude").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text(
            "# a stand-in for the operator's own global memory\n\nkeep me\n",
            encoding="utf-8")
        return home

    def test_refuse_real_config_dir_rejects_both_of_its_arguments(self):
        # `_refuse_real_config_dir` is pure — it resolves paths, compares, and
        # either raises or returns — so it can be asserted on directly, with
        # no function that can write anywhere near the assertion. Both
        # arguments are checked, and both spellings of the home (the home
        # itself and its `.claude`) are refused.
        home = self._stand_in_home()
        scratch = Path(tempfile.mkdtemp(prefix="guidance-refuse-pure-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        with mock.patch.object(guidance.os.path, "expanduser",
                               lambda p: str(home) if p == "~" else p):
            for dest, given_home, what in (
                    (home / ".claude", scratch, "config dir"),
                    (home, scratch, "config dir"),
                    (scratch, home, "HOME"),
                    (scratch, home / ".claude", "HOME")):
                with self.subTest(dest=str(dest), home=str(given_home)):
                    with self.assertRaises(guidance.GuidanceError) as ctx:
                        guidance._refuse_real_config_dir(dest, given_home)
                    self.assertIn("refusing", str(ctx.exception))
                    self.assertIn(what, str(ctx.exception))
            # And the ordinary case is allowed through, so the assertions
            # above are not vacuously true of every input.
            guidance._refuse_real_config_dir(scratch / "config", scratch / "home")

    def test_delivery_refuses_the_real_config_dir(self):
        # NOTHING REAL IS IN REACH HERE, deliberately. This test used to hand
        # the operator's own `~/.claude` to the real `deliver()` with only the
        # production refusal between it and their user memory — so the
        # standard mutation for a defensive branch (delete the guard, run the
        # suite) DESTROYED a 56 KB `~/.claude/CLAUDE.md`, replacing it with
        # this test's own `payload="anything\n"` inside a fleet-guidance
        # block. The refusal is resolved against a PATCHED home instead: the
        # same production branch executes, over a stand-in home whose memory
        # file is asserted byte-identical afterwards — which stays true even
        # when the guard is reverted, because a reverted guard can then only
        # write into the stand-in.
        root = self._checkout()
        home = self._stand_in_home()
        memory = home / ".claude" / "CLAUDE.md"
        before = memory.read_bytes()
        scratch = Path(tempfile.mkdtemp(prefix="guidance-refuse-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        with mock.patch.object(guidance.os.path, "expanduser",
                               lambda p: str(home) if p == "~" else p):
            for dest, given_home in ((home / ".claude", scratch), (scratch, home)):
                with self.subTest(dest=str(dest)):
                    with self.assertRaises(guidance.GuidanceError) as ctx:
                        guidance.deliver(root, scratch=scratch, dest_dir=dest,
                                         home=given_home, payload="anything\n")
                    self.assertIn("refusing", str(ctx.exception))
        self.assertFalse((scratch / "CLAUDE.md").exists())
        self.assertEqual(
            memory.read_bytes(), before,
            "the stand-in user memory must be byte-identical — this is the "
            "assertion that has to survive the revert-the-guard mutation")

    # ------------------------------------------------------------------
    # Item 5 — the environment allowlist
    # ------------------------------------------------------------------

    def test_agent_env_is_an_allowlist_that_still_carries_anthropic_vars(self):
        scratch = Path(tempfile.mkdtemp(prefix="guidance-env-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        ambient = {
            "SKILLS_EVALS_AMBIENT_LEAK": "must not reach the child",
            "CLAUDE_CODE_ENTRYPOINT": "remote",
            "ANTHROPIC_AUTH_TOKEN": "sk-ant-oat01-fake",
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        }
        with mock.patch.dict(os.environ, ambient):
            env = guidance.agent_env(
                workspace=scratch / "ws", home=scratch / "home",
                tmpdir=scratch / "tmp", config_dir=scratch / "config",
                env_spec={"FIXTURE_VAR": "$WORKSPACE/bin", "PLAIN": "value"})
        self.assertNotIn("SKILLS_EVALS_AMBIENT_LEAK", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env,
                         "an arm that inherits the ambient session's CLAUDE_* "
                         "settings is not measuring the guidance")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "sk-ant-oat01-fake",
                         "eval.yml exports the bearer step-locally; the CLI "
                         "must still see it")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.anthropic.com")
        self.assertEqual(env["HOME"], str(scratch / "home"))
        self.assertEqual(env["TMPDIR"], str(scratch / "tmp"))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(scratch / "config"))
        self.assertIn("PATH", env)
        self.assertEqual(env["FIXTURE_VAR"], f"{scratch / 'ws'}/bin")
        self.assertEqual(env["PLAIN"], "value")

    def test_passthrough_is_exactly_the_committed_six(self):
        # A REGRESSION FLOOR, not a red-first test: PASSTHROUGH is correct
        # today and this pins the mutation that makes it wrong. Only the
        # allowlist's NEGATIVES were asserted, so adding GITHUB_TOKEN,
        # AWS_SECRET_ACCESS_KEY, SSH_AUTH_SOCK, HOSTNAME or
        # CLAUDE_CODE_REMOTE_SESSION_ID to this tuple left all 438 tests green
        # while every one of them reached a bypassPermissions agent in the
        # key-bearing lane. The exact tuple, in order, is the assertion —
        # exactly as EXTRA_PASSTHROUGH is pinned below.
        self.assertEqual(
            guidance.PASSTHROUGH,
            ("PATH", "LANG", "LC_ALL", "SHELL", "USER", "NODE_PATH"),
            "every name added here is inherited by name into a "
            "bypassPermissions agent that runs beside a minted Anthropic "
            "token; widening it is a security change, not a convenience")

    def test_extra_passthrough_is_empty_in_production(self):
        # Same lock harness/propagation/init_probe.py carries: the hermetic
        # suite widens this to let the fake CLI's mode variable through, and a
        # widening left behind would silently un-scrub every arm.
        self.assertEqual(guidance.EXTRA_PASSTHROUGH, ())

    def test_hosted_passthrough_is_exactly_the_remote_flag(self):
        # Same floor as PASSTHROUGH: a name added here reaches every guidance
        # arm, so the exact tuple is the assertion.
        self.assertEqual(guidance.HOSTED_PASSTHROUGH, ("CLAUDE_CODE_REMOTE",))

    def test_remote_flag_reaches_the_arm_only_when_the_host_sets_it(self):
        # CLI 2.1.305 refuses a hosted arm without CLAUDE_CODE_REMOTE ("Not
        # logged in"); the rest of the host's CLAUDE_* names stay out.
        scratch = Path(tempfile.mkdtemp(prefix="hosted-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        kwargs = dict(workspace=scratch / "ws", home=scratch / "home",
                      tmpdir=scratch / "tmp", config_dir=scratch / "config")
        hosted = {"CLAUDE_CODE_REMOTE": "true",
                  "CLAUDE_SESSION_INGRESS_TOKEN_FILE": "/example/token"}
        with mock.patch.dict(os.environ, hosted):
            env = guidance.agent_env(**kwargs)
        self.assertEqual(env["CLAUDE_CODE_REMOTE"], "true")
        self.assertNotIn("CLAUDE_SESSION_INGRESS_TOKEN_FILE", env)
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("CLAUDE_CODE_REMOTE", None)
            self.assertNotIn("CLAUDE_CODE_REMOTE", guidance.agent_env(**kwargs))

    # ------------------------------------------------------------------
    # Items 3-6 — a whole guidance run through the fake CLI
    # ------------------------------------------------------------------

    TOKEN = "TOKENAAA-9999"
    # The control arm's own token. Pinned SEPARATELY from TOKEN: if the two
    # collapsed, every control arm would be handed the treatment token and
    # report itself contaminated.
    DECOY = "DECOYBBB-1111"

    def _guidance_fixture(self, tmp: Path, **overrides) -> Path:
        eval_dir = tmp / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        fixture = {
            "subject": "guidance",
            "section": "alpha",
            "prompt": "Do the ordinary task the trap is hidden inside.",
            "arms": {"with_guidance": {"mode": "section"},
                     "without_guidance": {"mode": "none"}},
            "env": {"FAKE_CLAUDE_MODE": "guidance_probe"},
            "objective_checks": [
                {"id": "token-visible", "type": "transcript_matches",
                 "must_match": [self.TOKEN]},
            ],
        }
        fixture.update(overrides)
        (eval_dir / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
        return eval_dir

    def _run_main(self, argv_tail) -> tuple[int, str]:
        """run_eval.main() in-process, with the fake CLI and a fixed token."""
        import contextlib
        import io
        argv = ["run_eval.py", *[str(a) for a in argv_tail]]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE)}), \
                mock.patch.object(guidance, "new_token", lambda: self.TOKEN), \
                mock.patch.object(guidance, "new_decoy_token",
                                  lambda: self.DECOY), \
                contextlib.redirect_stdout(buf):
            rc = run_eval.main()
        return rc, buf.getvalue()

    # A hostile timeout knob is the one class of fixture bug that can HANG
    # the runner rather than fail it: with `validate_timeouts` mutated away
    # the value reaches `subprocess.run(timeout=...)` unchecked, and a
    # fake-claude guard probe stayed alive 498 s under `timeout=None`.
    # `_run_main` calls `run_eval.main()` IN-PROCESS, so nothing bounds it at
    # all — a reviewer running that mutation gets a hung suite, not a red
    # test. Every timeout row therefore goes through the real CLI entry point
    # in a child with an OUTER bound around it. 30s is ~30x what a rejection
    # at fixture load costs (measured: the whole battery in 2.1s) and is what
    # a reviewer's mutation run pays per hanging row.
    OUTER_BOUND_S = 30

    def _run_main_subprocess(self, argv_tail,
                             timeout: int = OUTER_BOUND_S) -> tuple[int, str]:
        """`python3 harness/run_eval.py ...` in a child, outer-bounded.

        No token patching: the rows that use this fail at fixture load, before
        a token is minted or a CLI is invoked, so there is nothing to pin.
        """
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"),
               *[str(a) for a in argv_tail]]
        env = dict(os.environ, CLAUDE_BIN=str(FAKE_CLAUDE))
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env,
                                  capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(
                f"run_eval.py did not return inside {timeout}s for "
                f"{' '.join(cmd[2:])} — a timeout knob reached "
                "subprocess.run() unchecked, which is the hang "
                "validate_timeouts exists to prevent")
        return proc.returncode, proc.stdout + proc.stderr

    @staticmethod
    def _only_run_dir(results_dir: Path, key: str) -> Path:
        runs = sorted(p for p in (results_dir / key).iterdir() if p.is_dir())
        assert len(runs) == 1, f"expected one run dir, got {runs}"
        return runs[0]

    def _summary(self, results_dir: Path, key: str, arm: str) -> dict:
        path = self._only_run_dir(results_dir, key) / arm / "summary.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_guidance_run_writes_the_documented_summary_fields(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-run-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp)
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 0, out)

        with_summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertEqual(with_summary["subject"], "guidance")
        self.assertEqual(with_summary["section"], "alpha")
        self.assertEqual(with_summary["mode"], "section")
        self.assertEqual(with_summary["delivery"], "user")
        self.assertGreater(with_summary["bytes"], 0)
        self.assertEqual(with_summary["guard"]["expected"], True)
        self.assertEqual(with_summary["guard"]["observed"], True)
        self.assertIsNone(with_summary["error"])
        self.assertTrue(all(c["passed"] for c in with_summary["objective_checks"]),
                        with_summary["objective_checks"])

        without = self._summary(results, "guidance/alpha", "without_guidance")
        self.assertEqual(without["mode"], "none")
        self.assertEqual(without["decoy"], self.DECOY)
        self.assertGreater(without["bytes"], 0,
                           "the control is delivered its decoy, so its "
                           "payload is not empty — the GUIDANCE bytes are")
        self.assertLess(without["bytes"], with_summary["bytes"],
                        "and the decoy is a single paragraph, nothing like a "
                        "section's payload")
        self.assertEqual(without["guard"]["expected"], True)
        self.assertEqual(without["guard"]["observed"], True,
                         "the control must report ITS OWN decoy — that is the "
                         "proof it read its own scratch user memory")
        self.assertEqual(without["guard"]["contaminated"], False,
                         "and must not report the treatment token")
        self.assertFalse(any(c["passed"] for c in without["objective_checks"]),
                         "the control arm must not see the TREATMENT token")

        report = (self._only_run_dir(results, "guidance/alpha")
                  / "report.md").read_text(encoding="utf-8")
        self.assertIn("guidance/alpha", report)
        self.assertIn("with_guidance=section", report,
                      "the report header must name the mode pair")
        self.assertIn("without_guidance=none", report)

    def test_a_guidance_run_records_the_retirement_pooling_key(self):
        # #124: the retirement gate pools an isolation run by the fixture
        # (its directory name and content digest), the section's extent and
        # the rest of the guidance, and reads judge figures only from runs
        # judged unconditionally. Every arm records all of it.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-poolkey-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp)
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 0, out)
        want = guidance.content_digests(root, self._row(root, "alpha"))
        for arm in ("with_guidance", "without_guidance"):
            summary = self._summary(results, "guidance/alpha", arm)
            with self.subTest(arm=arm):
                self.assertEqual(summary["fixture_name"], "eval")
                self.assertEqual(summary["guidance_digests"], want)
                self.assertIs(summary["judge_conditional"], False)
                # Not a Git checkout, so no fixture digest: never a made-up one.
                self.assertNotIn("fixture_digest", summary)

    def test_a_guard_miss_on_the_with_arm_is_inconclusive_and_exits_2(self):
        # The `with` arm's probe came back blind: the guidance did not reach
        # the agent, so whatever that arm scored is meaningless. It must be
        # INCONCLUSIVE with no score written — never PASS, never FAIL.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-guardmiss-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_blind"})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("INCONCLUSIVE", out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertEqual(summary["guard"], dict(summary["guard"],
                                                expected=True, observed=False))
        self.assertIsNone(summary["objective_checks"],
                          "no score may be written for an arm whose delivery "
                          "could not be proved")
        self.assertIsNone(summary["judge"])
        self.assertEqual(summary["error"]["type"], "guard_miss")

    # ------------------------------------------------------------------
    # A3 — a delivery that did not happen is not a guard question
    #
    # `deliver()` already computed `installed` — an OFFLINE proof that the
    # marked block reached the config dir — and `_run_guidance_arm` never read
    # it, nor the hook's returncode. Measured with a hook that prints
    # `fleet-guidance: current` and writes nothing: the run spent a real guard
    # call and reported `guard_miss`, the AMBIGUOUS message ("delivered but
    # not read, or never delivered"), for the one case that is unambiguous and
    # free to detect.
    # ------------------------------------------------------------------

    SABOTAGED_HOOK = (
        "#!/usr/bin/env bash\n"
        "# Prints the success verdict and installs nothing.\n"
        "echo 'fleet-guidance: current'\n"
    )
    FAILING_HOOK = (
        "#!/usr/bin/env bash\n"
        "echo 'fleet-guidance: DEGRADED — synthetic failure' >&2\n"
        "exit 1\n"
    )

    def _checkout_with_hook(self, source: str) -> Path:
        root = self._checkout()
        self._skip_without_real_hook(root)
        hook = root / ".claude" / "hooks" / "fleet-memory.sh"
        hook.write_text(source, encoding="utf-8")
        hook.chmod(0o755)
        return root

    def _delivery_failure_run(self, prefix: str, hook_source: str):
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout_with_hook(hook_source)
        log = tmp / "argv.jsonl"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_ARGV_LOG": str(log)})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        return rc, out, results, log

    def test_a_hook_that_installs_nothing_is_delivery_failed_not_guard_miss(self):
        rc, out, results, log = self._delivery_failure_run(
            "guidance-sabotage-", self.SABOTAGED_HOOK)
        self.assertEqual(rc, 2, out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertEqual(
            summary["error"]["type"], "delivery_failed",
            "an arm whose payload provably never reached its config dir is a "
            "DELIVERY failure, not the ambiguous guard_miss")
        self.assertFalse(summary["installed"])
        self.assertEqual(summary["hook_returncode"], 0,
                         "this hook exits 0 — it is `installed` that is false")
        self.assertIsNone(summary["guard"],
                          "no guard call is made once delivery is known to "
                          "have failed")
        self.assertIsNone(summary["objective_checks"])
        self.assertIsNone(summary["judge"])
        self.assertFalse(
            log.exists() and self._argv_lines(log),
            "a delivery that provably did not happen must cost ZERO CLI "
            "invocations — the proof is offline and free")

    def test_a_hook_that_exits_nonzero_is_delivery_failed(self):
        rc, out, results, log = self._delivery_failure_run(
            "guidance-hookfail-", self.FAILING_HOOK)
        self.assertEqual(rc, 2, out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertEqual(summary["error"]["type"], "delivery_failed")
        self.assertEqual(summary["hook_returncode"], 1)
        self.assertIsNone(summary["guard"])
        self.assertFalse(log.exists() and self._argv_lines(log))

    def test_an_honest_hook_records_installed_and_still_scores(self):
        # The other side: the real hook installs, `installed` and the hook's
        # returncode land in the arm's `extra` either way, and the run scores
        # exactly as it did before.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-installed-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp)
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 0, out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertTrue(summary["installed"])
        self.assertEqual(summary["hook_returncode"], 0)
        self.assertIsNone(summary["error"])
        self.assertIsNotNone(summary["objective_checks"])

    def test_a_contaminated_control_arm_is_inconclusive_and_exits_2(self):
        # The trap this whole subject exists for: on a machine carrying the
        # fleet hook, ~/.claude/CLAUDE.md IS the guidance, so a `none` arm can
        # be silently delivered-to and the A/B reports a null delta that reads
        # as "the guidance does nothing". Simulated here with an ambient
        # memory file the fake CLI loads regardless of the arm's config dir.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-contam-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        # The control arm's decoy is delivered through the real hook.
        self._skip_without_real_hook(root)
        ambient = tmp / "ambient-CLAUDE.md"
        ambient.write_text(f"The magic word is {self.TOKEN}.\n", encoding="utf-8")
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_AMBIENT_MEMORY": str(ambient)})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "without_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("INCONCLUSIVE", out)
        summary = self._summary(results, "guidance/alpha", "without_guidance")
        # The control reports BOTH tokens: its own decoy (it read its scratch
        # memory) and the treatment token (something else delivered the
        # guidance to it). The second is what condemns the arm.
        self.assertTrue(summary["guard"]["expected"])
        self.assertTrue(summary["guard"]["observed"])
        self.assertTrue(summary["guard"]["contaminated"])
        self.assertFalse(summary["guard"]["ok"])
        self.assertEqual(summary["error"]["type"], "guard_contaminated")
        self.assertIsNone(summary["objective_checks"])

    def test_a_treatment_arm_that_reports_the_controls_decoy_is_contaminated(self):
        # N4. The forbidden-token side was one-directional: only a `none` arm
        # was given a token it must NOT report, so the OTHER direction —
        # a treatment arm that also reads the CONTROL's scratch user memory —
        # scored clean. It is the same per-arm isolation failure seen from
        # the other side, and just as fatal to the pair: the two arms are
        # then not measuring two different contexts. Measured on c5ea933 with
        # this exact fixture: rc 0, every check passing.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-contam-treatment-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        # An ambient file carrying the CONTROL's decoy, loaded by the fake CLI
        # regardless of the arm's config dir — the leak, pointed the other way.
        ambient = tmp / "ambient-CLAUDE.md"
        ambient.write_text(f"The magic word is {self.DECOY}.\n", encoding="utf-8")
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_AMBIENT_MEMORY": str(ambient)})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(
            rc, 2, "a treatment arm whose probe reported the control's decoy "
                   f"must be INCONCLUSIVE, never scored (stdout: {out!r})")
        self.assertIn("INCONCLUSIVE", out)
        treatment = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertTrue(treatment["guard"]["observed"],
                        "it did read its own payload — that is not the "
                        "problem")
        self.assertTrue(treatment["guard"]["contaminated"],
                        "and it also read the control's decoy, which nothing "
                        "delivered to it")
        self.assertFalse(treatment["guard"]["ok"])
        self.assertEqual(treatment["error"]["type"], "guard_contaminated")
        self.assertIsNone(treatment["objective_checks"],
                          "no score may be written for an arm whose "
                          "isolation provably did not hold")
        # The control in the SAME run is untouched: it reports its own decoy
        # (twice, from its scratch memory and from the ambient file) and
        # nothing it was not delivered.
        control = self._summary(results, "guidance/alpha", "without_guidance")
        self.assertTrue(control["guard"]["observed"])
        self.assertFalse(control["guard"]["contaminated"])

    def test_an_uncontaminated_treatment_arm_reports_only_its_own_token(self):
        # The other side of N4, so a guard that called every arm contaminated
        # would not satisfy the test above.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-clean-treatment-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp)
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 0, out)
        treatment = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertFalse(treatment["guard"]["contaminated"],
                         "a treatment arm that reads only its own payload is "
                         "not contaminated by the decoy check")
        self.assertTrue(treatment["guard"]["ok"])
        self.assertNotIn(self.DECOY, treatment["guard"]["reply"],
                         "and the decoy was never in its context")

    # ------------------------------------------------------------------
    # S-A — the guard prompt asks for EVERY magic word, and the fake CLI
    # obeys the prompt rather than a behaviour the prompt never requests
    # ------------------------------------------------------------------

    def test_the_guard_prompt_asks_for_every_magic_word(self):
        # The prompt used to ask for "that magic word" — ONE — which was
        # right while a contaminated control's context carried exactly one
        # (the treatment token) and had to report it. The DECOY made it two,
        # and gave the treatment token somewhere to hide.
        folded = " ".join(guidance.GUARD_PROMPT.split())
        marker = self._fake_claude_plural_marker()
        self.assertIn(
            marker, folded,
            "GUARD_PROMPT must ask for EVERY magic word: with a decoy in "
            "context a contaminated control carries two, and a probe obeying "
            "a singular question can report the decoy alone")
        self.assertIn("NO-MAGIC-WORD", folded,
                      "and must keep the sentinel the guard and the bridge "
                      "canary both read")

    @staticmethod
    def _fake_claude_plural_marker() -> str:
        """test/fake-claude's PLURAL_MARKER, read with `ast`.

        The fake keys its answer arity off this constant. Read back rather
        than restated so the prompt and the fake cannot drift apart and leave
        the suite measuring an arity the production prompt never asks for.
        """
        tree = ast.parse(FAKE_CLAUDE.read_text(encoding="utf-8"))
        values = [node.value.value for node in ast.walk(tree)
                  if isinstance(node, ast.Assign)
                  and isinstance(node.value, ast.Constant)
                  for target in node.targets
                  if isinstance(target, ast.Name)
                  and target.id == "PLURAL_MARKER"]
        assert len(values) == 1, f"expected one PLURAL_MARKER, got {values}"
        return values[0]

    def test_a_contaminated_control_is_caught_by_a_prompt_obeying_probe(self):
        # S-A, end to end. `guidance_probe_literal` answers the question it
        # was ASKED: every magic word when the prompt asks for every one of
        # them, otherwise the first it finds. Under the singular prompt on
        # c5ea933 it reported the decoy alone and this run scored 0 — a
        # contaminated control, clean. Under the plural prompt it reports
        # both, and the forbidden-token side has something to catch.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-literal-contam-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        ambient = tmp / "ambient-CLAUDE.md"
        ambient.write_text(f"The magic word is {self.TOKEN}.\n", encoding="utf-8")
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe_literal",
                      "FAKE_CLAUDE_AMBIENT_MEMORY": str(ambient)})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "without_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(
            rc, 2, "a contaminated control must never score clean, whatever "
                   f"arity its probe answers with (stdout: {out!r})")
        self.assertIn("INCONCLUSIVE", out)
        summary = self._summary(results, "guidance/alpha", "without_guidance")
        self.assertEqual(summary["error"]["type"], "guard_contaminated")
        self.assertTrue(summary["guard"]["contaminated"])
        self.assertIsNone(summary["objective_checks"])

    def test_a_clean_control_still_scores_under_a_prompt_obeying_probe(self):
        # The other side: with nothing contaminating it, the same probe
        # reports its own decoy and the arm is fine. Without this, a fake that
        # condemned every arm would satisfy the test above.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-literal-clean-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe_literal"})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "without_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 0, out)
        summary = self._summary(results, "guidance/alpha", "without_guidance")
        self.assertTrue(summary["guard"]["observed"])
        self.assertFalse(summary["guard"]["contaminated"])

    def test_a_control_arm_whose_probe_is_blind_is_inconclusive_too(self):
        # THE POINT OF THE DECOY. Before it, `mode: none` delivered nothing,
        # so the control's probe could only ever answer "no magic word" — the
        # same answer it gives when the arm IS contaminated, and the same
        # answer it gives when the arm never read its own memory at all. The
        # control's guard therefore passed unconditionally: this exact run
        # scored clean. Now the control is delivered a decoy of its own, so a
        # probe that reads nothing is caught the same way a treatment arm's is.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-blindcontrol-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_blind"})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "without_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("INCONCLUSIVE", out)
        summary = self._summary(results, "guidance/alpha", "without_guidance")
        self.assertEqual(summary["mode"], "none")
        self.assertTrue(summary["guard"]["expected"])
        self.assertFalse(summary["guard"]["observed"])
        self.assertFalse(summary["guard"]["contaminated"])
        self.assertEqual(summary["error"]["type"], "guard_miss")
        self.assertIsNone(summary["objective_checks"],
                          "no score may be written for a control arm that "
                          "never proved it reads its own scratch memory")

    def test_the_control_arms_decoy_is_delivered_through_the_real_hook(self):
        # The decoy travels the SAME path as a treatment payload — the real
        # fleet-memory.sh, into the arm's own scratch config dir — not a
        # shortcut the harness writes itself.
        root = self._checkout()
        self._skip_without_real_hook(root)
        scratch = Path(tempfile.mkdtemp(prefix="guidance-decoy-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        config, home = scratch / "config", scratch / "home"
        for path in (config, home):
            path.mkdir(parents=True)
        decoy = guidance.new_decoy_token()
        info = guidance.deliver(root, scratch=scratch, dest_dir=config,
                                home=home, payload=guidance.assemble(
                                    root, self._row(root, "alpha"), "none",
                                    token=decoy))
        self.assertTrue(info["installed"],
                        "the real hook must install a block that carries only "
                        "the decoy paragraph")
        self.assertEqual(info["returncode"], 0)
        delivered = (config / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(guidance.BEGIN_MARK, delivered)
        self.assertIn(decoy, delivered)
        self.assertNotIn("alpha section body", delivered,
                         "and no guidance with it")

    def test_a_guard_that_cannot_run_is_inconclusive_not_a_skipped_guard(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-guarderr-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp, env={"FAKE_CLAUDE_MODE": "error"})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 2, out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertIsNone(summary["guard"]["observed"])
        self.assertEqual(summary["error"]["type"], "guard_error")
        self.assertIsNone(summary["objective_checks"])

    def _argv_lines(self, log: Path) -> list[dict]:
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_guidance_arms_get_user_project_and_skill_arms_keep_project(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-argv-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        log = tmp / "argv.jsonl"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_ARGV_LOG": str(log)})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 0, out)
        calls = self._argv_lines(log)
        self.assertEqual(len(calls), 4, "two arms x (guard probe + agent)")
        for call in calls:
            with self.subTest(argv=call["argv"]):
                argv = call["argv"]
                self.assertIn("--setting-sources", argv)
                self.assertEqual(argv[argv.index("--setting-sources") + 1],
                                 "user,project",
                                 "a guidance arm must read USER memory, which "
                                 "is where the fleet hook delivers")
                self.assertNotIn("SKILLS_EVALS_AMBIENT_LEAK", call["env_keys"])

        # And the skill subject is untouched: still `project`.
        #
        # The two FAKE_CLAUDE_* names reach the child through the fixture's
        # own `env:` block, not through the runner's ambient environment.
        # Since #81 `run_eval.agent_env` is an allowlist and neither name is
        # on it, so setting them in `os.environ` alone left the stand-in
        # writing no log at all and this test died on a FileNotFoundError
        # rather than on anything it was asserting. `env:` is applied last
        # and always survives, which is the seam a fixture is meant to use —
        # so the fixture is copied out and given one rather than the
        # allowlist being widened for a test's convenience. CLAUDE_BIN stays
        # ambient: the harness reads it itself and never passes it on.
        skill_log = tmp / "skill-argv.jsonl"
        skill_eval = tmp / "skill-eval"
        shutil.copytree(example_evals.FIXTURES / "workflow-path-audit"
                        / "required-check-routing", skill_eval)
        skill_fixture = yaml.safe_load(
            (skill_eval / "fixture.yaml").read_text(encoding="utf-8"))
        skill_fixture["env"] = {"FAKE_CLAUDE_MODE": "agent",
                                "FAKE_CLAUDE_ARGV_LOG": str(skill_log)}
        (skill_eval / "fixture.yaml").write_text(
            yaml.safe_dump(skill_fixture, sort_keys=False), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE)}):
            proc = subprocess.run(
                [sys.executable, str(HARNESS_DIR / "run_eval.py"),
                 str(skill_eval),
                 "--arm", "without_skill", "--no-judge",
                 "--results-dir", str(tmp / "skill-results")],
                cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        skill_calls = self._argv_lines(skill_log)
        self.assertEqual(len(skill_calls), 1, "one agent call, no guard probe")
        argv = skill_calls[0]["argv"]
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")

    def test_delivery_project_falls_back_to_workspace_memory(self):
        # The documented fallback for a CLI that does not read memory from
        # CLAUDE_CONFIG_DIR: same real hook, pointed at the workspace, read as
        # PROJECT memory — and `delivery: project` recorded in every summary.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-project-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        log = tmp / "argv.jsonl"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_ARGV_LOG": str(log)})
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root, "--delivery", "project",
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 0, out)
        summary = self._summary(results, "guidance/alpha", "with_guidance")
        self.assertEqual(summary["delivery"], "project")
        self.assertTrue(summary["guard"]["observed"])
        for call in self._argv_lines(log):
            argv = call["argv"]
            self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")

    def test_a_whole_run_never_touches_the_real_user_memory(self):
        # The narrow half: one guidance run, snapshotted around its own call.
        # The guarantee the issue asks for is the RUN-WIDE one below, taken in
        # test/run_tests.py's main() around every test in the suite.
        real = Path(os.path.expanduser("~")) / ".claude" / "CLAUDE.md"
        before = real.read_bytes() if real.is_file() else None
        tmp = Path(tempfile.mkdtemp(prefix="guidance-realhome-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp)
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 0, out)
        after = real.read_bytes() if real.is_file() else None
        self.assertEqual(after, before,
                         f"{real} changed across a guidance run — every arm "
                         "gets a scratch config dir precisely so this file is "
                         "never delivered to")

    # ------------------------------------------------------------------
    # S2 — the user-memory snapshot spans the WHOLE run, not one test
    #
    # The assertion above takes its before/after around its OWN _run_main
    # call, so it stayed green while a different test in this same module
    # destroyed a 56 KB `~/.claude/CLAUDE.md` (the round-1 reviewer's
    # revert-the-guard mutation, which is a mutation the next reviewer will
    # run too). The snapshot that actually discharges the issue's "never
    # write to the real ~/.claude/CLAUDE.md; the test asserts it" is taken in
    # test/run_tests.py's main(), around build_suite() AND the runner, so a
    # write from ANY test in the suite fails the run.
    # ------------------------------------------------------------------

    MEMORY_PROBE = ISSUES_DIR / "test_issue_zz_memory_probe.py"
    MEMORY_PROBE_SOURCE = (
        "import os\n"
        "import unittest\n\n\n"
        "class MemoryProbe(unittest.TestCase):\n"
        "    def test_writes_the_file_the_runner_watches(self):\n"
        "        # Planted by TestIssue97 to drive the run-wide guard red.\n"
        "        # $SKILLS_EVALS_USER_MEMORY redirects the watched path to a\n"
        "        # temp file, so proving the guard fires costs nothing real.\n"
        "        with open(os.environ['SKILLS_EVALS_USER_MEMORY'], 'w',\n"
        "                  encoding='utf-8') as fh:\n"
        "            fh.write('clobbered by the memory probe\\n')\n"
    )

    def test_the_run_wide_user_memory_guard_fails_a_run_that_writes_the_file(self):
        self._skip_in_child()
        tmp = Path(tempfile.mkdtemp(prefix="memory-guard-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        watched = tmp / "CLAUDE.md"
        watched.write_text("stand-in user memory\n", encoding="utf-8")
        self.assertFalse(self.MEMORY_PROBE.exists(),
                         f"{self.MEMORY_PROBE} is left over from an earlier run")
        # Planted into a SCRATCH discovery dir (#182), never the real
        # test/issues/ — see _scratch_discovery_dir. Same reasoning as the
        # discovery probe above: a concurrent child suite from another
        # parallel worker discovers whatever lives under the real directory.
        # `-k` narrows the child to the probe alone, so the run is one test
        # long and the guard is the only thing that can make it exit 1.
        scratch = self._scratch_discovery_dir(
            "issue97-memory-probe-",
            **{self.MEMORY_PROBE.stem: self.MEMORY_PROBE_SOURCE})
        proc = self._run_suite(env_extra={MEMORY_ENV: str(watched),
                                          DISCOVERY_ENV: str(scratch)},
                               args=("-k", self.MEMORY_PROBE.stem))
        output = proc.stdout + proc.stderr
        self.assertTrue(
            re.search(r"^OK", output, flags=re.MULTILINE),
            "every test in the child run must PASS — the exit status under "
            "test comes from the run-wide guard alone, not from a failing "
            f"test\n{output[-3000:]}")
        self.assertEqual(self._ran_counts(output), [1],
                         "the child must have run exactly the probe\n"
                         f"{output[-3000:]}")
        self.assertEqual(
            proc.returncode, 1,
            "a run that changed the watched user-memory file must exit 1 even "
            f"though every test passed; got {proc.returncode}\n{output[-3000:]}")
        self.assertIn(str(watched), output,
                      "the failure must NAME the file that changed")
        self.assertNotIn("clobbered by the memory probe", output,
                         "the guard reports digests and the path, never the "
                         "file's contents")

    def test_an_ordinary_run_leaves_the_watched_file_alone(self):
        # The other side of the pin: with the watched path redirected and a
        # module that writes nothing in place of the probe, the same narrowed
        # child run exits 0. Without this, a guard that failed every run
        # would satisfy the test above.
        self._skip_in_child()
        tmp = Path(tempfile.mkdtemp(prefix="memory-guard-clean-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        watched = tmp / "CLAUDE.md"
        watched.write_text("stand-in user memory\n", encoding="utf-8")
        before = watched.read_bytes()
        self.assertFalse(self.MEMORY_PROBE.exists(),
                         f"{self.MEMORY_PROBE} is left over from an earlier run")
        clean = "test_issue_zz_memory_clean"
        scratch = self._scratch_discovery_dir(
            "issue97-memory-clean-", **{clean: self.CLEAN_SOURCE})
        proc = self._run_suite(env_extra={MEMORY_ENV: str(watched),
                                          DISCOVERY_ENV: str(scratch)},
                               args=("-k", clean))
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, output[-3000:])
        self.assertEqual(self._ran_counts(output), [1],
                         "the child must have run exactly the clean module\n"
                         f"{output[-3000:]}")
        self.assertEqual(watched.read_bytes(), before)

    def test_the_run_wide_user_memory_guard_names_its_override(self):
        # The child run above sets $SKILLS_EVALS_USER_MEMORY by literal name.
        # Pin that run_tests.py reads that same spelling, so renaming the knob
        # on one side turns this red instead of quietly making the proof
        # above test nothing.
        source = (TEST_DIR / "run_tests.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned = [n.value.value for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Constant)
                    for t in n.targets
                    if isinstance(t, ast.Name) and t.id == "USER_MEMORY_ENV"]
        self.assertEqual(assigned, [MEMORY_ENV],
                         "test/run_tests.py must define USER_MEMORY_ENV as "
                         f"{MEMORY_ENV!r}")

    def test_the_discovery_override_names_its_env_var(self):
        # Mirrors the pin above: the two planting tests set
        # $SKILLS_EVALS_DISCOVERY_DIR by literal name, so pin that
        # run_tests.py reads that same spelling — renaming the knob on one
        # side must turn this red instead of quietly making the two planting
        # pins plant into a scratch dir the child never looks at (#182).
        source = (TEST_DIR / "run_tests.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned = [n.value.value for n in ast.walk(tree)
                    if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Constant)
                    for t in n.targets
                    if isinstance(t, ast.Name) and t.id == "DISCOVERY_ENV"]
        self.assertEqual(assigned, [DISCOVERY_ENV],
                         "test/run_tests.py must define DISCOVERY_ENV as "
                         f"{DISCOVERY_ENV!r}")

    # ------------------------------------------------------------------
    # A2 — a timeout knob is a positive number, or it is absent
    #
    # `(fixture.get("guard") or {}).get("timeout_s", 300)` and
    # `fixture.get("timeout_s", 600)` return the VALUE when the key is
    # present, so an explicit YAML null (`guard:\n  timeout_s:`) yielded None
    # and `subprocess.run(timeout=None)` waited forever — measured against a
    # fake CLI that never returns; the only backstop in CI is the 45-minute
    # job kill, with no summary and no artifact. A string yielded a TypeError
    # traceback and rc 1, outside the GuidanceError contract (rc 2, a named
    # message, no traceback).
    # ------------------------------------------------------------------

    # Long enough that a run which reaches the CLI at all does not finish
    # inside this test's own patience — so a regression shows up as the outer
    # timeout firing, never as a passing test. That outer timeout is
    # `_run_main_subprocess`'s: every row below runs in a child, because
    # in-process there is no outer timeout at all and the mutation these rows
    # exist to catch hangs the suite instead of failing it.
    HANG = {"FAKE_CLAUDE_MODE": "timeout", "FAKE_CLAUDE_SLEEP": "600"}

    def _timeout_fixture(self, tmp: Path, **overrides) -> Path:
        return self._guidance_fixture(tmp, env=dict(self.HANG), **overrides)

    def test_a_null_or_bad_timeout_is_a_named_configuration_error(self):
        root = self._checkout()
        for label, overrides in (
                ("guard.timeout_s: null", {"guard": {"timeout_s": None}}),
                ("guard.timeout_s: abc", {"guard": {"timeout_s": "abc"}}),
                ("guard.timeout_s: 0", {"guard": {"timeout_s": 0}}),
                ("guard.timeout_s: -1", {"guard": {"timeout_s": -1}}),
                ("timeout_s: null", {"timeout_s": None}),
                ("timeout_s: abc", {"timeout_s": "abc"}),
                ("timeout_s: 0", {"timeout_s": 0}),
                ("timeout_s: -1", {"timeout_s": -1}),
                ("setup_timeout_s: null", {"setup_timeout_s": None}),
                ("judge.timeout_s: null", {"judge": {"timeout_s": None}}),
        ):
            with self.subTest(label=label):
                tmp = Path(tempfile.mkdtemp(prefix="guidance-timeout-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                eval_dir = self._timeout_fixture(tmp, **overrides)
                # In a CHILD with an outer bound, never `_run_main`: with
                # `validate_timeouts` mutated away these values reach
                # `subprocess.run(timeout=...)` and the run does not come
                # back — measured, a fake-claude guard probe alive 498 s
                # under `timeout=None`. In-process that hangs the whole
                # suite, which is not a result a reviewer can read.
                rc, out = self._run_main_subprocess(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", tmp / "results", "--no-judge"])
                self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                self.assertIn("positive number", out, f"{label}: {out}")
                self.assertIn("timeout_s", out, f"{label}: {out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")


    # ------------------------------------------------------------------
    # A-N1 — a present mapping-typed fixture key IS a mapping
    #
    # `validate_timeouts` walked to a knob's parent and, when the parent was
    # not a dict, set `node = {}` and broke — it NORMALISED the bad container
    # away rather than rejecting it. `guard: [1]` and `guard: 'x'` therefore
    # passed validation and reached `(fixture.get("guard") or
    # {}).get("timeout_s", 300)`: rc 1 and an `AttributeError` traceback,
    # outside the rc-2 named-message contract. `judge: [1]` degraded silently
    # inside the judge's `except Exception` instead.
    #
    # The same defect lives one key over on `env:`, which is not a timeout
    # parent at all: `(env_spec or {}).items()` on a present non-mapping is
    # the identical traceback (measured: rc 1, `'list' object has no
    # attribute 'items'`). MAPPING_FIXTURE_KEYS covers every knob parent —
    # derived from TIMEOUT_KNOBS, so a new nested knob brings its parent with
    # it — plus `env`.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # A-N1-2 — the fixture CONTAINER, and the `env:` entries inside it
    #
    # A-N1 typed the mapping-valued KEYS. The container that holds them and
    # the entries inside `env:` are the same defect one level out and one
    # level in, and both were rc 1 with a traceback on f9115ce:
    #   * a fixture whose root is a LIST — `AttributeError: 'list' object has
    #     no attribute 'get'`;
    #   * an EMPTY fixture file (YAML `None`) — `TypeError: argument of type
    #     'NoneType' is not iterable`, raised INSIDE A-N1's own
    #     `_require_mapping`;
    #   * `env: {"A=B": "x"}` — `ValueError: illegal environment variable
    #     name`, out of subprocess, after the arm had started.
    # The container is typed in `load_fixture` (all three CLIs have one) and
    # in `_require_mapping` itself, so a direct caller is sound too; the
    # entries are checked by one predicate at every function that builds a
    # child environment out of them, not at the loader alone.
    # ------------------------------------------------------------------

    BAD_FIXTURE_ROOTS = (
        ("a list root", "- a\n- b\n", "must be a YAML mapping"),
        ("an empty file", "", "the file is empty"),
        ("a scalar root", "just a string\n", "must be a YAML mapping"),
        ("a null root", "~\n", "the file is empty"),
    )

    # Every shape `execve` refuses, and only those: an int VALUE is
    # stringified rather than rejected, which is agent_env's documented
    # behaviour and is pinned by two committed tests
    # (AgentEnvTests.test_workspace_and_existing_vars_expand and
    # test_validate_timeouts_itself_refuses_a_non_mapping_parent), so the
    # check is on the stringified forms the OS actually sees.
    BAD_ENV_ENTRIES = (
        ("a name with an =", {"A=B": "x"}, "not a legal environment"),
        ("an empty name", {"": "x"}, "not a legal environment"),
        ("a NUL in the name", {"A\0B": "x"}, "not a legal environment"),
        ("a NUL in the value", {"A": "x\0y"}, "contains a NUL byte"),
    )

    # ------------------------------------------------------------------
    # A-N2-2 — every path an arm name becomes, not just the arm directory
    #
    # A-N2 modelled ONE path: the arm dir under the run dir. Measured on
    # f9115ce, both rc 1 with a traceback:
    #   * an arm named `report.md` — the run's own report is written into the
    #     run directory beside the arm dirs, so `_render_report` opened a
    #     DIRECTORY for writing (`IsADirectoryError`) AFTER both arms had been
    #     spent and their summaries written;
    #   * `a` * 255 / 256 / 4096 — `OSError: [Errno 36] File name too long`,
    #     raised by the per-arm workspace's mkdtemp, whose prefix makes it the
    #     tightest consumer of the name rather than the arm dir.
    # ------------------------------------------------------------------

    def test_the_arm_name_refusal_covers_every_file_the_run_writes(self):
        """The classification is derived from the code, not remembered: every
        file this module opens for writing is named either in RUN_DIR_FILES
        (beside the arm dirs, so an arm of that name collides) or in
        ARM_DIR_FILES (inside an arm dir, so it cannot)."""
        source = (HARNESS_DIR / "run_eval.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        constants = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                constants[node.targets[0].id] = node.value.value

        def terminal(expr, bindings):
            """The last path component of `expr`, as a filename or None."""
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
                return terminal(expr.right, bindings)
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                return expr.value
            if isinstance(expr, ast.Name):
                if expr.id in constants:
                    return constants[expr.id]
                if expr.id in bindings:
                    return terminal(bindings[expr.id], bindings)
            return None

        written = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bindings = {}
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    bindings[node.targets[0].id] = node.value
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "id", None) == "open"):
                    continue
                mode = (node.args[1].value if len(node.args) > 1
                        and isinstance(node.args[1], ast.Constant) else "r")
                if "w" not in str(mode) and "a" not in str(mode):
                    continue
                name = terminal(node.args[0], bindings)
                self.assertIsNotNone(
                    name,
                    f"run_eval.py:{node.lineno} opens a path for writing whose "
                    "filename this pin cannot read. Name it with a module "
                    "constant so it can be classified.")
                written.add(name)

        self.assertTrue(written, "no write-open found in run_eval.py — this "
                                 "pin must not pass vacuously")
        classified = set(run_eval.RUN_DIR_FILES) | set(run_eval.ARM_DIR_FILES)
        self.assertEqual(
            sorted(written - classified), [],
            f"{sorted(written - classified)}: run_eval.py writes these files "
            "and neither RUN_DIR_FILES nor ARM_DIR_FILES names them, so "
            "nothing says whether an arm of the same name would collide with "
            "one. Classify each: beside the arm dirs (RUN_DIR_FILES, and the "
            "arm-name check then refuses it) or inside one (ARM_DIR_FILES).")
        self.assertEqual(
            sorted(set(run_eval.RUN_DIR_FILES) - written), [],
            "RUN_DIR_FILES names a file run_eval.py no longer writes")

        # ...and every RUN_DIR_FILES name really is refused as an arm name.
        for name in run_eval.RUN_DIR_FILES:
            with self.subTest(arm=name):
                with self.assertRaises(guidance.GuidanceError) as caught:
                    run_eval._validate_arm_entry(name, {"mode": "none"})
                self.assertIn("run directory", str(caught.exception))

    def test_the_arm_name_length_cap_is_the_workspace_prefixs_own_limit(self):
        # Derived, not picked: the cap is what leaves the per-arm workspace
        # inside a 255-byte filesystem component once mkdtemp's prefix and its
        # 8 random characters are added.
        longest = "a" * run_eval.MAX_ARM_NAME_LEN
        run_eval._validate_arm_entry(longest, {"mode": "none"})
        probe = f"{run_eval.ARM_WORKSPACE_PREFIX}{longest}-XXXXXXXX"
        self.assertLessEqual(
            len(probe), 255,
            "the accepted longest arm name still makes a workspace directory "
            f"name of {len(probe)} bytes")
        for over in (run_eval.MAX_ARM_NAME_LEN + 1, 255, 256):
            with self.subTest(length=over):
                with self.assertRaises(guidance.GuidanceError) as caught:
                    run_eval._validate_arm_entry("a" * over, {"mode": "none"})
                self.assertIn("at most", str(caught.exception))

    def test_a_colliding_or_overlong_arm_name_is_refused_before_anything_runs(self):
        root = self._checkout()
        for label, arm_name in (("report.md", "report.md"),
                                ("a*255", "a" * 255),
                                ("a*256", "a" * 256)):
            with self.subTest(row=label):
                tmp = Path(tempfile.mkdtemp(prefix="arm-name-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                argv_log = tmp / "argv.jsonl"
                results = tmp / "results"
                eval_dir = self._guidance_fixture(
                    tmp, arms={arm_name: {"mode": "section"},
                               "control": {"mode": "none"}},
                    env={"FAKE_CLAUDE_MODE": "guidance_probe",
                         "FAKE_CLAUDE_ARGV_LOG": str(argv_log)})
                rc, out = self._run_main_subprocess(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", results, "--no-judge"])
                self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                self.assertIn("invalid arm name", out, f"{label}: {out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")
                self.assertFalse(argv_log.exists(),
                                 f"{label}: the CLI was invoked before the "
                                 "arm name was checked")
                self.assertFalse(results.exists(),
                                 f"{label}: a refused arm name writes nothing")

    def test_every_committed_arm_name_is_still_accepted(self):
        seen = 0
        for path in sorted((REPO_ROOT / "evals").rglob("fixture.yaml")):
            fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
            # Guidance fixtures only: `_validate_arm_entry` is the guidance
            # subject's gate, and a skill fixture's `arms:` entries are a
            # different shape with no `mode:`.
            if fixture.get("subject") != "guidance":
                continue
            for name, entry in (fixture.get("arms") or {}).items():
                seen += 1
                with self.subTest(fixture=str(path.parent), arm=name):
                    run_eval._validate_arm_entry(name, entry)
        self.assertGreater(seen, 0, "no committed guidance arm names — this "
                                    "floor would pass vacuously")

    def test_a_fixture_whose_root_is_not_a_mapping_is_a_named_error(self):
        root = self._checkout()
        for label, text, expected in self.BAD_FIXTURE_ROOTS:
            with self.subTest(row=label):
                tmp = Path(tempfile.mkdtemp(prefix="fixture-root-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                eval_dir = tmp / "eval"
                eval_dir.mkdir()
                (eval_dir / "fixture.yaml").write_text(text, encoding="utf-8")
                results = tmp / "results"
                rc, out = self._run_main_subprocess(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", results, "--no-judge"])
                self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                self.assertIn(expected, out, f"{label}: {out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")
                self.assertFalse(results.exists(),
                                 f"{label}: a refused fixture writes nothing")

    def test_an_illegal_env_entry_is_a_named_error_before_any_cli_call(self):
        root = self._checkout()
        for label, block, expected in self.BAD_ENV_ENTRIES:
            with self.subTest(row=label):
                tmp = Path(tempfile.mkdtemp(prefix="fixture-env-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                results = tmp / "results"
                eval_dir = self._guidance_fixture(tmp, env=block)
                rc, out = self._run_main_subprocess(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", results, "--no-judge"])
                self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                self.assertIn(expected, out, f"{label}: {out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")
                self.assertFalse(results.exists(),
                                 f"{label}: a refused fixture writes nothing")

    def test_an_ordinary_env_block_and_an_absent_one_still_run(self):
        # The floor: `env: {}` and no `env:` at all are untouched by this, and
        # so is the one committed fixture that declares a real block.
        for label, block in (("an empty block", {}), ("absent", None)):
            with self.subTest(row=label):
                guidance.check_env_block(block, "probe")
        guidance.check_env_block({"PATH": "$WORKSPACE/bin:$PATH"}, "probe")
        # An int value and an int name are stringified, not refused: that is
        # agent_env's committed behaviour and two tests on f9115ce assert it.
        guidance.check_env_block({"N": 7, 5: "x"}, "probe")

    def test_require_mapping_refuses_a_non_mapping_fixture_directly(self):
        # The direct-call half: A-N1's own predicate assumed the shape of its
        # first argument, so an empty fixture file died with a TypeError
        # inside the thing added to keep shapes out of the harness.
        path = Path("/nonexistent/fixture.yaml")
        for bad in (None, [1], "x", 7):
            with self.subTest(fixture=bad):
                with self.assertRaises(guidance.GuidanceError) as caught:
                    run_eval._require_mapping(bad, "guard", path)
                self.assertIn("expected a mapping", str(caught.exception))

    NON_MAPPINGS = ([1], "x", 7)

    def test_a_non_mapping_fixture_key_is_a_named_configuration_error(self):
        root = self._checkout()
        for key in run_eval.MAPPING_FIXTURE_KEYS:
            for value in self.NON_MAPPINGS:
                with self.subTest(key=key, value=value):
                    tmp = Path(tempfile.mkdtemp(prefix="fixture-mapping-"))
                    self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                    argv_log = tmp / "argv.jsonl"
                    results = tmp / "results"
                    # The argv log reaches the CLI through the fixture's own
                    # `env:` block — the allowlist strips it from the ambient
                    # environment — so the `env:` rows, whose env block IS
                    # the hostile value, prove "nothing ran" by the results
                    # directory never being created instead.
                    overrides = {"env": {"FAKE_CLAUDE_MODE": "guidance_probe",
                                         "FAKE_CLAUDE_ARGV_LOG": str(argv_log)}}
                    overrides[key] = value
                    eval_dir = self._guidance_fixture(tmp, **overrides)
                    rc, out = self._run_main_subprocess(
                        [eval_dir, "--arm", "both", "--guidance", root,
                         "--results-dir", results, "--no-judge"])
                    label = f"{key}: {value!r}"
                    if key != "env":
                        self.assertFalse(
                            argv_log.exists(),
                            f"{label}: the CLI was invoked before the "
                            "fixture's shape was checked")
                    self.assertFalse(
                        results.exists(),
                        f"{label}: a refused fixture must write nothing")
                    self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                    self.assertIn("must be a mapping", out, f"{label}: {out}")
                    self.assertIn(f"`{key}:`", out, f"{label}: the rejection "
                                  f"must name the key\n{out}")
                    self.assertNotIn("Traceback", out, f"{label}: {out}")

    def test_an_explicit_null_or_absent_mapping_key_still_runs_the_arm(self):
        # The other side, and the reason the check accepts null: every
        # `(fixture.get(k) or {})` read already treats null as absent, and
        # every committed fixture that omits the key must be untouched.
        # `env:` is exercised at the validator level in the test below
        # instead — dropping this fixture's `env:` block would take the fake
        # CLI's mode with it, so the arm could not be probed and the run
        # would be INCONCLUSIVE for a reason that has nothing to do with the
        # check under test.
        root = self._checkout()
        self._skip_without_real_hook(root)
        for label, overrides in (("guard: null", {"guard": None}),
                                 ("judge: null", {"judge": None}),
                                 ("absent", {})):
            with self.subTest(label=label):
                tmp = Path(tempfile.mkdtemp(prefix="fixture-mapping-ok-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                eval_dir = self._guidance_fixture(tmp, **overrides)
                rc, out = self._run_main(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", tmp / "results", "--no-judge"])
                self.assertEqual(rc, 0, f"{label}: {out}")

    def test_validate_timeouts_itself_refuses_a_non_mapping_parent(self):
        # The unit-level row, and the one the brief's mutation targets:
        # restoring `node = {}` in the parent walk leaves the rows above
        # green (validate_mapping_keys in main() catches them) and turns this
        # one red. validate_timeouts has to be sound for a direct caller too.
        path = Path("fixture.yaml")
        for _key, parents in run_eval.TIMEOUT_KNOBS:
            for parent in parents:
                for value in self.NON_MAPPINGS:
                    with self.subTest(parent=parent, value=value):
                        with self.assertRaises(guidance.GuidanceError) as ctx:
                            run_eval.validate_timeouts({parent: value}, path)
                        self.assertIn("must be a mapping", str(ctx.exception))
                        self.assertIn(parent, str(ctx.exception))
        # And accepts what it must — every mapping key, null and absent.
        run_eval.validate_timeouts({"guard": None, "judge": None}, path)
        run_eval.validate_timeouts({"guard": {"timeout_s": 10}}, path)
        for key in run_eval.MAPPING_FIXTURE_KEYS:
            with self.subTest(accepts=key):
                run_eval.validate_mapping_keys({key: None}, path)
                run_eval.validate_mapping_keys({}, path)
                run_eval.validate_mapping_keys({key: {"a": 1}}, path)
                with self.assertRaises(guidance.GuidanceError):
                    run_eval.validate_mapping_keys({key: [1]}, path)

    def test_every_timeout_knob_parent_is_a_checked_mapping_key(self):
        # Derivation, not repetition: a new nested knob cannot arrive with an
        # unchecked parent.
        parents = {parent for _key, ps in run_eval.TIMEOUT_KNOBS for parent in ps}
        self.assertTrue(parents, "TIMEOUT_KNOBS has no nested knob — this "
                        "assertion must not pass vacuously")
        self.assertTrue(parents <= set(run_eval.MAPPING_FIXTURE_KEYS),
                        f"{sorted(parents - set(run_eval.MAPPING_FIXTURE_KEYS))} "
                        "are timeout-knob parents that nothing type-checks")
        self.assertIn("env", run_eval.MAPPING_FIXTURE_KEYS,
                      "`env:` is read with `.items()` and has the identical "
                      "defect; it is not a timeout parent, so it is listed "
                      "explicitly")

    def test_a_valid_timeout_still_runs_the_arm(self):
        # The other side: a well-formed knob is untouched by the check. Uses
        # the ordinary probe CLI, not the hanging one.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-timeout-ok-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(tmp, timeout_s=3,
                                          guard={"timeout_s": 3})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 0, out)

    def test_a_skill_fixtures_timeout_knob_is_checked_the_same_way(self):
        # The same null hole is pre-existing on the skill leg (main's
        # `args.timeout or fixture.get("timeout_s", 600)`), so the check runs
        # at fixture load, before either subject branches.
        tmp = Path(tempfile.mkdtemp(prefix="skill-timeout-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = tmp / "eval"
        (eval_dir / "seed").mkdir(parents=True)
        (eval_dir / "fixture.yaml").write_text(yaml.safe_dump(
            {"skill": "a-skill", "prompt": "do the thing", "timeout_s": None},
            sort_keys=False), encoding="utf-8")
        rc, out = self._run_main_subprocess(
            [eval_dir, "--arm", "without_skill",
             "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("positive number", out)
        self.assertNotIn("Traceback", out)

    # ------------------------------------------------------------------
    # S1 — a timeout knob is bounded ABOVE as well as below
    #
    # `validate_timeouts` bounded each knob below and not above, so any value
    # in `job budget < t <= 2.147e6` was accepted and simply outlived
    # eval.yml's job budget — the job killed with no summary and no artifact,
    # which is the exact failure the rejection message describes for a null
    # and the one a reader steered by that message reaches for a very large
    # number to avoid. Above ~2.147e6 the value reaches `selector.poll` as
    # milliseconds: measured through the real CLI entry point with
    # `guard.timeout_s: 2200000`, rc 1, empty stdout, a bare
    # `OverflowError: timeout is too large`.
    # ------------------------------------------------------------------

    # Every knob TIMEOUT_KNOBS names, in the dotted spelling the rejection
    # message uses.
    CEILING_KNOBS = ("timeout_s", "setup_timeout_s", "guard.timeout_s",
                     "judge.timeout_s")
    # 2200000 is the round-2 measurement; 1e9 is the same class an order of
    # magnitude up; one second over the budget is the boundary row.
    ABOVE_THE_CEILING = (2200000, 10 ** 9, 2701)

    @staticmethod
    def _knob_override(knob: str, value) -> dict:
        if "." in knob:
            parent, leaf = knob.split(".", 1)
            return {parent: {leaf: value}}
        return {knob: value}

    def _job_budget_s(self) -> int:
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        return doc["jobs"]["eval"]["timeout-minutes"] * 60

    def test_the_timeout_ceiling_is_the_workflow_job_budget(self):
        # Two-sided anchoring, so the constant and the workflow cannot drift:
        # a ceiling ABOVE the job budget accepts knobs that still cannot
        # finish, and one BELOW it refuses fixtures the job has room for. The
        # budget is parsed with yaml.safe_load, never matched out of the text.
        budget = self._job_budget_s()
        self.assertIsInstance(budget, int)
        self.assertGreater(budget, 0,
                           "eval.yml's eval job must carry a timeout-minutes "
                           "— this assertion must not pass vacuously")
        self.assertEqual(
            run_eval.MAX_TIMEOUT_S, budget,
            "harness/run_eval.py's MAX_TIMEOUT_S must equal eval.yml's "
            "`timeout-minutes` x 60 for the eval job: it is the ceiling every "
            "timeout knob is checked against, and a knob larger than the job "
            "budget can only outlive it")

    def test_a_timeout_above_the_job_budget_is_a_named_configuration_error(self):
        root = self._checkout()
        ceiling = run_eval.MAX_TIMEOUT_S
        for knob in self.CEILING_KNOBS:
            for value in self.ABOVE_THE_CEILING:
                with self.subTest(knob=knob, value=value):
                    tmp = Path(tempfile.mkdtemp(prefix="guidance-ceiling-"))
                    self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                    argv_log = tmp / "argv.jsonl"
                    over = self._knob_override(knob, value)
                    eval_dir = self._guidance_fixture(
                        tmp, env=dict(self.HANG,
                                      FAKE_CLAUDE_ARGV_LOG=str(argv_log)),
                        **over)
                    rc, out = self._run_main_subprocess(
                        [eval_dir, "--arm", "both", "--guidance", root,
                         "--results-dir", tmp / "results", "--no-judge"])
                    label = f"{knob}={value}"
                    self.assertFalse(
                        argv_log.exists(),
                        f"{label}: the CLI was invoked before the knob was "
                        "checked — validation happens at fixture load, before "
                        "any subject branch or subprocess")
                    self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                    self.assertIn("positive number", out, f"{label}: {out}")
                    self.assertIn(knob, out, f"{label}: {out}")
                    self.assertIn(
                        str(ceiling), out,
                        f"{label}: the rejection must NAME the ceiling, so an "
                        f"operator knows what to write instead\n{out}")
                    self.assertNotIn("Traceback", out, f"{label}: {out}")
                    self.assertNotIn("OverflowError", out, f"{label}: {out}")

    def test_a_timeout_at_or_below_the_job_budget_still_runs_the_arm(self):
        # The other side, and why the bound is `<=` rather than `<`: a fixture
        # may legitimately spend the whole job budget on one knob. Driven with
        # the ordinary probe CLI, which returns at once, so an accepted knob
        # costs nothing to prove.
        root = self._checkout()
        self._skip_without_real_hook(root)
        for knob in self.CEILING_KNOBS:
            for value in (600, run_eval.MAX_TIMEOUT_S):
                with self.subTest(knob=knob, value=value):
                    tmp = Path(tempfile.mkdtemp(prefix="guidance-ceiling-ok-"))
                    self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                    eval_dir = self._guidance_fixture(
                        tmp, **self._knob_override(knob, value))
                    rc, out = self._run_main(
                        [eval_dir, "--arm", "both", "--guidance", root,
                         "--results-dir", tmp / "results", "--no-judge"])
                    self.assertEqual(rc, 0, f"{knob}={value}: {out}")


    # ------------------------------------------------------------------
    # S1-a — the ceiling is a HARNESS ceiling, not a fixture ceiling
    #
    # Round 2 put the bound in `validate_timeouts`, which sees the FIXTURE
    # dict and nothing else, and `--timeout` on the command line overrides
    # the value it just bounded (`args.timeout or fixture.get("timeout_s",
    # 600)`, at both call sites). So the defect S1 closed came straight back
    # through the flag beside it — measured on a6d165d through the real CLI
    # entry point: `--timeout 2200000` rc 1 and a bare `OverflowError`,
    # `--timeout 2701` rc 0 above the job budget, `--timeout 3000` against a
    # scored leg that never returns did not come back at all.
    #
    # The INVARIANT these three tests and the inventory below state together:
    # no value that reaches a subprocess timeout anywhere under harness/ may
    # be non-numeric, boolean, non-positive, non-finite or above
    # MAX_TIMEOUT_S, whatever its source — a fixture knob, a `--timeout`
    # flag on any of the three entry points that has one, an arm dict, or a
    # default.
    # ------------------------------------------------------------------

    def test_a_cli_timeout_override_is_held_to_the_same_ceiling(self):
        root = self._checkout()
        ceiling = run_eval.MAX_TIMEOUT_S
        # 2200000 is the OverflowError class; 1e9 the same an order up; 2701
        # one second over the job budget (rc 0 on a6d165d); 0 the value
        # `args.timeout or ...` silently swallowed as "no override"; -1 the
        # one a6d165d refused, but as a runner-level error rather than a
        # named configuration error.
        for value in (2200000, 10 ** 9, 2701, 0, -1):
            with self.subTest(value=value):
                tmp = Path(tempfile.mkdtemp(prefix="cli-timeout-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                argv_log = tmp / "argv.jsonl"
                # The ORDINARY probe CLI, not the hanging one: these rows
                # must be refused before a CLI is invoked at all, so the run
                # that proves it has to be one that would otherwise SUCCEED.
                # On a6d165d each row got as far as the CLI and showed its
                # own defect there — 2200000 and 1e9 rc 1 with a bare
                # OverflowError, 2701 rc 0 above the job budget, 0 silently
                # swallowed by `args.timeout or ...` and rc 0, -1 rc 2 but as
                # `Runner-level error in arm(s)` rather than a named
                # configuration error — and the argv log existed every time.
                eval_dir = self._guidance_fixture(
                    tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                              "FAKE_CLAUDE_ARGV_LOG": str(argv_log)})
                rc, out = self._run_main_subprocess(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", tmp / "results", "--no-judge",
                     "--timeout", value])
                label = f"--timeout {value}"
                self.assertFalse(
                    argv_log.exists(),
                    f"{label}: the CLI was invoked before the flag was "
                    "checked — the override is validated before any subject "
                    "branch and before any CLI call")
                self.assertEqual(rc, 2, f"{label}: expected rc 2\n{out}")
                self.assertIn("--timeout", out, f"{label}: the rejection must "
                              f"name the flag the operator typed\n{out}")
                self.assertIn("positive number", out, f"{label}: {out}")
                self.assertIn(
                    str(ceiling), out,
                    f"{label}: the rejection must NAME the ceiling, so an "
                    f"operator knows what to write instead\n{out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")
                self.assertNotIn("OverflowError", out, f"{label}: {out}")
                self.assertNotIn(
                    "Runner-level error", out,
                    f"{label}: a bad flag is a CONFIGURATION error named at "
                    f"parse time, not an arm that failed\n{out}")

    def test_a_cli_timeout_override_inside_the_ceiling_still_runs_the_arm(self):
        # The other side: a valid override is honoured exactly as before, so
        # the new predicate cannot be a blanket refusal that happens to make
        # the rows above pass.
        root = self._checkout()
        self._skip_without_real_hook(root)
        for value in (600, run_eval.MAX_TIMEOUT_S):
            with self.subTest(value=value):
                tmp = Path(tempfile.mkdtemp(prefix="cli-timeout-ok-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                eval_dir = self._guidance_fixture(tmp)
                rc, out = self._run_main(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", tmp / "results", "--no-judge",
                     "--timeout", value])
                self.assertEqual(rc, 0, f"--timeout {value}: {out}")

    def test_a_cli_timeout_over_the_budget_does_not_reach_a_hanging_leg(self):
        # The row with teeth. 3000 is between the job budget and the
        # OverflowError threshold, so a6d165d ACCEPTED it and handed it to
        # `subprocess.run(timeout=3000)` with a scored leg that never
        # returns: no rejection, no return, bounded in CI only by the
        # 45-minute job kill. Red on a6d165d as the child's own outer bound
        # firing, which `_run_main_subprocess` turns into a named failure.
        root = self._checkout()
        tmp = Path(tempfile.mkdtemp(prefix="cli-timeout-hang-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = self._guidance_fixture(tmp, env=dict(self.HANG))
        rc, out = self._run_main_subprocess(
            [eval_dir, "--arm", "both", "--guidance", root,
             "--results-dir", tmp / "results", "--no-judge",
             "--timeout", 3000])
        self.assertEqual(rc, 2, out)
        self.assertIn("--timeout", out, out)
        self.assertIn(str(run_eval.MAX_TIMEOUT_S), out, out)
        self.assertNotIn("Traceback", out, out)

    # ------------------------------------------------------------------
    # S1-a (b) — the INVENTORY. Every subprocess timeout under harness/ names
    # the validated source that bounds it.
    #
    # The two fixes above are line fixes; this is the invariant over the
    # surface. Twice now a bound has been added exactly where a review
    # pointed and the same defect has walked in through the door beside it —
    # the ceiling into the fixture knobs while `--timeout` overrode them
    # unchecked. So the sinks are ENUMERATED, by AST, and every one of them
    # has to say which validated source it is fed from. A new
    # `subprocess.run(..., timeout=x)` anywhere under harness/ fails this
    # test with its file and line until it is listed and justified.
    # ------------------------------------------------------------------

    # The call spellings that spawn a child, and the two Popen methods that
    # also carry a real subprocess timeout.
    SPAWN_NAMES = ("run", "Popen", "call", "check_call", "check_output")
    WAIT_NAMES = ("wait", "communicate")

    # The inventory. Key: (path relative to the repo root, enclosing
    # function, the call as spelled, the `timeout=` argument as source text).
    # Value: (how many identical call sites, the validated sources).
    #
    # A source is one of:
    #   ("literal",)          the argument IS a numeric literal; the test
    #                         checks it is positive and <= MAX_TIMEOUT_S
    #   ("constant", "<mod attr>")  a module-level constant in the sink's own
    #                         module; the test IMPORTS the module, reads the
    #                         attribute and runs the one predicate on its
    #                         value, so a constant edited above the ceiling
    #                         is red here as well as at the sink
    #   ("knob", "<dotted>")  a fixture knob TIMEOUT_KNOBS names, bounded by
    #                         validate_timeouts at fixture load
    #   ("flag", "<module>")  that module's `--timeout`, bounded by
    #                         guidance.check_timeout on `args.timeout` in its
    #                         own main() — the test parses the module and
    #                         requires that call to be there
    #   ("default", "<n>")    a keyword default in the callee's own
    #                         signature, no caller overriding it
    # Every source listed for a site must verify, so dropping any one of the
    # three predicates turns this test red and names the site.
    HARNESS_TIMEOUT_SINKS = {
        ("harness/scorers/commands.py", "_sandbox_prefix", "subprocess.run",
         "PROBE_TIMEOUT_S"): (1, (("constant", "PROBE_TIMEOUT_S"),)),
        # The compiled-prefix probe for a Ruby outside /usr (a relocated
        # `/opt/ruby-<v>`): one short-lived child, bounded by the same probe
        # constant as the sandbox probe.
        ("harness/scorers/commands.py", "_ruby_prefix", "<popen>.communicate",
         "PROBE_TIMEOUT_S"): (1, (("constant", "PROBE_TIMEOUT_S"),)),
        ("harness/scorers/commands.py", "_stop_process", "<popen>.wait",
         "CLEANUP_TIMEOUT_S"): (1, (("constant", "CLEANUP_TIMEOUT_S"),)),
        ("harness/scorers/commands.py", "_run_command", "<popen>.wait",
         "timeout_s"): (1, (("sink", "timeout_s"),)),
        ("harness/guidance.py", "deliver", "subprocess.run", "timeout"):
            (1, (("default", "120"),)),
        ("harness/propagation/arms.py", "_run_hook", "subprocess.run",
         "timeout"): (1, (("flag", "harness/run_propagation.py"),)),
        ("harness/propagation/arms.py", "arm_plugin_marketplace",
         "subprocess.run", "ctx.timeout"):
            (1, (("flag", "harness/run_propagation.py"),)),
        ("harness/propagation/init_probe.py", "probe", "<popen>.wait",
         "KILL_WAIT_TIMEOUT_S"): (1, (("constant", "KILL_WAIT_TIMEOUT_S"),)),
        ("harness/run_canary.py", "claude_version", "subprocess.run",
         "VERSION_TIMEOUT_S"): (1, (("constant", "VERSION_TIMEOUT_S"),)),
        # Two callers, two sources: run_canary's own `--timeout`, and
        # guidance.run_guard, which is handed the `guard.timeout_s` knob.
        ("harness/run_canary.py", "run_leg", "subprocess.run", "timeout"):
            (1, (("flag", "harness/run_canary.py"),
                 ("knob", "guard.timeout_s"))),
        # #202: the harness version, read once per run and recorded in every
        # arm's summary.json — the same shape as run_canary's.
        ("harness/run_eval.py", "claude_version", "subprocess.run",
         "VERSION_TIMEOUT_S"): (1, (("constant", "VERSION_TIMEOUT_S"),)),
        ("harness/run_eval.py", "run_setup", "subprocess.run", "timeout"):
            (1, (("knob", "setup_timeout_s"),)),
        # `args.timeout or fixture.get("timeout_s", 600)` — BOTH halves, which
        # is the whole of S1-a: round 2 validated the second and not the first.
        ("harness/run_eval.py", "run_agent", "subprocess.run", "timeout"):
            (1, (("flag", "harness/run_eval.py"), ("knob", "timeout_s"))),
        # The sink moved out of `score()` when main's #81 work extracted the
        # CLI call into `_run_judge_cli` — one function now hands a timeout
        # to subprocess for BOTH judge modes, absolute and pairwise, and it
        # is where the predicate sits. Same knob, same ceiling, one site.
        ("harness/scorers/judge.py", "_run_judge_cli", "subprocess.run",
         "timeout"): (1, (("knob", "judge.timeout_s"),)),
        # #343: all workspace Git commands and config parsing share this
        # process sink. Its parameter is checked here before any Git spawn.
        ("harness/workspace_git.py", "_invoke", "subprocess.run", "timeout"):
            (1, (("sink", "timeout"),)),
        ("harness/workspace_git.py", "_wait", "<popen>.wait", "timeout"):
            (1, (("sink", "timeout"),)),
    }

    @staticmethod
    def _harness_module(rel: str):
        """Import `harness/<a>/<b>.py` as the module the harness itself
        imports it as. Derived from the path, so a new sink module needs no
        entry anywhere."""
        import importlib
        name = rel[len("harness/"):-len(".py")].replace("/", ".")
        return importlib.import_module(name)

    @classmethod
    def _timeout_sinks(cls, path: Path) -> list[tuple[tuple, int]]:
        """Every subprocess-spawning call in `path` carrying `timeout=`, as
        ((relpath, function, spelling, argument source), lineno).

        Parsed, never matched out of the text, and deliberately generous
        about the SPELLING: `subprocess.run`, `sp.run` under
        `import subprocess as sp`, and a bare name bound by
        `from subprocess import run as r` all count, because a bound that
        only recognises the spelling in front of it is not a bound.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT).as_posix()
        module_aliases = {"subprocess"}
        bare = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        module_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for alias in node.names:
                    if alias.name in cls.SPAWN_NAMES:
                        bare[alias.asname or alias.name] = alias.name
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing(node) -> str:
            cur = parents.get(node)
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return cur.name
                cur = parents.get(cur)
            return "<module>"

        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func, spelling = node.func, None
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in module_aliases
                    and func.attr in cls.SPAWN_NAMES):
                spelling = f"subprocess.{func.attr}"
            elif isinstance(func, ast.Name) and func.id in bare:
                spelling = f"subprocess.{bare[func.id]}"
            elif isinstance(func, ast.Attribute) and func.attr in cls.WAIT_NAMES:
                spelling = f"<popen>.{func.attr}"
            if spelling is None:
                continue
            # A `**` splat and a POSITIONAL wait()/communicate() timeout are
            # both real subprocess timeouts and both were invisible to a walk
            # that filtered on `k.arg == "timeout"` — measured in round 4,
            # each planted as a new harness module and each leaving this
            # inventory green. A splat is recorded as `**<expr>`: nothing can
            # read a timeout out of it, so it can never be justified and the
            # row exists to say so.
            arguments = []
            for keyword in node.keywords:
                if keyword.arg == "timeout":
                    arguments.append(ast.unparse(keyword.value))
                elif keyword.arg is None:
                    arguments.append("**" + ast.unparse(keyword.value))
            if spelling == "<popen>.wait" and node.args:
                arguments.append(ast.unparse(node.args[0]))
            if spelling == "<popen>.communicate" and len(node.args) >= 2:
                arguments.append(ast.unparse(node.args[1]))
            for argument in arguments:
                found.append(((rel, enclosing(node), spelling, argument),
                              node.lineno))
        return found

    @staticmethod
    def _callers_passing_timeout(callee: str):
        """Every call to `callee` anywhere under harness/ that passes an
        explicit `timeout=`, as (relative path, line). Parsed, so the
        `("default", N)` rows' claim about callers is measured rather than
        asserted from memory."""
        found = []
        for path in sorted((REPO_ROOT / "harness").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO_ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", None))
                if name != callee:
                    continue
                if any(k.arg == "timeout" for k in node.keywords):
                    found.append((rel, node.lineno))
        return found

    @staticmethod
    def _main_checks_the_flag(module_rel: str) -> bool:
        """That module's own `main()` runs `check_timeout` on `args.timeout`.

        Parsed from the module's source, so the mutation the brief asks for —
        drop the predicate — makes every site that names this flag red, with
        the site's own file and line in the message.
        """
        tree = ast.parse((REPO_ROOT / module_rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "main"):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else getattr(func, "id", None))
                if name != "check_timeout":
                    continue
                if call.args and ast.unparse(call.args[0]) == "args.timeout":
                    return True
        return False

    @staticmethod
    def _main_validates_the_fixture() -> bool:
        """run_eval.main() runs `validate_timeouts` on the loaded fixture."""
        tree = ast.parse((REPO_ROOT / "harness" / "run_eval.py").read_text(
            encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "main"):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "id", None) == "validate_timeouts"
                        and call.args
                        and ast.unparse(call.args[0]) == "fixture"):
                    return True
        return False

    def test_every_harness_subprocess_timeout_names_its_validated_source(self):
        inventory = {}
        for path in sorted((REPO_ROOT / "harness").rglob("*.py")):
            for key, lineno in self._timeout_sinks(path):
                inventory.setdefault(key, []).append(lineno)
        self.assertTrue(
            inventory,
            "no subprocess timeout found anywhere under harness/ — this "
            "inventory must not be able to pass vacuously; the walk or the "
            "spellings it recognises have broken")

        unlisted = sorted(
            f"{key[0]}:{lineno} in {key[1]}() — {key[2]}(timeout={key[3]})"
            for key, lines in inventory.items()
            if key not in self.HARNESS_TIMEOUT_SINKS for lineno in lines)
        self.assertEqual(
            unlisted, [],
            "these subprocess timeouts under harness/ are in no inventory "
            "row, so nothing says which validated source bounds them. Add a "
            "row to HARNESS_TIMEOUT_SINKS naming that source — a fixture knob "
            "via validate_timeouts, a `--timeout` flag via "
            "guidance.check_timeout, a literal constant, or a callee default "
            f"— and say why it is bounded:\n  " + "\n  ".join(unlisted))
        gone = sorted(k for k in self.HARNESS_TIMEOUT_SINKS if k not in inventory)
        self.assertEqual(gone, [], "inventory rows that no longer name a real "
                                   "call site — delete them")

        ceiling = run_eval.MAX_TIMEOUT_S
        for key, (count, sources) in sorted(self.HARNESS_TIMEOUT_SINKS.items()):
            rel, function, spelling, argument = key
            where = f"{rel} {function}() {spelling}(timeout={argument})"
            with self.subTest(sink=where):
                self.assertEqual(
                    len(inventory[key]), count,
                    f"{where}: the inventory says {count} such call site(s), "
                    f"the tree has {len(inventory[key])} (lines "
                    f"{inventory[key]}). A new one needs its own "
                    "justification, not a bumped count.")
                self.assertTrue(sources, f"{where}: no source named")
                for source in sources:
                    kind = source[0]
                    if kind == "literal":
                        value = ast.literal_eval(argument)
                        self.assertIsInstance(value, (int, float), where)
                        self.assertGreater(value, 0, where)
                        self.assertLessEqual(
                            value, ceiling,
                            f"{where}: a literal timeout must itself be "
                            f"within the harness ceiling of {ceiling}s")
                    elif kind == "constant":
                        module = self._harness_module(rel)
                        value = getattr(module, source[1])
                        self.assertTrue(
                            guidance.timeout_is_sane(value),
                            f"{where}: {rel}'s {source[1]} is {value!r}, "
                            "which the one predicate refuses — a named "
                            "constant is only a bound while its VALUE is one")
                    elif kind == "default":
                        self.assertLessEqual(
                            float(source[1]), ceiling,
                            f"{where}: the callee's own default must be "
                            "within the ceiling")
                        # ...and no caller overrides it. The row CLAIMS "no
                        # caller passes timeout=", and until this assertion
                        # that claim was a comment: measured in round 4,
                        # `guidance.deliver(..., timeout=fixture.get(
                        # "deliver_timeout_s", 2200000))` left this test and
                        # the flag pin green and reproduced the OverflowError.
                        overriders = sorted(
                            f"{caller_rel}:{lineno}"
                            for caller_rel, lineno in
                            self._callers_passing_timeout(function))
                        self.assertEqual(
                            overriders, [],
                            f"{where}: the row says this sink is bounded by "
                            f"its own default of {source[1]}s and that no "
                            "caller overrides it, and these callers do: "
                            f"{overriders}. Either bound the value they pass "
                            "(and change the row's source), or stop passing "
                            "it.")
                    elif kind == "knob":
                        knob = source[1]
                        parents = tuple(knob.split(".")[:-1])
                        leaf = knob.split(".")[-1]
                        self.assertIn(
                            (leaf, parents), run_eval.TIMEOUT_KNOBS,
                            f"{where}: names the fixture knob {knob!r}, which "
                            "TIMEOUT_KNOBS does not carry — so "
                            "validate_timeouts never bounds it")
                        self.assertTrue(
                            self._main_validates_the_fixture(),
                            f"{where}: is bounded by the fixture knob {knob!r} "
                            "only while run_eval.main() still calls "
                            "validate_timeouts(fixture, ...); it does not")
                    elif kind == "flag":
                        module_rel = source[1]
                        self.assertTrue(
                            self._main_checks_the_flag(module_rel),
                            f"{where}: is fed by {module_rel}'s `--timeout`, "
                            "and that module's main() no longer runs "
                            "guidance.check_timeout on `args.timeout` — the "
                            "flag reaches subprocess.run unbounded, which is "
                            "the S1-a defect returning")
                    elif kind == "sink":
                        # Objective check constraints are validated at their
                        # execution sink, outside the top-level fixture knobs.
                        info = self._harness_timeout_sinks()[(rel, function)]
                        self.assertIn(source[1], info["checked"], where)
                        self.assertEqual(argument, source[1], where)
                        self.assertLess(info["first_check"], info["first_spawn"], where)
                    else:  # pragma: no cover — a typo in the table
                        self.fail(f"{where}: unknown source kind {kind!r}")

    def test_every_harness_timeout_flag_is_bounded_by_the_one_predicate(self):
        # The flags themselves, enumerated rather than assumed: any argparse
        # `--timeout` anywhere under harness/ must be run through
        # guidance.check_timeout in its own main(). Three today —
        # run_eval.py, run_canary.py, run_propagation.py — and round 3 found
        # the first of them unbounded after round 2 had bounded the knobs
        # beside it.
        flagged = []
        for path in sorted((REPO_ROOT / "harness").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "add_argument"):
                    continue
                if any(isinstance(a, ast.Constant) and a.value == "--timeout"
                       for a in node.args):
                    flagged.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertTrue(flagged, "no `--timeout` flag found under harness/ — "
                                 "this assertion must not pass vacuously")
        for module_rel in sorted(set(flagged)):
            with self.subTest(module=module_rel):
                self.assertTrue(
                    self._main_checks_the_flag(module_rel),
                    f"{module_rel} declares a `--timeout` flag whose value "
                    "reaches a subprocess timeout, but its main() never runs "
                    "guidance.check_timeout on `args.timeout`: argparse's "
                    "`type=int` bounds neither end, and a very large value "
                    "raises a bare OverflowError instead of naming a rule")

    # ------------------------------------------------------------------
    # S1-a-2 — the ceiling is enforced at the SINK, not at a table of sources
    #
    # Round 2 put the ceiling on the fixture knobs and `--timeout` walked past
    # it. Round 3 put the ceiling on `--timeout` and added an INVENTORY that
    # names, per call site, which validated source feeds it — and the
    # inventory checks that the source a row NAMES is validated, never that
    # the named source is the one actually feeding the call. Measured on
    # f9115ce: rebinding `run_agent`'s call site to an unvalidated fixture key
    # (`args.timeout or fixture.get("agent_timeout_s", 600)`) changed no
    # inventory key at all — the argument at the call site is still the local
    # name `timeout` — and left the inventory, the flag pin and all 120
    # TestIssue97 tests GREEN while a fixture with `agent_timeout_s: 2200000`
    # reproduced `OverflowError: timeout is too large`, rc 1. Two more
    # spellings the inventory cannot see at all: `opts = {"timeout": x};
    # subprocess.run(cmd, **opts)` (a `**` splat is `keyword(arg=None)`) and a
    # POSITIONAL timeout on `Popen.wait(...)`/`communicate(...)`.
    #
    # So the predicate now sits at the SINK: every function under harness/
    # that hands a timeout to a subprocess API calls guidance.check_timeout on
    # that value before it spawns, whatever the caller passed. The invariant
    # — no non-numeric, boolean, non-positive, non-finite or above-ceiling
    # value reaches a subprocess timeout anywhere under harness/, whatever its
    # source — is then true by construction rather than by a table of beliefs
    # about where values come from. The inventory above stays as
    # belt-and-braces; it is no longer the rope.
    # ------------------------------------------------------------------

    # The spellings that hand a value to a subprocess timeout. `**` splats and
    # the POSITIONAL argument of wait()/communicate() are here because the
    # round-3 walk filtered on `k.arg == "timeout"` and was blind to both.
    SINK_SPAWN_NAMES = ("run", "Popen", "call", "check_call", "check_output")
    SINK_WAIT_NAMES = ("wait", "communicate")

    class _SinkScan:
        """One parsed module under harness/: every function that hands a value
        to a subprocess timeout, and what it checks before it does.

        Deliberately generous about the spelling, for the same reason the
        fork scan is: a bound that only recognises the spelling in front of it
        is not a bound. `import subprocess as sp`, `from subprocess import run
        as r`, a name assigned `subprocess.run`, a `**` splat and a positional
        `wait(10)` all count.
        """

        def __init__(self, path, rel, spawn_names, wait_names):
            self.rel = rel
            self.spawn_names, self.wait_names = spawn_names, wait_names
            self.tree = ast.parse(path.read_text(encoding="utf-8"))
            self.aliases = {"subprocess"}
            self.bare = set()
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            self.aliases.add(alias.asname or alias.name)
                elif (isinstance(node, ast.ImportFrom)
                      and node.module == "subprocess"):
                    for alias in node.names:
                        if alias.name in spawn_names:
                            self.bare.add(alias.asname or alias.name)
                elif isinstance(node, ast.Assign):
                    value = node.value
                    if isinstance(value, ast.Name) and value.id in self.aliases:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                self.aliases.add(target.id)
                    if (isinstance(value, ast.Attribute)
                            and isinstance(value.value, ast.Name)
                            and value.value.id in self.aliases
                            and value.attr in spawn_names):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                self.bare.add(target.id)

        def spawn_kind(self, call):
            func = call.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in self.aliases
                    and func.attr in self.spawn_names):
                return f"subprocess.{func.attr}"
            if isinstance(func, ast.Name) and func.id in self.bare:
                return f"subprocess.{func.id}"
            if isinstance(func, ast.Attribute) and func.attr in self.wait_names:
                return f"<popen>.{func.attr}"
            return None

        def timeout_expressions(self, call, kind):
            """Every expression this call hands to a subprocess timeout.

            A `**` splat is included as `**<expr>`: it is a site whose timeout
            cannot be read off the source at all, so it must never match a
            checked expression and always turns the pin red until the caller
            is rewritten to pass the value plainly.
            """
            found = []
            for keyword in call.keywords:
                if keyword.arg == "timeout":
                    found.append(ast.unparse(keyword.value))
                elif keyword.arg is None:
                    found.append("**" + ast.unparse(keyword.value))
            if kind == "<popen>.wait" and call.args:
                found.append(ast.unparse(call.args[0]))
            if kind == "<popen>.communicate" and len(call.args) >= 2:
                found.append(ast.unparse(call.args[1]))
            return found

        def sinks(self):
            """{function name: {...}} for every function with a timeout sink."""
            out = {}
            for fn in ast.walk(self.tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                first_check = first_spawn = None
                handed, checked, sites = set(), set(), []
                for index, stmt in enumerate(fn.body):
                    for node in ast.walk(stmt):
                        if not isinstance(node, ast.Call):
                            continue
                        name = (node.func.attr
                                if isinstance(node.func, ast.Attribute)
                                else getattr(node.func, "id", None))
                        if name == "check_timeout":
                            if first_check is None:
                                first_check = index
                            if node.args:
                                checked.add(ast.unparse(node.args[0]))
                        kind = self.spawn_kind(node)
                        if kind is None:
                            continue
                        if first_spawn is None:
                            first_spawn = index
                        expressions = self.timeout_expressions(node, kind)
                        if expressions:
                            handed.update(expressions)
                            sites.append((node.lineno, kind))
                if handed:
                    out[fn.name] = {
                        "handed": handed, "checked": checked, "sites": sites,
                        "first_check": first_check, "first_spawn": first_spawn,
                        "lineno": fn.lineno}
            return out

    @classmethod
    def _harness_timeout_sinks(cls) -> dict:
        """Every (module, function) under harness/ that hands a value to a
        subprocess timeout, found by walking the TREE — every `*.py` under
        harness/ that rglob finds, `__pycache__` aside — rather than a list of
        files somebody remembered to keep up to date."""
        out = {}
        for path in sorted((REPO_ROOT / "harness").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            scan = cls._SinkScan(path, rel, cls.SINK_SPAWN_NAMES,
                                 cls.SINK_WAIT_NAMES)
            for name, info in scan.sinks().items():
                out[(rel, name)] = info
        return out

    def test_every_subprocess_timeout_sink_checks_the_value_before_it_spawns(self):
        """S1-a-2, the pin at the sink. Every function under harness/ that
        hands a value to a subprocess timeout runs guidance.check_timeout on
        THAT EXPRESSION, in a statement that precedes its first spawn.

        Three claims, each mechanically decided from the parse:
          * the sink list is not empty (the walk cannot pass vacuously);
          * each sink calls the predicate before it spawns, in statement
            order — so deleting the call from any one of them is red here;
          * the expressions checked COVER the expressions handed over, so a
            caller that rebinds the argument to something else is red too.
            A `**` splat can never be covered, which is the point: a timeout
            that cannot be read off the source must not reach a spawn.
        """
        sinks = self._harness_timeout_sinks()
        self.assertTrue(
            sinks,
            "no subprocess timeout sink found anywhere under harness/ — this "
            "pin must not be able to pass vacuously; the walk or the "
            "spellings it recognises have broken")
        for (rel, name), info in sorted(sinks.items()):
            where = f"{rel}:{info['lineno']} {name}()"
            with self.subTest(sink=where):
                self.assertIsNotNone(
                    info["first_check"],
                    f"{where} hands {sorted(info['handed'])} to a subprocess "
                    "timeout and never calls guidance.check_timeout. The "
                    "ceiling is enforced HERE, at the function that hands the "
                    "value to the OS, because a bound that only guards the "
                    "sources someone thought of is not a bound: measured on "
                    "f9115ce, rebinding one call site to an unvalidated "
                    "fixture key left every source-side pin green and "
                    "reproduced `OverflowError: timeout is too large`.")
                self.assertLess(
                    info["first_check"], info["first_spawn"],
                    f"{where} calls guidance.check_timeout only AFTER it has "
                    "already spawned — the check has to happen before the "
                    "value reaches the OS, not after")
                uncovered = sorted(info["handed"] - info["checked"])
                self.assertEqual(
                    uncovered, [],
                    f"{where} hands {uncovered} to a subprocess timeout, and "
                    f"checks {sorted(info['checked'])}. Every expression that "
                    "reaches `timeout=` must be the expression the predicate "
                    "was given — checking a DIFFERENT one is the round-3 "
                    "defect in a new costume. (A `**splat` can never be "
                    "covered: pass the timeout plainly instead.)")

    # How each sink is driven directly, and where its timeout comes from.
    # `param` = the named keyword argument; `const` = a module-level constant
    # this test patches. The LIST is not here — it is the walk above — and the
    # test refuses any sink the walk finds that has no driver, so a new sink
    # cannot arrive with no direct-call coverage.
    SINK_DRIVERS = {
        ("harness/scorers/commands.py", "_sandbox_prefix"):
            ("const", "PROBE_TIMEOUT_S"),
        ("harness/scorers/commands.py", "_ruby_prefix"):
            ("const", "PROBE_TIMEOUT_S"),
        ("harness/scorers/commands.py", "_stop_process"):
            ("const", "CLEANUP_TIMEOUT_S"),
        ("harness/scorers/commands.py", "_run_command"):
            ("param", "timeout_s"),
        ("harness/guidance.py", "deliver"): ("param", "timeout"),
        ("harness/propagation/arms.py", "_run_hook"): ("param", "timeout"),
        ("harness/propagation/arms.py", "arm_plugin_marketplace"):
            ("ctx", "timeout"),
        ("harness/propagation/init_probe.py", "probe"):
            ("const", "KILL_WAIT_TIMEOUT_S"),
        ("harness/run_canary.py", "claude_version"):
            ("const", "VERSION_TIMEOUT_S"),
        ("harness/run_canary.py", "run_leg"): ("param", "timeout"),
        ("harness/run_eval.py", "claude_version"): ("const", "VERSION_TIMEOUT_S"),
        ("harness/run_eval.py", "run_setup"): ("fixture", "setup_timeout_s"),
        ("harness/run_eval.py", "run_agent"): ("arm", "timeout"),
        ("harness/scorers/judge.py", "_run_judge_cli"): ("param", "timeout"),
        ("harness/workspace_git.py", "_invoke"): ("param", "timeout"),
        ("harness/workspace_git.py", "_wait"): ("param", "timeout"),
    }

    # These former process sinks now call the trusted Git boundary. Keep
    # driving their public timeout checks so moving the spawn does not erase
    # coverage of an invalid module constant at the original caller.
    GIT_TIMEOUT_CALLER_DRIVERS = {
        ("harness/run_eval.py", "_nested_repo_diff"): ("const", "GIT_TIMEOUT_S"),
        ("harness/scorers/objective.py", "git_ref_unchanged"):
            ("const", "GIT_TIMEOUT_S"),
        ("harness/scorers/objective.py", "git_remote_url_is"):
            ("const", "GIT_TIMEOUT_S"),
        ("harness/scorers/objective.py", "git_worktree_list_matches"):
            ("const", "GIT_TIMEOUT_S"),
        ("harness/scorers/objective.py", "reaper_ran_in_standalone_repo"):
            ("const", "GIT_TIMEOUT_S"),
    }

    # Every shape the one predicate refuses, as a caller could hand it over.
    BAD_SINK_TIMEOUTS = (2200000, True, 0, -1, float("nan"), float("inf"),
                         "600", None)
    GOOD_SINK_TIMEOUT = 600

    def _drive_sink(self, key, value, spawn_counter):
        """Call one sink function directly with `value` as its timeout.

        The arguments are deliberately minimal: every sink checks its timeout
        in a statement that precedes its first spawn (the pin above proves
        that from the parse), so a refused value is refused before any of
        them is looked at. `spawn_counter` replaces the sink module's own
        `subprocess.run`/`Popen`, so "nothing was spawned" is measured rather
        than assumed.
        """
        rel, name = key
        module = self._harness_module(rel)
        drivers = self.SINK_DRIVERS | self.GIT_TIMEOUT_CALLER_DRIVERS
        kind, knob = drivers[key]
        fn = getattr(module, name)
        tmp = Path(tempfile.mkdtemp(prefix="sink-driver-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(module.subprocess, "run", spawn_counter), \
                mock.patch.object(module.subprocess, "Popen", spawn_counter):
            if kind == "const":
                with mock.patch.object(module, knob, value):
                    return self._call_sink(rel, name, fn, tmp, None)
            return self._call_sink(rel, name, fn, tmp, (kind, knob, value))

    @staticmethod
    def _call_sink(rel, name, fn, tmp, supplied):
        """The one call per sink, with dummy arguments and the timeout (when
        it is not a module constant) supplied the way that sink takes it."""
        kwargs = {}
        if supplied is not None:
            kind, knob, value = supplied
            if kind == "param":
                kwargs[knob] = value
            elif kind == "ctx":
                import types
                kwargs["ctx"] = types.SimpleNamespace(**{knob: value})
            elif kind == "fixture":
                kwargs["fixture"] = {knob: value, "setup": "true"}
            elif kind == "arm":
                kwargs["arm"] = {"name": "without_skill", knob: value}
        if name == "_sandbox_prefix":
            return fn(tmp, tmp, {})
        if name == "_ruby_prefix":
            return fn(tmp / "ruby", tmp, None)
        if name == "_stop_process":
            # Never signal a real process in this direct-call driver.
            with mock.patch("os.killpg"):
                return fn(mock.MagicMock())
        if name == "_run_command":
            # Never signal a real process in this direct-call driver.
            with mock.patch("os.killpg"):
                return fn(["/usr/bin/true"], tmp, {}, None, None, **kwargs)
        if rel == "harness/guidance.py" and name == "deliver":
            return fn(tmp, scratch=tmp, dest_dir=tmp / "cfg", home=tmp / "home",
                      payload="x", **kwargs)
        if name == "_run_hook":
            return fn(tmp / "hook.sh", scratch=None, env_extra={}, **kwargs)
        if name == "arm_plugin_marketplace":
            return fn(**kwargs)
        if name == "probe":
            return fn(cwd=tmp, home=tmp, tmpdir=tmp)
        if name == "claude_version":
            return fn()
        if name == "run_leg":
            return fn(tmp, "p", "", model=None, **kwargs)
        if name == "run_setup":
            return fn(tmp, kwargs["fixture"])
        if name == "run_agent":
            return fn(tmp, "p", kwargs["arm"])
        if rel == "harness/workspace_git.py" and name == "_invoke":
            return fn(["version"], cwd=tmp, home=tmp, **kwargs)
        if rel == "harness/workspace_git.py" and name == "_wait":
            return fn(mock.MagicMock(), **kwargs)
        if name == "_nested_repo_diff":
            return fn(tmp, [])
        if name == "_run_judge_cli":
            return fn("a judge prompt", model=None, **kwargs)
        if name == "git_ref_unchanged":
            return fn(str(tmp), [], path=".", ref="HEAD", expected="x")
        if name == "git_remote_url_is":
            return fn(str(tmp), [], path=".", remote="origin", expected_path="x")
        if name == "git_worktree_list_matches":
            return fn(str(tmp), [], path=".", expected_names=[])
        if name == "reaper_ran_in_standalone_repo":
            return fn(str(tmp), [])
        raise AssertionError(f"no direct-call driver body for {rel}::{name}")

    def test_every_subprocess_timeout_sink_refuses_a_bad_value_before_it_spawns(self):
        """S1-a-2, driven rather than parsed. Every sink the walk finds, called
        directly with each shape the predicate refuses, raises the named error
        and spawns nothing — and accepts an ordinary 600.

        The sink LIST comes from the walk, not from SINK_DRIVERS: a new sink
        with no driver is a failure here, so direct-call coverage cannot fall
        behind the tree.
        """
        sinks = self._harness_timeout_sinks()
        self.assertTrue(sinks, "no subprocess timeout sink under harness/ — "
                               "this test must not pass vacuously")
        undriven = sorted(f"{rel}::{name}" for rel, name in sinks
                          if (rel, name) not in self.SINK_DRIVERS)
        self.assertEqual(
            undriven, [],
            f"{undriven} hand a value to a subprocess timeout and have no "
            "direct-call driver, so nothing proves the sink refuses a bad one "
            "before it spawns. Add a SINK_DRIVERS entry and a call body.")
        stale = sorted(f"{rel}::{name}" for rel, name in self.SINK_DRIVERS
                       if (rel, name) not in sinks)
        self.assertEqual(stale, [], f"{stale}: driver rows that name no sink "
                                    "the walk finds — delete them")

        spawned = []

        def spawn_counter(*args, **kwargs):
            spawned.append(args)
            raise AssertionError("a refused timeout reached a spawn")

        for key in sorted(sinks):
            for value in self.BAD_SINK_TIMEOUTS:
                with self.subTest(sink=f"{key[0]}::{key[1]}", value=value):
                    spawned.clear()
                    with self.assertRaises(guidance.GuidanceError) as caught:
                        self._drive_sink(key, value, spawn_counter)
                    message = str(caught.exception)
                    self.assertIn(
                        "positive number of seconds", message,
                        f"the refusal must be the TIMEOUT predicate's, not "
                        f"some other GuidanceError: {message}")
                    self.assertIn(key[1], message,
                                  f"the refusal must name the sink: {message}")
                    self.assertIn(str(guidance.MAX_TIMEOUT_S), message,
                                  f"the refusal must name the ceiling: {message}")
                    self.assertEqual(
                        spawned, [],
                        f"{key}: {value!r} reached a spawn before the sink "
                        "checked it")

    def test_every_subprocess_timeout_sink_accepts_an_ordinary_value(self):
        """The floor beside the test above: a sink whose predicate refused
        everything would pass that one and break the harness. 600 goes
        through every sink without a GuidanceError.

        Anything else a dummy-argument call raises (the sink got past its
        check and then found no workspace, no hook, no context — including a
        GuidanceError about one of those) is not this test's business: the
        ordering claim is the pin's, from the parse. Only a refusal by the
        TIMEOUT predicate fails here, recognised by the one sentence
        check_timeout writes and nothing else does.
        """
        for key in sorted(self._harness_timeout_sinks()):
            with self.subTest(sink=f"{key[0]}::{key[1]}"):
                try:
                    self._drive_sink(key, self.GOOD_SINK_TIMEOUT, mock.MagicMock())
                except Exception as exc:  # noqa: BLE001 — see the docstring
                    self.assertNotIn(
                        "positive number of seconds", str(exc),
                        f"{key} refused an ordinary "
                        f"{self.GOOD_SINK_TIMEOUT}s timeout: {exc}")

    def test_workspace_git_callers_keep_their_timeout_checks(self):
        """Moving Git spawns preserves the checks at the legacy callers.

        Both the check's ordering and the expression passed to the helper
        are parsed. The direct drivers below also prove an invalid constant
        cannot reach Git through any of these existing APIs.
        """
        for (rel, name), (_, constant) in sorted(
                self.GIT_TIMEOUT_CALLER_DRIVERS.items()):
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
            functions = [node for node in ast.walk(tree)
                         if isinstance(node, ast.FunctionDef) and node.name == name]
            self.assertEqual(len(functions), 1, (rel, name))
            checks, calls = [], []
            for index, statement in enumerate(functions[0].body):
                for node in ast.walk(statement):
                    if not isinstance(node, ast.Call):
                        continue
                    call_name = (node.func.attr if isinstance(node.func, ast.Attribute)
                                 else getattr(node.func, "id", None))
                    if call_name == "check_timeout" and node.args:
                        checks.append((index, ast.unparse(node.args[0])))
                    if (isinstance(node.func, ast.Attribute)
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "workspace_git"
                            and node.func.attr == "run"):
                        timeout_values = [ast.unparse(keyword.value)
                                          for keyword in node.keywords
                                          if keyword.arg == "timeout"]
                        self.assertEqual(timeout_values, [constant], (rel, name))
                        calls.append(index)
            self.assertTrue(calls, (rel, name))
            self.assertTrue(any(expression == constant and index < min(calls)
                                for index, expression in checks), (rel, name))

        def refused_spawn(*args, **kwargs):
            raise AssertionError("a refused caller timeout reached a spawn")

        for key in sorted(self.GIT_TIMEOUT_CALLER_DRIVERS):
            for value in self.BAD_SINK_TIMEOUTS:
                with self.subTest(caller=f"{key[0]}::{key[1]}", value=value):
                    with self.assertRaises(guidance.GuidanceError) as caught:
                        self._drive_sink(key, value, refused_spawn)
                    message = str(caught.exception)
                    self.assertIn("positive number of seconds", message)
                    self.assertIn(key[1], message)
                    self.assertIn(str(guidance.MAX_TIMEOUT_S), message)
            with self.subTest(caller=f"{key[0]}::{key[1]}", value=self.GOOD_SINK_TIMEOUT):
                try:
                    self._drive_sink(key, self.GOOD_SINK_TIMEOUT, mock.MagicMock())
                except Exception as exc:  # dummy workspace may be refused
                    self.assertNotIn("positive number of seconds", str(exc))

    def test_a_sink_still_spawns_with_an_ordinary_timeout(self):
        """Two real spawns, unmocked, so "refuses" above is never "refuses
        everything": `run_setup` runs a shell command and `_nested_repo_diff`
        runs git, both at their ordinary timeouts, both under this test's own
        mkdtemp."""
        tmp = Path(tempfile.mkdtemp(prefix="sink-positive-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        marker = tmp / "setup-ran"
        error = run_eval.run_setup(
            tmp, {"setup": f"touch {marker}", "setup_timeout_s": 600})
        self.assertIsNone(error, "an ordinary setup must run")
        self.assertTrue(marker.is_file(),
                        "run_setup must still spawn at an accepted timeout")
        subprocess.run(["git", "init", "-q", str(tmp)], check=True, timeout=30)
        subprocess.run(["git", "-C", str(tmp), "commit", "-q", "--allow-empty",
                        "-m", "seed"], check=True, timeout=30,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t",
                                GIT_AUTHOR_EMAIL="t@example.com",
                                GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@example.com"))
        diff = run_eval._nested_repo_diff(tmp, [tmp])
        self.assertIn("last commit", diff,
                      "_nested_repo_diff must still spawn git at its own "
                      f"{run_eval.GIT_TIMEOUT_S}s bound")

    def _harness_copy(self, replacements) -> Path:
        """A throwaway copy of harness/ with exact-string edits applied.

        This is the mutation-style half of S1-a-2: the rows below rebind a
        call site to a source no inventory row names, in a copy, and drive the
        REAL CLI against it. On f9115ce each of them is rc 1 and a bare
        `OverflowError: timeout is too large`; the sink check makes them rc 2
        and named without knowing anything about the new key.
        """
        tmp = Path(tempfile.mkdtemp(prefix="sink-mutation-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        shutil.copytree(HARNESS_DIR, tmp / "harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        # The copy reads its budget from its own root's evals/ (#68);
        # without one it stops at a configuration error before any sink.
        (tmp / "evals").mkdir()
        shutil.copy2(REPO_ROOT / "evals" / "budget.yml", tmp / "evals" / "budget.yml")
        for rel, old, new in replacements:
            path = tmp / "harness" / rel
            source = path.read_text(encoding="utf-8")
            self.assertIn(old, source, f"{rel}: the mutation's anchor is gone")
            path.write_text(source.replace(old, new), encoding="utf-8")
        return tmp / "harness" / "run_eval.py"

    def _run_copy(self, runner: Path, argv_tail) -> tuple[int, str]:
        if os.environ.get(CHILD_ENV):
            raise unittest.SkipTest("child suite run — guarded copied harness")
        cmd = [sys.executable, str(runner), *[str(a) for a in argv_tail]]
        env = dict(os.environ, CLAUDE_BIN=str(FAKE_CLAUDE))
        env[CHILD_ENV] = "1"
        try:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env,
                                  capture_output=True, text=True,
                                  timeout=self.OUTER_BOUND_S)
        except subprocess.TimeoutExpired:
            self.fail(f"{runner} did not return inside {self.OUTER_BOUND_S}s "
                      "— an unbounded timeout reached subprocess.run()")
        return proc.returncode, proc.stdout + proc.stderr

    def _scratch_skill_fixture(self, tmp: Path, **overrides) -> Path:
        """A minimal SKILL fixture with its own seed. `--arm without_skill`
        makes `run_agent` the FIRST subprocess this run reaches, so "0 CLI
        calls" is literal rather than "the guard leg ran first"."""
        eval_dir = tmp / "eval"
        (eval_dir / "seed").mkdir(parents=True, exist_ok=True)
        (eval_dir / "seed" / "placeholder.txt").write_text("x\n",
                                                           encoding="utf-8")
        # Pin the agent model so this test's standalone harness copy reaches
        # run_agent's timeout sink. Since #147, an unpinned skill fixture reads
        # the trusted evals/roster.yml; _harness_copy intentionally carries
        # harness/ alone, so leaving this unpinned would stop at model
        # selection and never exercise the sink this helper exists to test.
        fixture = {"skill": "some-skill", "prompt": "do the thing",
                   "model": "fixture-model"}
        fixture.update(overrides)
        (eval_dir / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
        return eval_dir

    # Each row: a label, the edit, and the fixture key that now feeds the
    # sink. Neither key exists in TIMEOUT_KNOBS, neither is a `--timeout`, and
    # neither changes any inventory key — which is exactly why a source-side
    # table cannot see them.
    REBOUND_SINK_SOURCES = (
        # A skill fixture, `--arm without_skill`: `run_agent` is the first
        # subprocess the run reaches, so "nothing was invoked" is literal.
        ("run_agent via an unvalidated agent_timeout_s", "skill",
         ("run_eval.py",
          '"timeout": args.timeout or fixture.get("timeout_s", 600),',
          '"timeout": args.timeout or fixture.get("agent_timeout_s", 600),'),
         "agent_timeout_s", "run_agent"),
        # The code half's N1, the `("default", 120)` row: `deliver`'s one
        # caller passes no `timeout=` at all, and the table said so in a
        # comment. Delivery precedes the guard, so this row is also
        # zero-CLI-calls.
        ("deliver via an unvalidated deliver_timeout_s", "guidance",
         ("run_eval.py",
          'dest_dir=config if channel == "user" else workspace)',
          'dest_dir=config if channel == "user" else workspace,\n'
          '            timeout=fixture.get("deliver_timeout_s", 2200000))'),
         "deliver_timeout_s", "deliver"),
    )

    def test_a_source_no_inventory_row_names_is_still_refused_at_the_sink(self):
        """S1-a-2's proof that the check is at the SINK and not at the sources.

        Both rows were measured GREEN on f9115ce through every source-side
        pin — the inventory, the `--timeout` predicate and all 120 TestIssue97
        tests — while the real CLI answered rc 1 with a bare `OverflowError:
        timeout is too large`. Nothing here teaches the harness about
        `agent_timeout_s` or `deliver_timeout_s`; the sink refuses them
        because it checks what it was handed.
        """
        root = self._checkout()
        for label, subject, edit, key, sink in self.REBOUND_SINK_SOURCES:
            with self.subTest(row=label):
                runner = self._harness_copy([edit])
                tmp = Path(tempfile.mkdtemp(prefix="sink-rebound-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                argv_log = tmp / "argv.jsonl"
                results = tmp / "results"
                env_block = {"FAKE_CLAUDE_MODE": "guidance_probe",
                             "FAKE_CLAUDE_ARGV_LOG": str(argv_log)}
                if subject == "skill":
                    eval_dir = self._scratch_skill_fixture(
                        tmp, env=env_block, **{key: 2200000})
                    argv_tail = [eval_dir, "--arm", "without_skill",
                                 "--results-dir", results, "--no-judge"]
                else:
                    eval_dir = self._guidance_fixture(
                        tmp, env=env_block, **{key: 2200000})
                    argv_tail = [eval_dir, "--arm", "both", "--guidance", root,
                                 "--results-dir", results, "--no-judge"]
                rc, out = self._run_copy(runner, argv_tail)
                self.assertEqual(rc, 2, f"{label}: expected the named rc-2 "
                                        f"configuration error\n{out}")
                self.assertIn("configuration error", out, f"{label}: {out}")
                self.assertIn(sink, out, f"{label}: the message must name the "
                                         f"sink it was refused at\n{out}")
                self.assertIn(str(guidance.MAX_TIMEOUT_S), out,
                              f"{label}: the message must name the ceiling\n{out}")
                self.assertNotIn("OverflowError", out, f"{label}: {out}")
                self.assertNotIn("Traceback", out, f"{label}: {out}")
                self.assertNotIn("Runner-level error", out, f"{label}: {out}")
                self.assertFalse(
                    argv_log.exists(),
                    f"{label}: the CLI was invoked before the sink checked "
                    "the timeout it was handed")


    # ------------------------------------------------------------------
    # F-1 — the harness reads guidance content only from INSIDE the checkout
    #
    # The manifest's `file:` is the one path harness/guidance.py builds out
    # of manifest DATA rather than a module constant, and nothing bounded it.
    # Measured on a6d165d: a row `file: ../OUTSIDE_SECRET.md` naming an
    # existing file outside the checkout was resolved, read, delivered to the
    # arm, and its content landed in
    # `results/guidance/<key>/<ts>/<arm>/transcripts/raw.json` at rc 0 — and
    # on main that directory is pushed to the PUBLIC `persistent/eval-results` branch.
    # The trust boundary eval.yml states is that guidance content is EXECUTED
    # by the arm; reading and publishing a file from outside the checkout is
    # a different thing and is not implied by it.
    # ------------------------------------------------------------------

    OUTSIDE_MARKER = "F1-OUTSIDE-CANARY-PAYLOAD"

    def _outside_file(self, tmp: Path) -> Path:
        """A readable file one level ABOVE the guidance checkout, carrying a
        marker nothing in the tree has. Its own mkdtemp, like every path
        these tests create.

        It carries a `## Alpha` heading on purpose: the fixture's section is
        `alpha`, so on a6d165d this file was a perfectly good section source
        — read, delivered and scored — rather than failing on a missing
        heading. The red these tests produce there is the real one.
        """
        outside = tmp / "OUTSIDE_SECRET.md"
        outside.write_text(
            f"# not guidance\n\n## Alpha\n\n{self.OUTSIDE_MARKER}\n",
            encoding="utf-8")
        return outside

    @staticmethod
    def _retarget_first_row(root: Path, value: str) -> None:
        manifest = root / "agents-md" / "eval-coverage.yml"
        rows = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        rows[0]["file"] = value
        manifest.write_text(yaml.safe_dump(rows, sort_keys=False),
                            encoding="utf-8")

    def _assert_marker_absent(self, results: Path) -> None:
        for path in results.rglob("*"):
            if not path.is_file():
                continue
            body = path.read_bytes()
            self.assertNotIn(
                self.OUTSIDE_MARKER.encode(), body,
                f"{path} carries content read from outside the guidance "
                "checkout — this directory is pushed to the public "
                "persistent/eval-results branch")

    def test_a_manifest_file_outside_the_checkout_is_refused_by_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-escape-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        outside = self._outside_file(tmp)
        root = make_guidance_checkout(tmp / "checkout")
        self._retarget_first_row(root, f"../{outside.name}")
        self.assertTrue(outside.is_file(), "the escape target must exist, or "
                        "the refusal could be a missing-file error instead")

        results = tmp / "results"
        # `canary_loader` echoes the whole delivered memory into the
        # transcript, which is the shape round 3 used to show the outside
        # content reaching results/.../transcripts/raw.json at rc 0. It reads
        # the WORKSPACE's CLAUDE.md, so the run uses `--delivery project` —
        # the documented fallback delivery, through the same hook.
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "canary_loader"})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--delivery", "project",
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("OUTSIDE the checkout", out, out)
        self.assertIn(str(root.resolve()), out, "the refusal must name the "
                      f"checkout it was pointed at\n{out}")
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn(self.OUTSIDE_MARKER, out,
                         "the refusal must not echo the content it refused")
        if results.exists():
            self._assert_marker_absent(results)

    def test_a_manifest_file_symlinked_out_of_the_checkout_is_refused(self):
        # The same read with one more step in it: an ordinary in-tree row,
        # whose file IS a symlink pointing out of the checkout. Refused only
        # because both sides are resolved before the comparison.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-symlink-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        outside = self._outside_file(tmp)
        root = make_guidance_checkout(tmp / "checkout")
        link = root / "agents-md" / "sections" / "escape.md"
        link.symlink_to(outside)
        self.assertTrue(link.is_file(), "the symlink must resolve to a real "
                        "file, or this proves nothing")
        self._retarget_first_row(root, "agents-md/sections/escape.md")

        results = tmp / "results"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "canary_loader"})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--delivery", "project",
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("OUTSIDE the checkout", out, out)
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn(self.OUTSIDE_MARKER, out, out)
        if results.exists():
            self._assert_marker_absent(results)

    def test_an_ordinary_in_tree_manifest_row_is_untouched(self):
        # The other side: containment refuses nothing a real checkout does.
        # The committed manifest's own rows are covered by
        # test_the_real_manifest_rows_all_resolve_inside_the_checkout below.
        root = self._checkout()
        self._skip_without_real_hook(root)
        tmp = Path(tempfile.mkdtemp(prefix="guidance-intree-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = self._guidance_fixture(tmp)
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", tmp / "results",
                                  "--no-judge"])
        self.assertEqual(rc, 0, out)

    def test_the_real_manifest_rows_all_resolve_inside_the_checkout(self):
        if not (REAL_GUIDANCE_DIR / "agents-md" / "eval-coverage.yml").is_file():
            self.skipTest(f"no _agent-guidance checkout at {REAL_GUIDANCE_DIR}")
        rows = guidance.load_manifest(REAL_GUIDANCE_DIR)
        self.assertTrue(rows, "the real manifest must be non-empty")
        for row in rows:
            with self.subTest(section=row["id"]):
                resolved = guidance.inside_checkout(REAL_GUIDANCE_DIR,
                                                    row["file"])
                self.assertTrue(resolved.is_file(), resolved)

    def test_the_containment_boundary_is_written_down_in_both_places(self):
        # The clause the reader meets before they meet the code. Pinned by
        # its operative words in both, because "the arm executes it anyway"
        # is the obvious objection and the answer — a read boundary is not
        # the execution boundary — has to survive the next rewrite.
        for label, text in (
                ("harness/guidance.py", guidance.__doc__ or ""),
                ("DESIGN.md",
                 (REPO_ROOT / "DESIGN.md").read_text(encoding="utf-8"))):
            with self.subTest(doc=label):
                folded = " ".join(text.split())
                for phrase in ("only from inside", "trust boundary",
                               "persistent/eval-results"):
                    # assertTrue, not assertIn: assertIn's default message
                    # would dump the whole docstring (or DESIGN.md) into the
                    # failure ahead of the sentence that explains it.
                    self.assertTrue(
                        phrase in folded,
                        f"{label} does not carry {phrase!r}. It must say that "
                        "guidance content is executed "
                        "by the arm (the header's trust boundary) and that "
                        "the harness reads it only from inside the checkout, "
                        "whose sink is the public persistent/eval-results branch")

    # ------------------------------------------------------------------
    # F-1-N — the funnel is the only way to build a path from the checkout
    #
    # F-1 bounded the manifest's `file:` and left the enumeration beside it:
    # "base.md, stub.md, the manifest itself and fleet-memory.sh are module
    # constants". A module constant is a NAME, not a location. Measured on
    # f9115ce, both identical on a6d165d and both rc 0:
    #   * `agents-md/eval-coverage.yml` replaced by a symlink out — the
    #     OUTSIDE file was read as the manifest and its ids reached stdout,
    #     because `load_manifest` called `path.read_text()` on a path it built
    #     itself;
    #   * `.claude/hooks/fleet-memory.sh` replaced by a symlink out — the
    #     OUTSIDE script was EXECUTED.
    # And `_read`'s own containment was unpinned: reverting it alone to
    # `Path(guidance_dir) / rel` left all 120 tests green.
    #
    # So the rule is the sink, not the list: `inside_checkout` is the only
    # thing in harness/guidance.py allowed to turn `guidance_dir` into a path.
    # Every read and every exec has to build a path first, so funnelling
    # construction funnels all of them — including the one a future edit adds.
    # ------------------------------------------------------------------

    OUTSIDE_MANIFEST_ID = "F1N-OUTSIDE-MANIFEST-ID"
    OUTSIDE_HOOK_MARKER = "f1n-outside-hook-ran"

    def test_the_checkout_funnel_is_the_only_way_a_path_is_built(self):
        tree = ast.parse((HARNESS_DIR / "guidance.py").read_text(encoding="utf-8"))
        funnelled, escapes = 0, []

        def mentions_the_checkout(node) -> bool:
            return any(isinstance(n, ast.Name) and n.id == "guidance_dir"
                       for n in ast.walk(node))

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name == "inside_checkout":
                continue  # the funnel itself
            exempt = set()
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", None) == "inside_checkout"):
                    if node.args and mentions_the_checkout(node.args[0]):
                        funnelled += 1
                    for argument in node.args:
                        for sub in ast.walk(argument):
                            exempt.add(id(sub))
            for node in ast.walk(fn):
                if id(node) in exempt:
                    continue
                derived = False
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    derived = mentions_the_checkout(node.left)
                elif isinstance(node, ast.Call):
                    name = (node.func.attr
                            if isinstance(node.func, ast.Attribute)
                            else getattr(node.func, "id", None))
                    if name in ("Path", "join", "joinpath"):
                        derived = any(mentions_the_checkout(a) for a in node.args)
                        if isinstance(node.func, ast.Attribute):
                            derived = derived or mentions_the_checkout(node.func.value)
                if derived:
                    escapes.append(
                        f"{fn.name}():{node.lineno}  {ast.unparse(node)}")

        self.assertGreater(
            funnelled, 0,
            "nothing in harness/guidance.py passes the checkout through "
            "inside_checkout any more — this assertion must not be able to "
            "pass vacuously")
        self.assertEqual(
            sorted(escapes), [],
            "these expressions build a path out of `guidance_dir` without "
            "passing inside_checkout, so whatever they go on to read, write "
            "or EXECUTE is not bounded by the checkout:\n  "
            + "\n  ".join(sorted(escapes))
            + "\nEvery read and every exec builds a path first, which is why "
            "the funnel is on the construction rather than on a list of the "
            "reads someone remembered.")

        # And the funnel is really inside `_read`, which is how base.md,
        # stub.md and every manifest `file:` reach it.
        read_fn = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "_read")
        self.assertIn(
            "inside_checkout",
            {getattr(c.func, "id", None) for c in ast.walk(read_fn)
             if isinstance(c, ast.Call)},
            "_read no longer calls inside_checkout — reverting exactly that "
            "left all 120 tests green on f9115ce")

    def test_read_refuses_a_relative_escape_when_called_directly(self):
        # The direct-call half: `load_manifest` validates every row at load,
        # so a caller that arrives around it — a hand-built row, a future
        # second entry point — is what `_read`'s own check is for, and
        # nothing exercised it.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-read-direct-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = make_guidance_checkout(tmp / "checkout")
        (tmp / "x.md").write_text("# outside\n", encoding="utf-8")
        with self.assertRaises(guidance.GuidanceError) as caught:
            guidance._read(root, "../x.md")
        self.assertIn("OUTSIDE the checkout", str(caught.exception))
        # ...and through assemble(), with a row that never saw load_manifest.
        with self.assertRaises(guidance.GuidanceError) as caught:
            guidance.assemble(root, {"id": "x", "heading": "Alpha",
                                     "file": "../x.md"}, "section")
        self.assertIn("OUTSIDE the checkout", str(caught.exception))

    def test_a_symlinked_manifest_cannot_be_read_from_outside_the_checkout(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-manifest-link-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = make_guidance_checkout(tmp / "checkout")
        outside = tmp / "OUTSIDE_MANIFEST.yml"
        outside.write_text(yaml.safe_dump(
            [{"id": self.OUTSIDE_MANIFEST_ID, "heading": "Alpha",
              "file": "agents-md/base.md"}], sort_keys=False), encoding="utf-8")
        manifest = root / "agents-md" / "eval-coverage.yml"
        manifest.unlink()
        manifest.symlink_to(outside)
        self.assertTrue(manifest.is_file(), "the symlink must resolve, or "
                        "this proves nothing")

        results = tmp / "results"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "canary_loader"})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--delivery", "project",
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("OUTSIDE the checkout", out, out)
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn(self.OUTSIDE_MANIFEST_ID, out,
                         "the outside manifest's ids must not reach stdout")
        if results.exists():
            for path in results.rglob("*"):
                if path.is_file():
                    self.assertNotIn(
                        self.OUTSIDE_MANIFEST_ID,
                        path.read_text(encoding="utf-8", errors="replace"),
                        f"{path} carries an id from outside the checkout")

    def test_a_symlinked_hook_is_never_executed_from_outside_the_checkout(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-hook-link-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = make_guidance_checkout(tmp / "checkout")
        hook = root / ".claude" / "hooks" / "fleet-memory.sh"
        if not hook.is_file():
            self.skipTest("no sibling _agent-guidance checkout: "
                          "make_guidance_checkout copied no real hook")
        marker = tmp / f"{self.OUTSIDE_HOOK_MARKER}.txt"
        outside = tmp / "outside-hook.sh"
        outside.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 0\n',
                           encoding="utf-8")
        outside.chmod(0o755)
        hook.unlink()
        hook.symlink_to(outside)
        self.assertTrue(hook.is_file(), "the symlink must resolve, or this "
                        "proves nothing")

        argv_log = tmp / "argv.jsonl"
        results = tmp / "results"
        eval_dir = self._guidance_fixture(
            tmp, env={"FAKE_CLAUDE_MODE": "guidance_probe",
                      "FAKE_CLAUDE_ARGV_LOG": str(argv_log)})
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("OUTSIDE the checkout", out, out)
        self.assertNotIn("Traceback", out, out)
        self.assertFalse(marker.exists(),
                         "the hook outside the checkout was EXECUTED")
        self.assertFalse(argv_log.exists(),
                         "the CLI was invoked before the hook's path was "
                         "checked")

    def test_a_non_string_manifest_file_is_a_named_configuration_error(self):
        root_template = make_guidance_checkout
        for value in (5, ["agents-md/base.md"], {"path": "x"}, True):
            with self.subTest(file=value):
                tmp = Path(tempfile.mkdtemp(prefix="guidance-file-type-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                root = root_template(tmp / "checkout")
                self._retarget_first_row(root, value)
                results = tmp / "results"
                eval_dir = self._guidance_fixture(
                    tmp, env={"FAKE_CLAUDE_MODE": "canary_loader"})
                rc, out = self._run_main(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--delivery", "project", "--results-dir", results,
                     "--no-judge"])
                self.assertEqual(rc, 2, out)
                self.assertIn("must be a string path", out, out)
                self.assertNotIn("Traceback", out, out)
                self.assertFalse(results.exists(),
                                 "a refused manifest must write nothing")

    def test_unknown_section_id_through_main_exits_2_naming_the_manifest(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-badid-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        eval_dir = self._guidance_fixture(tmp, section="no-such-section")
        rc, out = self._run_main([eval_dir, "--arm", "both", "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 2)
        self.assertIn("eval-coverage.yml", out)

    def test_missing_guidance_checkout_through_main_exits_2_naming_the_flag(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-nocheckout-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = self._guidance_fixture(tmp)
        rc, out = self._run_main([eval_dir, "--arm", "both",
                                  "--guidance", tmp / "nowhere",
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 2)
        self.assertIn("--guidance", out)

    # ------------------------------------------------------------------
    # Item 6 — scripts/make_badge.py accepts guidance/<id>
    # ------------------------------------------------------------------

    @staticmethod
    def _write_summary_file(run_dir: Path, arm: str, passed: int, total: int,
                            judge: float | None) -> None:
        arm_dir = run_dir / arm
        arm_dir.mkdir(parents=True, exist_ok=True)
        checks = [{"id": f"c{i}", "passed": i < passed} for i in range(total)]
        payload = {"arm": arm, "error": None, "objective_checks": checks,
                   "judge": {"overall": judge} if judge is not None else None}
        (arm_dir / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_make_badge_reads_a_guidance_runs_with_and_without_guidance_arms(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-badge-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        results = tmp / "results"
        run = results / "guidance" / "security" / "20260904T070000Z"
        self._write_summary_file(run, "with_guidance", 4, 4, 8.0)
        self._write_summary_file(run, "without_guidance", 1, 4, 5.0)

        badge = make_badge.build_badge(results, "guidance/security")
        self.assertEqual(badge["label"], "guidance eval: security")
        self.assertEqual(badge["color"], "green")
        self.assertIn("with 4/4 vs without 1/4", badge["message"])
        self.assertIn("2026-09-04", badge["message"])

        out = tmp / "badges" / "guidance" / "security.json"
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "make_badge.py"),
             "guidance/security", "--results-dir", str(results), "--out", str(out)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["label"],
                         "guidance eval: security")

    def test_make_badge_still_reads_skill_arms_for_a_skill_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="skill-badge-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        results = tmp / "results"
        run = results / "workflow-path-audit" / "20260904T070000Z"
        self._write_summary_file(run, "with_skill", 4, 4, 8.0)
        self._write_summary_file(run, "without_skill", 1, 4, 5.0)
        badge = make_badge.build_badge(results, "workflow-path-audit")
        self.assertEqual(badge["label"], "skill eval: workflow-path-audit")
        self.assertEqual(badge["color"], "green")

    def test_make_badge_rejects_a_name_that_escapes_the_results_tree(self):
        for bad in ("../etc", "/abs/name", "guidance/../../x"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    make_badge.build_badge(Path("results"), bad)

    # ------------------------------------------------------------------
    # Item 7 — the committed delivery canary, evals/guidance/_delivery
    # ------------------------------------------------------------------

    def _delivery_fixture(self) -> dict:
        return yaml.safe_load((DELIVERY_DIR / "fixture.yaml").read_text(encoding="utf-8"))

    def test_objective_only_on_a_guidance_fixture_exits_2_not_zero_checks(self):
        # N-f. A guidance fixture's checks are per arm, so a top-level
        # `objective_checks:` is normally absent — and `run_checks` over zero
        # checks printed `{"checks": []}` and exited 0, which reads as "every
        # check passed". An empty measurement is not a passing one.
        rc, out = self._run_main([DELIVERY_DIR, "--arm", "objective-only"])
        self.assertEqual(rc, 2, out)
        self.assertIn("objective_checks", out)
        self.assertIn("per arm", out)
        self.assertNotIn('"checks": []', out)

    def test_objective_only_still_scores_a_guidance_fixture_that_has_checks(self):
        # The other side: the branch is about ABSENT checks, not about the
        # subject. A guidance fixture that does declare top-level checks is
        # scored exactly as before.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-objonly-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        eval_dir = self._guidance_fixture(tmp, objective_checks=[
            {"id": "seed-clean", "type": "file_matches",
             "must_not_match": ["zzz-never-appears"]}])
        rc, out = self._run_main([eval_dir, "--arm", "objective-only"])
        self.assertEqual(rc, 0, out)
        self.assertIn('"checks"', out)
        self.assertIn("seed-clean", out)

    def test_a_skill_objective_only_run_survives_a_missing_markdown_it(self):
        # N-g. run_eval imports guidance for every subject, and guidance used
        # to import markdown_it at MODULE scope — so a skill fixture's
        # objective-only run died with a bare ImportError traceback on a
        # machine without the parser, for a code path that never parses
        # markdown. The import is at its one call site now.
        skill_dir = example_evals.FIXTURES / "workflow-path-audit" / "required-check-routing"
        baseline, _ = self._run_main([skill_dir, "--arm", "objective-only"])
        blocked = dict(sys.modules)
        blocked["markdown_it"] = None
        with mock.patch.dict(sys.modules, blocked, clear=True):
            with self.assertRaises(ImportError):
                import markdown_it  # noqa: F401
            rc, out = self._run_main([skill_dir, "--arm", "objective-only"])
        self.assertEqual(rc, baseline,
                         "a skill fixture's objective-only run must not "
                         f"depend on markdown-it-py\n{out[-1500:]}")
        self.assertNotIn("Traceback", out)

    def test_the_extent_parser_names_the_pip_line_when_it_cannot_import(self):
        blocked = dict(sys.modules)
        blocked["markdown_it"] = None
        with mock.patch.dict(sys.modules, blocked, clear=True):
            with self.assertRaises(guidance.GuidanceError) as ctx:
                guidance.h2_extents("# T\n\n## A\n\nbody\n")
        self.assertIn("markdown-it-py==4.2.0", str(ctx.exception))

    def test_the_delivery_fixture_declares_one_arm_per_mode(self):
        fixture = self._delivery_fixture()
        self.assertEqual(fixture["subject"], "guidance")
        self.assertIsInstance(fixture["section"], str)
        modes = [arm["mode"] for arm in fixture["arms"].values()]
        self.assertEqual(sorted(modes), sorted(guidance.MODES),
                         "the delivery canary runs one arm per mode")
        self.assertEqual(len(fixture["arms"]), 5)
        # Every treatment arm asserts the treatment token IS visible. The
        # control's check is TWO-SIDED: its own decoy must be visible (it read
        # its own scratch user memory) and the treatment token must not be
        # (nothing else delivered the guidance to it). Both tokens are fresh
        # per run, so the fixture names them with the harness's placeholders.
        for name, arm in fixture["arms"].items():
            with self.subTest(arm=name):
                checks = arm["objective_checks"]
                self.assertEqual([c["type"] for c in checks], ["transcript_matches"])
                if arm["mode"] == "none":
                    self.assertEqual(checks[0]["must_match"],
                                     [run_eval.DECOY_PLACEHOLDER])
                    self.assertEqual(checks[0]["must_not_match"],
                                     [run_eval.TOKEN_PLACEHOLDER])
                else:
                    self.assertEqual(checks[0]["must_match"],
                                     [run_eval.TOKEN_PLACEHOLDER])
                    self.assertNotIn("must_not_match", checks[0])

    def test_the_delivery_fixtures_section_exists_in_the_real_manifest(self):
        if not (REAL_GUIDANCE_DIR / guidance.MANIFEST_REL).is_file():
            reason = (f"no _agent-guidance checkout at {REAL_GUIDANCE_DIR} — "
                      "skipping the section-id agreement check")
            print(reason)
            self.skipTest(reason)
        manifest = guidance.load_manifest(REAL_GUIDANCE_DIR)
        row = guidance.find_row(manifest, self._delivery_fixture()["section"],
                                REAL_GUIDANCE_DIR)
        self.assertTrue(row["heading"])

    def test_the_delivery_canary_runs_all_five_modes_against_the_real_guidance(self):
        if not (REAL_GUIDANCE_DIR / guidance.HOOK_REL).is_file():
            reason = (f"no _agent-guidance checkout at {REAL_GUIDANCE_DIR} — "
                      "skipping the end-to-end delivery-canary run")
            print(reason)
            self.skipTest(reason)
        tmp = Path(tempfile.mkdtemp(prefix="delivery-canary-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        results = tmp / "results"
        fixture = self._delivery_fixture()
        # The committed fixture carries no `env:` (it is production), so the
        # fake CLI's mode reaches the child through the allowlist's documented
        # test seam instead — the same one init_probe.py has.
        with mock.patch.object(guidance, "EXTRA_PASSTHROUGH", ("FAKE_CLAUDE_MODE",)), \
                mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "guidance_probe"}):
            rc, out = self._run_main([DELIVERY_DIR, "--arm", "both",
                                      "--guidance", REAL_GUIDANCE_DIR,
                                      "--results-dir", results, "--no-judge"])
        self.assertEqual(rc, 0, out)
        key = f"guidance/{fixture['section']}"
        for name, arm in fixture["arms"].items():
            with self.subTest(arm=name):
                summary = self._summary(results, key, name)
                self.assertTrue(summary["guard"]["ok"], summary["guard"])
                self.assertTrue(summary["guard"]["expected"],
                                "every arm reports the token IT was delivered")
                self.assertTrue(summary["guard"]["observed"], summary["guard"])
                self.assertFalse(summary["guard"]["contaminated"],
                                 summary["guard"])
                self.assertGreater(summary["bytes"], 0,
                                   "the control's bytes are its decoy's")
                self.assertTrue(all(c["passed"] for c in summary["objective_checks"]),
                                summary["objective_checks"])
        stub = self._summary(results, key, "with_guidance_stub")["bytes"]
        full = self._summary(results, key, "with_guidance_full")["bytes"]
        minus = self._summary(results, key, "with_guidance_full_minus_section")["bytes"]
        section = self._summary(results, key, "with_guidance_section")["bytes"]
        self.assertLess(stub, full)
        self.assertLess(minus, full)
        self.assertLess(section, full)

    def test_every_committed_fixture_declares_a_known_subject(self):
        for path in sorted((REPO_ROOT / "evals").glob("**/fixture.yaml")):
            with self.subTest(fixture=str(path.relative_to(REPO_ROOT))):
                fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
                # `any`: a subject-agnostic real-work fixture, which the run
                # names a skill or section for (the owner, 2026-10-06, Q4).
                self.assertIn(fixture.get("subject", "skill"),
                              ("skill", "guidance", "any"))

    # ------------------------------------------------------------------
    # Item 8 + the dispatch input — .github/workflows/eval.yml
    # ------------------------------------------------------------------

    MARKDOWN_IT_PIN = "markdown-it-py==4.2.0"

    def _workflow(self) -> dict:
        return yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))

    def _eval_steps(self) -> list[dict]:
        return self._workflow()["jobs"]["eval"]["steps"]

    def _step_named(self, prefix: str) -> dict:
        for step in self._eval_steps():
            if (step.get("name") or "").startswith(prefix):
                return step
        self.fail(f"no step in eval.yml whose name starts with {prefix!r}")

    def test_dispatch_takes_a_fixture_input_defaulting_to_workflow_path_audit(self):
        # Without this input no fixture but workflow-path-audit can ever get a
        # real run on main, so a guidance fixture (or any backfill fixture)
        # could never be measured at all.
        doc = self._workflow()
        triggers = doc.get("on", doc.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"},
                         "the weekly routine owns scheduled evals; eval.yml stays manual")
        inputs = (triggers["workflow_dispatch"] or {}).get("inputs") or {}
        self.assertIn("fixture", inputs)
        self.assertEqual(inputs["fixture"].get("default"),
                         "evals/workflow-path-audit/required-check-routing",
                         "manual dispatch keeps its default")
        self.assertFalse(inputs["fixture"].get("required"),
                         "manual dispatch can omit the fixture input")

    def test_the_fixture_input_is_read_from_the_event_file_never_interpolated(self):
        # `${{ inputs.fixture }}` inside a run: block is a shell-injection
        # surface in the one workflow that holds a live API key. The value is
        # read from $GITHUB_EVENT_PATH as data instead.
        for step in self._eval_steps():
            run = step.get("run") or ""
            with self.subTest(step=step.get("name")):
                self.assertNotIn("${{", run)
        scripts = [s.get("run") or "" for s in self._eval_steps()]
        reading = [s for s in scripts if "GITHUB_EVENT_PATH" in s
                   and "inputs.fixture" in s]
        self.assertEqual(len(reading), 1,
                         "exactly one step reads the fixture input, and it "
                         "reads it from the event file")
        # The one other reader is "Propose a roster change", which reads the
        # boolean `roster_only` from the same file to word its issue text
        # (#203 round 1) — never the fixture.
        others = [s for s in scripts if "GITHUB_EVENT_PATH" in s
                  and "inputs.fixture" not in s]
        for other in others:
            self.assertIn(".inputs.roster_only", other)

    def test_the_fixture_is_validated_before_any_credential_step(self):
        names = [(s.get("name") or "") for s in self._eval_steps()]
        validate = next(i for i, s in enumerate(self._eval_steps())
                        if "GITHUB_EVENT_PATH" in (s.get("run") or ""))
        credential = next(i for i, name in enumerate(names)
                          if name.startswith("Mint OIDC token"))
        self.assertLess(validate, credential,
                        "a value that names no committed fixture must fail the "
                        "step before any credential is minted")
        run_step = self._step_named("Run the eval")
        self.assertIn("eval-fixture", run_step["run"],
                      "the run step must take the fixture from the file the "
                      "validation step wrote, not from the event again")

    def _validation_script(self) -> str:
        for step in self._eval_steps():
            if "GITHUB_EVENT_PATH" in (step.get("run") or ""):
                return step["run"]
        self.fail("no validation step found")

    def _run_validation(self, event: dict | None,
                        cwd: Path | None = None) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp(prefix="eval-dispatch-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        event_path = tmp / "event.json"
        event_path.write_text(json.dumps(event if event is not None else {}),
                              encoding="utf-8")
        # A real runner always provides GITHUB_OUTPUT (the step writes the
        # validated `eval_key` there; publication uses the matching matrix key).
        env = dict(os.environ, GITHUB_EVENT_PATH=str(event_path),
                   RUNNER_TEMP=str(tmp), GITHUB_OUTPUT=str(tmp / "gh-output"))
        proc = subprocess.run(["bash", "-c", self._validation_script()],
                              # The example checkout commits the fixtures
                              # these cases name (test/example_evals.py).
                              cwd=str(cwd or example_evals.checkout()), env=env,
                              capture_output=True, text=True, timeout=300)
        proc.selected = (tmp / "eval-fixture").read_text(encoding="utf-8") \
            if (tmp / "eval-fixture").is_file() else None
        proc.key = (tmp / "eval-key").read_text(encoding="utf-8") \
            if (tmp / "eval-key").is_file() else None
        proc.output = (tmp / "gh-output").read_text(encoding="utf-8") \
            if (tmp / "gh-output").is_file() else ""
        return proc

    def test_the_validation_step_accepts_every_committed_fixture(self):
        committed = sorted(str(p.parent.relative_to(REPO_ROOT))
                           for p in (REPO_ROOT / "evals").glob("**/fixture.yaml"))
        self.assertIn("evals/guidance/_delivery", committed)
        for fixture in committed:
            with self.subTest(fixture=fixture):
                proc = self._run_validation({"inputs": {"fixture": fixture}})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(proc.selected, fixture)
                # The selection output is exactly the validated key, one line;
                # the planner gives publication the same per-fixture key.
                self.assertEqual(
                    proc.output, f"eval_key={fixture[len('evals/'):]}\n")

    def test_the_validation_step_defaults_when_the_event_carries_no_input(self):
        # Manual dispatch may omit `inputs` or leave the fixture input empty.
        for event in ({}, {"inputs": {}}, {"inputs": {"fixture": ""}}):
            with self.subTest(event=event):
                proc = self._run_validation(event)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(proc.selected, "evals/workflow-path-audit/required-check-routing")

    # Every value the step must refuse, driven through the real `run:` block.
    # Two shapes of refusal, both exit 1 before the token exchange: a value
    # carrying a character outside [A-Za-z0-9/_.-] (rejected on SHAPE, before
    # any matching, and never echoed back into the log), and a well-shaped
    # value that simply names no committed fixture.
    REJECTED_DISPATCH_VALUES = (
        # names nothing committed
        "evals/nope",
        "evals",
        "evals/guidance",
        # a skill directory of nested fixtures names no single fixture (#66)
        "evals/workflow-path-audit",
        "evals/workflow-path-audit/required-check-routing/seed",
        123,                                    # a JSON number, not a string
        # path shapes that are not the committed spelling
        "../../etc/passwd",
        "/etc",
        "/tmp/x",
        "./evals/workflow-path-audit/required-check-routing",
        "evals//workflow-path-audit",
        "evals/../evals/workflow-path-audit/required-check-routing",
        "evals/workflow-path-audit/required-check-routing/",
        # whitespace-decorated
        "evals/workflow-path-audit/required-check-routing ",
        "evals/workflow-path-audit/required-check-routing\t",
        "evals/workflow-path-audit/required-check-routing\r",
        # shell metacharacters (inert here — every use is quoted — but the
        # gate refuses them on shape rather than relying on that)
        "; rm -rf /",
        "evals/*",
        "$(id)",
        "`id`",
        # a Cyrillic \u0430 where the ASCII `a` belongs: renders identically,
        # names nothing
        "evals/workflow-path-\u0430udit",
        # MULTI-LINE. `grep -F` treats each LINE of its pattern as a separate
        # pattern, so before the shape gate these passed whenever EITHER line
        # named a committed fixture — and the whole two-line string was
        # written to $RUNNER_TEMP/eval-fixture, so the OIDC exchange ran, a
        # real bearer was minted and the WIF preflight spent a call before the
        # run step failed on "no such fixture.yaml". The header promises the
        # opposite, twice.
        "/etc/passwd\nevals/workflow-path-audit/required-check-routing",
        "evals/workflow-path-audit/required-check-routing\nevals/guidance/_delivery",
        "evals/workflow-path-audit/required-check-routing\n$(id)",
        # structured JSON: jq renders these across lines, so they are
        # multi-line too
        ["evals/workflow-path-audit/required-check-routing"],
        {"fixture": "evals/workflow-path-audit/required-check-routing"},
    )

    def test_the_validation_step_rejects_anything_that_names_no_fixture(self):
        for bad in self.REJECTED_DISPATCH_VALUES:
            with self.subTest(fixture=bad):
                proc = self._run_validation({"inputs": {"fixture": bad}})
                output = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 1,
                                 f"{bad!r} must fail the step: {output}")
                self.assertTrue(
                    "names no committed fixture" in output
                    or "characters outside" in output,
                    f"{bad!r} must be refused by a NAMED rule, got: {output}")
                self.assertIsNone(
                    proc.selected,
                    f"{bad!r} reached $RUNNER_TEMP/eval-fixture, so the token "
                    "exchange would have run before anything failed")
                self.assertIsNone(proc.key)

    # The marker every rejection row below carries. It appears nowhere in the
    # committed tree, so finding it in the step's output means the step put it
    # there — and the only place it could have come from is the dispatch
    # input.
    ECHO_MARKER = "uniquely-identifiable-payload"

    def test_neither_rejection_branch_echoes_the_dispatched_value(self):
        # The log of this workflow is public, and the comment block above the
        # gate says so in as many words: the message names the RULE, not the
        # input. The SHAPE branch obeyed that. The "names no committed
        # fixture" branch fifteen lines later did not — it appended
        # `: $fixture`, so any charset-clean dispatch value, up to the 100 KB
        # a dispatch input can carry, was copied verbatim into a public log
        # by a workflow whose own header promises the opposite. Charset-clean
        # is not the same as safe to republish, and the dispatched value is
        # already on the run's own inputs for anyone who can read the log.
        #
        # BOTH branches are driven here, through the real `run:` block.
        for branch, value, rule in (
            # refused on shape, before any matching
            ("shape", f"$({self.ECHO_MARKER})", "characters outside"),
            # charset-clean, so it reaches the committed-set match and is
            # refused there
            ("no such fixture", f"evals/{self.ECHO_MARKER}",
             "names no committed fixture"),
        ):
            with self.subTest(branch=branch):
                proc = self._run_validation({"inputs": {"fixture": value}})
                output = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 1, output)
                self.assertIn(rule, output,
                              f"the {branch} branch must refuse by a NAMED "
                              f"rule\n{output}")
                self.assertNotIn(
                    self.ECHO_MARKER, output,
                    f"the {branch} branch echoed the dispatched value back "
                    f"into a public log\n{output}")
                self.assertIsNone(proc.selected)
                self.assertIsNone(proc.key)

    # A real NUL, written as an escape so this source file carries none.
    NUL = chr(0)

    def test_a_nul_byte_in_the_dispatch_value_is_refused_before_substitution(self):
        # N3 (code). `jq -r` emits a NUL faithfully; bash's `$( )` then DROPS
        # it, with only a warning on stderr — so every check in the step is
        # judging a string the dispatcher did not send, and the step's own
        # `fixture:` log line names something else again. Measured on
        # c5ea933, through this same `run:` block: a value whose JSON spelling
        # is the NUL escape followed by evals/workflow-path-audit was ACCEPTED
        # as that fixture, rc 0, with $RUNNER_TEMP/eval-fixture written and
        # only "warning: command substitution: ignored null byte in input" to
        # show for it.
        #
        # It failed SAFE — whatever survives the substitution still has to
        # match a committed path — but the gate never saw the byte it would
        # refuse, which is not what this workflow's header says it does.
        for label, value in (
            ("leading", self.NUL + "evals/workflow-path-audit/required-check-routing"),
            ("trailing", "evals/workflow-path-audit/required-check-routing" + self.NUL),
            # Collapses to a COMMITTED name once the NUL is dropped — the
            # shape that was accepted.
            ("embedded, collapsing to a committed name",
             "evals/workflow" + self.NUL + "-path-audit/required-check-routing"),
            # Collapses to nothing committed: refused before, refused now, but
            # by the NUL rule rather than by accident.
            ("embedded, collapsing to nothing committed",
             "evals/no" + self.NUL + "pe"),
        ):
            with self.subTest(value=label):
                proc = self._run_validation({"inputs": {"fixture": value}})
                output = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 1,
                                 f"{label}: a NUL anywhere in the dispatch "
                                 f"value must fail the step\n{output}")
                self.assertIn("NUL byte", output,
                              f"{label}: refused by a NAMED rule\n{output}")
                self.assertIsNone(
                    proc.selected,
                    f"{label}: reached $RUNNER_TEMP/eval-fixture, so the "
                    "token exchange would have run")
                self.assertIsNone(proc.key)
                self.assertNotIn(
                    self.ECHO_MARKER.split("-")[0], output,
                    "and the value is not echoed back into a public log")
                self.assertNotIn("evals/workflow", output, output)
        # The accepted rows are unchanged: the gate runs before the
        # substitution and a value with no NUL never reaches it.
        proc = self._run_validation(
            {"inputs": {"fixture": "evals/workflow-path-audit/required-check-routing"}})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.selected, "evals/workflow-path-audit/required-check-routing")

    @staticmethod
    def _commit_selection_tree(root: Path) -> None:
        """A standalone synthetic checkout with no inherited Git metadata."""
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(REPO_ROOT / "scripts" / "plan_scheduled_evals.py",
                     scripts / "plan_scheduled_evals.py")
        git = ["git", "-c", "maintenance.auto=false", "-C", str(root)]
        subprocess.run([*git, "init", "--quiet"], check=True, capture_output=True)
        subprocess.run([*git, "add", "evals", "scripts"], check=True, capture_output=True)
        subprocess.run([*git, "-c", "user.name=fixture", "-c",
                        "user.email=fixture@example.com", "commit", "--quiet",
                        "-m", "Track synthetic fixtures"], check=True, capture_output=True)

    def test_the_fixture_match_survives_a_committed_list_far_larger_than_a_pipe(self):
        # `printf '%s\n' "$committed" | grep -Fxq -- "$fixture"` is the
        # fleet's forbidden pipe-into-early-exit shape: grep exits the moment
        # it matches, printf takes SIGPIPE on its next write, and `set -o
        # pipefail` turns that into a nonzero pipeline status — so the step
        # fails CLOSED on a perfectly valid fixture once the committed list
        # outgrows a pipe buffer. Safe at today's ~200 bytes; measured first
        # false rejection at ~66 KB and 0/20 accepted at 280 KB. This drives
        # the real `run:` block against a synthetic evals/ tree of ~100 KB of
        # committed paths, repeatedly, because the failure is racy.
        tmp = Path(tempfile.mkdtemp(prefix="eval-bigtree-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        target = "evals/workflow-path-audit"
        (tmp / target).mkdir(parents=True)
        (tmp / target / "fixture.yaml").write_text("subject: skill\n", encoding="utf-8")
        # The filler sorts AFTER the target on purpose: `grep -Fxq` exits the
        # instant it matches, so the earlier in the sorted list the match is,
        # the more of the list `printf` still has to write into a pipe nobody
        # is reading. A dispatch of an early-sorting fixture is the ordinary
        # case, not a contrived one — `evals/disarm-inherited-reach` sorts
        # first in the real tree today.
        for i in range(3600):
            d = tmp / "evals" / f"zz-generated-fixture-{i:05d}"
            d.mkdir(parents=True)
            (d / "fixture.yaml").write_text("subject: skill\n", encoding="utf-8")
        self._commit_selection_tree(tmp)
        listed = subprocess.run(
            ["bash", "-c",
             "python3 scripts/plan_scheduled_evals.py --committed"],
            cwd=str(tmp), capture_output=True, text=True, timeout=120).stdout
        self.assertGreater(len(listed.encode("utf-8")), 100_000,
                           "the synthetic committed list must exceed 100 KB")
        for trial in range(20):
            with self.subTest(trial=trial):
                proc = self._run_validation({"inputs": {"fixture": target}},
                                            cwd=tmp)
                self.assertEqual(
                    proc.returncode, 0,
                    "a committed fixture must still be accepted when the "
                    f"committed list is {len(listed.encode('utf-8'))} bytes; "
                    "the step failed CLOSED on a valid fixture: "
                    f"{(proc.stdout + proc.stderr)[:400]}")
                self.assertEqual(proc.selected, target)

    def test_the_rejection_listing_does_not_word_split_or_glob(self):
        # N-d. The rejection branch used `printf '  %s\n' $committed`,
        # UNQUOTED: a committed path carrying a space is split across two
        # lines and one carrying a glob character is expanded against the
        # working directory, so the operator is shown a list that is not the
        # list the gate matched against. Driven through the real `run:` block
        # over a synthetic evals/ tree that carries both shapes.
        tmp = Path(tempfile.mkdtemp(prefix="eval-listing-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        awkward = ("two words", "star*dir", "bracket[1]")
        for name in (*awkward, "workflow-path-audit"):
            d = tmp / "evals" / name
            d.mkdir(parents=True)
            (d / "fixture.yaml").write_text("subject: skill\n", encoding="utf-8")
        # Something for a glob to expand ONTO, so an unquoted printf visibly
        # produces a different list rather than the pattern itself.
        (tmp / "stardir-decoy").mkdir()
        self._commit_selection_tree(tmp)
        proc = self._run_validation({"inputs": {"fixture": "evals/nope"}},
                                    cwd=tmp)
        self.assertEqual(proc.returncode, 1)
        output = proc.stdout + proc.stderr
        listed = [line.strip() for line in output.splitlines()
                  if line.startswith("  evals/")]
        self.assertEqual(
            sorted(listed),
            sorted(f"evals/{name}" for name in (*awkward, "workflow-path-audit")),
            "every committed path must be listed once, intact — no word "
            f"splitting, no globbing\n{output}")

    def test_the_gate_refuses_fixtures_outside_the_committed_index(self):
        tmp = Path(tempfile.mkdtemp(prefix="eval-tracked-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        tracked = tmp / "evals" / "workflow-path-audit"
        tracked.mkdir(parents=True)
        (tracked / "fixture.yaml").write_text("subject: skill\n", encoding="utf-8")
        self._commit_selection_tree(tmp)
        accepted = self._run_validation({"inputs": {"fixture": "evals/workflow-path-audit"}},
                                        cwd=tmp)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(accepted.selected, "evals/workflow-path-audit")
        untracked = tmp / "evals" / "untracked"
        untracked.mkdir()
        (untracked / "fixture.yaml").write_text("subject: skill\n", encoding="utf-8")
        rejected = self._run_validation({"inputs": {"fixture": "evals/untracked"}}, cwd=tmp)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertIsNone(rejected.selected)
        self.assertIn("names no committed fixture", rejected.stdout)

    def test_agent_guidance_is_checked_out_side_by_side_without_credentials(self):
        checkout = next(
            s for s in self._eval_steps()
            if (s.get("with") or {}).get("repository", "").endswith("/_agent-guidance"))
        with_block = checkout["with"]
        self.assertEqual(with_block.get("path"), "_agent-guidance",
                         "side by side, so the harness's sibling default resolves")
        self.assertIs(with_block.get("persist-credentials"), False)
        self.assertIsNone(lock_problem(checkout["uses"], EVAL_WORKFLOW))
        run = self._step_named("Run the eval")["run"]
        self.assertNotIn("--registry _agent-guidance", run,
                         "_agent-guidance is not a skill registry")
        self.assertIn("--guidance ../_agent-guidance", run)

    def test_markdown_it_py_is_pinned_exact_in_both_workflows(self):
        for workflow in (EVAL_WORKFLOW, CI_WORKFLOW):
            with self.subTest(workflow=workflow.name):
                doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
                installs = [s.get("run") or "" for job in doc["jobs"].values()
                            for s in job.get("steps", [])
                            if "pip install" in (s.get("run") or "")]
                self.assertTrue(installs, "no pip install step found")
                self.assertTrue(
                    any(self.MARKDOWN_IT_PIN in run for run in installs),
                    f"{workflow.name} must install {self.MARKDOWN_IT_PIN} — the "
                    "guidance payload parser is pinned exact, per the repo's "
                    "cooling-off convention")

    @staticmethod
    def _eval_header() -> str:
        import itertools
        lines = EVAL_WORKFLOW.read_text(encoding="utf-8").splitlines()
        return "\n".join(itertools.takewhile(
            lambda line: line.strip() == "" or line.lstrip().startswith("#"),
            lines))

    @classmethod
    def _eval_header_prose(cls) -> str:
        """The header with its `#` markers and line wrapping removed, so a
        phrase test asserts what the header SAYS rather than where the author
        happened to break the line."""
        stripped = [line.lstrip().lstrip("#").strip()
                    for line in cls._eval_header().splitlines()]
        return " ".join(part for part in stripped if part)

    def test_the_header_names_what_is_in_reach_for_each_subject(self):
        # A6. The header used to say that while the bypassPermissions agent
        # runs, "the only credential in reach is the short-lived WIF-derived
        # access token (and the single-use OIDC token file)". Measured when
        # this test was written: true of a GUIDANCE arm, which gets an
        # allowlist built from nothing, and FALSE of a SKILL arm, whose
        # `agent_env` was `dict(os.environ)` — it also inherited the runner's
        # GitHub OIDC request token and the Actions runtime token.
        #
        # Merging #81 closed that half: `run_eval.agent_env` is an allowlist
        # of its own now, so neither arm carries the three runner tokens. The
        # test measures both environments through the real functions and goes
        # on requiring the header to say what each one actually carries —
        # which is what keeps the header honest in EITHER direction, since a
        # name added back to `_ALLOWED_ENV` reds it just as its removal did.
        # The ban below stays: the two allowlists still forward every
        # ANTHROPIC_* variable and the skill one every CLAUDE_* variable
        # (CLAUDE_CODE_OAUTH_TOKEN included), so "the only credential in
        # reach" is not a sentence this header may make.
        header = self._eval_header_prose()
        self.assertFalse(
            "the only credential in reach" in header,
            "the header still claims the WIF token is 'the only credential in "
            "reach'. Both allowlists forward every ANTHROPIC_* variable, and "
            "the skill arm's also forwards every CLAUDE_* one — "
            "CLAUDE_CODE_OAUTH_TOKEN among them. Name what is in reach per "
            "subject instead.")

        tmp = Path(tempfile.mkdtemp(prefix="reach-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        workspace, home, tmpdir, config = (tmp / n for n in
                                           ("ws", "home", "tmp", "config"))
        for path in (workspace, home, tmpdir, config):
            path.mkdir(parents=True)
        runner_only = {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-oidc-token",
                       "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.com/oidc",
                       "ACTIONS_RUNTIME_TOKEN": "runner-runtime-token"}
        with mock.patch.dict(os.environ, runner_only):
            skill_env = run_eval.agent_env(workspace, None)
            guidance_env = guidance.agent_env(
                workspace=workspace, home=home, tmpdir=tmpdir, config_dir=config)

        # The environments are non-empty, so neither loop below can pass by
        # measuring nothing: `agent_env` returning `{}` would otherwise
        # satisfy every assertNotIn in it.
        self.assertTrue(skill_env, "run_eval.agent_env returned nothing")
        self.assertTrue(guidance_env, "guidance.agent_env returned nothing")
        for name in runner_only:
            with self.subTest(variable=name):
                self.assertNotIn(name, skill_env,
                                 "measured: the skill arm gets an allowlist "
                                 "too since #81, so the runner's GitHub "
                                 "tokens are no longer in reach")
                self.assertNotIn(name, guidance_env,
                                 "measured: the guidance arm gets an allowlist")
                self.assertIn(name, header,
                              "the header must still NAME each runner token "
                              "and say it is out of reach — a reader who "
                              "knows the runner sets it needs to be told "
                              f"where it went; {name} is missing from it")
        # The skill arm's allowlist, quoted in the header the same way the
        # guidance arm's is. Names only: the four PREFIXES are families, and
        # the header names the family.
        for name in run_eval._ALLOWED_ENV:
            with self.subTest(allowed=name):
                self.assertIn(name, header,
                              "the header quotes the skill arm's allowlist; "
                              f"{name} is in _ALLOWED_ENV but not in it")
        for prefix in run_eval._ALLOWED_ENV_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertIn(prefix.rstrip("_"), header,
                              "the header quotes the skill arm's allowlist "
                              f"prefixes; {prefix} is missing from it")
        # And it must still say what neither arm has.
        for phrase in ("no GitHub write credential", "no long-lived secret"):
            self.assertIn(phrase, header, f"the header must still say {phrase!r}")
        # The allowlist the header quotes for the guidance arm is the real one.
        for name in guidance.PASSTHROUGH:
            self.assertIn(name, header,
                          "the header quotes the guidance arm's allowlist; "
                          f"{name} is in PASSTHROUGH but not in the header")
        # PASSTHROUGH is only part of what `agent_env` sets, and the names it
        # does NOT cover are the ones the sentence lost: it enumerated the
        # three isolation variables and stopped, omitting WORKSPACE. Derived
        # from the MEASURED environment rather than restated, so the next name
        # added to `agent_env` cannot be omitted the same way.
        self.assertIn("WORKSPACE", guidance_env,
                      "agent_env must set WORKSPACE — this pin must not pass "
                      "vacuously")
        for name in sorted(guidance_env):
            if name.startswith("ANTHROPIC_"):
                continue  # the header names the family, not each member
            with self.subTest(variable=name):
                self.assertIn(
                    name, header,
                    f"{name} is in the guidance arm's environment (measured "
                    "through guidance.agent_env) and the header does not "
                    "name it")
        # And the fixture's own `env:` overlay, which the sentence omitted
        # altogether: a fixture can ADD names to that environment, so
        # "an allowlist built from nothing" is not the whole story.
        self.assertIn(
            "`env:` block", header,
            "the header must say the fixture's own `env:` block is applied "
            "on top of the allowlist, minus the three isolation names it may "
            "not set")

    def test_the_security_header_carries_the_agent_guidance_clause(self):
        header = self._eval_header()
        self.assertIn("_agent-guidance", header,
                      "the header must name every checked-out repo")
        self.assertIn("bypassPermissions", header)
        for phrase in ("write access", "equivalent to key access"):
            self.assertIn(phrase, header)
        guidance_clause = [para for para in header.split("#\n")
                           if "_agent-guidance" in para]
        self.assertTrue(
            any("key access" in para for para in guidance_clause),
            "the header must say that write access to _agent-guidance's "
            "default branch now equals key access here — the with arm "
            "executes its content under bypassPermissions")

    # ------------------------------------------------------------------
    # Item 1 — the ablation pair, and arm/schema validation
    # ------------------------------------------------------------------

    def test_the_default_arm_pair_is_section_versus_none(self):
        arms = run_eval.guidance_arms({}, "both")
        self.assertEqual([(a["name"], a["mode"]) for a in arms],
                         [("with_guidance", "section"), ("without_guidance", "none")])

    def test_ablation_runs_the_fixtures_declared_second_pair(self):
        fixture = {"ablation": ["full", "full-minus-section"]}
        arms = run_eval.guidance_arms(fixture, "both", ablation=True)
        self.assertEqual([(a["name"], a["mode"]) for a in arms],
                         [("ablation_full", "full"),
                          ("ablation_full_minus_section", "full-minus-section")])
        # Both ablation arms are delivered arms, so both must SEE the token —
        # the ablation asks about marginal value in situ, not about delivery.
        for arm in arms:
            self.assertTrue(guidance.guard_expectation(arm["mode"]),
                            "every arm, control included, must report the "
                            "token IT was delivered")
        for bad in ({}, {"ablation": ["full"]}, {"ablation": "full"},
                    {"ablation": ["full", "full"]}):
            with self.subTest(fixture=bad):
                with self.assertRaises(guidance.GuidanceError):
                    run_eval.guidance_arms(bad, "both", ablation=True)

    def test_an_ablation_run_end_to_end_delivers_both_arms(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-ablation-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        self._skip_without_real_hook(root)
        eval_dir = self._guidance_fixture(
            tmp, section="bravo", ablation=["full", "full-minus-section"])
        results = tmp / "results"
        rc, out = self._run_main([eval_dir, "--arm", "both", "--ablation",
                                  "--guidance", root, "--results-dir", results,
                                  "--no-judge"])
        self.assertEqual(rc, 0, out)
        full = self._summary(results, "guidance/bravo", "ablation_full")
        minus = self._summary(results, "guidance/bravo",
                              "ablation_full_minus_section")
        self.assertTrue(full["guard"]["ok"] and minus["guard"]["ok"])
        self.assertGreater(full["bytes"], minus["bytes"],
                           "the ablation arm is the corpus MINUS the section")

    def test_an_arm_whose_name_disagrees_with_its_mode_is_rejected(self):
        for arms in ({"with_guidance": {"mode": "none"}},
                     {"without_guidance": {"mode": "section"}},
                     {"with_guidance": {"mode": "nonsense"}},
                     {"with_guidance": {}},
                     {"../escape": {"mode": "section"}}):
            with self.subTest(arms=arms):
                with self.assertRaises(guidance.GuidanceError):
                    run_eval.guidance_arms({"arms": arms}, "both")

    def test_an_unknown_arm_name_lists_the_arms_the_fixture_declares(self):
        with self.assertRaises(guidance.GuidanceError) as ctx:
            run_eval.guidance_arms({}, "with_skill")
        self.assertIn("with_guidance", str(ctx.exception))

    def test_a_skill_fixture_rejects_a_guidance_arm_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="skill-arm-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proc = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"),
             str(example_evals.FIXTURES / "workflow-path-audit"),
             "--arm", "with_guidance", "--results-dir", str(tmp)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not valid for a skill fixture", proc.stdout + proc.stderr)

    # ------------------------------------------------------------------
    # Item 9 — README and DESIGN say what this subject does
    # ------------------------------------------------------------------

    def test_readme_and_design_document_the_guidance_subject(self):
        for doc_path in (REPO_ROOT / "README.md", REPO_ROOT / "DESIGN.md"):
            text = doc_path.read_text(encoding="utf-8")
            with self.subTest(doc=doc_path.name):
                self.assertIn("## Guidance subject", text)
                for mode in guidance.MODES:
                    self.assertIn(f"`{mode}`", text,
                                  f"{doc_path.name} must name the {mode} mode")
                for phrase in ("INCONCLUSIVE", "CLAUDE_CONFIG_DIR",
                               "fleet-memory"):
                    self.assertIn(phrase, text,
                                  f"{doc_path.name} must state the {phrase} rule")

    def test_a_checkout_without_the_hook_exits_2_naming_the_hook(self):
        # The guidance subject delivers through the REAL hook, so a checkout
        # that has the manifest but not the hook is a configuration problem
        # with a named message — never a traceback out of the middle of a run.
        tmp = Path(tempfile.mkdtemp(prefix="guidance-nohook-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "checkout"
        make_guidance_checkout(root)
        hook = root / guidance.HOOK_REL
        hook.unlink(missing_ok=True)
        eval_dir = self._guidance_fixture(tmp)
        rc, out = self._run_main([eval_dir, "--arm", "with_guidance",
                                  "--guidance", root,
                                  "--results-dir", tmp / "results", "--no-judge"])
        self.assertEqual(rc, 2, out)
        self.assertIn("fleet-memory", out)

    def test_ci_checks_out_agent_guidance_so_the_hook_tests_actually_run(self):
        doc = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
        checkouts = [s for job in doc["jobs"].values()
                     for s in job.get("steps", [])
                     if (s.get("uses") or "").startswith("actions/checkout@")]
        guidance_checkout = next(
            (s for s in checkouts
             if (s.get("with") or {}).get("repository", "").endswith("/_agent-guidance")),
            None)
        self.assertIsNotNone(
            guidance_checkout,
            "without an _agent-guidance checkout the real-hook delivery tests "
            "skip in CI, and the delivery path is never exercised there")
        self.assertEqual((guidance_checkout["with"] or {}).get("path"),
                         "_agent-guidance")
        self.assertIs((guidance_checkout["with"] or {}).get("persist-credentials"),
                      False)
        self.assertIsNone(lock_problem(guidance_checkout["uses"], CI_WORKFLOW))

    # ------------------------------------------------------------------
    # A5 — an EMPTY `arms:` is a fixture error, not the default pair
    #
    # `declared = fixture.get("arms") or DEFAULT_GUIDANCE_ARMS` made the
    # `not declared` guard on the next line dead code: `arms: {}`, `arms:` and
    # `arms: []` are all falsy, so all three silently ran the default
    # `section`/`none` pair under the fixture's own name — a fixture that
    # declared arms and got someone else's.
    # ------------------------------------------------------------------

    def test_an_empty_or_null_or_list_arms_key_is_rejected_not_defaulted(self):
        for label, arms in (("{}", {}), ("null", None), ("[]", []),
                            ("[a]", ["with_guidance"]), ("a string", "both")):
            with self.subTest(arms=label):
                with self.assertRaises(guidance.GuidanceError) as ctx:
                    run_eval.guidance_arms({"arms": arms}, "both")
                self.assertIn("arms:", str(ctx.exception))

    def test_an_absent_arms_key_still_takes_the_default_pair(self):
        arms = run_eval.guidance_arms({}, "both")
        self.assertEqual([(a["name"], a["mode"]) for a in arms],
                         [("with_guidance", "section"),
                          ("without_guidance", "none")])

    def test_an_empty_arms_key_is_rejected_through_main_too(self):
        tmp = Path(tempfile.mkdtemp(prefix="guidance-emptyarms-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = self._checkout()
        for label, arms in (("{}", {}), ("null", None), ("[]", [])):
            with self.subTest(arms=label):
                eval_dir = self._guidance_fixture(tmp / label, arms=arms)
                rc, out = self._run_main(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", tmp / label / "results", "--no-judge"])
                self.assertEqual(rc, 2, out)
                self.assertIn("arms:", out)
                self.assertNotIn("Traceback", out)


    # ------------------------------------------------------------------
    # A-N2 — an arm name is a NEW directory under the run directory
    #
    # `_ARM_NAME_RE` accepted the two names that are not. Measured through
    # main() on a6d165d, one arm per run: an arm named `..` exited 0 and
    # wrote `summary.json` and `transcripts/raw.json` one level ABOVE the
    # timestamped run directory — into the per-key directory that
    # accumulates run history on the public persistent/eval-results branch — and `.`
    # wrote into the run directory itself. Round 2 tested `../esc` and
    # `a/b`; the two canonical traversal names were the ones the traversal
    # check let through.
    # ------------------------------------------------------------------

    def test_the_two_traversal_arm_names_are_refused(self):
        root = self._checkout()
        for name in (".", ".."):
            with self.subTest(arm=name):
                tmp = Path(tempfile.mkdtemp(prefix="arm-dots-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                results = tmp / "results"
                eval_dir = self._guidance_fixture(
                    tmp, arms={name: {"mode": "none"}})
                rc, out = self._run_main(
                    [eval_dir, "--arm", "both", "--guidance", root,
                     "--results-dir", results, "--no-judge"])
                self.assertEqual(rc, 2, out)
                self.assertIn("invalid arm name", out, out)
                self.assertIn(repr(name), out, out)
                self.assertNotIn("Traceback", out, out)
                self.assertFalse(
                    results.exists(),
                    f"an arm named {name!r} must write nothing at all — on "
                    "a6d165d it wrote summary.json and transcripts/raw.json "
                    "outside its own run directory")

    def test_ordinary_and_committed_arm_names_are_accepted(self):
        # The other side, including every arm name the committed guidance
        # fixtures declare — read from the fixtures rather than retyped, so
        # this cannot drift from what CI really dispatches.
        committed = set()
        for path in sorted((REPO_ROOT / "evals").glob("**/fixture.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("subject") == "guidance":
                committed |= set(doc.get("arms") or {})
        self.assertTrue(committed, "no committed guidance fixture declares "
                        "arms — this assertion must not pass vacuously")
        modes = {"none": "none", "stub": "stub", "section": "section",
                 "full": "full", "full_minus_section": "full-minus-section"}

        def mode_for(name: str) -> str:
            if name.startswith("without") or name == "none":
                return "none"
            for suffix, mode in modes.items():
                if name.endswith(suffix):
                    return mode
            return "section"

        for name in sorted(committed | {"normal", "none",
                                        "with_guidance_section", "..."}):
            with self.subTest(arm=name):
                entry = run_eval._validate_arm_entry(
                    name, {"mode": mode_for(name)})
                self.assertEqual(entry["mode"], mode_for(name))

    def test_the_arm_name_rule_is_the_new_directory_property(self):
        # Stated as the property the refusal exists for, not as a blocklist:
        # joined to a run directory and normalised the way the filesystem
        # will, an accepted name is a direct child of it, still called what
        # it was called.
        run_dir = Path(tempfile.mkdtemp(prefix="arm-property-"))
        self.addCleanup(shutil.rmtree, run_dir, ignore_errors=True)
        for name in (".", "..", "...", "a", "a-b", "_x", ".hidden", "A9"):
            joined = Path(os.path.normpath(run_dir / name))
            child = joined.parent == run_dir and joined.name == name
            with self.subTest(arm=name, is_new_child=child):
                self.assertEqual(
                    run_eval._names_a_new_directory(name), child,
                    f"{name!r}: the validator and the filesystem disagree "
                    "about whether this names a new directory under the run "
                    "directory")

    def test_an_arm_with_an_unknown_key_is_rejected_at_load_time(self):
        with self.assertRaises(guidance.GuidanceError) as ctx:
            run_eval.guidance_arms(
                {"arms": {"with_guidance": {"mode": "section",
                                            "objective_check": []}}}, "both")
        self.assertIn("objective_check", str(ctx.exception))

    # N-c / A-N3. The default budgets a fixture inherits when it omits the
    # knobs: `args.timeout or fixture.get("timeout_s", 600)` on the agent leg,
    # `(fixture.get("guard") or {}).get("timeout_s", 300)` on the guard leg,
    # `judge_cfg.get("timeout_s", 120)` on the judge, and `deliver`'s own
    # `timeout: int = 120` on the hook. A five-arm fixture that inherits all
    # of them needs 5 x 1140 s = 95 min and does NOT fit in eval.yml's 45.
    #
    # Read out of the harness source, not written down here (#287): a default
    # the harness changes must move the budget with it.
    _RUN_EVAL = HARNESS_DIR / "run_eval.py"
    _ARM_FNS = ("_run_arm", "_run_guidance_arm")
    DEFAULT_AGENT_BUDGET_S = _harness_default(
        _RUN_EVAL, _ARM_FNS, "fixture", "timeout_s")
    DEFAULT_GUARD_BUDGET_S = _harness_default(
        _RUN_EVAL, ("_run_guidance_arm",), "fixture.get('guard') or {}",
        "timeout_s")
    # Both arm functions judge through `_call_judge` (#371), the one site.
    DEFAULT_JUDGE_BUDGET_S = _harness_default(
        _RUN_EVAL, ("_call_judge",), "judge_cfg", "timeout_s")
    # The per-arm `setup:` hook (`run_setup`, skill arms only).
    DEFAULT_SETUP_BUDGET_S = _harness_default(
        _RUN_EVAL, ("run_setup",), "fixture", "setup_timeout_s")
    # `deliver(..., timeout: int = 120)` in harness/guidance.py, and
    # `_run_guidance_arm` passes no override — a fixed per-arm cost, spent
    # running the real hook. Measured in round 3: a hook that sleeps gives
    # rc 2 at 120.2 s.
    DELIVER_BUDGET_S = _signature_default(
        HARNESS_DIR / "guidance.py", "deliver", "timeout")

    @staticmethod
    def _judge_branch_keys() -> tuple[str, ...]:
        """The fixture key(s) `_run_guidance_arm`'s judge branch actually
        reads, parsed out of the harness.

        A-N3-2. A-N3 said "use the key the harness reads" and the helper then
        hardcoded `judge_rubric`, with nothing tying the two together — the
        fit test parses `_run_guidance_arm` for the `setup_timeout_s` claim
        and did not parse it for this one. Measured: changing the harness to
        `fixture.get("judge_prompt", fixture.get("judge_rubric"))` and adding
        a committed five-arm fixture that declares `judge_prompt:` and
        `judge: {timeout_s: 2700}` left the fit test green at 1 800 s against
        a real worst case of 15 300 s under a 2 700 s job. The A-N3 defect
        verbatim, one key-spelling over.
        """
        tree = ast.parse((HARNESS_DIR / "run_eval.py").read_text(
            encoding="utf-8"))
        arm_fn = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_run_guidance_arm")
        keys = []
        for node in ast.walk(arm_fn):
            if not isinstance(node, ast.If):
                continue
            if not any(ast.unparse(sub) == "args.no_judge"
                       for sub in ast.walk(node.test)):
                continue
            for call in ast.walk(node.test):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "get"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "fixture"
                        and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)):
                    keys.append(call.args[0].value)
        return tuple(dict.fromkeys(keys))

    @staticmethod
    def _turns(fixture: dict) -> int:
        """Agent calls per arm: the prompt plus one per `followups:` entry.
        `run_agent` gives EVERY turn the whole `timeout`, so a follow-up is a
        second full agent budget, not a rounding error (#287)."""
        return 1 + len(fixture.get("followups") or [])

    @classmethod
    def _skill_fixture_budget(cls, fixture: dict) -> tuple[int, int]:
        """(per-arm seconds, arm count) for a skill fixture, defaults and all.

        Every arm `_run_arm` runs spends: one agent budget per turn, the
        `setup:` hook when the fixture declares one (`run_setup`, called from
        `materialize_workspace` for each arm), and the judge — a skill
        fixture must declare `judge_rubric:` and eval.yml passes no
        `--no-judge`. `--arm both` is the two skill arms, one trial.
        """
        agent = (fixture.get("timeout_s", cls.DEFAULT_AGENT_BUDGET_S)
                 * cls._turns(fixture))
        setup = (fixture.get("setup_timeout_s", cls.DEFAULT_SETUP_BUDGET_S)
                 if fixture.get("setup") else 0)
        judge = (fixture.get("judge") or {}).get(
            "timeout_s", cls.DEFAULT_JUDGE_BUDGET_S)
        return agent + setup + judge, 2

    @classmethod
    def _guidance_fixture_budget(cls, fixture: dict) -> tuple[int, int]:
        """(per-arm seconds, arm count) for a guidance fixture, defaults and
        all — a fixture that omits the knobs is the expensive case, not a free
        one.

        EVERY per-arm cost `_run_guidance_arm` can spend, because a budget
        that counts some of them is the defect this exists to prevent, one
        level down. It used to count two of four: a planted five-arm fixture
        with `judge.timeout_s: 2700` passed at 1 200 s against a real worst
        case of 14 700 s under a 2 700 s job.

        `setup_timeout_s` is NOT among them and is not a gap: `run_setup` is
        called from `_run_arm` and the objective-only branch only, never from
        the guidance path, so a guidance fixture's `setup_timeout_s` is
        validated and then inert.

        The judge is counted whenever the fixture declares any key
        `_run_guidance_arm`'s judge branch reads — READ OUT OF THE HARNESS by
        `_judge_branch_keys`, not written down here, so the budget uses
        whatever the branch tests rather than whatever it tested when this
        was written. eval.yml passes no `--no-judge`, so a declared rubric IS
        spent; it passes no `--timeout` either, so the agent leg is the
        fixture's own knob.
        """
        agent = (fixture.get("timeout_s", cls.DEFAULT_AGENT_BUDGET_S)
                 * cls._turns(fixture))
        guard = (fixture.get("guard") or {}).get(
            "timeout_s", cls.DEFAULT_GUARD_BUDGET_S)
        judge = ((fixture.get("judge") or {}).get(
            "timeout_s", cls.DEFAULT_JUDGE_BUDGET_S)
            if any(fixture.get(key) for key in cls._judge_branch_keys())
            else 0)
        arms = fixture.get("arms")
        count = len(arms) if isinstance(arms, dict) and arms else 2
        return agent + guard + judge + cls.DELIVER_BUDGET_S, count

    def test_the_guidance_budget_counts_every_per_arm_cost(self):
        # A-N3. The budget counted two of the four costs `_run_guidance_arm`
        # can spend, and `judge.timeout_s` is the one with room to hide in: a
        # planted five-arm fixture with `judge: {timeout_s: 2700}` passed the
        # fit test at 1 200 s against a real worst case of 14 700 s under a
        # 2 700 s job. Asserted as arithmetic here so the addition is pinned
        # even while no committed fixture declares a rubric.
        base = {"timeout_s": 10, "guard": {"timeout_s": 20},
                "judge": {"timeout_s": 30}, "setup_timeout_s": 999,
                "arms": {"a": {}, "b": {}, "c": {}}}
        deliver = self.DELIVER_BUDGET_S

        # A-N3-2: the keys come from the harness, and the assertions below
        # are driven by them rather than by a spelling written down here.
        judge_keys = self._judge_branch_keys()
        self.assertTrue(
            judge_keys,
            "no `fixture.get(...)` found in _run_guidance_arm's judge branch "
            "— the budget would then count the judge for nothing, and this "
            "assertion must not pass vacuously. Has the branch's shape "
            "changed?")

        per_arm, arms = self._guidance_fixture_budget(base)
        self.assertEqual(arms, 3)
        self.assertEqual(
            per_arm, deliver + 10 + 20,
            "without a `judge_rubric:` the judge is never called — "
            "`_run_guidance_arm` branches on that key — so it costs nothing")

        for key in judge_keys:
            with self.subTest(judge_key=key):
                per_arm, _ = self._guidance_fixture_budget(
                    dict(base, **{key: "score it"}))
                self.assertEqual(
                    per_arm, deliver + 10 + 20 + 30,
                    f"a declared `{key}:` IS spent: _run_guidance_arm's judge "
                    "branch reads that key and eval.yml passes no "
                    "`--no-judge`, so every arm pays `judge.timeout_s` as "
                    "well")

        # The defaults, all four of them, for a fixture that omits every knob.
        per_arm, arms = self._guidance_fixture_budget(
            {judge_keys[0]: "score it"})
        self.assertEqual(arms, 2, "a fixture with no `arms:` runs the default "
                         "treatment/control pair")
        self.assertEqual(per_arm, deliver + self.DEFAULT_AGENT_BUDGET_S
                         + self.DEFAULT_GUARD_BUDGET_S
                         + self.DEFAULT_JUDGE_BUDGET_S)

        # `setup_timeout_s` is inert on this path and must NOT be counted:
        # `run_setup` is called from `_run_arm` and the objective-only branch
        # only. Parsed rather than asserted from memory.
        guidance_arm = next(
            node for node in ast.walk(ast.parse(
                (HARNESS_DIR / "run_eval.py").read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_guidance_arm")
        self.assertNotIn(
            "run_setup",
            {n.func.id for n in ast.walk(guidance_arm)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)},
            "_run_guidance_arm now calls run_setup, so `setup_timeout_s` is a "
            "real per-arm cost and the budget must count it")

    @staticmethod
    def _dispatchable_by_run_eval(fixture: dict) -> bool:
        """Whether `run_eval.py` would run this fixture at all: a guidance
        fixture, or a skill fixture with the `skill` and `prompt` strings it
        requires of every non-objective-only arm. The propagation probe and
        the bridge canary live under evals/ too but have their own runners
        (`run_propagation.py`, `run_canary.py`); eval.yml handed to
        `run_eval.py` they exit 2 on the missing field before any agent call,
        so they spend no budget."""
        if fixture.get("subject") == "guidance":
            return True
        return (isinstance(fixture.get("skill"), str)
                and isinstance(fixture.get("prompt"), str))

    def test_every_fixture_fits_inside_the_workflow_job_timeout(self):
        # #287. This used to check guidance fixtures only and counted one
        # agent call per arm, so rename-pdfs (a skill fixture with one
        # `followups:` entry and the default 600 s) needed
        # 2 x (2 x 600 + 120) = 2640 s of a 2700 s job and nothing noticed.
        # Any fixture under evals/ is dispatchable, and a job that outlives
        # `timeout-minutes` dies with no summary and no artifact — the failure
        # mode that is hardest to read in CI.
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        job_budget_s = doc["jobs"]["eval"]["timeout-minutes"] * 60
        # The budget below assumes the invocation eval.yml really makes:
        # `--arm both` (two skill arms, or the guidance fixture's declared
        # arms), one trial, no `--timeout` override and no `--no-judge`.
        # Parsed from the step, so a flag added there fails here instead of
        # silently making the arithmetic wrong.
        step = next(step for step in doc["jobs"]["eval"]["steps"]
                    if step.get("id") == "eval")
        tokens = shlex.split(step["run"].replace("\\\n", " "))
        self.assertIn("run_eval.py", " ".join(tokens))
        self.assertEqual(tokens[tokens.index("--arm") + 1], "both")
        for flag in ("--trials", "--timeout", "--no-judge"):
            self.assertNotIn(
                flag, tokens,
                f"eval.yml's run step now passes {flag}: the per-fixture "
                "budget in this test no longer describes what a leg spends")
        checked = {"skill": 0, "guidance": 0}
        skipped = []
        # This repository's fixtures plus the example ones
        # (test/example_evals.py), so a skill fixture is always checked.
        root = example_evals.checkout()
        for path in sorted((root / "evals").glob("**/fixture.yaml")):
            fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
            name = str(path.parent.relative_to(root))
            if fixture.get("subject") == "any":
                # Subject-agnostic: eval.yml passes no --skill or --section,
                # so run_eval.py refuses it (exit 2) before any agent call.
                continue
            if not self._dispatchable_by_run_eval(fixture):
                skipped.append(name)
                continue
            guidance_subject = fixture.get("subject") == "guidance"
            checked["guidance" if guidance_subject else "skill"] += 1
            per_arm, arms = (self._guidance_fixture_budget(fixture)
                             if guidance_subject
                             else self._skill_fixture_budget(fixture))
            worst_case = per_arm * arms
            with self.subTest(fixture=name):
                self.assertLessEqual(
                    worst_case, job_budget_s * 0.75,
                    f"{name}: {arms} arms x {per_arm}s per arm = "
                    f"{worst_case}s does not leave room inside eval.yml's "
                    f"{job_budget_s}s job timeout for the CLI install and the "
                    "badge commit (0.75 x the job timeout is the ceiling). "
                    f"The agent leg runs {self._turns(fixture)} turn(s) — the "
                    "prompt plus each `followups:` entry — and EVERY turn "
                    "gets the whole `timeout_s:` (default "
                    f"{self.DEFAULT_AGENT_BUDGET_S}s). Lower `timeout_s:`, "
                    "drop a follow-up or split the fixture. Per-arm extras "
                    "for a skill fixture: the judge "
                    f"({self.DEFAULT_JUDGE_BUDGET_S}s default) and a "
                    f"`setup:` hook ({self.DEFAULT_SETUP_BUDGET_S}s default). "
                    f"For a guidance fixture: deliver ({self.DELIVER_BUDGET_S}s)"
                    f", the guard ({self.DEFAULT_GUARD_BUDGET_S}s default) and "
                    "the judge when it declares "
                    + " or ".join(f"`{key}:`"
                                  for key in self._judge_branch_keys())
                    + " (eval.yml passes no `--no-judge`; those are the keys "
                    "_run_guidance_arm's judge branch reads, parsed out of "
                    "the harness).")
        for subject, count in checked.items():
            self.assertGreater(count, 0,
                               f"no committed {subject} fixture — this test "
                               "would pass vacuously for that subject")
        # A fixture skipped for not being run_eval's is named, so a real
        # fixture that loses its `skill:`/`prompt:` cannot slip out of the
        # budget unnoticed.
        self.assertEqual(
            sorted(skipped),
            ["evals/guidance-bridge-canary", "evals/propagation"],
            "the fixtures run_eval.py does not run changed; each is either "
            "another runner's (add it here) or has lost `skill:`/`prompt:`")

    def test_a_followup_turn_costs_a_whole_agent_budget(self):
        # #287, as arithmetic: both subjects multiply the agent leg by the
        # turn count and by nothing else, and the skill path counts the judge
        # and a declared `setup:`.
        base = {"timeout_s": 100, "judge": {"timeout_s": 7}}
        self.assertEqual(self._skill_fixture_budget(base), (107, 2))
        with_followups = dict(base, followups=["a", "b"])
        self.assertEqual(self._skill_fixture_budget(with_followups),
                         (307, 2))
        self.assertEqual(
            self._skill_fixture_budget(
                dict(base, setup="true", setup_timeout_s=11)),
            (118, 2))
        self.assertEqual(
            self._skill_fixture_budget({"followups": ["a"]}),
            (2 * self.DEFAULT_AGENT_BUDGET_S + self.DEFAULT_JUDGE_BUDGET_S, 2),
            "an omitted `timeout_s:` is the harness default, per turn")
        gbase = {"timeout_s": 10, "guard": {"timeout_s": 20},
                 "arms": {"a": {}, "b": {}}}
        self.assertEqual(
            self._guidance_fixture_budget(dict(gbase, followups=["x"])),
            (self.DELIVER_BUDGET_S + 2 * 10 + 20, 2),
            "a guidance arm's agent leg runs every turn; deliver and the "
            "guard run once")

    def test_the_job_timeout_comment_accounts_for_a_guidance_dispatch(self):
        # The two comments the round-1 review found stale: both described a
        # 2-arm skill A/B only, and a guidance dispatch is one agent call AND
        # one guard probe per declared arm.
        header = self._eval_header_prose().lower()
        self.assertTrue("guard probe" in header,
                        "the cost paragraph must say a guidance dispatch also "
                        "spends a guard probe per arm")
        self.assertFalse(
            "a full run is 2 agent arms + 2 judge calls" in header,
            "that describes a skill A/B only; a guidance dispatch is one "
            "agent call AND one guard probe per declared arm")
        self.assertTrue(
            "scored leg" in header,
            "the header must say the report's cost column counts the scored "
            "leg only — the guard probes are real calls and are not in it")
        text = EVAL_WORKFLOW.read_text(encoding="utf-8")
        comment = text[:text.index("    timeout-minutes: 45")]
        self.assertFalse(
            "2 arms x 10 min agent budget + judges + setup" in text,
            "the stale timeout-minutes comment must be gone")
        self.assertTrue("per\n    # DECLARED arm" in comment,
                        "the timeout-minutes comment must account for a "
                        "guidance fixture's per-declared-arm budget")
