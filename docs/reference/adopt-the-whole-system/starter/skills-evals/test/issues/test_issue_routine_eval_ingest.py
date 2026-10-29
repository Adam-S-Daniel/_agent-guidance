#!/usr/bin/env python3
"""ADR 0010 decision 2: ingest a routine's `claude/eval-<run id>` branch.

`scripts/ingest_routine_results.py` validates the branch (untrusted: an AI
run wrote it) and stages its results; `.github/workflows/routine-eval-ingest.yml`
runs it from the default branch on the `workflow_run` of
`.github/workflows/routine-eval-results-pushed.yml`. What is pinned here:

  * the validator accepts a branch that only adds harness-shaped result files
    under `<root>/<run id>/`, stages exactly those bytes at
    `routine-results/<run id>/...`, and keeps only KEEP_LONG_TERM;
  * it rejects the whole branch on any modified, deleted or out-of-place
    path (a workflow, a script), a symlink, a submodule, an executable bit,
    raw.json, a second root, a run id that disagrees with the branch name,
    an over-cap file, non-UTF-8 or control characters, and every summary,
    trace or report schema violation tested below, and never quotes file
    content in its message;
  * `resolve` takes the branch only from a same-repository push or a
    dispatch input, through a strict pattern;
  * `place` is idempotent and never overwrites a differing file;
  * both workflows, parsed with PyYAML (never line-scanned), hold the
    trust boundary: workflow_run for the writing job, `permissions: {}` at
    the top and `contents: write` only on `ingest`, bare-SHA pins with no
    trailing comment, no `${{ }}` in any `run:`, no concurrency group, and
    every push aimed at persistent/eval-results.

Fixtures are built in-test in temporary git repositories (example.com
only). Discovered and run by test/run_tests.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
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
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
INGEST = WORKFLOWS / "routine-eval-ingest.yml"
SIGNAL = WORKFLOWS / "routine-eval-results-pushed.yml"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402

import cli_json  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import run_eval  # noqa: E402

RUN_ID = "20261006T194256Z-f944ea"
BRANCH = f"claude/eval-{RUN_ID}"
STAMP = "20261006T194334Z"
MARKER = "do-not-echo-this-marker"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-10-06T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-06T00:00:00Z",
    # No background auto-gc: it would outlive the test and race the cleanup.
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
}


def summary(arm="with_skill", **changes) -> dict:
    doc = {
        "skill": "writing-adrs", "arm": arm, "timestamp": STAMP,
        "error": None,
        "agent": {"cost_usd": 0.25, "num_turns": 14, "duration_ms": 71356,
                  "usage": {"input_tokens": 26, "output_tokens": 5542,
                            "iterations": [{"type": "message"}]}},
        "objective_checks": [{"id": "exactly-one-adr-file", "passed": True,
                              "detail": "1 file(s) matched"}],
        "judge": {"dimensions": [{"name": "restraint", "score": 10,
                                  "rationale": "made no unrelated edits"}],
                  "overall": 9.1},
        "harness": {"name": "claude-code", "version": "2.1.292 (Claude Code)",
                    "permission_mode": "auto", "effort": None},
        "models_used": ["model-a", "model-c"], "judge_models_used": ["model-b"],
        # A parent and a subagent on another model, as run_eval writes them.
        "model_tokens": {
            "model-a": {"input_tokens": 26, "output_tokens": 5542,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 200,
                        "canonical_model": None},
            "model-c": {"input_tokens": 10, "output_tokens": 20,
                        "cache_read_input_tokens": None,
                        "cache_creation_input_tokens": 0,
                        "canonical_model": "model-c"}},
        "cross_model": {"model": "model-a", "canonical_model": None,
                        "complete": True, "dropped": 0, "other_share": None,
                        "threshold": 0.5, "flagged": None},
        "fixture": "bootstrap", "n": 1,
    }
    doc.update(changes)
    return doc


def counts(inp, out, read, write, canonical=None) -> dict:
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write, "canonical_model": canonical}


def flagged_summary(**cross) -> dict:
    """A summary whose subagent did 900 of 1000 tokens, consistently: the
    share and flag are the ones its counts give."""
    doc = summary(models_used=["claude-opus-4-8", "claude-sonnet-5"])
    doc["model_tokens"] = {
        "claude-opus-4-8": counts(100, 200, 500, 100, "claude-opus-4-8"),
        "claude-sonnet-5[1m]": counts(10, 20, 50, 20)}
    doc["cross_model"] = {"model": "claude-sonnet-5",
                          "canonical_model": "claude-sonnet-5",
                          "complete": True, "dropped": 0, "other_share": 0.9,
                          "threshold": 0.5, "flagged": True, **cross}
    return doc


def old_summary(arm="with_skill") -> dict:
    """The shape every summary had before per-model tokens and effort."""
    doc = summary(arm)
    del doc["harness"]["effort"], doc["model_tokens"], doc["cross_model"]
    return doc


def trace(**changes) -> dict:
    doc = {"schema_version": 1,
           "limits": {"input_chars": 200, "output_chars": 300,
                      "max_bytes": 65536},
           "calls": 1,
           "events": [
               {"call": 0, "kind": "tool_use", "id": "toolu_1", "name": "Bash",
                "input": "curl https://example.com/api", "input_chars": 28,
                "subagent": False},
               {"call": 0, "kind": "tool_result", "id": "toolu_1",
                "is_error": False, "output": "ok", "output_chars": 2,
                "subagent": False}],
           "omitted_events": 0}
    doc.update(changes)
    return doc


REPORT = "# Eval report: writing-adrs\n\n- Timestamp: 20261006T194334Z\n"


def good_files(root="eval-results") -> dict[str, bytes]:
    base = f"{root}/{RUN_ID}/writing-adrs/{STAMP}"
    files = {f"{base}/report.md": REPORT.encode()}
    for arm in ("with_skill", "without_skill"):
        files[f"{base}/bootstrap/{arm}/summary.json"] = \
            json.dumps(summary(arm), indent=2).encode()
        files[f"{base}/bootstrap/{arm}/transcripts/tool_trace.json"] = \
            json.dumps(trace(), indent=2).encode()
    return files


class GitCase(unittest.TestCase):
    """A scratch repository with a `main` and a results branch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q", "-b", "main")
        self.write({"README.md": b"see https://example.com\n",
                    ".github/workflows/ci.yml": b"name: CI\n",
                    "scripts/tool.py": b"print('main')\n"})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "main")
        self.git("checkout", "-q", "-b", BRANCH)

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, files: dict[str, bytes]) -> None:
        for name, data in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def commit(self, files: dict[str, bytes] | None = None) -> str:
        self.write(good_files() if files is None else files)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "results")
        return self.git("rev-parse", "HEAD")

    def validate(self, branch=BRANCH, expect=None, out=None):
        out = out or self.tmp / "staged"
        return ingest.validate(str(self.repo), "main", branch, branch, expect,
                               out)

    def assertRejected(self, pattern=None, **kwargs):
        with self.assertRaises(ingest.Rejected) as caught:
            self.validate(**kwargs)
        if pattern:
            self.assertRegex(str(caught.exception), pattern)
        self.assertNotIn(MARKER, str(caught.exception))
        staged = self.tmp / "staged"
        self.assertFalse(staged.exists() and any(staged.rglob("*.json")),
                         "a rejected branch stages nothing")
        return caught.exception


class AcceptTests(GitCase):

    def test_accepts_the_canonical_root_and_stages_exact_kept_bytes(self):
        files = good_files()
        sha = self.commit(files)
        out = self.tmp / "staged"
        result = self.validate(expect=sha, out=out)
        self.assertEqual(result, {"run_id": RUN_ID, "sha": sha, "files": "3"})
        staged = {str(p.relative_to(out)): p.read_bytes()
                  for p in out.rglob("*") if p.is_file()}
        prefix = f"{ingest.SOURCE_ROOT}/"
        expected = {"routine-results/" + name[len(prefix):]: data
                    for name, data in files.items()
                    if not name.endswith("tool_trace.json")}
        self.assertEqual(staged, expected)

    def test_legacy_root_only_for_its_own_run(self):
        legacy = "20261006T192432Z-93755e"
        self.assertEqual(ingest.LEGACY_ROOTS,
                         {(legacy, "c3521ee53809daea4570e5fe80f3ac847959f88a"): "results"})
        branch = f"claude/eval-{legacy}"
        self.git("checkout", "-q", "-b", branch, "main")
        sha = self.commit({n.replace(f"eval-results/{RUN_ID}", f"results/{legacy}"): d
                           for n, d in good_files().items()})
        with mock.patch.object(ingest, "LEGACY_ROOTS", {(legacy, sha): "results"}):
            result = ingest.validate(str(self.repo), "main", branch, branch, None,
                                     self.tmp / "staged")
            self.git("commit", "-q", "--allow-empty", "-m", "Repush the same run")
            with self.assertRaisesRegex(ingest.Rejected, "not under"):
                ingest.validate(str(self.repo), "main", branch, branch, None,
                                self.tmp / "repushed")
        self.assertEqual(result["run_id"], legacy)
        self.assertTrue((self.tmp / "staged" / "routine-results" / legacy)
                        .is_dir())

    def test_old_and_new_summary_shapes_are_accepted(self):
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/bootstrap"
        flagged = flagged_summary()
        flagged["harness"]["effort"] = "xhigh"
        for name, doc in (("old", old_summary()), ("new", summary()),
                          ("flagged", flagged)):
            with self.subTest(shape=name):
                self.git("reset", "-q", "--hard", "main")
                self.commit({f"{base}/with_skill/summary.json":
                             json.dumps(doc).encode()})
                self.assertEqual(self.validate(out=self.tmp / name)["files"], "1")

    def test_rejects_contradictory_token_accounting(self):
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/bootstrap"
        incomplete = {"complete": False, "dropped": 1, "other_share": None,
                      "flagged": None}
        cases = [
            ("complete with entries dropped", flagged_summary(dropped=1)),
            ("complete with entries dropped",
             summary(model_tokens={}, cross_model=dict(
                 summary()["cross_model"]))),
            ("share or flag for an incomplete",
             flagged_summary(complete=False, dropped=1)),
            ("share or flag for an incomplete",
             flagged_summary(**dict(incomplete, flagged=False))),
            ("threshold is not 0.5", flagged_summary(threshold=1)),
            ("threshold is not 0.5", flagged_summary(threshold=0.95)),
            ("flagged does not match", flagged_summary(flagged=False)),
            ("flagged does not match", flagged_summary(flagged=None)),
            # A share that would justify its flag, but not the counts'.
            ("other_share does not match",
             flagged_summary(other_share=0.4, flagged=False)),
            ("other_share does not match", flagged_summary(other_share=0.1,
                                                           flagged=False)),
            ("other_share does not match", flagged_summary(other_share=None,
                                                           flagged=None)),
            ("other_share does not match",
             summary(cross_model=dict(summary()["cross_model"],
                                      other_share=0.9, flagged=True))),
            ("canonical_model does not match",
             flagged_summary(canonical_model="claude-opus-4-8")),
            ("canonical_model does not match",
             flagged_summary(model="sonnet")),
            ("come together", summary(cross_model="DELETE")),
            ("come together", summary(model_tokens="DELETE")),
        ]
        for pattern, doc in cases:
            with self.subTest(pattern=pattern, cross=str(doc.get("cross_model"))[:80]):
                for key, value in list(doc.items()):
                    if value == "DELETE":
                        del doc[key]
                # Each case on its own: one wrongly accepted must not leave
                # staged files that fail the cases after it.
                shutil.rmtree(self.tmp / "staged", ignore_errors=True)
                self.git("reset", "-q", "--hard", "main")
                self.commit({f"{base}/with_skill/summary.json":
                             json.dumps(doc).encode()})
                self.assertRejected(pattern)

    def near_half(self, own, other, share, flagged) -> dict:
        doc = flagged_summary(other_share=share, flagged=flagged)
        doc["model_tokens"] = {"claude-opus-4-8": counts(other, 0, 0, 0),
                               "claude-sonnet-5": counts(own, 0, 0, 0)}
        return doc

    def test_the_flag_follows_the_counts_not_the_supplied_share(self):
        # Both shares sit within the float tolerance of the true one, but
        # the flag must be the counts' own: other tokens over half of all.
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/bootstrap"
        rejected = {
            "just over half, flag withheld": self.near_half(
                499999999999, 500000000001, 0.5, False),
            "exactly half, flag raised": self.near_half(
                500000000000, 500000000000, 0.5000000005, True),
        }
        for name, doc in rejected.items():
            with self.subTest(case=name):
                shutil.rmtree(self.tmp / "staged", ignore_errors=True)
                self.git("reset", "-q", "--hard", "main")
                self.commit({f"{base}/with_skill/summary.json":
                             json.dumps(doc).encode()})
                self.assertRejected("flagged does not match the model_tokens")
        accepted = {
            "just over half": self.near_half(499999999999, 500000000001,
                                             0.500000000001, True),
            "exactly half": self.near_half(500000000000, 500000000000, 0.5,
                                           False),
        }
        for name, doc in accepted.items():
            with self.subTest(case=name):
                self.git("reset", "-q", "--hard", "main")
                self.commit({f"{base}/with_skill/summary.json":
                             json.dumps(doc).encode()})
                out = self.tmp / name.replace(" ", "-")
                self.assertEqual(self.validate(out=out)["files"], "1")

    def test_an_incomplete_or_unresolved_accounting_without_a_share_is_accepted(self):
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/bootstrap"
        cases = {
            "dropped": flagged_summary(complete=False, dropped=2,
                                       other_share=None, flagged=None),
            "alias": flagged_summary(model="sonnet", canonical_model=None,
                                     other_share=None, flagged=None),
            "no result": summary(model_tokens={}, cross_model=dict(
                summary()["cross_model"], complete=False)),
        }
        for name, doc in cases.items():
            with self.subTest(case=name):
                self.git("reset", "-q", "--hard", "main")
                self.commit({f"{base}/with_skill/summary.json":
                             json.dumps(doc).encode()})
                self.assertEqual(self.validate(out=self.tmp / name)["files"], "1")

    def test_trials_and_guidance_keys_are_accepted(self):
        base = f"eval-results/{RUN_ID}"
        trial = summary(trial=2)
        del trial["n"]
        guidance = summary("with_guidance", subject="guidance",
                           section="security", mode="section", bytes=120,
                           delivery="user", guard=None)
        del guidance["skill"], guidance["fixture"]
        self.commit({
            f"{base}/writing-adrs/{STAMP}/bootstrap/with_skill/trial-2/"
            "summary.json": json.dumps(trial).encode(),
            f"{base}/guidance/security/{STAMP}/with_guidance/summary.json":
                json.dumps(guidance).encode()})
        self.assertEqual(self.validate()["files"], "2")

    def test_a_bare_run_of_one_arm_is_accepted_as_it_is(self):
        # A bare fire (`arms: bare`, ADR 0012's 2026-10-27 addendum) pushes
        # one isolation `without_skill` arm and its trials, no `pairing`.
        # Ingest marks nothing: compute_gates sets it apart on read.
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}"
        files = {f"{base}/report.md": REPORT.encode(),
                 f"{base}/bootstrap/without_skill/summary.json":
                     json.dumps(summary("without_skill", n=3, errors=0)).encode()}
        for k in (1, 2, 3):
            trial = summary("without_skill", trial=k)
            del trial["n"]
            files[f"{base}/bootstrap/without_skill/trial-{k}/summary.json"] = \
                json.dumps(trial).encode()
        self.commit(files)
        out = self.tmp / "staged"
        self.assertEqual(self.validate(out=out)["files"], "5")
        staged = sorted(str(p.relative_to(out)) for p in out.rglob("summary.json"))
        self.assertEqual(len(staged), 4)
        self.assertFalse(any("with_skill" in name for name in staged))

    def test_keep_long_term_is_summary_and_report_only(self):
        # Owner's answer (2026-10-06): "summary + report only". A trace is
        # still validated (RejectContentTests), then dropped.
        self.assertEqual(ingest.KEEP_LONG_TERM,
                         frozenset({"summary.json", "report.md"}))
        self.commit()
        self.validate()
        kept = sorted(p.name for p in (self.tmp / "staged").rglob("*")
                      if p.is_file())
        self.assertEqual(kept, ["report.md", "summary.json", "summary.json"])

    def test_keep_long_term_is_the_one_allowlist(self):
        self.commit()
        with mock.patch.object(ingest, "KEEP_LONG_TERM",
                               frozenset({"summary.json", "tool_trace.json"})):
            self.validate()
        kept = sorted(p.name for p in (self.tmp / "staged").rglob("*")
                      if p.is_file())
        self.assertEqual(kept, ["summary.json", "summary.json",
                                "tool_trace.json", "tool_trace.json"])

    def test_real_routine_branches_parse(self):
        # The two path shapes the routine really pushed on 2026-10-06.
        for rel in (f"writing-adrs/{STAMP}/report.md",
                    f"writing-adrs/{STAMP}/bootstrap/with_skill/summary.json",
                    f"writing-adrs/{STAMP}/bootstrap/without_skill/"
                    "transcripts/tool_trace.json"):
            with self.subTest(rel=rel):
                ingest.parse_result_path(rel)


class HarnessWriterTests(GitCase):
    """The validator accepts what the harness's own writers produce, so a
    field the harness adds cannot drift past the ingest schema unnoticed
    (2026-10-20: `complete` and the `*_incomplete` event flags did)."""

    def test_accepts_files_the_harness_writes(self):
        messages = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                 "input": {"command": "curl https://example.com/" + "a" * 400}},
                {"type": "tool_use", "id": "not an id", "name": "Read",
                 "input": {"file_path": "README.md"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "is_error": True, "content": "b" * 500}]}}]
        tool_trace = cli_json.bounded_tool_trace([cli_json.tool_events(messages)])
        self.assertIn("complete", tool_trace)
        flags = {key for event in tool_trace["events"] for key in event}
        self.assertTrue({"input_incomplete", "output_incomplete"} <= flags)
        self.assertIn(None, [event["id"] for event in tool_trace["events"]])
        quiet = {k: v for k, v in os.environ.items()
                 if k not in ("GITHUB_ACTIONS", "GITHUB_RUN_ID",
                              "CLAUDE_CODE_REMOTE_SESSION_ID")}
        results = self.repo / ingest.SOURCE_ROOT / RUN_ID
        with mock.patch.dict(os.environ, quiet, clear=True):
            for arm in ("with_skill", "without_skill"):
                run_eval._write_summary(
                    results, "writing-adrs", arm, STAMP, None, None, None, None,
                    None, extra={"n": 1},
                    harness_version="2.1.296 (Claude Code)",
                    tool_trace=tool_trace)
        self.write({f"{ingest.SOURCE_ROOT}/{RUN_ID}/writing-adrs/{STAMP}/"
                    "report.md": REPORT.encode()})
        sha = self.commit({})
        self.assertEqual(self.validate(expect=sha)["files"], "3")


class InPlaceSemanticConsistencyTests(unittest.TestCase):
    """The trusted ingester checks that arm proofs agree with the frozen context."""

    @staticmethod
    def record(role="without"):
        guidance_digest = "a" * 64
        return {
            "pairing": "in_place", "role": role,
            "arm": "with_skill" if role == "with" else "without_skill",
            "skill": "sample",
            "context": {
                "repository": "owner/consumer", "revision": "1" * 40,
                "digest": "2" * 64, "lock_digest": None, "sources": [],
                "guidance": {"revision": "3" * 40, "digest": guidance_digest,
                             "bytes": 100, "sections": [],
                             "hook_digest": "4" * 64},
                "measurements": {"guidance_bytes": 100,
                                 "skill_catalog_bytes": 0,
                                 "skill_payload_bytes": 0}},
            "context_subject": {
                "kind": "skill", "registry": "owner/registry",
                "bundle": "bundle", "skill": "sample", "action": "added",
                "deployed_digest": None, "tested_digest": "5" * 64,
                "bytes": 1},
            "arm_context": {
                "skills": 1 if role == "with" else 0,
                "skills_digest": "6" * 64, "guidance_bytes": 100,
                "guidance_digest": guidance_digest,
                "subject_present": role == "with"}}

    def check(self, doc):
        key = ("guidance/sample" if doc["context_subject"]["kind"] == "guidance"
               else "sample")
        ingest.check_in_place(doc, "summary.json", {"key": key})

    @classmethod
    def guidance_record(cls, role="without"):
        doc = cls.record(role)
        doc["arm"] = "with_guidance" if role == "with" else "without_guidance"
        doc["subject"] = "guidance"
        doc["section"] = "sample"
        doc.pop("skill")
        doc["context_subject"] = {
            "kind": "guidance", "section": "sample", "file": "AGENTS.md",
            "heading": "## Sample", "action": "added",
            "start_char": 0, "end_char": 0, "start_byte": 0, "end_byte": 0,
            "bytes": 0, "deployed_digest": None, "tested_digest": "5" * 64,
            "tested_bytes": 10}
        doc["arm_context"]["guidance_bytes"] = 111 if role == "with" else 100
        if role == "with":
            doc["arm_context"]["guidance_digest"] = "7" * 64
        return doc

    def test_accepts_consistent_added_subject_arms(self):
        self.check(self.record("with"))
        self.check(self.record("without"))
        self.check(self.guidance_record("with"))
        doc = self.guidance_record("without")
        self.check(doc)

    def test_rejects_added_subject_without_arm_guidance_change(self):
        doc = self.guidance_record("without")
        doc["arm_context"]["guidance_digest"] = "7" * 64
        with self.assertRaisesRegex(ingest.Rejected, "guidance"):
            self.check(doc)

    def test_rejects_missing_in_place_hook_digest(self):
        doc = self.guidance_record()
        doc["context"]["guidance"]["hook_digest"] = None
        with self.assertRaisesRegex(ingest.Rejected, "hook digest"):
            self.check(doc)

    def test_others_digest_is_optional_and_a_digest(self):
        # #70: compute_gates pairs the arms by it; older summaries lack it.
        for role in ("with", "without"):
            doc = self.record(role)
            doc["arm_context"]["others_digest"] = "8" * 64
            self.check(doc)
            for bad in ("8" * 63, "A" * 64, 7, None):
                doc["arm_context"]["others_digest"] = bad
                with self.subTest(role=role, bad=bad), \
                        self.assertRaisesRegex(ingest.Rejected, "others digest: malformed"):
                    self.check(doc)

    def test_rejects_with_skill_arm_with_no_installed_skills(self):
        doc = self.record("with")
        doc["arm_context"]["skills"] = 0
        with self.assertRaisesRegex(ingest.Rejected, "installed skill"):
            self.check(doc)


class RejectPathTests(GitCase):

    def test_rejects_a_modified_main_file(self):
        files = good_files()
        files["README.md"] = b"changed\n"
        self.commit(files)
        self.assertRejected(r"'README\.md': modified or deleted")

    def test_rejects_a_deleted_main_file(self):
        self.commit()
        self.git("rm", "-q", "scripts/tool.py")
        self.git("commit", "-q", "-m", "delete")
        self.assertRejected(r"status D")

    def test_rejects_an_added_workflow_or_script(self):
        for extra in (".github/workflows/evil.yml", "scripts/new.py",
                      "eval-results/x.py", "harness/new.py"):
            with self.subTest(extra=extra):
                self.git("reset", "-q", "--hard", "main")
                files = good_files()
                files[extra] = b"name: x\n"
                self.commit(files)
                self.assertRejected()

    def test_rejects_a_symlink(self):
        self.commit()
        link = self.repo / f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/" \
            "bootstrap/with_skill/transcripts/link"
        os.symlink("/etc/hostname", link)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "link")
        self.assertRejected(r"mode 120000")

    def test_rejects_a_submodule(self):
        self.commit()
        sha = self.git("rev-parse", "main")
        self.git("update-index", "--add", "--cacheinfo",
                 f"160000,{sha},eval-results/{RUN_ID}/sub")
        self.git("commit", "-q", "-m", "gitlink")
        self.assertRejected(r"mode 160000")

    def test_rejects_an_executable_file(self):
        files = good_files()
        self.commit(files)
        name = next(n for n in files if n.endswith("report.md"))
        os.chmod(self.repo / name, 0o755)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chmod")
        self.assertRejected(r"modified or deleted|mode 100755")

    def test_rejects_raw_json_and_unknown_files(self):
        base = f"eval-results/{RUN_ID}/writing-adrs/{STAMP}"
        for extra in (f"{base}/bootstrap/with_skill/transcripts/raw.json",
                      f"{base}/notes.txt", f"{base}/bootstrap/report.md",
                      f"{base}/bootstrap/transcripts/summary.json",
                      f"eval-results/{RUN_ID}/writing-adrs/report.md"):
            with self.subTest(extra=extra):
                self.git("reset", "-q", "--hard", "main")
                files = good_files()
                files[extra] = b"{}"
                self.commit(files)
                self.assertRejected()

    def test_rejects_a_run_id_that_disagrees_with_the_branch(self):
        files = {n.replace(RUN_ID, "20261006T000000Z-000000"): d
                 for n, d in good_files().items()}
        self.commit(files)
        self.assertRejected(r"not under")

    def test_rejects_the_old_results_root_for_a_new_run(self):
        self.commit(good_files("results"))
        self.assertRejected(r"not under 'eval-results/")

    def test_rejects_a_second_root_beside_the_canonical_one(self):
        files = good_files("results")
        files.update(good_files())
        self.commit(files)
        self.assertRejected(r"not under")

    def test_rejects_the_a3f2d3_layout_with_no_run_id_directory(self):
        # claude/eval-20261006T195724Z-a3f2d3 pushed results/<key>/<ts>/...
        # with no run id directory; it stays rejected.
        branch = "claude/eval-20261006T195724Z-a3f2d3"
        self.git("checkout", "-q", "-b", branch, "main")
        self.commit({n.replace(f"eval-results/{RUN_ID}/", "results/"): d
                     for n, d in good_files().items()})
        with self.assertRaisesRegex(ingest.Rejected, "not under"):
            ingest.validate(str(self.repo), "main", branch, branch, None,
                            self.tmp / "staged")

    def test_untrusted_paths_are_escaped_in_messages(self):
        files = good_files()
        evil = (f"eval-results/{RUN_ID}/writing-adrs/{STAMP}/x\n"
                "::error::injected")
        files[evil] = b"{}"
        self.commit(files)
        exc = self.assertRejected()
        self.assertNotIn("\n", str(exc))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = ingest.main(["validate", "--repo", str(self.repo),
                                "--base", "main", "--source", BRANCH,
                                "--branch", BRANCH,
                                "--out", str(self.tmp / "staged2")])
        self.assertEqual(code, 1)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("rejected: "))

    def test_rejects_bad_branch_names(self):
        self.commit()
        for name in ("claude/eval-x", f"claude/eval-{RUN_ID}\nsha=0",
                     f"claude/eval-{RUN_ID}/x", f"feature/eval-{RUN_ID}",
                     f"claude/eval-{RUN_ID.upper()}"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ingest.Rejected, "branch name"):
                    ingest.validate(str(self.repo), "main", BRANCH, name, None,
                                    self.tmp / "staged")

    def test_rejects_a_moved_branch(self):
        self.commit()
        self.assertRejected(r"moved", expect="0" * 40)

    def test_rejects_unrelated_history_and_empty_branches(self):
        self.assertRejected(r"adds no files")
        self.git("checkout", "-q", "--orphan", "other")
        self.git("rm", "-rq", "--cached", ".")
        self.commit()
        with self.assertRaisesRegex(ingest.Rejected, "git merge-base"):
            ingest.validate(str(self.repo), "main", "other", BRANCH, None,
                            self.tmp / "staged")

    def test_rejects_over_cap_files(self):
        files = good_files()
        name = next(n for n in files if n.endswith("report.md"))
        files[name] = REPORT.encode() + b"x" * ingest.SIZE_CAPS["report.md"]
        self.commit(files)
        self.assertRejected(r"over the report\.md cap")

    def test_rejects_too_many_files(self):
        self.commit()
        with mock.patch.object(ingest, "MAX_FILES", 4):
            self.assertRejected(r"more than 4 files")


class RejectContentTests(GitCase):

    def commit_summary(self, raw: bytes):
        files = good_files()
        name = next(n for n in files if n.endswith("with_skill/summary.json"))
        files[name] = raw
        self.commit(files)

    def bad_summary(self, pattern, **changes):
        self.git("reset", "-q", "--hard", "main")
        doc = summary(**changes)
        for key, value in list(doc.items()):
            if value == "DELETE":
                del doc[key]
        self.commit_summary(json.dumps(doc).encode())
        self.assertRejected(pattern)

    def test_rejects_non_utf8_and_control_characters(self):
        for raw, pattern in ((b"\xff\xfe{}", "not UTF-8"),
                             (b'{"a": "\x1b[31m"}', "control character"),
                             (b'{"a": 1}\x00', "control character")):
            with self.subTest(raw=raw):
                self.git("reset", "-q", "--hard", "main")
                self.commit_summary(raw)
                self.assertRejected(pattern)

    def test_rejects_duplicate_keys_and_non_finite_numbers(self):
        for raw, pattern in ((b'{"arm": "a", "arm": "b"}', "duplicate"),
                             (b'{"x": NaN}', "non-finite"),
                             (b'{"x": Infinity}', "non-finite"),
                             (b'{"x": ', "not valid JSON")):
            with self.subTest(raw=raw):
                self.git("reset", "-q", "--hard", "main")
                self.commit_summary(raw)
                self.assertRejected(pattern)

    def test_rejects_summary_schema_violations(self):
        cases = [
            ("unexpected keys", {"script": "rm -rf /"}),
            ("missing keys", {"harness": "DELETE"}),
            ("arm does not match", {"arm": "without_skill"}),
            ("timestamp does not match", {"timestamp": "20261006T000000Z"}),
            ("fixture does not match", {"fixture": "other"}),
            ("skill does not match", {"skill": "other-skill"}),
            ("num_turns", {"agent": {"num_turns": "14"}}),
            ("cost_usd", {"agent": {"cost_usd": -1}}),
            ("cost_usd", {"agent": {"cost_usd": True}}),
            ("agent: unexpected", {"agent": {"exec": "x"}}),
            ("judge.overall", {"judge": {"overall": 11}}),
            ("judge score", {"judge": {"dimensions": [{"score": -2}]}}),
            ("passed not a bool", {"objective_checks": [{"id": "a",
                                                         "passed": "yes"}]}),
            ("models_used", {"models_used": ["has space"]}),
            ("models_used", {"models_used": "model-a"}),
            ("harness: missing", {"harness": {"name": "claude-code",
                                              "version": None}}),
            (r"\bn\b", {"n": 0}),
            ("trial outside", {"trial": 1}),
            ("string too long", {"agent": {"usage": {"x": "a" * 5000}}}),
            ("nested too deep", {"agent": {"usage": json.loads(
                "[" * 12 + "]" * 12)}}),
            ("error.type", {"error": {"type": 3}}),
            ("harness: unexpected", {"harness": {
                "name": "claude-code", "version": None, "permission_mode": "auto",
                "effort": None, "exec": "x"}}),
            ("harness.effort", {"harness": {
                "name": "claude-code", "version": None, "permission_mode": "auto",
                "effort": "ultra"}}),
            ("model_tokens must be", {"model_tokens": ["model-a"]}),
            ("model_tokens must be", {"model_tokens": {
                f"m-{i}": {} for i in range(17)}}),
            ("not a model id", {"model_tokens": {"has space": {}}}),
            ("model_tokens entry: unexpected", {"model_tokens": {"model-a": {
                "input_tokens": 1, "output_tokens": 1,
                "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1,
                "canonical_model": None, "costUSD": 1}}}),
            ("model_tokens entry: missing", {"model_tokens": {"model-a": {
                "input_tokens": 1}}}),
            ("model_tokens count", {"model_tokens": {"model-a": {
                "input_tokens": 10 ** 13, "output_tokens": 1,
                "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1,
                "canonical_model": None}}}),
            ("model_tokens count", {"model_tokens": {"model-a": {
                "input_tokens": -1, "output_tokens": 1,
                "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1,
                "canonical_model": None}}}),
            ("model_tokens count", {"model_tokens": {"model-a": {
                "input_tokens": "1", "output_tokens": 1,
                "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1,
                "canonical_model": None}}}),
            ("canonical_model is not", {"model_tokens": {"model-a": {
                "input_tokens": 1, "output_tokens": 1,
                "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1,
                "canonical_model": 3}}}),
            ("cross_model: missing", {"cross_model": {"model": "model-a"}}),
            ("cross_model.complete", {"cross_model": {
                "model": "model-a", "canonical_model": None, "complete": "yes",
                "dropped": 0, "other_share": None, "threshold": 0.5,
                "flagged": None}}),
            ("cross_model.other_share", {"cross_model": {
                "model": "model-a", "canonical_model": None, "complete": True,
                "dropped": 0, "other_share": 1.5, "threshold": 0.5,
                "flagged": True}}),
            ("cross_model.dropped", {"cross_model": {
                "model": "model-a", "canonical_model": None, "complete": False,
                "dropped": -1, "other_share": None, "threshold": 0.5,
                "flagged": None}}),
            ("cross_model.flagged", {"cross_model": {
                "model": "model-a", "canonical_model": None, "complete": True,
                "dropped": 0, "other_share": 0.9, "threshold": 0.5,
                "flagged": "yes"}}),
            ("cross_model.model", {"cross_model": {
                "model": "has space", "canonical_model": None, "complete": True,
                "dropped": 0, "other_share": None, "threshold": 0.5,
                "flagged": None}}),
        ]
        for pattern, changes in cases:
            with self.subTest(pattern=pattern, changes=str(changes)[:60]):
                self.bad_summary(pattern, **changes)

    def test_rejects_trace_and_report_violations(self):
        long = "a" * 400
        cases = [
            ("schema_version", trace(schema_version=2)),
            ("unknown kind", trace(events=[{"kind": "exec"}])),
            ("event.input", trace(events=[{
                "call": 0, "kind": "tool_use", "id": "t", "name": "Bash",
                "input": long, "input_chars": 400, "subagent": False}])),
            ("unexpected keys", trace(extra=1)),
            ("event: unexpected", trace(events=[{
                "call": 0, "kind": "tool_result", "id": "t", "is_error": False,
                "output": "", "output_chars": 0, "subagent": False,
                "run": "x"}])),
            ("complete not a bool", trace(complete="yes")),
            ("complete with omitted", trace(complete=True, omitted_events=1)),
            ("input_incomplete not true", trace(events=[{
                "call": 0, "kind": "tool_use", "id": "t", "name": "Bash",
                "input": "", "input_chars": 0, "input_incomplete": False,
                "subagent": False}])),
            ("event: unexpected", trace(events=[{
                "call": 0, "kind": "tool_use", "id": "t", "name": "Bash",
                "input": "", "input_chars": 0, "output_incomplete": True,
                "subagent": False}])),
        ]
        for pattern, doc in cases:
            with self.subTest(pattern=pattern):
                self.git("reset", "-q", "--hard", "main")
                files = good_files()
                name = next(n for n in files if n.endswith("tool_trace.json"))
                files[name] = json.dumps(doc).encode()
                self.commit(files)
                self.assertRejected(pattern)
        self.git("reset", "-q", "--hard", "main")
        files = good_files()
        name = next(n for n in files if n.endswith("report.md"))
        files[name] = f"<script>{MARKER}</script>\n".encode()
        self.commit(files)
        self.assertRejected("eval title")

    def test_cli_rejection_never_quotes_content(self):
        files = good_files()
        name = next(n for n in files if n.endswith("with_skill/summary.json"))
        files[name] = json.dumps(summary(arm=MARKER)).encode()
        self.commit(files)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = ingest.main(["validate", "--repo", str(self.repo),
                                "--base", "main", "--source", BRANCH,
                                "--branch", BRANCH,
                                "--out", str(self.tmp / "staged")])
        self.assertEqual(code, 1)
        self.assertIn("rejected:", err.getvalue())
        self.assertNotIn(MARKER, err.getvalue())


class ResolveTests(unittest.TestCase):

    def resolve(self, name, event):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump(event, handle)
        self.addCleanup(os.unlink, handle.name)
        return ingest.resolve(name, handle.name)

    def run_event(self, **changes):
        run = {"event": "push", "head_branch": BRANCH, "head_sha": "a" * 40,
               "head_repository": {"full_name": "owner/repo"}}
        run.update(changes)
        return {"workflow_run": run, "repository": {"full_name": "owner/repo"}}

    def test_workflow_run_from_a_same_repo_push(self):
        self.assertEqual(self.resolve("workflow_run", self.run_event()),
                         {"branch": BRANCH, "sha": "a" * 40, "run_id": RUN_ID})

    def test_dispatch_input(self):
        self.assertEqual(
            self.resolve("workflow_dispatch", {"inputs": {"branch": BRANCH}}),
            {"branch": BRANCH, "sha": "", "run_id": RUN_ID})

    def test_rejections(self):
        cases = [
            ("workflow_run", self.run_event(event="pull_request")),
            ("workflow_run", self.run_event(
                head_repository={"full_name": "fork/repo"})),
            ("workflow_run", self.run_event(head_sha="abc")),
            ("workflow_run", self.run_event(head_branch=BRANCH + "\nsha=x")),
            ("workflow_run", self.run_event(head_branch="main")),
            ("workflow_dispatch", {"inputs": {"branch": "claude/eval-$(id)"}}),
            ("workflow_dispatch", {"inputs": {}}),
            ("push", {"ref": "refs/heads/" + BRANCH}),
        ]
        for name, event in cases:
            with self.subTest(name=name, event=str(event)[:80]):
                with self.assertRaises(ingest.Rejected):
                    self.resolve(name, event)


class PlaceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.staged = self.tmp / "staged"
        self.tree = self.tmp / "tree"
        (self.staged / "routine-results" / RUN_ID).mkdir(parents=True)
        (self.staged / "routine-results" / RUN_ID / "report.md").write_text(
            REPORT, encoding="utf-8")
        self.tree.mkdir()

    def test_new_then_idempotent(self):
        self.assertEqual(ingest.place(self.staged, self.tree),
                         {"changed": "true", "added": "1"})
        self.assertEqual(
            (self.tree / "routine-results" / RUN_ID / "report.md").read_text(
                encoding="utf-8"), REPORT)
        self.assertEqual(ingest.place(self.staged, self.tree),
                         {"changed": "false", "added": "0"})

    def test_never_overwrites_a_differing_file(self):
        target = self.tree / "routine-results" / RUN_ID / "report.md"
        target.parent.mkdir(parents=True)
        target.write_text("earlier\n", encoding="utf-8")
        with self.assertRaisesRegex(ingest.Rejected, "different content"):
            ingest.place(self.staged, self.tree)
        self.assertEqual(target.read_text(encoding="utf-8"), "earlier\n")

    def test_refuses_a_symlink_in_the_destination(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.tree / "routine-results")
        with self.assertRaisesRegex(ingest.Rejected, "symlink"):
            ingest.place(self.staged, self.tree)
        self.assertEqual(list(outside.iterdir()), [])


# ---------------------------------------------------------------------------
# Workflow shape, from the parsed YAML.

PUSH_TARGET = re.compile(r"\bpush\s+origin\s+([^\s;]+)")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict):
    return doc.get("on", doc.get(True))


def run_blocks(doc: dict) -> list[tuple[str, str]]:
    return [(name, step["run"]) for name, job in doc["jobs"].items()
            for step in job.get("steps") or [] if "run" in step]


def uses_nodes(node):
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "uses":
                yield value
            yield from uses_nodes(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from uses_nodes(item)


class WorkflowShapeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ingest = load(INGEST)
        cls.signal = load(SIGNAL)
        cls.job = cls.ingest["jobs"]["ingest"]

    def test_the_writing_job_runs_on_workflow_run_of_the_signal(self):
        on = triggers(self.ingest)
        self.assertEqual(set(on), {"workflow_run", "workflow_dispatch"})
        self.assertEqual(on["workflow_run"]["workflows"], [self.signal["name"]])
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertEqual(on["workflow_run"]["branches"], ["claude/eval-*"])
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"branch"})

    def test_signal_is_a_filtered_push_with_no_reach(self):
        on = triggers(self.signal)
        self.assertEqual(set(on), {"push"})
        self.assertEqual(on["push"]["branches"], ["claude/eval-*"])
        self.assertEqual(on["push"]["paths"],
                         [f"{ingest.SOURCE_ROOT}/**"])
        self.assertEqual(self.signal["permissions"], {})
        for job in self.signal["jobs"].values():
            self.assertNotIn("permissions", job)
            for step in job["steps"]:
                self.assertNotIn("uses", step, "the signal checks nothing out")

    def test_permissions_are_minimal(self):
        self.assertEqual(self.ingest["permissions"], {})
        self.assertEqual(set(self.ingest["jobs"]), {"ingest"})
        self.assertEqual(self.job["permissions"], {"contents": "write"})

    def test_only_the_default_branch_copy_runs(self):
        self.assertIn("github.ref == format('refs/heads/{0}', "
                      "github.event.repository.default_branch)",
                      self.job["if"])

    def test_no_concurrency_group(self):
        for doc in (self.ingest, self.signal):
            self.assertNotIn("concurrency", doc)
            for job in doc["jobs"].values():
                self.assertNotIn("concurrency", job)

    def test_every_uses_is_locked_and_already_pinned_elsewhere(self):
        pinned = set()
        for other in WORKFLOWS.glob("*.yml"):
            if other not in (INGEST, SIGNAL):
                pinned |= {n.value for n in uses_nodes(
                    yaml.compose(other.read_text(encoding="utf-8")))}
        for path in (INGEST, SIGNAL):
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for node in uses_nodes(yaml.compose(text)):
                with self.subTest(path=path.name, uses=node.value):
                    self.assertIsNone(lock_problem(node.value, path))
                    rest = lines[node.end_mark.line][node.end_mark.column:]
                    self.assertEqual(rest.strip(), "",
                                     "a pin carries no trailing comment")
                    self.assertIn(node.value, pinned)

    def test_no_expressions_in_run_blocks(self):
        for doc in (self.ingest, self.signal):
            blocks = run_blocks(doc)
            self.assertTrue(blocks)
            for job, body in blocks:
                with self.subTest(job=job, body=body[:40]):
                    self.assertNotIn("${{", body,
                                     "pass values through env, never inline")

    def test_single_checkout_of_the_default_branch_without_credentials(self):
        checkouts = [s for s in self.job["steps"]
                     if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"]["ref"],
                         "${{ github.event.repository.default_branch }}")
        self.assertIs(checkouts[0]["with"]["persist-credentials"], False)

    def test_git_add_keeps_gitignore_in_force(self):
        adds = [body for _, body in run_blocks(self.ingest) if " add " in body]
        self.assertTrue(adds)
        for body in adds:
            self.assertIsNone(re.search(r"\badd\s+(-f|--force)\b", body))

    def test_every_push_targets_persistent_eval_results(self):
        targets = [t for _, body in run_blocks(self.ingest)
                   for t in PUSH_TARGET.findall(body)]
        self.assertEqual(targets, ["HEAD:refs/heads/persistent/eval-results"])

    def test_the_guards_can_fail(self):
        # Negative controls on the helpers the shape tests lean on.
        self.assertIsNotNone(lock_problem("actions/checkout@v4", INGEST))
        self.assertEqual(PUSH_TARGET.findall("git push origin HEAD:main"),
                         ["HEAD:main"])
        bad = {"jobs": {"j": {"steps": [{"run": "echo ${{ inputs.x }}"}]}}}
        self.assertIn("${{", run_blocks(bad)[0][1])


if __name__ == "__main__":
    unittest.main()
