"""`run` and `usage` blocks on every summary (#370 item 1).

Every `summary.json` the harness writes says how the run was billed, what ran
it and where (`run`), and what it cost (`usage`): start and end times, tokens
and cost split into agent, judge and guard, and the account meter as the CLI
reported it during calls the harness made anyway. A value that could not be
read is null with a reason, never omitted and never guessed.

Expected values are literals. Hermetic: the CLI is test/fake-claude or a
mocked `subprocess.run`; no real CLI, no network, and the in-process tests
run on a stand-in clock.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
ROOT = TEST_DIR.parent
HARNESS_DIR = ROOT / "harness"
FAKE_CLAUDE = TEST_DIR / "fake-claude"
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TEST_DIR))

import cli_json  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import run_eval  # noqa: E402
from arm_test_env import install_arm_test_environment  # noqa: E402

REGISTRY_URL = "https://github.com/example-adopter/agentskills"
#: `IngestTests.edited`: remove the key instead of setting it.
DROP = object()
FAKE_VERSION = "fake-claude 0.0.0 (hermetic test stub)"
#: The timestamp test/fake-claude puts on the message before its meter events.
FAKE_MESSAGE_AT = "2026-10-10T15:10:13.663Z"
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

RUN_KEYS = {"schema_version", "billing", "runner", "location", "id",
            "session_id", "harness_commit", "cli_version", "source", "reasons"}
RUN_NULLABLE = ("billing", "runner", "location", "id", "session_id",
                "harness_commit", "cli_version")
USAGE_KEYS = {"schema_version", "started_at", "ended_at", "meter",
              "meter_delta", "concurrent_runs", "other_account_activity",
              "asserted_quiet_by", "outer_session", "limit_promotion",
              "price_table_version", "measured", "reasons"}
USAGE_NULLABLE = ("ended_at", "meter", "meter_delta", "concurrent_runs",
                  "limit_promotion", "price_table_version")
MEASURED_KEYS = {"scope", "started_at", "ended_at", "calls", "meter", "agent",
                 "judge", "guard", "reasons"}
MEASURED_NULLABLE = ("started_at", "ended_at", "calls", "meter")
ROLE_KEYS = {"calls", "duration_ms", "num_turns", "cost_usd", "cost_basis",
             "models", "cache_creation", "reasons"}
ROLE_NULLABLE = ("num_turns", "cost_usd", "cost_basis", "cache_creation",
                 "models")
#: `meter_delta.uncovered` for a run whose only calls printed its snapshots.
def uncovered(before: int, from_last: int) -> dict:
    return {"calls_before_first_snapshot": before,
            "calls_from_last_snapshot": from_last}




def meter(week: float, five: float = 0.01, week_resets: int = 1791824400) -> dict:
    """One `rate_limit_info`, in the shape CLI 2.1.296 emitted on 2026-10-10."""
    return {"status": "allowed", "resetsAt": 1791660000,
            "rateLimitType": "five_hour", "overageStatus": "rejected",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": five, "resetsAt": 1791660000},
                "seven_day": {"utilization": week, "resetsAt": week_resets}}}


def result_object(**changes) -> dict:
    """One CLI `type: result` object with per-model usage and a cache split."""
    result = {
        "type": "result", "is_error": False, "result": "done",
        "total_cost_usd": 0.25, "num_turns": 3, "duration_ms": 4000,
        "session_id": "sess-1",
        "usage": {"input_tokens": 10, "output_tokens": 20,
                  "cache_read_input_tokens": 50,
                  "cache_creation_input_tokens": 18,
                  "cache_creation": {"ephemeral_5m_input_tokens": 7,
                                     "ephemeral_1h_input_tokens": 11}},
        "modelUsage": {
            "claude-sonnet-5": {"inputTokens": 10, "outputTokens": 20,
                                "cacheReadInputTokens": 50,
                                "cacheCreationInputTokens": 18,
                                "costUSD": 0.2, "costBasis": "list"},
            "claude-haiku-5-5": {"inputTokens": 1, "outputTokens": 2,
                                 "cacheReadInputTokens": 3,
                                 "cacheCreationInputTokens": 4,
                                 "costUSD": 0.05, "costBasis": "list"}}}
    result.update(changes)
    return result


def verbose_array(result: dict, infos=(), at: str | None = FAKE_MESSAGE_AT) -> list:
    """The `--output-format json --verbose` message array around `result`."""
    message = {"type": "assistant", "message": {"content": []}}
    if at is not None:
        message["timestamp"] = at
    return [{"type": "system", "subtype": "init"}, message,
            *({"type": "rate_limit_event", "rate_limit_info": info}
              for info in infos), result]


def no_calls(role: str) -> dict:
    return {"calls": 0, "duration_ms": 0, "num_turns": 0, "cost_usd": 0,
            "cost_basis": None, "models": {},
            "cache_creation": {"ephemeral_5m_input_tokens": 0,
                               "ephemeral_1h_input_tokens": 0},
            "reasons": {"cost_basis": f"no {role} call was made"}}


def flags(**given) -> argparse.Namespace:
    return argparse.Namespace(**{"run_billing": None, "run_runner": None,
                                 "run_location": None, "run_id": None,
                                 "asserted_quiet": None, **given})


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class Checks(unittest.TestCase):
    """Shape assertions shared by the classes below."""

    def assert_reasons(self, block: dict, nullable, where: str) -> None:
        wanted = {name for name in nullable if block[name] is None}
        self.assertEqual(set(block["reasons"]), wanted, where)
        for name, why in block["reasons"].items():
            self.assertIsInstance(why, str, f"{where}.{name}")
            self.assertTrue(why.strip(), f"{where}.{name}")

    def assert_complete(self, summary: dict) -> None:
        run, usage = summary["run"], summary["usage"]
        self.assertEqual(set(run), RUN_KEYS)
        self.assertEqual(set(run["source"]), set(RUN_NULLABLE[:5]))
        self.assert_reasons(run, RUN_NULLABLE, "run")
        for name in RUN_NULLABLE[:5]:
            self.assertEqual(run["source"][name] is None, run[name] is None, name)
        self.assertEqual(set(usage), USAGE_KEYS)
        self.assert_reasons(usage, USAGE_NULLABLE, "usage")
        self.assertRegex(usage["started_at"], TIME)
        self.assertEqual(set(usage["outer_session"]), {"id", "usage", "note"})
        self.assertIsNone(usage["outer_session"]["usage"])
        self.assertEqual(usage["outer_session"]["note"], "not measured")
        measured = usage["measured"]
        self.assertEqual(set(measured), MEASURED_KEYS)
        self.assert_reasons(measured, MEASURED_NULLABLE, "usage.measured")
        # A snapshot is stored once, in the trial that recorded it: the
        # run-level record carries the count, never the snapshots.
        if usage["meter"] is not None:
            self.assertEqual(set(usage["meter"]),
                             {"snapshot_count", "unreadable_events", "not_sampled"})
        for role in ("agent", "judge", "guard"):
            self.assertEqual(set(measured[role]), ROLE_KEYS, role)
            self.assert_reasons(measured[role], ROLE_NULLABLE, role)
        json.dumps(summary, allow_nan=False)


# ---------------------------------------------------------------------------
# The `run` block: flags, then what the environment observes, else null

class RunBlockTests(Checks):

    COMMIT = "a" * 40

    def block(self, given: argparse.Namespace, environ: dict, **changes) -> dict:
        found = {"harness_commit": self.COMMIT, "cli_version": "2.1.296 (Claude Code)",
                 **changes}
        return run_eval.run_block(given, environ, **found)

    def test_flags_are_recorded_as_flags(self):
        block = self.block(flags(run_billing="subscription", run_runner="routine",
                                 run_location="cloud",
                                 run_id="20261012T170500Z-ab12cd"), {})
        self.assertEqual(block, {
            "schema_version": 1, "billing": "subscription", "runner": "routine",
            "location": "cloud", "id": "20261012T170500Z-ab12cd",
            "session_id": None, "harness_commit": self.COMMIT,
            "cli_version": "2.1.296 (Claude Code)",
            "source": {"billing": "flag", "runner": "flag", "location": "flag",
                       "id": "flag", "session_id": None},
            "reasons": {"session_id": block["reasons"]["session_id"]}})
        self.assert_reasons(block, RUN_NULLABLE, "run")

    def test_the_environment_is_used_where_it_observes_and_is_labeled(self):
        block = self.block(flags(), {"GITHUB_ACTIONS": "true",
                                     "GITHUB_RUN_ID": "123456789"})
        self.assertEqual(block["runner"], "actions")
        self.assertEqual(block["id"], "123456789")
        self.assertEqual(block["source"], {
            "billing": None, "runner": "env:GITHUB_ACTIONS", "location": None,
            "id": "env:GITHUB_RUN_ID", "session_id": None})
        # Billing and a runner's location are not observable there: null, with
        # a reason, never a guess.
        self.assertIsNone(block["billing"])
        self.assertIsNone(block["location"])
        self.assert_reasons(block, RUN_NULLABLE, "run")

    def test_a_remote_session_is_a_cloud_location_and_names_the_session(self):
        block = self.block(flags(), {"CLAUDE_CODE_REMOTE_SESSION_ID": "session_01AbC"})
        self.assertEqual(block["location"], "cloud")
        self.assertEqual(block["session_id"], "session_01AbC")
        self.assertEqual(block["source"]["location"],
                         "env:CLAUDE_CODE_REMOTE_SESSION_ID")
        self.assertEqual(block["source"]["session_id"],
                         "env:CLAUDE_CODE_REMOTE_SESSION_ID")
        # Being a hosted session does not say a routine started it.
        self.assertIsNone(block["runner"])

    def test_flags_beat_the_environment(self):
        block = self.block(
            flags(run_runner="workstation", run_location="local", run_id="mine-1"),
            {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123456789",
             "CLAUDE_CODE_REMOTE_SESSION_ID": "session_01AbC"})
        self.assertEqual((block["runner"], block["location"], block["id"]),
                         ("workstation", "local", "mine-1"))
        self.assertEqual(block["source"]["runner"], "flag")
        self.assertEqual(block["source"]["location"], "flag")
        self.assertEqual(block["source"]["id"], "flag")

    def test_nothing_known_is_null_with_a_reason_for_every_field(self):
        block = self.block(flags(), {}, harness_commit=None, cli_version=None)
        for name in RUN_NULLABLE:
            self.assertIsNone(block[name], name)
        self.assertEqual(block["source"], dict.fromkeys(RUN_NULLABLE[:5]))
        self.assert_reasons(block, RUN_NULLABLE, "run")

    def test_an_environment_value_that_is_not_an_identifier_is_not_recorded(self):
        for bad in ("", "has space", "a|b", "x" * 129, "../up"):
            with self.subTest(bad=bad):
                block = self.block(flags(), {"GITHUB_RUN_ID": bad,
                                             "GITHUB_ACTIONS": "TRUE"})
                self.assertIsNone(block["id"])
                self.assertIsNone(block["runner"])

    def test_the_harness_commit_is_this_checkouts_head(self):
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        self.assertEqual(run_eval.HARNESS_COMMIT, head.stdout.strip())


# ---------------------------------------------------------------------------
# Meter events in the CLI's own output

class RateLimitEventTests(unittest.TestCase):

    def test_events_are_read_from_a_verbose_array_verbatim_and_in_order(self):
        first, second = meter(0.72), meter(0.73, five=0.02)
        events = cli_json.rate_limit_events(
            verbose_array(result_object(), [first, second]))
        self.assertEqual(events, [{"at": FAKE_MESSAGE_AT, "info": first},
                                  {"at": FAKE_MESSAGE_AT, "info": second}])

    def test_a_window_the_harness_does_not_know_is_kept(self):
        info = meter(0.72)
        info["unifiedWindows"]["seven_day_fable"] = {"utilization": 0.4,
                                                     "resetsAt": 1791824400}
        events = cli_json.rate_limit_events(verbose_array(result_object(), [info]))
        self.assertEqual(events[0]["info"], info)

    def test_no_event_is_invented(self):
        for decoded in (result_object(), verbose_array(result_object()), [],
                        None, "text", [None, 3]):
            with self.subTest(decoded=decoded):
                self.assertEqual(cli_json.rate_limit_events(decoded), [])

    def test_an_event_without_a_preceding_timestamp_has_none(self):
        events = cli_json.rate_limit_events(
            verbose_array(result_object(), [meter(0.5)], at=None))
        self.assertEqual(events, [{"at": None, "info": meter(0.5)}])
        junk = verbose_array(result_object(), [meter(0.5)], at="yesterday | `x`")
        self.assertIsNone(cli_json.rate_limit_events(junk)[0]["at"])

    def test_an_event_too_large_or_misshapen_to_keep_is_marked_unreadable(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}
        for info in ("text", None, [1], {"k": "x" * 5000}, deep,
                     {str(i): i for i in range(400)}, {"n": float("inf")},
                     {"n": 10 ** 400}, {"n": list(range(65))}, {"k" * 65: 1}):
            with self.subTest(info=info):
                events = cli_json.rate_limit_events(
                    verbose_array(result_object(), [info]))
                self.assertEqual(events, [{"at": FAKE_MESSAGE_AT, "info": None}])


# ---------------------------------------------------------------------------
# test/fake-claude can supply meter events

class FakeClaudeTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.batches = self.tmp / "meter.json"

    def call(self, *argv, batches=None, meter_file=False) -> object:
        """One fake CLI call; `batches` (re)writes the meter file first."""
        env = {"PATH": os.environ["PATH"], "FAKE_CLAUDE_MODE": "agent"}
        if batches is not None:
            self.batches.write_text(json.dumps(batches), encoding="utf-8")
        if batches is not None or meter_file:
            env["FAKE_CLAUDE_RATE_LIMIT_FILE"] = str(self.batches)
        proc = subprocess.run([sys.executable, str(FAKE_CLAUDE), "-p", "x", *argv],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def infos(self, out) -> list:
        return [event["info"] for event in cli_json.rate_limit_events(out)]

    def test_without_the_file_the_output_is_the_single_object_it_always_was(self):
        out = self.call("--output-format", "json", "--verbose")
        self.assertIsInstance(out, dict)
        self.assertEqual(out["num_turns"], 3)

    def test_each_verbose_call_takes_the_next_batch_off_the_file(self):
        first, second = [meter(0.72)], [meter(0.73), meter(0.74)]
        out = self.call("--output-format", "json", "--verbose",
                        batches=[first, second])
        self.assertIsInstance(out, list)
        self.assertEqual(cli_json.rate_limit_events(out),
                         [{"at": FAKE_MESSAGE_AT, "info": first[0]}])
        self.assertEqual(cli_json.normalize_cli_result(out)["num_turns"], 3)
        out = self.call("--output-format", "json", "--verbose", meter_file=True)
        self.assertEqual(self.infos(out), second)
        # Used up: a later call still answers in the array form, with no event.
        out = self.call("--output-format", "json", "--verbose", meter_file=True)
        self.assertIsInstance(out, list)
        self.assertEqual(self.infos(out), [])

    def test_a_call_without_verbose_takes_nothing(self):
        out = self.call("--output-format", "json", batches=[[meter(0.72)]])
        self.assertIsInstance(out, dict)
        self.assertEqual(json.loads(self.batches.read_text(encoding="utf-8")),
                         [[meter(0.72)]])


# ---------------------------------------------------------------------------
# One trial's own calls, on a stand-in clock

class Clock:
    """Starts at 2026-10-12T17:00:00Z and moves only when told to."""

    def __init__(self):
        self.at = datetime(2026, 10, 12, 17, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Cli:
    """Stands in for subprocess.run: each call takes five seconds of the
    clock and answers with the next canned stdout (the last one repeats)."""

    def __init__(self, clock: Clock, *stdouts, raises=None):
        self.clock, self.stdouts, self.raises = clock, list(stdouts), raises
        self.argvs: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.argvs.append(list(cmd))
        self.clock.advance(5)
        if self.raises is not None:
            raise self.raises
        stdout = self.stdouts[min(len(self.argvs), len(self.stdouts)) - 1]
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")


class MeasuredTests(Checks):

    TS = "20261012T170000Z"
    AGENT = {
        "calls": 1, "duration_ms": 5000, "num_turns": 3, "cost_usd": 0.25,
        "cost_basis": "list",
        "models": {
            "claude-haiku-5-5": {"input_tokens": 1, "output_tokens": 2,
                                 "cache_read_input_tokens": 3,
                                 "cache_creation_input_tokens": 4,
                                 "cost_usd": 0.05},
            "claude-sonnet-5": {"input_tokens": 10, "output_tokens": 20,
                                "cache_read_input_tokens": 50,
                                "cache_creation_input_tokens": 18,
                                "cost_usd": 0.2}},
        "cache_creation": {"ephemeral_5m_input_tokens": 7,
                           "ephemeral_1h_input_tokens": 11},
        "reasons": {}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.workspace = Path(tempfile.mkdtemp(prefix="workspace-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)
        self.clock = Clock()
        patcher = mock.patch.object(run_eval, "_now", self.clock.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        run_eval._TELEMETRY.begin(flags())
        self.addCleanup(run_eval._TELEMETRY.begin)

    def agent(self, cli: Cli, **arm) -> dict:
        arm = {"name": "without_skill", "timeout": 30, **arm}
        with mock.patch.object(run_eval.subprocess, "run", cli), \
             mock.patch.dict(run_eval.os.environ,
                             {"CLAUDE_BIN": "fake-claude", "HOME": str(self.tmp),
                              "XDG_STATE_HOME": str(self.tmp / "state")}):
            return run_eval.run_agent(self.workspace, "Do the task.", arm)

    def written(self, arm="without_skill", **extra) -> dict:
        with mock.patch.dict(run_eval.os.environ, clear=False) as environ:
            for name in ("GITHUB_ACTIONS", "GITHUB_RUN_ID",
                         "CLAUDE_CODE_REMOTE_SESSION_ID"):
                environ.pop(name, None)
            run_eval._write_summary(self.tmp / "results", "s", arm, self.TS,
                                    None, None, None, None, None,
                                    extra={"n": 1, **extra})
        path = self.tmp / "results" / "s" / self.TS / arm / "summary.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_an_agent_call_records_its_times_tokens_cost_and_cache_split(self):
        out = self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), [meter(0.72)]))))
        self.assertNotIn("error", out, out)
        summary = self.written()
        self.assert_complete(summary)
        measured = summary["usage"]["measured"]
        self.assertEqual(measured["scope"], "trial")
        self.assertEqual(measured["started_at"], "2026-10-12T17:00:00Z")
        self.assertEqual(measured["ended_at"], "2026-10-12T17:00:05Z")
        self.assertEqual(measured["calls"], [
            {"role": "agent", "index": 0, "started_at": "2026-10-12T17:00:00Z",
             "ended_at": "2026-10-12T17:00:05Z", "duration_ms": 5000,
             "outcome": "ok"}])
        self.assertEqual(measured["agent"], self.AGENT)
        self.assertEqual(measured["judge"], no_calls("judge"))
        self.assertEqual(measured["guard"], no_calls("guard"))
        self.assertEqual(measured["meter"], {"omitted": 0, "snapshots": [
            {"at": FAKE_MESSAGE_AT, "at_source": "cli_message",
             "call": {"role": "agent", "index": 0}, "info": meter(0.72)}]})
        self.assertEqual(summary["usage"]["meter"]["snapshot_count"], 1)

    def test_a_follow_up_session_is_two_calls_and_one_cumulative_cost(self):
        # A resumed call's cost and modelUsage already cover the session.
        first = result_object(total_cost_usd=0.1)
        cli = Cli(self.clock, json.dumps(first), json.dumps(result_object()))
        out = self.agent(cli, followups=["go on"])
        self.assertNotIn("error", out, out)
        measured = self.written()["usage"]["measured"]
        self.assertEqual([(c["index"], c["started_at"], c["ended_at"])
                          for c in measured["calls"]],
                         [(0, "2026-10-12T17:00:00Z", "2026-10-12T17:00:05Z"),
                          (1, "2026-10-12T17:00:05Z", "2026-10-12T17:00:10Z")])
        self.assertEqual(measured["agent"], {
            **self.AGENT, "calls": 2, "duration_ms": 10000, "num_turns": 6,
            "cache_creation": {"ephemeral_5m_input_tokens": 14,
                               "ephemeral_1h_input_tokens": 22}})

    def test_a_call_without_a_result_is_null_with_a_reason_never_zero(self):
        cli = Cli(self.clock, raises=subprocess.TimeoutExpired("claude", 30))
        out = self.agent(cli)
        self.assertEqual(out["error"], "timeout")
        summary = self.written()
        self.assert_complete(summary)
        measured = summary["usage"]["measured"]
        self.assertEqual(measured["calls"][0]["outcome"], "timeout")
        self.assertEqual(measured["calls"][0]["duration_ms"], 5000)
        agent = measured["agent"]
        self.assertEqual((agent["calls"], agent["duration_ms"]), (1, 5000))
        for name in ROLE_NULLABLE:
            self.assertIsNone(agent[name], name)
        self.assertEqual(agent["reasons"]["models"],
                         "the agent call ended without a result")

    def test_a_result_without_the_optional_figures_is_null_with_a_reason(self):
        bare = {"type": "result", "is_error": False, "result": "done",
                "session_id": "s", "modelUsage": {"m-1": {"inputTokens": 5}}}
        self.agent(Cli(self.clock, json.dumps(bare)))
        agent = self.written()["usage"]["measured"]["agent"]
        self.assertEqual(agent["models"], {"m-1": {
            "input_tokens": 5, "output_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None, "cost_usd": None}})
        for name in ROLE_NULLABLE[:4]:
            self.assertIsNone(agent[name], name)
        self.assertEqual(set(agent["reasons"]), set(ROLE_NULLABLE[:4]))

    def test_misshapen_cli_figures_are_null_and_the_summary_still_ingests(self):
        odd = result_object(
            num_turns=2.5, total_cost_usd="free", usage=["not", "a", "mapping"],
            modelUsage={"m-1": {"inputTokens": -1, "outputTokens": 10 ** 400,
                                "costUSD": float("nan"),
                                "costBasis": {"not": "a label"}},
                        "bad id": {"inputTokens": 1}, "m-2": "text"})
        self.agent(Cli(self.clock, json.dumps(odd)))
        summary = self.written()
        self.assert_complete(summary)
        agent = summary["usage"]["measured"]["agent"]
        # Two of its three modelUsage entries could not be read, so the
        # per-model record is unknown rather than one model presented as all.
        for name in ROLE_NULLABLE:
            self.assertIsNone(agent[name], name)
        ingest.check_run(summary["run"], "s")
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_a_judge_call_costs_what_its_model_usage_says(self):
        # A judge result that carries its modelUsage alone (#371 records the
        # other figures when the result has them).
        started = self.clock.now()
        self.clock.advance(7)
        usage = result_object()["modelUsage"]
        run_eval._TELEMETRY.timed("judge", started, [{"modelUsage": usage}, None])
        judge = self.written()["usage"]["measured"]["judge"]
        self.assertEqual(judge, {
            **self.AGENT, "duration_ms": 7000, "num_turns": None,
            "cache_creation": None,
            "reasons": dict.fromkeys(
                ("num_turns", "cache_creation"),
                "the judge's CLI result does not carry it")})

    def test_calls_belong_to_the_next_summary_written_and_to_no_other(self):
        self.agent(Cli(self.clock, json.dumps(result_object())))
        self.assertEqual(self.written("with_skill")["usage"]["measured"]["agent"]
                         ["calls"], 1)
        later = self.written("without_skill")["usage"]["measured"]
        self.assertEqual(later["agent"], no_calls("agent"))
        self.assertEqual(later["calls"], [])

    def test_a_summary_with_no_trial_says_so(self):
        measured = self.written()["usage"]["measured"]
        self.assertEqual(measured["calls"], [])
        self.assertEqual(measured["meter"], {"snapshots": [], "omitted": 0})
        self.assertIsNone(measured["started_at"])
        self.assertIsNone(measured["ended_at"])
        self.assertEqual(set(measured["reasons"]), {"started_at", "ended_at"})

    def test_the_first_and_last_event_of_each_call_are_kept(self):
        infos = [meter(0.70), meter(0.71), meter(0.72)]
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), infos))))
        usage = self.written()["usage"]
        self.assertEqual([s["info"] for s in
                          usage["measured"]["meter"]["snapshots"]],
                         [infos[0], infos[2]])
        self.assertEqual(usage["meter_delta"], {
            "confounded": True, "scope": "account-wide",
            "from_at": FAKE_MESSAGE_AT, "to_at": FAKE_MESSAGE_AT,
            "uncovered": uncovered(0, 1),
            "windows": {
                "five_hour": {"from": 0.01, "to": 0.01, "delta": 0.0,
                              "window_reset": False},
                "seven_day": {"from": 0.7, "to": 0.72, "delta": 0.02,
                              "window_reset": False}}})

    def test_a_window_that_reset_between_snapshots_has_no_delta(self):
        infos = [meter(0.95), meter(0.01, week_resets=1792429200)]
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), infos))))
        windows = self.written()["usage"]["meter_delta"]["windows"]
        self.assertEqual(windows["seven_day"], {
            "from": 0.95, "to": 0.01, "delta": None, "window_reset": True})

    def test_an_event_without_a_timestamp_is_stamped_with_its_calls_start(self):
        # Only an event before the first timestamped message has none, so
        # the call's start is the nearest time known to come before it, and
        # the run's snapshots stay in order.
        array = verbose_array(result_object(), [meter(0.5)], at=None)
        array += [{"type": "assistant", "timestamp": "2026-10-12T17:00:01.000Z"},
                  {"type": "rate_limit_event", "rate_limit_info": meter(0.6)}]
        self.agent(Cli(self.clock, json.dumps(array)))
        usage = self.written()["usage"]
        first, last = usage["measured"]["meter"]["snapshots"]
        self.assertEqual((first["at"], first["at_source"]),
                         ("2026-10-12T17:00:00Z", "call_start"))
        self.assertEqual((last["at"], last["at_source"]),
                         ("2026-10-12T17:00:01.000Z", "cli_message"))
        self.assertLessEqual(usage["meter_delta"]["from_at"],
                             usage["meter_delta"]["to_at"])

    def test_the_delta_names_the_calls_it_does_not_cover(self):
        # Review round 1 on #378: one real call printed one event, as it
        # began. So the last snapshot is the START of the last agent call,
        # and that call and everything after it are not in the delta.
        guard_started = self.clock.now()
        self.clock.advance(2)
        run_eval._TELEMETRY.timed("guard", guard_started)
        for week in (0.72, 0.74):
            self.agent(Cli(self.clock, json.dumps(
                verbose_array(result_object(), [meter(week)]))))
        judge_started = self.clock.now()
        self.clock.advance(3)
        run_eval._TELEMETRY.timed("judge", judge_started,
                                  [{"modelUsage": result_object()["modelUsage"]}])
        usage = self.written()["usage"]
        # The guard (here printing no event) came before the first snapshot;
        # the second agent call and the judge (likewise) are from the last
        # snapshot on.
        self.assertEqual(usage["meter_delta"]["uncovered"], uncovered(1, 2))
        # Only the run's end is unread now: the judge and the guard print the
        # meter event an agent call does when they are made `--verbose`.
        self.assertEqual(set(usage["meter"]["not_sampled"]), {"run_end"})
        self.assertIn("added call", usage["meter"]["not_sampled"]["run_end"])

    def test_a_run_of_one_agent_call_has_no_delta_and_says_why(self):
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), [meter(0.72)]))))
        usage = self.written()["usage"]
        self.assertIsNone(usage["meter_delta"])
        self.assertIn("one agent call", usage["reasons"]["meter_delta"])

    def test_snapshots_too_large_to_write_are_dropped_to_fit_the_ingest_cap(self):
        # Review round 1 on #378: twelve snapshots whose `info` is within the
        # event limits (keys of short number lists, about 2 KB compact, one
        # number to a line once indented) wrote a summary.json more than
        # twice the ingester's 65,536-byte cap.
        info = {f"w{i:02}": [1, 2, 3, 4, 5, 6, 7, 8, 9] for i in range(40)}
        info["unifiedWindows"] = meter(0.72)["unifiedWindows"]
        self.assertLess(len(json.dumps(info)), cli_json.METER_INFO_MAX_BYTES)
        self.assertEqual(cli_json.rate_limit_events(
            verbose_array(result_object(), [info]))[0]["info"], info)
        for _ in range(12):
            self.agent(Cli(self.clock, json.dumps(
                verbose_array(result_object(), [info]))))
        summary = self.written()
        path = self.tmp / "results" / "s" / self.TS / "without_skill" / "summary.json"
        self.assertLess(path.stat().st_size, ingest.SIZE_CAPS["summary.json"] // 2)
        self.assert_complete(summary)
        kept = summary["usage"]["measured"]["meter"]
        # What could not be written is counted, and the delta still has its ends.
        self.assertEqual(len(kept["snapshots"]) + kept["omitted"], 12)
        self.assertLess(len(kept["snapshots"]), 12)
        self.assertEqual(summary["usage"]["meter"]["snapshot_count"], 12)
        self.assertIsNotNone(summary["usage"]["meter_delta"])
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_calls_of_a_trial_that_wrote_no_summary_go_to_no_other_trial(self):
        self.agent(Cli(self.clock, json.dumps(result_object())))
        run_eval._TELEMETRY.begin_trial()
        measured = self.written()["usage"]["measured"]
        self.assertEqual(measured["calls"], [])
        self.assertEqual(measured["agent"], no_calls("agent"))

    def test_a_judge_call_that_failed_says_it_ended_without_a_result(self):
        run_eval._TELEMETRY.timed("judge", self.clock.now(), [], "error")
        judge = self.written()["usage"]["measured"]["judge"]
        self.assertEqual(judge["calls"], 1)
        for name in ROLE_NULLABLE:
            self.assertEqual(judge["reasons"][name],
                             "the judge call ended without a result")

    def test_an_unreadable_event_is_counted_not_kept(self):
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), ["junk", meter(0.5)]))))
        found = self.written()["usage"]
        self.assertEqual(found["meter"]["unreadable_events"], 1)
        self.assertEqual(found["meter"]["snapshot_count"], 1)
        self.assertEqual([s["info"] for s in found["measured"]["meter"]["snapshots"]],
                         [meter(0.5)])

    def test_the_meter_record_is_bounded_and_keeps_both_ends(self):
        for week in range(40):
            self.agent(Cli(self.clock, json.dumps(
                verbose_array(result_object(), [meter(week / 100)]))))
        found = self.written()["usage"]
        kept = [s["info"]["unifiedWindows"]["seven_day"]["utilization"]
                for s in found["measured"]["meter"]["snapshots"]]
        self.assertEqual(kept, [w / 100 for w in (*range(6), *range(34, 40))])
        self.assertEqual(found["measured"]["meter"]["omitted"], 28)
        # The delta still spans the whole run: its ends are never dropped.
        self.assertEqual(found["meter"]["snapshot_count"], 40)
        self.assertEqual(found["meter_delta"]["windows"]["seven_day"]["delta"], 0.39)

    def fullest(self, sessions: int) -> dict:
        """A summary after `sessions` two-call agent sessions, each call with
        two meter events naming three windows, on sixteen models."""
        info = meter(0.72)
        info["unifiedWindows"]["seven_day_fable"] = {"utilization": 0.4,
                                                     "resetsAt": 1791824400}
        usage = {f"claude-model-{i}": {
            "inputTokens": 10 ** 9, "outputTokens": 10 ** 9,
            "cacheReadInputTokens": 10 ** 9, "cacheCreationInputTokens": 10 ** 9,
            "costUSD": 123.456789, "costBasis": "list"} for i in range(16)}
        reply = json.dumps(verbose_array(result_object(modelUsage=usage),
                                         [info, info]))
        for _ in range(sessions):
            self.agent(Cli(self.clock, reply, reply), followups=["go on"])
        return self.written()

    def test_the_fullest_blocks_fit_in_half_the_ingest_cap(self):
        # Measured: every snapshot slot and every call slot taken, with
        # today's size of `info` plus one window, pretty-printed as on disk,
        # is 26 KB. A larger `info` is held by the written-size bound below.
        summary = self.fullest(16)
        self.assertEqual(len(summary["usage"]["measured"]["meter"]["snapshots"]), 12)
        self.assertEqual(len(summary["usage"]["measured"]["calls"]), 32)
        size = len(json.dumps({"run": summary["run"], "usage": summary["usage"]},
                              indent=2).encode("utf-8"))
        self.assertLess(size, ingest.SIZE_CAPS["summary.json"] // 2, size)
        ingest.check_run(summary["run"], "s")
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_past_the_call_bound_only_the_role_totals_are_kept(self):
        summary = self.fullest(17)
        self.assert_complete(summary)
        measured = summary["usage"]["measured"]
        self.assertIsNone(measured["calls"])
        self.assertEqual(measured["agent"]["calls"], 34)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    # A judge call held back until its pair's objective result was known
    # (#371) is made after its trial's summary was written, and `amend` adds
    # it to that summary.

    def amended(self, summary: dict, seconds: float = 3) -> dict:
        """`summary` after a judge call of `seconds`, begun two seconds after
        the summary was written, is added to it."""
        self.clock.advance(2)
        started = self.clock.now()
        self.clock.advance(seconds)
        run_eval._TELEMETRY.amend(summary, "judge", started, [
            {"modelUsage": result_object()["modelUsage"]}])
        return summary["usage"]["measured"]

    def test_a_call_added_after_the_summary_extends_its_times_and_calls(self):
        self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        self.assertEqual(summary["usage"]["measured"]["ended_at"],
                         "2026-10-12T17:00:05Z")
        measured = self.amended(summary)
        # Agent 17:00:00 to :05, then the judge :07 to :10.
        self.assertEqual(measured["ended_at"], "2026-10-12T17:00:10Z")
        self.assertEqual([(c["role"], c["index"], c["outcome"])
                          for c in measured["calls"]],
                         [("agent", 0, "ok"), ("judge", 0, "ok")])
        self.assertEqual(measured["calls"][1], {
            "role": "judge", "index": 0, "started_at": "2026-10-12T17:00:07Z",
            "ended_at": "2026-10-12T17:00:10Z", "duration_ms": 3000,
            "outcome": "ok"})
        self.assertEqual((measured["judge"]["calls"],
                          measured["judge"]["duration_ms"]), (1, 3000))
        self.assertEqual(measured["agent"]["calls"], 1)
        self.assert_complete(summary)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_a_call_added_after_the_summary_counts_toward_the_meter_delta(self):
        # Two agent calls that each print a snapshot, then the added call:
        # the delta leaves out the last agent call and the one after it.
        for week in (0.72, 0.75):
            self.agent(Cli(self.clock, json.dumps(
                verbose_array(result_object(), [meter(week)]))))
        summary = self.written()
        self.assertEqual(summary["usage"]["meter_delta"]["uncovered"],
                         uncovered(0, 1))
        self.amended(summary)
        self.assertEqual(self.written("with_skill")["usage"]["meter_delta"]
                         ["uncovered"], uncovered(0, 2))

    def test_a_call_added_past_the_call_bound_drops_the_list_not_the_totals(self):
        for _ in range(run_eval.USAGE_MAX_CALLS):
            self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        self.assertEqual(len(summary["usage"]["measured"]["calls"]),
                         run_eval.USAGE_MAX_CALLS)
        measured = self.amended(summary)
        self.assertIsNone(measured["calls"])
        self.assertIn(f"more than {run_eval.USAGE_MAX_CALLS} calls",
                      measured["reasons"]["calls"])
        self.assertEqual((measured["agent"]["calls"], measured["judge"]["calls"]),
                         (run_eval.USAGE_MAX_CALLS, 1))
        self.assert_complete(summary)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_a_call_added_past_the_size_bound_drops_the_list_not_the_totals(self):
        self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        size = len(json.dumps({"usage": {"measured": summary["usage"]["measured"]}},
                              indent=2).encode("utf-8"))
        # Room for what is there now, and not for one more listed call.
        with mock.patch.object(run_eval, "USAGE_MAX_BYTES", size):
            measured = self.amended(summary)
        self.assertIsNone(measured["calls"])
        self.assertIn("too large to list", measured["reasons"]["calls"])
        self.assertEqual(measured["judge"]["calls"], 1)
        self.assert_complete(summary)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    # A deferred judge call prints meter events like any call (#370, #371);
    # `amend` turns them into snapshots exactly as `_measure` does.

    def event(self, week: float, at: str | None = FAKE_MESSAGE_AT) -> dict:
        return {"at": at, "info": None if week is None else meter(week)}

    def amended_with(self, summary: dict, events: list, role="judge") -> dict:
        self.clock.advance(2)
        started = self.clock.now()
        self.clock.advance(3)
        run_eval._TELEMETRY.amend(summary, role, started, [
            {"modelUsage": result_object()["modelUsage"]}], "ok", events)
        # What `finish()` does once the run has ended.
        summary["usage"].update(run_eval._TELEMETRY._run_level())
        return summary["usage"]["measured"]

    def weeks(self, snapshots: list) -> list:
        return [s["info"]["unifiedWindows"]["seven_day"]["utilization"]
                for s in snapshots]

    def test_a_call_added_after_the_summary_keeps_its_first_and_last_event(self):
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), [meter(0.72)]))))
        summary = self.written()
        measured = self.amended_with(summary, [
            self.event(0.73), self.event(0.74), self.event(0.75, at=None)])
        self.assertEqual(measured["meter"]["omitted"], 0)
        first, last = measured["meter"]["snapshots"][1:]
        self.assertEqual(self.weeks([first, last]), [0.73, 0.75])
        self.assertEqual(first, {"at": FAKE_MESSAGE_AT, "at_source": "cli_message",
                                 "call": {"role": "judge", "index": 0},
                                 "info": meter(0.73)})
        # No timestamp of its own: the call's start, two seconds in.
        self.assertEqual((last["at"], last["at_source"]),
                         ("2026-10-12T17:00:07Z", "call_start"))
        telemetry = run_eval._TELEMETRY
        self.assertEqual(len(telemetry.snapshots), 3)
        self.assertEqual((telemetry.calls_seen, telemetry.snapshot_calls),
                         (2, [0, 1, 1]))
        self.assert_complete(summary)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_a_call_added_after_the_summary_keeps_both_ends_of_a_full_record(self):
        # Six agent calls of two events each fill the trial's twelve.
        weeks = [0.10 + 0.01 * i for i in range(12)]
        for i in range(6):
            self.agent(Cli(self.clock, json.dumps(verbose_array(
                result_object(), [meter(weeks[2 * i]), meter(weeks[2 * i + 1])]))))
        summary = self.written()
        self.assertEqual(len(summary["usage"]["measured"]["meter"]["snapshots"]),
                         run_eval.METER_MAX_SNAPSHOTS)
        measured = self.amended_with(summary, [
            self.event(0.5), self.event(0.6), self.event(0.7)])
        meter_block = measured["meter"]
        # 12 + 2 new = 14, of which the first six and the last six are kept.
        self.assertEqual(self.weeks(meter_block["snapshots"]),
                         weeks[:6] + weeks[8:] + [0.5, 0.7])
        self.assertEqual(meter_block["omitted"], 2)
        self.assertEqual(len(run_eval._TELEMETRY.snapshots), 14)
        self.assertEqual(run_eval._TELEMETRY.snapshot_calls[-2:], [6, 6])
        self.assert_complete(summary)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_a_call_added_to_a_record_that_already_omitted_counts_what_it_omitted(self):
        for i in range(8):
            self.agent(Cli(self.clock, json.dumps(verbose_array(
                result_object(), [meter(0.1 + 0.01 * i), meter(0.2 + 0.01 * i)]))))
        summary = self.written()
        self.assertEqual(summary["usage"]["measured"]["meter"]["omitted"], 4)
        measured = self.amended_with(summary, [self.event(0.5)])
        # 16 in the trial, one new: 17 in all, 12 kept.
        self.assertEqual(measured["meter"]["omitted"], 5)
        self.assertEqual(len(measured["meter"]["snapshots"]), 12)
        self.assertEqual(self.weeks(measured["meter"]["snapshots"])[-1], 0.5)

    def test_an_api_billed_run_stores_no_amended_snapshot_but_counts_it(self):
        run_eval._TELEMETRY.begin(flags(run_billing="api"))
        self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        self.assertIsNone(summary["usage"]["measured"]["meter"])
        measured = self.amended_with(summary, [self.event(0.73), self.event(0.74)])
        self.assertIsNone(measured["meter"])
        self.assertEqual(len(run_eval._TELEMETRY.snapshots), 2)
        self.assertEqual(measured["judge"]["calls"], 1)

    def test_an_unreadable_event_of_an_added_call_is_counted_not_kept(self):
        self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        measured = self.amended_with(summary, [self.event(None), self.event(0.73)])
        self.assertEqual(self.weeks(measured["meter"]["snapshots"]), [0.73])
        self.assertEqual(run_eval._TELEMETRY.unreadable, 1)
        self.assertEqual(run_eval._TELEMETRY.snapshot_calls, [1])

    def test_snapshots_of_an_added_call_too_large_to_write_are_dropped(self):
        self.agent(Cli(self.clock, json.dumps(
            verbose_array(result_object(), [meter(0.72)]))))
        summary = self.written()
        size = len(json.dumps({"usage": {"measured": summary["usage"]["measured"]}},
                              indent=2).encode("utf-8"))
        # Room for what is there now: not for the new snapshots, nor for the
        # trial's first and last.
        with mock.patch.object(run_eval, "USAGE_MAX_BYTES", size):
            measured = self.amended_with(summary, [
                self.event(0.73), self.event(0.74)])
        self.assertEqual(measured["meter"], {"snapshots": [], "omitted": 3})
        self.assertEqual(len(run_eval._TELEMETRY.snapshots), 3)
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_events_of_a_call_added_past_the_call_bound_are_still_kept(self):
        for _ in range(run_eval.USAGE_MAX_CALLS):
            self.agent(Cli(self.clock, json.dumps(result_object())))
        summary = self.written()
        measured = self.amended_with(summary, [self.event(0.73)])
        self.assertIsNone(measured["calls"])
        self.assertIn(f"more than {run_eval.USAGE_MAX_CALLS} calls",
                      measured["reasons"]["calls"])
        self.assertEqual(self.weeks(measured["meter"]["snapshots"]), [0.73])
        self.assertEqual(run_eval._TELEMETRY.snapshot_calls,
                         [run_eval.USAGE_MAX_CALLS])
        ingest.check_usage(summary["usage"], "s", summary["run"])

    def test_split_events_separates_the_meter_from_the_usage_figures(self):
        entries = [{"modelUsage": {"m": 1}, "events": [self.event(0.7)]},
                   None, {"modelUsage": None, "total_cost_usd": 1,
                          "events": [self.event(0.8), self.event(0.9)]},
                   {"modelUsage": None}]
        results, events = run_eval._split_events(entries)
        self.assertEqual(results, [{"modelUsage": {"m": 1}}, None,
                                   {"modelUsage": None, "total_cost_usd": 1},
                                   {"modelUsage": None}])
        self.assertEqual(self.weeks(events), [0.7, 0.8, 0.9])
        # The entries themselves are left as they were.
        self.assertIn("events", entries[0])
        self.assertEqual(run_eval._split_events([]), ([], []))

    def test_a_call_added_to_a_summary_that_lists_no_calls_adds_none(self):
        summary = self.fullest(17)
        self.assertIsNone(summary["usage"]["measured"]["calls"])
        measured = self.amended(summary)
        self.assertIsNone(measured["calls"])
        self.assertEqual(measured["judge"]["calls"], 1)
        ingest.check_usage(summary["usage"], "s", summary["run"])


class RunLevelTests(Checks):
    """What every summary of one run shares, and what ending the run adds."""

    TS = "20261012T170000Z"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clock = Clock()
        patcher = mock.patch.object(run_eval, "_now", self.clock.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(run_eval._TELEMETRY.begin)

    def write(self, arm: str) -> Path:
        run_eval._write_summary(self.tmp, "s", arm, self.TS, None, None, None,
                                None, None, extra={"n": 1})
        return self.tmp / "s" / self.TS / arm / "summary.json"

    def read(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def record(self, week: float) -> None:
        now = self.clock.now()
        run_eval._TELEMETRY.record("agent", [
            {"started": now, "ended": now, "outcome": "ok",
             "events": [{"at": None, "info": meter(week)}]}])

    def test_ending_the_run_stamps_every_summary_with_the_same_record(self):
        run_eval._TELEMETRY.begin(flags())
        self.record(0.72)
        first = self.write("with_skill")
        before = self.read(first)["usage"]
        self.assertIsNone(before["ended_at"])
        self.assertIn("ended_at", before["reasons"])
        self.assertIsNone(before["meter_delta"])
        self.clock.advance(60)
        self.record(0.74)
        second = self.write("without_skill")
        self.clock.advance(1)
        run_eval._TELEMETRY.finish()
        one, two = self.read(first)["usage"], self.read(second)["usage"]
        for usage in (one, two):
            self.assertEqual(usage["started_at"], "2026-10-12T17:00:00Z")
            self.assertEqual(usage["ended_at"], "2026-10-12T17:01:01Z")
            self.assertNotIn("ended_at", usage["reasons"])
            self.assertEqual(usage["meter"]["snapshot_count"], 2)
            # Each summary holds only the snapshot its own call printed.
            self.assertEqual(len(usage["measured"]["meter"]["snapshots"]), 1)
            self.assertEqual(usage["meter_delta"]["windows"]["seven_day"],
                             {"from": 0.72, "to": 0.74, "delta": 0.02,
                              "window_reset": False})
        self.assertEqual({k: v for k, v in one.items() if k != "measured"},
                         {k: v for k, v in two.items() if k != "measured"})
        # What each summary measured for itself is left alone.
        self.assertEqual(one["measured"]["agent"]["calls"], 1)
        self.assertEqual(one["measured"]["ended_at"], "2026-10-12T17:00:00Z")
        self.assert_complete(self.read(first))

    def test_the_meter_delta_is_labeled_confounded_and_account_wide(self):
        run_eval._TELEMETRY.begin(flags())
        self.record(0.72)
        self.record(0.73)
        delta = self.read(self.write("with_skill"))["usage"]["meter_delta"]
        self.assertIs(delta["confounded"], True)
        self.assertEqual(delta["scope"], "account-wide")

    def test_an_api_billed_run_has_no_meter_and_says_why(self):
        run_eval._TELEMETRY.begin(flags(run_billing="api"))
        self.record(0.72)
        self.record(0.73)
        usage = self.read(self.write("with_skill"))["usage"]
        self.assertIsNone(usage["meter"])
        self.assertIsNone(usage["meter_delta"])
        self.assertIsNone(usage["measured"]["meter"])
        self.assertIn("API-billed", usage["reasons"]["meter"])
        self.assertIn("API-billed", usage["reasons"]["meter_delta"])
        self.assertIn("API-billed", usage["measured"]["reasons"]["meter"])

    def test_what_else_ran_is_unknown_unless_someone_asserts_quiet(self):
        run_eval._TELEMETRY.begin(flags())
        usage = self.read(self.write("with_skill"))["usage"]
        self.assertEqual(usage["other_account_activity"], "unknown")
        self.assertIsNone(usage["asserted_quiet_by"])
        self.assertIsNone(usage["concurrent_runs"])
        self.assertEqual(usage["reasons"]["concurrent_runs"],
                         "not knowable inside one run; computed at ingest")
        run_eval._TELEMETRY.begin(flags(asserted_quiet="my"))
        usage = self.read(self.write("without_skill"))["usage"]
        self.assertEqual(usage["other_account_activity"], "asserted_quiet")
        self.assertEqual(usage["asserted_quiet_by"], "my")

    def test_the_outer_session_is_named_from_the_environment_and_not_measured(self):
        run_eval._TELEMETRY.begin(flags())
        with mock.patch.dict(run_eval.os.environ,
                             {"CLAUDE_CODE_REMOTE_SESSION_ID": "session_01AbC"}):
            summary = self.read(self.write("with_skill"))
        self.assertEqual(summary["usage"]["outer_session"], {
            "id": "session_01AbC", "usage": None, "note": "not measured"})
        self.assertEqual(summary["run"]["session_id"], "session_01AbC")

    def test_an_arm_aggregate_sums_its_trials_and_lists_no_calls(self):
        run_eval._TELEMETRY.begin(flags())
        trials = []
        for k in (1, 2):
            now = self.clock.now()
            self.clock.advance(10)
            run_eval._TELEMETRY.record(
                "agent", [{"started": now, "ended": self.clock.now(),
                           "outcome": "ok", "events": []}], [result_object()])
            arm_dir = self.tmp / "s" / self.TS / "with_skill" / f"trial-{k}"
            run_eval._write_summary(self.tmp, "s", "with_skill", self.TS, None,
                                    None, None, None, None, extra={"trial": k},
                                    arm_dir=arm_dir)
            trials.append(self.read(arm_dir / "summary.json"))
        run_eval._write_summary(self.tmp, "s", "with_skill", self.TS, None,
                                None, None, None, None, extra={"n": 2},
                                usage_trials=trials)
        summary = self.read(self.tmp / "s" / self.TS / "with_skill" / "summary.json")
        self.assert_complete(summary)
        measured = summary["usage"]["measured"]
        self.assertEqual(measured["scope"], "arm")
        self.assertIsNone(measured["calls"])
        self.assertIsNone(measured["meter"])
        self.assertEqual(measured["reasons"]["meter"],
                         "listed in each trial's own summary")
        self.assertEqual((measured["started_at"], measured["ended_at"]),
                         ("2026-10-12T17:00:00Z", "2026-10-12T17:00:20Z"))
        self.assertEqual(measured["agent"], {
            "calls": 2, "duration_ms": 20000, "num_turns": 6, "cost_usd": 0.5,
            "cost_basis": "list",
            "models": {
                "claude-haiku-5-5": {"input_tokens": 2, "output_tokens": 4,
                                     "cache_read_input_tokens": 6,
                                     "cache_creation_input_tokens": 8,
                                     "cost_usd": 0.1},
                "claude-sonnet-5": {"input_tokens": 20, "output_tokens": 40,
                                    "cache_read_input_tokens": 100,
                                    "cache_creation_input_tokens": 36,
                                    "cost_usd": 0.4}},
            "cache_creation": {"ephemeral_5m_input_tokens": 14,
                               "ephemeral_1h_input_tokens": 22},
            "reasons": {}})
        self.assertEqual(measured["judge"], no_calls("judge"))

    def test_one_trial_that_could_not_be_read_makes_the_sum_unknown(self):
        run_eval._TELEMETRY.begin(flags())
        trials = []
        for k, results in ((1, [result_object()]), (2, [])):
            now = self.clock.now()
            run_eval._TELEMETRY.record(
                "agent", [{"started": now, "ended": now, "outcome": "ok",
                           "events": []}], results)
            arm_dir = self.tmp / "s" / self.TS / "with_skill" / f"trial-{k}"
            run_eval._write_summary(self.tmp, "s", "with_skill", self.TS, None,
                                    None, None, None, None, extra={"trial": k},
                                    arm_dir=arm_dir)
            trials.append(self.read(arm_dir / "summary.json"))
        run_eval._write_summary(self.tmp, "s", "with_skill", self.TS, None,
                                None, None, None, None, extra={"n": 2},
                                usage_trials=trials)
        summary = self.read(self.tmp / "s" / self.TS / "with_skill" / "summary.json")
        self.assert_complete(summary)
        agent = summary["usage"]["measured"]["agent"]
        self.assertEqual(agent["calls"], 2)
        self.assertIsNone(agent["cost_usd"])
        self.assertIsNone(agent["num_turns"])
        # Not trial 1's tokens alone, presented as the arm's.
        self.assertIsNone(agent["models"])
        self.assertIn("models", agent["reasons"])


# ---------------------------------------------------------------------------
# End to end: run_eval.py against test/fake-claude

class EndToEndTests(Checks):

    TS = "20261012T170000Z"
    SKILL = "usage-probe"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "argv.log"
        self.batches = self.tmp / "meter.json"
        self.eval_dir = self.tmp / "evals" / self.SKILL
        (self.eval_dir / "seed").mkdir(parents=True)
        (self.eval_dir / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        self.registry = self.tmp / "registry"
        skill_dir = self.registry / "plugins" / "b" / "skills" / self.SKILL
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {self.SKILL}\ndescription: stand-in.\n---\n",
            encoding="utf-8")
        self.results = self.tmp / "results"
        # A budget no stand-in meter here reaches (#68): these tests are
        # about the usage record, and the committed per-run cap (3 points) is
        # what their 0.72 to 0.75 readings would otherwise stop at. The stop
        # itself is test_issue_68_allowance_guard.py's.
        self.budget = self.tmp / "budget.yml"
        self.budget.write_text(yaml.safe_dump({
            "weekly_cap": 1, "per_run_cap": 1, "per_run_overrun": 0.005,
            "stop_when_account_above": 1}), encoding="utf-8")

    def fixture(self, batches=None, **extra) -> None:
        # The stand-in CLI's settings reach an arm only through the fixture's
        # own `env:` block (the arm's environment is an allowlist).
        env = {"FAKE_CLAUDE_MODE": "agent_and_judge",
               "FAKE_CLAUDE_ARGV_LOG": str(self.log)}
        if batches is not None:
            self.batches.write_text(json.dumps(batches), encoding="utf-8")
            env["FAKE_CLAUDE_RATE_LIMIT_FILE"] = str(self.batches)
        (self.eval_dir / "fixture.yaml").write_text(yaml.safe_dump({
            "skill": self.SKILL, "registry": REGISTRY_URL, "prompt": "do it",
            "model": "claude-sonnet-5", "judge_rubric": "r",
            "judge": {"model": "fake-judge"}, "env": env,
            "objective_checks": [{"id": "seed-kept", "description": "d",
                                  "type": "files_unchanged",
                                  "paths": ["README.md"]}], **extra}),
            encoding="utf-8")

    def run_eval(self, *args, arm="both", environ=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GITHUB_ACTIONS", "GITHUB_RUN_ID",
                            "CLAUDE_CODE_REMOTE_SESSION_ID")}
        # A scratch HOME: the harness watches the profile an arm loads, and
        # another session writing the operator's real one mid-arm fails the
        # arm (seen once here, 2026-10-10, as `~/.claude/settings.json` changed).
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        env.update({"CLAUDE_BIN": str(FAKE_CLAUDE), "HOME": str(home),
                    "XDG_STATE_HOME": str(home / "state"),
                    "FAKE_CLAUDE_MODE": "agent_and_judge",
                    "FAKE_CLAUDE_ARGV_LOG": str(self.log), **(environ or {})})
        return subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"),
             str(self.eval_dir), "--arm", arm,
             "--registry", f"agentskills={self.registry}",
             "--results-dir", str(self.results), "--timeout", "30",
             "--timestamp", self.TS, "--budget", str(self.budget), *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300)

    def said(self, proc) -> str:
        """What a run printed, and every arm error it recorded."""
        errors = {rel: summary["error"] for rel, summary in self.summaries().items()
                  if summary.get("error")} if self.results.exists() else {}
        return f"{proc.stdout}{proc.stderr}{json.dumps(errors, indent=1)}"

    def summaries(self) -> dict:
        return {path.relative_to(self.results).as_posix():
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.results.rglob("summary.json"))}

    def summary(self, arm: str, rel: str = "summary.json") -> dict:
        return self.summaries()[f"{self.SKILL}/{self.TS}/{arm}/{rel}"]

    def cli_calls(self) -> list[list[str]]:
        return [json.loads(line)["argv"] for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def test_every_summary_of_a_run_carries_both_blocks_and_ingests(self):
        self.fixture(batches=[[meter(0.72)], [meter(0.73)], [meter(0.73)],
                              [meter(0.75, five=0.04)]])
        # `--judge always`: this test is about the usage record of a judge
        # that runs inside each trial, not about when the judge runs (#371).
        proc = self.run_eval("--trials", "2", "--judge", "always",
                             "--run-billing", "subscription",
                             "--run-runner", "routine", "--run-location", "cloud",
                             "--run-id", "20261012T170500Z-ab12cd")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        found = self.summaries()
        # Two arms, each two trials and their aggregate.
        self.assertEqual(len(found), 6, sorted(found))
        shared = set()
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assert_complete(summary)
                ingest.check_summary(summary, rel, ingest.parse_result_path(rel))
                self.assertEqual(summary["run"], {
                    "schema_version": 1, "billing": "subscription",
                    "runner": "routine", "location": "cloud",
                    "id": "20261012T170500Z-ab12cd", "session_id": None,
                    "harness_commit": run_eval.HARNESS_COMMIT,
                    "cli_version": FAKE_VERSION,
                    "source": {"billing": "flag", "runner": "flag",
                               "location": "flag", "id": "flag",
                               "session_id": None},
                    "reasons": summary["run"]["reasons"]})
                usage = summary["usage"]
                self.assertRegex(usage["ended_at"], TIME)
                self.assertLessEqual(usage["started_at"], usage["ended_at"])
                shared.add(json.dumps({k: v for k, v in usage.items()
                                       if k != "measured"}, sort_keys=True))
        # One run, one run-level record, the same in all six.
        self.assertEqual(len(shared), 1)
        # Each snapshot is in the summary of the trial whose call printed it,
        # and nowhere else: four in the run, none in either arm's aggregate.
        for arm, trial, week in (("with_skill", 1, 0.72), ("with_skill", 2, 0.73),
                                 ("without_skill", 1, 0.73),
                                 ("without_skill", 2, 0.75)):
            mine = self.summary(arm, f"trial-{trial}/summary.json")["usage"]["measured"]
            self.assertEqual(mine["meter"], {"omitted": 0, "snapshots": [
                {"at": FAKE_MESSAGE_AT, "at_source": "cli_message",
                 "call": {"role": "agent", "index": 0}, "info": meter(
                     week, five=0.04 if week == 0.75 else 0.01)}]})
            self.assertIsNone(self.summary(arm)["usage"]["measured"]["meter"])
        usage = self.summary("with_skill")["usage"]
        self.assertEqual(usage["meter"]["snapshot_count"], 4)
        self.assertEqual(usage["meter_delta"], {
            "confounded": True, "scope": "account-wide",
            "from_at": FAKE_MESSAGE_AT, "to_at": FAKE_MESSAGE_AT,
            # The last agent call and its judge call.
            "uncovered": uncovered(0, 2),
            "windows": {
                "five_hour": {"from": 0.01, "to": 0.04, "delta": 0.03,
                              "window_reset": False},
                "seven_day": {"from": 0.72, "to": 0.75, "delta": 0.03,
                              "window_reset": False}}})
        trial = self.summary("with_skill", "trial-1/summary.json")
        measured = trial["usage"]["measured"]
        self.assertEqual([(c["role"], c["index"], c["outcome"])
                          for c in measured["calls"]],
                         [("agent", 0, "ok"), ("judge", 0, "ok")])
        self.assertEqual(measured["agent"]["cost_usd"], 0.05)
        # The stand-in's modelUsage names tokens, not a cost; its result
        # carries the call's own total (#371).
        self.assertEqual(measured["judge"]["models"], {"fake-judge": {
            "input_tokens": 1234, "output_tokens": 567,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None, "cost_usd": None}})
        self.assertEqual(measured["judge"]["cost_usd"], 0.05)
        self.assertEqual(measured["judge"]["num_turns"], 2)
        self.assertEqual(measured["guard"], no_calls("guard"))
        aggregate = self.summary("with_skill")["usage"]["measured"]
        self.assertEqual((aggregate["scope"], aggregate["calls"]), ("arm", None))
        self.assertEqual((aggregate["agent"]["calls"], aggregate["agent"]["cost_usd"],
                          aggregate["judge"]["calls"]), (2, 0.1, 2))

    def test_judge_calls_that_print_no_meter_event_are_counted_as_uncovered(self):
        # The default (`--judge auto`) on a tie: no judge call until both
        # arms' trials are scored, so all four come after the last agent
        # call (#371). Here the judge prints no meter event (the meter file
        # is only in the arms' environment), so the agent call's start is
        # the run's last snapshot and the four judge calls are counted as
        # outside the delta. A judge that prints one is read as it starts
        # (`test_a_deferred_judge_call_is_read_as_it_starts_like_an_agent_call`).
        self.fixture(batches=[[meter(0.72)], [meter(0.73)], [meter(0.73)],
                              [meter(0.75, five=0.04)]])
        proc = self.run_eval("--trials", "2", "--run-billing", "subscription")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        found = self.summaries()
        self.assertEqual(len(found), 6, sorted(found))
        shared = set()
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assert_complete(summary)
                ingest.check_summary(summary, rel, ingest.parse_result_path(rel))
                usage = summary["usage"]
                shared.add(json.dumps({k: v for k, v in usage.items()
                                       if k != "measured"}, sort_keys=True))
                # The last agent call and the four judge calls.
                self.assertEqual(usage["meter_delta"]["uncovered"],
                                 uncovered(0, 5))
                self.assertEqual(usage["meter"]["snapshot_count"], 4)
        self.assertEqual(len(shared), 1)
        for arm in ("with_skill", "without_skill"):
            for trial in (1, 2):
                rel = f"trial-{trial}/summary.json"
                with self.subTest(arm=arm, trial=trial):
                    measured = self.summary(arm, rel)["usage"]["measured"]
                    self.assertEqual([(c["role"], c["index"], c["outcome"])
                                      for c in measured["calls"]],
                                     [("agent", 0, "ok"), ("judge", 0, "ok")])
                    self.assertEqual(measured["ended_at"],
                                     measured["calls"][1]["ended_at"])
                    self.assertEqual((measured["judge"]["calls"],
                                      measured["judge"]["cost_usd"]), (1, 0.05))
                    self.assertEqual(len(measured["meter"]["snapshots"]), 1)
            aggregate = self.summary(arm)["usage"]["measured"]
            self.assertEqual((aggregate["agent"]["calls"],
                              aggregate["judge"]["calls"]), (2, 2))
        # Every agent call came before any judge call.
        kinds = ["judge" if "fake-judge" in argv else "agent"
                 for argv in self.cli_calls() if "--version" not in argv]
        self.assertEqual(kinds, ["agent"] * 4 + ["judge"] * 4)

    def metered(self, *args, **kw):
        """`run_eval`, with the meter file also in the process environment:
        the judge inherits that, while an arm sees only its fixture's `env:`."""
        return self.run_eval(*args, environ={
            "FAKE_CLAUDE_RATE_LIMIT_FILE": str(self.batches)}, **kw)

    def assert_judge_is_metered(self, calls: list) -> None:
        judges = [a for a in calls if "fake-judge" in a]
        self.assertEqual(len(judges), 4, calls)
        for argv in judges:
            self.assertIn("--verbose", argv)

    def test_a_deferred_judge_call_is_read_as_it_starts_like_an_agent_call(self):
        # The default (`--judge auto`) on a tie: the four judge calls come
        # after the last agent call, and each prints a meter event (#370,
        # #371), so the delta reaches the last judge call.
        self.fixture(batches=[[meter(0.72)], [meter(0.73)], [meter(0.74)],
                              [meter(0.75)], [meter(0.76)], [meter(0.77)],
                              [meter(0.78)], [meter(0.79, five=0.04)]])
        proc = self.metered("--trials", "2", "--run-billing", "subscription")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        calls = [a for a in self.cli_calls() if "--version" not in a]
        # No call was added: four agent calls, four judge calls.
        self.assertEqual(len(calls), 8, calls)
        self.assert_judge_is_metered(calls)
        found = self.summaries()
        self.assertEqual(len(found), 6, sorted(found))
        for rel, summary in found.items():
            with self.subTest(summary=rel):
                self.assert_complete(summary)
                ingest.check_summary(summary, rel, ingest.parse_result_path(rel))
                usage = summary["usage"]
                self.assertEqual(usage["meter"], {
                    "snapshot_count": 8, "unreadable_events": 0,
                    "not_sampled": {"run_end": run_eval._NO_RUN_END}})
                self.assertEqual(usage["meter_delta"]["uncovered"], uncovered(0, 1))
                self.assertEqual(usage["meter_delta"]["windows"]["seven_day"],
                                 {"from": 0.72, "to": 0.79, "delta": 0.07,
                                  "window_reset": False})
        weeks = {}
        for arm in ("with_skill", "without_skill"):
            for trial in (1, 2):
                snaps = self.summary(arm, f"trial-{trial}/summary.json")[
                    "usage"]["measured"]["meter"]["snapshots"]
                self.assertEqual([s["call"] for s in snaps], [
                    {"role": "agent", "index": 0}, {"role": "judge", "index": 0}])
                weeks[arm, trial] = [
                    s["info"]["unifiedWindows"]["seven_day"]["utilization"]
                    for s in snaps]
        self.assertEqual(weeks, {
            ("with_skill", 1): [0.72, 0.76], ("with_skill", 2): [0.73, 0.77],
            ("without_skill", 1): [0.74, 0.78], ("without_skill", 2): [0.75, 0.79]})

    def test_a_judge_inside_each_trial_is_read_as_it_starts(self):
        # One batch per call, in the order the calls are made: each trial's
        # agent call and then its judge call.
        self.fixture(batches=[[meter(0.72 + 0.01 * i)] for i in range(8)])
        proc = self.metered("--trials", "2", "--judge", "always",
                            "--run-billing", "subscription")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        calls = [a for a in self.cli_calls() if "--version" not in a]
        self.assertEqual(len(calls), 8, calls)
        self.assert_judge_is_metered(calls)
        weeks = []
        for arm in ("with_skill", "without_skill"):
            for trial in (1, 2):
                summary = self.summary(arm, f"trial-{trial}/summary.json")
                ingest.check_summary(summary, "x", ingest.parse_result_path(
                    f"{self.SKILL}/{self.TS}/{arm}/trial-{trial}/summary.json"))
                snaps = summary["usage"]["measured"]["meter"]["snapshots"]
                self.assertEqual([s["call"]["role"] for s in snaps],
                                 ["agent", "judge"])
                weeks += [s["info"]["unifiedWindows"]["seven_day"]["utilization"]
                          for s in snaps]
        self.assertEqual(sorted(weeks), [0.72 + 0.01 * i for i in range(8)])
        usage = self.summary("with_skill")["usage"]
        self.assertEqual(usage["meter"]["snapshot_count"], 8)
        self.assertEqual(usage["meter_delta"]["uncovered"], uncovered(0, 1))
        self.assertEqual(usage["meter"]["not_sampled"],
                         {"run_end": run_eval._NO_RUN_END})

    def test_telemetry_adds_no_cli_call(self):
        # One version call, then an agent call and a judge call per arm: the
        # five calls this run made before it recorded any usage.
        self.fixture(batches=[[meter(0.72)], [meter(0.73)]])
        proc = self.run_eval("--run-billing", "subscription")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        calls = self.cli_calls()
        self.assertEqual(len(calls), 5, calls)
        self.assertEqual(sum("--version" in argv for argv in calls), 1)
        self.assertEqual(self.summary("with_skill")["usage"]["meter"]
                         ["snapshot_count"], 2)

    def test_no_snapshot_is_invented_when_the_cli_emits_none(self):
        self.fixture()
        proc = self.run_eval("--no-judge")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        for arm in ("with_skill", "without_skill"):
            usage = self.summary(arm)["usage"]
            self.assertEqual(usage["meter"]["snapshot_count"], 0)
            self.assertEqual(usage["measured"]["meter"],
                             {"snapshots": [], "omitted": 0})
            self.assertIsNone(usage["meter_delta"])
            self.assertIn("meter_delta", usage["reasons"])
            self.assert_complete(self.summary(arm))

    def test_an_api_billed_run_records_no_meter(self):
        self.fixture(batches=[[meter(0.72)], [meter(0.73)]])
        proc = self.run_eval("--no-judge", "--run-billing", "api")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        for rel, summary in self.summaries().items():
            self.assertIsNone(summary["usage"]["meter"])
            self.assertIsNone(summary["usage"]["measured"]["meter"])
            self.assertIn("API-billed", summary["usage"]["reasons"]["meter"])
            ingest.check_summary(summary, rel, ingest.parse_result_path(rel))

    def test_the_environment_fills_what_no_flag_gave_and_flags_win(self):
        self.fixture()
        proc = self.run_eval(
            "--no-judge", "--run-location", "local", arm="without_skill",
            environ={"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "4242",
                     "CLAUDE_CODE_REMOTE_SESSION_ID": "session_01AbC"})
        self.assertEqual(proc.returncode, 0, self.said(proc))
        run = self.summary("without_skill")["run"]
        self.assertEqual((run["billing"], run["runner"], run["location"],
                          run["id"], run["session_id"]),
                         (None, "actions", "local", "4242", "session_01AbC"))
        self.assertEqual(run["source"], {
            "billing": None, "runner": "env:GITHUB_ACTIONS", "location": "flag",
            "id": "env:GITHUB_RUN_ID",
            "session_id": "env:CLAUDE_CODE_REMOTE_SESSION_ID"})
        self.assertEqual(set(run["reasons"]), {"billing"})

    def test_asserted_quiet_is_recorded_with_who_asserted_it(self):
        self.fixture()
        proc = self.run_eval("--no-judge", "--asserted-quiet", "example-adopter",
                             arm="without_skill")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        usage = self.summary("without_skill")["usage"]
        self.assertEqual((usage["other_account_activity"],
                          usage["asserted_quiet_by"]),
                         ("asserted_quiet", "example-adopter"))

    def test_a_pre_run_refusal_still_carries_both_blocks(self):
        self.fixture(judge="not a mapping")
        proc = self.run_eval("--run-billing", "subscription")
        self.assertEqual(proc.returncode, 2, self.said(proc))
        found = self.summaries()
        self.assertEqual(len(found), 2, sorted(found))
        for summary in found.values():
            self.assert_complete(summary)
            self.assertEqual(summary["run"]["billing"], "subscription")
            self.assertIsNone(summary["run"]["cli_version"])
            self.assertRegex(summary["usage"]["ended_at"], TIME)
            self.assertEqual(summary["usage"]["measured"]["calls"], [])
        self.assertFalse(self.log.exists())
        # The refusal's report names the run like any other.
        report = (self.results / self.SKILL / self.TS / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("- Run: billing subscription, runner not recorded, ", report)

    def test_the_report_names_the_run_and_the_meter(self):
        self.fixture(batches=[[meter(0.72)], [meter(0.74)]])
        proc = self.run_eval("--no-judge", "--run-billing", "subscription",
                             "--run-runner", "workstation",
                             "--run-location", "local", "--run-id", "ws-1")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        report = (self.results / self.SKILL / self.TS / "report.md").read_text(
            encoding="utf-8").splitlines()
        usage = self.summary("with_skill")["usage"]
        self.assertIn(
            "- Run: billing subscription, runner workstation, location local, "
            f"id ws-1; started {usage['started_at']}, ended {usage['ended_at']}",
            report)
        self.assertIn(
            "- Account meter (account-wide, so anything else the account ran "
            "is in it; read as each agent, judge or guard call starts, so the "
            "1 call(s) from the last reading on are not in it): five_hour 0.01 to 0.01, "
            "seven_day 0.72 to 0.74, from 2 snapshots", report)
        self.assertEqual(report[0], f"# Eval report: {self.SKILL}")

    def test_a_report_without_a_meter_reading_says_why(self):
        self.fixture()
        proc = self.run_eval("--no-judge", "--trials", "2", arm="without_skill")
        self.assertEqual(proc.returncode, 0, self.said(proc))
        report = (self.results / self.SKILL / self.TS / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("- Run: billing not recorded, runner not recorded, "
                      "location not recorded, id not recorded; started ", report)
        self.assertIn("- Account meter: no change recorded (", report)

    def test_a_run_value_outside_the_allowed_set_is_an_argparse_error(self):
        self.fixture()
        for bad in (("--run-billing", "free"), ("--run-runner", "laptop"),
                    ("--run-location", "moon"), ("--run-id", "has space"),
                    ("--run-id", "../up"), ("--asserted-quiet", "a|b"),
                    ("--asserted-quiet", "")):
            with self.subTest(flag=bad):
                proc = self.run_eval("--no-judge", *bad)
                self.assertEqual(proc.returncode, 2, self.said(proc))
                self.assertIn(bad[0], proc.stderr)
                self.assertFalse(self.log.exists())
                self.assertFalse(self.results.exists())


# ---------------------------------------------------------------------------
# A guidance arm: the guard call is recorded, with what it cannot tell

class GuidanceArmTests(Checks):

    def test_the_guard_call_is_listed_and_its_cost_is_null_with_a_reason(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        clock = Clock()
        args = argparse.Namespace(results_dir=tmp / "results", model="m-1",
                                  timeout=30, no_judge=True, harness_version=None)
        ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
               "guidance_dir": tmp, "row": {}, "section": "s", "key": "guidance/s"}
        arm = {"name": "treatment", "mode": "section", "objective_checks": []}
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}

        def fake_guard(**kwargs):
            clock.advance(3)
            return {"ok": True, "expected": True, "observed": True, "error": None}

        def fake_agent(workspace, prompt, arm_config):
            return {"transcript": "t", "usage": {}, "cost_usd": 0.0,
                    "num_turns": 1, "duration_ms": 1, "raw": result_object()}

        run_eval._TELEMETRY.begin(flags())
        self.addCleanup(run_eval._TELEMETRY.begin)
        with mock.patch.object(run_eval, "_now", clock.now), \
             mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", fake_guard), \
             mock.patch.object(run_eval, "run_agent", fake_agent):
            run_eval._run_guidance_arm(arm, {"prompt": "do it"}, tmp / "no-seed",
                                       ctx, args, "T")
        summary = json.loads((tmp / "results" / "guidance" / "s" / "T"
                              / "treatment" / "summary.json")
                             .read_text(encoding="utf-8"))
        self.assert_complete(summary)
        measured = summary["usage"]["measured"]
        self.assertEqual(measured["calls"], [
            {"role": "guard", "index": 0, "started_at": "2026-10-12T17:00:00Z",
             "ended_at": "2026-10-12T17:00:03Z", "duration_ms": 3000,
             "outcome": "ok"}])
        self.assertEqual(measured["started_at"], "2026-10-12T17:00:00Z")
        self.assertEqual(measured["ended_at"], "2026-10-12T17:00:03Z")
        guard = measured["guard"]
        self.assertEqual((guard["calls"], guard["duration_ms"]), (1, 3000))
        for name in ROLE_NULLABLE:
            self.assertIsNone(guard[name], name)
            self.assertIn("guard", guard["reasons"][name])


# ---------------------------------------------------------------------------
# The ingester accepts both blocks and checks their shape

class IngestTests(Checks):

    TS = "20261012T170000Z"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        clock = Clock()
        self.addCleanup(run_eval._TELEMETRY.begin)
        with mock.patch.object(run_eval, "_now", clock.now):
            run_eval._TELEMETRY.begin(flags(
                run_billing="subscription", run_runner="routine",
                run_location="cloud", run_id="20261012T170500Z-ab12cd"))
            for week in (0.72, 0.73):
                run_eval._TELEMETRY.record("agent", [
                    {"started": clock.now(), "ended": clock.now(),
                     "outcome": "ok",
                     "events": [{"at": FAKE_MESSAGE_AT, "info": meter(week)}]}],
                    [result_object()])
            run_eval._write_summary(self.tmp, "s", "with_skill", self.TS, None,
                                    None, None, None, None, extra={"n": 1},
                                    harness_version="2.1.296 (Claude Code)")
            run_eval._TELEMETRY.finish()
        self.rel = f"s/{self.TS}/with_skill/summary.json"
        self.good = json.loads((self.tmp / self.rel).read_text(encoding="utf-8"))

    def check(self, summary: dict) -> None:
        ingest.check_summary(summary, self.rel, ingest.parse_result_path(self.rel))

    def changed(self, *path, value) -> dict:
        summary = copy.deepcopy(self.good)
        target = summary
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return summary

    def edited(self, *edits) -> dict:
        """`self.good` with each (path, value) applied; DROP removes the key."""
        summary = copy.deepcopy(self.good)
        for path, value in edits:
            target = summary
            for key in path[:-1]:
                target = target[key]
            if value is DROP:
                del target[path[-1]]
            else:
                target[path[-1]] = value
        return summary

    def test_a_summary_with_both_blocks_is_accepted(self):
        self.assert_complete(self.good)
        self.check(self.good)

    def test_summaries_from_before_the_judge_and_guard_were_read_still_ingest(self):
        # `not_sampled` named the judge and the guard until they were made
        # `--verbose` (#370): such a summary is still accepted.
        why = "the call is made without --verbose"
        old = self.changed("usage", "meter", "not_sampled", value={
            "judge": why, "guard": why, "run_end": why})
        self.check(old)

    def test_a_judge_or_guard_snapshot_is_accepted(self):
        self.assertEqual(self.good["usage"]["meter"]["not_sampled"],
                         {"run_end": run_eval._NO_RUN_END})
        snapshots = self.good["usage"]["measured"]["meter"]["snapshots"]
        for role in ("judge", "guard"):
            with self.subTest(role=role):
                summary = self.changed(
                    "usage", "measured", "meter", "snapshots", value=[
                        {**shot, "call": {"role": role, "index": 0}}
                        for shot in snapshots])
                self.check(summary)

    def test_values_the_harness_never_writes_are_rejected(self):
        # Review round 1 on #378: each of these passed `check_summary`.
        flood = ["x" * 4000] * 100
        usage, measured = ("usage",), ("usage", "measured")
        delta, window = (*usage, "meter_delta"), (*usage, "meter_delta", "windows",
                                                  "seven_day")
        snapshot = (*measured, "meter", "snapshots", 0)
        cases = {
            "concurrent_runs": [((*usage, "concurrent_runs"), flood),
                                ((*usage, "reasons", "concurrent_runs"), DROP)],
            "limit_promotion": [((*usage, "limit_promotion"), flood),
                                ((*usage, "reasons", "limit_promotion"), DROP)],
            "price_table_version": [((*usage, "price_table_version"), flood),
                                    ((*usage, "reasons", "price_table_version"),
                                     DROP)],
            "outer_session.usage": [((*usage, "outer_session", "usage"), flood)],
            "window.from": [((*window, "from"), "text")],
            "window.delta": [((*window, "delta"), "big")],
            "window.delta is not to minus from": [((*window, "delta"), 0.5)],
            "from_at": [((*delta, "from_at"), "whenever")],
            "windows empty": [((*delta, "windows"), {})],
            "uncovered": [((*delta, "uncovered"), {"calls_before_first_snapshot": -1,
                                                  "calls_from_last_snapshot": "x"})],
            "snapshot role": [((*snapshot, "call", "role"), ["agent"])],
            "snapshot index": [((*snapshot, "call", "index"), -5)],
            "not_sampled": [((*usage, "meter", "not_sampled"), ["judge"])],
            "not_sampled role": [((*usage, "meter", "not_sampled"),
                                  {"someone": "why"})],
            "source of another field": [(("run", "source", "runner"),
                                         "env:GITHUB_ACTIONS")],
            "env source of billing": [(("run", "source", "billing"),
                                       "env:ANTHROPIC_API_KEY")],
            "flag source of session_id": [
                (("run", "session_id"), "session_01AbC"),
                (("run", "source", "session_id"), "flag"),
                (("run", "reasons", "session_id"), DROP)],
            "calls and role count": [((*measured, "calls"), [])],
            "ended before started": [((*usage, "ended_at"), "2026-10-12T16:59:59Z")],
            "impossible time": [((*usage, "started_at"), "9999-99-99T99:99:99Z")],
            "measured ended before started": [
                ((*measured, "ended_at"), "2026-10-12T16:59:59Z")],
            "call ended before started": [
                ((*measured, "calls", 0, "ended_at"), "2026-10-12T16:59:59Z")],
            "empty cli_version": [(("run", "cli_version"), "")],
            "empty outcome": [((*measured, "calls", 0, "outcome"), "")],
            "outcome with a space": [((*measured, "calls", 0, "outcome"), "it broke")],
            "delta without two snapshots": [((*usage, "meter", "snapshot_count"), 1)],
            "more snapshots than the run": [((*usage, "meter", "snapshot_count"), 2),
                                            ((*measured, "meter", "omitted"), 5)],
        }
        for name, edits in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ingest.Rejected):
                    self.check(self.edited(*edits))

    def test_no_single_changed_value_makes_the_ingester_crash(self):
        # Every node of both blocks, replaced by each of these in turn: the
        # ingester answers or raises Rejected, never anything else.
        values = (None, "x", "", -1, 1.5, 10 ** 400, True, [], {}, ["x"],
                  {"a": 1}, "9999-99-99T99:99:99Z")

        def paths(node, prefix):
            yield prefix
            for key, value in (node.items() if isinstance(node, dict) else
                               enumerate(node) if isinstance(node, list) else ()):
                yield from paths(value, (*prefix, key))

        tried = 0
        for block in ("run", "usage"):
            for path in paths(self.good[block], (block,)):
                for value in values:
                    tried += 1
                    try:
                        self.check(self.edited((path, value)))
                    except ingest.Rejected:
                        pass
        # `not_sampled` lost its judge and guard nodes: 2 paths of 12 values
        # each, 24 fewer tries than before.
        self.assertGreater(tried, 1950)

    def test_a_call_that_has_not_ended_is_rejected(self):
        with self.assertRaises(ingest.Rejected):
            self.check(self.edited(
                (("usage", "measured", "calls", 0, "ended_at"), None)))

    def test_a_source_must_be_one_the_harness_reads_for_that_field(self):
        # The env sources the harness has, each on its own field and value.
        self.check(self.edited(
            (("run", "runner"), "actions"),
            (("run", "source", "runner"), "env:GITHUB_ACTIONS"),
            (("run", "id"), "4242"), (("run", "source", "id"), "env:GITHUB_RUN_ID"),
            (("run", "session_id"), "session_01AbC"),
            (("run", "source", "session_id"), "env:CLAUDE_CODE_REMOTE_SESSION_ID"),
            (("run", "reasons", "session_id"), DROP),
            (("usage", "outer_session", "id"), "session_01AbC")))
        with self.assertRaises(ingest.Rejected):
            # GITHUB_ACTIONS observes the `actions` runner and nothing else.
            self.check(self.edited(
                (("run", "runner"), "workstation"),
                (("run", "source", "runner"), "env:GITHUB_ACTIONS")))

    def test_a_summary_with_neither_block_still_ingests(self):
        old = {k: v for k, v in self.good.items() if k not in ("run", "usage")}
        self.check(old)

    def test_one_block_without_the_other_is_rejected(self):
        for name in ("run", "usage"):
            partial = {k: v for k, v in self.good.items() if k != name}
            with self.assertRaisesRegex(ingest.Rejected, "come together"):
                self.check(partial)

    def test_an_unknown_runner_is_rejected(self):
        with self.assertRaisesRegex(ingest.Rejected, "run.runner"):
            self.check(self.changed("run", "runner", value="laptop"))

    def test_other_values_outside_the_run_blocks_shape_are_rejected(self):
        for path, value in (
                (("run", "billing"), "free"),
                (("run", "location"), "moon"),
                (("run", "schema_version"), 2),
                (("run", "id"), "has space"),
                (("run", "harness_commit"), "abc123"),
                (("run", "source", "billing"), "guess"),
                (("run", "source", "billing"), None),
                (("run", "extra"), 1),
                (("run", "reasons"), {})):
            with self.subTest(path=path, value=value):
                with self.assertRaises(ingest.Rejected):
                    self.check(self.changed(*path, value=value))

    def test_a_null_without_a_reason_is_rejected(self):
        with self.assertRaises(ingest.Rejected):
            self.check(self.changed("run", "billing", value=None))
        with self.assertRaises(ingest.Rejected):
            self.check(self.changed("usage", "reasons", value={}))
        with self.assertRaises(ingest.Rejected):
            self.check(self.changed("usage", "measured", "judge", "reasons",
                                    value={}))

    def test_a_meter_delta_that_is_not_labeled_confounded_is_rejected(self):
        self.assertIsNotNone(self.good["usage"]["meter_delta"])
        for key, value in (("confounded", False), ("scope", "this run")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ingest.Rejected, "meter_delta"):
                    self.check(self.changed("usage", "meter_delta", key,
                                            value=value))

    def test_an_api_billed_run_may_not_carry_a_meter(self):
        summary = self.changed("run", "billing", value="api")
        with self.assertRaisesRegex(ingest.Rejected, "meter"):
            self.check(summary)

    def test_other_values_outside_the_usage_blocks_shape_are_rejected(self):
        snapshots = self.good["usage"]["measured"]["meter"]["snapshots"]
        for path, value in (
                (("usage", "schema_version"), 2),
                (("usage", "started_at"), "yesterday"),
                (("usage", "other_account_activity"), "quiet"),
                (("usage", "asserted_quiet_by"), "my"),
                (("usage", "outer_session", "note"), "x" * 5000),
                (("usage", "extra"), 1),
                (("usage", "measured", "meter", "snapshots"), snapshots * 7),
                (("usage", "measured", "meter", "snapshots", 0, "info"), "text"),
                (("usage", "measured", "meter", "snapshots", 0, "at_source"),
                 "guess"),
                (("usage", "measured", "meter", "snapshots", 0, "at_source"),
                 "call_end"),
                (("usage", "measured", "meter", "snapshots", 0, "call"),
                 {"role": "agent", "arm": "a", "index": 0}),
                (("usage", "measured", "meter"), None),
                (("usage", "meter", "snapshots"), snapshots),
                (("usage", "meter", "snapshot_count"), "two"),
                (("usage", "measured", "scope"), "run"),
                # An arm aggregate lists no snapshots of its own.
                (("usage", "measured", "scope"), "arm"),
                (("usage", "measured", "agent", "cost_usd"), -1),
                (("usage", "measured", "agent", "calls"), "one"),
                (("usage", "measured", "agent", "models"),
                 {"m": {"input_tokens": 1}}),
                (("usage", "measured", "calls"), [{"role": "agent"}])):
            with self.subTest(path=path):
                with self.assertRaises(ingest.Rejected):
                    self.check(self.changed(*path, value=value))

    def test_the_ingesters_constants_match_the_harness(self):
        self.assertEqual(ingest.RUN_CHOICES, run_eval.RUN_CHOICES)
        self.assertEqual(ingest.RUN_NAME_RE.pattern, run_eval.RUN_NAME.pattern)
        self.assertEqual(ingest.MAX_METER_SNAPSHOTS, run_eval.METER_MAX_SNAPSHOTS)
        self.assertEqual(ingest.USAGE_ROLES, run_eval.USAGE_ROLES)
        self.assertEqual(ingest.MAX_USAGE_CALLS, run_eval.USAGE_MAX_CALLS)
        # The same observations, per field: source -> the value it implies.
        self.assertEqual(ingest.RUN_ENV, {
            field: {f"env:{name}": meaning for name, _, meaning in observed}
            for field, observed in run_eval.RUN_ENV.items()})
        # The fields a flag can set are the `--run-*` options `main` declares.
        usage = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "run_eval.py"), "--help"],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertEqual(set(re.findall(r"--run-([a-z]+)", usage.stdout)),
                         set(ingest.RUN_FLAGGED))


# ---------------------------------------------------------------------------
# eval.yml says what it is

class EvalWorkflowTests(unittest.TestCase):

    def test_the_actions_run_names_its_billing_runner_and_location(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "eval.yml").read_text(encoding="utf-8"))
        scripts = [step["run"] for job in workflow["jobs"].values()
                   for step in job.get("steps", [])
                   if "harness/run_eval.py" in step.get("run", "")]
        self.assertEqual(len(scripts), 1, scripts)
        words = scripts[0].replace("\\\n", " ").split()
        for flag, value in (("--run-billing", "api"), ("--run-runner", "actions"),
                            ("--run-location", "cloud")):
            self.assertEqual(words[words.index(flag) + 1], value, flag)
        # The run id is this workflow run's own, read from the environment.
        self.assertNotIn("--run-id", words)


if __name__ == "__main__":
    unittest.main()
