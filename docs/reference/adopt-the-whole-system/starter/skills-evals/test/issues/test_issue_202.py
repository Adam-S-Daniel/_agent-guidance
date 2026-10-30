#!/usr/bin/env python3
"""Issue #202 — a tier's VENDOR-DEFAULT model takes its seat at once, and
the version it superseded retires after a buffer of complete ISO weeks
under the exit bar.

Three pieces, each tested here: `scripts/probe_model_defaults.py` (asks the
freshly installed Claude Code CLI, with no credential, which model each
family alias resolves to — #203), `harness/roster.py`'s
`defaults_doc`/`--defaults` (seats that default and retires what it
superseded), and `harness/timeweeks.py`'s complete-week helper the buffer is
counted with.

Hermetic, like the rest of the suite: the probe runs against
`test/fake-claude-init` (through a small wrapper that fixes its mode, because
the probe scrubs the environment it would otherwise be read from), never the
real CLI, and every roster call runs on a frozen `now`.

Discovered and run by test/run_tests.py (see `build_suite`/`DISCOVERY_DIR`
there); also runnable on its own with `python3 test/issues/test_issue_202.py`,
which really runs it — the `unittest.main()` at the bottom is what makes
that true.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
SCRIPTS_DIR = REPO_ROOT / "scripts"
POLICY = REPO_ROOT / "evals" / "roster-policy.yml"
EVAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval.yml"
PROBE = SCRIPTS_DIR / "probe_model_defaults.py"
FAKE_CLAUDE_INIT = TEST_DIR / "fake-claude-init"
FAKE_VERSION_LINE = "fake-claude-init 0.0.0 (hermetic propagation stub)"

sys.path.insert(0, str(HARNESS_DIR))
import roster  # noqa: E402
import timeweeks  # noqa: E402

sys.path.insert(0, str(SCRIPTS_DIR))
import render_roster_yaml  # noqa: E402

sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402

# A fixed commit time for every throwaway repo's commits: a sha that depends
# on the wall clock can contain any digit run, so the fixture pins it.
FIXED_GIT_DATES = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
                   "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"}


def _touches_number(calls, number):
    """True when a call carries `number` as a whole token (`pr edit 101`,
    `#101`), never as a run of digits inside a longer word such as the
    `--match-head-commit <sha>` value a generated sha can supply."""
    pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(str(number))}(?![0-9A-Za-z])")
    return any(pattern.search(c) for c in calls)

#: The variables a scrubbed probe environment may carry. `LC_CTYPE` is not
#: the probe's: Python sets it itself at start-up when the locale is `C`
#: (PEP 538 coercion), and the wrapper that records the environment is Python.
#: The two switches the probe sets on purpose (#203 probe round 1, F5): no
#: auto-update, no nonessential traffic. Fixed values, never a credential —
#: and the only names allowed past `CREDENTIAL_NAME` below, by exact name.
PROBE_FLAGS = {"DISABLE_AUTOUPDATER": "1",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
PROBE_ENV_ALLOWED = {"PATH", "HOME", "LANG", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                     "XDG_DATA_HOME", "XDG_STATE_HOME", "LC_CTYPE", *PROBE_FLAGS}
CREDENTIAL_NAME = re.compile(r"^(ANTHROPIC_|CLAUDE_|AWS_|GOOGLE_)|TOKEN|KEY",
                             re.IGNORECASE)


#: The `roster-pr` job's step that manages the pull request. Round 6 put
#: the roster App's token-mint step ahead of it, so it is found by name,
#: never by position.
MANAGE_STEP = "Manage the roster pull request"


def _manage_step(doc):
    return next(s for s in doc["jobs"]["roster-pr"]["steps"]
                if s.get("name") == MANAGE_STEP)


def credential_like(names) -> list[str]:
    """Every credential-shaped name except the probe's own fixed flags."""
    return sorted(n for n in names if CREDENTIAL_NAME.search(n) and n not in PROBE_FLAGS)


class _ProbeFixture(unittest.TestCase):
    """Runs `scripts/probe_model_defaults.py` against the hermetic fake."""

    #: What the fake resolves the shipped ladder's aliases to.
    RESOLVED = {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-5",
                "opus": "claude-opus-5-5", "fable": "claude-fable-5-1"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _wrapper(self, mode="simulate", *, dump=None, version=None, extra_env=None):
        """A `claude` that records the environment it was given (one JSON
        line per call) and then runs the fake in `mode`. The mode cannot
        reach the fake through the environment: the probe scrubs it."""
        path = self.tmp / f"claude-{mode}"
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"dump = {str(dump) if dump else None!r}\n"
            "if dump:\n"
            "    home = os.environ.get('HOME', '')\n"
            "    record = {'env': dict(os.environ), 'cwd': os.getcwd(),\n"
            "              'home_is_dir': os.path.isdir(home),\n"
            "              'home_listing': sorted(os.listdir(home))\n"
            "              if os.path.isdir(home) else None}\n"
            "    with open(dump, 'a', encoding='utf-8') as f:\n"
            "        f.write(json.dumps(record) + '\\n')\n"
            f"if {version!r} is not None and '--version' in sys.argv:\n"
            f"    sys.stdout.write({version!r})\n"
            "    sys.exit(0)\n"
            f"os.environ['FAKE_INIT_MODE'] = {mode!r}\n"
            f"os.environ.update({dict(extra_env or {})!r})\n"
            f"os.execv(sys.executable, [sys.executable, {str(FAKE_CLAUDE_INIT)!r}]"
            " + sys.argv[1:])\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def _policy(self, tiers):
        path = self.tmp / "policy.yml"
        path.write_text(yaml.safe_dump({"tiers": tiers}), encoding="utf-8")
        return path

    def _probe(self, claude, *extra, env=None, timeout=120):
        out = self.tmp / "defaults.json"
        done = subprocess.run(
            [sys.executable, str(PROBE), "--claude", str(claude), "--out", str(out),
             *extra], capture_output=True, text=True, timeout=timeout,
            env=env if env is not None else dict(os.environ))
        document = (json.loads(out.read_text(encoding="utf-8"))
                    if out.is_file() else None)
        return done, document


class TestProbeScript(_ProbeFixture):
    """`scripts/probe_model_defaults.py` (#203): the vendor defaults come
    from the freshly installed CLI, probed with NO credential."""

    def test_the_shipped_ladder_resolves_four_and_skips_mythos(self):
        done, doc = self._probe(self._wrapper())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], self.RESOLVED)
        self.assertEqual(doc["skipped"], ["mythos"])
        self.assertEqual(doc["errors"], {})
        self.assertEqual(doc["harness_version"], FAKE_VERSION_LINE)
        self.assertIsNotNone(timeweeks.parse_ts(doc["probed_at"]))
        self.assertEqual(set(doc), {"probed_at", "harness_version", "defaults",
                                    "skipped", "errors"})

    def test_the_aliases_come_from_the_policy_ladder(self):
        policy = self._policy(["haiku", ["opus", "zeta"]])
        done, doc = self._probe(self._wrapper(), "--policy", str(policy))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"haiku": self.RESOLVED["haiku"],
                                           "opus": self.RESOLVED["opus"]})
        self.assertEqual(doc["skipped"], ["zeta"])

    def test_a_cli_that_exits_before_init_is_no_init(self):
        policy = self._policy(["opus", "sonnet"])
        for mode in ("crash", "no-init"):
            with self.subTest(mode=mode):
                done, doc = self._probe(self._wrapper(mode), "--policy", str(policy))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(doc["defaults"], {})
                self.assertEqual(doc["errors"], {"opus": "no-init",
                                                  "sonnet": "no-init"})

    def test_a_cli_that_hangs_before_init_times_out(self):
        policy = self._policy(["opus"])
        done, doc = self._probe(self._wrapper("probe-hang"), "--policy", str(policy),
                                "--timeout", "0.5", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["errors"], {"opus": "timeout"})
        self.assertEqual(doc["defaults"], {})

    def test_a_junk_model_is_bad_model_and_never_echoed(self):
        policy = self._policy(["opus"])
        done, doc = self._probe(self._wrapper("junk-model"), "--policy", str(policy))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["errors"], {"opus": "bad-model"})
        self.assertEqual(doc["defaults"], {})
        for text in (done.stdout, done.stderr, json.dumps(doc)):
            self.assertNotIn("::error::", text)
            self.assertNotIn("Opus 5.5", text)

    def test_junk_lines_before_the_init_event_are_skipped(self):
        policy = self._policy(["opus"])
        done, doc = self._probe(self._wrapper("garbage-lines"), "--policy", str(policy))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"]})

    def test_the_cli_is_terminated_after_the_init_event(self):
        # The fake hangs for 600 s after init; a probe that waited for it
        # would blow this call's 60 s timeout long before its own 120 s one.
        policy = self._policy(["opus", "sonnet"])
        done, doc = self._probe(self._wrapper("init-then-hang"), "--policy",
                                str(policy), "--timeout", "120", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"],
                                           "sonnet": self.RESOLVED["sonnet"]})

    def test_a_cli_that_ignores_sigterm_is_killed(self):
        policy = self._policy(["opus"])
        done, doc = self._probe(self._wrapper("init-ignore-term"), "--policy",
                                str(policy), "--timeout", "120", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"]})

    def test_the_environment_is_scrubbed_and_home_is_a_fresh_temp_dir(self):
        dump = self.tmp / "env.jsonl"
        env = dict(os.environ, ANTHROPIC_API_KEY="k", ANTHROPIC_AUTH_TOKEN="t",
                   CLAUDE_CODE_OAUTH_TOKEN="t", AWS_SECRET_ACCESS_KEY="k",
                   GOOGLE_APPLICATION_CREDENTIALS="/x", GH_TOKEN="t",
                   SOME_API_KEY="k", HOME=str(self.tmp / "real-home"))
        policy = self._policy(["opus", "sonnet"])
        done, doc = self._probe(self._wrapper(dump=dump), "--policy", str(policy),
                                env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(doc["defaults"]), 2)
        records = [json.loads(line) for line in
                   dump.read_text(encoding="utf-8").splitlines()]
        # `--version` once, then one call per alias.
        self.assertEqual(len(records), 3)
        homes = set()
        for record in records:
            names = set(record["env"])
            self.assertLessEqual(names, PROBE_ENV_ALLOWED, names - PROBE_ENV_ALLOWED)
            self.assertEqual(credential_like(names), [])
            for flag, value in PROBE_FLAGS.items():
                self.assertEqual(record["env"].get(flag), value, flag)
            home = record["env"]["HOME"]
            self.assertNotEqual(home, env["HOME"])
            self.assertTrue(record["home_is_dir"])
            self.assertEqual(record["home_listing"],
                             sorted(p for p in record["home_listing"]
                                    if p in (".config", ".cache", ".local")))
            for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                        "XDG_STATE_HOME"):
                self.assertTrue(record["env"][key].startswith(home + os.sep), key)
            self.assertEqual(record["env"]["LANG"], "C")
            self.assertEqual(record["cwd"], os.path.realpath(home))
            homes.add(home)
            # Removed afterwards.
            self.assertFalse(Path(home).exists(), home)
        self.assertEqual(len(homes), 3, "every CLI call gets its own fresh HOME")

    def test_scrubbed_env_keeps_nothing_from_the_parent(self):
        import probe_model_defaults
        parent = {"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "CLAUDE_CONFIG_DIR": "/c",
                  "AWS_PROFILE": "p", "GOOGLE_CLOUD_PROJECT": "g", "MY_TOKEN": "t",
                  "DEPLOY_KEY": "k", "HOME": "/root", "USER": "u"}
        with mock.patch.dict(os.environ, parent, clear=True):
            env = probe_model_defaults.scrubbed_env(Path("/tmp/probe-home"))
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HOME"], "/tmp/probe-home")
        self.assertEqual(env["LANG"], "C")
        self.assertLessEqual(set(env), PROBE_ENV_ALLOWED - {"LC_CTYPE"})
        # F5 (#203 probe round 1): no auto-update, no nonessential traffic,
        # and still nothing credential-like past those two fixed flags.
        for flag, value in PROBE_FLAGS.items():
            self.assertEqual(env.get(flag), value, flag)
        self.assertEqual(credential_like(env), [])
        self.assertNotIn("/c", env.values())

    def test_a_missing_binary_is_a_usage_error(self):
        # F7 (#203 probe round 1): `--claude` is resolved once, up front; a
        # binary that is not there is the caller's mistake, said plainly,
        # and no document is written (eval.yml writes its own stand-in, so
        # the roster still freezes every family — see
        # TestWorkflowProbeFallbackDocument).
        missing = self.tmp / "no-such-claude"
        policy = self._policy(["opus", "sonnet"])
        done, doc = self._probe(missing, "--policy", str(policy))
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIsNone(doc)
        self.assertIn("--claude", done.stderr)
        self.assertIn("no executable", done.stderr)

    def test_the_harness_version_is_sanitised_first_line(self):
        policy = self._policy(["opus"])
        done, doc = self._probe(self._wrapper(version="2.1.283 `(Claude Code)` ::x\n"
                                                      "second line\n"),
                                "--policy", str(policy))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["harness_version"], "2.1.283 (Claude Code) x")
        done, doc = self._probe(self._wrapper(version="`::`\n"), "--policy", str(policy))
        self.assertIsNone(doc["harness_version"])

    def test_an_unwritable_out_is_a_usage_error(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("x", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(PROBE), "--claude", str(self._wrapper()),
             "--policy", str(self._policy(["opus"])),
             "--out", str(blocker / "defaults.json")],
            capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0)

    def test_the_fetch_script_is_gone(self):
        self.assertFalse((SCRIPTS_DIR / "fetch_model_defaults.py").exists())


class TestCompleteWeeks(unittest.TestCase):
    """`timeweeks.complete_weeks_since` — the buffer's week count."""

    def test_a_week_counts_once_it_has_ended_and_began_after_the_start(self):
        count, labels = timeweeks.complete_weeks_since(
            datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
            datetime(2026, 10, 6, 6, tzinfo=timezone.utc), 3)
        self.assertEqual((count, labels), (1, ["2026-W40"]))

    def test_the_boundaries_are_inclusive(self):
        # Starts exactly on W40's Monday midnight; ends exactly on W41's.
        count, labels = timeweeks.complete_weeks_since(
            datetime(2026, 9, 28, tzinfo=timezone.utc),
            datetime(2026, 10, 5, tzinfo=timezone.utc), 1)
        self.assertEqual((count, labels), (1, ["2026-W40"]))

    def test_nothing_complete_yet(self):
        self.assertEqual(timeweeks.complete_weeks_since(
            datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
            datetime(2026, 10, 4, 23, tzinfo=timezone.utc), 1), (0, []))
        # An end before the start is not a negative count.
        self.assertEqual(timeweeks.complete_weeks_since(
            datetime(2026, 9, 26, tzinfo=timezone.utc),
            datetime(2026, 9, 1, tzinfo=timezone.utc), 1), (0, []))

    def test_only_the_most_recent_labels_are_returned(self):
        count, labels = timeweeks.complete_weeks_since(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 6, tzinfo=timezone.utc), 2)
        self.assertEqual(count, 4)  # W37, W38, W39, W40
        self.assertEqual(labels, ["2026-W39", "2026-W40"])
        self.assertEqual(timeweeks.complete_weeks_since(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 6, tzinfo=timezone.utc), 0), (4, []))


class TestDefaultsProvenance(unittest.TestCase):
    """Test _defaults_provenance function: it formats the source field and
    probed_at timestamp from a defaults document, stripping "(Claude Code)"
    suffix from the version if present."""

    def test_version_with_claude_code_suffix_is_stripped(self):
        """A version "2.1.283 (Claude Code)" should produce
        "claude-code-cli 2.1.283"."""
        source, probed_at = roster._defaults_provenance({
            "harness_version": "2.1.283 (Claude Code)",
            "probed_at": "2026-09-28T11:26:18Z"
        })
        self.assertEqual(source, "claude-code-cli 2.1.283")
        self.assertEqual(probed_at, "2026-09-28T11:26:18Z")

    def test_version_without_suffix_stays_the_same(self):
        """A version "2.1.283" without the suffix should still produce
        "claude-code-cli 2.1.283"."""
        source, probed_at = roster._defaults_provenance({
            "harness_version": "2.1.283",
            "probed_at": "2026-09-28T11:26:18Z"
        })
        self.assertEqual(source, "claude-code-cli 2.1.283")
        self.assertEqual(probed_at, "2026-09-28T11:26:18Z")

    def test_non_matching_version_gives_version_unknown(self):
        """A non-matching version value should give "claude-code-cli
        (version unknown)"."""
        source, probed_at = roster._defaults_provenance({
            "harness_version": None,
            "probed_at": "2026-09-28T11:26:18Z"
        })
        self.assertEqual(source, "claude-code-cli (version unknown)")
        self.assertEqual(probed_at, "2026-09-28T11:26:18Z")

    def test_claude_code_suffix_is_case_sensitive(self):
        """Only exact " (Claude Code)" suffix (case-sensitive) is stripped."""
        source, probed_at = roster._defaults_provenance({
            "harness_version": "2.1.283 (claude code)",
            "probed_at": "2026-09-28T11:26:18Z"
        })
        # Since the version matches the regex, it should be used as-is.
        # The lowercase version does NOT match the suffix pattern.
        self.assertEqual(source, "claude-code-cli 2.1.283 (claude code)")


class _RosterFixture(unittest.TestCase):
    """Synthetic catalogue and census shaped like the 2026-09-22 census."""

    #: Sunday of 2026-W39. opus-5-5 shipped a day earlier.
    NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    #: Tuesday of 2026-W41: W40 (Sep 28 - Oct 4) is complete by then.
    LATER = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    ENTER = ["2026-W36", "2026-W37", "2026-W38", "2026-W39"]

    @staticmethod
    def _model(model_id, display_name, created):
        return {"id": model_id, "display_name": display_name, "created_at": created}

    @classmethod
    def _models_doc(cls, extra=(), drop=()):
        models = [
            cls._model("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01T00:00:00Z"),
            cls._model("claude-sonnet-4-6", "Claude Sonnet 4.6", "2025-11-24T00:00:00Z"),
            cls._model("claude-sonnet-5", "Claude Sonnet 5", "2026-02-01T00:00:00Z"),
            cls._model("claude-opus-4-8", "Claude Opus 4.8", "2026-01-15T00:00:00Z"),
            cls._model("claude-opus-5", "Claude Opus 5", "2026-04-01T00:00:00Z"),
            cls._model("claude-opus-5-5", "Claude Opus 5.5", "2026-09-26T12:00:00Z"),
            cls._model("claude-fable-5", "Claude Fable 5", "2026-05-01T00:00:00Z"),
            cls._model("claude-fable-5-1", "Claude Fable 5.1", "2026-09-01T00:00:00Z"),
        ]
        models = [m for m in models if m["id"] not in drop] + list(extra)
        return {"fetched_at": "2026-09-27T11:00:00Z", "models": models}

    @classmethod
    def _census(cls, generated_at="2026-09-22T06:00:00Z", extra=None):
        # 454 + 452 + 63 + 30 = 999 a week: 45.4%, 45.2%, 6.3%, 3.0%.
        counts = {
            "claude-sonnet-5": {w: 454 for w in cls.ENTER},
            "claude-opus-5": {w: 452 for w in cls.ENTER},
            "claude-haiku-4-5": {w: 63 for w in cls.ENTER},
            "claude-fable-5-1": {w: 30 for w in cls.ENTER},
        }
        for model_id, by_week in (extra or {}).items():
            counts.setdefault(model_id, {}).update(by_week)
        return {"generated_at": generated_at, "weeks": [], "counts": counts}

    @classmethod
    def _later_census(cls, opus5_w40, *, opus55_w40=0, generated_at="2026-10-06T06:00:00Z"):
        """The census a week on: W40 complete, opus-5 at `opus5_w40` turns."""
        w40 = {"claude-sonnet-5": 500, "claude-opus-5": opus5_w40,
               "claude-opus-5-5": opus55_w40, "claude-haiku-4-5": 60}
        return cls._census(generated_at=generated_at,
                           extra={m: {"2026-W40": n} for m, n in w40.items()})

    PREVIOUS = {"schema": 1,
                "arms": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}],
                "judge": {"id": "claude-fable-5-1", "is_arm": False},
                "preflight": {"id": "claude-haiku-4-5"},
                "catalogue_seen": []}

    #: `scripts/probe_model_defaults.py`'s document (#203): model ids, read
    #: off the unauthenticated CLI's init event.
    DEFAULTS = {"probed_at": "2026-09-27T10:00:00Z",
                "harness_version": "2.1.283 (Claude Code)",
                "defaults": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"},
                "skipped": ["mythos"], "errors": {}}
    #: What the published block and every reason name as the source.
    SOURCE = "claude-code-cli 2.1.283"

    #: The shipped policy since 2026-10-25 retires a superseded arm at once
    #: (buffer 0) and measures 2-week windows. These tests exercise the
    #: buffer and the 4/8-week windows, which the machinery keeps, so they
    #: pin the values the machinery was written against; the shipped values
    #: are asserted in `TestShippedPolicyFollowsCurrentUse`.
    MACHINERY_POLICY = {"superseded_exit_weeks": 1, "arm_enter_window_weeks": 4,
                        "arm_exit_window_weeks": 8}

    @classmethod
    def _policy(cls, **overrides):
        policy = roster.load_policy(POLICY)
        policy.update(cls.MACHINERY_POLICY)
        policy.update(overrides)
        return policy

    def _compute(self, *, defaults=None, census=None, now=None, previous="default",
                 models=None, policy=None):
        warnings: list[str] = []
        result = roster.compute_roster(
            models_doc=models or self._models_doc(),
            census_doc=self._census() if census is None else census,
            policy=policy or self._policy(),
            previous=copy.deepcopy(self.PREVIOUS if previous == "default" else previous),
            now=now or self.NOW, warn=warnings.append,
            defaults_doc=defaults)
        return result, warnings

    @staticmethod
    def _arms(result):
        return [a["id"] for a in result["arms"]]

    @staticmethod
    def _reason(result, model_id, section="arms"):
        return next(e["reason"] for e in result[section] if e["id"] == model_id)


class TestVendorDefaultSeats(_RosterFixture):

    def test_the_202_worked_example(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        self.assertEqual(sorted(self._arms(result)),
                         ["claude-opus-5", "claude-opus-5-5", "claude-sonnet-5"])
        new = self._reason(result, "claude-opus-5-5")
        self.assertIn("vendor default for the opus tier", new)
        self.assertIn(self.SOURCE, new)
        self.assertIn("probed 2026-09-27T10:00:00Z", new)
        self.assertIn("`claude-opus-5` carries 45.2%", new)
        self.assertIn("no cooling-off", new)
        self.assertNotIn("past the", new)
        held = self._reason(result, "claude-opus-5")
        self.assertIn("superseded by `claude-opus-5-5`", held)
        self.assertIn("0 of 1", held)
        self.assertIn("vendor default for the sonnet tier",
                      self._reason(result, "claude-sonnet-5"))
        self.assertEqual(result["judge"]["id"], "claude-fable-5-1")
        self.assertEqual(result["defaults"]["resolved"],
                         {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"})
        self.assertEqual(result["defaults"]["unresolved"], [])
        self.assertEqual(result["defaults"]["source"], self.SOURCE)
        self.assertEqual(result["defaults"]["probed_at"], "2026-09-27T10:00:00Z")
        self.assertEqual(set(result["defaults"]),
                         {"source", "probed_at", "resolved", "unresolved"})
        self.assertEqual(result["proposal"]["status"], "differs")
        seat = next(c for c in result["proposal"]["changes"]
                    if c["to"] == "claude-opus-5-5")
        self.assertIn("vendor default", seat["reason"])

    def test_one_complete_week_under_the_exit_bar_retires_it(self):
        result, _ = self._compute(defaults=self.DEFAULTS, now=self.LATER,
                                  census=self._later_census(opus5_w40=5))
        self.assertNotIn("claude-opus-5", self._arms(result))
        self.assertIn("claude-opus-5-5", self._arms(result))
        why = self._reason(result, "claude-opus-5", "retired_since_last")
        self.assertIn("superseded by `claude-opus-5-5`", why)
        self.assertIn("2026-W40", why)
        self.assertIn("under the 2% exit bar", why)
        change = next(c for c in result["proposal"]["changes"]
                      if c["from"] == "claude-opus-5")
        self.assertIn("superseded", change["reason"])

    def test_one_complete_week_at_or_above_the_exit_bar_holds_it(self):
        result, _ = self._compute(defaults=self.DEFAULTS, now=self.LATER,
                                  census=self._later_census(opus5_w40=300))
        self.assertIn("claude-opus-5", self._arms(result))
        held = self._reason(result, "claude-opus-5")
        self.assertIn("superseded by `claude-opus-5-5`", held)
        self.assertIn("at or above the 2% exit bar", held)

    def test_a_stale_census_holds_it(self):
        census = self._later_census(opus5_w40=5)
        # Past the 14-day freshness window at LATER + 20 days.
        result, _ = self._compute(defaults=self.DEFAULTS, census=census,
                                  now=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
        self.assertIn("claude-opus-5", self._arms(result))
        held = self._reason(result, "claude-opus-5")
        self.assertIn("no evidence to retire it", held)
        self.assertIn("claude-opus-5-5", self._arms(result))

    def test_a_too_thin_buffer_window_holds_it(self):
        census = self._census(generated_at="2026-10-06T06:00:00Z",
                              extra={"claude-opus-5": {"2026-W40": 1},
                                     "claude-sonnet-5": {"2026-W40": 5}})
        result, _ = self._compute(defaults=self.DEFAULTS, census=census, now=self.LATER)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("turn floor", self._reason(result, "claude-opus-5"))

    def test_zero_buffer_weeks_retires_it_at_once(self):
        result, _ = self._compute(defaults=self.DEFAULTS,
                                  policy=self._policy(superseded_exit_weeks=0))
        self.assertEqual(sorted(self._arms(result)),
                         ["claude-opus-5-5", "claude-sonnet-5"])
        why = self._reason(result, "claude-opus-5", "retired_since_last")
        self.assertIn("superseded by `claude-opus-5-5`", why)

    def test_a_superseded_model_gets_no_usage_seat(self):
        # opus-5 carries 45.2% but is not a previous arm: its usage keeps the
        # opus tier on the roster and seats the default, never itself.
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"}]}
        result, _ = self._compute(defaults=self.DEFAULTS, previous=previous)
        self.assertEqual(sorted(self._arms(result)),
                         ["claude-opus-5-5", "claude-sonnet-5"])
        why = self._reason(result, "claude-opus-5", "excluded")
        self.assertIn("superseded", why)
        # And out of its buffer, at 45% in the buffer week, a previous arm
        # is HELD by rule 3 (over the exit bar), not seated by usage.
        result, _ = self._compute(defaults=self.DEFAULTS, now=self.LATER,
                                  census=self._later_census(opus5_w40=450))
        held = self._reason(result, "claude-opus-5")
        self.assertNotIn("entry bar", held)
        self.assertIn("superseded", held)

    def test_a_preview_newer_than_the_default_takes_no_seat(self):
        defaults = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-5",
                                                  "sonnet": "claude-sonnet-5"}}
        # opus-5-5 is well past the cooling-off here, so today's rule would
        # seat it as newest in a qualifying tier.
        result, _ = self._compute(defaults=defaults, now=datetime(
            2026, 10, 8, 12, tzinfo=timezone.utc), census=self._later_census(
                opus5_w40=450, opus55_w40=40))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertIn("claude-opus-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-5", "excluded")
        self.assertIn("newer than the vendor default `claude-opus-5`", why)

    def test_a_default_in_a_tier_that_is_not_on_the_roster_takes_no_seat(self):
        defaults = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-5-5",
                                                  "sonnet": "claude-sonnet-5",
                                                  "fable": "claude-fable-5-1"}}
        result, _ = self._compute(defaults=defaults)
        self.assertNotIn("claude-fable-5-1", self._arms(result))
        self.assertEqual(result["defaults"]["resolved"]["fable"], "claude-fable-5-1")
        why = self._reason(result, "claude-fable-5-1", "excluded")
        self.assertIn("vendor default for the fable/mythos tier", why)
        self.assertIn("not on the roster", why)

    def test_a_previous_arm_puts_the_tier_on_the_roster(self):
        # Haiku carries 6.3% — under the entry bar — but a previous haiku arm
        # keeps the tier on the roster, so a new haiku default is seated.
        models = self._models_doc(extra=[self._model(
            "claude-haiku-5", "Claude Haiku 5", "2026-09-25T00:00:00Z")])
        previous = {**self.PREVIOUS, "arms": self.PREVIOUS["arms"]
                    + [{"id": "claude-haiku-4-5"}]}
        defaults = {**self.DEFAULTS, "defaults": {**self.DEFAULTS["defaults"],
                                                  "haiku": "claude-haiku-5"}}
        result, _ = self._compute(defaults=defaults, previous=previous, models=models)
        self.assertIn("claude-haiku-5", self._arms(result))
        self.assertIn("previous arm `claude-haiku-4-5`",
                      self._reason(result, "claude-haiku-5"))

    def test_with_no_usable_census_every_tier_is_on_the_roster(self):
        result, _ = self._compute(defaults=self.DEFAULTS, previous=None,
                                  census={"generated_at": None, "counts": {}})
        self.assertIn("claude-opus-5-5", self._arms(result))
        self.assertNotIn("claude-opus-5", self._arms(result))
        self.assertIn("no fresh census", self._reason(result, "claude-opus-5-5"))

    def test_the_summary_names_the_defaults_it_read(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        summary = roster.render_summary(result)
        self.assertIn("model defaults", summary)
        self.assertIn(self.SOURCE, summary)
        self.assertIn("2026-09-27T10:00:00Z", summary)


class TestSeatedDefaultCanRetire(_RosterFixture):
    """F1 (#203 round 1): a vendor default that is ALREADY an arm is not
    kept forever by its own seat — it gets rule 3's exit check."""

    WEEKS = [f"2026-W{n:02d}" for n in range(36, 50)]
    NOW_DEC = datetime(2026, 12, 8, 12, tzinfo=timezone.utc)
    PREV = {**_RosterFixture.PREVIOUS,
            "arms": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5-5"}]}

    def _census_no_opus(self, generated_at="2026-12-07T06:00:00Z"):
        counts = {"claude-sonnet-5": {w: 900 for w in self.WEEKS},
                  "claude-haiku-4-5": {w: 100 for w in self.WEEKS}}
        return {"generated_at": generated_at, "weeks": [], "counts": counts}

    def test_a_seated_default_with_no_usage_retires_by_the_exit_bar(self):
        result, _ = self._compute(defaults=self.DEFAULTS, previous=self.PREV,
                                  census=self._census_no_opus(), now=self.NOW_DEC)
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-5", "retired_since_last")
        self.assertIn("below the 2% exit bar for the last 8 weeks", why)
        self.assertIn("0.0%", why)

    def test_the_same_on_a_stale_census_is_held(self):
        # Generated 2026-11-10: past the 14-day freshness window by 12-08.
        result, _ = self._compute(defaults=self.DEFAULTS, previous=self.PREV,
                                  census=self._census_no_opus("2026-11-10T06:00:00Z"),
                                  now=self.NOW_DEC)
        self.assertIn("claude-opus-5-5", self._arms(result))
        self.assertEqual(
            [r for r in result["retired_since_last"] if r["id"] == "claude-opus-5-5"], [])

    def test_a_seated_default_over_the_exit_bar_is_held(self):
        census = self._census_no_opus()
        census["counts"]["claude-opus-5-5"] = {w: 40 for w in self.WEEKS}
        result, _ = self._compute(defaults=self.DEFAULTS, previous=self.PREV,
                                  census=census, now=self.NOW_DEC)
        self.assertIn("claude-opus-5-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-5")
        self.assertIn("at or above the 2% exit bar", why)
        self.assertIn("vendor default for the opus tier", why)

    def test_its_own_seat_is_not_a_previous_arm_in_the_tier(self):
        result, _ = self._compute(defaults=self.DEFAULTS, previous=self.PREV,
                                  census=self._census_no_opus(), now=self.NOW_DEC)
        for entry in result["arms"] + result["excluded"]:
            self.assertNotIn("previous arm `claude-opus-5-5` is in it", entry["reason"])


class _PreviewFixture(_RosterFixture):
    """A catalogue with a model newer than the opus default (`opus-5-6`)."""

    WEEKS = [f"2026-W{n:02d}" for n in range(33, 42)]
    NOW_OCT = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    D = {**_RosterFixture.DEFAULTS, "defaults": {"opus": "claude-opus-5-5",
                                                 "sonnet": "claude-sonnet-5"}}
    PREV = {**_RosterFixture.PREVIOUS,
            "arms": [{"id": "claude-opus-5-5"}, {"id": "claude-opus-5-6"},
                     {"id": "claude-sonnet-5"}]}

    def _models(self):
        return self._models_doc(extra=[self._model(
            "claude-opus-5-6", "Claude Opus 5.6", "2026-10-01T00:00:00Z")])

    def _census(self, opus56=0, generated_at="2026-10-12T06:00:00Z"):
        counts = {"claude-sonnet-5": {w: 500 for w in self.WEEKS},
                  "claude-opus-5-5": {w: 450 for w in self.WEEKS},
                  "claude-haiku-4-5": {w: 50 for w in self.WEEKS}}
        if opus56:
            counts["claude-opus-5-6"] = {w: opus56 for w in self.WEEKS}
        return {"generated_at": generated_at, "weeks": [], "counts": counts}


class TestPreviewArmGetsTheExitCheck(_PreviewFixture):
    """F2(b) (#203 round 1): a previous arm NEWER than its tier's default is
    no longer retired on sight — it gets rule 3's exit check. It still
    earns no NEW seat."""

    def test_a_heavily_used_preview_arm_is_held_not_retired(self):
        # The reviewer's a2.py: ~28% usage must not retire it.
        result, _ = self._compute(defaults=self.D, previous=self.PREV,
                                  models=self._models(), census=self._census(400),
                                  now=self.NOW_OCT)
        self.assertIn("claude-opus-5-6", self._arms(result))
        self.assertIn("claude-opus-5-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-6")
        self.assertIn("held over from the previous roster", why)
        self.assertIn("at or above the 2% exit bar", why)
        self.assertEqual(result["retired_since_last"], [])

    def test_an_unused_preview_arm_retires_by_the_exit_bar(self):
        result, _ = self._compute(defaults=self.D, previous=self.PREV,
                                  models=self._models(), census=self._census(),
                                  now=self.NOW_OCT)
        self.assertNotIn("claude-opus-5-6", self._arms(result))
        why = self._reason(result, "claude-opus-5-6", "retired_since_last")
        self.assertIn("below the 2% exit bar", why)

    def test_a_stale_census_holds_a_preview_arm(self):
        result, _ = self._compute(defaults=self.D, previous=self.PREV,
                                  models=self._models(),
                                  census=self._census(generated_at="2026-09-20T06:00:00Z"),
                                  now=self.NOW_OCT)
        self.assertIn("claude-opus-5-6", self._arms(result))
        self.assertIn("no evidence to retire it", self._reason(result, "claude-opus-5-6"))

    def test_a_preview_that_is_not_a_previous_arm_still_earns_no_seat(self):
        previous = {**self.PREV, "arms": [{"id": "claude-opus-5-5"},
                                          {"id": "claude-sonnet-5"}]}
        result, _ = self._compute(defaults=self.D, previous=previous,
                                  models=self._models(), census=self._census(400),
                                  now=self.NOW_OCT)
        self.assertNotIn("claude-opus-5-6", self._arms(result))
        self.assertIn("newer than the vendor default",
                      self._reason(result, "claude-opus-5-6", "excluded"))


class TestNoCarriedDefaults(_RosterFixture):
    """#203: the carried-defaults machinery is gone — the owner's decision:
    nothing is carried from one run to the next. The committed roster keeps
    no `defaults:` block, and a block an older committed roster still has is
    never read. A failed probe FREEZES its families for that one run
    (#203 probe round 1; see TestFailedProbeFreezesItsFamily), which is not
    a carry: nothing about the failure outlives the run."""

    OLD_BLOCK = {"source": "https://code.claude.com/docs/en/model-config.md",
                 "fetched_at": "2026-09-27T10:00:00Z",
                 "resolved": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"}}
    DOWN = {"probed_at": "2026-09-27T10:00:00Z", "harness_version": None,
            "defaults": {}, "skipped": [],
            "errors": {"haiku": "no-init", "sonnet": "no-init", "opus": "no-init",
                       "fable": "no-init", "mythos": "no-init"}}

    def test_a_failed_probe_never_reads_a_committed_defaults_block(self):
        with_block = {**self.PREVIOUS, "defaults": copy.deepcopy(self.OLD_BLOCK)}
        # No document at all: byte for byte the roster without one.
        baseline, _ = self._compute()
        result, _ = self._compute(defaults=None, previous=with_block)
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(baseline, sort_keys=True))
        # A failed probe: frozen, and the old block still decides nothing —
        # the same roster the same failure computes without the block.
        failed, _ = self._compute(defaults=self.DOWN)
        result, _ = self._compute(defaults=self.DOWN, previous=with_block)
        self.assertNotIn("defaults", result)
        self.assertNotIn("vendor default for the", json.dumps(result))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(failed, sort_keys=True))

    def test_the_rendered_roster_has_no_defaults_block(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        self.assertIn("defaults", result)
        text = render_roster_yaml.render(result, "1", "abc")
        document = yaml.safe_load(text)
        self.assertNotIn("defaults", document)
        self.assertNotIn("defaults:", text)
        self.assertEqual(roster.committed_roster_problems(document), [])

    def test_the_published_roster_keeps_the_block_for_the_reviewer(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        self.assertEqual(result["defaults"], {
            "source": self.SOURCE, "probed_at": "2026-09-27T10:00:00Z",
            "resolved": {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5"},
            "unresolved": []})

    def test_no_change_kind_is_about_defaults(self):
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                          {"id": "claude-opus-5-5"}],
                "defaults": copy.deepcopy(self.OLD_BLOCK)}
        census = self._later_census(opus5_w40=5, opus55_w40=440)
        for doc in (self.DEFAULTS, self.DOWN):
            with self.subTest(doc=bool(doc["defaults"])):
                result, _ = self._compute(defaults=doc, previous=prev,
                                          census=census, now=self.LATER)
                self.assertEqual([c for c in result["proposal"]["changes"]
                                  if c["kind"] == "defaults"
                                  or c["field"].startswith("defaults")], [])

    def test_no_doc_renders_no_defaults_block(self):
        result, _ = self._compute()
        self.assertNotIn("defaults:", render_roster_yaml.render(result, "1", "abc"))

    def test_the_carry_machinery_is_gone(self):
        for name in ("_carried_defaults", "_drop_stale_carried",
                     "_committed_defaults_problems", "COMMITTED_DEFAULTS_KEYS",
                     "DEFAULTS_DOCS_URL", "DEFAULTS_NAME_RE"):
            self.assertFalse(hasattr(roster, name), name)
        self.assertFalse(hasattr(render_roster_yaml, "_defaults_lines"))
        self.assertNotIn("carried", roster.render_summary(
            self._compute(defaults=self.DEFAULTS)[0]))


class TestUnresolvedDefaultsFallBack(_RosterFixture):
    """A PROBE FAILURE — an error recorded for the alias, or a document that
    resolved nothing — FREEZES that family for the run (#203 probe round 1):
    its previous arms are held, nothing of it is newly seated but by usage,
    and a warning says so. A CATALOGUE MISMATCH — the probe answered, but
    this run's catalogue does not match the answer — freezes too, exactly
    the same way, in its own wording (#203 probe round 2, R2-1; round 7,
    R7-1)."""

    @staticmethod
    def _policy(**overrides):
        # A POSITIVE cooling-off, supplied rather than read: the shipped
        # value is 0 since the owner's decision of 2026-09-27 (#202), and
        # these tests pin that an unresolved default leaves its tier on the
        # rule that applies the cooling-off whenever one is configured.
        return _RosterFixture._policy(**{"cooling_off_days": 7, **overrides})

    def _assert_todays_opus_rule(self, result):
        # opus-5 is a previous arm: held by the freeze, not by usage.
        # opus-5-5 gets no new seat, and the freeze — not the cooling-off —
        # is the reason given.
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("vendor default for `opus` unknown this run",
                      self._reason(result, "claude-opus-5"))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertIn("vendor default for `opus` unknown this run",
                      self._reason(result, "claude-opus-5-5", "excluded"))
        self.assertIn("opus", result["defaults_failed"])
        if "defaults" in result:
            self.assertNotIn("opus", result["defaults"]["resolved"])

    def _assert_opus_frozen_on_a_mismatch(self, result, cls):
        # A catalogue mismatch (R2-1) freezes exactly like a probe failure
        # (R7-1, #203 probe round 7): opus-5, a listed previous arm, is
        # held; opus-5-5 gets no new seat while it holds that seat.
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"], cls)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(result, "claude-opus-5"))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(result, "claude-opus-5-5", "excluded"))

    def test_an_id_that_is_not_available(self):
        defaults = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                                  "sonnet": "claude-sonnet-5"}}
        result, warnings = self._compute(defaults=defaults)
        self._assert_opus_frozen_on_a_mismatch(result, "not-available")
        self.assertTrue(any("`opus`" in w and "not an available model" in w
                            for w in warnings), warnings)
        self.assertEqual(result["defaults"]["unresolved"][0]["alias"], "opus")
        self.assertIn("claude-opus-9", result["defaults"]["unresolved"][0]["reason"])

    def test_an_id_in_the_wrong_tier(self):
        # R3-2 (#203 probe round 3): a wrong-tier answer is nonsensical, so
        # it is a probe FAILURE and freezes, not a catalogue mismatch.
        defaults = {**self.DEFAULTS, "defaults": {"opus": "claude-sonnet-5",
                                                  "sonnet": "claude-sonnet-5"}}
        result, warnings = self._compute(defaults=defaults)
        self._assert_todays_opus_rule(result)
        self.assertEqual(result["defaults_failed"], {"opus": "wrong-tier"})
        self.assertNotIn("defaults_mismatched", result)
        self.assertTrue(any("`opus`" in w and "sonnet tier" in w and "nonsensical" in w
                            for w in warnings), warnings)

    def test_an_alias_off_the_ladder_is_ignored(self):
        defaults = {**self.DEFAULTS, "defaults": {"opusplan": "claude-opus-5-5"}}
        result, warnings = self._compute(defaults=defaults)
        self._assert_todays_opus_rule(result)
        self.assertTrue(any("not a family word" in w for w in warnings), warnings)

    def test_a_probe_error_leaves_that_alias_unresolved_with_its_class(self):
        defaults = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
                    "errors": {"opus": "timeout", "haiku": "::error::x\ny"}}
        result, warnings = self._compute(defaults=defaults)
        self._assert_todays_opus_rule(result)
        self.assertEqual(result["defaults"]["resolved"], {"sonnet": "claude-sonnet-5"})
        reasons = {u["alias"]: u["reason"] for u in result["defaults"]["unresolved"]}
        self.assertIn("timeout", reasons["opus"])
        self.assertIn("an error was recorded", reasons["haiku"])
        self.assertTrue(any("`opus`" in w and "timeout" in w for w in warnings), warnings)
        self.assertNotIn("::error::", json.dumps(result) + "\n".join(warnings))

    def test_a_skipped_alias_is_not_unresolved(self):
        result, warnings = self._compute(defaults=self.DEFAULTS)
        self.assertNotIn("mythos", [u["alias"] for u in result["defaults"]["unresolved"]])
        self.assertFalse(any("mythos" in w for w in warnings), warnings)

    def test_junk_is_ignored_with_a_warning(self):
        for junk in ({"defaults": ["opus"]},
                     {"defaults": {"opus": 5}},
                     {"defaults": {"opus": "x" * 65}},
                     {"defaults": {"opus": "Opus 5.5"}},
                     {"defaults": {"opus": "claude-opus-5-5\n::error::x"}},
                     {"defaults": {"OPUS": "claude-opus-5-5"}},
                     {"defaults": {f"a{i}": "claude-opus-5-5" for i in range(17)}}):
            with self.subTest(junk=str(junk)[:40]):
                doc = {**self.DEFAULTS, **junk}
                result, warnings = self._compute(defaults=doc)
                self.assertNotIn("claude-opus-5-5", self._arms(result))
                self.assertTrue(warnings)
                self.assertFalse(any("::error::" in w for w in warnings), warnings)
                self.assertNotIn("::error::", json.dumps(result))


class TestSnapshotDefaults(_RosterFixture):
    """#203: the CLI can resolve an alias to a DATED id (haiku did, on
    2.1.283). Where the catalogue also lists the undated alias the roster
    collapses the snapshot onto it, and the default resolves to that seat."""

    def test_a_dated_default_resolves_onto_its_undated_alias(self):
        models = self._models_doc(extra=[self._model(
            "claude-haiku-4-5-20251001", "Claude Haiku 4.5", "2025-10-01T00:00:00Z")])
        defaults = {**self.DEFAULTS, "defaults": {**self.DEFAULTS["defaults"],
                                                  "haiku": "claude-haiku-4-5-20251001"}}
        result, warnings = self._compute(defaults=defaults, models=models)
        self.assertEqual(result["defaults"]["resolved"]["haiku"], "claude-haiku-4-5")
        self.assertEqual(result["defaults"]["unresolved"], [])
        self.assertFalse(any("haiku" in w for w in warnings), warnings)

    def test_a_dated_default_alone_in_the_catalogue_stands_on_its_own(self):
        models = self._models_doc(
            extra=[self._model("claude-haiku-4-5-20251001", "Claude Haiku 4.5",
                               "2025-10-01T00:00:00Z")],
            drop=("claude-haiku-4-5",))
        defaults = {**self.DEFAULTS, "defaults": {"haiku": "claude-haiku-4-5-20251001"}}
        result, _ = self._compute(defaults=defaults, models=models)
        self.assertEqual(result["defaults"]["resolved"],
                         {"haiku": "claude-haiku-4-5-20251001"})


class TestAliasConflicts(_RosterFixture):
    """F7 (#203 round 1), revised by R2-4 (#203 round 2): an alias resolves
    only to a model of its OWN family word. Two peer aliases in one rung
    (`fable`, `mythos`) are two families, not a conflict; a peer alias
    naming another family's model is unresolved on its own."""

    def test_a_peer_alias_naming_another_family_is_unresolved_alone(self):
        defaults = {**self.DEFAULTS, "defaults": {"fable": "claude-fable-5-1",
                                                  "mythos": "claude-fable-5"}}
        result, warnings = self._compute(defaults=defaults)
        self.assertEqual(result["defaults"]["resolved"], {"fable": "claude-fable-5-1"})
        self.assertEqual([u["alias"] for u in result["defaults"]["unresolved"]],
                         ["mythos"])
        self.assertTrue(any("`mythos`" in w and "fable family" in w
                            for w in warnings), warnings)

    def test_peer_aliases_naming_their_own_families_both_resolve(self):
        models = self._models_doc(extra=[self._model(
            "claude-mythos-1", "Claude Mythos 1", "2026-09-20T00:00:00Z")])
        defaults = {**self.DEFAULTS, "defaults": {"fable": "claude-fable-5-1",
                                                  "mythos": "claude-mythos-1"}}
        result, _ = self._compute(defaults=defaults, models=models)
        self.assertEqual(result["defaults"]["resolved"],
                         {"fable": "claude-fable-5-1", "mythos": "claude-mythos-1"})
        self.assertEqual(result["defaults"]["unresolved"], [])

    def test_a_third_peer_off_its_family_does_not_block_the_others(self):
        policy = self._policy(tiers=["haiku", "sonnet", "opus",
                                     ["fable", "mythos", "fablex"]])
        defaults = {**self.DEFAULTS, "defaults": {"fable": "claude-fable-5-1",
                                                  "mythos": "claude-fable-5",
                                                  "fablex": "claude-fable-5-1"}}
        result, _ = self._compute(defaults=defaults, policy=policy)
        self.assertEqual(result["defaults"]["resolved"], {"fable": "claude-fable-5-1"})
        self.assertEqual(sorted(u["alias"] for u in result["defaults"]["unresolved"]),
                         ["fablex", "mythos"])


class TestPreflightWordingAtAPositiveCoolingOff(_RosterFixture):
    """F6 (#203 round 1): at a positive cooling-off the preflight reason is
    origin/main's, word for word; only at 0 does the new wording apply."""

    def test_a_positive_cooling_off_keeps_the_old_wording(self):
        for days in (7, 3):
            with self.subTest(days=days):
                result, _ = self._compute(policy=self._policy(cooling_off_days=days))
                self.assertEqual(
                    result["preflight"]["reason"],
                    f"newest model in the haiku tier that is past the {days}-day "
                    f"cooling-off: the lowest tier the Models API still returns, "
                    f"and this is its cheapest safely-invocable pick")

    def test_zero_uses_the_new_wording(self):
        result, _ = self._compute(policy=self._policy(cooling_off_days=0))
        self.assertEqual(
            result["preflight"]["reason"],
            "newest model in the haiku tier (no cooling-off applies: "
            "`cooling_off_days` is 0): the lowest tier the Models API still "
            "returns, and this is its cheapest safely-invocable pick")


class TestNoDefaultsIsByteForByteUnchanged(_RosterFixture):
    """No probe document at all is the same roster the pre-#202 code
    computes, with one exception (N1, #203 adversarial round 10):
    `_seated_form`'s recognition of a previous arm whose listed spelling
    switched between dated and undated is not gated on a defaults document
    existing, so a renamed previous arm is still held under its new
    spelling rather than reading as "no longer returned" and dropping its
    seat outright — see TestNoDocumentStillRecognizesARename. A document
    that failed entirely is NOT unchanged (#203 probe round 1): it freezes
    every family it could not vouch for — see
    TestFailedProbeFreezesItsFamily."""

    FAILED = {"probed_at": "2026-09-27T10:00:00Z", "harness_version": None,
              "defaults": {}, "skipped": [],
              "errors": {"haiku": "timeout", "sonnet": "timeout",
                         "opus": "timeout", "fable": "timeout", "mythos": "timeout"}}

    def test_no_doc_changes_nothing(self):
        baseline, _ = self._compute()
        result, warnings = self._compute(defaults=None)
        self.assertEqual(json.dumps(result, sort_keys=True),
                         json.dumps(baseline, sort_keys=True))
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(warnings, [])

    def test_empty_and_failed_docs_freeze_rather_than_change_nothing(self):
        baseline, _ = self._compute()
        # Without a document, the usage rules seat opus-5-5 as newest in a
        # qualifying tier; every failed document must hold the opus tier
        # where it stands instead.
        self.assertIn("claude-opus-5-5", self._arms(baseline))
        for doc in ({}, self.FAILED,
                    {**self.DEFAULTS, "defaults": {}},
                    {**self.FAILED, "errors": {"opus": "FileNotFoundError"}},
                    {**self.DEFAULTS, "defaults": None},
                    "not a mapping"):
            with self.subTest(doc=str(doc)[:40]):
                result, _ = self._compute(defaults=doc)
                self.assertEqual(sorted(self._arms(result)),
                                 ["claude-opus-5", "claude-sonnet-5"])
                self.assertIn("opus", result["defaults_failed"])
                self.assertIn("sonnet", result["defaults_failed"])
                self.assertEqual(result["retired_since_last"], [])

    def test_a_failed_probe_says_so_in_one_warning(self):
        _, warnings = self._compute(defaults=self.FAILED)
        said = [w for w in warnings if "model defaults" in w]
        self.assertEqual(len(said), 1, warnings)
        self.assertIn("timeout", said[0])
        self.assertIn("held", said[0])
        self.assertIn("none retired except models gone from the Models API", said[0])
        self.assertIn("none added unless the family holds no seat the Models API "
                      "still lists", said[0])
        self.assertNotIn("usage and newest-in-tier rules", said[0])


class TestNoDocumentStillRecognizesARename(_RosterFixture):
    """N1 (#203 adversarial round 10): with NO defaults document at all,
    a previous arm whose listed spelling switched between dated and
    undated since the previous run is still recognized as that arm — the
    one way the no-document roster is NOT byte-for-byte the pre-#202
    code's, which just read the dated spelling as gone and dropped the
    seat. The reviewer's nodoc.py ("dated->alias only")."""

    def test_a_dated_previous_arm_collapses_onto_its_now_listed_alias(self):
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5-20260401"}]}
        result, _ = self._compute(defaults=None, previous=previous)
        self.assertIn("claude-opus-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-20260401", "retired_since_last")
        self.assertIn("collapsed onto its undated alias `claude-opus-5`", why)
        self.assertIn("which holds the seat", why)


class TestShippedPolicyFollowsCurrentUse(_RosterFixture):
    """The owner's decision of 2026-10-25: a superseded model retires on the
    first roster run that sees it replaced, whatever the census says, and the
    enter/exit windows are 2 weeks. Uses the shipped policy unmodified."""

    def _shipped(self):
        return roster.load_policy(POLICY)

    def test_the_shipped_windows_are_two_weeks_and_exit_is_not_shorter(self):
        policy = self._shipped()
        self.assertEqual(policy["arm_enter_window_weeks"], 2)
        self.assertEqual(policy["arm_exit_window_weeks"], 2)
        self.assertGreaterEqual(policy["arm_exit_window_weeks"],
                                policy["arm_enter_window_weeks"])
        roster.validate_policy(policy)

    def test_a_superseded_arm_retires_whatever_the_census_says(self):
        policy = self._shipped()
        self.assertEqual(policy["superseded_exit_weeks"], 0)
        stale = self._census(generated_at="2026-07-01T06:00:00Z")
        thin = {"generated_at": "2026-09-22T06:00:00Z", "weeks": [],
                "counts": {"claude-opus-5": {"2026-W39": 1}}}
        # The census is fresh and healthy in the third case: opus-5 still
        # carries the whole tier's usage and is retired anyway.
        for label, census in (("stale", stale), ("thin", thin),
                              ("healthy", self._census())):
            with self.subTest(census=label):
                result, _ = self._compute(defaults=self.DEFAULTS, census=census,
                                          policy=policy)
                self.assertNotIn("claude-opus-5", self._arms(result))
                self.assertIn("claude-opus-5-5", self._arms(result))
                why = self._reason(result, "claude-opus-5", "retired_since_last")
                self.assertIn("superseded by `claude-opus-5-5`", why)
                self.assertIn("`superseded_exit_weeks` is 0", why)


class TestSupersededExitWeeksPolicy(unittest.TestCase):

    def test_the_shipped_policy_sets_no_buffer(self):
        # 0 since the owner's decision of 2026-10-25.
        policy = roster.load_policy(POLICY)
        self.assertEqual(policy["superseded_exit_weeks"], 0)
        roster.validate_policy(policy)
        self.assertIn("#202", POLICY.read_text(encoding="utf-8"))

    def test_bad_values_fail_by_name(self):
        base = roster.load_policy(POLICY)
        for bad, label in ((None, "missing"), (-1, "negative"), (True, "bool"),
                           ("1", "string"), (1.0, "float"), (None, "None")):
            policy = dict(base)
            if label == "missing":
                del policy["superseded_exit_weeks"]
            else:
                policy["superseded_exit_weeks"] = bad
            with self.subTest(bad=label):
                with self.assertRaises(ValueError) as ctx:
                    roster.validate_policy(policy)
                self.assertIn("superseded_exit_weeks", str(ctx.exception))


class TestMainReadsDefaults(_RosterFixture):

    def test_main_passes_defaults_through(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "models.json").write_text(json.dumps(self._models_doc()), encoding="utf-8")
        (tmp / "census.json").write_text(json.dumps(self._census()), encoding="utf-8")
        (tmp / "defaults.json").write_text(json.dumps(self.DEFAULTS), encoding="utf-8")
        (tmp / "previous.yml").write_text(yaml.safe_dump(self.PREVIOUS), encoding="utf-8")
        now = self.NOW

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        argv = ["roster.py", "--models", str(tmp / "models.json"),
                "--census", str(tmp / "census.json"),
                "--previous", str(tmp / "previous.yml"),
                "--defaults", str(tmp / "defaults.json"),
                "--out", str(tmp / "out.json")]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(roster, "datetime", Frozen), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            rc = roster.main()
        self.assertEqual(rc, 0)
        out = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
        self.assertIn("claude-opus-5-5", [a["id"] for a in out["arms"]])


class TestWorkflowProbesDefaults(unittest.TestCase):
    """eval.yml's roster step probes the installed CLI (#203) before the
    Models API bearer is exported, and the probe cannot fail the step."""

    def _roster_step(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        return next(s for s in doc["jobs"]["roster"]["steps"]
                    if "roster" in (s.get("name") or "").lower())["run"]

    def test_the_roster_step_probes_defaults_first_and_passes_them(self):
        script = self._roster_step()
        self.assertNotIn("fetch_model_defaults", script)
        probe_at = script.index("scripts/probe_model_defaults.py")
        roster_at = script.index("harness/roster.py")
        self.assertLess(probe_at, roster_at)
        self.assertIn('--out "$work/defaults.json"', script[probe_at:roster_at])
        self.assertIn('--defaults "$work/defaults.json"', script[roster_at:])
        self.assertNotIn("${{", script)

    def test_the_probe_runs_before_the_bearer_is_exported(self):
        lines = [ln.strip() for ln in self._roster_step().splitlines()]
        probe = next(i for i, ln in enumerate(lines)
                     if "scripts/probe_model_defaults.py" in ln
                     and not ln.startswith("#"))
        bearer = [i for i, ln in enumerate(lines)
                  if "ANTHROPIC_AUTH_TOKEN" in ln and not ln.startswith("#")]
        self.assertTrue(bearer)
        self.assertLess(probe, min(bearer))

    def test_the_probe_cannot_fail_the_step(self):
        script = self._roster_step()
        line = next(ln for ln in script.splitlines()
                    if "scripts/probe_model_defaults.py" in ln
                    and not ln.strip().startswith("#"))
        self.assertTrue(line.rstrip().endswith("|| true"), line)

    def test_the_probe_scrubs_the_environment_regardless(self):
        # Order in the step is one guard; this is the other. Even with the
        # bearer (and every other credential shape) in the parent process,
        # the CLI it starts sees none of it.
        import probe_model_defaults
        parent = {"PATH": "/usr/bin", "ANTHROPIC_AUTH_TOKEN": "bearer",
                  "ANTHROPIC_API_KEY": "k", "ANTHROPIC_ADMIN_KEY": "k",
                  "GITHUB_TOKEN": "t", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t",
                  "ACTIONS_RUNTIME_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1"}
        with mock.patch.dict(os.environ, parent, clear=True):
            env = probe_model_defaults.scrubbed_env(Path("/tmp/probe-home"))
        self.assertEqual(credential_like(env), [])
        self.assertNotIn("bearer", json.dumps(env))
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)


class TestRosterOnlyDispatch(unittest.TestCase):
    """A dispatch can refresh and propose the roster WITHOUT a paid eval run:
    the owner held paid runs until the roster seats the new defaults, and the
    roster step exists only inside this workflow."""

    # B1 (round 3 on #209): the SKIPPED steps live in the `eval` job (whose
    # OWN `if:` is now `!cancelled() && !inputs.roster_only`, so the whole
    # job — not just these steps — is skipped; their own step-level `if:`
    # stays too, defensively). The KEPT steps live in the `roster` job,
    # which has no `roster_only`-conditioned `if:` at all: it always runs.
    # B1 (round 4 on #209, blocker): "Build the badge over the run window,
    # commit, and push" moved to the `publish` job, whose own `if:` is
    # requires `needs.eval.result` to be success, failure, or cancelled — a
    # `roster_only` dispatch skips the whole `eval` job (`result` reads
    # `skipped`, which cannot satisfy listed condition), so this
    # step no longer needs, or carries, its own `roster_only`-conditioned
    # `if:` at all.
    SKIPPED = ("WIF auth preflight", "Run the eval (both arms, judge)")
    KEPT = ("Mint OIDC token and exchange for Anthropic access token",
            "Refresh the model roster", "Propose a roster change")

    def setUp(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML reads the bare `on:` key as boolean True.
        self.triggers = doc.get("on", doc.get(True))
        self.jobs = doc["jobs"]
        self.steps = {s.get("name"): s for s in
                      doc["jobs"]["roster"]["steps"] + doc["jobs"]["eval"]["steps"]}

    def test_the_input_is_a_boolean_that_defaults_off(self):
        spec = self.triggers["workflow_dispatch"]["inputs"]["roster_only"]
        self.assertEqual(spec.get("type"), "boolean")
        self.assertIs(spec.get("default"), False)

    def test_the_paid_steps_are_skipped_in_roster_only_mode(self):
        for name in self.SKIPPED:
            with self.subTest(step=name):
                self.assertIn(name, self.steps)
                self.assertEqual(self.steps[name].get("if"),
                                 "${{ !inputs.roster_only }}")

    def test_the_roster_steps_still_run(self):
        for name in self.KEPT:
            with self.subTest(step=name):
                self.assertIn(name, self.steps)
                self.assertNotIn("roster_only", str(self.steps[name].get("if", "")))

    def test_the_input_never_reaches_a_run_block(self):
        # Never interpolated: the one run block that reads it does so from
        # $GITHUB_EVENT_PATH with jq, at run time (#203 round 1).
        for name, step in self.steps.items():
            with self.subTest(step=name):
                run = step.get("run", "")
                self.assertNotIn("${{ inputs.roster_only", run)
                self.assertNotIn("${{ !inputs.roster_only", run)
                self.assertNotIn("github.event.inputs", run)
                for line in run.splitlines():
                    if "roster_only" in line and ".inputs.roster_only" in line:
                        self.assertIn('"$GITHUB_EVENT_PATH"', line)

    NOTE_START = "# >>> roster-only note"
    NOTE_END = "# <<< roster-only note"

    def _note_fragment(self):
        run = self.steps["Propose a roster change"]["run"]
        self.assertIn(self.NOTE_START, run)
        start = run.index(self.NOTE_START)
        end = run.index(self.NOTE_END)
        return run[start:end]

    def _eval_note(self, event):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "event.json").write_text(json.dumps(event), encoding="utf-8")
        script = ("set -euo pipefail\n" + self._note_fragment()
                  + '\nprintf "%s" "$eval_note"\n')
        done = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=30,
                              env={"PATH": os.environ.get("PATH", ""),
                                   "GITHUB_EVENT_PATH": str(tmp / "event.json"),
                                   # A normal run's eval step succeeded (#203
                                   # probe round 3 reads its outcome).
                                   "EVAL_OUTCOME": "success"})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    @unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
    def test_a_roster_only_run_does_not_claim_an_eval_ran(self):
        for value in (True, "true"):
            with self.subTest(value=value):
                note = self._eval_note({"inputs": {"roster_only": value}})
                self.assertIn("no eval ran", note)
                self.assertIn("nothing from this run is published to `persistent/eval-results`", note)
                self.assertNotIn("results will still be published", note)

    @unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
    def test_a_normal_run_says_the_eval_runs_on_the_committed_roster(self):
        # S3 (round 4 on #209): "runs", not "ran" — this step (in the
        # `roster` job, which never `needs: eval`) writes this sentence
        # without knowing whether the `eval` job has even started yet.
        for event in ({"inputs": {"roster_only": False}},
                      {"inputs": {"roster_only": "false"}},
                      {"inputs": {"fixture": "evals/x"}}, {"schedule": "0 7 * * 1"}):
            with self.subTest(event=event):
                note = self._eval_note(event)
                self.assertIn("runs, in a separate job after this one, on the committed",
                              note)
                self.assertNotIn("ran on the committed", note)
                self.assertNotIn("no eval ran", note)

    def test_both_bodies_carry_the_note(self):
        # B1 (round 3 on #209): this step's OWN local `eval_note` (the
        # simplified 2-way roster_only/not version) is used only by the
        # "same"/frozen-same bodies it writes directly — the fuller
        # success/failure/unknown version now lives in `roster-pr`'s own
        # `# >>> eval-note` fragment (see TestEvalNoteKnowsTheOutcome).
        run = self.steps["Propose a roster change"]["run"]
        self.assertNotIn("The paid eval ran on the committed", run.replace(
            self._note_fragment(), ""))
        self.assertGreaterEqual(run.count('"$eval_note"'), 1)

    def test_the_input_and_readme_say_nothing_is_published(self):
        spec = self.triggers["workflow_dispatch"]["inputs"]["roster_only"]
        for text in (spec["description"],
                     (REPO_ROOT / "README.md").read_text(encoding="utf-8")):
            flat = " ".join(text.split())
            self.assertIn("publishes nothing to `persistent/eval-results`", flat)
            self.assertIn("`roster/proposal`", flat)


# --- #203 round 2 ------------------------------------------------------------


class _WeeklyLoop(_RosterFixture):
    """The real week-by-week loop, inline and hermetic (#203 round 2): every
    Monday at 12:00 compute the roster against a census generated at 06:00
    over the twelve complete weeks before it, render the proposal, check it
    against the committed-roster contract, and feed it back as `previous`
    whenever its status is "differs" — i.e. a human merges every proposal."""

    BASE = [
        _RosterFixture._model("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01T00:00:00Z"),
        _RosterFixture._model("claude-sonnet-5", "Claude Sonnet 5", "2026-02-01T00:00:00Z"),
        _RosterFixture._model("claude-opus-5", "Claude Opus 5", "2026-04-01T00:00:00Z"),
        _RosterFixture._model("claude-opus-5-5", "Claude Opus 5.5", "2026-09-26T12:00:00Z"),
        _RosterFixture._model("claude-fable-5-1", "Claude Fable 5.1", "2026-09-01T00:00:00Z"),
    ]
    PREV0 = {"schema": 1,
             "arms": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}],
             "judge": {"id": "claude-fable-5-1", "is_arm": False},
             "preflight": {"id": "claude-haiku-4-5"},
             "catalogue_seen": [],
             "provenance": {"seeded": "x", "from": "y"},
             "generated_at": "2026-09-21T00:00:00Z"}
    #: Monday of 2026-W40.
    START = datetime(2026, 9, 28, tzinfo=timezone.utc)

    @staticmethod
    def _week_number(label):
        year, week = label.split("-W")
        return (int(year) - 2026) * 53 + int(week)

    def _docs(self, k, **defaults):
        probed = (self.START + timedelta(weeks=k, hours=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"probed_at": probed, "harness_version": "2.1.283 (Claude Code)",
                "defaults": defaults or {"opus": "claude-opus-5-5",
                                         "sonnet": "claude-sonnet-5"},
                "skipped": ["mythos"], "errors": {}}

    def _loop(self, usage, weeks, docs_for, previous=None, models=None):
        prev = copy.deepcopy(previous or self.PREV0)
        out = []
        for k in range(weeks):
            now = self.START + timedelta(weeks=k, hours=12)
            counts: dict = {}
            for back in range(12, 0, -1):
                label = timeweeks.iso_week(now - timedelta(weeks=back))
                for model_id, n in usage(self._week_number(label)).items():
                    counts.setdefault(model_id, {})[label] = n
            census = {"generated_at": (self.START + timedelta(weeks=k, hours=6))
                      .strftime("%Y-%m-%dT%H:%M:%SZ"), "weeks": [], "counts": counts}
            warnings: list[str] = []
            committed = copy.deepcopy(prev)
            result = roster.compute_roster(
                models_doc={"fetched_at": now.isoformat(),
                            "models": list(models or self.BASE)},
                census_doc=census, policy=self._policy(), previous=copy.deepcopy(prev),
                now=now, warn=warnings.append, defaults_doc=docs_for(k))
            out.append({"result": result, "warnings": warnings, "committed": committed})
            if result["proposal"]["status"] == "differs":
                prev = yaml.safe_load(render_roster_yaml.render(result, "1", "abc"))
                self.assertEqual(roster.committed_roster_problems(prev), [], k)
        return out, prev


class TestNoCarryPolicyKey(unittest.TestCase):
    """#203: `defaults_carry_max_age_days` went with the carry."""

    def test_the_shipped_policy_has_no_carry_key_and_validates(self):
        policy = roster.load_policy(POLICY)
        self.assertNotIn("defaults_carry_max_age_days", policy)
        roster.validate_policy(policy)
        self.assertNotIn("defaults_carry_max_age_days", POLICY.read_text(encoding="utf-8"))

    def test_the_eval_workflow_has_no_carried_defaults_note(self):
        text = EVAL_WORKFLOW.read_text(encoding="utf-8")
        for stale in ("carried-defaults note", "defaults_state", "carried_note",
                      "defaults_carry_max_age_days", "vendor defaults carried"):
            self.assertNotIn(stale, text)


class TestSeatedDefaultHeldByItsTier(_WeeklyLoop):
    """R2-3 (#203 round 2): a seated default with nothing else putting its
    tier on the roster is held while the TIER's combined share clears the
    exit bar, and a retirement quotes the tier's share."""

    def test_s5_a_tier_between_the_bars_keeps_an_arm(self):
        # The reviewer's s5.py: the opus tier at a steady ~5% (between the
        # 2% exit and 10% entry bars); the fleet switches to 5.5 in W40.
        def usage(n):
            return {"claude-sonnet-5": 900, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0,
                    "claude-opus-5": 0 if n >= 40 else 50,
                    "claude-opus-5-5": 50 if n >= 40 else 0}

        out, _ = self._loop(usage, 5, self._docs)
        for k, week in enumerate(out):
            arms = self._arms(week["result"])
            self.assertTrue([a for a in arms if "opus" in a], (k, arms))
        for week in out[1:]:
            self.assertIn("claude-opus-5-5", self._arms(week["result"]))
        held = self._reason(out[1]["result"], "claude-opus-5-5")
        self.assertIn("opus tier's combined share", held)
        self.assertIn("at or above the 2% exit bar", held)

    def test_a_tier_under_the_exit_bar_retires_the_default_with_the_tier_share(self):
        weeks = [f"2026-W{n:02d}" for n in range(33, 41)]
        census = {"generated_at": "2026-10-05T06:00:00Z", "weeks": [], "counts": {
            "claude-sonnet-5": {w: 1000 for w in weeks},
            "claude-opus-5": {w: 5 for w in weeks},
            "claude-opus-5-5": {w: 5 for w in weeks}}}
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5-5"}]}
        result, _ = self._compute(defaults=self.DEFAULTS, previous=previous,
                                  census=census,
                                  now=datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        why = self._reason(result, "claude-opus-5-5", "retired_since_last")
        self.assertIn("opus tier's combined share", why)
        self.assertIn("below the 2% exit bar for the last 8 weeks", why)
        change = next(c for c in result["proposal"]["changes"]
                      if c["from"] == "claude-opus-5-5")
        # 70 of 7070 over the seven census weeks in the window: both opus
        # models' turns, not the default's own 35.
        self.assertIn("70 of the window's 7070", change["reason"])
        self.assertIn("combined share", change["reason"])


class TestPeerFamilies(_RosterFixture):
    """R2-4 (#203 round 2): the vendor-default rules apply only to models
    of the default's own family word; a peer family in the same rung is
    decided by rules 1-3 as before."""

    def test_p9_a_peer_family_keeps_its_usage_seat(self):
        models = self._models_doc(extra=[self._model(
            "claude-mythos-1", "Claude Mythos 1", "2026-09-20T00:00:00Z")])
        weeks = ["2026-W37", "2026-W38", "2026-W39", "2026-W40"]
        census = {"generated_at": "2026-10-05T06:00:00Z", "weeks": [], "counts": {
            "claude-sonnet-5": {w: 400 for w in weeks},
            "claude-mythos-1": {w: 400 for w in weeks},
            "claude-opus-5-5": {w: 200 for w in weeks}}}
        now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        docs = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-5-5",
                                              "sonnet": "claude-sonnet-5",
                                              "fable": "claude-fable-5-1"}}
        with_docs, _ = self._compute(defaults=docs, models=models, census=census, now=now)
        without, _ = self._compute(defaults=None, models=models, census=census, now=now)
        self.assertIn("claude-mythos-1", self._arms(with_docs))
        self.assertIn("carries", self._reason(with_docs, "claude-mythos-1"))
        self.assertEqual(with_docs["judge"]["id"], without["judge"]["id"])
        self.assertNotIn("claude-fable-5-1", self._arms(with_docs))
        self.assertIn("no `fable` model in it",
                      self._reason(with_docs, "claude-fable-5-1", "excluded"))
        for entry in with_docs["excluded"]:
            if entry["id"] == "claude-mythos-1":
                self.fail(entry["reason"])


class TestRound2Nits(_RosterFixture):
    """R2-5 and R2-6 (#203 round 2), as they stand after the probe."""

    def _main_stderr(self, defaults_text, previous):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "models.json").write_text(json.dumps(self._models_doc()), encoding="utf-8")
        (tmp / "census.json").write_text(json.dumps(self._census()), encoding="utf-8")
        (tmp / "defaults.json").write_text(defaults_text, encoding="utf-8")
        (tmp / "previous.yml").write_text(yaml.safe_dump(previous), encoding="utf-8")
        now = self.NOW

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        argv = ["roster.py", "--models", str(tmp / "models.json"),
                "--census", str(tmp / "census.json"),
                "--previous", str(tmp / "previous.yml"),
                "--defaults", str(tmp / "defaults.json"),
                "--out", str(tmp / "out.json")]
        err = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(roster, "datetime", Frozen), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(err):
            rc = roster.main()
        self.assertEqual(rc, 0)
        return err.getvalue(), json.loads((tmp / "out.json").read_text(encoding="utf-8"))

    def test_an_unreadable_defaults_file_warns_once_and_names_the_real_fallback(self):
        for previous in (self.PREVIOUS,
                         {**self.PREVIOUS, "defaults": TestNoCarriedDefaults.OLD_BLOCK}):
            with self.subTest(block="defaults" in previous):
                stderr, out = self._main_stderr("{not json", previous)
                lines = [ln for ln in stderr.splitlines() if "model defaults" in ln]
                self.assertEqual(len(lines), 1, stderr)
                self.assertIn("unreadable", lines[0])
                # R2-1(c), R3-4(c): held, none retired but a model gone
                # from the Models API.
                self.assertIn("none retired except models gone from the Models API",
                              lines[0])
                self.assertNotIn("carried", stderr)
                self.assertNotIn("defaults", out)
                # F1/F3 (#203 probe round 1): unreadable freezes every
                # family on the ladder, and the published roster says so.
                self.assertEqual(out["defaults_document_failed"], "unreadable")
                self.assertEqual(set(out["defaults_failed"]),
                                 set(roster.tier_words(self._policy())))

    def test_a_junk_harness_version_publishes_an_unknown_version(self):
        for junk in ("bad ::error:: `version`", 5, None, "x" * 200):
            with self.subTest(junk=str(junk)[:20]):
                result, _ = self._compute(defaults={**self.DEFAULTS,
                                                    "harness_version": junk})
                self.assertEqual(result["defaults"]["source"],
                                 "claude-code-cli (version unknown)")
                self.assertNotIn("::", json.dumps(result["defaults"]))

    def test_a_junk_probed_at_publishes_null(self):
        result, _ = self._compute(defaults={**self.DEFAULTS, "probed_at": "yesterday"})
        self.assertIsNone(result["defaults"]["probed_at"])
        self.assertIn("probed at an unknown time", self._reason(result, "claude-opus-5-5"))



# --- #203 probe round 1 -------------------------------------------------------


def _s5_usage(n):
    """The reviewer's S5 fleet: the opus tier at a steady ~5% (between the
    2% exit and 10% entry bars), switching from 5 to 5.5 in W40."""
    return {"claude-sonnet-5": 900, "claude-haiku-4-5": 50, "claude-fable-5-1": 0,
            "claude-opus-5": 0 if n >= 40 else 50,
            "claude-opus-5-5": 50 if n >= 40 else 0}


class TestFailedProbeFreezesItsFamily(_WeeklyLoop):
    """F1 + F2 (#203 probe round 1): a family whose vendor default the probe
    could not establish is FROZEN for that run — its previous arms keep
    their seats, none of its models is newly seated, none is retired. Per
    run only: nothing is carried to the next (the owner's decision)."""

    HELD = ("vendor default for `opus` unknown this run (probe: timeout); held; "
            "none retired on a failed probe")

    def _failing(self, weeks, errors, defaults=None):
        def docs(k):
            doc = self._docs(k)
            if k in weeks:
                doc["defaults"] = dict(defaults or {})
                doc["errors"] = dict(errors)
            return doc
        return docs

    def test_flip_a_one_week_opus_failure_changes_no_opus_seat(self):
        # The reviewer's flip.py: week 2's opus probe times out. Without the
        # freeze the tier fell back to the usage rules, retired the seated
        # default that week, and never seated it again.
        out, _ = self._loop(_s5_usage, 6, self._failing(
            {2}, {"opus": "timeout"}, {"sonnet": "claude-sonnet-5"}))
        self.assertEqual(out[2]["result"]["retired_since_last"], [])
        self.assertEqual(out[2]["result"]["added_since_last"], [])
        week1 = self._arms(out[1]["result"])
        self.assertIn("claude-opus-5-5", week1)
        for k in range(2, 6):
            self.assertEqual(self._arms(out[k]["result"]), week1, k)
        self.assertEqual(self._reason(out[2]["result"], "claude-opus-5-5"), self.HELD)
        self.assertEqual(out[2]["result"]["defaults_failed"], {"opus": "timeout"})
        # The next week probes cleanly and decides as usual: nothing carried.
        self.assertNotIn("defaults_failed", out[3]["result"])
        self.assertIn("vendor default for the opus tier",
                      self._reason(out[3]["result"], "claude-opus-5-5"))

    def test_total_every_alias_failing_holds_every_previous_arm(self):
        # The reviewer's total.py: every alias fails in week 2.
        errors = {a: "no-init" for a in ("opus", "sonnet", "haiku", "fable")}
        out, _ = self._loop(_s5_usage, 3, self._failing({2}, errors))
        week2 = out[2]["result"]
        self.assertEqual(week2["retired_since_last"], [])
        self.assertEqual(week2["added_since_last"], [])
        self.assertEqual(self._arms(week2), self._arms(out[1]["result"]))
        self.assertEqual(week2["defaults_failed"], errors)
        self.assertEqual(week2["defaults_document_failed"], "no-defaults")
        for arm in week2["arms"]:
            self.assertIn("unknown this run (probe: no-init)", arm["reason"])
        # Nothing of a frozen family is newly seated: haiku and fable say why.
        for model_id in ("claude-haiku-4-5", "claude-fable-5-1"):
            self.assertIn("no new seat on a failed probe",
                          self._reason(week2, model_id, "excluded"))
        self.assertEqual(week2["proposal"]["status"], "same")

    def test_a_skipped_family_is_not_frozen(self):
        # mythos is `skipped` (not an alias of this CLI), so it was never a
        # default to fail: a new mythos model is seated by the usage rules
        # exactly as without a document.
        models = list(self.BASE) + [self._model(
            "claude-mythos-1", "Claude Mythos 1", "2026-09-20T00:00:00Z")]

        def usage(n):
            return {**_s5_usage(n), "claude-mythos-1": 400}

        errors = {a: "no-init" for a in ("opus", "sonnet", "haiku", "fable")}
        out, _ = self._loop(usage, 1, self._failing({0}, errors), models=models)
        result = out[0]["result"]
        self.assertIn("claude-mythos-1", self._arms(result))
        self.assertIn("carries", self._reason(result, "claude-mythos-1"))
        self.assertNotIn("mythos", result["defaults_failed"])
        # Recorded as an error instead, mythos is frozen like the rest — and
        # a frozen family holding no listed seat still seats a model that
        # clears the usage entry bar (R2-1(c), #203 probe round 2; R3-1).
        out, _ = self._loop(usage, 1, self._failing(
            {0}, {**errors, "mythos": "no-init"}), models=models)
        self.assertIn("mythos", out[0]["result"]["defaults_failed"])
        self.assertIn("unknown this run (probe: no-init), and the family holds no "
                      "seat the Models API still lists, so this seat rests on usage "
                      "alone", self._reason(out[0]["result"], "claude-mythos-1"))

    def test_an_unreadable_document_freezes_every_family(self):
        warnings: list[str] = []
        result = roster.compute_roster(
            models_doc=self._models_doc(), census_doc=self._census(),
            policy=self._policy(), previous=copy.deepcopy(self.PREVIOUS),
            now=self.NOW, warn=warnings.append, defaults_doc=None,
            defaults_problem="defaults.json is present but unreadable (ValueError)")
        self.assertEqual(result["defaults_document_failed"], "unreadable")
        self.assertEqual(result["defaults_failed"],
                         {w: "unreadable" for w in roster.tier_words(self._policy())})
        self.assertEqual(sorted(self._arms(result)), ["claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(result["retired_since_last"], [])

    def test_a_frozen_family_retires_nothing_even_under_the_exit_bar(self):
        # A previous opus arm at 0% for eight weeks would retire by rule 3;
        # frozen, it is held.
        weeks = [f"2026-W{n:02d}" for n in range(36, 50)]
        census = {"generated_at": "2026-12-07T06:00:00Z", "weeks": [], "counts": {
            "claude-sonnet-5": {w: 900 for w in weeks},
            "claude-haiku-4-5": {w: 100 for w in weeks}}}
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5-5"}]}
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout"}}
        result, _ = self._compute(defaults=doc, previous=previous, census=census,
                                  now=datetime(2026, 12, 8, 12, tzinfo=timezone.utc))
        self.assertIn("claude-opus-5-5", self._arms(result))
        self.assertEqual(result["retired_since_last"], [])
        self.assertEqual(self._reason(result, "claude-opus-5-5"), self.HELD)

    def test_a_frozen_family_keeps_a_snapshot_arm_on_its_collapsed_alias(self):
        # A previous arm published under a dated id whose undated alias the
        # catalogue now lists is the same seat, renamed — not a new one.
        models = self._models_doc(extra=[self._model(
            "claude-opus-5-20260401", "Claude Opus 5", "2026-04-01T00:00:00Z")])
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5-20260401"}]}
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout"}}
        result, _ = self._compute(defaults=doc, previous=previous, models=models)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertNotIn("claude-opus-5-5", self._arms(result))

    def test_only_a_probe_failure_freezes_its_family(self):
        # R2-1 (#203 probe round 2): an id the catalogue does not list is a
        # catalogue mismatch — the probe answered — so nothing is frozen.
        # One of another tier is nonsensical, so it IS a failure (R3-2).
        doc = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                             "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=doc)
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"], "not-available")
        doc = {**self.DEFAULTS, "defaults": {"opus": "claude-sonnet-5",
                                             "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=doc)
        self.assertEqual(result["defaults_failed"], {"opus": "wrong-tier"})
        self.assertNotIn("defaults_mismatched", result)
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout"}}
        result, _ = self._compute(defaults=doc)
        self.assertEqual(result["defaults_failed"], {"opus": "timeout"})
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertIn("claude-opus-5", self._arms(result))

    def test_a_clean_probe_publishes_no_failure_keys(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        self.assertNotIn("defaults_failed", result)
        self.assertNotIn("defaults_document_failed", result)


class TestPreviewNotSeatedOnAFailedProbe(_PreviewFixture):
    """F2 (#203 probe round 1), the reviewer's preview.py: a probe that
    fails for one week must not seat the newer-than-default model, or the
    next week's clean probe retires it again — a flip on no evidence."""

    def test_preview_a_failed_week_adds_nothing_so_nothing_flips_back(self):
        prev = {**self.PREV, "arms": [{"id": "claude-opus-5-5"},
                                      {"id": "claude-sonnet-5"}]}
        failed = {**self.D, "defaults": {"sonnet": "claude-sonnet-5"},
                  "errors": {"opus": "timeout"}}
        r1, _ = self._compute(defaults=failed, previous=prev, models=self._models(),
                              census=self._census(), now=self.NOW_OCT)
        self.assertEqual(r1["added_since_last"], [])
        self.assertNotIn("claude-opus-5-6", self._arms(r1))
        self.assertIn("no new seat on a failed probe",
                      self._reason(r1, "claude-opus-5-6", "excluded"))
        prev2 = {**prev, "arms": [{"id": a["id"]} for a in r1["arms"]]}
        r2, _ = self._compute(defaults=self.D, previous=prev2, models=self._models(),
                              census=self._census(), now=self.NOW_OCT)
        self.assertEqual(r2["retired_since_last"], [])
        self.assertEqual(self._arms(r2), self._arms(r1))


class TestFailedProbeIsLoud(_WeeklyLoop):
    """F3 (#203 probe round 1): the failure is in the published roster and
    near the top of the summary, where the reviewer looks."""

    def _failed(self, **overrides):
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout", "fable": "no-init"}}
        doc.update(overrides)
        return self._compute(defaults=doc)[0]

    def test_the_summary_names_the_frozen_families_near_the_top(self):
        summary = roster.render_summary(self._failed())
        head = summary.splitlines()[:4]
        line = next((ln for ln in head if "probe" in ln), None)
        self.assertIsNotNone(line, head)
        self.assertIn("`fable` (no-init)", line)
        self.assertIn("`opus` (timeout)", line)
        self.assertIn("held", line)
        self.assertNotIn("::", summary)

    def test_a_whole_document_failure_names_its_class(self):
        result = self._compute(defaults={**self.DEFAULTS, "defaults": {},
                                         "errors": {}})[0]
        self.assertEqual(result["defaults_document_failed"], "no-defaults")
        head = "\n".join(roster.render_summary(result).splitlines()[:4])
        self.assertIn("no-defaults", head)

    def test_the_total_week_summary_is_loud(self):
        errors = {a: "no-init" for a in ("opus", "sonnet", "haiku", "fable")}

        def docs(k):
            doc = self._docs(k)
            if k == 2:
                doc["defaults"], doc["errors"] = {}, dict(errors)
            return doc

        out, _ = self._loop(_s5_usage, 3, docs)
        summary = roster.render_summary(out[2]["result"])
        self.assertIn("probe failed", summary.lower())
        for alias in errors:
            self.assertIn(f"`{alias}` (no-init)", summary)

    def test_a_clean_probe_adds_no_line(self):
        summary = roster.render_summary(self._compute(defaults=self.DEFAULTS)[0])
        self.assertNotIn("probe failed", summary.lower())


class TestMismatchSummaryLineSaysNotAProbeFailure(_RosterFixture):
    """R8-4 (#203 probe round 8): the mismatch summary line must say, in so
    many words, that it is not a probe failure — the exact text a mutant
    that dropped "; not a probe failure:" survived round 8's mutation pass
    with no test pinning it."""

    def test_the_mismatch_line_says_not_a_probe_failure(self):
        defaults = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                                  "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=defaults)
        summary = roster.render_summary(result)
        self.assertIn("**Vendor default not matched:**", summary)
        self.assertIn("Frozen this run, not a probe failure: seats held", summary)


#: Round 7 (ADR 0003, "the App publishes the proposal branch"): the stub
#: `gh`'s answer to the git-data REST calls `roster-pr` now publishes the
#: proposal with — blob, tree, commit, ref create/update — plus the two
#: reads that go with them (a commit's tree, a file's blob sha at a ref).
#: Each is answered by REAL `git` against `$TEST_GIT_DIR`, never a canned
#: sha, so the commit `roster-pr` creates is a real commit the compare route
#: (and every assertion below) can inspect. Every call is logged to
#: `$GITDATA_LOG` as JSON with the credential `gh` would have used, so a test
#: can require that each write ran as the roster App. Two fault hooks model
#: what the API could do behind the job's back: `TEST_RETARGET_AFTER_PUBLISH`
#: moves the ref past the created commit right after the ref write, and
#: `TEST_TREE_EXTRA_FILE` makes the tree carry one more file than asked.
GITDATA_STUB = r"""
import base64
import json
import os
import subprocess
import sys
import tempfile

method, url, input_path = sys.argv[1], sys.argv[2], sys.argv[3]
gitdir = os.environ["TEST_GIT_DIR"]
body = None
if input_path:
    with open(input_path, encoding="utf-8") as handle:
        body = json.load(handle)
token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
with open(os.environ["GITDATA_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"method": method, "url": url, "token": token,
                             "body": body}) + "\n")


def git(*args, data=None, env=None):
    done = subprocess.run(["git", "-C", gitdir, *args], input=data,
                          capture_output=True, env=env)
    if done.returncode:
        raise SystemExit(1)
    return done.stdout.decode().strip()


def exists(ref):
    return subprocess.run(["git", "-C", gitdir, "rev-parse", "-q", "--verify", ref],
                          capture_output=True).returncode == 0


def retarget(ref):
    if os.environ.get("TEST_RETARGET_AFTER_PUBLISH"):
        tree = git("rev-parse", ref + "^{tree}")
        extra = git("commit-tree", tree, "-p", ref, "-m", "planted after publish",
                    env=dict(os.environ, GIT_AUTHOR_NAME="x", GIT_AUTHOR_EMAIL="x@example.com",
                             GIT_COMMITTER_NAME="x", GIT_COMMITTER_EMAIL="x@example.com"))
        git("update-ref", ref, extra)


path = url.split("/", 3)[3]
if method == "POST" and path == "git/blobs":
    if body.get("encoding") != "base64":
        raise SystemExit(1)
    sha = git("hash-object", "-w", "--stdin", data=base64.b64decode(body["content"]))
    print(json.dumps({"sha": sha}))
elif method == "POST" and path == "git/trees":
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        git("read-tree", body["base_tree"], env=env)
        entries = list(body["tree"])
        if os.environ.get("TEST_TREE_EXTRA_FILE"):
            extra = git("hash-object", "-w", "--stdin", data=b"planted\n")
            entries.append({"path": "zzz-extra.txt", "mode": "100644", "sha": extra})
        for entry in entries:
            git("update-index", "--add", "--cacheinfo",
                "%s,%s,%s" % (entry["mode"], entry["sha"], entry["path"]), env=env)
        print(json.dumps({"sha": git("write-tree", env=env)}))
elif method == "POST" and path == "git/commits":
    author = body["author"]
    env = dict(os.environ, GIT_AUTHOR_NAME=author["name"], GIT_AUTHOR_EMAIL=author["email"],
               GIT_COMMITTER_NAME=author["name"], GIT_COMMITTER_EMAIL=author["email"])
    args = ["commit-tree", body["tree"], "-m", body["message"]]
    for parent in body["parents"]:
        args += ["-p", parent]
    print(json.dumps({"sha": git(*args, env=env)}))
elif method == "GET" and path.startswith("git/commits/"):
    sha = path.rsplit("/", 1)[1]
    print(json.dumps({"sha": git("rev-parse", sha + "^{commit}"),
                      "tree": {"sha": git("rev-parse", sha + "^{tree}")}}))
elif method == "PATCH" and path.startswith("git/refs/heads/"):
    ref = "refs/" + path[len("git/refs/"):]
    if not exists(ref):
        raise SystemExit(1)  # 422: Reference does not exist
    if not body.get("force") and git("merge-base", ref, body["sha"]) != git("rev-parse", ref):
        raise SystemExit(1)  # 422: Update is not a fast forward
    git("update-ref", ref, body["sha"])
    retarget(ref)
    print(json.dumps({"ref": ref, "object": {"sha": body["sha"]}}))
elif method == "POST" and path == "git/refs":
    ref = body["ref"]
    if exists(ref):
        raise SystemExit(1)  # 422: Reference already exists
    git("update-ref", ref, body["sha"])
    retarget(ref)
    print(json.dumps({"ref": ref, "object": {"sha": body["sha"]}}))
elif method == "GET" and path.startswith("contents/evals/roster.yml?ref="):
    ref = path.split("?ref=", 1)[1]
    print(json.dumps({"sha": git("rev-parse", ref + ":evals/roster.yml")}))
else:
    raise SystemExit(1)
"""


class _ProposeStepFixture(unittest.TestCase):
    """The "Propose a roster change" step body, run hermetically with a stub
    `gh`: shared by the #203 probe-round tests below. No test methods of its
    own, so subclasses do not re-run each other's."""

    START = "# >>> defaults-failed note"
    END = "# <<< defaults-failed note"
    WARNING = "::warning::vendor-default probe failed for {n} {word}; their seats were held"
    MARKER = "<!-- skills-evals:roster-proposal -->"

    @staticmethod
    def _plural(n):
        """"family" at 1, "families" otherwise (#203 probe round 9, R9-3):
        the two `::warning::` lines must not read "for 1 families"."""
        return "family" if n == 1 else "families"

    #: needs.roster.outputs.<key> -> the env var name the roster-pr job's
    #: step reads it as (must match .github/workflows/eval.yml's
    #: `roster-pr` job env: block exactly — TestRosterPrJobEnvMatchesOutputs
    #: asserts that). `eval_note` is NOT here (B1, round 3 on #209): it is
    #: computed inside `roster-pr` itself, from `needs.eval.result` — the
    #: `roster` job cannot know it (it must never depend on `eval`).
    OUTPUT_ENV = {
        "roster_mode": "ROSTER_MODE", "status": "STATUS",
        "probe_clean": "PROBE_CLEAN", "issue_number": "ISSUE_NUMBER",
        "proposed_roster_b64": "PROPOSED_ROSTER_B64", "base_sha": "BASE_SHA",
        "rejected": "REJECTED",
        "rejection_reason": "REJECTION_REASON",
        "rendered_identical": "RENDERED_IDENTICAL",
        "probe_note": "PROBE_NOTE", "roster_summary": "ROSTER_SUMMARY",
    }

    #: Round 6: the roster App's installation token, as the `roster-pr`
    #: step receives it (`ROSTER_APP_TOKEN`). Present by default so the
    #: auto path is reachable; a test sets `self._app_token = ""` to model
    #: the mint step failing under `continue-on-error`.
    _app_token = "app-token-fake"

    def setUp(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.run_body = next(s for s in doc["jobs"]["roster"]["steps"]
                             if s.get("name") == "Propose a roster change")["run"]
        #: F2 (adversarial round 1 on #209): every `gh pr`/`gh workflow run`
        #: call moved out of `self.run_body` into this second job's own
        #: step, run second by `_run_two_jobs` below with the first job's
        #: $GITHUB_OUTPUT threaded into it exactly as
        #: `needs.eval.outputs.*` would be.
        self.roster_pr_run_body = _manage_step(doc)["run"]
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "gitdata.py").write_text(GITDATA_STUB, encoding="utf-8")

    @staticmethod
    def _parse_github_output(path):
        """A minimal reader for the `$GITHUB_OUTPUT` file format: plain
        `key=value` lines and `key<<DELIM` / ... / `DELIM` blocks (used here
        for every multi-line value, with a delimiter chosen at random per
        call — see eval.yml's `emit_ml`)."""
        out = {}
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if "<<" in line:
                key, delim = line.split("<<", 1)
                body = []
                i += 1
                while i < len(lines) and lines[i] != delim:
                    body.append(lines[i])
                    i += 1
                out[key] = "\n".join(body)
            elif "=" in line:
                key, _, value = line.partition("=")
                out[key] = value
            i += 1
        return out

    def _run_two_jobs(self, gh_script, env, cwd):
        """Run `self.run_body` (the `eval` job's propose step) then, unless
        it exits before reaching the tracker-issue lookup at all in a way
        that produces no outputs, `self.roster_pr_run_body` (the `roster-pr`
        job's step) — both against the SAME stub `gh` on `PATH`, so `calls`
        below is the full cross-job call log a real run would produce.
        `env` is the base env dict; `RUNNER_TEMP` must already be a fresh
        directory the propose step can write $GITHUB_OUTPUT into.

        S1 (round 2, spec-roster-mode-r2.md): step 1 may exit non-zero (a
        failed `git push`, as on a real runner) — its `$GITHUB_OUTPUT` is
        still processed and the `roster-pr` job still runs (F3), so this no
        longer hard-asserts `done1.returncode == 0`; `self.step1_returncode`
        records it for a test that cares.

        B1 (round 2): `TEST_GIT_DIR` and `GITHUB_SHA` are threaded into both
        jobs' env automatically — `TEST_GIT_DIR` names the git checkout the
        stub `gh`'s `api` routing (below) reads `evals/roster-policy.yml`,
        `roster/proposal`'s head and the `$GITHUB_SHA...$PUSHED_SHA` compare
        from (never a fixture-side guess at what GitHub would answer);
        `GITHUB_SHA` defaults to that checkout's HEAD at call time, i.e.
        BEFORE step 1 can touch it — the one exception is a test that
        deliberately sets `self._github_sha` itself (A1-style: the trusted
        sha must be captured before any tampering happens)."""
        stub = self.tmp / "bin"
        stub.mkdir(exist_ok=True)
        gh = stub / "gh"
        gh.write_text(gh_script, encoding="utf-8")
        gh.chmod(0o755)
        gh_log = self.tmp / "gh.log"
        gh_log.unlink(missing_ok=True)
        (self.tmp / "gh-auth.log").unlink(missing_ok=True)
        (self.tmp / "gitdata.log").unlink(missing_ok=True)
        (self.tmp / "body.md").unlink(missing_ok=True)
        github_output = self.tmp / "github-output-1"
        github_output.write_text("", encoding="utf-8")
        test_git_dir = str(cwd) if cwd is not None else ""
        github_sha = getattr(self, "_github_sha", None)
        if github_sha is None and test_git_dir:
            probe = subprocess.run(["git", "-C", test_git_dir, "rev-parse", "HEAD"],
                                   capture_output=True, text=True)
            # A non-git `cwd` (a plain directory `_write_policy` wrote
            # into) still needs a syntactically valid $GITHUB_SHA so the
            # production script attempts the policy read at all — the
            # stub's `contents` route falls back to a plain file read of
            # `evals/roster-policy.yml` under `TEST_GIT_DIR` when `git
            # show` itself fails, so a dummy-but-hex sha is enough here.
            github_sha = probe.stdout.strip() if probe.returncode == 0 else "0" * 40
        github_sha = github_sha or ""
        env1 = dict(env, PATH=f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
                   GITHUB_OUTPUT=str(github_output), TEST_GIT_DIR=test_git_dir,
                   GITHUB_SHA=github_sha, **FIXED_GIT_DATES)
        done1 = subprocess.run(["bash", "-c", self.run_body], capture_output=True,
                               text=True, timeout=60, env=env1, cwd=cwd or self.tmp)
        self.step1_returncode = done1.returncode
        outputs = self._parse_github_output(github_output)
        #: Round 7: what the `roster` job handed on, for a test to inspect.
        self.outputs1 = outputs
        stdout = done1.stdout
        # Round 7: a test may change the world between the two jobs (say,
        # land a commit on `main`), exactly as time passes on a real run.
        between = getattr(self, "_between_jobs", None)
        if between is not None:
            between()
        # The roster-pr job runs unconditionally from here — `if:
        # ${{ !cancelled() }}`, same as production (F3) — whether or not the
        # propose step above reached far enough to write any output at all;
        # missing outputs just mean empty/default env vars below.
        github_output2 = self.tmp / "github-output-2"
        env2 = dict(env, PATH=f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
                   GITHUB_OUTPUT=str(github_output2), TEST_GIT_DIR=test_git_dir,
                   GITHUB_SHA=github_sha, **FIXED_GIT_DATES)
        for key, var in self.OUTPUT_ENV.items():
            if key in outputs:
                env2[var] = outputs[key]
        # Round 6: only the `roster-pr` job ever holds the App token.
        env2["ROSTER_APP_TOKEN"] = self._app_token
        # Round 7: the git-data stub's fault hooks, for the second job only.
        env2.update(getattr(self, "_job2_env", {}))
        done2 = subprocess.run(["bash", "-c", self.roster_pr_run_body],
                               capture_output=True, text=True, timeout=60,
                               env=env2, cwd=self.tmp)
        self.assertEqual(done2.returncode, 0, done2.stderr)
        stdout += done2.stdout
        log = gh_log.read_text(encoding="utf-8").splitlines() if gh_log.exists() else []
        body = ((self.tmp / "body.md").read_text(encoding="utf-8")
                if (self.tmp / "body.md").exists() else None)
        # Only the high-volume tracker-issue LISTING is filtered out (every
        # test triggers it); the new `gh api` calls this round adds — the
        # policy read, the head check, the compare check, the PR lookup —
        # are meaningful signal now, not noise.
        return stdout, [ln for ln in log if "issues?state=open" not in ln], body

    def _fragment(self):
        self.assertIn(self.START, self.run_body)
        return self.run_body[self.run_body.index(self.START):self.run_body.index(self.END)]

    MODE_START = "# >>> roster-mode"
    MODE_END = "# <<< roster-mode"

    def _mode_fragment(self):
        self.assertIn(self.MODE_START, self.run_body)
        return self.run_body[self.run_body.index(self.MODE_START):
                             self.run_body.index(self.MODE_END)]

    def _run_mode_fragment(self, cwd, policy_text=None):
        """Run just the `roster_mode` fragment with `cwd` as the working
        directory `evals/roster-policy.yml` is read from (the trusted `main`
        checkout, per the design). `policy_text=None` means no such file at
        all; any other value is written verbatim."""
        if policy_text is not None:
            (cwd / "evals").mkdir(parents=True, exist_ok=True)
            (cwd / "evals" / "roster-policy.yml").write_text(policy_text, encoding="utf-8")
        # `emit` is defined once, ahead of this fragment, in the real step;
        # a no-op stand-in here since this fragment now calls it and this
        # test never reads $GITHUB_OUTPUT.
        script = ("set -euo pipefail\nemit() { :; }\n" + self._mode_fragment()
                  + '\nprintf "MODE=%s\\n" "$roster_mode"\n')
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=30, cwd=cwd, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _latest(self, payload):
        path = self.tmp / "latest.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                        encoding="utf-8")
        return path

    def _run_fragment(self, payload):
        runner = self.tmp / "fragment-runner"
        (runner / "roster-inputs").mkdir(parents=True, exist_ok=True)
        (runner / "roster-inputs" / "summary.md").write_text("### Model roster\n",
                                                              encoding="utf-8")
        # `emit`/`emit_ml` are defined once, ahead of this fragment, in the
        # real step; no-op stand-ins here since this fragment now calls
        # them and this test never reads $GITHUB_OUTPUT.
        script = ("set -euo pipefail\nemit() { :; }\nemit_ml() { cat >/dev/null; }\n"
                  + "eval_note=''\n"
                  + f"computed={str(self._latest(payload))!r}\n"
                  + self._fragment()
                  + '\nprintf "COUNT=%s\\n" "$defaults_failed"'
                  + '\nprintf "NOTE=%s\\n" "$probe_note"\n')
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=30, env={"PATH": os.environ.get("PATH", ""),
                                              "RUNNER_TEMP": str(runner)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _api_routing(self):
        """Shared `gh api` routing (B1/S4, round 2): every `gh api`
        call this round's `roster-pr` job newly makes — the policy read
        at `$GITHUB_SHA`, `roster/proposal`'s actual head, the
        `$GITHUB_SHA...$PUSHED_SHA` compare, and the server-side PR
        lookup — is answered here from `$TEST_GIT_DIR` via REAL `git`,
        never a hardcoded stand-in: a regression in what this job asks
        for or how it validates the answer is caught the same way F1's
        fork filter is, through genuine `git`/`jq`/`python3` rather than
        a fixture that already assumes the answer. The one exception is
        the PR lookup's rows, which a test controls directly
        (`_pr_list_rows`/`_pr_list_value`, `_AutoProposeStepFixture`)
        since "which PRs are open" is not something a local git
        checkout can answer for itself. Falls through to `issues.json`
        for the tracker-issue LISTING call (`gh api --paginate --slurp
        repos/.../issues?...`), unchanged from before this round."""
        text = (
            '        if [ "$1" = api ]; then\n'
            '          shift\n'
            '          url="$1"; shift\n'
            "          jqexpr=''; method=GET; input=''\n"
            '          while [ "$#" -gt 0 ]; do\n'
            '            case "$1" in\n'
            '              --jq) jqexpr="$2"; shift ;;\n'
            '              -X|--method) method="$2"; shift ;;\n'
            '              --input) input="$2"; shift ;;\n'
            '            esac\n'
            '            shift\n'
            '          done\n'
            '          gitdir="${TEST_GIT_DIR:-}"\n'
            "          resp=''\n"
            '          case "$url" in\n'
            '            repos/*/contents/evals/roster-policy.yml\\?ref=*)\n'
            '              sha="${url##*ref=}"\n'
            "              content=''\n"
            '              if [ -n "$gitdir" ]; then\n'
            '                content=$(git -C "$gitdir" show "$sha:evals/roster-policy.yml" 2>/dev/null) || content=\'\'\n'
            '                if [ -z "$content" ] && [ -f "$gitdir/evals/roster-policy.yml" ]; then\n'
            '                  content=$(cat "$gitdir/evals/roster-policy.yml")\n'
            '                fi\n'
            '              fi\n'
            "              resp=$(python3 -c '\n"
            'import base64\n'
            'import json\n'
            'import sys\n'
            'data = sys.stdin.buffer.read()\n'
            'print(json.dumps({"content": base64.b64encode(data).decode()}))\n'
            '\' <<<"$content")\n'
            '              ;;\n'
            '            repos/*/git/ref/heads/roster/proposal)\n'
            "              sha=''\n"
            '              if [ -n "$gitdir" ]; then\n'
            '                sha=$(git -C "$gitdir" rev-parse roster/proposal 2>/dev/null) || sha=\'\'\n'
            '              fi\n'
            "              resp=$(python3 -c '\n"
            'import json\n'
            'import sys\n'
            'print(json.dumps({"object": {"sha": sys.argv[1]}}))\n'
            '\' "$sha")\n'
            '              ;;\n'
            '            repos/*/git/ref/heads/main)\n'
            # S1 (round 4 on #209): the live-`main`-sha resolution the
            # policy-at-live-main read now does before it reads the
            # policy — same shape as the roster/proposal-head lookup
            # above, resolving "main" through real git when `gitdir` is
            # one (`_repo`-built fixtures); a plain non-git `TEST_GIT_DIR`
            # (a bare `_write_policy` directory, as several older,
            # pre-round-4 tests still use) gets the SAME dummy-but-valid-
            # shape sha `_run_two_jobs` already gives `$GITHUB_SHA` in
            # that case, so the contents-at-that-sha route below still
            # falls through to its own plain-file read rather than
            # reading nothing.
            "              sha=''\n"
            '              if [ -n "$gitdir" ]; then\n'
            '                sha=$(git -C "$gitdir" rev-parse main 2>/dev/null) || sha=\'\'\n'
            '                if [ -z "$sha" ] && [ -f "$gitdir/evals/roster-policy.yml" ]; then\n'
            '                  sha="0000000000000000000000000000000000000000"\n'
            '                fi\n'
            '              fi\n'
            # S1 (round 4 on #209): a test may force what the API answers
            # for `main`'s sha, to feed the step a malformed one.
            '              if [ -n "${TEST_MAIN_REF_SHA:-}" ]; then sha="$TEST_MAIN_REF_SHA"; fi\n'
            "              resp=$(python3 -c '\n"
            'import json\n'
            'import sys\n'
            'print(json.dumps({"object": {"sha": sys.argv[1]}}))\n'
            '\' "$sha")\n'
            '              ;;\n'
            '            repos/*/compare/*)\n'
            '              range="${url##*/compare/}"\n'
            '              base_sha="${range%%...*}"\n'
            '              head_sha="${range##*...}"\n'
            "              ahead=0; behind=0; namestatus=''; commitlog=''\n"
            '              if [ -n "$gitdir" ] && git -C "$gitdir" cat-file -e "$base_sha" 2>/dev/null \\\n'
            '                 && git -C "$gitdir" cat-file -e "$head_sha" 2>/dev/null; then\n'
            '                ahead=$(git -C "$gitdir" rev-list --count "$base_sha..$head_sha" 2>/dev/null || echo 0)\n'
            '                behind=$(git -C "$gitdir" rev-list --count "$head_sha..$base_sha" 2>/dev/null || echo 0)\n'
            '                namestatus=$(git -C "$gitdir" diff --name-status "$base_sha" "$head_sha" 2>/dev/null || echo \'\')\n'
            '                commitlog=$(git -C "$gitdir" log --reverse --format=\'%B%x1f%ae%x1e\' "$base_sha..$head_sha" 2>/dev/null || echo \'\')\n'
            '              fi\n'
            '              cstatus=diverged\n'
            '              if [ "$ahead" -gt 0 ] && [ "$behind" -eq 0 ]; then cstatus=ahead\n'
            '              elif [ "$ahead" -eq 0 ] && [ "$behind" -gt 0 ]; then cstatus=behind\n'
            '              elif [ "$ahead" -eq 0 ] && [ "$behind" -eq 0 ]; then cstatus=identical\n'
            '              fi\n'
            "              resp=$(COMMITLOG=\"$commitlog\" python3 -c '\n"
            'import json\n'
            'import os\n'
            'import sys\n'
            'status, ahead, behind = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
            'names = {"A": "added", "M": "modified", "D": "removed"}\n'
            'files = []\n'
            'for line in sys.stdin:\n'
            '    line = line.rstrip("\\n")\n'
            '    if not line:\n'
            '        continue\n'
            '    code, _, path = line.partition("\\t")\n'
            '    files.append({"filename": path, "status": names.get(code[:1], code)})\n'
            'commits = []\n'
            'for record in (os.environ.get("COMMITLOG") or "").split("\\x1e"):\n'
            '    record = record.lstrip("\\n")\n'
            '    if not record.strip():\n'
            '        continue\n'
            '    message, _, email = record.partition("\\x1f")\n'
            '    commits.append({"commit": {"message": message.rstrip("\\n"),\n'
            '                               "author": {"email": email.strip()}}})\n'
            'print(json.dumps({"status": status, "ahead_by": ahead, "behind_by": behind, '
            '"files": files, "commits": commits}))\n'
            '\' "$cstatus" "$ahead" "$behind" <<<"$namestatus")\n'
            '              ;;\n'
            # Round 7: the git-data publish calls, answered by real git
            # (GITDATA_STUB above); a refused call is an HTTP error.
            '            repos/*/git/blobs|repos/*/git/trees|repos/*/git/commits|'
            'repos/*/git/commits/*|repos/*/git/refs|repos/*/git/refs/*|'
            'repos/*/contents/evals/roster.yml\\?ref=*)\n'
            '              resp=$(GITDATA_LOG=__GITDATA_LOG__ python3 __GITDATA__ "$method" "$url" "$input") '
            "|| { echo 'HTTP 422' >&2; exit 1; }\n"
            '              ;;\n'
            '            repos/*/pulls\\?state=open*)\n'
            '              if [ -f __PR_ROWS__ ]; then\n'
            '                resp=$(cat __PR_ROWS__)\n'
            '              else\n'
            "                resp='[]'\n"
            '              fi\n'
            '              ;;\n'
            '          esac\n'
            '          if [ -n "$resp" ]; then\n'
            '            if [ -n "$jqexpr" ]; then\n'
            '              printf \'%s\' "$resp" | jq -r "$jqexpr"\n'
            '            else\n'
            '              printf \'%s\\n\' "$resp"\n'
            '            fi\n'
            '            exit 0\n'
            '          fi\n'
            '          cat __ISSUES__\n'
            '          exit 0\n'
            '        fi\n'
        )
        return (text.replace('__PR_ROWS__', repr(str(self.tmp / 'pr-list-rows.json')))
                    .replace('__ISSUES__', repr(str(self.tmp / 'issues.json')))
                    .replace('__GITDATA_LOG__', repr(str(self.tmp / 'gitdata.log')))
                    .replace('__GITDATA__', repr(str(self.tmp / 'gitdata.py'))))

    def _gh_script(self):
        """The stub `gh` shared by every `_ProposeStepFixture` test: logs
        every call, routes `gh api` through `_api_routing` (round 2), fails
        any call whose full argument line CONTAINS `self._gh_fail` (R2-3,
        widened in round 2 from an exact `$1 $2` match so it can also
        target an `api ...` call), and captures any `--body-file` argument
        to `body.md`. `_AutoProposeStepFixture` extends this with `pr
        create` and the PR-rows-backed `pulls` answer; every other `gh pr`/
        `gh workflow` call (F2's new job) falls through here unmatched,
        which succeeds with empty stdout — "no PR found" / "nothing to
        report" — exactly like a bare `gh` invocation nobody stubbed for
        would look wrong to distinguish from in a test that predates the
        auto path."""
        fail_sentinel = getattr(self, "_gh_fail", None) or "\x01NEVERMATCH\x01"
        return (
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {str(self.tmp / 'gh.log')!r}\n"
            # Round 6: which credential each call ran with — `gh` itself
            # reads GH_TOKEN first, then GITHUB_TOKEN, so this records the
            # one it would actually use.
            "tok=\"${GH_TOKEN:-${GITHUB_TOKEN:-}}\"\n"
            f"printf '%s\\t%s\\n' \"$tok\" \"$*\" >> {str(self.tmp / 'gh-auth.log')!r}\n"
            # R2-3: `_gh_fail` names a SUBSTRING of the full argument line
            # that fails — checked before any stand-in below, so a failing
            # api/pr/issue call really fails rather than also answering.
            "case \"$*\" in\n"
            f"  *{fail_sentinel!r}*)\n"
            "    echo 'HTTP 502' >&2; exit 1 ;;\n"
            "esac\n"
            + self._api_routing() +
            "prev=''\n"
            "for arg in \"$@\"; do\n"
            f"  if [ \"$prev\" = --body-file ]; then cat \"$arg\" > {str(self.tmp / 'body.md')!r}; fi\n"
            "  prev=\"$arg\"\n"
            "done\n")

    def _run_step(self, latest, issues, cwd=None):
        """The whole two-job round trip with a stub `gh`, from the tracker
        listing to the "same" branch — which is as far as a "same" run
        goes. `cwd` is the `eval` job's working directory (default: the temp
        dir, which has no `scripts/`, so a "differs" run is rejected before
        publication)."""
        runner = self.tmp / "runner"
        (runner / "roster").mkdir(parents=True)
        (runner / "roster-inputs").mkdir()
        (runner / "roster" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        (runner / "roster-inputs" / "summary.md").write_text("### Model roster\n",
                                                            encoding="utf-8")
        (self.tmp / "issues.json").write_text(json.dumps([issues]), encoding="utf-8")
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"schedule": "0 7 * * 1"}), encoding="utf-8")
        env = {"RUNNER_TEMP": str(runner), "GITHUB_EVENT_PATH": str(event),
               "REPO": "example/skills-evals", "RUN_ID": "1",
               "SERVER_URL": "https://github.example.com",
               "GITHUB_TOKEN": "t", "GH_TOKEN": "t"}
        return self._run_two_jobs(self._gh_script(), env, cwd)

    def _tracker(self):
        return [{"number": 7, "user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": self.MARKER}]

    FAILED = {"proposal": {"status": "same", "changes": []},
              "defaults_failed": {"opus": "timeout"}}

    # R2-3 (#203 probe round 2): a failed gh write in the frozen-`same`
    # branch warns with a fixed text and never fails the step.
    def _run_failing(self, fail, issues):
        self._gh_fail = fail
        return self._run_step(self.FAILED, issues)


class TestProposeStepOnAFailedProbe(_ProposeStepFixture):
    """F3 (#203 probe round 1): eval.yml's "Propose a roster change" step
    warns with a fixed text when the computed roster carries
    `defaults_failed`, and keeps the tracking issue open even on "same"."""

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_a_failed_probe_warns_with_the_fixed_text(self):
        out = self._run_fragment({"proposal": {"status": "same"},
                                  "defaults_failed": {"opus": "timeout",
                                                      "sonnet": "no-init"}})
        self.assertIn(self.WARNING.format(n=2, word=self._plural(2)) + "\n", out)
        self.assertIn("COUNT=2\n", out)
        self.assertIn("NOTE=**The vendor-default probe failed", out)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_no_failure_or_a_junk_document_warns_nothing(self):
        for payload in ({"proposal": {"status": "same"}},
                        {"defaults_failed": ["opus"]},
                        {"defaults_failed": "::error::x"},
                        "{not json", "[1, 2]"):
            with self.subTest(payload=str(payload)[:30]):
                out = self._run_fragment(payload)
                self.assertNotIn("::warning::", out)
                self.assertIn("COUNT=0\n", out)
                self.assertIn("NOTE=\n", out)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_the_failed_note_reuses_the_singular_plural_word(self):
        # N2 (adv round 10): `failed_note` used to hardcode "families" even
        # at count 1; it now reuses `$failed_word`, same as the ::warning::.
        out = self._run_fragment({"proposal": {"status": "same"},
                                  "defaults_failed": {"opus": "timeout"}})
        self.assertIn("NOTE=**The vendor-default probe failed for 1 family this run.**",
                      out)
        out = self._run_fragment({"proposal": {"status": "same"},
                                  "defaults_failed": {"opus": "timeout",
                                                      "sonnet": "no-init"}})
        self.assertIn("NOTE=**The vendor-default probe failed for 2 families this "
                      "run.**", out)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_the_mismatch_note_reuses_the_singular_plural_word(self):
        out = self._run_fragment({"proposal": {"status": "same"},
                                  "defaults_mismatched": {"opus": {
                                      "id": "claude-opus-9", "class": "not-available"}}})
        self.assertIn("NOTE=**A vendor default did not match this run's catalogue "
                      "for 1 family this run.**", out)
        out = self._run_fragment({"proposal": {"status": "same"},
                                  "defaults_mismatched": {
                                      "opus": {"id": "claude-opus-9",
                                              "class": "not-available"},
                                      "sonnet": {"id": "claude-sonnet-9",
                                                "class": "not-available"}}})
        self.assertIn("NOTE=**A vendor default did not match this run's catalogue "
                      "for 2 families this run.**", out)

    def test_the_fragment_interpolates_nothing(self):
        fragment = self._fragment()
        self.assertNotIn("${{", fragment)
        # Each warning's text is fixed: the only expansion in each line is
        # the count that line's own case validated as digits, and the
        # singular/plural word chosen from that same count in shell (#203
        # probe round 9, R9-3: "for 1 families" must read "for 1 family")
        # — the failed count and word in the first ::warning:: line, the
        # mismatched count and word in the second (R8-1, #203 probe round 8).
        warning_lines = [ln for ln in fragment.splitlines() if "::warning::" in ln]
        self.assertEqual(len(warning_lines), 2, warning_lines)
        for line, var, word_var in zip(
                warning_lines, ("defaults_failed", "defaults_mismatched"),
                ("failed_word", "mismatched_word")):
            self.assertEqual(re.findall(r"\$\{?(\w+)", line), [var, word_var], line)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_same_with_a_failed_probe_opens_the_issue(self):
        out, calls, body = self._run_step(self.FAILED, [])
        self.assertIn(self.WARNING.format(n=1, word=self._plural(1)), out)
        # R9-3 (#203 probe round 9): the singular reads "1 family", never
        # the plural "1 families".
        self.assertIn("for 1 family;", out)
        self.assertNotIn("for 1 families", out)
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue create"), calls)
        self.assertIn("probe failed", calls[0])
        self.assertTrue(body.startswith(self.MARKER), body)
        # N2 (adv round 10): the note body reuses `$failed_word` too, so
        # the singular reads "1 family", never "1 families".
        self.assertIn("The vendor-default probe failed for 1 family this run", body)
        self.assertNotIn("for 1 families", body)
        self.assertIn("### Model roster", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_same_with_a_failed_probe_keeps_an_open_issue_open(self):
        out, calls, body = self._run_step(self.FAILED, self._tracker())
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue edit 7"), calls)
        self.assertFalse(any(c.startswith("issue close") for c in calls), calls)
        self.assertIn("probe failed", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_same_with_a_clean_probe_still_closes_the_issue(self):
        out, calls, _ = self._run_step({"proposal": {"status": "same", "changes": []}},
                                       self._tracker())
        self.assertNotIn("::warning::", out)
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue close 7"), calls)

    def test_the_differs_bodies_carry_the_probe_note(self):
        # F2 moved the actual differs-branch body construction into the
        # `roster-pr` job's own step; the `eval` job's tail past this
        # fragment only forwards `probe_note` as a job output once.
        tail = self.run_body[self.run_body.index(self.END):]
        self.assertGreaterEqual(tail.count('"$probe_note"')
                                + self.roster_pr_run_body.count('"$probe_note"'), 3)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_failed_create_warns_and_exits_zero(self):
        out, calls, _ = self._run_failing("issue create", [])
        self.assertTrue(calls[0].startswith("issue create"), calls)
        self.assertIn("::warning::could not create the roster tracking issue\n", out)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_failed_edit_warns_and_exits_zero(self):
        out, calls, _ = self._run_failing("issue edit", self._tracker())
        self.assertTrue(calls[0].startswith("issue edit 7"), calls)
        self.assertIn("::warning::could not update the roster tracking issue\n", out)
        self.assertNotIn("updated the roster tracking issue", out)


class TestProposeStepOnAMismatch(_ProposeStepFixture):
    """R8-1 (#203 probe round 8): a catalogue mismatch with no probe
    failure is loud in the workflow exactly like a failure — its own fixed
    `::warning::`, its own probe-note sentence, and an open tracking issue
    even on "same" — but the issue's title and first line never say "probe
    failed" for a mismatch-only run, and name both classes when both are
    present. The reviewer's h1.py, h2.py and h3.py."""

    MISMATCH_WARNING = ("::warning::vendor default not matched by this run's "
                        "catalogue for {n} {word}; their seats were held")

    MISMATCHED = {"proposal": {"status": "same", "changes": []},
                 "defaults_mismatched": {"opus": {"id": "claude-opus-9",
                                                  "class": "not-available"}}}
    BOTH = {"proposal": {"status": "same", "changes": []},
           "defaults_failed": {"sonnet": "timeout"},
           "defaults_mismatched": {"opus": {"id": "claude-opus-9",
                                            "class": "not-available"}}}

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_mismatch_only_same_with_no_issue_creates_one(self):
        out, calls, body = self._run_step(self.MISMATCHED, [])
        self.assertIn(self.MISMATCH_WARNING.format(n=1, word=self._plural(1)), out)
        self.assertIn("for 1 family;", out)
        self.assertNotIn("for 1 families", out)
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue create"), calls)
        self.assertNotIn("probe failed", calls[0])
        self.assertIn("did not match", calls[0])
        self.assertTrue(body.startswith(self.MARKER), body)
        self.assertNotIn("probe failed", body)
        self.assertIn("did not match this run's catalogue", body)
        self.assertIn("### Model roster", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_mismatch_only_same_with_an_open_issue_edits_never_closes(self):
        out, calls, body = self._run_step(self.MISMATCHED, self._tracker())
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue edit 7"), calls)
        self.assertFalse(any(c.startswith("issue close") for c in calls), calls)
        self.assertNotIn("probe failed", body)
        self.assertIn("did not match this run's catalogue", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_both_classes_present_names_both_in_the_title_and_never_closes(self):
        out, calls, body = self._run_step(self.BOTH, [])
        self.assertIn(self.WARNING.format(n=1, word=self._plural(1)), out)
        self.assertIn(self.MISMATCH_WARNING.format(n=1, word=self._plural(1)), out)
        # S3 (round 2): "same" now ALSO looks up a stale roster PR
        # to close, in EVERY mode — one more `gh api ...pulls...` call
        # from the roster-pr job, alongside this job's own issue call.
        self.assertEqual(len([c for c in calls if c.startswith("issue ")]), 1, calls)
        self.assertTrue(calls[0].startswith("issue create"), calls)
        self.assertIn("probe failed", calls[0])
        self.assertIn("did not match", calls[0])
        self.assertFalse(any(c.startswith("issue close") for c in calls), calls)


class TestWorkflowProbeFallbackDocument(unittest.TestCase):
    """A probe that exits non-zero writes no document; the roster step then
    writes a stand-in in its place, so the roster freezes every family
    rather than reading "no probe at all" (F1 + F7, #203 probe round 1).
    The stand-in names its own class, `probe-exited` (R2-4, #203 probe
    round 2); an empty `{}` is a probe that ran and resolved nothing."""

    def test_a_failed_probe_leaves_a_document_that_freezes(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        script = next(s for s in doc["jobs"]["roster"]["steps"]
                      if "roster" in (s.get("name") or "").lower())["run"]
        line = next(ln.strip() for ln in script.splitlines()
                    if "scripts/probe_model_defaults.py" in ln
                    and not ln.strip().startswith("#"))
        self.assertIn("|| printf '{\"probe_exit\": \"nonzero\"}\\n' "
                      "> \"$work/defaults.json\"", line)
        self.assertTrue(line.endswith("|| true"), line)
        result = roster.compute_roster(
            models_doc=_RosterFixture._models_doc(), census_doc=_RosterFixture._census(),
            policy=_RosterFixture._policy(), previous=copy.deepcopy(_RosterFixture.PREVIOUS),
            now=_RosterFixture.NOW, warn=lambda _m: None, defaults_doc={})
        self.assertEqual(result["defaults_document_failed"], "no-defaults")


class TestProbeKillsItsProcessGroup(_ProbeFixture):
    """F4 (#203 probe round 1): whatever the leader does, `_stop` SIGKILLs
    the whole process group — a child that ignores SIGTERM included."""

    def test_a_sigterm_ignoring_child_in_the_group_is_killed(self):
        pidfile = self.tmp / "child.pid"
        wrapper = self._wrapper("init-orphan-child",
                                extra_env={"FAKE_INIT_PIDFILE": str(pidfile)})
        policy = self._policy(["opus"])

        def gone(pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            try:  # reparented and killed but not yet reaped: a zombie is gone
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
                return stat.rsplit(")", 1)[1].split()[0] in ("Z", "X")
            except OSError:
                return True

        def reap():
            if pidfile.exists():
                with contextlib.suppress(ProcessLookupError, ValueError):
                    os.kill(int(pidfile.read_text(encoding="utf-8")), 9)

        self.addCleanup(reap)
        done, doc = self._probe(wrapper, "--policy", str(policy), "--timeout", "120",
                                timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"]})
        pid = int(pidfile.read_text(encoding="utf-8"))
        # A short bounded wait on process state — the one sleep here, and
        # it is in the test, not in product code: SIGKILL delivery to a
        # reparented process is asynchronous.
        for _ in range(100):
            if gone(pid):
                break
            time.sleep(0.05)
        self.assertTrue(gone(pid), f"the group child {pid} survived the probe")


class TestProbeResolvesClaude(_ProbeFixture):
    """F7 (#203 probe round 1): `--claude` is resolved once, in main(), with
    `shutil.which` and then made absolute — so a relative path means what it
    meant in the caller's directory, not in the probe's temporary HOME."""

    def test_a_relative_claude_path_works(self):
        wrapper = self._wrapper()
        out = self.tmp / "defaults.json"
        done = subprocess.run(
            [sys.executable, str(PROBE), "--claude", f"./{wrapper.name}",
             "--policy", str(self._policy(["opus"])), "--out", str(out)],
            capture_output=True, text=True, timeout=120, cwd=self.tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"]})
        self.assertEqual(doc["harness_version"], FAKE_VERSION_LINE)

    def test_a_bare_name_resolves_on_path(self):
        wrapper = self._wrapper()
        env = dict(os.environ, PATH=f"{self.tmp}{os.pathsep}{os.environ.get('PATH', '')}")
        done, doc = self._probe(wrapper.name, "--policy", str(self._policy(["opus"])),
                                env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(doc["defaults"], {"opus": self.RESOLVED["opus"]})


class TestUndatedDefaultMapsOntoADatedId(_RosterFixture):
    """F6 (#203 probe round 1): an undated id the catalogue does not list
    maps onto its ONE dated `<id>-YYYYMMDD`, mirroring the dated->undated
    collapse; none is "not available", two or more is ambiguous — and both
    of those are catalogue mismatches, which freeze the family exactly like
    a probe failure, in their own wording (R2-1, #203 probe round 2; R7-1,
    round 7)."""

    def _catalogue(self, *dated):
        return self._models_doc(drop=("claude-opus-5-5",), extra=[
            self._model(i, "x", "2026-09-26T12:00:00Z") for i in dated])

    def test_one_dated_candidate_resolves(self):
        # The reviewer's snap.py, first case.
        result, warnings = self._compute(
            defaults=self.DEFAULTS, models=self._catalogue("claude-opus-5-5-20260926"))
        self.assertEqual(result["defaults"]["resolved"]["opus"],
                         "claude-opus-5-5-20260926")
        self.assertEqual(result["defaults"]["unresolved"], [])
        self.assertIn("claude-opus-5-5-20260926", self._arms(result))
        self.assertIn("vendor default for the opus tier",
                      self._reason(result, "claude-opus-5-5-20260926"))
        self.assertNotIn("defaults_failed", result)

    def test_no_dated_candidate_is_not_available(self):
        result, _ = self._compute(defaults=self.DEFAULTS, models=self._catalogue())
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"], "not-available")
        self.assertIn("not an available model",
                      result["defaults"]["unresolved"][0]["reason"])

    def test_two_dated_candidates_are_ambiguous_and_freeze(self):
        models = self._catalogue("claude-opus-5-5-20260926", "claude-opus-5-5-20261001")
        result, warnings = self._compute(defaults=self.DEFAULTS, models=models)
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"],
                         "ambiguous-snapshot")
        self.assertNotIn("opus", result["defaults"]["resolved"])
        why = result["defaults"]["unresolved"][0]["reason"]
        self.assertIn("2 dated snapshots", why)
        # Frozen exactly like a probe failure (R7-1, #203 probe round 7):
        # opus-5, the listed previous arm, holds the seat; neither dated
        # snapshot of opus-5-5 earns one while it does.
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(result, "claude-opus-5"))
        for snap in ("claude-opus-5-5-20260926", "claude-opus-5-5-20261001"):
            self.assertNotIn(snap, self._arms(result))
            self.assertIn("no new seat on a catalogue mismatch while the family "
                          "holds a seat the Models API still lists",
                          self._reason(result, snap, "excluded"))

    def test_a_dated_id_of_another_base_is_not_a_candidate(self):
        # `claude-opus-5-20260101` is a snapshot of opus-5, not of opus-5-5.
        result, _ = self._compute(defaults=self.DEFAULTS, models=self._catalogue(
            "claude-opus-5-20260101"))
        self.assertNotIn("defaults_failed", result)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"], "not-available")


class TestNetworkWording(unittest.TestCase):
    """F5 (#203 probe round 1): the real CLI makes unauthenticated TLS
    connections before and after init; the docs must not say nothing is
    sent, and the "cannot run either" claim was false."""

    FILES = ("README.md", "DESIGN.md",
             "docs/decisions/0002-roster-follows-vendor-defaults.md",
             "scripts/probe_model_defaults.py", ".github/workflows/eval.yml",
             "evals/roster-policy.yml", "harness/roster.py")
    STALE = ("nothing is sent", "sends nothing", "before it can try the api",
             "never left to try the api", "cannot run either",
             "eval cannot run", "carries nothing over")

    def test_no_stale_claim_survives(self):
        for rel in self.FILES:
            flat = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").lower().split())
            flat = re.sub(r"\s*#\s*", " ", flat)
            for stale in self.STALE:
                with self.subTest(file=rel, stale=stale):
                    self.assertNotIn(stale, flat)

    def test_the_docs_say_what_is_true_instead(self):
        for rel in ("README.md", "DESIGN.md",
                    "docs/decisions/0002-roster-follows-vendor-defaults.md",
                    "scripts/probe_model_defaults.py"):
            flat = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
            with self.subTest(file=rel):
                self.assertIn("no credential", flat)
                self.assertIn("frozen", flat.lower())


# --- #203 probe round 2 -------------------------------------------------------


def _mismatch_words(model_id, alias, cls):
    return (f"the CLI's default `{model_id}` for `{alias}` does not match this "
            f"run's catalogue ({cls})")


class TestCatalogueMismatchFreezesLikeAFailure(_WeeklyLoop):
    """R2-1(a) (#203 probe round 2), superseded by R7-1 (round 7): the probe
    ANSWERED for an alias, but this run's Models API catalogue does not
    match the answer. That is not a probe FAILURE, but since round 7 it
    FREEZES the family exactly the same way a failure does (the reviewer's
    g2.py, g3.py: a mismatch week changes no seat a clean week would not),
    replacing round 4's guessed "effective default". The wording still
    says what happened, never "probe failed" or "unknown this run". A
    wrong-tier or wrong-family answer is a failure since R3-2: see
    TestMismatchGetsNoNewestInTierSeat."""

    def _clean_baseline(self, **kwargs):
        # A clean probe naming opus-5 directly: the same listed previous
        # arm the freeze below holds, so a one-week mismatch changes no
        # seat this clean run would not (the governing guarantee, R7-1).
        return self._compute(defaults={**self.DEFAULTS, "skipped": [],
                                       "defaults": {"opus": "claude-opus-5"}},
                             **kwargs)[0]

    def _assert_frozen_as_the_clean_run_would_seat(self, result, baseline, alias):
        self.assertNotIn("defaults_failed", result)
        self.assertNotIn("defaults_document_failed", result)
        self.assertEqual(self._arms(result), self._arms(baseline))
        self.assertEqual(result["retired_since_last"], baseline["retired_since_last"])
        self.assertIn(alias, result["defaults_mismatched"])

    def test_each_mismatch_class_freezes_like_the_clean_run_would_seat(self):
        # `no-created-at` is a PROBE FAILURE since R5-1 (#203 probe round
        # 5), not a mismatch class: see TestNoCreatedAtIsAProbeFailure.
        cases = (
            ("not-available", {"opus": "claude-opus-9"}, "opus", {}),
            ("ambiguous-snapshot", {"opus": "claude-opus-5-5"}, "opus",
             {"models": self._models_doc(drop=("claude-opus-5-5",), extra=[
                 self._model("claude-opus-5-5-20260926", "x", "2026-09-26T12:00:00Z"),
                 self._model("claude-opus-5-5-20261001", "x", "2026-09-26T12:00:00Z")])}),
        )
        for cls, defaults, alias, kwargs in cases:
            with self.subTest(cls=cls):
                baseline = self._clean_baseline(**kwargs)
                result, warnings = self._compute(
                    defaults={**self.DEFAULTS, "defaults": defaults,
                              "skipped": []}, **kwargs)
                self._assert_frozen_as_the_clean_run_would_seat(result, baseline, alias)
                self.assertEqual(result["defaults_mismatched"][alias]["class"], cls)
                self.assertEqual(result["defaults_mismatched"][alias]["id"],
                                 defaults[alias])
                said = _mismatch_words(defaults[alias], alias, cls)
                self.assertTrue(any(said in w for w in warnings), warnings)
                summary = roster.render_summary(result)
                self.assertIn(said, summary)
                self.assertIn("held; none retired on a catalogue mismatch",
                              self._reason(result, "claude-opus-5"))
                text = "\n".join(warnings) + summary
                self.assertNotIn("probe failed", text.lower())
                self.assertNotIn("unknown this run", text)

    def test_scenario_a_a_persistent_mismatch_holds_the_listed_seat_and_says_so(self):
        # The reviewer's loop.py A, updated for R5-2 (#203 probe round 5):
        # the CLI says opus is an id this bearer never lists, while opus-5
        # (a previous arm, from `PREV0`) is still listed and opus-5-5 (newer,
        # heavily used) is not yet one. The listed seat holds — opus-5-5
        # earns no seat while it remains listed — and the wording says so.
        def usage(n):
            return {"claude-sonnet-5": 900, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0, "claude-opus-5": 0,
                    "claude-opus-5-5": 400}

        out, _ = self._loop(usage, 1, lambda k: self._docs(
            k, opus="claude-opus-6", sonnet="claude-sonnet-5",
            haiku="claude-haiku-4-5"))
        week0 = out[0]["result"]
        self.assertNotIn("defaults_failed", week0)
        self.assertIn("claude-opus-5", self._arms(week0))
        self.assertNotIn("claude-opus-5-5", self._arms(week0))
        said = _mismatch_words("claude-opus-6", "opus", "not-available")
        self.assertIn(said, self._reason(week0, "claude-opus-5"))
        self.assertTrue(any(said in w for w in out[0]["warnings"]), out[0]["warnings"])
        head = "\n".join(roster.render_summary(week0).splitlines()[:4])
        self.assertIn(said, head)

    def test_a_probe_error_still_freezes(self):
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout"}}
        result, _ = self._compute(defaults=doc)
        self.assertEqual(result["defaults_failed"], {"opus": "timeout"})
        self.assertNotIn("defaults_mismatched", result)

    def test_a_clean_probe_publishes_no_mismatch_key(self):
        result, _ = self._compute(defaults=self.DEFAULTS)
        self.assertNotIn("defaults_mismatched", result)


class TestDatedDefaultOntoUndated(_WeeklyLoop):
    """R2-1(b) (#203 probe round 2): the probe answers a dated
    `<base>-YYYYMMDD` the catalogue does not list, while it lists `<base>`:
    the default resolves onto `<base>` (the mirror of round 1's F6)."""

    def test_one_run(self):
        defaults = {**self.DEFAULTS, "defaults": {**self.DEFAULTS["defaults"],
                                                  "haiku": "claude-haiku-4-5-20251001"}}
        result, warnings = self._compute(defaults=defaults)
        self.assertEqual(result["defaults"]["resolved"]["haiku"], "claude-haiku-4-5")
        self.assertNotIn("defaults_failed", result)
        self.assertNotIn("defaults_mismatched", result)
        self.assertFalse(any("haiku" in w for w in warnings), warnings)

    def test_scenario_b_haiku_at_40_percent_is_seated(self):
        # The reviewer's loop.py B.
        def usage(n):
            return {"claude-sonnet-5": 500, "claude-haiku-4-5": 600,
                    "claude-opus-5": 400, "claude-opus-5-5": 0}

        out, _ = self._loop(usage, 3, lambda k: self._docs(
            k, opus="claude-opus-5", sonnet="claude-sonnet-5",
            haiku="claude-haiku-4-5-20251001"))
        for k, week in enumerate(out):
            self.assertIn("claude-haiku-4-5", self._arms(week["result"]), k)
            self.assertNotIn("defaults_failed", week["result"], k)
        self.assertIn("vendor default for the haiku tier",
                      self._reason(out[0]["result"], "claude-haiku-4-5"))


class TestFrozenFamilyStillSeatsByUsage(_WeeklyLoop):
    """R2-1(c) (#203 probe round 2), narrowed by R3-1 (round 3): a frozen
    family retires nothing (but a model gone from the Models API). While it
    still holds a seat the Models API lists it gets no new seat at all;
    once it holds none, a model of it that clears the usage ENTRY bar is
    seated, so the roster never loses the family the fleet moved to."""

    NEW = [_RosterFixture._model("claude-sonnet-6", "S6", "2026-10-05T00:00:00Z"),
           _RosterFixture._model("claude-opus-6", "O6", "2026-10-05T00:00:00Z")]

    @staticmethod
    def _no_init(k):
        return {"probed_at": "2026-10-01T00:00:00Z",
                "harness_version": "3.0.0 (Claude Code)", "defaults": {},
                "skipped": [], "errors": {a: "no-init" for a in
                                          ("haiku", "sonnet", "opus", "fable", "mythos")}}

    @staticmethod
    def _usage(n):
        return {"claude-sonnet-6": 900, "claude-opus-6": 800,
                "claude-sonnet-5": 0, "claude-opus-5": 0}

    def test_scenario_c_every_alias_no_init_while_the_fleet_moves(self):
        # The reviewer's whole.py, chained: three weeks with both
        # generations listed, then the old ones leave the Models API.
        models = list(self.BASE) + list(self.NEW)
        out, prev = self._loop(self._usage, 3, self._no_init, models=models)
        week0 = out[0]["result"]
        # R3-1: the old arms are still listed, so the families are on the
        # roster and a failed probe adds nothing — not even opus-6 at 47%.
        self.assertEqual(sorted(self._arms(week0)), ["claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(week0["retired_since_last"], [])
        # Old arms are held although they are at 0%: a freeze never removes.
        self.assertIn("held; none retired on a failed probe",
                      self._reason(week0, "claude-opus-5"))
        for model_id in ("claude-opus-6", "claude-sonnet-6", "claude-opus-5-5"):
            self.assertIn("no new seat on a failed probe while the family holds a "
                          "seat the Models API still lists",
                          self._reason(week0, model_id, "excluded"))
        # haiku and fable hold no seat: only the entry bar could seat them.
        for model_id in ("claude-haiku-4-5", "claude-fable-5-1"):
            self.assertNotIn(model_id, self._arms(week0))
            self.assertIn("no new seat on a failed probe but by the",
                          self._reason(week0, model_id, "excluded"))
        for week in out[1:]:
            self.assertEqual(week["result"]["retired_since_last"], [])
            self.assertEqual(sorted(self._arms(week["result"])),
                             sorted(self._arms(week0)))
        gone = [m for m in models if m["id"] not in ("claude-sonnet-5", "claude-opus-5")]
        out2, _ = self._loop(self._usage, 1, self._no_init, previous=prev, models=gone)
        last = out2[0]["result"]
        self.assertEqual(sorted(self._arms(last)), ["claude-opus-6", "claude-sonnet-6"])
        self.assertEqual(sorted(r["id"] for r in last["retired_since_last"]),
                         ["claude-opus-5", "claude-sonnet-5"])
        for entry in last["retired_since_last"]:
            self.assertEqual(entry["reason"], "no longer returned by the Models API")

    def test_scenario_c_from_the_seed_roster_is_never_empty(self):
        # whole.py exactly: the old generation already gone at week 0.
        gone = [m for m in list(self.BASE) + list(self.NEW)
                if m["id"] not in ("claude-sonnet-5", "claude-opus-5")]
        out, _ = self._loop(self._usage, 2, self._no_init, models=gone)
        for week in out:
            self.assertEqual(sorted(self._arms(week["result"])),
                             ["claude-opus-6", "claude-sonnet-6"])

    def test_a_frozen_seat_by_usage_says_the_probe_failed(self):
        models = [m for m in list(self.BASE) + list(self.NEW)
                  if m["id"] not in ("claude-sonnet-5", "claude-opus-5")]
        out, _ = self._loop(self._usage, 1, self._no_init, models=models)
        why = self._reason(out[0]["result"], "claude-opus-6")
        self.assertIn("unknown this run (probe: no-init)", why)
        self.assertIn("entry bar", why)


class TestProbeExitedDocument(_RosterFixture):
    """R2-4 (#203 probe round 2): eval.yml's stand-in for a probe that
    exited non-zero is its own class, `probe-exited` — a probe failure that
    freezes — while `{}` from a probe that ran stays `no-defaults`."""

    def test_the_stand_in_freezes_as_probe_exited(self):
        result, warnings = self._compute(defaults={"probe_exit": "nonzero"})
        self.assertEqual(result["defaults_document_failed"], "probe-exited")
        self.assertEqual(result["defaults_failed"],
                         {w: "probe-exited" for w in roster.tier_words(self._policy())})
        self.assertEqual(sorted(self._arms(result)), ["claude-opus-5", "claude-sonnet-5"])
        self.assertTrue(any("the probe script exited with an error" in w
                            for w in warnings), warnings)
        self.assertIn("the probe script exited with an error",
                      roster.render_summary(result))

    def test_an_empty_document_stays_no_defaults(self):
        result, _ = self._compute(defaults={})
        self.assertEqual(result["defaults_document_failed"], "no-defaults")

    def test_the_workflow_writes_the_stand_in(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        script = next(s for s in doc["jobs"]["roster"]["steps"]
                      if s.get("name") == "Refresh the model roster")["run"]
        line = next(ln.strip() for ln in script.splitlines()
                    if "scripts/probe_model_defaults.py" in ln
                    and not ln.strip().startswith("#"))
        self.assertIn("|| printf '{\"probe_exit\": \"nonzero\"}\\n' > \"$work/defaults.json\"",
                      line)
        self.assertTrue(line.endswith("|| true"), line)
        stand_in = json.loads('{"probe_exit": "nonzero"}')
        result, _ = self._compute(defaults=stand_in)
        self.assertEqual(result["defaults_document_failed"], "probe-exited")


class TestProposeStepRunsUnlessCancelled(unittest.TestCase):
    """R2-2 (#203 probe round 2): the loud channels — the proposal step's
    warning and tracking issue — fire even when a later-listed step before
    it (the eval) failed, but never on a cancelled workflow."""

    def _step(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        return next(s for s in doc["jobs"]["roster"]["steps"]
                    if s.get("name") == "Propose a roster change")

    def test_the_step_runs_unless_cancelled(self):
        condition = " ".join(str(self._step().get("if", "")).split())
        self.assertEqual(condition, "${{ !cancelled() }}")
        self.assertNotIn("always()", condition)

    def test_it_still_handles_a_missing_roster(self):
        body = self._step()["run"]
        self.assertIn('if [ ! -f "$computed" ]; then', body)

    def test_the_docs_say_what_fires(self):
        for path in (EVAL_WORKFLOW,
                     REPO_ROOT / "docs" / "decisions"
                     / "0002-roster-follows-vendor-defaults.md"):
            flat = " ".join(path.read_text(encoding="utf-8").split())
            flat = re.sub(r"\s*#\s*", " ", flat)
            with self.subTest(path=path.name):
                self.assertIn("unless the workflow is cancelled", flat)


class TestHarnessVersionKillsItsGroup(_ProbeFixture):
    """R2-5 (#203 probe round 2): `claude --version` runs through the same
    new-session Popen and `_stop` as the alias probes, so a forked child
    holding stdout is killed and the timeout is enforced."""

    def _fake(self, *, print_version):
        pidfile = self.tmp / "verchild.pid"
        path = self.tmp / "claude-verchild"
        path.write_text(
            f"#!{sys.executable}\n"
            "import os, sys, time\n"
            "if '--version' in sys.argv:\n"
            "    pid = os.fork()\n"
            "    if pid == 0:\n"
            "        time.sleep(120)\n"
            "        os._exit(0)\n"
            f"    open({str(pidfile)!r}, 'w').write(str(pid))\n"
            + ("    print('9.9.9', flush=True)\n" if print_version else "")
            + "    os._exit(0)\n"
            "sys.exit(1)\n", encoding="utf-8")
        path.chmod(0o755)
        return path, pidfile

    @staticmethod
    def _gone(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return stat.rsplit(")", 1)[1].split()[0] in ("Z", "X")
        except OSError:
            return True

    def _check(self, print_version, timeout, expected):
        import probe_model_defaults
        fake, pidfile = self._fake(print_version=print_version)

        def reap():
            if pidfile.exists():
                with contextlib.suppress(ProcessLookupError, ValueError):
                    os.kill(int(pidfile.read_text(encoding="utf-8")), 9)

        self.addCleanup(reap)
        started = time.monotonic()
        version = probe_model_defaults.harness_version(str(fake), timeout)
        elapsed = time.monotonic() - started
        self.assertEqual(version, expected)
        pid = int(pidfile.read_text(encoding="utf-8"))
        for _ in range(100):  # bounded wait on process state, as above
            if self._gone(pid):
                break
            time.sleep(0.05)
        self.assertTrue(self._gone(pid), f"the --version child {pid} survived")
        return elapsed

    def test_a_forked_child_holding_stdout_is_killed(self):
        elapsed = self._check(True, 30.0, "9.9.9")
        self.assertLess(elapsed, 15.0)

    def test_the_timeout_is_enforced_without_a_version_line(self):
        elapsed = self._check(False, 1.0, None)
        self.assertLess(elapsed, 15.0)

    def test_the_docstring_names_the_setsid_escape(self):
        text = " ".join(PROBE.read_text(encoding="utf-8").split())
        self.assertIn("setsid()", text)
        self.assertIn("escapes the process-group kill", text)


class TestRound2Docs(unittest.TestCase):
    """R2-6 (#203 probe round 2): the stale claims are gone."""

    STALE = {
        "HANDOFF.md": ("a failed probe falls back to the usage rules",),
        "evals/roster-policy.yml": ("a tier with no resolved vendor default, e.g. haiku",),
    }

    def test_no_stale_claim_survives(self):
        for rel, stale in self.STALE.items():
            flat = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
            flat = re.sub(r"\s*#\s*", " ", flat)
            for claim in stale:
                with self.subTest(file=rel, claim=claim):
                    self.assertNotIn(claim, flat)


# --- #203 probe round 3 -------------------------------------------------------


def _failed_doc(skipped=("mythos",)):
    """Every ladder alias the probe did not skip failed with `no-init`."""
    return {"probed_at": "2026-10-01T00:00:00Z",
            "harness_version": "2.1.283 (Claude Code)", "defaults": {},
            "skipped": list(skipped),
            "errors": {a: "no-init" for a in ("haiku", "sonnet", "opus", "fable", "mythos")
                       if a not in skipped}}


class TestFreezeSeatsByUsageOnlyWhenTheFamilyHoldsNothing(_WeeklyLoop):
    """R3-1 (#203 probe round 3): in a frozen family a model clearing the
    usage entry bar is seated ONLY when the family holds no previous arm the
    Models API still lists this run — the "the roster would lose the family"
    case. Otherwise a frozen family gets no new seat at all, so a one-week
    failure can never add a seat the next clean week retires."""

    def _clean(self, opus):
        return lambda k: self._docs(k, opus=opus, sonnet="claude-sonnet-5",
                                    haiku="claude-haiku-4-5")

    def _weeks(self, out):
        return [self._arms(week["result"]) for week in out]

    def test_s1_a_superseded_model_is_not_seated_on_a_failed_week(self):
        # The reviewer's s1.py: default opus-5-5 seated; opus-5 (superseded)
        # still carries 30%; the probe fails in week 1 only.
        def usage(n):
            return {"claude-sonnet-5": 500, "claude-opus-5": 300,
                    "claude-opus-5-5": 200, "claude-haiku-4-5": 0}

        prev = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-5"}])
        clean = self._clean("claude-opus-5-5")
        base, _ = self._loop(usage, 6, clean, previous=prev)
        out, _ = self._loop(usage, 6, lambda k: _failed_doc() if k == 1 else clean(k),
                            previous=prev)
        self.assertEqual(self._weeks(out), self._weeks(base))
        week1 = out[1]["result"]
        self.assertIn("opus", week1["defaults_failed"])
        self.assertEqual(week1["added_since_last"], [])
        self.assertIn("no new seat on a failed probe while the family holds a seat "
                      "the Models API still lists",
                      self._reason(week1, "claude-opus-5", "excluded"))
        for week in out:
            self.assertEqual(week["result"]["retired_since_last"], [])

    def test_s2_a_preview_is_not_seated_on_a_failed_week(self):
        # The reviewer's s2.py: default opus-5 seated; the fleet runs the
        # preview opus-5-5 at 30% via --model.
        def usage(n):
            return {"claude-sonnet-5": 500, "claude-opus-5": 200, "claude-opus-5-5": 300}

        prev = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}])
        clean = self._clean("claude-opus-5")
        base, _ = self._loop(usage, 4, clean, previous=prev)
        out, _ = self._loop(usage, 4, lambda k: _failed_doc() if k == 1 else clean(k),
                            previous=prev)
        self.assertEqual(self._weeks(out), self._weeks(base))
        self.assertEqual(out[1]["result"]["added_since_last"], [])
        self.assertNotIn("claude-opus-5-5", self._arms(out[1]["result"]))

    def _gone_models(self):
        return ([m for m in self.BASE if m["id"] not in ("claude-sonnet-5", "claude-opus-5")]
                + [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                   self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")])

    def test_the_usage_seat_boundary_is_the_entry_bar_inclusive(self):
        # R3-7(b): with no held arm left in the API, a share EXACTLY at
        # `arm_enter_usage_pct` is seated — the same `>=` as rule 1.
        for opus6, seated in ((100, True), (99, False)):
            with self.subTest(opus6=opus6):
                def usage(n, opus6=opus6):
                    return {"claude-sonnet-6": 1000 - opus6, "claude-opus-6": opus6}

                out, _ = self._loop(usage, 1, lambda k: _failed_doc(skipped=()),
                                    models=self._gone_models())
                result = out[0]["result"]
                self.assertIn("opus", result["defaults_failed"])
                self.assertEqual("claude-opus-6" in self._arms(result), seated)
                if seated:
                    why = self._reason(result, "claude-opus-6")
                    self.assertIn("carries 10.0% of rankable census usage", why)
                    self.assertIn("holds no seat the Models API still lists", why)
                else:
                    self.assertIn("usage entry bar",
                                  self._reason(result, "claude-opus-6", "excluded"))


class TestFrozenFamilyFallsBackWithNoCensus(_RosterFixture):
    """R3-6 (#203 probe round 3): with no usable enter window, a frozen
    family holding no seat the Models API still lists falls back to newest
    per tier, as the no-probe fallback does — a freeze never empties the
    roster where no probe at all would not."""

    NOW5 = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    PREV = {**_RosterFixture.PREVIOUS,
            "arms": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}]}

    def _run(self, models, defaults):
        warnings: list[str] = []
        result = roster.compute_roster(
            models_doc={"fetched_at": self.NOW5.isoformat(), "models": models},
            census_doc=None, policy=self._policy(), previous=copy.deepcopy(self.PREV),
            now=self.NOW5, warn=warnings.append, defaults_doc=defaults)
        return result

    def test_s5_a_failed_probe_seats_what_no_probe_would(self):
        models = [self._model("claude-haiku-5", "H5", "2026-09-01T00:00:00Z"),
                  self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]
        no_probe = self._run(models, None)
        failed = self._run(models, _failed_doc())
        self.assertEqual(self._arms(failed), self._arms(no_probe))
        self.assertEqual(sorted(self._arms(failed)),
                         ["claude-haiku-5", "claude-opus-6", "claude-sonnet-6"])
        self.assertIn("falls back to newest per tier as with no probe",
                      self._reason(failed, "claude-opus-6"))
        self.assertIn("unknown this run (probe: no-init)",
                      self._reason(failed, "claude-opus-6"))

    def test_a_held_arm_still_blocks_the_fallback(self):
        models = [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-5", "O5", "2026-04-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]
        failed = self._run(models, _failed_doc())
        self.assertIn("claude-opus-5", self._arms(failed))
        self.assertNotIn("claude-opus-6", self._arms(failed))
        self.assertIn("claude-sonnet-6", self._arms(failed))


class TestMismatchKeepsTheNoCensusFallback(_RosterFixture):
    """R3-6's guard, kept for a mismatch too (#203 probe rounds 3 and 7,
    R7-1): with no usable enter window a mismatched family holding no seat
    the Models API still lists keeps the no-probe fallback (its newest
    model), so a mismatch never empties the family either; holding one, the
    freeze holds that seat and nothing newer is seated."""

    NOW5 = TestFrozenFamilyFallsBackWithNoCensus.NOW5
    PREV = TestFrozenFamilyFallsBackWithNoCensus.PREV
    _run = TestFrozenFamilyFallsBackWithNoCensus._run

    MISMATCH = {"probed_at": "2026-10-01T00:00:00Z",
                "harness_version": "2.1.283 (Claude Code)",
                "defaults": {"opus": "claude-opus-9"}, "skipped": ["mythos"],
                "errors": {}}

    def test_s5_a_mismatch_seats_what_no_probe_would(self):
        models = [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]
        no_probe = self._run(models, None)
        result = self._run(models, self.MISMATCH)
        self.assertEqual(result["defaults_mismatched"]["opus"]["class"], "not-available")
        self.assertEqual(self._arms(result), self._arms(no_probe))
        self.assertIn("claude-opus-6", self._arms(result))

    def test_a_held_arm_blocks_the_newest_seat(self):
        models = [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-5", "O5", "2026-04-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]
        result = self._run(models, self.MISMATCH)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertNotIn("claude-opus-6", self._arms(result))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(result, "claude-opus-6", "excluded"))


class TestMismatchGetsNoNewestInTierSeat(_WeeklyLoop):
    """R3-2 (#203 probe round 3), as decided since R7-1 (round 7) and
    narrowed by R5-1 (round 5, `no-created-at` is now a probe failure, not a
    mismatch class): a CATALOGUE MISMATCH (not-available, ambiguous-snapshot)
    FREEZES its family exactly like a probe failure, so a model newer than
    the listed previous arm it holds gets no new seat, and a one-week
    mismatch adds nothing the next week retires."""

    @staticmethod
    def _usage(n):
        return {"claude-sonnet-5": 500, "claude-opus-5": 300, "claude-opus-5-5": 0}

    def _clean(self, k):
        return self._docs(k, opus="claude-opus-5", sonnet="claude-sonnet-5",
                          haiku="claude-haiku-4-5")

    def test_s3_a_one_week_mismatch_adds_and_flips_nothing(self):
        # `no-created-at` moved to TestNoCreatedAtIsAProbeFailure (R5-1,
        # #203 probe round 5): it is a probe failure now, not this class.
        dated = "2026-09-01T00:00:00Z"
        cases = (
            ("not-available", "claude-opus-6", list(self.BASE)),
            ("ambiguous-snapshot", "claude-opus-7",
             list(self.BASE) + [self._model("claude-opus-7-20260901", "O7", dated),
                                self._model("claude-opus-7-20260902", "O7", dated)]),
        )
        for cls, bad, models in cases:
            with self.subTest(cls=cls):
                base, _ = self._loop(self._usage, 4, self._clean, models=models)
                out, _ = self._loop(
                    self._usage, 4,
                    lambda k: self._docs(k, opus=bad, sonnet="claude-sonnet-5",
                                         haiku="claude-haiku-4-5") if k == 1
                    else self._clean(k), models=models)
                self.assertEqual([self._arms(w["result"]) for w in out],
                                 [self._arms(w["result"]) for w in base])
                week1 = out[1]["result"]
                self.assertEqual(week1["defaults_mismatched"]["opus"]["class"], cls)
                self.assertNotIn("defaults_failed", week1)
                self.assertEqual(week1["added_since_last"], [])
                self.assertEqual(out[2]["result"]["retired_since_last"], [])
                self.assertIn("held; none retired on a catalogue mismatch",
                              self._reason(week1, "claude-opus-5"))
                held_seat = ("no new seat on a catalogue mismatch while the family "
                            "holds a seat the Models API still lists")
                newer = [e for e in week1["excluded"] if held_seat in e["reason"]]
                self.assertTrue(newer, week1["excluded"])
                for entry in newer:
                    self.assertIn(_mismatch_words(bad, "opus", cls), entry["reason"])

    def test_a_mismatch_holds_the_listed_seat_and_seats_no_newer_used_model(self):
        # Round 2's loop.py A, updated for R5-2 (#203 probe round 5): opus-5
        # (a previous arm, still listed) HOLDS the seat; opus-5-5 (newer,
        # 30%-used, not yet a previous arm) earns no seat while opus-5
        # remains listed — the governing guarantee wins over seating the
        # model the fleet actually uses.
        def usage(n):
            return {"claude-sonnet-5": 900, "claude-opus-5": 0, "claude-opus-5-5": 400}

        out, _ = self._loop(usage, 1, lambda k: self._docs(
            k, opus="claude-opus-6", sonnet="claude-sonnet-5"))
        week0 = out[0]["result"]
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(week0, "claude-opus-5"))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(week0, "claude-opus-5-5", "excluded"))

    def test_wrong_tier_is_a_probe_failure_and_freezes(self):
        base, _ = self._loop(self._usage, 3, self._clean)
        out, _ = self._loop(self._usage, 3, lambda k: self._docs(
            k, opus="claude-sonnet-5", sonnet="claude-sonnet-5",
            haiku="claude-haiku-4-5") if k == 1 else self._clean(k))
        self.assertEqual([self._arms(w["result"]) for w in out],
                         [self._arms(w["result"]) for w in base])
        week1 = out[1]["result"]
        self.assertEqual(week1["defaults_failed"], {"opus": "wrong-tier"})
        self.assertNotIn("defaults_mismatched", week1)
        self.assertEqual(week1["added_since_last"], [])
        said = [w for w in out[1]["warnings"] if "`opus`" in w]
        self.assertTrue(any("nonsensical" in w and "treated as a probe failure" in w
                            for w in said), said)
        self.assertIn("vendor-default probe failed", roster.render_summary(week1))

    def test_wrong_family_is_a_probe_failure_and_freezes(self):
        policy = self._policy(tiers=["haiku", "sonnet", "opus", ["fable", "mythos"]])
        warnings: list[str] = []
        result = roster.compute_roster(
            models_doc=self._models_doc(), census_doc=self._census(), policy=policy,
            previous=copy.deepcopy(self.PREVIOUS), now=self.NOW, warn=warnings.append,
            defaults_doc={**self.DEFAULTS, "skipped": [],
                          "defaults": {**self.DEFAULTS["defaults"],
                                       "mythos": "claude-fable-5"}})
        self.assertEqual(result["defaults_failed"], {"mythos": "wrong-family"})
        self.assertNotIn("defaults_mismatched", result)
        self.assertTrue(any("nonsensical" in w for w in warnings), warnings)


class TestRound3Wording(_WeeklyLoop):
    """R3-4 (#203 probe round 3): the exact phrases, and the old ones gone."""

    def test_a_mismatch_says_does_not_match(self):
        out, _ = self._loop(lambda n: {"claude-sonnet-5": 500, "claude-opus-5": 300}, 1,
                            lambda k: self._docs(k, opus="claude-opus-6",
                                                 sonnet="claude-sonnet-5"))
        result = out[0]["result"]
        text = roster.render_summary(result) + "\n".join(out[0]["warnings"])
        self.assertIn("the CLI's default `claude-opus-6` for `opus` does not match this "
                      "run's catalogue (not-available)", text)
        self.assertNotIn("is not in this run's catalogue", text)
        self.assertNotIn("as with no default", text)

    def test_a_held_arm_says_none_retired(self):
        out, _ = self._loop(lambda n: {"claude-sonnet-5": 500, "claude-opus-5": 300}, 1,
                            lambda k: _failed_doc())
        result = out[0]["result"]
        why = self._reason(result, "claude-opus-5")
        self.assertTrue(why.endswith("held; none retired on a failed probe"), why)
        self.assertNotIn("no seat changes", json.dumps(result))
        summary = roster.render_summary(result)
        self.assertIn("none retired except models gone from the Models API", summary)
        self.assertNotIn("none retired, and none added but by the usage entry bar", summary)

    def test_the_workflow_probe_note_says_the_same(self):
        text = " ".join(EVAL_WORKFLOW.read_text(encoding="utf-8").split())
        self.assertIn("none retired except models gone from the Models API", text)
        self.assertNotIn("Their seats were held and none retired;", text)

    def test_the_docs_quote_the_new_phrases(self):
        adr = " ".join((REPO_ROOT / "docs" / "decisions"
                        / "0002-roster-follows-vendor-defaults.md").read_text(
                            encoding="utf-8").split())
        self.assertIn("held; none retired on a failed probe", adr)
        for rel in ("README.md", "DESIGN.md",
                    "docs/decisions/0002-roster-follows-vendor-defaults.md"):
            flat = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
            with self.subTest(file=rel):
                self.assertIn("does not match this run's catalogue", flat)
                self.assertNotIn("is not in this run's catalogue", flat)
                self.assertNotIn("no seat changes on a failed probe", flat)


class TestEvalNoteBeforeTheEval(unittest.TestCase):
    """R3-3 (#203 probe round 3) used to build this sentence from the `eval`
    JOB's `result` (`needs.eval.result`, read by `roster-pr` after the eval).
    ADR 0004 (2026-09-30) moved `roster-pr` BEFORE the eval — it arms the
    roster pull request so the eval can run on the merged roster — so it
    can no longer know how the eval turned out: the sentence now says what
    WILL happen, and points at the run summary, where `roster-wait` and the
    `eval` job's roster step say which roster the eval used and why. These
    rows replace the old outcome-per-result rows deliberately."""

    START = "# >>> eval-note"
    END = "# <<< eval-note"

    def setUp(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.roster_pr_step = _manage_step(doc)

    def _note(self, event=None, extra_env=None):
        run = self.roster_pr_step["run"]
        fragment = run[run.index(self.START):run.index(self.END)]
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "event.json").write_text(json.dumps(event or {"schedule": "x"}),
                                        encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""),
               "GITHUB_EVENT_PATH": str(tmp / "event.json"), **(extra_env or {})}
        done = subprocess.run(["bash", "-c", "set -euo pipefail\n" + fragment
                               + '\nprintf "%s" "$eval_note"\n'],
                              capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_the_eval_result_is_no_longer_read(self):
        self.assertNotIn("EVAL_RESULT", self.roster_pr_step["env"])
        self.assertNotIn("EVAL_RESULT", self.roster_pr_step["run"])
        self.assertNotIn("${{", self.roster_pr_step["run"])

    @unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
    def test_a_normal_run_says_the_eval_follows_on_the_merged_or_committed_roster(self):
        for event in ({"schedule": "x"}, {"inputs": {"roster_only": False}},
                      {"inputs": {"roster_only": "false"}}):
            with self.subTest(event=event):
                note = self._note(event)
                self.assertIn("The paid eval runs after this job", note)
                self.assertIn("merged roster", note)
                self.assertIn("committed `evals/roster.yml` otherwise", note)
                self.assertIn("The run summary says which roster it used and why", note)
                self.assertNotIn("ran on the committed", note)
                self.assertNotIn("no eval ran", note)

    @unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
    def test_a_stray_eval_result_changes_nothing(self):
        # Nothing in the environment can pull the old outcome sentences back.
        for outcome in ("success", "failure", "cancelled", "skipped"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self._note(extra_env={"EVAL_RESULT": outcome}), self._note())

    @unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
    def test_roster_only_says_no_eval_ran(self):
        for value in (True, "true"):
            with self.subTest(value=value):
                note = self._note({"inputs": {"roster_only": value}})
                self.assertIn("no eval ran", note)
                self.assertNotIn("The paid eval runs after this job", note)


class TestProposeStepGhWritesWarn(_ProposeStepFixture):
    """R3-5 (#203 probe round 3): every `gh issue` write in the propose step
    warns with a fixed `::warning::could not <verb> the roster tracking
    issue` and the step continues; `git push` of the proposal branch still
    fails the step."""

    DIFFERS = {"proposal": {"status": "differs", "changes": []}}

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_failed_close_warns_and_exits_zero(self):
        self._gh_fail = "issue close"
        out, calls, _ = self._run_step({"proposal": {"status": "same", "changes": []}},
                                       self._tracker())
        self.assertTrue(calls[0].startswith("issue close 7"), calls)
        self.assertIn("::warning::could not close the roster tracking issue\n", out)
        self.assertNotIn("closed the stale proposal issue", out)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_failed_rejection_edit_warns_and_exits_zero(self):
        # No scripts/ in the step's working directory: rendering is
        # rejected. The rejection path now also looks up (and, if found,
        # disables auto-merge on) an open PR before writing the issue —
        # the parent fixture's stub `gh` has no special case for `pr list`,
        # so it answers empty and no PR is found.
        self._gh_fail = "issue edit"
        out, calls, body = self._run_step(self.DIFFERS, self._tracker())
        issue_calls = [c for c in calls if c.startswith("issue ")]
        self.assertTrue(issue_calls[0].startswith("issue edit 7"), issue_calls)
        self.assertIn("needs review", issue_calls[0])
        self.assertIn("::warning::could not update the roster tracking issue\n", out)
        self.assertNotIn("updated the blocked proposal issue", out)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_failed_rejection_create_warns_and_exits_zero(self):
        self._gh_fail = "issue create"
        out, calls, _ = self._run_step(self.DIFFERS, [])
        issue_calls = [c for c in calls if c.startswith("issue ")]
        self.assertTrue(issue_calls[0].startswith("issue create"), issue_calls)
        self.assertIn("::warning::could not create the roster tracking issue\n", out)

    def test_every_gh_issue_write_is_guarded(self):
        """Structural, over BOTH fragments (F2 split the differs-branch gh
        issue writes into the roster-pr job's `write_issue` helper — one
        `edit`/`create` pair shared by its three call sites, rather than one
        literal pair per call site as the pre-split script had): every `gh
        issue` command is an `if` condition or ends in
        `|| echo "::warning::could not ..."`."""
        def find_commands(body):
            lines = body.splitlines()
            commands = []
            i = 0
            while i < len(lines):
                stripped = lines[i].strip()
                if re.match(r"^(if )?gh issue ", stripped):
                    command = stripped
                    while command.endswith("\\"):
                        i += 1
                        command = command[:-1] + " " + lines[i].strip()
                    commands.append(command)
                i += 1
            return commands
        commands = find_commands(self.run_body) + find_commands(self.roster_pr_run_body)
        # eval job: frozen-same edit + create, clean-same close (3).
        # roster-pr job: `write_issue`'s one edit + create pair, shared by
        # its three call sites (2).
        self.assertGreaterEqual(len(commands), 5, commands)
        for command in commands:
            with self.subTest(command=command[:40]):
                guarded = (command.startswith("if gh issue") or re.search(
                    r'\|\| echo "::warning::could not (close|update|create) the roster '
                    r'tracking issue"$', command))
                self.assertTrue(guarded, command)

    def test_the_roster_job_pushes_nothing_and_a_failed_publish_only_warns(self):
        # Round 7 (ADR 0003): the `roster` job no longer commits or pushes
        # — `roster-pr` publishes `roster/proposal` with the roster App's
        # token — so no step of `roster` carries any git write, and the
        # publish that replaced the push is a fixed warning like every
        # other write the proposal makes, never a failed job.
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        for step in doc["jobs"]["roster"]["steps"]:
            words = (step.get("run") or "").split()
            with self.subTest(step=step.get("name")):
                for verb in ("push", "commit", "worktree"):
                    self.assertFalse(any(a == "git" and b == verb
                                         for a, b in zip(words, words[1:])),
                                     f"`git {verb}` in the roster job")
                self.assertNotIn("extraheader", step.get("run") or "")
                self.assertNotIn("--force-with-lease", step.get("run") or "")
        self.assertIn('echo "::warning::could not publish the roster proposal to '
                      'roster/proposal"', self.roster_pr_run_body)
        readme = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("a failed `gh issue` write in it is a fixed `::warning::`, never a "
                      "failed job; it pushes nothing itself — `roster-pr` publishes "
                      "`roster/proposal` with the roster App's token, and a failed "
                      "publish there is a fixed `::warning::` too", readme)


class TestProposeStepDiffersBranchGhWrites(_ProposeStepFixture):
    """R3-5: the differs branch's final `gh issue` write, run for real
    against a throwaway git repository whose `origin` is a local bare repo
    created here (never a copy of this checkout, so nothing can reach a real
    remote)."""

    def _repo(self):
        origin = self.tmp / "origin.git"
        work = self.tmp / "work"
        git = ["git", "-c", "init.defaultBranch=main"]
        subprocess.run(git + ["init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(git + ["init", "-q", str(work)], check=True)
        shutil.copytree(REPO_ROOT / "harness", work / "harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (work / "scripts").mkdir()
        shutil.copy2(SCRIPTS_DIR / "render_roster_yaml.py", work / "scripts")
        (work / "evals").mkdir()
        shutil.copy2(REPO_ROOT / "evals" / "roster.yml", work / "evals")
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *ident, "commit", "-q", "-m", "x"],
                       check=True, env={**os.environ, **FIXED_GIT_DATES})
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)],
                       check=True)
        return work

    def _differs(self):
        committed = yaml.safe_load((REPO_ROOT / "evals" / "roster.yml").read_text(
            encoding="utf-8"))
        loop = _WeeklyLoop()
        out, _ = loop._loop(lambda n: {"claude-sonnet-5": 500, "claude-opus-5": 300,
                                       "claude-opus-5-5": 300}, 1,
                            lambda k: loop._docs(k), previous=committed)
        result = out[0]["result"]
        self.assertEqual(result["proposal"]["status"], "differs")
        return result

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_failed_final_create_and_edit_warn_and_exit_zero(self):
        latest = self._differs()
        work = self._repo()
        for fail, issues, verb in (("issue create", [], "create"),
                                   ("issue edit", self._tracker(), "update")):
            with self.subTest(fail=fail):
                self._gh_fail = fail
                (self.tmp / "gh.log").unlink(missing_ok=True)
                shutil.rmtree(self.tmp / "runner", ignore_errors=True)
                shutil.rmtree(self.tmp / "bin", ignore_errors=True)
                out, calls, _ = self._run_step(latest, issues, cwd=work)
                self.assertTrue(any("a change is proposed" in c for c in calls), calls)
                self.assertIn(f"::warning::could not {verb} the roster tracking issue\n",
                              out)


class TestRosterPrJobEnvMatchesOutputs(unittest.TestCase):
    """F2 (adversarial round 1 on #209): the `roster-pr` job's step `env:`
    must read every `needs.eval.outputs.<key>` the `eval` job actually
    declares, under the SAME env var name `_ProposeStepFixture.OUTPUT_ENV`
    stitches job 1's outputs into job 2's env for in every hermetic test
    above — a rename on either side that drifts from the other would make
    every one of those tests pass against a mapping the real workflow does
    not use, which is exactly the gap this test closes."""

    def _doc(self):
        return yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))

    def test_env_maps_every_declared_output_under_the_fixtures_names(self):
        doc = self._doc()
        roster_outputs = doc["jobs"]["roster"]["outputs"]
        roster_pr_env = _manage_step(doc)["env"]
        # `roster_latest_json` is the one output NOT read by `roster-pr`
        # (B1, round 3 on #209): it goes to the `eval` job's badge step
        # instead, checked separately below.
        self.assertEqual(set(roster_outputs) - {"roster_latest_json"},
                         set(_ProposeStepFixture.OUTPUT_ENV),
                         "the roster job's outputs (minus roster_latest_json): "
                         "keys must match _ProposeStepFixture.OUTPUT_ENV exactly")
        for key, var in _ProposeStepFixture.OUTPUT_ENV.items():
            with self.subTest(key=key):
                self.assertEqual(roster_outputs[key],
                                 "${{ steps.propose.outputs.%s }}" % key)
                self.assertEqual(roster_pr_env.get(var),
                                 "${{ needs.roster.outputs.%s }}" % key,
                                 f"roster-pr job's env.{var} must read "
                                 f"needs.roster.outputs.{key}")

    def test_eval_job_badge_step_reads_roster_latest_json_from_needs(self):
        # B1 (round 3 on #209): the ONE roster output not read by
        # `roster-pr` — it is an exhibit copy the badge step threads
        # through instead (ADR 0001, decision 3: read by no decision). B1
        # (round 4 on #209, blocker): that step lives in the `publish` job
        # now, not `eval`.
        doc = self._doc()
        roster_outputs = doc["jobs"]["roster"]["outputs"]
        self.assertEqual(roster_outputs["roster_latest_json"],
                         "${{ steps.propose.outputs.roster_latest_json }}")
        badge_step = next(s for s in doc["jobs"]["publish"]["steps"]
                          if s.get("name") == "Build the badge over the run window, "
                                              "commit, and push")
        self.assertEqual(badge_step["env"].get("ROSTER_LATEST_JSON"),
                         "${{ needs.roster.outputs.roster_latest_json }}")

    def test_roster_pr_job_runs_before_the_eval_unless_cancelled(self):
        doc = self._doc()
        job = doc["jobs"]["roster-pr"]
        # Round 5 made this `[roster, eval, publish]` so no in-run write
        # holder could still be running once roster-pr armed the PR. ADR
        # 0004 moves the arming BEFORE the eval: `roster-wait` resolves it
        # (merged or disarmed) before `eval`, and so `publish`, can start —
        # test_issue_eval_on_merged_roster.TestTheJobGraph asserts that.
        self.assertEqual(job.get("needs"), ["roster", "disarm"])
        # B1.4 (round 2): only on `main` — added to the original
        # `!cancelled()` gate, never replacing it.
        self.assertEqual(job.get("if"),
                         "${{ !cancelled() && github.ref == 'refs/heads/main' }}")

    def test_eval_job_needs_roster_and_is_not_fatal_to_a_roster_failure(self):
        # B1 (round 3 on #209): the `eval` job depends on `roster` (for the
        # exhibit-copy output only) but must still run on the committed
        # roster when `roster` fails outright — never the other way
        # (`roster` must never `needs: eval`). B1 (round 4 on #209,
        # blocker): `eval` now ALSO needs `disarm`, which must run and
        # finish (or fail harmlessly) before the agent starts.
        doc = self._doc()
        self.assertIsNone(doc["jobs"]["roster"].get("needs"))
        eval_job = doc["jobs"]["eval"]
        # ADR 0004: `eval` also waits for `roster-pr` (the arming) and
        # `roster-wait` (the bounded wait that resolves it). ADR 0008 adds
        # the successful fixture plan without making roster failures fatal.
        self.assertEqual(eval_job.get("needs"),
                         ["plan", "roster", "disarm", "roster-pr", "roster-wait"])
        # ADR 0004 review, S1 (fail closed): also gated on `roster-wait`
        # confirming the arming cleared (or being skipped, off `main`); a
        # failed `roster` or `disarm` is still never fatal.
        self.assertEqual(eval_job.get("if"), "${{ !cancelled() && needs.plan.result == 'success' && !inputs.roster_only && (needs.roster-wait.result == 'skipped' || needs.roster-wait.outputs.cleared == 'true') }}")
        self.assertEqual(doc["jobs"]["disarm"].get("needs"), "roster")


class TestRosterModeFragment(_ProposeStepFixture):
    """`roster_mode` in `evals/roster-policy.yml`: `auto`/`proposal` pass
    through, a missing key or file is silently `proposal`, and any other
    value is a fixed warning plus `proposal` (spec-roster-mode.md #1)."""

    def test_missing_key_is_silently_proposal(self):
        d = self.tmp / "missing-key"; d.mkdir()
        out = self._run_mode_fragment(d, "arm_enter_usage_pct: 10\n")
        self.assertNotIn("::warning::", out)
        self.assertIn("MODE=proposal\n", out)

    def test_missing_file_is_silently_proposal(self):
        d = self.tmp / "missing-file"; d.mkdir()
        out = self._run_mode_fragment(d, None)
        self.assertNotIn("::warning::", out)
        self.assertIn("MODE=proposal\n", out)

    def test_auto_passes_through(self):
        d = self.tmp / "auto"; d.mkdir()
        out = self._run_mode_fragment(d, "roster_mode: auto\n")
        self.assertNotIn("::warning::", out)
        self.assertIn("MODE=auto\n", out)

    def test_proposal_passes_through(self):
        d = self.tmp / "proposal"; d.mkdir()
        out = self._run_mode_fragment(d, "roster_mode: proposal\n")
        self.assertNotIn("::warning::", out)
        self.assertIn("MODE=proposal\n", out)

    def test_bad_value_warns_and_falls_back(self):
        d = self.tmp / "bad"; d.mkdir()
        out = self._run_mode_fragment(d, "roster_mode: yolo\n")
        self.assertIn("::warning::roster_mode is not auto or proposal; using proposal\n",
                      out)
        self.assertIn("MODE=proposal\n", out)

    def test_the_fragment_interpolates_nothing(self):
        self.assertNotIn("${{", self._mode_fragment())


class _AutoProposeStepFixture(_ProposeStepFixture):
    """Extends the parent fixture's stub `gh` with the `roster_mode: auto`
    surface (`gh pr list/create/edit/merge/close`, `gh workflow run`).
    Every other command (issue create/edit/close, `gh api`, `--body-file`
    capture) behaves exactly as `_ProposeStepFixture`'s stub."""

    def setUp(self):
        super().setUp()
        #: `gh api .../pulls?...`'s row set when `_pr_list_rows` is None —
        #: a single same-repo row numbered `_pr_list_value` ("" or a
        #: non-digit means no open PR), matching the REPO `_run_step` uses.
        self._pr_list_value = ""
        #: S4 (round 2): a list of GitHub PR objects — each at least
        #: `{"number": int, "head": {"repo": {"full_name": "<owner>/<repo>"}}}`
        #: — filtered through the REAL `--jq` expression eval.yml's
        #: `find_roster_pr` actually sends (via real `jq`, not a hardcoded
        #: stand-in), so a regression to an unfiltered `.[0].number` form
        #: would make a foreign-owner row reappear here. None (the
        #: default) keeps the plain `_pr_list_value` behavior above.
        self._pr_list_rows = None
        #: the digits `gh pr create`'s printed URL ends with.
        self._pr_create_number = "55"

    def _auth_calls(self):
        """(token, argument line) for every stub `gh` call of the last
        run — every runner resets `gh-auth.log` alongside `gh.log`."""
        path = self.tmp / "gh-auth.log"
        if not path.exists():
            return []
        return [tuple(ln.split("\t", 1)) for ln in
                path.read_text(encoding="utf-8").splitlines()]

    def _gitdata(self):
        """Round 7: every git-data call of the last run, as logged by
        GITDATA_STUB — `{"method", "url", "token", "body"}` each."""
        path = self.tmp / "gitdata.log"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]

    def _gitdata_writes(self):
        return [c for c in self._gitdata() if c["method"] != "GET"]

    @staticmethod
    def _rev(work, ref):
        """`ref` in `work`, or None when it does not exist."""
        done = subprocess.run(["git", "-C", str(work), "rev-parse", "-q", "--verify", ref],
                              capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else None

    def _write_policy(self, cwd, mode):
        (cwd / "evals").mkdir(parents=True, exist_ok=True)
        text = "" if mode is None else f"roster_mode: {mode}\n"
        (cwd / "evals" / "roster-policy.yml").write_text(text, encoding="utf-8")

    def _gh_script(self):
        rows = self._pr_list_rows
        if rows is None:
            rows = []
            case = self._pr_list_value
            if case and case.isdigit():
                rows = [{"number": int(case),
                         "head": {"repo": {"full_name": "example/skills-evals"},
                                  "ref": "roster/proposal"}}]
        rows_file = self.tmp / "pr-list-rows.json"
        rows_file.write_text(json.dumps(rows), encoding="utf-8")
        fail_sentinel = getattr(self, "_gh_fail", None) or "\x01NEVERMATCH\x01"
        return (
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {str(self.tmp / 'gh.log')!r}\n"
            # Round 6: which credential each call ran with — `gh` itself
            # reads GH_TOKEN first, then GITHUB_TOKEN, so this records the
            # one it would actually use.
            "tok=\"${GH_TOKEN:-${GITHUB_TOKEN:-}}\"\n"
            f"printf '%s\\t%s\\n' \"$tok\" \"$*\" >> {str(self.tmp / 'gh-auth.log')!r}\n"
            # R2-3: `_gh_fail` names a SUBSTRING of the full argument line
            # that fails — checked before the api routing and the pr-
            # command stand-ins below, so a failing api/`pr create`/etc.
            # call really fails rather than also printing a stand-in.
            "case \"$*\" in\n"
            f"  *{fail_sentinel!r}*)\n"
            "    echo 'HTTP 502' >&2; exit 1 ;;\n"
            "esac\n"
            + self._api_routing() +
            "if [ \"$1 $2\" = 'pr create' ]; then "
            f"printf 'https://github.example.com/example/skills-evals/pull/%s\\n' {self._pr_create_number!r}; "
            "exit 0; fi\n"
            "prev=''\n"
            "for arg in \"$@\"; do\n"
            f"  if [ \"$prev\" = --body-file ]; then cat \"$arg\" > {str(self.tmp / 'body.md')!r}; fi\n"
            "  prev=\"$arg\"\n"
            "done\n")

    def _run_step(self, latest, issues, cwd=None):
        runner = self.tmp / "runner"
        (runner / "roster").mkdir(parents=True)
        (runner / "roster-inputs").mkdir()
        (runner / "roster" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        (runner / "roster-inputs" / "summary.md").write_text("### Model roster\n",
                                                            encoding="utf-8")
        (self.tmp / "issues.json").write_text(json.dumps([issues]), encoding="utf-8")
        event = self.tmp / "event.json"
        # ADR 0004 review, nit 2: a test may set `self._event` to model a
        # dispatch (e.g. `roster_only`); a scheduled run by default.
        event.write_text(json.dumps(getattr(self, "_event", None) or {"schedule": "0 7 * * 1"}),
                         encoding="utf-8")
        env = {"RUNNER_TEMP": str(runner), "GITHUB_EVENT_PATH": str(event),
               "REPO": "example/skills-evals", "RUN_ID": "1",
               "SERVER_URL": "https://github.example.com",
               "GITHUB_TOKEN": "t", "GH_TOKEN": "t"}
        return self._run_two_jobs(self._gh_script(), env, cwd)

    def _repo(self, mode="auto", name="work"):
        """A throwaway git repository (never a copy of this checkout — its
        `origin` is a local bare repo created here) carrying enough of the
        tree (`harness/`, `scripts/render_roster_yaml.py`, the committed
        `evals/roster.yml`, and a written `evals/roster-policy.yml`) for the
        "differs" branch's render + admission to pass."""
        origin = self.tmp / f"{name}-origin.git"
        work = self.tmp / name
        git = ["git", "-c", "init.defaultBranch=main"]
        subprocess.run(git + ["init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(git + ["init", "-q", str(work)], check=True)
        shutil.copytree(REPO_ROOT / "harness", work / "harness",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (work / "scripts").mkdir()
        shutil.copy2(SCRIPTS_DIR / "render_roster_yaml.py", work / "scripts")
        (work / "evals").mkdir()
        shutil.copy2(REPO_ROOT / "evals" / "roster.yml", work / "evals")
        self._write_policy(work, mode)
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *ident, "commit", "-q", "-m", "x"],
                       check=True, env={**os.environ, **FIXED_GIT_DATES})
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)],
                       check=True)
        return work

    def _differs(self, defaults_failed=None, defaults_mismatched=None):
        committed = yaml.safe_load((REPO_ROOT / "evals" / "roster.yml").read_text(
            encoding="utf-8"))
        loop = _WeeklyLoop()
        out, _ = loop._loop(lambda n: {"claude-sonnet-5": 500, "claude-opus-5": 300,
                                       "claude-opus-5-5": 300}, 1,
                            lambda k: loop._docs(k), previous=committed)
        result = out[0]["result"]
        self.assertEqual(result["proposal"]["status"], "differs")
        if defaults_failed is not None:
            result = dict(result); result["defaults_failed"] = defaults_failed
        if defaults_mismatched is not None:
            result = dict(result); result["defaults_mismatched"] = defaults_mismatched
        return result

    DIFFERS = {"proposal": {"status": "differs", "changes": []}}
    SAME = {"proposal": {"status": "same", "changes": []}}


class TestRosterModeAutoDiffers(_AutoProposeStepFixture):
    """`roster_mode: auto` + a differs proposal that pushes cleanly + a
    clean probe: the "Propose a roster change" step opens/updates a pull
    request, dispatches `ci.yml` on its head, and enables auto-merge
    (spec-roster-mode.md #3)."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_clean_auto_differs_opens_a_pr_and_enables_auto_merge(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_create_number = "77"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ") or c.startswith("workflow ")]
        # S4 (round 2): the PR lookup is now `gh api .../pulls?...`, not
        # `gh pr list` — check the api call log directly.
        self.assertTrue(any(c.startswith("api ") and "/pulls?" in c for c in calls), calls)
        create = next((c for c in pr_calls if c.startswith("pr create")), None)
        self.assertIsNotNone(create, pr_calls)
        self.assertIn("--base main", create)
        self.assertIn("--head roster/proposal", create)
        self.assertIn("automatic update (run 1)", create)
        # Round 6: no `ci.yml` dispatch — a dispatched `test` never
        # satisfied the PR's required check (run 36509251840, PR #214);
        # the App-opened PR's own `pull_request` run does.
        self.assertFalse(any(c.startswith("workflow ") for c in pr_calls), pr_calls)
        # N1: enabling is idempotent — a `pr merge ... --disable-auto` runs
        # (and is ignored) BEFORE the `--auto` enable call itself, so the
        # first "pr merge" call is that disable, not the enable.
        merge_calls = [c for c in pr_calls if c.startswith("pr merge")]
        self.assertGreaterEqual(len(merge_calls), 2, merge_calls)
        self.assertIn("--disable-auto", merge_calls[0])
        self.assertNotIn("--auto", merge_calls[0])
        merge = next((c for c in merge_calls if "--auto" in c), None)
        self.assertIsNotNone(merge, pr_calls)
        self.assertTrue(_touches_number([merge], 77), merge)
        self.assertIn("--auto", merge)
        self.assertIn("--merge", merge)
        self.assertIn("--match-head-commit", merge)
        self.assertGreater(merge_calls.index(merge), 0,
                           "N1: the disable-auto call must precede the enable call")
        issue_calls = [c for c in calls if c.startswith("issue ")]
        self.assertTrue(any("merging automatically" in c for c in issue_calls), issue_calls)
        self.assertIn("Pull request #77", body)
        self.assertIn("merges automatically once its `test` check passes", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_an_open_pr_is_edited_not_recreated(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "12"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr create") for c in pr_calls), pr_calls)
        edit = next((c for c in pr_calls if c.startswith("pr edit")), None)
        self.assertIsNotNone(edit, pr_calls)
        self.assertTrue(edit.startswith("pr edit 12"), edit)
        self.assertIn("Pull request #12", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_non_digit_pr_number_is_treated_as_none(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "null"
        self._pr_create_number = "88"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertTrue(any(c.startswith("pr create") for c in pr_calls), pr_calls)
        self.assertFalse(any(c.startswith("pr edit") for c in pr_calls), pr_calls)
        self.assertIn("Pull request #88", body)


class TestRosterModeProposalDiffers(_AutoProposeStepFixture):
    """`roster_mode: proposal` on a differs proposal: no PR/workflow calls
    at all, and the issue text is unchanged from before this feature
    (spec-roster-mode.md's "proposal+differs" row)."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_proposal_mode_makes_no_pr_create_or_workflow_calls(self):
        latest = self._differs()
        work = self._repo("proposal")
        out, calls, body = self._run_step(latest, [], cwd=work)
        # A `pr list` lookup DOES happen now (it also guards against a
        # stale auto-merge — see the disable-auto test below), but nothing
        # that creates, edits, dispatches or merges.
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            or c.startswith("pr merge") or c.startswith("workflow ")
                            for c in calls), calls)
        issue_calls = [c for c in calls if c.startswith("issue ")]
        self.assertTrue(any("a change is proposed" in c for c in issue_calls), issue_calls)
        self.assertIn("Open a pull request from `roster/proposal` and merge it after CI",
                      body)
        self.assertNotIn("roster_mode", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_proposal_mode_with_an_open_pr_disables_auto_merge(self):
        # Review finding: a PR opened by an earlier `roster_mode: auto` run
        # must not keep auto-merge once the mode is switched back to
        # `proposal` — a later push whose `test` happens to pass would
        # otherwise merge a head no run this time approved.
        latest = self._differs()
        work = self._repo("proposal")
        self._pr_list_value = "34"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr create") for c in pr_calls), pr_calls)
        disable = next((c for c in pr_calls
                        if c.startswith("pr merge 34") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, pr_calls)
        self.assertIn("Pull request #34", body)
        self.assertIn("turned off", body)
        self.assertIn("roster_mode", body)


class TestRosterModeAutoDirtyProbe(_AutoProposeStepFixture):
    """`roster_mode: auto` with a probe that is not clean (`defaults_failed`
    or `defaults_mismatched` > 0): today's behavior exactly, and the issue
    says why auto was skipped (spec-roster-mode.md #2)."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_defaults_failed_skips_the_pr_and_says_why(self):
        latest = self._differs(defaults_failed={"opus": "timeout"})
        work = self._repo("auto")
        out, calls, body = self._run_step(latest, [], cwd=work)
        # No PR is open, so the only new call is the lookup itself (which
        # also guards against a stale auto-merge — see
        # TestRosterModeUnclean/ProposalDisablesStaleAutoMerge below).
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            or c.startswith("pr merge") or c.startswith("workflow ")
                            for c in calls), calls)
        self.assertIn("roster_mode: auto", body)
        self.assertIn("vendor-default probe was not clean", body)
        self.assertIn("a human must", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_defaults_mismatched_skips_the_pr_and_says_why(self):
        latest = self._differs(defaults_mismatched={
            "opus": {"id": "claude-opus-9", "class": "not-available"}})
        work = self._repo("auto")
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            or c.startswith("pr merge") or c.startswith("workflow ")
                            for c in calls), calls)
        self.assertIn("vendor-default probe was not clean", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_both_failed_and_mismatched_skip_the_pr(self):
        latest = self._differs(defaults_failed={"opus": "timeout"},
                               defaults_mismatched={
                                   "sonnet": {"id": "claude-sonnet-9",
                                             "class": "not-available"}})
        work = self._repo("auto")
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            or c.startswith("pr merge") or c.startswith("workflow ")
                            for c in calls), calls)
        self.assertIn("vendor-default probe was not clean", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_unclean_probe_with_an_open_pr_disables_auto_merge(self):
        # Review finding: an open PR from an EARLIER (clean) run must not
        # keep auto-merge just because THIS run's probe went dirty — a
        # later push whose `test` happens to pass would otherwise merge a
        # head no run this time approved.
        latest = self._differs(defaults_failed={"opus": "timeout"})
        work = self._repo("auto")
        self._pr_list_value = "21"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ") or c.startswith("workflow ")]
        self.assertFalse(any(c.startswith("workflow run") for c in pr_calls), pr_calls)
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            for c in pr_calls), pr_calls)
        self.assertFalse(any("--auto" in c for c in pr_calls), pr_calls)
        disable = next((c for c in pr_calls
                        if c.startswith("pr merge 21") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, pr_calls)
        self.assertIn("Pull request #21", body)
        self.assertIn("turned off", body)
        self.assertIn("vendor-default probe was not clean", body)


class TestRosterModeAutoRejectedProposal(_AutoProposeStepFixture):
    """`roster_mode: auto` with a proposal rejected before publication
    (render or admission failure, before the branch is even pushed): no PR
    calls, and the issue explains why auto was not reached
    (spec-roster-mode.md #2, "a rejected proposal")."""

    def test_rejected_proposal_makes_no_pr_calls(self):
        d = self.tmp / "rejected"; d.mkdir()
        self._write_policy(d, "auto")
        out, calls, body = self._run_step(self.DIFFERS, [], cwd=d)
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            or c.startswith("pr merge") or c.startswith("workflow ")
                            for c in calls), calls)
        self.assertIn("no pull request was opened", body)
        self.assertIn("roster_mode: auto", body)

    def test_rejected_proposal_with_an_open_pr_disables_auto_merge(self):
        # Review finding: a rejected proposal never touches the branch, so
        # a PR an EARLIER run opened is still exactly as it was — but this
        # run does not approve it either, so its auto-merge must come off.
        d = self.tmp / "rejected-with-pr"; d.mkdir()
        self._write_policy(d, "auto")
        self._pr_list_value = "56"
        out, calls, body = self._run_step(self.DIFFERS, [], cwd=d)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr create") or c.startswith("pr edit")
                            for c in pr_calls), pr_calls)
        disable = next((c for c in pr_calls
                        if c.startswith("pr merge 56") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, pr_calls)
        self.assertIn("Pull request #56", body)
        self.assertIn("turned off", body)


class TestRosterModeAutoSame(_AutoProposeStepFixture):
    """`roster_mode: auto` with a "same" status and an open `roster/proposal`
    PR: the PR is closed alongside the tracking issue
    (spec-roster-mode.md #4)."""

    def test_an_open_pr_is_closed_alongside_the_issue(self):
        d = self.tmp / "same"; d.mkdir()
        self._write_policy(d, "auto")
        self._pr_list_value = "9"
        out, calls, body = self._run_step(self.SAME, self._tracker(), cwd=d)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertTrue(any(c.startswith("pr close 9") for c in pr_calls), pr_calls)
        self.assertTrue(any(c.startswith("issue close 7") for c in calls), calls)

    def test_no_open_pr_closes_nothing_but_still_closes_the_issue(self):
        d = self.tmp / "same-nopr"; d.mkdir()
        self._write_policy(d, "auto")
        self._pr_list_value = ""
        out, calls, body = self._run_step(self.SAME, self._tracker(), cwd=d)
        self.assertFalse(any(c.startswith("pr close") for c in calls), calls)
        self.assertTrue(any(c.startswith("issue close 7") for c in calls), calls)

    def test_proposal_mode_same_still_closes_an_open_pr(self):
        # S3 (round 2, repro A5): ADR 0003 decision 4 says every run that
        # does not re-enable auto-merge turns it off, but a "same" run
        # under `roster_mode: proposal` used to make NO `pr` calls at all
        # — leaving an armed auto-merge PR from an earlier `auto` run open
        # forever once the mode is switched back. The close now runs
        # regardless of mode: the committed roster no longer differs
        # either way, so a PR this automation opened earlier has nothing
        # left to merge.
        d = self.tmp / "same-proposal"; d.mkdir()
        self._write_policy(d, "proposal")
        self._pr_list_value = "9"
        out, calls, body = self._run_step(self.SAME, self._tracker(), cwd=d)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertTrue(any(c.startswith("pr close 9") for c in pr_calls), pr_calls)
        self.assertTrue(any(c.startswith("issue close 7") for c in calls), calls)


class TestRosterModeGhFailuresDegradeToProposal(_AutoProposeStepFixture):
    """Every `gh` call the auto path adds fails the same way: a fixed
    `::warning::could not <verb> the roster pull request`, the step still
    exits 0, and the issue degrades to proposal wording naming a human
    (spec-roster-mode.md #3's last bullet)."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_each_failure_warns_and_degrades(self):
        latest = self._differs()
        cases = (("pulls?state=open", "could not find the roster pull request"),
                 ("pr create", "could not create the roster pull request"),
                 ("pr merge", "could not enable auto-merge for the roster pull request"))
        for i, (fail, warning) in enumerate(cases):
            with self.subTest(fail=fail):
                work = self._repo("auto", name=f"gh-fail-{i}")
                self._gh_fail = fail
                self._pr_list_value = ""
                (self.tmp / "gh.log").unlink(missing_ok=True)
                (self.tmp / "body.md").unlink(missing_ok=True)
                shutil.rmtree(self.tmp / "runner", ignore_errors=True)
                shutil.rmtree(self.tmp / "bin", ignore_errors=True)
                out, calls, body = self._run_step(latest, [], cwd=work)
                self.assertIn(f"::warning::{warning}\n", out)
                # Wording differs by whether a PR number was ever learned
                # (a "pr list"/"pr create" failure never gets one; a
                # "pr merge" failure keeps the one `pr create` already
                # returned) — "did not complete" and "a
                # human must" are the two phrases common to both.
                self.assertIn("did not complete", body)
                self.assertIn("a human must", body)
                self.assertNotIn("merging automatically", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_pr_edit_failure_warns_and_degrades(self):
        latest = self._differs()
        work = self._repo("auto", name="gh-fail-edit")
        self._gh_fail = "pr edit"
        self._pr_list_value = "3"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertIn("::warning::could not update the roster pull request\n", out)
        self.assertIn("a human must", body)
        self.assertIn("Pull request #3", body)
        self.assertIn("turned off", body)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("workflow run") for c in calls), calls)
        self.assertFalse(any("--auto" in c and "--merge" in c for c in pr_calls), pr_calls)
        # The edit itself failed, but the safety net still turns off any
        # auto-merge the PR might already be carrying from an earlier run.
        disable = next((c for c in pr_calls
                        if c.startswith("pr merge 3") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, pr_calls)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_disable_auto_merge_failure_warns_and_exits_zero(self):
        # (d) from the review finding: the disable-auto call itself can
        # fail too, and degrades the same way every other gh call here
        # does — a fixed warning, the step still exits 0.
        latest = self._differs()
        work = self._repo("proposal")
        self._pr_list_value = "9"
        self._gh_fail = "pr merge"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertIn("::warning::could not disable auto-merge for the roster pull request\n",
                      out)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_disable_auto_merge_failure_never_claims_it_was_turned_off(self):
        # F4 (adversarial round 1 on #209, repro R3): the tracking issue
        # must say the disable FAILED, never "has been turned off", when
        # the `gh pr merge --disable-auto` call itself failed.
        latest = self._differs()
        work = self._repo("proposal")
        self._pr_list_value = "9"
        self._gh_fail = "pr merge"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertIn("could NOT be turned off", body)
        self.assertIn("disable it by hand on PR #9", body)
        self.assertNotIn("has been turned off", body)


class TestAdversarialRound1F1NeverMatchesAForkPr(_AutoProposeStepFixture):
    """F1 (adversarial round 1 on #209, blocker), S4 (round 2): the PR
    lookup has no owner filter unless it re-asserts one — round 2 moved it
    from a client-side `isCrossRepository` boolean to a server-side
    `?head=<owner>:roster/proposal` filter PLUS a `.jq` re-check against
    `env.REPO`'s full name, through the REAL `--jq` expression eval.yml
    sends (piped through actual `jq` here, never a hardcoded stand-in), so
    a regression to an unfiltered `.[0].number` form fails these."""

    FOREIGN = {"number": 101, "head": {"repo": {"full_name": "attacker/skills-evals"},
                                       "ref": "roster/proposal"}}
    OWN_55 = {"number": 55, "head": {"repo": {"full_name": "example/skills-evals"},
                                     "ref": "roster/proposal"}}
    OWN_102 = {"number": 102, "head": {"repo": {"full_name": "example/skills-evals"},
                                       "ref": "roster/proposal"}}

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_foreign_owner_only_row_is_treated_as_no_open_pr(self):
        latest = self.SAME
        d = self.tmp / "fork-only"; d.mkdir()
        self._write_policy(d, "auto")
        self._pr_list_rows = [self.FOREIGN]
        out, calls, body = self._run_step(latest, self._tracker(), cwd=d)
        # "same" + roster_mode auto: the PR-close path runs, finds nothing
        # (the foreign-owner row is filtered out), and closes no PR.
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr close") for c in pr_calls), pr_calls)
        self.assertFalse(_touches_number(pr_calls, 101), pr_calls)
        self.assertNotIn("101", body or "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_foreign_owner_row_never_reaches_the_auto_path(self):
        # The auto path (create/edit + enable) never sees the foreign
        # owner's number: with only that row open, it is found as if no PR
        # were open at all, so a fresh one is created rather than that PR
        # being edited or merged.
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_rows = [self.FOREIGN]
        self._pr_create_number = "202"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr edit 101") for c in pr_calls), pr_calls)
        self.assertFalse(_touches_number(pr_calls, 101), pr_calls)
        self.assertTrue(any(c.startswith("pr create") for c in pr_calls), pr_calls)
        self.assertIn("Pull request #202", body)
        self.assertNotIn("#101", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_foreign_owner_row_newer_than_a_same_repo_row_uses_the_same_repo_number(self):
        # gh's default order is newest-first: a foreign-owner PR opened
        # after this repo's own must still lose to the same-repo one.
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_rows = [self.OWN_102 | {"head": {"repo": {"full_name": "attacker/skills-evals"}}},
                              self.OWN_55]
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        edit = next((c for c in pr_calls if c.startswith("pr edit")), None)
        self.assertIsNotNone(edit, pr_calls)
        self.assertTrue(edit.startswith("pr edit 55"), edit)
        self.assertFalse(any(c.startswith("pr create") for c in pr_calls), pr_calls)
        self.assertIn("Pull request #55", body)
        self.assertNotIn("#102", body)

    OWN_OTHER_BRANCH = {"number": 77,
                        "head": {"repo": {"full_name": "example/skills-evals"},
                                 "ref": "some-other-branch"}}

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_same_repo_pr_from_another_branch_is_treated_as_no_open_pr(self):
        # N15 (round 3 on #209): a same-repo, same-base open PR that was
        # opened from some OTHER branch (never `roster/proposal` at all)
        # must never be matched or closed.
        latest = self.SAME
        d = self.tmp / "other-branch"; d.mkdir()
        self._write_policy(d, "auto")
        self._pr_list_rows = [self.OWN_OTHER_BRANCH]
        out, calls, body = self._run_step(latest, self._tracker(), cwd=d)
        pr_calls = [c for c in calls if c.startswith("pr ")]
        self.assertFalse(any(c.startswith("pr close") for c in pr_calls), pr_calls)
        self.assertFalse(_touches_number(pr_calls, 77), pr_calls)
        self.assertNotIn("77", body or "")


class TestAdversarialRound1F5MatchHeadCommit(_AutoProposeStepFixture):
    """F5 (adversarial round 1 on #209, repro R4): `--match-head-commit`
    must carry the sha actually pushed to origin's `roster/proposal`, never
    the checkout's pre-commit HEAD (mutant M1) and never a sha read from
    some other checkout (mutant M8)."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_match_head_commit_equals_the_actually_pushed_sha(self):
        latest = self._differs()
        work = self._repo("auto")
        pre_push_head = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        out, calls, body = self._run_step(latest, [], cwd=work)
        # Round 7: the roster App's ref write lands in the repository the
        # stub API answers from, never a bare `origin` of it.
        pushed = self._rev(work, "refs/heads/roster/proposal")
        self.assertIsNotNone(pushed)
        merge = next(c for c in calls if c.startswith("pr merge") and "--auto" in c)
        self.assertIn(f"--match-head-commit {pushed}", merge)
        # M1: never the pre-publish checkout HEAD (a different commit — the
        # commit created on top of it is what actually got published).
        self.assertNotEqual(pushed, pre_push_head)
        self.assertNotIn(f"--match-head-commit {pre_push_head}", merge)


class TestB1RosterPrHelper(_AutoProposeStepFixture):
    """Base for the round-2 tests below: runs `roster-pr`'s own step in
    isolation, with no `eval` job at all, so each test can hand it exactly
    the (possibly hostile) inputs it wants to probe — B1's independent
    verification and N1's hostile-input validation both need this."""

    def _run_roster_pr_direct(self, overrides, work=None):
        runner_temp = self.tmp / "roster-pr-only"
        shutil.rmtree(runner_temp, ignore_errors=True)
        runner_temp.mkdir(parents=True)
        base = {
            "REPO": "example/skills-evals", "RUN_ID": "1",
            "SERVER_URL": "https://github.example.com",
            "GITHUB_TOKEN": "t", "GH_TOKEN": "t",
            "ROSTER_APP_TOKEN": self._app_token,
            "RUNNER_TEMP": str(runner_temp),
            "TEST_GIT_DIR": str(work) if work else "",
            "GITHUB_SHA": "", "ROSTER_MODE": "", "STATUS": "", "PROBE_CLEAN": "",
            "ISSUE_NUMBER": "", "BASE_SHA": "", "PROPOSED_ROSTER_B64": "", "REJECTED": "",
            "REJECTION_REASON": "", "RENDERED_IDENTICAL": "", "EVAL_NOTE": "",
            "PROBE_NOTE": "", "ROSTER_SUMMARY": "",
        }
        base.update(overrides)
        stub = self.tmp / "bin2"
        stub.mkdir(exist_ok=True)
        gh = stub / "gh"
        gh.write_text(self._gh_script(), encoding="utf-8")
        gh.chmod(0o755)
        gh_log = self.tmp / "gh.log"
        gh_log.unlink(missing_ok=True)
        (self.tmp / "gh-auth.log").unlink(missing_ok=True)
        (self.tmp / "gitdata.log").unlink(missing_ok=True)
        (self.tmp / "body.md").unlink(missing_ok=True)
        env = dict(base, PATH=f"{stub}{os.pathsep}{os.environ.get('PATH', '')}")
        done = subprocess.run(["bash", "-c", self.roster_pr_run_body], capture_output=True,
                              text=True, timeout=60, env=env, cwd=self.tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        log = gh_log.read_text(encoding="utf-8").splitlines() if gh_log.exists() else []
        body = ((self.tmp / "body.md").read_text(encoding="utf-8")
                if (self.tmp / "body.md").exists() else None)
        return done.stdout, [ln for ln in log if "issues?state=open" not in ln], body

    IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.com"]

    def _candidate_env(self, **overrides):
        """A baseline env that satisfies every precondition for the
        `attempt-candidate` fragment EXCEPT whatever `overrides`
        deliberately breaks — `roster_mode` comes from the policy at
        `$GITHUB_SHA`/`TEST_GIT_DIR` in `_run_roster_pr_direct`, never from
        `ROSTER_MODE` here (B1.1); `_run_candidate_fragment` instead sets
        `$roster_mode` directly, so `GITHUB_SHA` here only needs to be
        SYNTACTICALLY valid (hex, 40 chars) to satisfy the candidate
        fragment's own `[ -n "$github_sha" ]` check."""
        env = {"STATUS": "differs", "PROBE_CLEAN": "true", "REJECTED": "false",
               "PUSHED_SHA": "a" * 40, "GITHUB_SHA": "b" * 40}
        env.update(overrides)
        return env

    PARSE_START = "# >>> input-parsing"
    PARSE_END = "# <<< input-parsing"

    def _parsing_fragment(self):
        body = self.roster_pr_run_body
        self.assertIn(self.PARSE_START, body)
        return body[body.index(self.PARSE_START):body.index(self.PARSE_END)]

    CANDIDATE_START = "# >>> attempt-candidate"
    CANDIDATE_END = "# <<< attempt-candidate"

    def _candidate_fragment(self):
        body = self.roster_pr_run_body
        self.assertIn(self.CANDIDATE_START, body)
        return body[body.index(self.CANDIDATE_START):body.index(self.CANDIDATE_END)]

    def _run_candidate_fragment(self, env, roster_mode="auto"):
        """Run the initial parsing block, then set `$roster_mode` directly
        (bypassing the policy-read API call entirely — irrelevant here)
        and run ONLY the `attempt-candidate` fragment, printing `$candidate`
        — isolates M12/M17 from `verify_publish`'s independent head/compare
        checks, which would otherwise mask a dropped `rejected`/
        `pushed_sha` check here from a test that only observes the
        combined `attempt` outcome (real git calls, a real branch)."""
        # Round 7: `$pushed_sha` is no longer an input the parse reads — it
        # is set by this job's own publish — so the candidate fragment's
        # value for it is set directly here, after the parse.
        pushed = env.get("PUSHED_SHA", "")
        script = ("set -uo pipefail\n" + self._parsing_fragment() + "\n"
                  f"pushed_sha={pushed!r}\n"
                  f"roster_mode={roster_mode!r}\n" + self._candidate_fragment() + "\n"
                  'printf "CANDIDATE_OUT=%s\\n" "$candidate"\n')
        base = {"GITHUB_SHA": "", "STATUS": "", "PROBE_CLEAN": "", "ISSUE_NUMBER": "",
                "BASE_SHA": "", "REJECTED": "", "REJECTION_REASON": "",
                "RENDERED_IDENTICAL": ""}
        base.update({k: v for k, v in env.items() if k != "PUSHED_SHA"})
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=30, env=dict(base, PATH=os.environ.get("PATH", "")))
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _run_parsing_fragment(self, env):
        """Run ONLY the initial `needs.eval.outputs.*` parsing block in
        isolation and print every variable it sets — kills a mutation to
        one field's validation directly, rather than only through
        whatever the higher-level attempt gate happens to also block (N1,
        round 2: B1's independent checks now mask some of these
        downstream, so testing the parse itself is what actually catches
        a regression there)."""
        script = (
            "set -uo pipefail\n" + self._parsing_fragment() + "\n"
            'printf "GITHUB_SHA_OUT=%s\\n" "$github_sha"\n'
            'printf "STATUS_OUT=%s\\n" "$status"\n'
            'printf "PROBE_CLEAN_OUT=%s\\n" "$probe_clean"\n'
            'printf "ISSUE_OUT=%s\\n" "$issue"\n'
            'printf "BASE_SHA_OUT=%s\\n" "$base_sha"\n'
            'printf "PUSHED_SHA_OUT=%s\\n" "$pushed_sha"\n'
            'printf "REJECTED_OUT=%s\\n" "$rejected"\n'
            'printf "REJECTION_REASON_OUT=%s\\n" "$rejection_reason"\n'
            'printf "RENDERED_IDENTICAL_OUT=%s\\n" "$rendered_identical"\n')
        base = {"GITHUB_SHA": "", "STATUS": "", "PROBE_CLEAN": "", "ISSUE_NUMBER": "",
                "BASE_SHA": "", "PUSHED_SHA": "", "REJECTED": "", "REJECTION_REASON": "",
                "RENDERED_IDENTICAL": ""}
        base.update(env)
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=30, env=dict(base, PATH=os.environ.get("PATH", "")))
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _payload(self, work, suffix="# changed\n"):
        """Round 7: what the `roster` job hands `roster-pr` for a proposal
        rendered against `work`'s HEAD — the committed `evals/roster.yml`
        plus `suffix`, base64 — as the env this job reads it from."""
        head = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        data = (work / "evals" / "roster.yml").read_bytes() + suffix.encode()
        import base64
        return {"BASE_SHA": head,
                "PROPOSED_ROSTER_B64": base64.b64encode(data).decode()}


class TestB1PolicyReadFromApi(TestB1RosterPrHelper):
    """B1.1 (round 2): the auto/proposal decision comes from a fresh read
    of `evals/roster-policy.yml` at `$GITHUB_SHA`, never from the `eval`
    job's own (untrusted) `ROSTER_MODE` output."""

    def test_policy_unreadable_falls_back_to_proposal(self):
        work = self.tmp / "unreadable"; work.mkdir()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "a" * 40, "STATUS": "same"}, work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertNotIn("::warning::roster_mode is not auto", out)

    def test_policy_bad_value_warns_and_falls_back(self):
        work = self.tmp / "bad-value"; work.mkdir()
        self._write_policy(work, "yolo")
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "a" * 40, "STATUS": "same"}, work=work)
        self.assertIn("::warning::roster_mode is not auto or proposal; using proposal\n", out)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)

    def test_no_github_sha_falls_back_to_proposal_without_reading_anything(self):
        work = self.tmp / "no-sha"; work.mkdir()
        self._write_policy(work, "auto")
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "same"}, work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertFalse(any(c.startswith("api ") and "contents" in c for c in calls), calls)

    def test_policy_unreadable_and_forged_auto_output_still_falls_back_to_proposal(self):
        # N8 (round 3 on #209): the policy is unreadable/absent AND the
        # `roster` job's own (untrusted) ROSTER_MODE output claims "auto" —
        # fail closed to "proposal" regardless, never the forged value.
        work = self.tmp / "unreadable-forged"; work.mkdir()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "a" * 40, "STATUS": "same", "ROSTER_MODE": "auto"}, work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertNotIn("::warning::roster_mode is not auto", out)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)

    def test_main_saying_proposal_overrides_a_stale_auto_at_github_sha(self):
        # N9 (round 3 on #209): a policy edit that landed on `main` AFTER
        # this run's trigger commit — setting `roster_mode: proposal` to
        # pull the emergency brake — must win even though `$GITHUB_SHA`
        # itself still reads `auto`.
        work = self._repo("auto")
        github_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self._write_policy(work, "proposal")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm",
                        "pull the emergency brake"], check=True)
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": github_sha, "STATUS": "same"}, work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertIn("raw=auto", out)  # confirms $GITHUB_SHA alone still says auto

    def test_github_sha_saying_proposal_is_not_overridden_by_main(self):
        # The mirror case: `$GITHUB_SHA` itself says `proposal` (or main
        # simply hasn't caught up to an `auto` flip yet) — `auto` never
        # wins on a majority or a tie, only when BOTH agree. `main` is
        # short-circuited (never even read) once `$GITHUB_SHA` alone isn't
        # `auto`, since the AND can no longer become `auto` either way —
        # so there is no "main says auto" line to see here; the decision
        # itself is the only thing worth asserting.
        work = self._repo("proposal")
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
        self._write_policy(work, "auto")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm",
                        "flip main to auto"], check=True)
        # `$GITHUB_SHA` is the trigger commit BEFORE that flip.
        github_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD~1"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": github_sha, "STATUS": "same"}, work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertFalse(any(c.startswith("api ") and "ref=main" in c for c in calls), calls)

    def test_live_main_is_resolved_to_a_sha_before_the_policy_read(self):
        # S1 (round 4 on #209): the "policy at live main" read no longer
        # asks the contents API for `?ref=main` directly — it resolves
        # `main` to an explicit sha via `git/ref/heads/main` first, then
        # reads the policy AT THAT SHA, so a run's log names exactly which
        # commit its "live main" read came from.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "main"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "a" * 40, "STATUS": "same"}, work=work)
        self.assertIn(f"live main resolved to: {main_sha}", out)
        self.assertTrue(
            any(c.startswith("api ") and "git/ref/heads/main" in c for c in calls), calls)
        self.assertTrue(
            any(c.startswith("api ") and f"ref={main_sha}" in c for c in calls), calls)
        self.assertFalse(
            any(c.startswith("api ") and "roster-policy.yml?ref=main" in c for c in calls),
            calls)

    def test_a_malformed_live_main_sha_is_never_read_and_falls_back_to_proposal(self):
        # S1 (round 4 on #209): the resolved sha must be validated as 40
        # hex before the policy is read at it. An answer that is a ref
        # NAME (or any other junk) is treated as unresolved: nothing is
        # read at it, and `auto` (which needs BOTH reads) is not reached.
        work = self._repo("auto")
        github_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        for junk in ("main", "refs/heads/main", github_sha[:39], github_sha.upper()):
            with self.subTest(junk=junk):
                out, calls, body = self._run_roster_pr_direct(
                    {"GITHUB_SHA": github_sha, "STATUS": "same",
                     "TEST_MAIN_REF_SHA": junk}, work=work)
                self.assertIn("live main resolved to: <unresolved>", out)
                self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
                self.assertFalse(
                    any(c.startswith("api ") and "roster-policy.yml?ref=" in c
                        and github_sha not in c for c in calls), calls)

    def test_a2_forged_eval_output_never_overrides_the_committed_policy(self):
        # A2 (repro, round 2): the eval job's own `ROSTER_MODE` output
        # claims `auto` (as a compromised eval job's forged
        # $GITHUB_OUTPUT would), but the committed policy at `$GITHUB_SHA`
        # says `proposal` — the committed policy decides, and no PR call
        # is ever made.
        work = self.tmp / "kill-switch"; work.mkdir()
        self._write_policy(work, "proposal")
        out, calls, body = self._run_roster_pr_direct(
            self._candidate_env(GITHUB_SHA="a" * 40, ROSTER_MODE="auto"), work=work)
        self.assertIn("roster_mode (policy at $GITHUB_SHA): proposal", out)
        self.assertIn("roster_mode (eval job's own output, informational only): auto", out)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        self.assertFalse(any(c.startswith("pr create") for c in calls), calls)


class TestB1PublishVerification(_AutoProposeStepFixture):
    """B1.2 + B1.3 (round 2, blocker): before enabling auto-merge, `roster-
    pr` independently confirms — from the API alone — that the pushed sha
    really is `roster/proposal`'s current head and really is exactly one
    commit ahead of the trusted `$GITHUB_SHA`, touching only
    `evals/roster.yml`. Each check is exercised directly against a real
    git history (never a canned stand-in), so a regression to trusting
    `$PUSHED_SHA`/`$GITHUB_SHA` at face value fails these."""

    IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    #: Should-fix 1 (round 3 on #209): the exact identity/message the
    #: `roster` job's propose step commits the proposal with — a test whose
    #: only intended fault is something OTHER than this check must commit
    #: with these too, or that check would mask the fault under test.
    BOT_IDENT = ["-c", "user.name=skills-evals real-eval bot",
                 "-c", "user.email=skills-evals@users.noreply.github.com"]
    BOT_MESSAGE = "roster: proposed model roster (run 1)"  # RUN_ID=1, see _direct's base env

    VERIFY_START = "# >>> publish-verify"
    VERIFY_END = "# <<< publish-verify"

    def _verify(self, work, publish_base, pushed_sha):
        """Round 7: `verify_publish` on its own — the proposal commit is
        now created inside `roster-pr` itself, so a hostile history can no
        longer be handed in as `$PUSHED_SHA`; the verification is run
        directly against `$publish_base` (the live-`main` parent) and
        `$pushed_sha`, through the stub `gh`'s REAL-git ref and compare
        routes. Prints `ok`, `head` or `compare`. The end-to-end
        counterparts (a retarget after publish, a tree carrying an extra
        file) are in TestRound7AppPublishesTheProposal."""
        body = self.roster_pr_run_body
        fragment = body[body.index(self.VERIFY_START):body.index(self.VERIFY_END)]
        script = ("set -uo pipefail\nexport REPO\n"
                  f"publish_base={publish_base!r}\npushed_sha={pushed_sha!r}\n"
                  + fragment
                  + '\nif r=$(verify_publish); then echo "VERIFY=ok"; else echo "VERIFY=$r"; fi\n')
        stub = self.tmp / "bin3"; stub.mkdir(exist_ok=True)
        gh = stub / "gh"; gh.write_text(self._gh_script(), encoding="utf-8"); gh.chmod(0o755)
        env = {"PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
               "REPO": "example/skills-evals", "RUN_ID": "1", "GH_TOKEN": "t",
               "GITHUB_TOKEN": "t", "TEST_GIT_DIR": str(work)}
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=60, env=env, cwd=self.tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        verdicts = [ln.split("=", 1)[1] for ln in done.stdout.splitlines()
                    if ln.startswith("VERIFY=")]
        self.assertEqual(len(verdicts), 1, done.stdout)
        return verdicts[0]

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_no_roster_proposal_branch_at_all_is_a_head_mismatch(self):
        # The simplest B1.2 failure: `roster/proposal` was never pushed (or
        # this job's stub can't see it), so `roster-pr` must not trust a
        # `$PUSHED_SHA` it has no independent confirmation of.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, "b" * 40), "head")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_an_extra_commit_makes_ahead_by_two_and_blocks_auto_merge(self):
        # A1-equivalent (round 2), with the trusted `$GITHUB_SHA` captured
        # BEFORE any tampering — exactly the guarantee real GitHub Actions
        # gives (`github.sha` is fixed at dispatch time, independent of
        # whatever the `eval` job's own checkout later does): a planted
        # extra commit riding along with the proposal makes `roster/
        # proposal` two commits ahead, and the compare check catches it.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "roster change"],
                       check=True)
        (work / "harness" / "evil.py").write_text("print('planted')\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "planted"],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_an_extra_file_in_the_same_commit_blocks_auto_merge(self):
        # ahead_by stays 1, but the commit touches a second file too —
        # still not a bare `evals/roster.yml` change, so it must not pass.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        (work / "harness" / "evil.py").write_text("print('planted')\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "roster + evil"],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_verify_failure_with_an_open_pr_says_inspect_not_merge_after_ci(self):
        # Should-fix 6 (round 3 on #209), end to end since round 7: the
        # App-published commit comes back carrying an extra file (the
        # stub's TEST_TREE_EXTRA_FILE fault), with an EARLIER run's PR
        # already open — never "merge it after CI" about content that
        # failed independent verification; name the failed check instead.
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "55"
        self._job2_env = {"TEST_TREE_EXTRA_FILE": "1"}
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        self.assertIn("inspect before merging", body)
        self.assertIn("did not verify as a single", body)
        self.assertNotIn("merge it after CI", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_main_moved_on_since_the_proposal_blocks_auto_merge(self):
        # `$GITHUB_SHA` (this run's trigger commit) is AHEAD of the commit
        # `roster/proposal` actually branched from — behind_by > 0 — so the
        # proposal is stale relative to the trusted commit and must not
        # merge as if it were current.
        work = self._repo("auto")
        base_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "roster change"],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
        (work / "README-extra.md").write_text("later main commit\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "main moved on"],
                       check=True)
        later_main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                        capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, later_main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_a_clean_single_commit_change_verifies_and_enables_auto_merge(self):
        # Negative control: the ordinary, honest case (equivalent to
        # `TestRosterModeAutoDiffers`'s own end-to-end test, but built
        # directly here) must still pass B1's checks and enable auto-merge
        # — proving the verification is not simply always-false.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "ok")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_a_wrong_commit_message_blocks_auto_merge(self):
        # Should-fix 1 (round 3 on #209): everything else about the commit
        # is honest (single commit, right file, right identity) except its
        # own message — a hostile commit-msg hook inside the `eval` job's
        # untrusted workspace, say, planting a closing keyword.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm",
                        self.BOT_MESSAGE + "\n\nFixes #126"], check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_a_wrong_commit_author_blocks_auto_merge(self):
        # Should-fix 1: right file, right message, wrong author identity.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    # ---- single-fault compare tests (round 3, N1/N3/N4/N11/N12) ---------
    # Each of the four rows above stacks two or more violations at once
    # (an extra commit is both `ahead_by != 1` AND touches a second file),
    # so dropping any ONE of the compare's five clauses still failed
    # against those cases (another clause caught it) — these isolate each
    # clause with a history that violates exactly it.

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_two_commits_each_touching_only_roster_yml_blocks_auto_merge(self):
        # N1/N11: `ahead_by == 1` dropped, or loosened to `>= 1`. Two
        # separate commits, each touching only `evals/roster.yml`, verify
        # as a single-file `modified` change (the diff is against the
        # trees, not per-commit) but `ahead_by` is 2 — must still block.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        for suffix in ("# one\n", "# two\n"):
            (work / "evals" / "roster.yml").write_text(
                (work / "evals" / "roster.yml").read_text() + suffix)
            subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "roster change"],
                           check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_a_commit_touching_only_harness_blocks_auto_merge(self):
        # N3: the filename check dropped to `True`. `ahead_by == 1`,
        # `behind_by == 0`, but the one changed file is NOT
        # `evals/roster.yml` at all.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "harness" / "roster.py").write_text(
            (work / "harness" / "roster.py").read_text() + "# changed\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_roster_yml_added_blocks_auto_merge(self):
        # N4: the `statuses == ["modified"]` check dropped. Delete the
        # committed `evals/roster.yml` on `main` first so the proposal
        # branch's commit is a pure ADD, not a modify.
        work = self._repo("auto")
        subprocess.run(["git", "-C", str(work), "rm", "-q", "evals/roster.yml"], check=True)
        subprocess.run(["git", "-C", str(work), *self.IDENT, "commit", "-qm", "drop roster.yml"],
                       check=True)
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals").mkdir(exist_ok=True)
        (work / "evals" / "roster.yml").write_text("schema: 1\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_roster_yml_deleted_blocks_auto_merge(self):
        # N4 (the deletion half): a single commit that removes
        # `evals/roster.yml` verifies as `ahead_by == 1`, one file, but
        # `status == "removed"`, never `"modified"`.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        subprocess.run(["git", "-C", str(work), "rm", "-q", "evals/roster.yml"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
    def test_roster_yml_plus_a_later_file_blocks_auto_merge(self):
        # N12: the filename check loosened to `names[:1] == [...]`, so an
        # EXTRA file sorted after `evals/roster.yml` in the name-status
        # list would slip through. `zzz-extra.txt` sorts after
        # `evals/roster.yml` in `git diff --name-status`'s alphabetical
        # order.
        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "roster/proposal"], check=True)
        (work / "evals" / "roster.yml").write_text(
            (work / "evals" / "roster.yml").read_text() + "# changed\n")
        (work / "zzz-extra.txt").write_text("planted\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), *self.BOT_IDENT, "commit", "-qm", self.BOT_MESSAGE],
                       check=True)
        pushed_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        # Sanity: confirm the assumed sort order before relying on it.
        namestatus = subprocess.run(
            ["git", "-C", str(work), "diff", "--name-status", main_sha, pushed_sha],
            capture_output=True, text=True, check=True).stdout
        names = [line.split("\t", 1)[1] for line in namestatus.strip().splitlines()]
        self.assertEqual(names, ["evals/roster.yml", "zzz-extra.txt"], namestatus)
        self.assertEqual(self._verify(work, main_sha, pushed_sha), "compare")


class TestShouldFix1NeutralizeClosingKeywordsAndMentions(_AutoProposeStepFixture):
    """Should-fix 1 (round 3 on #209): a closing keyword or `@mention`
    inside `harness/roster.py`'s rendered `summary.md` (shaped in part by
    the untrusted census) must never survive verbatim into a PR or issue
    body — neutralised once, at the source, so every downstream read is
    already safe."""

    def _run_step_with_summary(self, latest, issues, cwd, summary):
        runner = self.tmp / "runner"
        (runner / "roster").mkdir(parents=True)
        (runner / "roster-inputs").mkdir()
        (runner / "roster" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        (runner / "roster-inputs" / "summary.md").write_text(summary, encoding="utf-8")
        (self.tmp / "issues.json").write_text(json.dumps([issues]), encoding="utf-8")
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"schedule": "0 7 * * 1"}), encoding="utf-8")
        env = {"RUNNER_TEMP": str(runner), "GITHUB_EVENT_PATH": str(event),
               "REPO": "example/skills-evals", "RUN_ID": "1",
               "SERVER_URL": "https://github.example.com",
               "GITHUB_TOKEN": "t", "GH_TOKEN": "t"}
        return self._run_two_jobs(self._gh_script(), env, cwd)

    HOSTILE_SUMMARY = "### Model roster\n\nFixes #126 cc @someone\n"

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_differs_auto_path_never_carries_a_raw_closing_keyword_or_mention(self):
        latest = self._differs()
        work = self._repo("auto")
        out, calls, body = self._run_step_with_summary(latest, [], work, self.HOSTILE_SUMMARY)
        self.assertTrue(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        self.assertNotIn("Fixes #126", body or "")
        self.assertNotIn(" @someone", body or "")
        # Substance survives — only the two trigger characters gain a
        # zero-width space immediately after them.
        self.assertIn("Fixes #​126", body or "")
        self.assertIn("@​someone", body or "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_same_path_issue_body_never_carries_a_raw_closing_keyword_or_mention(self):
        # The frozen-`same` branch embeds `summary.md` directly, inside the
        # SAME step that neutralises it — never routed through the
        # `roster_summary` output at all.
        latest = dict(self.SAME, defaults_failed={"opus": "timeout"})
        d = self.tmp / "same-hostile"; d.mkdir()
        self._write_policy(d, "auto")
        out, calls, body = self._run_step_with_summary(latest, self._tracker(), d,
                                                        self.HOSTILE_SUMMARY)
        self.assertNotIn("Fixes #126", body or "")
        self.assertNotIn(" @someone", body or "")
        self.assertIn("Fixes #​126", body or "")
        self.assertIn("@​someone", body or "")


class TestS1FailedPushDisablesOnly(_AutoProposeStepFixture):
    """S1 (round 2, repro A3): `pushed_sha` is emitted only AFTER `git push`
    succeeds. A failed push (a `pre-receive` hook denying it, as any
    branch-protection rule on a real remote might) must leave `roster-pr`
    on the disable-only path — never editing/creating a PR or dispatching
    `ci.yml` as if a proposal had actually been published."""

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_failed_push_emits_no_pushed_sha_and_only_disables(self):
        # Round 7: the "push" is the App's ref write now, inside roster-pr;
        # refused (as a ruleset or an API error would), it leaves
        # `$pushed_sha` empty and roster-pr on the disable-only path. The
        # `roster` job itself pushes nothing, so it succeeds either way.
        latest = self._differs()
        work = self._repo("auto")
        self._gh_fail = "git/refs"
        self._pr_list_value = "55"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertEqual(self.step1_returncode, 0)
        self.assertIsNone(self._rev(work, "refs/heads/roster/proposal"))
        self.assertIn("::warning::could not publish the roster proposal to roster/proposal\n",
                      out)
        self.assertFalse(any(c.startswith("pr edit") or c.startswith("pr create")
                            or c.startswith("workflow run")
                            or (c.startswith("pr merge") and "--auto" in c)
                            for c in calls), calls)
        disable = next((c for c in calls
                        if c.startswith("pr merge 55") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, calls)
        self.assertNotIn("merging automatically", body or "")
        # Should-fix 5 (round 3 on #209): PR #55 predates this run — never
        # tell a reviewer to "merge it after CI" as if this run's (never
        # pushed) content were on it.
        self.assertIn("This run's proposal was not pushed; PR #55 still "
                      "holds an earlier proposal — review before merging.",
                      body or "")
        self.assertNotIn("merge it after CI", body or "")


class TestN1HostileValidation(TestB1RosterPrHelper):
    """N1 (round 2): feeding hostile/malformed values at each of `roster-
    pr`'s own validations. Kills mutants M2 (pushed_sha), M3/M9 (status),
    M4 (rejection_reason), M5 (issue), M11 (probe_clean), M12 (rejected),
    M15 (rendered_identical), M17 (attempt ignoring pushed_sha). M2-M5/M11/
    M12/M17 are asserted directly on the relevant fragment
    (`_run_parsing_fragment`/`_run_candidate_fragment`), never only through
    the combined `attempt` outcome — B1's independent head/compare checks
    inside `verify_publish` mask several of these downstream (an empty or
    garbage value fails B1.2's head-equality check regardless of whether
    its OWN validation ran), which is what made several of them survive an
    earlier, end-to-end-only version of these tests."""

    def test_non_hex_base_sha_is_parsed_as_empty(self):
        # M2, re-aimed by round 7: the sha crossing the job boundary is now
        # BASE_SHA (what the `roster` job rendered against); a non-hex or
        # wrong-length value is normalized to "" by the parse itself.
        # PUSHED_SHA is no longer read at all: `$pushed_sha` starts empty
        # whatever the environment says, and only this job's own publish
        # sets it.
        out = self._run_parsing_fragment({"BASE_SHA": "not-a-sha"})
        self.assertIn("BASE_SHA_OUT=\n", out)
        out2 = self._run_parsing_fragment({"BASE_SHA": "a" * 39})  # one hex char short
        self.assertIn("BASE_SHA_OUT=\n", out2)
        out3 = self._run_parsing_fragment({"BASE_SHA": "a" * 40})  # the valid case
        self.assertIn(f"BASE_SHA_OUT={'a' * 40}\n", out3)
        out4 = self._run_parsing_fragment({"PUSHED_SHA": "a" * 40})
        self.assertIn("PUSHED_SHA_OUT=\n", out4)

    def test_unknown_status_is_parsed_as_empty(self):
        # M3: an unrecognised STATUS must be normalized to "" by the parse
        # itself, asserted directly (the higher-level "differs"/"same"
        # branches treat "" and any other non-differs/non-same value
        # identically, so they cannot distinguish this mutation on their
        # own — see `test_unknown_status_still_disables_an_open_pr` below
        # for the behavior that DOES differ, at the disable-only level).
        out = self._run_parsing_fragment({"STATUS": "bogus"})
        self.assertIn("STATUS_OUT=\n", out)
        for good in ("same", "differs"):
            out2 = self._run_parsing_fragment({"STATUS": good})
            self.assertIn(f"STATUS_OUT={good}\n", out2)

    def test_unknown_status_still_disables_an_open_pr(self):
        # M9: the disable-on-unknown-status call must not be dropped.
        work = self.tmp / "m9"; work.mkdir()
        self._pr_list_value = "13"
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "weird"}, work=work)
        disable = next((c for c in calls
                        if c.startswith("pr merge 13") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, calls)

    def test_a_rejection_reason_off_the_allowlist_is_parsed_as_empty(self):
        # M4: only the two exact fixed sentences the `eval` job can emit
        # ever survive the parse; anything else (a tool's own untrusted
        # stderr) is normalized to "" — asserted directly on the parse,
        # then confirmed end to end that it is never echoed into the
        # issue body either.
        out = self._run_parsing_fragment(
            {"REJECTION_REASON": "rm -rf / #pwned"})
        self.assertIn("REJECTION_REASON_OUT=\n", out)
        good = ("Rendering was rejected because the computed proposal could "
                "not be rendered safely.")
        out2 = self._run_parsing_fragment({"REJECTION_REASON": good})
        self.assertIn(f"REJECTION_REASON_OUT={good}\n", out2)

        work = self.tmp / "m4"; work.mkdir()
        out3, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "REJECTED": "true",
             "REJECTION_REASON": "rm -rf / #pwned"}, work=work)
        self.assertNotIn("rm -rf", body or "")
        self.assertNotIn("pwned", body or "")

    def test_a_non_digit_issue_number_is_parsed_as_empty(self):
        # M5: a non-digit ISSUE_NUMBER must be normalized to "" by the
        # parse itself, asserted directly, then confirmed end to end that
        # it is never interpolated into `gh issue edit <n>`.
        out = self._run_parsing_fragment({"ISSUE_NUMBER": "7; rm -rf /"})
        self.assertIn("ISSUE_OUT=\n", out)
        out2 = self._run_parsing_fragment({"ISSUE_NUMBER": "42"})
        self.assertIn("ISSUE_OUT=42\n", out2)

        work = self.tmp / "m5"; work.mkdir()
        out3, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "REJECTED": "true",
             "ISSUE_NUMBER": "7; rm -rf /",
             "REJECTION_REASON": "Rendering was rejected because the computed "
                                 "proposal could not be rendered safely."},
            work=work)
        self.assertFalse(any(c.startswith("issue edit") for c in calls), calls)
        self.assertTrue(any(c.startswith("issue create") for c in calls), calls)

    def test_empty_probe_clean_is_parsed_as_not_clean(self):
        # M11: an empty/missing PROBE_CLEAN must parse to `false`, not
        # `true` — asserted directly on the fragment's own output, then
        # confirmed end to end that it blocks the auto-merge attempt.
        out = self._run_parsing_fragment({"PROBE_CLEAN": ""})
        self.assertIn("PROBE_CLEAN_OUT=false\n", out)
        out2 = self._run_parsing_fragment({"PROBE_CLEAN": "true"})
        self.assertIn("PROBE_CLEAN_OUT=true\n", out2)

        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        out3, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": main_sha, "STATUS": "differs", "REJECTED": "false",
             "PROBE_CLEAN": "", **self._payload(work)}, work=work)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        self.assertIn("vendor-default probe was not clean", body or "")

    def test_rejected_true_never_attempts_even_if_everything_else_looks_clean(self):
        # M12: REJECTED must gate the `attempt-candidate` on its own —
        # asserted directly on that fragment (isolated from
        # `verify_publish`'s independent head/compare checks, which would
        # otherwise mask a dropped `rejected` check here — the same trap
        # M17 hit below), then confirmed end to end on a REAL verifying
        # branch that no auto-merge is attempted.
        out = self._run_candidate_fragment(self._candidate_env(REJECTED="true"))
        self.assertIn("CANDIDATE_OUT=false\n", out)
        out2 = self._run_candidate_fragment(self._candidate_env(REJECTED="false"))
        self.assertIn("CANDIDATE_OUT=true\n", out2)

        work = self._repo("auto")
        main_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        out3, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": main_sha, "STATUS": "differs", "REJECTED": "true",
             "PROBE_CLEAN": "true", **self._payload(work),
             "REJECTION_REASON": "Rendering was rejected because the computed "
                                 "proposal could not be rendered safely."},
            work=work)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        self.assertIn("no pull request was opened", body or "")

    def test_missing_pushed_sha_never_attempts(self):
        # M17: an empty PUSHED_SHA must gate the `attempt-candidate` on its
        # own — asserted directly on that fragment (B1.2's head-equality
        # check would ALSO reject an empty pushed_sha downstream, which is
        # exactly why testing only the combined `attempt` outcome against
        # a real branch cannot kill this mutant: `actual_head` is never
        # empty against a real branch, so it never equals an empty
        # `$pushed_sha` either way — this is a real "defended in depth by
        # B1" case, not a test gap, so the direct fragment assertion is
        # the only way to observe THIS check's own regression).
        out = self._run_candidate_fragment(self._candidate_env(PUSHED_SHA=""))
        self.assertIn("CANDIDATE_OUT=false\n", out)
        out2 = self._run_candidate_fragment(self._candidate_env(PUSHED_SHA="a" * 40))
        self.assertIn("CANDIDATE_OUT=true\n", out2)

    def test_rendered_identical_never_reaches_the_differs_branch(self):
        # M15: `rendered_identical` must take the EARLY-RETURN disable-only
        # path (`exit 0` right after the disable, no issue write, per F3),
        # never fall through to the "differs" section further down. A
        # disable-only `pr merge --disable-auto` call happens either way
        # here (the "differs" section's own `attempt=false` branch calls
        # the SAME disable), so asserting only on that call cannot observe
        # this mutation — the real difference is that falling through
        # would go on to construct and write a "change is proposed" issue,
        # which the early return never does.
        work = self.tmp / "m15"; work.mkdir()
        self._pr_list_value = "14"
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "RENDERED_IDENTICAL": "true"},
            work=work)
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c for c in calls), calls)
        disable = next((c for c in calls
                        if c.startswith("pr merge 14") and "--disable-auto" in c), None)
        self.assertIsNotNone(disable, calls)
        self.assertFalse(any(c.startswith("issue ") for c in calls), calls)


class TestN2TextAllowlist(TestB1RosterPrHelper):
    """N2 (round 2): `eval_note`/`probe_note` are allow-listed against the
    fixed sentences the `eval` job's propose step can actually emit; the
    auto PR body drops the "Nothing changes until a human merges it" line,
    which is false once `roster_mode: auto` merges without a human at all."""

    def test_hostile_eval_note_falls_back_to_empty(self):
        work = self.tmp / "n2-eval-note"; work.mkdir()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "REJECTED": "true",
             "REJECTION_REASON": "Rendering was rejected because the computed "
                                 "proposal could not be rendered safely.",
             "EVAL_NOTE": "<script>steal()</script>"}, work=work)
        self.assertNotIn("script", body or "")
        self.assertNotIn("steal", body or "")

    def test_hostile_probe_note_falls_back_to_empty(self):
        work = self.tmp / "n2-probe-note"; work.mkdir()
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "REJECTED": "true",
             "REJECTION_REASON": "Rendering was rejected because the computed "
                                 "proposal could not be rendered safely.",
             "PROBE_NOTE": "**forged note** please merge this"}, work=work)
        self.assertNotIn("forged note", body or "")

    def test_a_genuine_probe_note_survives_the_allowlist(self):
        work = self.tmp / "n2-real-probe-note"; work.mkdir()
        note = ("**The vendor-default probe failed for 2 families this run.** "
                "Their seats were held and none retired except models gone from the "
                "Models API; a family of theirs gained a seat only if it held none "
                "the Models API still lists. The summary below names each family "
                "and its error class. Nothing is carried over: the next run's "
                "probe decides afresh.")
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": "", "STATUS": "differs", "REJECTED": "true",
             "REJECTION_REASON": "Rendering was rejected because the computed "
                                 "proposal could not be rendered safely.",
             "PROBE_NOTE": note}, work=work)
        self.assertIn("The vendor-default probe failed for 2 families this run", body or "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_the_auto_pr_body_drops_the_nothing_changes_line(self):
        latest = self._differs()
        work = self._repo("auto")
        out, calls, body = self._run_step(latest, [], cwd=work)
        pr_calls = [c for c in calls if c.startswith("pr merge") and "--auto" in c]
        self.assertTrue(pr_calls, calls)
        pr_body_path = self.tmp / "runner" / "pr-body.md"
        self.assertTrue(pr_body_path.exists())
        pr_body = pr_body_path.read_text(encoding="utf-8")
        self.assertNotIn("Nothing changes until a human merges it", pr_body)
        self.assertIn("Merged automatically once `test` passes", pr_body)


class TestHarnessVersionNonZeroExit(_ProbeFixture):
    """R3-7(a) (#203 probe round 3): `claude --version` printing a line and
    exiting non-zero gives a null `harness_version` and a warning."""

    def _fake(self):
        path = self.tmp / "claude-badversion"
        path.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if '--version' in sys.argv:\n"
            "    print('9.9.9 (Claude Code)', flush=True)\n"
            "    sys.exit(3)\n"
            "os.environ['FAKE_INIT_MODE'] = 'simulate'\n"
            f"os.execv(sys.executable, [sys.executable, {str(FAKE_CLAUDE_INIT)!r}]"
            " + sys.argv[1:])\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_a_version_line_with_a_nonzero_exit_is_null_with_a_warning(self):
        import probe_model_defaults
        fake = self._fake()
        self.assertIsNone(probe_model_defaults.harness_version(str(fake), 30.0))
        done, doc = self._probe(fake, "--policy", str(self._policy(["opus"])))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(doc["harness_version"])
        self.assertIn("claude-opus-5-5", json.dumps(doc["defaults"]))
        self.assertIn("probe_model_defaults: warning: `claude --version` gave no usable "
                      "version line; harness_version is null", done.stderr)


# --- #203 probe round 4 -------------------------------------------------------


def _families(result):
    rungs = roster.tier_rungs(roster.load_policy(POLICY))
    return {roster.family_of(a["id"], rungs) for a in result["arms"]}


class TestMismatchFreezesInsteadOfAnEffectiveDefault(_WeeklyLoop):
    """R4-1 + R4-2 (#203 probe round 4), superseded by R7-1 (round 7): a
    CATALOGUE-MISMATCHED family FREEZES exactly like a probe failure —
    every listed previous arm holds its seat outright — rather than being
    decided on a guessed "effective default". With no previous arm listed,
    the family falls back to newest-in-tier when the enter window is not
    usable, and otherwise has no seat — as a clean run with the tier off the
    roster."""

    OPUS6 = _RosterFixture._model("claude-opus-6", "Claude Opus 6", "2026-09-27T00:00:00Z")

    def test_r4_s3_a_one_week_mismatch_matches_the_clean_run(self):
        # The reviewer's r4 s3.py: seated default opus-5-5 at ~1%, the
        # superseded opus-5 at ~41%; week 2 answers an unlisted opus-7.
        def usage(n):
            return {"claude-sonnet-5": 500, "claude-opus-5": 400,
                    "claude-opus-5-5": 10, "claude-haiku-4-5": 60}

        prev = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-5"}])

        def clean(k):
            return self._docs(k, opus="claude-opus-5-5", sonnet="claude-sonnet-5",
                              haiku="claude-haiku-4-5")

        def docs(k):
            return (self._docs(k, opus="claude-opus-7", sonnet="claude-sonnet-5",
                               haiku="claude-haiku-4-5") if k == 2 else clean(k))

        base, _ = self._loop(usage, 5, clean, previous=prev)
        out, _ = self._loop(usage, 5, docs, previous=prev)
        expected = [["claude-sonnet-5", "claude-opus-5-5"]] * 5
        self.assertEqual([self._arms(w["result"]) for w in base], expected)
        self.assertEqual([self._arms(w["result"]) for w in out], expected)
        week2 = out[2]["result"]
        self.assertEqual(week2["defaults_mismatched"]["opus"]["class"], "not-available")
        self.assertNotIn("defaults_failed", week2)
        for week in out:
            self.assertEqual(week["result"]["retired_since_last"], [])
            self.assertEqual(week["result"]["added_since_last"], [])
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(week2, "claude-opus-5-5"))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(week2, "claude-opus-5", "excluded"))

    def test_r2_loop_a_a_persistent_mismatch_holds_the_listed_seat(self):
        # Round 2's loop.py A, updated for R7-1 (#203 probe round 7): the
        # CLI answers an unlisted opus every week from week 0. opus-5 (the
        # only previous arm `PREV0` lists) is frozen and holds the seat every
        # week; opus-5-5 (~30%-used, not a previous arm) earns none while
        # opus-5 remains listed (the governing guarantee wins over "a
        # persistent mismatch still seats the model the fleet uses").
        def usage(n):
            return {"claude-sonnet-5": 900, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0, "claude-opus-5": 0,
                    "claude-opus-5-5": 400}

        out, _ = self._loop(usage, 6, lambda k: self._docs(
            k, opus="claude-opus-6", sonnet="claude-sonnet-5",
            haiku="claude-haiku-4-5"))
        weeks = [self._arms(w["result"]) for w in out]
        for k, arms in enumerate(weeks):
            self.assertIn("claude-opus-5", arms, k)
            self.assertNotIn("claude-opus-5-5", arms, k)
            self.assertEqual(out[k]["result"]["retired_since_last"], [], k)
            self.assertEqual(out[k]["result"]["added_since_last"], [], k)
        self.assertTrue(self._reason(out[0]["result"], "claude-opus-5").startswith(
            _mismatch_words("claude-opus-6", "opus", "not-available")))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(out[0]["result"], "claude-opus-5-5", "excluded"))

    def test_the_used_model_is_seated_once_no_previous_arm_of_the_family_is_listed(self):
        # Added for R5-2 (#203 probe round 5): the persistent mismatch
        # continues, but the listed arm (opus-5) then leaves the Models API
        # — the family holds no listed seat at all, so it falls back to
        # usage-qualified models and the heavily used opus-6 is seated.
        models_with_opus5 = self.BASE + [self.OPUS6]
        models_without_opus5 = [m for m in models_with_opus5
                                if m["id"] != "claude-opus-5"]

        def usage(n):
            return {"claude-sonnet-5": 900, "claude-opus-5": 0, "claude-opus-5-5": 0,
                    "claude-opus-6": 400}

        def docs(k):
            return self._docs(k, opus="claude-opus-7", sonnet="claude-sonnet-5")

        def models_for(k):
            return models_with_opus5 if k == 0 else models_without_opus5

        prev = copy.deepcopy(self.PREV0)
        out = []
        for k in range(2):
            now = self.START + timedelta(weeks=k, hours=12)
            counts: dict = {}
            for back in range(12, 0, -1):
                label = timeweeks.iso_week(now - timedelta(weeks=back))
                for model_id, n in usage(self._week_number(label)).items():
                    counts.setdefault(model_id, {})[label] = n
            census = {"generated_at": (self.START + timedelta(weeks=k, hours=6))
                      .strftime("%Y-%m-%dT%H:%M:%SZ"), "weeks": [], "counts": counts}
            result = roster.compute_roster(
                models_doc={"fetched_at": now.isoformat(), "models": models_for(k)},
                census_doc=census, policy=self._policy(), previous=copy.deepcopy(prev),
                now=now, warn=lambda _: None, defaults_doc=docs(k))
            out.append(result)
            if result["proposal"]["status"] == "differs":
                prev = yaml.safe_load(render_roster_yaml.render(result, "1", "abc"))
        self.assertIn("claude-opus-5", self._arms(out[0]))
        self.assertNotIn("claude-opus-6", self._arms(out[0]))
        self.assertIn("claude-opus-6", self._arms(out[1]))

    #: BASE plus an older sonnet no rule seats, so the judge is never an arm
    #: when the fallback seats every tier's newest.
    THIN_MODELS = _WeeklyLoop.BASE + [_RosterFixture._model(
        "claude-sonnet-4-6", "Claude Sonnet 4.6", "2025-11-24T00:00:00Z")]

    def _one(self, usage, docs, prev):
        now = self.START + timedelta(hours=12)
        counts: dict = {}
        for back in range(12, 0, -1):
            label = timeweeks.iso_week(now - timedelta(weeks=back))
            for model_id, n in usage(self._week_number(label)).items():
                counts.setdefault(model_id, {})[label] = n
        census = {"generated_at": (self.START + timedelta(hours=6))
                  .strftime("%Y-%m-%dT%H:%M:%SZ"), "weeks": [], "counts": counts}
        return roster.compute_roster(
            models_doc={"fetched_at": now.isoformat(), "models": list(self.THIN_MODELS)},
            census_doc=census, policy=self._policy(), previous=copy.deepcopy(prev),
            now=now, warn=lambda _: None, defaults_doc=docs)

    def test_r4_s2_s4_a_thin_enter_window_empties_no_family(self):
        # The reviewer's r4 s2.py / s4.py: the census is fresh, the enter
        # window is empty, the exit window is usable, and the previous
        # sonnet-5/opus-5 arms sit at 0%.
        prev = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}])
        cases = {
            "s2": (lambda n: {"claude-sonnet-5": 100 if n <= 35 else 0,
                              "claude-haiku-4-5": 0, "claude-opus-5": 0},
                   dict(opus="claude-opus-9", sonnet="claude-sonnet-5"),
                   dict(opus="claude-opus-5-5", sonnet="claude-sonnet-5")),
            "s4": (lambda n: {"claude-haiku-4-5": 100 if n <= 35 else 0,
                              "claude-sonnet-5": 0, "claude-opus-5": 0},
                   dict(haiku="claude-haiku-9", sonnet="claude-sonnet-9",
                        opus="claude-opus-9", fable="claude-fable-9"),
                   dict(haiku="claude-haiku-4-5", sonnet="claude-sonnet-5",
                        opus="claude-opus-5-5", fable="claude-fable-5-1")),
        }
        for name, (usage, bad, good) in cases.items():
            with self.subTest(case=name):
                no_probe = self._one(usage, None, prev)
                result = self._one(usage, self._docs(0, **bad), prev)
                self.assertTrue(result["defaults_mismatched"])
                self.assertIsNotNone(result["source"]["census_at"])
                self.assertLessEqual(_families(no_probe), _families(result))
                out, _ = self._loop(usage, 3, lambda k: self._docs(
                    k, **(bad if k == 0 else good)), previous=prev,
                    models=self.THIN_MODELS)
                for k in range(1, 3):
                    before = out[k - 1]["result"]
                    after = out[k]["result"]
                    added = {a["id"] for a in before["added_since_last"]}
                    retired = {r["id"] for r in before["retired_since_last"]}
                    self.assertFalse(added & {r["id"] for r in after["retired_since_last"]},
                                     (k, added))
                    self.assertFalse(retired & {a["id"] for a in after["added_since_last"]},
                                     (k, retired))

    def test_no_candidate_with_a_usable_enter_window_has_no_seat(self):
        # Nothing of the family is a listed previous arm or clears the bar:
        # the same as a clean run with the tier off the roster.
        def usage(n):
            return {"claude-sonnet-5": 900, "claude-opus-5": 0, "claude-opus-5-5": 0}

        prev = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"}])
        clean, _ = self._loop(usage, 1, lambda k: self._docs(
            k, opus="claude-opus-5-5", sonnet="claude-sonnet-5"), previous=prev)
        out, _ = self._loop(usage, 1, lambda k: self._docs(
            k, opus="claude-opus-9", sonnet="claude-sonnet-5"), previous=prev)
        result = out[0]["result"]
        self.assertEqual(self._arms(result), self._arms(clean[0]["result"]))
        self.assertNotIn("opus", _families(result))
        said = _mismatch_words("claude-opus-9", "opus", "not-available")
        self.assertIn(said, self._reason(result, "claude-opus-5-5", "excluded"))


class TestFrozenFallbackKeysOnTheEnterWindow(_WeeklyLoop):
    """R4-3 (#203 probe round 4): R3-6's fallback keys on the ENTER window,
    not on the census as a whole — a fresh census whose enter window is under
    the floors still falls back to newest-in-tier for a frozen family with
    nothing listed."""

    MODELS = [_RosterFixture._model("claude-haiku-5", "H5", "2026-09-01T00:00:00Z"),
              _RosterFixture._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
              # Not the newest opus, so never seated: the judge.
              _RosterFixture._model("claude-opus-5-9", "O59", "2026-08-01T00:00:00Z"),
              _RosterFixture._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]

    def test_a_fresh_census_with_a_thin_enter_window_falls_back(self):
        def usage(n):
            return {"claude-sonnet-6": 100 if n <= 35 else 0}

        no_probe, _ = self._loop(usage, 1, lambda k: None, models=self.MODELS)
        out, _ = self._loop(usage, 1, lambda k: _failed_doc(), models=self.MODELS)
        result = out[0]["result"]
        self.assertIsNotNone(result["source"]["census_at"])
        self.assertIn("opus", result["defaults_failed"])
        self.assertEqual(self._arms(result), self._arms(no_probe[0]["result"]))
        self.assertIn("claude-opus-6", self._arms(result))
        self.assertIn("falls back to newest per tier as with no probe",
                      self._reason(result, "claude-opus-6"))

    def test_the_docs_say_enter_window(self):
        for rel in ("README.md", "DESIGN.md", "evals/roster-policy.yml",
                    "docs/decisions/0002-roster-follows-vendor-defaults.md"):
            flat = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
            flat = re.sub(r"\s*#\s*", " ", flat)
            with self.subTest(file=rel):
                self.assertIn("no usable enter window", flat)
                self.assertNotIn("no usable census, as the newest", flat)
                self.assertNotIn("With no usable census and no seat", flat)


class TestFrozenFallbackMutants(_RosterFixture):
    """R4-4 (#203 probe round 4): the three mutants round 4 left alive."""

    NOW5 = TestFrozenFamilyFallsBackWithNoCensus.NOW5
    PREV = TestFrozenFamilyFallsBackWithNoCensus.PREV

    def _run(self, models, defaults, policy=None):
        return roster.compute_roster(
            models_doc={"fetched_at": self.NOW5.isoformat(), "models": models},
            census_doc=None, policy=policy or self._policy(),
            previous=copy.deepcopy(self.PREV), now=self.NOW5, warn=lambda _: None,
            defaults_doc=defaults)

    def test_a_only_the_newest_of_a_frozen_family_is_seated(self):
        models = [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-5-9", "O59", "2026-08-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-09-01T00:00:00Z")]
        no_probe = self._run(models, None)
        failed = self._run(models, _failed_doc())
        self.assertEqual(self._arms(failed), self._arms(no_probe))
        self.assertIn("claude-opus-6", self._arms(failed))
        self.assertNotIn("claude-opus-5-9", self._arms(failed))

    def test_b_a_held_snapshot_on_its_collapsed_alias_blocks_a_usage_seat(self):
        # The previous arm is the dated opus-5 snapshot; the catalogue lists
        # its undated alias too, so the family still holds a listed seat and
        # a heavily used opus-5-5 is NOT seated on the failed probe.
        models = self._models_doc(extra=[self._model(
            "claude-opus-5-20260401", "Claude Opus 5", "2026-04-01T00:00:00Z")])
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5-20260401"}]}
        census = self._census(extra={"claude-opus-5-5": {w: 452 for w in self.ENTER}})
        doc = {**self.DEFAULTS, "defaults": {"sonnet": "claude-sonnet-5"},
               "errors": {"opus": "timeout"}}
        result, _ = self._compute(defaults=doc, previous=previous, models=models,
                                  census=census)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertNotIn("claude-opus-5-5", self._arms(result))
        self.assertIn("while the family holds a seat the Models API still lists",
                      self._reason(result, "claude-opus-5-5", "excluded"))

    def test_c_a_positive_cooling_off_is_honoured_as_with_no_probe(self):
        policy = self._policy(cooling_off_days=7)
        models = [self._model("claude-sonnet-6", "S6", "2026-09-01T00:00:00Z"),
                  self._model("claude-opus-5-9", "O59", "2026-08-01T00:00:00Z"),
                  self._model("claude-opus-6", "O6", "2026-10-03T00:00:00Z")]
        no_probe = self._run(models, None, policy)
        failed = self._run(models, _failed_doc(), policy)
        self.assertEqual(self._arms(failed), self._arms(no_probe))
        self.assertNotIn("claude-opus-6", self._arms(failed))


class TestB1Round4Disarm(unittest.TestCase):
    """B1 (round 4 on #209, blocker), item (f): the `disarm` job's own
    step, run in isolation with a stub `gh` — an open pull request from
    `roster/proposal` gets `pr merge <n> --disable-auto`; none makes no
    merge call at all; and a failed lookup is a fixed `::warning::`,
    exit 0, never a failed job. Hermetic: stub `gh` only, never the real
    GitHub API."""

    def setUp(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.script = doc["jobs"]["disarm"]["steps"][0]["run"]
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, pr_number, fail_lookup=False, fail_merge=False):
        stub = self.tmp / "bin"
        stub.mkdir(exist_ok=True)
        log = self.tmp / "gh.log"
        log.unlink(missing_ok=True)
        gh = stub / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {str(log)!r}\n"
            'if [ "$1" = api ]; then\n'
            f'  {"exit 1" if fail_lookup else ""}\n'
            f'  printf %s {pr_number!r}\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1 $2" = "pr merge" ]; then\n'
            f'  {"exit 1" if fail_merge else ""}\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8")
        gh.chmod(0o755)
        env = {"PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
               "REPO": "example/skills-evals", "GITHUB_TOKEN": "t", "GH_TOKEN": "t"}
        done = subprocess.run(["bash", "-c", self.script], capture_output=True,
                              text=True, timeout=30, env=env)
        log_lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return done, log_lines

    def test_an_open_pr_is_disarmed(self):
        done, log = self._run("42")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(any(ln.startswith("pr merge 42 --repo example/skills-evals --disable-auto")
                             for ln in log), log)

    def test_no_open_pr_makes_no_merge_call(self):
        done, log = self._run("")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(any(ln.split(" ")[:2] == ["pr", "merge"] for ln in log), log)

    def test_lookup_failure_warns_and_exits_zero(self):
        done, _log = self._run("", fail_lookup=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("::warning::could not find the roster pull request to disarm",
                      done.stdout)

    def test_disable_failure_warns_and_exits_zero(self):
        done, log = self._run("42", fail_merge=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(any(ln.startswith("pr merge 42") for ln in log), log)
        self.assertIn(
            "::warning::could not disable auto-merge for the roster pull "
            "request before the agent ran", done.stdout)


class TestB1Round4DisarmLookupFilters(unittest.TestCase):
    """B1 (round 4 on #209), item 3: `disarm` finds the PR with "the same
    validated lookup roster-pr uses" — so it gets the same fork (F1) and
    other-branch (N15) rows `TestAdversarialRound1F1NeverMatchesAForkPr`
    feeds `roster-pr`, through the REAL `--jq` expression the step sends,
    piped through actual `jq` with `REPO` exported exactly as the step's
    `env:` provides it. The base class's stub ignores `--jq`, so dropping
    either client-side re-check survived it."""

    FOREIGN = {"number": 101, "head": {"repo": {"full_name": "attacker/skills-evals"},
                                       "ref": "roster/proposal"}}
    OWN_55 = {"number": 55, "head": {"repo": {"full_name": "example/skills-evals"},
                                     "ref": "roster/proposal"}}
    OWN_OTHER_BRANCH = {"number": 77, "head": {"repo": {"full_name": "example/skills-evals"},
                                               "ref": "some-other-branch"}}

    def setUp(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.script = doc["jobs"]["disarm"]["steps"][0]["run"]
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run_rows(self, rows):
        stub = self.tmp / "bin-rows"
        stub.mkdir(exist_ok=True)
        log = self.tmp / "gh-rows.log"
        log.unlink(missing_ok=True)
        data = self.tmp / "rows.json"
        data.write_text(json.dumps(rows), encoding="utf-8")
        gh = stub / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {str(log)!r}\n"
            'if [ "$1" = api ]; then\n'
            '  expr=""\n'
            '  while [ $# -gt 0 ]; do\n'
            '    if [ "$1" = --jq ]; then expr="$2"; shift; fi\n'
            '    shift\n'
            '  done\n'
            f'  exec jq -r "$expr" {str(data)!r}\n'
            "fi\n"
            "exit 0\n",
            encoding="utf-8")
        gh.chmod(0o755)
        env = {"PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
               "REPO": "example/skills-evals", "GITHUB_TOKEN": "t", "GH_TOKEN": "t"}
        done = subprocess.run(["bash", "-c", self.script], capture_output=True,
                              text=True, timeout=30, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return lines

    def _merges(self, lines):
        return [ln for ln in lines if ln.split(" ")[:2] == ["pr", "merge"]]

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_the_lookup_is_owner_filtered_server_side(self):
        lines = self._run_rows([])
        api = [ln for ln in lines if ln.startswith("api ")]
        self.assertEqual(len(api), 1, lines)
        self.assertIn("head=example:roster/proposal", api[0])
        self.assertIn("state=open", api[0])

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_fork_row_is_never_disarmed(self):
        self.assertEqual(self._merges(self._run_rows([self.FOREIGN])), [])

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_a_same_repo_row_from_another_branch_is_never_disarmed(self):
        self.assertEqual(self._merges(self._run_rows([self.OWN_OTHER_BRANCH])), [])

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_the_own_row_is_disarmed_past_a_newer_fork_and_other_branch_row(self):
        merges = self._merges(self._run_rows(
            [self.FOREIGN, self.OWN_OTHER_BRANCH, self.OWN_55]))
        self.assertEqual(merges, ["pr merge 55 --repo example/skills-evals --disable-auto"])


class TestB1Round4JobGraph(unittest.TestCase):
    """B1 (round 4 on #209): the job graph's gates, asserted on the PARSED
    workflow. `publish` holds `contents: write` and runs after a successful
    plan and a completed eval matrix, including a failed sibling; each
    publisher requires its own success payload. `disarm` runs only for
    `main` and only after `roster`; `eval` waits for `disarm` but is never
    blocked by it failing or being skipped."""

    def setUp(self):
        self.jobs = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    @staticmethod
    def _needs(job):
        needs = job.get("needs") or []
        return sorted([needs] if isinstance(needs, str) else needs)

    def test_publish_runs_after_a_completed_eval_matrix_and_a_successful_plan(self):
        publish = self.jobs["publish"]
        # ADR 0004 review, S1: `publish` (contents: write) is gated on the
        # same `roster-wait` confirmation as `eval`, so it needs that job.
        # ADR 0008 also requires a successful plan, and permits publication
        # of successful fixtures when a sibling evaluation fails.
        self.assertEqual(self._needs(publish), ["eval", "plan", "roster", "roster-wait"])
        self.assertEqual(publish.get("if"), "${{ !cancelled() && needs.plan.result == 'success' && (needs.eval.result == 'success' || needs.eval.result == 'failure' || needs.eval.result == 'cancelled') && (needs.roster-wait.result == 'skipped' || needs.roster-wait.outputs.cleared == 'true') }}")

    def test_roster_pr_runs_after_disarm_and_a_failed_one_never_blocks_it(self):
        # Round 5 had roster-pr wait for `publish` (`contents: write`),
        # since `--match-head-commit` is checked only at enable time. ADR
        # 0004 arms BEFORE the eval instead, and `roster-wait` resolves the
        # arming before `eval` and `publish` start. It needs `disarm` so
        # that job cannot turn off this job's own arming. The `if:` stays
        # `!cancelled()`-shaped so a failed `disarm` never blocks it.
        rp = self.jobs["roster-pr"]
        self.assertEqual(self._needs(rp), ["disarm", "roster"])
        self.assertEqual(rp.get("if"),
                         "${{ !cancelled() && github.ref == 'refs/heads/main' }}")

    def test_disarm_runs_on_main_only_after_roster(self):
        disarm = self.jobs["disarm"]
        self.assertEqual(self._needs(disarm), ["roster"])
        self.assertEqual(disarm.get("if"),
                         "${{ !cancelled() && github.ref == 'refs/heads/main' }}")

    def test_eval_waits_for_disarm_but_a_skipped_or_failed_one_never_blocks_it(self):
        ev = self.jobs["eval"]
        # ADR 0004: and for `roster-pr` (never blocking) and `roster-wait`,
        # which since the review's S1 DOES block unless it confirmed the
        # arming cleared or was skipped — the gate logic itself is
        # evaluated in test_issue_eval_on_merged_roster.TestTheGates.
        self.assertEqual(self._needs(ev), ["disarm", "plan", "roster", "roster-pr", "roster-wait"])
        self.assertEqual(ev.get("if"), "${{ !cancelled() && needs.plan.result == 'success' && !inputs.roster_only && (needs.roster-wait.result == 'skipped' || needs.roster-wait.outputs.cleared == 'true') }}")


class TestB1VerifyComparePredicate(unittest.TestCase):
    """The compare predicate inside `roster-pr`'s `verify_publish`, run
    directly on hand-built compare JSON. The git-history tests in
    `TestB1PublishVerification` build that JSON from a REAL history, where
    `ahead_by`, `len(commits)`, `behind_by` and `status` always agree —
    so any one of those four clauses could be dropped while the others
    still caught every history (round-3 mutants N1, N2, N5, N10, N11
    survived round 4's sweep that way). Each case here violates exactly one
    clause, so every clause is load-bearing on its own."""

    GOOD = {"status": "ahead", "ahead_by": 1, "behind_by": 0,
            "files": [{"filename": "evals/roster.yml", "status": "modified"}],
            "commits": [{"commit": {"message": "roster: proposed model roster (run 7)",
                                    "author": {"email": "skills-evals@users.noreply.github.com"}}}]}

    @classmethod
    def setUpClass(cls):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        body = _manage_step(doc)["run"]
        block = body.split("# >>> publish-verify", 1)[1].split("# <<< publish-verify", 1)[0]
        start = block.index("python3 -c '") + len("python3 -c '")
        end = block.index("' 2>/dev/null) || ok=\"false\"", start)
        cls.program = block[start:end]

    def _verdict(self, compare):
        # In-process, never a spawned interpreter: the program only reads
        # stdin, `RUN_ID` from the environment, and prints one word.
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(compare))), \
                mock.patch.dict(os.environ, {"RUN_ID": "7"}), \
                contextlib.redirect_stdout(out):
            exec(compile(self.program, "<roster-pr verify_publish>", "exec"),
                 {"__name__": "__verify__"})
        return out.getvalue().strip()

    def _with(self, **changes):
        d = copy.deepcopy(self.GOOD)
        d.update(changes)
        return d

    def test_the_honest_compare_verifies(self):
        self.assertEqual(self._verdict(self.GOOD), "true")

    def test_ahead_by_two_with_one_listed_commit_fails(self):
        # N1 (clause dropped) and N11 (loosened to >= 1).
        self.assertEqual(self._verdict(self._with(ahead_by=2)), "false")

    def test_ahead_by_zero_fails(self):
        self.assertEqual(self._verdict(self._with(ahead_by=0)), "false")

    def test_behind_by_one_fails(self):
        # N2: `behind_by == 0` dropped.
        self.assertEqual(self._verdict(self._with(behind_by=1)), "false")

    def test_a_non_ahead_status_fails(self):
        # N5: `status == "ahead"` dropped.
        for status in ("diverged", "identical", "behind", None):
            with self.subTest(status=status):
                self.assertEqual(self._verdict(self._with(status=status)), "false")

    def test_two_listed_commits_with_ahead_by_one_fails(self):
        # N10: `len(commits) == 1` loosened to `>= 1` (the second commit
        # is itself a perfectly formed bot commit).
        commits = self.GOOD["commits"] * 2
        self.assertEqual(self._verdict(self._with(commits=commits)), "false")

    def test_malformed_json_fails(self):
        for compare in ([], "x", None, {}):
            with self.subTest(compare=compare):
                self.assertEqual(self._verdict(compare), "false")


class TestN4RosterLatestJsonNewlineRoundTrip(unittest.TestCase):
    """N4 (round 4 on #209): `harness/roster.py` always writes
    `roster/latest.json` with a trailing newline. The `roster` job's
    `emit_ml` (its `content="$(cat)"` strips ANY stdin's trailing newline)
    and GitHub's own `$GITHUB_OUTPUT` multiline format (the newline before
    the closing delimiter is a separator, never part of the value) both
    drop it on the way through `needs.roster.outputs.roster_latest_json` —
    so the `publish` job must put it back explicitly, rather than silently
    publishing a `roster/latest.json` byte-different from what was
    computed."""

    def setUp(self):
        self.doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _emit_ml_fragment(self):
        propose = next(s["run"] for s in self.doc["jobs"]["roster"]["steps"]
                       if s.get("name") == "Propose a roster change")
        start = propose.index("emit_ml() {")
        # The function body: find its closing brace at column 0 (`  }` at
        # the same indent as the opening `emit_ml() {`), never a naive
        # first-`}` scan — the body itself contains a `{` in `printf`.
        end = propose.index("\n}\n", start) + len("\n}\n")
        return propose[start:end]

    def _publish_fragment(self):
        publish = next(s["run"] for s in self.doc["jobs"]["publish"]["steps"]
                       if s.get("name") == "Build the badge over the run window, "
                                           "commit, and push")
        start = publish.index('if [ -n "${ROSTER_LATEST_JSON:-}" ]; then')
        end = publish.index("fi\n", start) + len("fi\n")
        return publish[start:end]

    def test_trailing_newline_survives_the_round_trip(self):
        computed = self.tmp / "computed.json"
        computed.write_text('{"schema": 1}\n', encoding="utf-8")

        github_output = self.tmp / "github-output"
        github_output.write_text("", encoding="utf-8")
        emit_script = (self._emit_ml_fragment()
                      + '\nemit_ml roster_latest_json < ' + str(computed) + '\n')
        done = subprocess.run(["bash", "-c", emit_script], capture_output=True,
                              text=True, timeout=30,
                              env={"PATH": os.environ.get("PATH", ""),
                                   "GITHUB_OUTPUT": str(github_output)})
        self.assertEqual(done.returncode, 0, done.stderr)
        outputs = _ProposeStepFixture._parse_github_output(github_output)
        roster_latest_json = outputs["roster_latest_json"]
        # Confirms the loss this test guards against actually happens —
        # if this assertion itself ever fails, the round-trip fix below is
        # masking nothing and the test would pass for the wrong reason.
        self.assertFalse(roster_latest_json.endswith("\n"))

        work = self.tmp / "work"
        work.mkdir()
        publish_script = self._publish_fragment()
        done2 = subprocess.run(["bash", "-c", publish_script], capture_output=True,
                               text=True, timeout=30, cwd=work,
                               env={"PATH": os.environ.get("PATH", ""),
                                    "ROSTER_LATEST_JSON": roster_latest_json,
                                    "GITHUB_STEP_SUMMARY": str(self.tmp / "summary")})
        self.assertEqual(done2.returncode, 0, done2.stderr)
        published = (work / "roster" / "latest.json").read_bytes()
        self.assertEqual(published, computed.read_bytes(),
                         "roster/latest.json published by `publish` must be "
                         "byte-identical to what `roster.py` computed, "
                         "trailing newline included")


class TestRound4Docs(unittest.TestCase):
    """R4-5 (#203 probe round 4): docs accuracy."""

    ADR = REPO_ROOT / "docs" / "decisions" / "0002-roster-follows-vendor-defaults.md"

    def _flat(self, path):
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_a_the_frozen_zero_default_case_is_a_clean_runs_outcome(self):
        self.assertIn("the same outcome a clean run gives", self._flat(self.ADR))
        for rel in ("README.md", "DESIGN.md", "HANDOFF.md", "evals/roster-policy.yml",
                    "docs/decisions/0002-roster-follows-vendor-defaults.md",
                    "harness/roster.py", ".github/workflows/eval.yml"):
            with self.subTest(file=rel):
                self.assertNotIn("clean probe next week seats",
                                 self._flat(REPO_ROOT / rel))

    def test_b_a_freeze_usage_seat_can_outlast_the_freeze(self):
        flat = self._flat(self.ADR)
        self.assertIn("can outlast the freeze", flat)
        self.assertIn("`superseded_exit_weeks` buffer", flat)

    def test_c_the_note_never_claims_publication(self):
        # B1 (round 4 on #209) required the success sentence to name a
        # failed `publish`. ADR 0004 moved `roster-pr` before the eval, so
        # its note can claim no outcome at all: it must not say results
        # were (or will be) published, only point at the run summary for
        # whether they were.
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        roster_pr = _manage_step(doc)
        line = next(ln for ln in roster_pr["run"].splitlines()
                    if "The paid eval runs after this job" in ln)
        self.assertIn("whether its results were published", line)
        self.assertNotIn("are published", line)


# --- #203 probe round 5 -------------------------------------------------------


class TestNoCreatedAtIsAProbeFailure(_WeeklyLoop):
    """R5-1 (#203 probe round 5): `no-created-at` (the vendor default the
    CLI names is listed but has no `created_at`) is a PROBE FAILURE, not a
    catalogue mismatch — the family freezes (F1/F2, #203 probe round 1),
    exactly as a wrong-tier or wrong-family answer does (R3-2). Without the
    freeze, a one-week loss of `created_at` retired a seat a clean run would
    have kept — the reviewer's p1.py."""

    @staticmethod
    def _usage(n):
        # Fleet still mostly on opus-5 (~42%), the vendor's newer default
        # opus-5-5 already carrying a small usage share of its own.
        return {"claude-sonnet-5": 500, "claude-opus-5": 400, "claude-opus-5-5": 5}

    def _no_created_at(self, k):
        models = [dict(m) for m in self.BASE]
        for m in models:
            if m["id"] == "claude-opus-5-5":
                m["created_at"] = None
        return models

    def test_p1_a_one_week_loss_of_created_at_changes_no_opus_seat(self):
        clean = self._docs
        base, _ = self._loop(self._usage, 4, clean)

        def docs_and_models(k):
            return clean(k)

        # Week 1's Models API drops opus-5-5's created_at; every other week
        # is clean.
        def loop_with_nca():
            prev = copy.deepcopy(self.PREV0)
            out = []
            for k in range(4):
                now = self.START + timedelta(weeks=k, hours=12)
                counts: dict = {}
                for back in range(12, 0, -1):
                    label = timeweeks.iso_week(now - timedelta(weeks=back))
                    for model_id, n in self._usage(self._week_number(label)).items():
                        counts.setdefault(model_id, {})[label] = n
                census = {"generated_at": (self.START + timedelta(weeks=k, hours=6))
                          .strftime("%Y-%m-%dT%H:%M:%SZ"), "weeks": [], "counts": counts}
                models = self._no_created_at(k) if k == 1 else list(self.BASE)
                warnings: list[str] = []
                result = roster.compute_roster(
                    models_doc={"fetched_at": now.isoformat(), "models": models},
                    census_doc=census, policy=self._policy(), previous=copy.deepcopy(prev),
                    now=now, warn=warnings.append, defaults_doc=clean(k))
                out.append({"result": result, "warnings": warnings})
                if result["proposal"]["status"] == "differs":
                    prev = yaml.safe_load(render_roster_yaml.render(result, "1", "abc"))
            return out

        out = loop_with_nca()
        self.assertEqual([sorted(self._arms(w["result"])) for w in out],
                         [sorted(self._arms(w["result"])) for w in base])
        week1 = out[1]["result"]
        self.assertEqual(week1["defaults_failed"], {"opus": "no-created-at"})
        self.assertNotIn("defaults_mismatched", week1)
        self.assertEqual(week1["retired_since_last"], [])
        self.assertEqual(week1["added_since_last"], [])
        self.assertEqual(
            self._reason(week1, "claude-opus-5-5"),
            "vendor default for `opus` unknown this run (probe: "
            "no-created-at); held; none retired on a failed probe")
        # The next week probes cleanly and decides as usual: nothing carried.
        self.assertNotIn("defaults_failed", out[2]["result"])

    def test_the_two_classes_still_match_the_defaults_error_re(self):
        for cls in (roster.UNRESOLVED_NO_CREATED_AT, roster.UNRESOLVED_WRONG_TIER):
            with self.subTest(cls=cls):
                self.assertTrue(roster.DEFAULTS_ERROR_RE.match(cls))



class TestPreviousArmHeldUnderItsDatedId(_RosterFixture):
    """R5-3 (#203 probe round 5): `_default_rung_decision` treats a model as
    a previous arm if it is literally in `previous_arms` OR it is the alias
    of a dated snapshot id in `previous_arms` — so a superseded arm
    published under its dated id still gets the `superseded_exit_weeks`
    buffer once its undated alias appears in the catalogue too, rather than
    retiring on sight — the reviewer's p3.py."""

    def test_a_superseded_arm_held_only_under_its_dated_id_keeps_its_buffer(self):
        extra = [self._model("claude-opus-5-20260401", "Claude Opus 5",
                             "2026-04-01T00:00:00Z")]
        models = self._models_doc(extra=extra)
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                          {"id": "claude-opus-5-20260401"},
                                          {"id": "claude-opus-5-5"}]}
        result, _ = self._compute(defaults=self.DEFAULTS, previous=prev, models=models)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("inside its 1-week buffer",
                      self._reason(result, "claude-opus-5"))
        # The dated arm's own retirement is the alias collapse, not a real
        # loss: opus-5 (undated) now holds the seat.
        ret = self._reason(result, "claude-opus-5-20260401", "retired_since_last")
        self.assertIn("which holds the seat", ret)

    def test_retired_since_last_claims_the_alias_holds_the_seat_only_if_it_does(self):
        # When the undated alias does NOT end up seated this run (its own
        # buffer has expired and its usage is below the exit bar, so it
        # retires too), the dated snapshot's retirement must not falsely
        # claim the alias "holds the seat".
        extra = [self._model("claude-opus-5-20260401", "Claude Opus 5",
                             "2026-04-01T00:00:00Z")]
        models = self._models_doc(extra=extra)
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                          {"id": "claude-opus-5-20260401"},
                                          {"id": "claude-opus-5-5"}]}
        census = self._later_census(opus5_w40=0, opus55_w40=0)
        result, _ = self._compute(defaults=self.DEFAULTS, previous=prev, models=models,
                                  census=census, now=self.LATER)
        self.assertNotIn("claude-opus-5", self._arms(result))
        ret = self._reason(result, "claude-opus-5-20260401", "retired_since_last")
        self.assertNotIn("which holds the seat", ret)
        self.assertIn("below the", ret)


class TestR8DatedIdNewerThanDefault(_RosterFixture):
    """R8-2 (#203 probe round 8): `_default_rung_decision`'s newer-than-
    default branch checked literal `previous_arms` rather than the dated-
    id-collapsing `held_arm_ids` every other branch in the function uses,
    so a previous arm published only under its DATED id got no exit check
    at all and was silently excluded, while the identical arm under its
    undated form was correctly held. The reviewer's e1.py."""

    def test_a_dated_previous_arm_newer_than_default_is_held_like_the_undated_form(self):
        extra = [self._model("claude-opus-6", "Claude Opus 6", "2026-09-27T00:00:00Z"),
                 self._model("claude-opus-6-20260927", "Claude Opus 6",
                            "2026-09-27T00:00:00Z")]
        models = self._models_doc(extra=extra)
        census = self._census(extra={"claude-opus-6-20260927":
                                     {w: 200 for w in self.ENTER}})
        for label, prevarm in (("dated", "claude-opus-6-20260927"),
                               ("undated", "claude-opus-6")):
            with self.subTest(label=label):
                prev = {**self.PREVIOUS,
                       "arms": self.PREVIOUS["arms"] + [{"id": prevarm}]}
                result, _ = self._compute(defaults=self.DEFAULTS, models=models,
                                          census=census, previous=prev)
                self.assertIn("claude-opus-6", self._arms(result))
                why = self._reason(result, "claude-opus-6")
                self.assertIn("newer than the vendor default `claude-opus-5-5`", why)


class TestR8SeatedInTierDatedId(_RosterFixture):
    """R8-3(a) (#203 probe round 8): `compute_roster`'s tier-on-roster check
    for a deferred default (`seated_in_tier`) compared each previous arm id
    LITERALLY against `seated_ids`, never folding it through `snapshots`
    first — so a peer previous arm seated this run under its undated alias,
    but held in the committed roster only under its dated id, made the
    tier look empty of any other seated previous arm and the default got
    no seat at all. The reviewer's e2.py (E3)."""

    def test_a_dated_previous_peer_still_seats_the_tier_default(self):
        census = self._census()
        census["counts"]["claude-opus-5"] = {w: 50 for w in self.ENTER}
        census["counts"]["claude-sonnet-5"] = {w: 850 for w in self.ENTER}
        models = self._models_doc(extra=[self._model(
            "claude-opus-5-20260401", "Claude Opus 5", "2026-04-01T00:00:00Z")])
        for label, prevarm in (("dated", "claude-opus-5-20260401"),
                               ("undated", "claude-opus-5")):
            with self.subTest(label=label):
                prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                                  {"id": prevarm}]}
                result, _ = self._compute(defaults=self.DEFAULTS, models=models,
                                          previous=prev, census=census)
                self.assertIn("claude-opus-5-5", self._arms(result))
                self.assertIn("the tier is on the roster because previous arm",
                              self._reason(result, "claude-opus-5-5"))


class TestR8GenericPathDatedId(_RosterFixture):
    """R8-3(b) (#203 probe round 8): the no-default generic path
    (`if reason is None and model_id in previous_arms: holdover(...)`)
    also checked literal `previous_arms` rather than the collapsed held
    set, so a family with no vendor default (haiku here) held a previous
    arm published under its undated id but silently dropped the identical
    arm published only under its dated id. The reviewer's e2.py (E2)."""

    def test_a_dated_previous_arm_in_a_no_default_tier_is_still_held(self):
        models = self._models_doc(extra=[self._model(
            "claude-haiku-4-5-20251001", "Claude Haiku 4.5", "2025-10-01T00:00:00Z")])
        for label, prevarm in (("dated", "claude-haiku-4-5-20251001"),
                               ("undated", "claude-haiku-4-5")):
            with self.subTest(label=label):
                prev = {**self.PREVIOUS,
                       "arms": self.PREVIOUS["arms"] + [{"id": prevarm}],
                       "preflight": {"id": "claude-haiku-4-5"}}
                result, _ = self._compute(defaults=self.DEFAULTS, models=models,
                                          previous=prev)
                self.assertIn("claude-haiku-4-5", self._arms(result))


class TestR10SeatedInTierWordingNamesTheRename(_RosterFixture):
    """N3 (adversarial round 10): when `seated_in_tier`'s previous arm is
    held under a RENAMED spelling — the previous roster's own id
    (`claude-opus-5`) is not what this run's catalogue lists; `_seated_form`
    maps it onto the dated snapshot that is (`claude-opus-5-20260401`) — the
    default's "the tier is on the roster because previous arm `X`" reason
    named the renamed form `X` a "previous arm", which the previous roster
    never called it by that spelling. The reviewer's c4.py."""

    def test_the_reason_names_both_the_seated_form_and_the_previous_arm(self):
        models = self._models_doc(
            drop=["claude-opus-5"],
            extra=[self._model("claude-opus-5-20260401", "Claude Opus 5",
                               "2026-04-01T00:00:00Z")])
        census = self._census(extra={"claude-opus-5": {w: 50 for w in self.ENTER}})
        previous = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                              {"id": "claude-opus-5"}]}
        result, _ = self._compute(defaults=self.DEFAULTS, models=models,
                                  census=census, previous=previous)
        self.assertIn("claude-opus-5-20260401", self._arms(result))
        why = self._reason(result, "claude-opus-5-5")
        self.assertIn("the tier is on the roster because", why)
        self.assertIn("`claude-opus-5-20260401` (previous arm `claude-opus-5`) "
                      "is in it and still seated", why)
        self.assertNotIn("previous arm `claude-opus-5-20260401`", why)


class TestMismatchCandidateSurvivingMutants(_RosterFixture):
    """R5-4 (#203 probe round 5): three mutants that survived round 4's
    mutation pass. Round 4's candidate-selection machinery they targeted is
    gone (R7-1, #203 probe round 7 — a mismatch freezes exactly like a
    probe failure now), but the edge cases they pin — a share exactly at
    the entry bar, a listed arm held only under its dated id, and a
    `created_at`-less model that must not raise — are still real, so the
    tests are converted to the freeze behavior rather than deleted."""

    def test_a_share_exactly_at_the_entry_bar_qualifies_with_no_listed_arm(self):
        # (a): no previous arm of the family is listed, so candidates come
        # from usage alone; a share exactly at `arm_enter_usage_pct` must
        # qualify (`>=`, not `>`).
        models = self._models_doc(drop=("claude-opus-5", "claude-opus-5-5"),
                                  extra=[self._model("claude-opus-6", "O6",
                                                     "2026-09-01T00:00:00Z")])
        weeks = ["2026-W36", "2026-W37", "2026-W38", "2026-W39"]
        # Exactly 10.0% over the 4-week enter window.
        census = {"generated_at": "2026-09-27T06:00:00Z", "weeks": [], "counts": {
            "claude-sonnet-5": {w: 900 for w in weeks},
            "claude-opus-6": {w: 100 for w in weeks}}}
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"}]}
        doc = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                             "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=doc, previous=prev, models=models,
                                  census=census)
        self.assertIn("claude-opus-6", self._arms(result))

    def test_b_the_dated_alias_of_a_listed_arm_is_a_held_candidate(self):
        # (b): the listed previous arm is published under its DATED id
        # only, and carries too little usage to qualify on its own; a
        # much newer, heavily used peer (opus-6) must still get NO seat —
        # `holds_listed_seat` sees the listed arm via the dated-alias
        # collapse (`frozen_held_ids`), not just literal ids. Without the
        # alias collapse, the dated id is not in `available` at all, the
        # family looks like it holds no seat, and opus-6 would wrongly
        # qualify by usage instead.
        extra = [self._model("claude-opus-5-20260401", "Claude Opus 5",
                             "2026-04-01T00:00:00Z"),
                 self._model("claude-opus-6", "Claude Opus 6",
                            "2026-09-20T00:00:00Z")]
        models = self._models_doc(extra=extra)
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                          {"id": "claude-opus-5-20260401"}]}
        weeks = ["2026-W36", "2026-W37", "2026-W38", "2026-W39"]
        census = {"generated_at": "2026-09-27T06:00:00Z", "weeks": [], "counts": {
            "claude-sonnet-5": {w: 500 for w in weeks},
            "claude-opus-5": {w: 5 for w in weeks},
            "claude-opus-6": {w: 400 for w in weeks}}}
        doc = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                             "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=doc, previous=prev, models=models,
                                  census=census)
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertNotIn("claude-opus-6", self._arms(result))
        self.assertIn("no new seat on a catalogue mismatch while the family "
                      "holds a seat the Models API still lists",
                      self._reason(result, "claude-opus-6", "excluded"))

    def test_c_a_mismatched_model_with_no_created_at_does_not_raise(self):
        # (c): the freeze branch holds a listed arm outright, with no need
        # to read `created_at` at all — a listed arm lacking one (`claude-
        # opus-6`) and another (`claude-opus-7`) must not raise, and both
        # are simply held.
        opus6 = self._model("claude-opus-6", "Claude Opus 6", None)
        opus7 = self._model("claude-opus-7", "Claude Opus 7", None)
        models = self._models_doc(extra=[opus6, opus7],
                                  drop=("claude-opus-5", "claude-opus-5-5"))
        prev = {**self.PREVIOUS, "arms": [{"id": "claude-sonnet-5"},
                                          {"id": "claude-opus-6"},
                                          {"id": "claude-opus-7"}]}
        doc = {**self.DEFAULTS, "defaults": {"opus": "claude-opus-9",
                                             "sonnet": "claude-sonnet-5"}}
        result, _ = self._compute(defaults=doc, previous=prev, models=models)
        self.assertIn("claude-opus-7", self._arms(result))
        self.assertIn("claude-opus-6", self._arms(result))
        # #203 round 7: the freeze holds every listed previous arm outright
        # on a catalogue mismatch — no `created_at`, no buffer, no
        # exception — exactly as it does on a failed probe.
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(result, "claude-opus-6"))
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(result, "claude-opus-7"))


class TestMismatchWeekRetiresNothing(_WeeklyLoop):
    """R6-1 (#203 probe round 6), superseded by R7-1 (round 7): a
    catalogue-mismatch week freezes its family exactly like a probe
    failure — every listed previous arm is held outright, no exit check at
    all — so a single mismatch week must not retire a listed previous arm
    even when a real exit check that week would (opus-5 here, well under
    the exit bar): the arm holds its seat every week, mismatch or not,
    identically to a run with no mismatch at all. The reviewer's
    adv203-probe-r6 f1.py, run with the `_WeeklyLoop` week-by-week loop."""

    MODELS = [
        _RosterFixture._model("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-01T00:00:00Z"),
        _RosterFixture._model("claude-sonnet-5", "Claude Sonnet 5", "2026-02-01T00:00:00Z"),
        _RosterFixture._model("claude-opus-5", "Claude Opus 5", "2026-04-01T00:00:00Z"),
        _RosterFixture._model("claude-opus-5-5", "Claude Opus 5.5", "2026-06-01T00:00:00Z"),
        _RosterFixture._model("claude-fable-5-1", "Claude Fable 5.1", "2026-09-01T00:00:00Z"),
    ]
    BAD_DEFAULT = "claude-opus-6"  # Not in MODELS: every "mm" week is a
                                    # catalogue mismatch (probe: not-available).

    @staticmethod
    def _usage(n):
        # opus-5 well under the 2% exit bar (0.5%), opus-5-5 dominant —
        # the shape from the reproduction: a real exit check on opus-5
        # would retire it, so only a mismatch's own carve-out can save it.
        return {"claude-sonnet-5": 500, "claude-haiku-4-5": 50,
                "claude-fable-5-1": 0, "claude-opus-5": 5,
                "claude-opus-5-5": 400}

    def _docs_with(self, mismatch_weeks):
        def docs_for(k):
            d = self._docs(k, opus="claude-opus-5", sonnet="claude-sonnet-5")
            if k in mismatch_weeks:
                d["defaults"]["opus"] = self.BAD_DEFAULT
            return d
        return docs_for

    def test_a_mismatch_week_changes_nothing_a_clean_week_would_not_change(self):
        previous = {**self.PREV0,
                    "arms": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"},
                            {"id": "claude-opus-5-5"}]}
        clean_out, _ = self._loop(self._usage, 4, self._docs_with(set()),
                                  previous=previous, models=self.MODELS)
        mismatch_out, _ = self._loop(self._usage, 4, self._docs_with({1}),
                                     previous=previous, models=self.MODELS)
        clean_arms = [sorted(self._arms(w["result"])) for w in clean_out]
        mismatch_arms = [sorted(self._arms(w["result"])) for w in mismatch_out]
        self.assertEqual(clean_arms, mismatch_arms)
        for week in clean_out + mismatch_out:
            self.assertEqual(week["result"].get("retired_since_last", []), [])
        # claude-opus-5 keeps its seat every week of the mismatch run too.
        for week in mismatch_out:
            self.assertIn("claude-opus-5", self._arms(week["result"]))


class TestRound5Docs(unittest.TestCase):
    """R5-5 (#203 probe round 5): docs accuracy."""

    ADR = REPO_ROOT / "docs" / "decisions" / "0002-roster-follows-vendor-defaults.md"

    def _flat(self, path):
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_no_created_at_is_listed_as_a_failure_everywhere(self):
        for rel in ("README.md", "DESIGN.md", "evals/roster-policy.yml",
                    "docs/decisions/0002-roster-follows-vendor-defaults.md",
                    "harness/roster.py"):
            flat = self._flat(REPO_ROOT / rel)
            with self.subTest(file=rel):
                self.assertNotIn(
                    "not available, an ambiguous snapshot, no created_at", flat)

    def test_the_governing_guarantee_is_stated_in_the_adr_and_class_docstring(self):
        flat = self._flat(self.ADR)
        self.assertIn("changes no seat", flat)
        roster_flat = self._flat(REPO_ROOT / "harness" / "roster.py")
        self.assertIn("changes no seat", roster_flat)


# --- #203 probe round 7 -------------------------------------------------------


class TestRound7ProbeReproductions(_WeeklyLoop):
    """R7-1 + R7-2 (#203 probe round 7): the reviewer's own reproductions,
    each RED on HEAD f7e0ca8 — the effective-default machinery that round 7
    deleted retired or excluded a listed seat a clean week keeps, through a
    different branch each time; R7-2 is a plain clean-run bug, unrelated to
    a mismatch. Verified red against f7e0ca8's `harness/roster.py` loaded as
    its own module (never checked out into this working tree) before this
    class was written; the failure each docstring names is what that run
    showed."""

    def test_g2_a_ramping_family_holds_its_seat_through_one_mismatch_week(self):
        # g2.py: opus-5 carries no usage before week 39, then a steady 0.5%
        # — never above the 2% exit bar. On f7e0ca8, the mismatch week ran
        # opus-5 straight through the EFFECTIVE default's "seated or held on
        # the tier's combined share" exit check and retired it on the spot
        # (`retired_since_last == [{'id': 'claude-opus-5', ...}]`, arms ==
        # `['claude-sonnet-5']`) — a seat the same week's CLEAN run (vendor
        # default opus-5-5, opus-5 superseded but inside its buffer) does
        # NOT retire. The freeze must retire no more than a clean week does
        # that same week: nothing.
        def usage(n):
            return {"claude-sonnet-5": 1000, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0,
                    "claude-opus-5": 5 if n >= 39 else 0,
                    "claude-opus-5-5": 0}

        def clean(k):
            return self._docs(k)

        def mismatch(k):
            d = self._docs(k)
            if k == 0:
                d["defaults"]["opus"] = "claude-opus-6"
            return d

        base, _ = self._loop(usage, 6, clean)
        out, _ = self._loop(usage, 6, mismatch)
        week0 = out[0]["result"]
        self.assertEqual(week0["retired_since_last"], [])
        self.assertIn("claude-opus-5", self._arms(week0))
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(week0, "claude-opus-5"))
        # From the week after the mismatch on, the two runs agree exactly —
        # the family was never dropped by the one mismatch week.
        self.assertEqual([self._arms(w["result"]) for w in out[1:]],
                         [self._arms(w["result"]) for w in base[1:]])

    def test_g3_a_family_held_only_under_its_dated_id_survives_a_mismatch(self):
        # g3.py: the family's only listed arm is published under its DATED
        # id, at 5.5% usage (between the 2% exit and 10% entry bars). On
        # f7e0ca8, the mismatch week retired it outright
        # (`retired_since_last == [{'id': 'claude-opus-5-20260401', ...}]`,
        # arms == `['claude-sonnet-5']`) and NEVER RECOVERED — every later
        # clean week still disagreed with the all-clean run
        # (`weeks1..3 equal: False` in the reproduction). The freeze must
        # hold it, and every week after the mismatch must match the
        # all-clean run exactly.
        M = self._model
        MODELS = list(self.BASE) + [M("claude-opus-5-20260401", "Claude Opus 5",
                                      "2026-04-01T00:00:00Z")]
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-20260401"}])

        def usage(n):
            return {"claude-sonnet-5": 1000, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0, "claude-opus-5": 55,
                    "claude-opus-5-5": 0}

        def clean(k):
            return self._docs(k)

        def mismatch(k):
            d = self._docs(k)
            if k == 0:
                d["defaults"]["opus"] = "claude-opus-6"
            return d

        base, _ = self._loop(usage, 4, clean, previous=PREV, models=MODELS)
        out, _ = self._loop(usage, 4, mismatch, previous=PREV, models=MODELS)
        week0 = out[0]["result"]
        # The dated snapshot's own retirement is the alias collapse, not a
        # real loss (its undated alias holds the seat instead); no OTHER
        # retirement happens.
        self.assertEqual([r["id"] for r in week0["retired_since_last"]],
                         ["claude-opus-5-20260401"])
        self.assertIn("which holds the seat",
                      self._reason(week0, "claude-opus-5-20260401", "retired_since_last"))
        self.assertIn("claude-opus-5", self._arms(week0))
        self.assertIn("held; none retired on a catalogue mismatch",
                      self._reason(week0, "claude-opus-5"))
        self.assertEqual([self._arms(w["result"]) for w in out[1:]],
                         [self._arms(w["result"]) for w in base[1:]])

    def test_g4_a_seated_default_held_only_under_its_dated_id_at_5_percent(self):
        # g4.py: a CLEAN run (no mismatch at all) — the vendor default names
        # the alias `claude-opus-5` directly, and the only previous arm of
        # that family is listed under its dated snapshot
        # `claude-opus-5-20260401`. `_default_rung_decision`'s
        # `model_id == default_id` branch checked `model_id in
        # previous_arms` literally, which the dated form never satisfies, so
        # on f7e0ca8 this genuine clean-run bug (R7-2) fell through to "the
        # tier is not on the roster" and excluded it even at 5% usage
        # (above the 2% exit bar) — `arms == ['claude-sonnet-5']`. Fixed by
        # reading the alias-collapsed id set the superseded-arm buffer
        # already used.
        M = self._model
        MODELS = list(self.BASE) + [M("claude-opus-5-20260401", "Claude Opus 5",
                                      "2026-04-01T00:00:00Z")]
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-20260401"}])

        def usage(n):
            return {"claude-sonnet-5": 1000, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0, "claude-opus-5": 55,
                    "claude-opus-5-5": 0}

        def clean(k):
            return self._docs(k, opus="claude-opus-5", sonnet="claude-sonnet-5")

        out, _ = self._loop(usage, 1, clean, previous=PREV, models=MODELS)
        result = out[0]["result"]
        self.assertIn("claude-opus-5", self._arms(result))

    def test_g4b_the_same_seat_retires_correctly_at_half_a_percent(self):
        # g4b.py: R7-2's fix must not paper over a genuine retirement — the
        # same dated-only previous arm, now at 0.5% (under the 2% exit bar),
        # still retires. The negative control: on f7e0ca8 the bug excluded
        # it too, but for the WRONG reason ("the tier is not on the
        # roster" unconditionally, never reaching a real exit check) —
        # this assertion alone was already green there, which is why it is
        # test_g4, not this one, that pins the bug.
        M = self._model
        MODELS = list(self.BASE) + [M("claude-opus-5-20260401", "Claude Opus 5",
                                      "2026-04-01T00:00:00Z")]
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-20260401"}])

        def usage(n):
            return {"claude-sonnet-5": 1000, "claude-haiku-4-5": 50,
                    "claude-fable-5-1": 0, "claude-opus-5": 5,
                    "claude-opus-5-5": 0}

        def clean(k):
            return self._docs(k, opus="claude-opus-5", sonnet="claude-sonnet-5")

        out, _ = self._loop(usage, 1, clean, previous=PREV, models=MODELS)
        result = out[0]["result"]
        self.assertNotIn("claude-opus-5", self._arms(result))

    def test_g5_a_mismatch_holds_both_a_dated_and_a_directly_listed_arm(self):
        # g5.py: two previous arms of the same family — one held only under
        # its dated id (`claude-opus-5-20260401`, collapsing onto
        # `claude-opus-5`), one listed directly (`claude-opus-5-5`) — both
        # under one mismatch week. On f7e0ca8 both stayed seated too (the
        # effective default's candidate set is every listed arm, held_here
        # non-empty), but through two DIFFERENT, effective-default-shaped
        # reasons ("seat decided on `claude-opus-5-5`, the newest of the
        # family's listed seats and usage-qualified models" for one, "held
        # over from the previous roster: superseded by ... none retired on
        # a catalogue mismatch" for the other) rather than the one freeze
        # both now get.
        M = self._model
        MODELS = [m for m in self.BASE if m["id"] != "claude-opus-5-5"] + [
            M("claude-opus-5-5", "Claude Opus 5.5", "2026-06-01T00:00:00Z"),
            M("claude-opus-5-20260401", "Claude Opus 5", "2026-04-01T00:00:00Z")]
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5-20260401"},
                                      {"id": "claude-opus-5-5"}])

        def usage(n):
            return {"claude-sonnet-5": 1000, "claude-haiku-4-5": 50,
                    "claude-opus-5": 5, "claude-opus-5-5": 300}

        def mismatch(k):
            d = self._docs(k)
            d["defaults"]["opus"] = "claude-opus-6"
            return d

        out, _ = self._loop(usage, 1, mismatch, previous=PREV, models=MODELS)
        result = out[0]["result"]
        self.assertIn("claude-opus-5", self._arms(result))
        self.assertIn("claude-opus-5-5", self._arms(result))
        for model_id in ("claude-opus-5", "claude-opus-5-5"):
            self.assertIn("held; none retired on a catalogue mismatch",
                          self._reason(result, model_id))


class TestR9RenamedArmFormSurvivesAFrozenFamily(_WeeklyLoop):
    """R9-1 (#203 probe round 9, adversarial round 9): `frozen_held_ids`
    (built from `previous_arms`/`snapshots` alone) recognized a previous arm
    under its DATED id only when the catalogue listed BOTH the dated id and
    its undated alias this run (the ordinary same-run collapse). If the
    Models API instead switches which SPELLING it lists between runs — the
    dated form gone, only the undated alias remains, or the mirror, the
    undated form gone with only a dated snapshot remaining — a clean run
    still recognizes the renamed arm (`_resolve_defaults`'s
    `_dated_candidates`/undated-collapse fallback), but a run whose probe
    FAILED or MISMATCHED that same week did not: it froze the family, found
    no literal match for the previous arm's old spelling in
    `frozen_held_ids`, and either retired it outright or, worse, let a
    `holds_listed_seat` check pass on an unrelated arm of the same family
    while silently dropping this one's seat. Fixed by `_seated_form`, the
    one helper both `frozen_held_ids` and `seated_in_tier` now use to map a
    previous arm onto whichever spelling THIS run's catalogue lists.

    Each direction is checked against a probe FAILURE and a catalogue
    MISMATCH — the two ways `_resolve_defaults` freezes a family — and each
    asserts the frozen week's arms and its `retired_since_last` are
    IDENTICAL to the same week's clean run: a bad probe must retire nothing
    a clean run keeps."""

    def test_r9a_a_dated_previous_arm_survives_when_only_the_alias_is_listed(self):
        # adv203-probe-r9's repro_rename.py, direction (a): the previous
        # roster names the opus seat under its DATED id
        # `claude-opus-5-5-20260926`; this run's catalogue (`BASE`) lists
        # only the undated alias `claude-opus-5-5`. A clean run resolves
        # the vendor default straight to the alias and retires the dated
        # id as a collapse, not a loss. Before the fix, a frozen week found
        # `claude-opus-5-5-20260926` in neither `previous_arms` (literal)
        # nor `snapshots` (no same-run collapse: the dated form isn't even
        # listed) and excluded the alias outright, even though the OTHER
        # previous arm `claude-opus-5` kept `holds_listed_seat` true — so
        # the alias's seat was silently dropped while the family read as
        # "held".
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5"},
                                      {"id": "claude-opus-5-5-20260926"}])

        def usage(n):
            return {"claude-sonnet-5": 900, "claude-opus-5": 100,
                    "claude-opus-5-5": 200, "claude-haiku-4-5": 0,
                    "claude-fable-5-1": 0}

        def clean(k):
            return self._docs(k, opus="claude-opus-5-5", sonnet="claude-sonnet-5")

        def probe_failed(k):
            d = self._docs(k, sonnet="claude-sonnet-5")
            d["errors"] = {"opus": "timeout"}
            return d

        def mismatch(k):
            return self._docs(k, opus="claude-opus-9", sonnet="claude-sonnet-5")

        base, _ = self._loop(usage, 1, clean, previous=PREV)
        clean_arms = self._arms(base[0]["result"])
        clean_retired = sorted(r["id"] for r in base[0]["result"]["retired_since_last"])
        self.assertIn("claude-opus-5-5", clean_arms)
        for bad, label in ((probe_failed, "probe failure"), (mismatch, "mismatch")):
            with self.subTest(bad=label):
                out, _ = self._loop(usage, 1, bad, previous=PREV)
                result = out[0]["result"]
                self.assertEqual(self._arms(result), clean_arms)
                self.assertEqual(
                    sorted(r["id"] for r in result["retired_since_last"]),
                    clean_retired)
                self.assertIn("claude-opus-5-5", self._arms(result))
                self.assertIn("held; none retired on",
                              self._reason(result, "claude-opus-5-5"))

    def test_r9b_an_undated_previous_arm_survives_when_only_a_dated_id_is_listed(self):
        # adv203-probe-r9's repro_b.py, direction (b): the previous roster
        # names the opus seat under its UNDATED alias `claude-opus-5`; this
        # run's catalogue lists only a dated snapshot of it,
        # `claude-opus-5-20260401` (the mirror of R9-1(a)). A clean run
        # resolves the vendor default to the sole dated candidate
        # (`_dated_candidates`) and retires the undated previous arm as a
        # collapse onto it. Before the fix, a frozen week found
        # `claude-opus-5` unlisted and its dated form nowhere in
        # `frozen_held_ids`, so `holds_listed_seat("opus")` read false (no
        # OTHER previous opus arm was listed either) and the family fell
        # through to seating a seat by usage or the newest-per-tier
        # fallback instead of holding the same id the clean run seats.
        M = self._model
        MODELS = [m for m in self.BASE if m["id"] != "claude-opus-5"] + [
            M("claude-opus-5-20260401", "Claude Opus 5", "2026-04-01T00:00:00Z")]
        PREV = dict(self.PREV0, arms=[{"id": "claude-sonnet-5"},
                                      {"id": "claude-opus-5"}])

        def usage(n):
            return {"claude-sonnet-5": 900, "claude-opus-5": 55,
                    "claude-opus-5-5": 0, "claude-haiku-4-5": 0,
                    "claude-fable-5-1": 0}

        def clean(k):
            return self._docs(k, opus="claude-opus-5", sonnet="claude-sonnet-5")

        def probe_failed(k):
            d = self._docs(k, sonnet="claude-sonnet-5")
            d["errors"] = {"opus": "timeout"}
            return d

        def mismatch(k):
            return self._docs(k, opus="claude-opus-9", sonnet="claude-sonnet-5")

        base, _ = self._loop(usage, 1, clean, previous=PREV, models=MODELS)
        clean_arms = self._arms(base[0]["result"])
        clean_retired = sorted(r["id"] for r in base[0]["result"]["retired_since_last"])
        self.assertIn("claude-opus-5-20260401", clean_arms)
        for bad, label in ((probe_failed, "probe failure"), (mismatch, "mismatch")):
            with self.subTest(bad=label):
                out, _ = self._loop(usage, 1, bad, previous=PREV, models=MODELS)
                result = out[0]["result"]
                self.assertEqual(self._arms(result), clean_arms)
                self.assertEqual(
                    sorted(r["id"] for r in result["retired_since_last"]),
                    clean_retired)
                self.assertIn("claude-opus-5-20260401", self._arms(result))
                self.assertIn("held; none retired on",
                              self._reason(result, "claude-opus-5-20260401"))


class TestR10RenamedSeatCarriesItsSupersededReason(_RosterFixture):
    """S2 (adversarial round 10): a previous arm listed under a spelling
    this run's catalogue no longer carries (`_seated_form` maps it to the
    id that DOES hold the seat) used to read "no longer returned by the
    Models API" whenever that seated form had itself just retired this
    run (e.g. superseded, `superseded_exit_weeks: 0`) — losing the real
    reason, which lived in `retire_notes` keyed by the seated form, not by
    the previous roster's old spelling. Fixed: when the seated form is not
    an arm this run but IS in `retire_notes`, its reason is reused and
    copied onto the previous roster's own id too."""

    def test_a_renamed_dated_previous_arm_reads_the_same_reason_as_the_control(self):
        # The previous roster lists the opus seat under a dated id this
        # run's catalogue no longer lists at all (`claude-opus-5` — the
        # undated alias — is what THIS run's catalogue has); a clean probe
        # supersedes it with `claude-opus-5-5`, and `superseded_exit_weeks`
        # is 0, so the seated form retires at once. The control keeps the
        # previous arm listed under the literal id this run's catalogue
        # still lists; both must read the identical "superseded by" reason.
        policy = self._policy(superseded_exit_weeks=0)
        renamed_previous = {**self.PREVIOUS, "arms": [
            {"id": "claude-sonnet-5"}, {"id": "claude-opus-5-20260401"}]}
        renamed, _ = self._compute(defaults=self.DEFAULTS, previous=renamed_previous,
                                   policy=policy)
        control, _ = self._compute(defaults=self.DEFAULTS, previous=self.PREVIOUS,
                                   policy=policy)
        control_reason = self._reason(control, "claude-opus-5", "retired_since_last")
        self.assertIn("superseded by `claude-opus-5-5`", control_reason)
        renamed_reason = self._reason(renamed, "claude-opus-5-20260401",
                                      "retired_since_last")
        self.assertEqual(renamed_reason, control_reason)



class TestProposalSentenceIsCoupledToRosterPrSed(_WeeklyLoop):
    """Round 5 (mutant F): `render_summary` words a "differs" proposal, and
    `roster-pr`'s `sed` rewrites that exact sentence for the AUTO pull
    request's body. Two files, one string, no shared constant — so this
    renders the real summary, pulls the real `sed` script out of the parsed
    workflow step, runs real `sed`, and requires the human-merge clause to
    be gone. Changing either side alone leaves the clause in place."""

    CLAUSE = "Nothing changes until a human merges it"

    @staticmethod
    def _sed_script():
        import shlex
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        run = _manage_step(doc)["run"]
        # The step's shell text spells `sed \` and then its double-quoted
        # script on the next line, closed by the `$( ... )` paren; shlex
        # splits that one quoted word out exactly as the shell would,
        # except that the shell also turns a double-quoted backslash-backtick into a bare
        # backtick, which POSIX shlex leaves alone (and backslash-backtick is a GNU sed
        # anchor, not a literal) — applied by hand below.
        lines = run[run.index("| sed \\"):].splitlines()
        script_line = lines[1].strip()
        assert script_line.endswith('")'), script_line
        argv = ["sed", *shlex.split(script_line[:-1])]
        argv[1] = argv[1].replace("\\`", "`")
        return argv[1]

    @unittest.skipUnless(shutil.which("sed"), "needs sed")
    def test_the_sed_rewrites_the_sentence_the_summary_actually_renders(self):
        result = self._compute(defaults=self.DEFAULTS)[0]
        result["proposal"] = {"status": "differs", "changes": []}
        summary = roster.render_summary(result)
        self.assertIn(self.CLAUSE, summary)
        out = subprocess.run(["sed", self._sed_script()], input=summary,
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn(self.CLAUSE, out)
        self.assertIn("merges automatically once `test` passes", out)


# --- Round 6: the roster PR is the App's (ADR 0003) ---------------------------


APP_TOKEN_ACTION_PREFIX = "actions/create-github-app-token@"
APP_TOKEN_STEP_ID = "roster-app-token"


class TestRosterAppTokenIsConfined(unittest.TestCase):
    """Round 6 (live finding 2026-09-29: run 36509251840 opened PR #214 with
    GITHUB_TOKEN; its `pull_request` run 36509304069 sat `action_required`,
    and the `test` it dispatched, run 36509302288, never counted for the
    PR). The roster PR is opened, reopened and armed by a dedicated GitHub
    App instead — and that App's key and token exist in the `roster-pr`
    job ONLY, never beside the npm-latest CLI or the agent. Parsed YAML
    throughout, never a text match on the workflow."""

    def setUp(self):
        self.doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        self.job = self.doc["jobs"]["roster-pr"]

    def test_the_mint_step_comes_first_pinned_bare_with_exact_inputs(self):
        # Only the two interpreter-setup steps may precede it, and each is
        # EXACTLY this dict: anything else in a pre-mint step (a `run:` that
        # edits PATH or GITHUB_ENV, an `env`, an `if`) is code that runs
        # before the key exists and shapes the steps that hold it.
        *setup, step, manage = self.job["steps"]
        # setup-python's tag is Dependabot's to move; the lock check below
        # keeps it exact.
        python = dict(setup[0])
        self.assertTrue(str(python.pop("uses", "")).startswith("actions/setup-python@"))
        self.assertEqual([python, setup[1]], [
            {"name": "Set up Python",
             "with": {"python-version": "3.12"}},
            {"name": "Install roster dependency",
             "run": "pip install pyyaml==6.0.3"},
        ])
        self.assertEqual(step.get("id"), APP_TOKEN_STEP_ID)
        self.assertTrue(str(step.get("uses")).startswith(APP_TOKEN_ACTION_PREFIX),
                        "the App token action by exact tag, locked in actions.lock")
        for pinned in (setup[0]["uses"], step["uses"]):
            self.assertIsNone(lock_problem(pinned, EVAL_WORKFLOW))
        self.assertIs(step.get("continue-on-error"), True,
                      "a missing or broken App must degrade, not fail the job")
        self.assertIsNone(step.get("run"))
        self.assertIsNone(step.get("env"))
        self.assertEqual(step.get("with"), {
            "client-id": "${{ vars.ROSTER_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.ROSTER_APP_PRIVATE_KEY }}",
            "owner": "example-adopter",
            "repositories": "skills-evals",
            "permission-contents": "write",
            "permission-pull-requests": "write",
        }, "exactly these inputs: one repository, exactly two scopes — a "
           "dropped scope makes the token inherit the installation's full "
           "set, and any other permission-* widens it")
        self.assertEqual([s.get("name") for s in self.job["steps"][2:]],
                         ["Mint the roster App token", MANAGE_STEP])

    def test_the_app_is_referenced_by_no_job_but_roster_pr(self):
        markers = ("ROSTER_APP", APP_TOKEN_STEP_ID, "create-github-app-token",
                   "secrets.", "vars.")
        for name, job in self.doc["jobs"].items():
            if name == "roster-pr":
                continue
            # The kit's eval.yml reads its four federation ids from repository
            # variables (DECISIONS, Review fixes); they are not the roster App.
            text = re.sub(r"\$\{\{ vars\.ANTHROPIC_(FEDERATION_RULE|ORGANIZATION"
                          r"|SERVICE_ACCOUNT|WORKSPACE)_ID \}\}", "", json.dumps(job))
            for marker in markers:
                with self.subTest(job=name, marker=marker):
                    self.assertNotIn(marker, text,
                                     f"`{name}` must never see the roster App's "
                                     "key, client id or token")
        rest = {k: v for k, v in self.doc.items() if k != "jobs"}
        for marker in markers:
            with self.subTest(where="workflow level", marker=marker):
                self.assertNotIn(marker, json.dumps(rest, default=str))

    def test_inside_roster_pr_only_the_mint_step_reads_the_key(self):
        mint, manage = self.job["steps"][-2:]
        self.assertNotIn("env", self.job, "no job-level env on roster-pr")
        self.assertIn("secrets.ROSTER_APP_PRIVATE_KEY", json.dumps(mint))
        self.assertNotIn("secrets.", json.dumps(manage))
        self.assertNotIn("vars.", json.dumps(manage))

    def test_the_manage_step_gets_the_token_from_the_step_output(self):
        manage = _manage_step(self.doc)
        env = manage["env"]
        self.assertEqual(env.get("ROSTER_APP_TOKEN"),
                         "${{ steps.%s.outputs.token }}" % APP_TOKEN_STEP_ID)
        # GH_TOKEN/GITHUB_TOKEN stay the job's own GITHUB_TOKEN: an empty
        # App token in GH_TOKEN would make `gh` fall back to GITHUB_TOKEN
        # silently, which is exactly what must never open or arm the PR.
        self.assertEqual(env.get("GH_TOKEN"), "${{ github.token }}")
        self.assertEqual(env.get("GITHUB_TOKEN"), "${{ github.token }}")
        refs = [k for k, v in env.items() if APP_TOKEN_STEP_ID in str(v)]
        self.assertEqual(refs, ["ROSTER_APP_TOKEN"])
        self.assertNotIn("${{", manage["run"])

    def test_no_dispatch_and_no_actions_scope_anywhere(self):
        for name, job in self.doc["jobs"].items():
            with self.subTest(job=name):
                self.assertNotIn("actions", job.get("permissions") or {})
                for step in job.get("steps", []):
                    self.assertNotIn("workflow run", step.get("run") or "")
        self.assertEqual(self.job["permissions"],
                         {"pull-requests": "write", "issues": "write",
                          "contents": "read"})

    def test_ci_runs_test_on_a_reopened_pull_request(self):
        # The close/reopen cycle only works if ci.yml's `pull_request`
        # trigger keeps GitHub's default activity types (opened,
        # synchronize, reopened): a `types:` list without `reopened` would
        # leave the reopened roster PR with no `test` at all.
        ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml")
                            .read_text(encoding="utf-8"))
        triggers = ci.get("on", ci.get(True))
        pr = triggers["pull_request"]
        types = (pr or {}).get("types")
        self.assertTrue(types is None or "reopened" in types, types)


class TestRosterPrHandsTheArmingToTheWait(_AutoProposeStepFixture):
    """ADR 0004: `roster-pr` tells `roster-wait` what it armed — `armed`,
    `pr_number`, `pushed_sha` on its `$GITHUB_OUTPUT` — and ONLY when
    auto-merge really was enabled on this run's own published commit. Every
    other path leaves them unset, which `roster-wait` reads as "nothing
    armed" (the eval then runs on the committed roster)."""

    def _outputs(self):
        # Nothing emitted means the file was never even created.
        path = self.tmp / "github-output-2"
        return self._parse_github_output(path) if path.exists() else {}

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_an_armed_pr_is_handed_on(self):
        work = self._repo("auto")
        self._pr_create_number = "77"
        self._run_step(self._differs(), [], cwd=work)
        pushed = self._rev(work, "refs/heads/roster/proposal")
        self.assertIsNotNone(pushed)
        self.assertEqual(self._outputs(), {"armed": "true", "pr_number": "77",
                                           "pushed_sha": pushed})

    WAITS = "waits a bounded time"

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_the_wait_sentence_is_said_only_when_an_eval_follows(self):
        # Nit 2: a `roster_only` dispatch has no wait and no eval, so its
        # PR body and tracking issue must not promise either.
        for event, waits in (({"schedule": "0 7 * * 2"}, True),
                             ({"inputs": {"roster_only": True}}, False),
                             ({"inputs": {"roster_only": "true"}}, False)):
            with self.subTest(event=event):
                shutil.rmtree(self.tmp / "work", ignore_errors=True)
                shutil.rmtree(self.tmp / "work-origin.git", ignore_errors=True)
                shutil.rmtree(self.tmp / "runner", ignore_errors=True)
                self._event = event
                self._pr_create_number = "77"
                _, _, issue = self._run_step(self._differs(), [], cwd=self._repo("auto"))
                pr_body = (self.tmp / "runner" / "pr-body.md").read_text(encoding="utf-8")
                self.assertIn("merges automatically", issue)
                self.assertIs(self.WAITS in issue, waits, issue)
                self.assertIs(self.WAITS in pr_body, waits, pr_body)
                if not waits:
                    self.assertIn("roster_only", issue)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_nothing_is_handed_on_when_nothing_was_armed(self):
        cases = {"proposal mode": ("proposal", None, None),
                 "no App token": ("auto", "", None),
                 "arming failed": ("auto", None, "--match-head-commit")}
        for label, (mode, token, fail) in cases.items():
            with self.subTest(case=label):
                shutil.rmtree(self.tmp / "work", ignore_errors=True)
                shutil.rmtree(self.tmp / "work-origin.git", ignore_errors=True)
                shutil.rmtree(self.tmp / "runner", ignore_errors=True)
                (self.tmp / "github-output-2").unlink(missing_ok=True)
                work = self._repo(mode)
                self._app_token = "app-token-fake" if token is None else token
                self._gh_fail = fail
                _, calls, _ = self._run_step(self._differs(), [], cwd=work)
                if fail:
                    # The negative control: this case really reached the
                    # enable call, and only that call failing kept it quiet.
                    self.assertTrue([c for c in calls if fail in c], calls)
                out = self._outputs()
                for key in ("armed", "pr_number", "pushed_sha"):
                    self.assertNotIn(key, out)


class TestRosterPrUsesTheAppToken(_AutoProposeStepFixture):
    """The `roster-pr` step against a stub `gh` that records which
    credential each call ran with (`gh-auth.log`)."""

    APP = "app-token-fake"

    def _pushed_sha(self):
        return self._rev(self.tmp / "work", "refs/heads/roster/proposal")

    def _by(self, prefix):
        return [(tok, c) for tok, c in self._auth_calls() if c.startswith(prefix)]

    def _app_calls(self):
        return [c for tok, c in self._auth_calls() if tok == self.APP]

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_no_open_pr_is_created_and_armed_by_the_app(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_create_number = "77"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pushed = self._pushed_sha()
        creates = self._by("pr create")
        self.assertEqual(len(creates), 1, calls)
        self.assertEqual(creates[0][0], self.APP, "the App opens the PR")
        arms = [(t, c) for t, c in self._by("pr merge 77") if "--auto" in c]
        self.assertEqual(len(arms), 1, calls)
        self.assertEqual(arms[0][0], self.APP, "the App arms auto-merge")
        self.assertIn("--merge", arms[0][1])
        self.assertIn(f"--match-head-commit {pushed}", arms[0][1])
        self.assertEqual(self._by("pr close"), [])
        self.assertEqual(self._by("pr reopen"), [])
        self.assertFalse(any(c.startswith("workflow ") for c in calls), calls)
        # The App never touches the tracking issue or the reads; those
        # stay on GITHUB_TOKEN. Round 7: its only `api` calls are the
        # git-data writes that publish `roster/proposal`.
        for tok, c in self._auth_calls():
            if c.startswith("issue ") or c.startswith("api "):
                write = " -X POST " in f" {c} " or " -X PATCH " in f" {c} "
                with self.subTest(call=c):
                    self.assertEqual(tok, self.APP if write else "t")
        self.assertIn("Pull request #77", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_an_open_pr_is_closed_reopened_and_armed_by_the_app(self):
        # The `roster` job moved this PR's head with GITHUB_TOKEN, which
        # triggers no `synchronize` run; the App's close + reopen fires a
        # `reopened` run of `test` on the new head.
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "12"
        out, calls, body = self._run_step(latest, [], cwd=work)
        pushed = self._pushed_sha()
        self.assertEqual(self._by("pr create"), [])
        seq = [(t, c) for t, c in self._auth_calls()
               if c.startswith(("pr edit 12", "pr close 12", "pr reopen 12"))
               or (c.startswith("pr merge 12") and "--auto" in c
                   and "--disable-auto" not in c)]
        self.assertEqual([c.split()[1] for _, c in seq],
                         ["edit", "close", "reopen", "merge"], seq)
        for tok, c in seq:
            with self.subTest(call=c):
                self.assertEqual(tok, self.APP)
        self.assertIn(f"--match-head-commit {pushed}", seq[-1][1])
        self.assertFalse(any(c.startswith("workflow ") for c in calls), calls)
        self.assertIn("Pull request #12", body)
        self.assertIn("merging automatically", " ".join(
            c for c in calls if c.startswith("issue ")))

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_missing_app_token_opens_and_arms_nothing(self):
        for token in ("", "not a token", "tok\nen"):
            with self.subTest(token=token):
                self._app_token = token
                latest = self._differs()
                work = self._repo("auto", name=f"missing-{len(token)}")
                shutil.rmtree(self.tmp / "runner", ignore_errors=True)
                out, calls, body = self._run_step(latest, [], cwd=work)
                self.assertFalse(any(c.startswith(("pr create", "pr edit", "pr close",
                                                   "pr reopen", "workflow "))
                                     or (c.startswith("pr merge") and "--auto" in c
                                         and "--disable-auto" not in c)
                                     for c in calls), calls)
                # Round 7: no App token, nothing published — not by the
                # App, and never by GITHUB_TOKEN instead.
                self.assertEqual(self._gitdata_writes(), [])
                self.assertIsNone(self._rev(work, "refs/heads/roster/proposal"))
                self.assertIn("::warning::the roster App token was unavailable; the "
                              "roster proposal was not published, and no roster pull "
                              "request was opened or armed\n", out)
                self.assertIn("The roster App token was unavailable, so this run's "
                              "proposal could not be published to `roster/proposal` "
                              "and no pull request was opened or armed.", body or "")
                self.assertNotIn("merging automatically", " ".join(calls))
                self.assertTrue(any(c.startswith("issue ") and "a change is proposed" in c
                                    for c in calls), calls)
        self._app_token = "app-token-fake"

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_missing_app_token_with_an_open_pr_only_disables_it(self):
        self._app_token = ""
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "12"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertFalse(any(c.startswith(("pr create", "pr edit", "pr close",
                                           "pr reopen"))
                             or (c.startswith("pr merge") and "--auto" in c
                                 and "--disable-auto" not in c)
                             for c in calls), calls)
        disables = [(t, c) for t, c in self._by("pr merge 12") if "--disable-auto" in c]
        self.assertEqual(len(disables), 1, calls)
        self.assertEqual(disables[0][0], "t", "the disable stays on GITHUB_TOKEN")
        self.assertEqual(self._gitdata_writes(), [])
        self.assertIn("The roster App token was unavailable, so this run's proposal was "
                      "not published and PR #12 still holds an earlier proposal", body)
        self.assertIn("turned off", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_proposal_mode_uses_the_app_token_only_to_publish(self):
        # Round 7: `roster_mode: proposal` publishes `roster/proposal` with
        # the App too (a ruleset will refuse GITHUB_TOKEN there), but the
        # App never touches the pull request in that mode.
        latest = self._differs()
        work = self._repo("proposal")
        self._pr_list_value = "34"
        out, calls, body = self._run_step(latest, [], cwd=work)
        app = self._app_calls()
        self.assertTrue(app, "the App publishes the proposal")
        for c in app:
            with self.subTest(call=c):
                self.assertTrue(c.startswith("api repos/example/skills-evals/git/"), c)
                self.assertTrue(" -X POST " in f" {c} " or " -X PATCH " in f" {c} ", c)
        disables = [(t, c) for t, c in self._by("pr merge 34") if "--disable-auto" in c]
        self.assertEqual([t for t, _ in disables], ["t"])
        self.assertNotIn("App token", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_failed_reopen_warns_arms_nothing_and_says_so(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "12"
        self._gh_fail = "pr reopen"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertIn("::warning::could not reopen the roster pull request\n", out)
        self.assertFalse(any(c.startswith("pr merge") for c in calls), calls)
        self.assertIn("Pull request #12 was closed so `test` could run on its new "
                      "head, and could not be reopened", body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq") and shutil.which("git"),
                         "needs bash, jq and git")
    def test_a_failed_close_warns_and_disables_with_github_token(self):
        latest = self._differs()
        work = self._repo("auto")
        self._pr_list_value = "12"
        self._gh_fail = "pr close"
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertIn("::warning::could not close the roster pull request to re-run "
                      "its checks\n", out)
        self.assertEqual(self._by("pr reopen"), [])
        self.assertFalse(any(c.startswith("pr merge") and "--auto" in c
                             and "--disable-auto" not in c for c in calls), calls)
        disables = [(t, c) for t, c in self._by("pr merge 12") if "--disable-auto" in c]
        self.assertEqual([t for t, _ in disables], ["t"])
        self.assertIn("did not complete", body)


# --- Round 7: the App publishes the proposal branch (ADR 0003) ----------------


class TestRound7AppPublishesTheProposal(_AutoProposeStepFixture):
    """Round 7: the `roster` job renders and admits the proposal but no
    longer commits or pushes it (and no longer holds `contents: write`);
    `roster-pr` publishes it with the roster App's token through the
    git-data REST API, so a ruleset can later confine
    `refs/heads/roster/proposal` to that App. Every write is answered by
    REAL git (GITDATA_STUB), so the published commit is inspected as a
    commit, never as a canned answer."""

    APP = "app-token-fake"
    NEEDS = unittest.skipUnless(shutil.which("bash") and shutil.which("jq")
                                and shutil.which("git"), "needs bash, jq and git")

    def _git(self, work, *args):
        return subprocess.run(["git", "-C", str(work), *args], capture_output=True,
                              text=True, check=True).stdout.strip()

    def _commit_on_main(self, work, path, text, message):
        self._git(work, "checkout", "-q", "main")
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self._git(work, "add", "-A")
        self._git(work, "-c", "user.name=t", "-c", "user.email=t@example.com",
                  "commit", "-qm", message)
        return self._git(work, "rev-parse", "HEAD")

    def _arms(self, calls):
        return [c for c in calls if c.startswith("pr merge") and "--auto" in c
                and "--disable-auto" not in c]

    # ---- the roster job: shape --------------------------------------------

    def test_the_roster_job_holds_no_contents_write_and_hands_on_the_file(self):
        doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
        roster_job = doc["jobs"]["roster"]
        self.assertEqual(roster_job["permissions"],
                         {"contents": "read", "id-token": "write", "issues": "write"})
        outputs = roster_job["outputs"]
        self.assertNotIn("pushed_sha", outputs)
        self.assertEqual(outputs["proposed_roster_b64"],
                         "${{ steps.propose.outputs.proposed_roster_b64 }}")
        self.assertEqual(outputs["base_sha"], "${{ steps.propose.outputs.base_sha }}")
        propose = next(s for s in roster_job["steps"]
                       if s.get("name") == "Propose a roster change")
        self.assertEqual(sorted(propose["env"]), ["GH_TOKEN", "REPO", "RUN_ID"])
        manage = _manage_step(doc)
        self.assertNotIn("PUSHED_SHA", manage["env"])
        self.assertEqual(manage["env"]["PROPOSED_ROSTER_B64"],
                         "${{ needs.roster.outputs.proposed_roster_b64 }}")
        self.assertEqual(manage["env"]["BASE_SHA"], "${{ needs.roster.outputs.base_sha }}")

    # ---- the roster job: the handoff fragment -------------------------------

    HANDOFF_START = "# >>> proposal-handoff"
    HANDOFF_END = "# <<< proposal-handoff"

    def _handoff(self, proposed: bytes, committed: bytes = b"schema: 1\n"):
        """Run the `roster` job's handoff fragment alone over a git checkout
        whose committed `evals/roster.yml` is `committed`, with `proposed`
        as the rendered file. Returns (stdout, outputs, head)."""
        work = self.tmp / "handoff"
        shutil.rmtree(work, ignore_errors=True)
        (work / "evals").mkdir(parents=True)
        (work / "evals" / "roster.yml").write_bytes(committed)
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(work)],
                       check=True)
        self._git(work, "add", "-A")
        self._git(work, "-c", "user.name=t", "-c", "user.email=t@example.com",
                  "commit", "-qm", "x")
        runner = self.tmp / "handoff-runner"
        shutil.rmtree(runner, ignore_errors=True)
        runner.mkdir()
        (runner / "proposed-roster.yml").write_bytes(proposed)
        out_file = self.tmp / "handoff-output"
        out_file.write_text("", encoding="utf-8")
        fragment = self.run_body[self.run_body.index(self.HANDOFF_START):
                                 self.run_body.index(self.HANDOFF_END)]
        script = ("set -euo pipefail\n"
                  "emit() { printf '%s=%s\\n' \"$1\" \"$2\" >> \"$GITHUB_OUTPUT\"; }\n"
                  + fragment)
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=30, cwd=work,
                              env={"PATH": os.environ.get("PATH", ""),
                                   "RUNNER_TEMP": str(runner),
                                   "GITHUB_OUTPUT": str(out_file)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout, self._parse_github_output(out_file), self._git(work, "rev-parse", "HEAD")

    def test_the_handoff_is_the_exact_bytes_and_the_rendered_against_sha(self):
        import base64
        proposed = b"schema: 1\nroster:\n  arms: [\"a\"]\n"
        out, outputs, head = self._handoff(proposed)
        self.assertEqual(base64.b64decode(outputs["proposed_roster_b64"]), proposed)
        self.assertEqual(outputs["base_sha"], head)
        self.assertNotIn("rendered_identical", outputs)

    def test_a_byte_identical_render_hands_on_nothing(self):
        out, outputs, _ = self._handoff(b"schema: 1\n", committed=b"schema: 1\n")
        self.assertEqual(outputs, {"rendered_identical": "true"})

    def test_the_handoff_cap_fails_closed_to_no_proposal(self):
        import base64
        at_cap = b"a: " + b"x" * (32768 - 4) + b"\n"
        self.assertEqual(len(at_cap), 32768)
        _, outputs, _ = self._handoff(at_cap)
        self.assertEqual(base64.b64decode(outputs["proposed_roster_b64"]), at_cap)
        over = at_cap + b"#"
        out, outputs, _ = self._handoff(over)
        self.assertEqual(outputs, {})
        self.assertIn("::warning::the rendered roster is empty or larger than 32768 bytes; "
                      "the roster proposal was not handed on", out)

    # ---- roster-pr: publishing ----------------------------------------------

    @NEEDS
    def test_auto_publishes_with_the_app_then_opens_and_arms_that_commit(self):
        import base64
        latest = self._differs()
        work = self._repo("auto")
        main_before = self._git(work, "rev-parse", "main")
        self._pr_create_number = "77"
        out, calls, body = self._run_step(latest, [], cwd=work)
        proposed = base64.b64decode(self.outputs1["proposed_roster_b64"])
        self.assertEqual(self.outputs1["base_sha"], main_before)
        writes = self._gitdata_writes()
        self.assertEqual([(w["method"], w["url"].split("/", 3)[3]) for w in writes],
                         [("POST", "git/blobs"), ("POST", "git/trees"),
                          ("POST", "git/commits"), ("POST", "git/refs")])
        for w in writes:
            with self.subTest(write=w["url"]):
                self.assertEqual(w["token"], self.APP)
        self.assertEqual(writes[-1]["body"]["ref"], "refs/heads/roster/proposal")
        created = self._rev(work, "refs/heads/roster/proposal")
        self.assertEqual(writes[-1]["body"]["sha"], created)
        # One commit on live main, changing exactly evals/roster.yml to the
        # bytes the roster job rendered, with the identity verify checks.
        self.assertEqual(self._git(work, "rev-parse", created + "^"), main_before)
        self.assertEqual(self._git(work, "diff", "--name-status", main_before, created),
                         "M\tevals/roster.yml")
        shown = subprocess.run(["git", "-C", str(work), "show", created + ":evals/roster.yml"],
                               capture_output=True, check=True).stdout
        self.assertEqual(shown, proposed)
        self.assertEqual(self._git(work, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B", created),
                         "skills-evals real-eval bot <skills-evals@users.noreply.github.com>|"
                         "skills-evals real-eval bot <skills-evals@users.noreply.github.com>|"
                         "roster: proposed model roster (run 1)")
        self.assertEqual(self._git(work, "ls-tree", created, "evals/roster.yml").split()[0],
                         "100644")
        arms = self._arms(calls)
        self.assertEqual(len(arms), 1, calls)
        self.assertIn(f"--match-head-commit {created}", arms[0])
        self.assertTrue(any(tok == self.APP and c.startswith("pr create")
                            for tok, c in self._auth_calls()))

    @NEEDS
    def test_an_existing_branch_is_force_updated_not_recreated(self):
        latest = self._differs()
        work = self._repo("auto")
        stale = self._commit_on_main(work, "stale.txt", "old proposal\n", "stale")
        self._git(work, "update-ref", "refs/heads/roster/proposal", stale)
        self._git(work, "reset", "-q", "--hard", "HEAD~1")
        self._git(work, "update-ref", "refs/heads/main", "HEAD")
        out, calls, body = self._run_step(latest, [], cwd=work)
        writes = self._gitdata_writes()
        refs = [w for w in writes if "/git/refs" in w["url"]]
        self.assertEqual(len(refs), 1, writes)
        self.assertEqual(refs[0]["method"], "PATCH")
        self.assertTrue(refs[0]["url"].endswith("/git/refs/heads/roster/proposal"))
        self.assertIs(refs[0]["body"]["force"], True)
        self.assertEqual(refs[0]["token"], self.APP)
        created = self._rev(work, "refs/heads/roster/proposal")
        self.assertNotEqual(created, stale)
        self.assertEqual(refs[0]["body"]["sha"], created)
        self.assertIn(f"--match-head-commit {created}", self._arms(calls)[0])

    @NEEDS
    def test_main_changing_roster_yml_after_the_render_publishes_nothing(self):
        latest = self._differs()
        work = self._repo("auto")
        rendered_against = self._git(work, "rev-parse", "HEAD")
        roster_text = (work / "evals" / "roster.yml").read_text(encoding="utf-8")
        self._between_jobs = lambda: self._commit_on_main(
            work, "evals/roster.yml", roster_text + "# a concurrent human edit\n",
            "concurrent roster edit")
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertEqual(self._gitdata_writes(), [])
        self.assertIsNone(self._rev(work, "refs/heads/roster/proposal"))
        self.assertEqual(self._arms(calls), [])
        self.assertNotEqual(self._git(work, "rev-parse", "main"), rendered_against)
        self.assertIn("::notice::main changed evals/roster.yml after this run rendered its "
                      "proposal; it was not published\n", out)
        self.assertIn("`main` changed `evals/roster.yml` after this run rendered its "
                      "proposal, so the proposal was not published; the next run "
                      "re-proposes against the new `main`.", body)

    @NEEDS
    def test_main_moving_elsewhere_publishes_on_live_main(self):
        latest = self._differs()
        work = self._repo("auto")
        rendered_against = self._git(work, "rev-parse", "HEAD")
        moved = {}
        self._between_jobs = lambda: moved.setdefault(
            "sha", self._commit_on_main(work, "docs/unrelated.md", "later\n", "unrelated"))
        out, calls, body = self._run_step(latest, [], cwd=work)
        created = self._rev(work, "refs/heads/roster/proposal")
        self.assertIsNotNone(created)
        self.assertEqual(self._git(work, "rev-parse", created + "^"), moved["sha"])
        self.assertNotEqual(moved["sha"], rendered_against)
        self.assertEqual(self._git(work, "diff", "--name-only", moved["sha"], created),
                         "evals/roster.yml")
        self.assertIn(f"--match-head-commit {created}", self._arms(calls)[0])

    @NEEDS
    def test_proposal_mode_publishes_with_the_app_and_arms_nothing(self):
        latest = self._differs()
        work = self._repo("proposal")
        out, calls, body = self._run_step(latest, [], cwd=work)
        created = self._rev(work, "refs/heads/roster/proposal")
        self.assertIsNotNone(created)
        self.assertTrue(self._gitdata_writes())
        self.assertTrue(all(w["token"] == self.APP for w in self._gitdata_writes()))
        self.assertFalse(any(c.startswith(("pr create", "pr edit", "pr close", "pr reopen"))
                             for c in calls), calls)
        self.assertEqual(self._arms(calls), [])
        self.assertIn("Open a pull request from `roster/proposal` and merge it after CI",
                      body)

    @NEEDS
    def test_a_retarget_after_the_publish_is_caught_before_arming(self):
        latest = self._differs()
        work = self._repo("auto")
        self._job2_env = {"TEST_RETARGET_AFTER_PUBLISH": "1"}
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertEqual(self._arms(calls), [])
        self.assertFalse(any(c.startswith("pr create") for c in calls), calls)
        self.assertIn("the pushed branch's head does not match this run's proposal", body)

    @NEEDS
    def test_a_missing_token_publishes_nothing_and_inlines_the_file(self):
        import base64
        self._app_token = ""
        latest = self._differs()
        work = self._repo("auto")
        out, calls, body = self._run_step(latest, [], cwd=work)
        self.assertEqual(self._gitdata_writes(), [])
        self.assertIsNone(self._rev(work, "refs/heads/roster/proposal"))
        proposed = base64.b64decode(self.outputs1["proposed_roster_b64"]).decode()
        self.assertIn("The rendered `evals/roster.yml` this run could not publish:\n\n"
                      "```yaml\n" + proposed.rstrip("\n") + "\n```\n", body)

    def test_the_inlined_file_is_fenced_past_its_own_backticks(self):
        import base64
        work = self._repo("auto")
        self._app_token = ""
        head = self._git(work, "rev-parse", "HEAD")
        data = b"# ````` five backticks\nschema: 1\n"
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": head, "STATUS": "differs", "PROBE_CLEAN": "true",
             "BASE_SHA": head, "PROPOSED_ROSTER_B64": base64.b64encode(data).decode()},
            work)
        self.assertIn("``````yaml\n# ````` five backticks\nschema: 1\n``````\n", body)

    def _run_roster_pr_direct(self, overrides, work):
        return TestB1RosterPrHelper._run_roster_pr_direct(self, overrides, work=work)

    def test_an_invalid_payload_publishes_nothing(self):
        import base64
        work = self._repo("auto")
        head = self._git(work, "rev-parse", "HEAD")
        cases = {
            "over the cap": base64.b64encode(b"a: " + b"x" * 32766 + b"\n").decode(),
            "not base64": "not base64!",
            "a NUL": base64.b64encode(b"schema: 1\n\0\n").decode(),
            "not UTF-8": base64.b64encode(b"schema: \xff\n").decode(),
            "not a mapping": base64.b64encode(b"- a\n- b\n").decode(),
            "not YAML": base64.b64encode(b"schema: [1\n").decode(),
            "empty": "",
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                out, calls, body = self._run_roster_pr_direct(
                    {"GITHUB_SHA": head, "STATUS": "differs", "PROBE_CLEAN": "true",
                     "BASE_SHA": head, "PROPOSED_ROSTER_B64": payload}, work)
                self.assertEqual(self._gitdata_writes(), [])
                self.assertIsNone(self._rev(work, "refs/heads/roster/proposal"))
                self.assertIn("::warning::the roster proposal handed on by the roster job "
                              "did not validate; it was not published\n", out)
        at_cap = base64.b64encode(b"a: " + b"x" * 32764 + b"\n").decode()
        for label, env in (("a junk run id", {"RUN_ID": "1; x"}),
                           ("a junk base sha", {"BASE_SHA": "main"})):
            with self.subTest(case=label):
                out, calls, body = self._run_roster_pr_direct(
                    {"GITHUB_SHA": head, "STATUS": "differs", "PROBE_CLEAN": "true",
                     "BASE_SHA": head, "PROPOSED_ROSTER_B64": at_cap, **env}, work)
                self.assertEqual(self._gitdata_writes(), [])
        # The negative control: the same payload at exactly the cap does publish.
        out, calls, body = self._run_roster_pr_direct(
            {"GITHUB_SHA": head, "STATUS": "differs", "PROBE_CLEAN": "true",
             "BASE_SHA": head, "PROPOSED_ROSTER_B64": at_cap}, work)
        self.assertIsNotNone(self._rev(work, "refs/heads/roster/proposal"))
        self.assertTrue(self._arms(calls), calls)


if __name__ == "__main__":
    unittest.main()
