"""Passive real-work fleet counters: differential artifacts and bounded traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

import cli_json  # noqa: E402
import guidance_violations as counters  # noqa: E402
import run_eval  # noqa: E402

SHA = "a" * 40
OTHER_SHA = "b" * 40
WORKFLOW = ".github/workflows/ci.yml"


def trace(*commands):
    events = []
    for index, item in enumerate(commands):
        command, failed = item if isinstance(item, tuple) else (item, False)
        ident = f"tool-{index}"
        events.extend([
            {"call": 0, "kind": "tool_use", "name": "Bash", "id": ident,
             "input": command, "input_chars": len(command), "subagent": False},
            {"call": 0, "kind": "tool_result", "id": ident, "is_error": failed,
             "output": "", "output_chars": 0, "subagent": False}])
    return {"schema_version": 1, "events": events, "omitted_events": 0,
            "complete": True}


def verbose(*commands, session="session-1", is_error=False):
    messages = []
    for event in trace(*commands)["events"]:
        if event["kind"] == "tool_use":
            block = {"type": "tool_use", "id": event["id"], "name": "Bash",
                     "input": {"command": event["input"]}}
            role = "assistant"
        else:
            block = {"type": "tool_result", "tool_use_id": event["id"],
                     "content": "", "is_error": event["is_error"]}
            role = "user"
        messages.append({"type": role, "message": {"content": [block]}})
    result = {"type": "result", "result": "done", "is_error": is_error}
    if session is not None:
        result["session_id"] = session
    return [*messages, result]


def workflow(steps):
    return "jobs:\n  test:\n    steps:\n" + steps


LOCKFILE = ".github/workflows/actions.lock"
# A made-up tag: the fixture builds its own lockfile, not the repo's.
TAG = "actions/checkout@v9.8.7"


def lockfile(uses=TAG, commit="sha1-" + SHA, workflow_path=WORKFLOW, listed=TAG, ref=None):
    ref = uses.rpartition('@')[2] if ref is None else ref
    return (f"version: 'v0.0.3'\nworkflows:\n  '{workflow_path}':\n    - '{listed}'\n"
            f"dependencies:\n  '{uses}':\n    ref: '{ref}'\n"
            f"    commit: '{commit}'\n")


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import arm_test_environment, install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()



class GuidanceViolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "ws"
        self.workspace.mkdir()

    def write(self, value, path=WORKFLOW):
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        return target

    def measure(self, before=None, evidence=None):
        return counters.measure(before or counters.snapshot(self.workspace),
                                self.workspace, evidence if evidence is not None else trace())

    def test_new_yaml_violations_exclude_inherited_occurrences(self):
        old = workflow("      - uses: actions/checkout@v4\n      - run: echo '${{ inputs.old }}'\n")
        self.write(old)
        before = counters.snapshot(self.workspace)
        self.write(old + "      - uses: actions/setup-node@main\n      - run: echo '${{ github.event.issue.title }}'\n")
        rules = self.measure(before)["rules"]
        self.assertEqual(rules["unpinned_actions"]["count"], 1)
        self.assertEqual(rules["event_input_in_run"]["count"], 1)
        self.assertNotIn("issue.title", json.dumps(rules))

    def test_yaml_carveouts_contexts_and_harmless_run_edits(self):
        self.write(workflow("      - run: echo '${{ inputs.old }}'\n"))
        before = counters.snapshot(self.workspace)
        self.write(workflow(
            "      - run: echo '${{ inputs.old }}'; echo harmless\n"
            f"      - uses: actions/checkout@{SHA}\n"
            "      - uses: ./local/action\n      - uses: docker://image:tag\n"
            "      - uses: example-adopter/example-site-platform/.github/actions/demo@v0.1.88\n"
            "      - env: {uses: decoy@main, run: '${{ inputs.decoy }}'}\n")
            + "runs: {using: composite, steps: [{uses: decoy@main}]}\n")
        rules = self.measure(before)["rules"]
        self.assertEqual(rules["unpinned_actions"]["count"], 0)
        self.assertEqual(rules["event_input_in_run"]["count"], 0)

    def test_composite_actions_reusable_workflows_and_duplicate_occurrences(self):
        before = counters.snapshot(self.workspace)
        self.write("runs:\n  using: composite\n  steps:\n    - uses: owner/action@main\n"
                   "    - uses: owner/action@main\n    - run: echo '${{ inputs.message }}'\n"
                   "jobs: {decoy: {uses: ignored@main}}\n", "nested/action.yaml")
        self.write("jobs: {reusable: {uses: owner/repo/.github/workflows/demo.yml@main}}\n")
        rules = self.measure(before)["rules"]
        self.assertEqual(rules["unpinned_actions"]["count"], 3)
        self.assertEqual(rules["event_input_in_run"]["count"], 1)

    def test_invalid_bounded_or_symlinked_yaml_is_unknown(self):
        before = counters.snapshot(self.workspace)
        for source in ("jobs: [", "jobs: {}\njobs: {}\n", "x: &x [*x]\n", "jobs: {j: {uses: []}}"):
            with self.subTest(source=source):
                self.write(source)
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual(entry["status"], "unknown")
                self.assertIsNone(entry["count"])
        path = self.write("jobs: {}")
        with mock.patch.object(counters, "MAX_FILE_BYTES", 2):
            self.assertIsNone(self.measure(before)["rules"]["unpinned_actions"]["count"])
        path.unlink()
        path.symlink_to(Path(self.temp.name) / "outside.yml")
        self.assertIsNone(self.measure(before)["rules"]["unpinned_actions"]["count"])
        path.unlink()
        for directory in ("a", "z"):
            self.write("runs: {using: composite, steps: []}", f"{directory}/action.yml")

        def reverse_directory_walk(*_args, **_kwargs):
            directories = ["z", "a"]
            yield str(self.workspace), directories, []
            # Like os.walk, subsequent descent respects the caller's mutation.
            for directory in directories:
                yield str(self.workspace / directory), [], ["action.yml"]

        import os
        with mock.patch.object(os, "walk", reverse_directory_walk), \
                mock.patch.object(counters, "MAX_FILES", 1):
            bounded = counters.snapshot(self.workspace)
        self.assertEqual(list(bounded["files"]), ["a/action.yml"])
        self.assertFalse(bounded["complete"])

        self.write("jobs: {}", ".github/workflows/a.yml")
        self.write("jobs: {}", ".github/workflows/z.yml")
        listing = [(str(self.workspace / ".github/workflows"), [], ["z.yml", "a.yml"])]
        with mock.patch.object(os, "walk", return_value=iter(listing)), \
                mock.patch.object(counters, "MAX_FILES", 1):
            bounded = counters.snapshot(self.workspace)
        self.assertEqual(list(bounded["files"]), [".github/workflows/a.yml"])

    def test_actions_lock_pins_a_tagged_step_for_its_own_workflow(self):
        before = counters.snapshot(self.workspace)
        self.write(lockfile(), LOCKFILE)
        self.write(workflow(f"      - uses: {TAG}\n      - uses: $/local/action\n"
                            f"      - uses: actions/setup-node@{SHA}\n"
                            "      - uses: example-adopter/example-site-platform/.github/actions/demo@v0.1.88\n"))
        measured = self.measure(before)
        self.assertEqual(measured["rules"]["unpinned_actions"],
                         {"section_id": counters.RULES["unpinned_actions"],
                          "status": "known", "count": 0, "observed_count": 0})
        self.assertNotIn("sha1-", json.dumps(measured))
        self.assertNotIn(TAG, json.dumps(measured))

    def test_a_tag_actions_lock_does_not_lock_still_counts(self):
        cases = {
            "no lockfile": None,
            "uppercase commit": lockfile(commit="sha1-" + SHA.upper()),
            "bare hex commit": lockfile(commit=SHA),
            "short commit": lockfile(commit="sha1-" + SHA[:39]),
            "other key": lockfile(uses="actions/checkout@v7.0.0"),
            "other workflow": lockfile(workflow_path=".github/workflows/other.yml"),
            "not listed": lockfile(listed="actions/checkout@v7.0.0"),
            "ref differs from tag": lockfile(ref="v7.0.0"),
            "ref missing": lockfile().replace("    ref: 'v9.8.7'\n", ""),
        }
        for label, lock in cases.items():
            with self.subTest(case=label):
                (self.workspace / LOCKFILE).unlink(missing_ok=True)
                before = counters.snapshot(self.workspace)
                if lock is not None:
                    self.write(lock, LOCKFILE)
                self.write(workflow(f"      - uses: {TAG}\n"))
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual((entry["status"], entry["count"]), ("known", 1))
                self.write("jobs: {}")

    def test_actions_lock_key_matches_owner_and_repo_case_insensitively_but_the_ref_exactly(self):
        mixed = "Adam-S-Daniel/verify-actions-lock@v1.0.0"
        lower = mixed.lower()
        cases = {
            "mixed-case step, lowercase key": (mixed, lockfile(uses=lower, listed=lower), 0),
            "only the ref's case differs": ("Adam-S-Daniel/verify-actions-lock@V1.0.0",
                                            lockfile(uses=lower, listed=lower), 1),
        }
        for label, (uses, lock, count) in cases.items():
            with self.subTest(case=label):
                (self.workspace / LOCKFILE).unlink(missing_ok=True)
                before = counters.snapshot(self.workspace)
                self.write(lock, LOCKFILE)
                self.write(workflow(f"      - uses: {uses}\n"))
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual((entry["status"], entry["count"]), ("known", count))
                self.write("jobs: {}")

    def test_lock_problem_matches_owner_and_repo_case_insensitively_but_the_ref_exactly(self):
        from lockfile_pins import lock_problem
        lower = "adam-s-daniel/verify-actions-lock@v1.0.0"
        lock = {"workflows": {WORKFLOW: [lower]},
                "dependencies": {lower: {"ref": "v1.0.0", "commit": "sha1-" + SHA}}}
        self.assertIsNone(lock_problem("Adam-S-Daniel/verify-actions-lock@v1.0.0", WORKFLOW, lock))
        self.assertIsNotNone(lock_problem("Adam-S-Daniel/verify-actions-lock@V1.0.0", WORKFLOW, lock))

    def test_actions_lock_never_pins_reusable_workflows_or_uncalled_composite_steps(self):
        reusable = "owner/repo/.github/workflows/demo.yml@v1.2.3"
        before = counters.snapshot(self.workspace)
        self.write(lockfile(uses=reusable, listed=reusable), LOCKFILE)
        self.write(f"jobs: {{reusable: {{uses: {reusable}}}}}\n")
        self.write("runs:\n  using: composite\n  steps:\n    - uses: " + TAG + "\n",
                   "nested/action.yml")
        self.assertEqual(self.measure(before)["rules"]["unpinned_actions"]["count"], 2)

    COMPOSITE = "runs:\n  using: composite\n  steps:\n    - uses: " + TAG + "\n"

    def test_actions_lock_pins_a_composite_step_through_a_calling_workflow(self):
        # ADR 0024: a `$/` composite's inner actions are locked by the
        # lockfile list of the workflow that calls it. The composite sorts
        # before .github/workflows, so this also proves walk order is moot.
        before = counters.snapshot(self.workspace)
        self.write(lockfile(), LOCKFILE)
        self.write(workflow("      - uses: $/.github/actions/demo\n"))
        self.write(self.COMPOSITE, ".github/actions/demo/action.yml")
        entry = self.measure(before)["rules"]["unpinned_actions"]
        self.assertEqual((entry["status"], entry["count"]), ("known", 0))

    def test_a_composite_step_is_not_pinned_without_a_valid_calling_lock(self):
        other = ".github/workflows/other.yml"
        cases = {
            "no caller": (lockfile(), "      - uses: actions/setup-node@" + SHA + "\n"),
            "caller names another dir": (lockfile(), "      - uses: $/.github/actions/other\n"),
            "called as ./ not $/": (lockfile(), "      - uses: ./.github/actions/demo\n"),
            "called as a job, not a step": (lockfile(), None),
            "caller's list lacks it": (lockfile(workflow_path=other), "      - uses: $/.github/actions/demo\n"),
            "uppercase commit": (lockfile(commit="sha1-" + SHA.upper()), "      - uses: $/.github/actions/demo\n"),
            "ref differs from tag": (lockfile(ref="v7.0.0"), "      - uses: $/.github/actions/demo\n"),
            "other key": (lockfile(uses="actions/checkout@v7.0.0", listed="actions/checkout@v7.0.0"),
                          "      - uses: $/.github/actions/demo\n"),
        }
        for label, (lock, steps) in cases.items():
            with self.subTest(case=label):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                self.workspace = Path(temp.name)
                before = counters.snapshot(self.workspace)
                self.write(lock, LOCKFILE)
                self.write(workflow(steps) if steps else "jobs: {call: {uses: $/.github/actions/demo}}\n")
                self.write(self.COMPOSITE, ".github/actions/demo/action.yml")
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual((entry["status"], entry["count"]), ("known", 1))

    def test_a_composite_step_with_an_unreadable_caller_is_unknown(self):
        before = counters.snapshot(self.workspace)
        self.write(lockfile(), LOCKFILE)
        self.write("jobs: [", WORKFLOW)
        self.write(self.COMPOSITE, ".github/actions/demo/action.yml")
        entry = self.measure(before)["rules"]["unpinned_actions"]
        self.assertEqual((entry["status"], entry["count"]), ("unknown", None))

    def test_an_unreadable_actions_lock_is_unknown(self):
        before = counters.snapshot(self.workspace)
        self.write(workflow(f"      - uses: {TAG}\n"))
        for source in ("dependencies: [", "dependencies: {}\ndependencies: {}\n",
                       "x: &x [*x]\n", "- not a mapping\n"):
            with self.subTest(source=source):
                self.write(source, LOCKFILE)
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual((entry["status"], entry["count"]), ("unknown", None))
        lock = self.workspace / LOCKFILE
        lock.unlink()
        outside = Path(self.temp.name) / "outside.lock"
        outside.write_text(lockfile(), encoding="utf-8")
        lock.symlink_to(outside)
        entry = self.measure(before)["rules"]["unpinned_actions"]
        self.assertEqual((entry["status"], entry["count"]), ("unknown", None))

    def test_an_unreadable_actions_lock_leaves_event_input_in_run_counted(self):
        # Only `unpinned_actions` depends on the lockfile; a broken one must
        # not hide the unrelated interpolation counter.
        cases = {"malformed": "dependencies: [",
                 "duplicate key": "dependencies: {}\ndependencies: {}\n",
                 "symlinked": None}
        for label, source in cases.items():
            with self.subTest(case=label):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                self.workspace = Path(temp.name) / "ws"
                self.workspace.mkdir()
                before = counters.snapshot(self.workspace)
                self.write(workflow(f"      - uses: {TAG}\n"
                                    "      - run: echo ${{ github.event.issue.title }}\n"))
                if source is None:
                    outside = Path(temp.name) / "outside.lock"
                    outside.write_text(lockfile(), encoding="utf-8")
                    lock = self.workspace / LOCKFILE
                    lock.symlink_to(outside)
                else:
                    self.write(source, LOCKFILE)
                rules = self.measure(before)["rules"]
                self.assertEqual((rules["event_input_in_run"]["status"],
                                  rules["event_input_in_run"]["count"]), ("known", 1))
                self.assertEqual((rules["unpinned_actions"]["status"],
                                  rules["unpinned_actions"]["count"]), ("unknown", None))

    @staticmethod
    def lock_for(lists):
        """A valid TAG entry, with each workflow path in `lists` mapped to its refs."""
        body = "".join(f"  '{path}':\n" + "".join(f"    - '{ref}'\n" for ref in refs)
                       for path, refs in lists.items())
        return (f"version: 'v0.0.3'\nworkflows:\n{body}"
                f"dependencies:\n  '{TAG}':\n    ref: 'v9.8.7'\n    commit: 'sha1-{SHA}'\n")

    def test_a_composite_needs_every_calling_workflow_to_lock_it(self):
        # Credit is the intersection over callers: a second caller that is
        # not onboarded (absent from `workflows`) or whose list lacks the
        # key removes it, whichever order the workflows are walked in.
        other = ".github/workflows/other.yml"
        cases = {
            "second caller not onboarded": ({WORKFLOW: [TAG]}, 1),
            "second caller's list lacks it": ({WORKFLOW: [TAG], other: []}, 1),
            "first caller not onboarded": ({other: [TAG]}, 1),
            "both callers lock it": ({WORKFLOW: [TAG], other: [TAG]}, 0),
        }
        for label, (lists, expected) in cases.items():
            with self.subTest(case=label):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                self.workspace = Path(temp.name)
                before = counters.snapshot(self.workspace)
                self.write(self.lock_for(lists), LOCKFILE)
                self.write(workflow("      - uses: $/.github/actions/demo\n"))
                self.write(workflow("      - uses: $/.github/actions/demo\n"), other)
                self.write(self.COMPOSITE, ".github/actions/demo/action.yml")
                entry = self.measure(before)["rules"]["unpinned_actions"]
                self.assertEqual((entry["status"], entry["count"]), ("known", expected))

    CALLS_B = "runs:\n  using: composite\n  steps:\n    - uses: $/.github/actions/b\n"

    def chain(self, lists, a_source, b_source=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        before = counters.snapshot(self.workspace)
        self.write(self.lock_for(lists), LOCKFILE)
        self.write(workflow("      - uses: $/.github/actions/a\n"))
        self.write(a_source, ".github/actions/a/action.yml")
        self.write(b_source or self.COMPOSITE, ".github/actions/b/action.yml")
        entry = self.measure(before)["rules"]["unpinned_actions"]
        return entry["status"], entry["count"]

    def test_a_composite_called_through_another_composite_is_credited(self):
        # ci.yml -> $/a -> $/b -> tag: b's inner tag is locked by ci.yml's list.
        self.assertEqual(self.chain({WORKFLOW: [TAG]}, self.CALLS_B), ("known", 0))

    def test_a_composite_chain_needs_an_unbroken_listed_path(self):
        cases = {
            "chain broken (a calls another dir)":
                ({WORKFLOW: [TAG]}, self.CALLS_B.replace("actions/b", "actions/c")),
            "chain broken (a calls b as ./)":
                ({WORKFLOW: [TAG]}, self.CALLS_B.replace("$/", "./")),
            "caller's list lacks it": ({WORKFLOW: []}, self.CALLS_B),
            "caller not onboarded": ({".github/workflows/other.yml": [TAG]}, self.CALLS_B),
        }
        for label, (lists, a_source) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.chain(lists, a_source), ("known", 1))

    def test_a_composite_cycle_terminates_and_credits_only_reached_dirs(self):
        b_source = self.COMPOSITE + "    - uses: $/.github/actions/a\n"
        self.assertEqual(self.chain({WORKFLOW: [TAG]}, self.CALLS_B, b_source), ("known", 0))
        self.assertEqual(self.chain({WORKFLOW: []}, self.CALLS_B, b_source), ("known", 1))
        # A cycle no workflow reaches is credited by nobody.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        before = counters.snapshot(self.workspace)
        self.write(self.lock_for({WORKFLOW: [TAG]}), LOCKFILE)
        self.write(workflow(f"      - uses: actions/setup-node@{SHA}\n"))
        self.write(self.CALLS_B, ".github/actions/a/action.yml")
        self.write(b_source, ".github/actions/b/action.yml")
        entry = self.measure(before)["rules"]["unpinned_actions"]
        self.assertEqual((entry["status"], entry["count"]), ("known", 1))

    def test_release_tags_take_ascii_digits_only(self):
        # `\d` would accept any Unicode decimal digit (here Arabic-Indic).
        from lockfile_pins import lock_problem
        for prefix in ("example-adopter/example-site-platform/.github/actions/demo@",
                       "example-adopter/example-site-platform/.github/workflows/demo.yml@"):
            with self.subTest(prefix=prefix):
                self.assertTrue(counters._pinned(prefix + "v0.1.88"))
                self.assertFalse(counters._pinned(prefix + "v١.٢.٣"))
                self.assertIsNone(lock_problem(prefix + "v0.1.88", WORKFLOW, {}))
                self.assertIsNotNone(lock_problem(prefix + "v١.٢.٣", WORKFLOW, {}))

    def test_failed_pushes_do_not_count_and_typical_successful_push_does(self):
        rules = self.measure(evidence=trace(("git push -u origin feat/demo", True),
                                          "git push -u origin feat/demo"))["rules"]
        self.assertEqual(rules["push_without_verification"]["count"], 1)

    def test_exact_successful_subsequent_verification_is_required(self):
        push = f"git push origin {SHA}:feat/demo"
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        for evidence, expected in (
                (trace(push, check), 0),
                (trace(push, check.replace(SHA, SHA.upper())), 0),
                (trace(push.replace(SHA, SHA.upper()), check), 0),
                (trace(check, push), 1),
                (trace(push, (check, True)), 1),
                (trace(push, check.replace(SHA, OTHER_SHA)), 1),
                (trace(push, check.replace("feat/demo", "feat/other")), 1)):
            with self.subTest(expected=expected, evidence=evidence):
                self.assertEqual(self.measure(evidence=evidence)["rules"]["push_without_verification"],
                                 {"section_id": counters.RULES["push_without_verification"],
                                  "status": "known", "count": expected, "observed_count": expected})

    def assert_push_unknown(self, evidence):
        self.assertEqual(self.measure(evidence=evidence)["rules"]["push_without_verification"],
                         {"section_id": counters.RULES["push_without_verification"],
                          "status": "unknown", "count": None, "observed_count": 0})

    def run_replies(self, *replies):
        scripted = [reply if isinstance(reply, (subprocess.CompletedProcess, BaseException))
                    else subprocess.CompletedProcess(["unused-cli"], 0,
                                                     stdout=json.dumps(reply), stderr="")
                    for reply in replies]
        with mock.patch.object(run_eval.subprocess, "run", side_effect=scripted) as run, \
                mock.patch.object(run_eval, "agent_env", return_value={"PATH": arm_test_environment()["PATH"],
                                                                     "HOME": str(self.workspace)}), \
                mock.patch.dict(run_eval.os.environ, {"CLAUDE_BIN": "unused-cli"}):
            answer = run_eval.run_agent(self.workspace, "first", {
                "name": "without_skill", "timeout": 5,
                "followups": ["next"] * (len(replies) - 1)})
        self.assertIsInstance(answer.get("tool_trace"), dict)
        return answer, run.call_count

    def test_trace_requires_explicit_true_completeness(self):
        for value in (None, False, 0, 1, "true"):
            evidence = trace(f"git push origin {SHA}:feat/demo")
            if value is None:
                del evidence["complete"]
            else:
                evidence["complete"] = value
            with self.subTest(value=value):
                self.assert_push_unknown(evidence)

    def test_bounded_trace_propagates_completeness_and_omissions(self):
        events = trace(f"git push origin {SHA}:feat/demo")["events"]
        for evidence in (cli_json.bounded_tool_trace([events], complete=False),
                         cli_json.bounded_tool_trace([events], max_bytes=0)):
            self.assertIs(evidence["complete"], False)
            self.assert_push_unknown(evidence)
        self.assertIs(cli_json.bounded_tool_trace([[]])["complete"], True)

    def test_followup_evidence_loss_keeps_prior_push_but_makes_count_unknown(self):
        push = f"git push origin {SHA}:feat/demo"
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        cases = [
            ("malformed nonzero", subprocess.CompletedProcess(["unused-cli"], 1,
                stdout="{", stderr=""), "nonzero_exit"),
            ("malformed zero", subprocess.CompletedProcess(["unused-cli"], 0,
                stdout="{", stderr=""), "invalid_json"),
            ("timeout", subprocess.TimeoutExpired(["unused-cli"], 5), "timeout"),
            ("nonverbose success", verbose()[-1], None),
            ("nonverbose failure", subprocess.CompletedProcess(["unused-cli"], 1,
                stdout=json.dumps(verbose()[-1]), stderr=""), "nonzero_exit"),
        ]
        for commands in ((push,), (push, check)):
            first = verbose(*commands)
            expected = cli_json.bounded_tool_trace([cli_json.tool_events(first), []],
                                                  complete=False)
            for label, reply, error in cases:
                with self.subTest(label=label, verified=len(commands) == 2):
                    answer, calls = self.run_replies(first, reply)
                    self.assertEqual(answer.get("error"), error)
                    self.assertEqual(calls, 2)
                    self.assertEqual(answer["tool_trace"], expected)
                    self.assert_push_unknown(answer["tool_trace"])

    def test_first_nonverbose_call_keeps_later_verbose_call_index(self):
        answer, calls = self.run_replies(verbose()[-1],
                                        verbose(f"git push origin {SHA}:feat/demo"))
        self.assertEqual(calls, 2)
        self.assertEqual(answer["tool_trace"]["calls"], 2)
        self.assertEqual([event["call"] for event in answer["tool_trace"]["events"]], [1, 1])
        self.assert_push_unknown(answer["tool_trace"])

    def test_later_verbose_call_cannot_restore_lost_completeness(self):
        answer, calls = self.run_replies(
            verbose(f"git push origin {SHA}:feat/demo"), verbose()[-1],
            verbose(f"git merge-base --is-ancestor {SHA} origin/feat/demo"))
        self.assertEqual(calls, 3)
        self.assertEqual(answer["tool_trace"]["calls"], 3)
        self.assertEqual([event["call"] for event in answer["tool_trace"]["events"]], [0, 0, 2, 2])
        self.assert_push_unknown(answer["tool_trace"])

    def test_verbose_no_tools_is_known_zero(self):
        answer, calls = self.run_replies(verbose(), verbose())
        self.assertEqual(calls, 2)
        self.assertEqual(answer["tool_trace"], cli_json.bounded_tool_trace([[], []]))
        entry = self.measure(evidence=answer["tool_trace"])["rules"]["push_without_verification"]
        self.assertEqual(entry["status"], "known")
        self.assertEqual(entry["count"], 0)

    def test_completely_observed_verbose_followup_verifies_first_push(self):
        answer, calls = self.run_replies(
            verbose(f"git push origin {SHA}:feat/demo"),
            verbose(f"git merge-base --is-ancestor {SHA} origin/feat/demo"))
        self.assertEqual(calls, 2)
        self.assertIs(answer["tool_trace"]["complete"], True)
        entry = self.measure(evidence=answer["tool_trace"])["rules"]["push_without_verification"]
        self.assertEqual(entry["status"], "known")
        self.assertEqual(entry["count"], 0)

    def test_valid_verbose_failures_preserve_complete_tool_evidence(self):
        payload = verbose(f"git push origin {SHA}:feat/demo", is_error=True)
        for reply, error in ((payload, "agent_error"),
                             (subprocess.CompletedProcess(["unused-cli"], 1,
                              stdout=json.dumps(payload), stderr=""), "nonzero_exit")):
            with self.subTest(error=error):
                answer, calls = self.run_replies(reply)
                self.assertEqual(answer["error"], error)
                self.assertEqual(calls, 1)
                self.assertIs(answer["tool_trace"]["complete"], True)
                entry = self.measure(evidence=answer["tool_trace"])["rules"]["push_without_verification"]
                self.assertEqual(entry["status"], "known")
                self.assertEqual(entry["count"], 1)

    def test_unusable_verbose_array_is_incomplete_even_if_tools_were_extracted(self):
        first = verbose(f"git push origin {SHA}:feat/demo")
        for shape, incomplete in (("missing result", verbose("echo harmless")[:-1]),
                                  ("invalid item", [*verbose("echo harmless"), None])):
            for status in (0, 1):
                with self.subTest(shape=shape, status=status):
                    reply = subprocess.CompletedProcess(["unused-cli"], status,
                                                         stdout=json.dumps(incomplete), stderr="")
                    answer, calls = self.run_replies(first, reply)
                    self.assertEqual(calls, 2)
                    self.assertEqual(answer["tool_trace"]["calls"], 2)
                    self.assertIs(answer["tool_trace"]["complete"], False)
                    self.assertEqual([event["call"] for event in answer["tool_trace"]["events"]], [0, 0, 1, 1])
                    self.assert_push_unknown(answer["tool_trace"])

    def test_missing_resume_session_keeps_evidence_without_an_attempted_followup(self):
        first = verbose(f"git push origin {SHA}:feat/demo", session=None)
        answer, calls = self.run_replies(first, verbose())
        self.assertEqual(answer["error"], "invalid_json")
        self.assertEqual(calls, 1)
        self.assertEqual(answer["tool_trace"],
                         cli_json.bounded_tool_trace([cli_json.tool_events(first)]))
        entry = self.measure(evidence=answer["tool_trace"])["rules"]["push_without_verification"]
        self.assertEqual(entry["status"], "known")
        self.assertEqual(entry["count"], 1)

    def test_first_call_without_usable_evidence_is_explicitly_unknown(self):
        for reply in (verbose()[-1], subprocess.TimeoutExpired(["unused-cli"], 5),
                      subprocess.CompletedProcess(["unused-cli"], 0, stdout="{", stderr="")):
            with self.subTest(reply=type(reply).__name__):
                answer, calls = self.run_replies(reply)
                self.assertEqual(calls, 1)
                self.assertEqual(answer["tool_trace"],
                                 cli_json.bounded_tool_trace([[]], complete=False))
                self.assert_push_unknown(answer["tool_trace"])

    def test_tool_error_count_does_not_report_false_zero_for_incomplete_trace(self):
        self.assertIsNone(run_eval._tool_error_count(cli_json.bounded_tool_trace([[]], complete=False)))
        self.assertEqual(run_eval._tool_error_count(cli_json.bounded_tool_trace([[]])), 0)
        self.assertEqual(run_eval._tool_error_count({"events": [], "omitted_events": 0}), 0)

    def test_equivalent_remote_refs_preserve_case_and_remote_identity(self):
        for destination in ("feat/demo", "refs/heads/feat/demo"):
            push = f"git push origin {SHA}:{destination}"
            for ref, expected in (("origin/feat/demo", 0),
                                  ("refs/remotes/origin/feat/demo", 0),
                                  ("refs/remotes/origin/Feat/demo", 1),
                                  ("refs/remotes/Origin/feat/demo", 1),
                                  ("refs/remotes/upstream/feat/demo", 1)):
                with self.subTest(destination=destination, ref=ref):
                    evidence = trace(push, f"git merge-base --is-ancestor {SHA} {ref}")
                    self.assertEqual(self.measure(evidence=evidence)["rules"]["push_without_verification"],
                                     {"section_id": counters.RULES["push_without_verification"],
                                      "status": "known", "count": expected,
                                      "observed_count": expected})

    def test_unsupported_successful_verification_forms_are_unknown(self):
        push = f"git push origin {SHA}:feat/demo"
        for command in (f"git merge-base --is-ancestor {SHA[:7]} origin/feat/demo",
                        "git merge-base --is-ancestor HEAD origin/feat/demo",
                        f"git merge-base --is-ancestor {SHA}^ origin/feat/demo",
                        f"git merge-base --is-ancestor {SHA}~0 origin/feat/demo",
                        f"git merge-base --is-ancestor {SHA}^{{commit}} origin/feat/demo",
                        f"git merge-base --is-ancestor {SHA} origin/feat/demo^{{commit}}",
                        f"git merge-base --is-ancestor {SHA} origin/feat/demo~0",
                        f"git merge-base --is-ancestor {SHA} origin/feat/demo@{{0}}",
                        f"git merge-base --is-ancestor {SHA} HEAD",
                        f"git merge-base --is-ancestor {SHA} {OTHER_SHA}",
                        f"git merge-base --is-ancestor -- {SHA} origin/feat/demo",
                        f"git merge-base --is-ancestor {SHA} origin/feat/demo --",
                        f"git merge-base --all --is-ancestor {SHA} origin/feat/demo",
                        f"git merge-base {SHA} origin/feat/demo"):
            with self.subTest(command=command):
                self.assert_push_unknown(trace(push, command))

    def test_opaque_verification_executables_are_unknown(self):
        push = f"git push origin {SHA}:feat/demo"
        for command in ("./verify-push.sh", "verify-push", "python3 verifier.py",
                        "/usr/bin/python3 verifier.py", "node verifier.js", "git verify-push"):
            with self.subTest(command=command):
                self.assert_push_unknown(trace(push, command))

    def test_delegation_without_parent_push_is_unknown(self):
        for name in ("Task", "Agent"):
            evidence = trace()
            evidence["events"] = [
                {"call": 0, "kind": "tool_use", "name": name, "id": "child-1",
                 "input": "check the branch", "input_chars": 16, "subagent": False},
                {"call": 0, "kind": "tool_result", "id": "child-1", "is_error": False,
                 "output": "verified", "output_chars": 8, "subagent": False}]
            with self.subTest(name=name):
                self.assert_push_unknown(evidence)

    def test_delegated_verification_cannot_prove_parent_push_omission(self):
        for name in ("Task", "Agent"):
            evidence = trace(f"git push origin {SHA}:feat/demo")
            evidence["events"].extend([
                {"call": 0, "kind": "tool_use", "name": name, "id": "child-1",
                 "input": "verify the push", "input_chars": 15, "subagent": False},
                {"call": 0, "kind": "tool_result", "id": "child-1", "is_error": False,
                 "output": "verified", "output_chars": 8, "subagent": False}])
            with self.subTest(name=name):
                self.assert_push_unknown(evidence)

    def test_verbose_delegation_remains_unknown_with_partial_child_evidence(self):
        for name in ("Task", "Agent"):
            for parent_push in (False, True):
                for child_evidence in (False, True):
                    payload = verbose(*([f"git push origin {SHA}:feat/demo"]
                                        if parent_push else []))
                    payload.insert(-1, {"type": "assistant", "message": {"content": [
                        {"type": "tool_use", "id": "child-1", "name": name,
                         "input": {"prompt": "verify the push"}}]}})
                    if child_evidence:
                        for message in verbose(
                                f"git merge-base --is-ancestor {SHA} origin/feat/demo")[:-1]:
                            payload.insert(-1, {**message, "parent_tool_use_id": "child-1"})
                    payload.insert(-1, {"type": "user", "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "child-1",
                         "content": "Every command succeeded; verification complete.",
                         "is_error": False}]}})
                    with self.subTest(name=name, parent_push=parent_push,
                                      child_evidence=child_evidence):
                        answer, calls = self.run_replies(payload)
                        self.assertEqual(calls, 1)
                        self.assertIs(answer["tool_trace"]["complete"], True)
                        self.assert_push_unknown(answer["tool_trace"])

    def test_unquoted_expansion_in_push_arguments_is_unknown(self):
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        for argument in ("feat/*", "feat/?", "feat/[ab]", "feat/{a,b}",
                         "feat/{1..3}", "~example/feat/demo", 'feat/*"demo"'):
            with self.subTest(argument=argument):
                self.assert_push_unknown(trace(f"git push origin {argument}", check))
        self.assert_push_unknown(trace(f"git push ~example {SHA}:feat/demo", check))

    def test_unquoted_expansion_in_verification_arguments_is_unknown(self):
        push = f"git push origin {SHA}:feat/demo"
        for ref in ("origin/feat/*", "origin/feat/?", "origin/feat/[ab]",
                    "origin/feat/{a,b}", "origin/feat/{1..3}", "~example/feat/demo"):
            with self.subTest(ref=ref):
                self.assert_push_unknown(trace(push, f"git merge-base --is-ancestor {SHA} {ref}"))

    def test_quoted_and_escaped_expansion_characters_remain_literal(self):
        for argument, literal in (("'feat/*'", "feat/*"), ('"feat/?"', "feat/?"),
                                  ('feat/"[ab]"', "feat/[ab]"),
                                  (r"feat/\*", "feat/*"), (r"feat/\?", "feat/?"),
                                  (r"feat/\[ab\]", "feat/[ab]"),
                                  ("'feat/{a,b}'", "feat/{a,b}"),
                                  (r"feat/\{a,b\}", "feat/{a,b}"),
                                  ('"~example/demo"', "~example/demo"),
                                  (r"\~example/demo", "~example/demo")):
            with self.subTest(argument=argument):
                self.assertEqual(counters._commands(f"git push origin {argument}"),
                                 ([["git", "push", "origin", literal]], True))
        evidence = trace(f"git push origin '{SHA}:feat/demo'",
                         f'git merge-base --is-ancestor "{SHA}" origin/feat/"demo"')
        self.assertEqual(self.measure(evidence=evidence)["rules"]["push_without_verification"]
                         ["count"], 0)

    def test_same_length_input_incomplete_flag_without_markers_is_unknown(self):
        for commands in ((f"git push origin {SHA}:feat/demo",),
                         (f"git push origin {SHA}:feat/demo",
                          f"git merge-base --is-ancestor {SHA} origin/feat/demo")):
            evidence = trace(*commands)
            evidence["events"][0]["input_incomplete"] = True
            self.assertEqual(evidence["events"][0]["input_chars"],
                             len(evidence["events"][0]["input"]))
            with self.subTest(verified=len(commands) == 2):
                self.assert_push_unknown(evidence)

    def test_repository_assignments_do_not_supply_push_or_check_evidence(self):
        push = f"git push origin {SHA}:feat/demo"
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        for command in (push, check):
            self.assertEqual(counters._commands("GIT_DIR=other/.git " + command), ([], False))
        for evidence in (trace("GIT_DIR=other/.git " + push, check),
                         trace(push, "GIT_DIR=other/.git " + check)):
            with self.subTest(evidence=evidence):
                self.assert_push_unknown(evidence)

    def test_assignment_only_call_leaves_later_repository_context_unknown(self):
        self.assert_push_unknown(trace("GIT_DIR=other/.git",
                                       f"git push origin {SHA}:feat/demo",
                                       f"git merge-base --is-ancestor {SHA} origin/feat/demo"))

    def test_background_launch_does_not_supply_push_or_check_evidence(self):
        push = f"git push origin {SHA}:feat/demo"
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        for suffix in (" &", " & # launched\n", " &\n"):
            for command in (push, check):
                self.assertEqual(counters._commands(command + suffix), ([], False))
            for evidence in (trace(push, check + suffix), trace(push + suffix, check)):
                with self.subTest(evidence=evidence):
                    self.assert_push_unknown(evidence)

    def test_compound_verification_cannot_prove_a_minimum_omission(self):
        self.assert_push_unknown(trace(f"git push origin {SHA}:feat/demo",
                                       f"git fetch origin && git merge-base --is-ancestor {SHA} origin/feat/demo"))

    def test_incomplete_or_dynamic_later_verification_cannot_prove_a_minimum_omission(self):
        push = f"git push origin {SHA}:feat/demo"
        check = f"git merge-base --is-ancestor {SHA} origin/feat/demo"
        dynamic = trace(push, "git merge-base --is-ancestor $sha origin/feat/demo")
        clipped = trace(push, check)
        clipped["events"][2]["input_chars"] += 1
        omitted = trace(push)
        omitted["omitted_events"] = 2
        incomplete = trace(push, check)
        incomplete["events"][3]["output_incomplete"] = True
        missing = trace(push, check)
        missing["events"].pop()
        for evidence in (dynamic, clipped, omitted, incomplete, missing):
            with self.subTest(evidence=evidence):
                self.assert_push_unknown(evidence)

    def test_branch_verification_without_sha_binding_is_unknown(self):
        evidence = trace("git push -u origin feat/demo",
                         f"git merge-base --is-ancestor {SHA} origin/feat/demo")
        self.assertIsNone(self.measure(evidence=evidence)["rules"]["push_without_verification"]["count"])

    def test_incomplete_missing_dynamic_or_compound_traces_are_unknown(self):
        samples = [None, trace("git push origin $branch"),
                   trace("git push origin feat/demo; echo done"),
                   trace("git -C repo push origin feat/demo"),
                   trace("/usr/bin/git push origin feat/demo"),
                   trace("command git push origin feat/demo"),
                   trace("env git push origin feat/demo"),
                   trace("unused() { git push origin feat/demo; }"),
                   trace("git push"), trace("bash -c 'git push origin feat/demo'")]
        capped = trace("git push origin feat/demo")
        capped["omitted_events"] = 2
        samples.append(capped)
        unmatched = trace("git push origin feat/demo")
        unmatched["events"].pop()
        samples.append(unmatched)
        clipped = trace("git push origin feat/demo")
        clipped["events"][0]["input_chars"] = 300
        samples.append(clipped)
        redacted = trace("git push origin feat/demo")
        redacted["events"][1]["output_incomplete"] = True
        samples.append(redacted)
        for evidence in samples:
            with self.subTest(evidence=evidence):
                self.assertIsNone(counters.measure(counters.snapshot(self.workspace), self.workspace, evidence)
                                  ["rules"]["push_without_verification"]["count"])

    def test_bash_ast_does_not_execute_comments_or_heredoc_text(self):
        for source in ("# git push origin feat/demo\necho harmless",
                       "cat <<'EOF'\ngit push origin feat/demo\nEOF"):
            entry = self.measure(evidence=trace(source))["rules"]["push_without_verification"]
            self.assertEqual(entry["observed_count"], 0)

    def test_redacted_trace_records_evidence_loss_without_secret_content(self):
        payload = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tool-1", "name": "Bash",
             "input": {"command": "git push origin '" + "sensitive-value" + "'"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "sensitive-value"}]}}]
        events = cli_json.tool_events(payload, ["sensitive-value"])
        self.assertTrue(events[0]["input_incomplete"])
        self.assertTrue(events[1]["output_incomplete"])
        self.assertNotIn("sensitive-value", json.dumps(events))
        entry = self.measure(evidence=cli_json.bounded_tool_trace([events]))["rules"]["push_without_verification"]
        self.assertIsNone(entry["count"])

    def test_aggregation_includes_error_trials_and_missing_denominators(self):
        measured = self.measure(evidence=trace("git push origin feat/demo"))
        unknown = counters.measure(None, None, None)
        trials = [{"guidance_violations": measured, "error": {"type": "agent_error"}},
                  {"guidance_violations": unknown}, {}]
        block = run_eval.aggregate_trials(trials)["aggregate"]["guidance_violations"]
        self.assertEqual(block["push_without_verification"],
                         {"section_id": counters.RULES["push_without_verification"],
                          "n": 1, "n_missing": 2, "sum": 1})
        self.assertIsNone(counters.aggregate([{}]))

    def test_single_multi_and_guidance_reports_show_unknown_denominators(self):
        trial = {"arm": "without_skill", "error": None,
                 "guidance_violations": self.measure(evidence=trace("git push origin feat/demo"))}
        single = run_eval._render_report("demo", "prompt", "stamp", [trial])
        multi = run_eval._render_trials_report("demo", "stamp", 2, [{"label": "demo", "prompt": "prompt",
            "arms": [{"arm": "without_skill", "stats": run_eval.aggregate_trials([trial, {}])}]}])
        guidance = run_eval._render_guidance_report("git-practices", "prompt", "stamp", "user", {},
                                                   [{**trial, "mode": "none"}])
        for report in (single, multi, guidance):
            self.assertIn("push_without_verification (git-push-does-not-mean-commit-exists)", report)
        self.assertIn("1 / 1; 1 unknown", multi)

    def args(self):
        return argparse.Namespace(results_dir=Path(self.temp.name) / "results", timeout=None,
                                  no_judge=True, model=None, permission_mode="auto")

    def fixture(self):
        return run_eval.apply_runtime_subject({"subject": "any", "prompt": "fix"},
                                               "demo", None, "without_skill", Path("fixture.yaml"))

    def test_skill_runner_records_agent_and_scorer_errors_before_cleanup(self):
        fixture, args = self.fixture(), self.args()
        for scorer_error in (False, True):
            self.workspace.mkdir(exist_ok=True)
            output = Path(self.temp.name) / ("scorer" if scorer_error else "agent")
            def agent(*_args):
                self.write(workflow("      - uses: owner/action@main\n"))
                return ({"error": "agent_error"} if not scorer_error else {}) | {"tool_trace": trace("git push origin feat/demo")}
            with mock.patch.object(run_eval, "materialize_workspace", return_value=self.workspace), \
                    mock.patch.object(run_eval, "assert_stand_ins_on_path"), \
                    mock.patch.object(run_eval, "run_agent", side_effect=agent), \
                    mock.patch.object(run_eval.objective, "run_checks", side_effect=run_eval.objective.ScorerUnavailableError("unavailable")):
                result = run_eval._run_arm("without_skill", fixture, self.workspace, {}, args, "stamp",
                                           (None, None, None), out_dir=output)
            written = json.loads((output / "summary.json").read_text())
            self.assertEqual(written["guidance_violations"], result["guidance_violations"])
            self.assertEqual(written["guidance_violations"]["rules"]["unpinned_actions"]["count"], 1)
            self.assertEqual(written["guidance_violations"]["rules"]["push_without_verification"]["count"], 1)

    def test_pre_agent_refusal_is_unknown_in_summary_and_report(self):
        fixture, args = self.fixture(), self.args()
        output = Path(self.temp.name) / "refusal"
        with mock.patch.object(run_eval, "materialize_workspace") as materialize:
            result = run_eval._run_arm("without_skill", fixture, self.workspace, {}, args, "stamp",
                                       (None, None, "refused"), out_dir=output)
        materialize.assert_not_called()
        written = json.loads((output / "summary.json").read_text())
        self.assertEqual(written["guidance_violations"], result["guidance_violations"])
        for entry in written["guidance_violations"]["rules"].values():
            self.assertIsNone(entry["count"])
        self.assertIn("unknown", run_eval._render_report("demo", "prompt", "stamp", [result]))

    def test_guidance_trial_attaches_counters_before_scorer_failure(self):
        fixture = run_eval.apply_runtime_subject({"subject": "any", "prompt": "fix"},
                                                None, "git-practices", "both", Path("fixture.yaml"))
        arm = {"name": "none", "mode": "none", "objective_checks": []}
        ctx = {"delivery": "user", "section": "git-practices", "key": "guidance/git-practices/demo",
               "token": "demo", "decoys": {"none": "decoy"}, "row": {}, "guidance_dir": self.workspace}
        args = self.args()
        def agent(workspace, *_args):
            target = workspace / WORKFLOW
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(workflow("      - uses: owner/action@main\n"), encoding="utf-8")
            return {"tool_trace": trace("git push origin feat/demo")}
        with mock.patch.object(run_eval, "_git"), \
                mock.patch.object(run_eval.guidance, "assemble", return_value="payload"), \
                mock.patch.object(run_eval.guidance, "deliver", return_value={"bytes": 7, "verdict": "ok", "installed": True, "returncode": 0}), \
                mock.patch.object(run_eval.guidance, "run_guard", return_value={"ok": True}), \
                mock.patch.object(run_eval, "run_agent", side_effect=agent), \
                mock.patch.object(run_eval.objective, "run_checks", side_effect=run_eval.objective.ScorerUnavailableError("unavailable")):
            result = run_eval._run_guidance_arm(arm, fixture, self.workspace, ctx, args, "stamp")
        written = json.loads((args.results_dir / ctx["key"] / "stamp" / "none" / "summary.json").read_text())
        self.assertEqual(written["guidance_violations"], result["guidance_violations"])
        self.assertEqual(written["guidance_violations"]["rules"]["unpinned_actions"]["count"], 1)
        self.assertEqual(written["guidance_violations"]["rules"]["push_without_verification"]["count"], 1)

    def test_guidance_setup_failure_has_unknown_counters(self):
        fixture = {"_real_work": True, "subject": "guidance", "section": "git-practices"}
        arm = {"name": "none", "mode": "none"}
        ctx = {"section": "git-practices", "key": "guidance/git-practices/demo"}
        args = self.args()
        with mock.patch.object(run_eval.seed_prep, "prepare_seed", side_effect=run_eval.guidance.GuidanceError("unavailable")):
            result = run_eval._run_guidance_arm(arm, fixture, self.workspace, ctx, args, "stamp")
        written = json.loads((args.results_dir / ctx["key"] / "stamp" / "none" / "summary.json").read_text())
        self.assertEqual(written["guidance_violations"], result["guidance_violations"])
        self.assertEqual(written["guidance_violations"]["rules"]["unpinned_actions"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
