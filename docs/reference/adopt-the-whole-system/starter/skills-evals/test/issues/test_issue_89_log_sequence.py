"""Issue #89: ordered offline calls carry typed values and bounded polls."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "harness"))
from scorers import objective


class TestLogSequence(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="log-sequence-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.log = self.workspace / ".gh-invocations.log"
        self.payload = self.workspace / ".gh/replay/workflow-run-deploy-preview.json"
        self.payload.parent.mkdir(parents=True)
        self.payload.write_text(json.dumps({"databaseId": 7001}))
        self.events = [
            {"match": {"class": "write", "key": "workflow-run-deploy-preview.json", "exit": 0},
             "captures": {"run_id": {"type": "integer", "path": str(self.payload.relative_to(self.workspace)),
                                      "field": "databaseId"}}, "max": 1},
            {"match": {"class": "read", "key": "run-view-${run_id}.json",
                       "argv_prefix": ["run", "view", "${run_id}"], "exit": 0}, "min": 3, "max": 3},
        ]
        self._write()

    def _record(self, klass, key, argv, *, code=0, count=None):
        escaped = key.encode("unicode_escape").decode("ascii").replace(" ", "\\x20")
        step = "" if count is None else f" count={count}"
        return f"--- invocation (class={klass} key={escaped} exit={code}{step}) --- {json.dumps(argv)}\n"

    def _write(self, *, run_id="7001", polls=3, reversed_order=False):
        dispatch = self._record("write", "workflow-run-deploy-preview.json", ["workflow", "run", "deploy-preview"])
        reads = "".join(self._record("read", f"run-view-{run_id}.json", ["run", "view", run_id], count=count)
                        for count in range(1, polls + 1))
        self.log.write_text(reads + dispatch if reversed_order else dispatch + reads)

    def _score(self, events=None):
        return objective.log_sequence(str(self.workspace), [self.log.name],
                                      events=self.events if events is None else events)

    def _fixture(self, events=None):
        return {"objective_checks": [{"id": "sequence", "type": "log_sequence", "paths": [self.log.name],
                                       "events": self.events if events is None else events}]}

    def test_scripted_correct_loop_passes_and_routes_through_registry(self):
        self.assertTrue(self._score()[0])
        results = objective.run_checks(self._fixture(), str(self.workspace), str(self.workspace))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["passed"])

    def test_returned_run_id_must_be_carried_into_key_and_argv(self):
        self._write(run_id="7002")
        self.assertFalse(self._score()[0])
        self._write()
        self.log.write_text(self.log.read_text().replace('["run", "view", "7001"]', '["run", "view", "7002"]'))
        self.assertFalse(self._score()[0])

    def test_excess_polls_fail_even_when_an_earlier_subsequence_is_correct(self):
        self._write(polls=4)
        self.assertFalse(self._score()[0])

    def test_poll_before_dispatch_fails_order(self):
        self._write(reversed_order=True)
        self.assertIn("out of order", self._score()[1])

    def test_minimum_occurrence_bound_rejects_unfinished_loop(self):
        self._write(polls=2)
        self.assertFalse(self._score()[0])

    def test_optional_bound_counts_every_occurrence_including_before_dispatch(self):
        events = copy.deepcopy(self.events)
        events[-1] = {"match": {"class": "read", "key": ["run-list.json", "run-view-${run_id}.json"]},
                      "min": 0, "max": 3}
        self.assertTrue(self._score(events)[0])
        before = self._record("read", "run-list.json", ["run", "list"])
        self.log.write_text(before + self.log.read_text())
        self.assertFalse(self._score(events)[0])
        self._write(polls=0)
        self.assertTrue(self._score(events)[0])

    def test_string_capture_nested_field_is_literal_not_regex(self):
        self.payload.write_text(json.dumps({"nested": {"name": "a.+[b]"}}))
        events = copy.deepcopy(self.events)
        events[0]["captures"]["run_id"].update(type="string", field="nested.name")
        self._write(run_id="a.+[b]")
        self.assertTrue(self._score(events)[0])
        self._write(run_id="axxb")
        self.assertFalse(self._score(events)[0])

    def test_capture_missing_wrong_type_or_invalid_json_fails_closed(self):
        for value in ({}, {"databaseId": True}, {"databaseId": "7001"}, [], "bad-json"):
            with self.subTest(value=value):
                self.payload.write_text(value if isinstance(value, str) else json.dumps(value))
                self.assertFalse(self._score()[0])
        self.payload.unlink()
        self.assertFalse(self._score()[0])

    def test_missing_empty_malformed_or_forged_log_fails_closed(self):
        self.log.unlink()
        self.assertFalse(self._score()[0])
        for text in ("", "not an invocation\n", self._record("read", "run-list.json", ["run\nview"]),
                     '--- invocation (class=read key=run-list.json exit=0) --- {"run": "list"}\n',
                     '--- invocation (class=read key=run-list.json exit=0) --- [1]\n'):
            with self.subTest(text=text):
                self.log.write_text(text)
                self.assertFalse(self._score()[0])

    def test_paths_cannot_escape_workspace_or_use_globs(self):
        for path in ("../outside", "/absolute", "*.log", "a\x00b", None):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    objective.log_sequence(str(self.workspace), [path], events=self.events)
        target = self.workspace / "outside-link"
        target.symlink_to(self.workspace.parent)
        self.assertFalse(objective.log_sequence(str(self.workspace), ["outside-link/evidence"], events=self.events)[0])
        events = copy.deepcopy(self.events)
        events[0]["captures"]["run_id"]["path"] = "outside-link/payload.json"
        self.assertFalse(self._score(events)[0])

    def test_unknown_top_level_constraint_is_rejected(self):
        fixture = self._fixture()
        fixture["objective_checks"][0]["event"] = self.events
        with self.assertRaisesRegex(ValueError, "constraint key"):
            objective.run_checks(fixture, str(self.workspace), str(self.workspace))

    def test_malformed_nested_constraints_are_rejected_before_scoring(self):
        cases = []
        for location, key, value in (((), "extra", True), ((), "min", True), ((), "max", None),
                                     ((), "min", -1), ((), "max", 0), (("match",), "extra", 1),
                                     (("match",), "exit", False), (("match",), "class", "denied"),
                                     (("match",), "key", []), (("match",), "argv_prefix", "run"),
                                     (("captures", "run_id"), "extra", 1),
                                     (("captures", "run_id"), "type", "boolean"),
                                     (("captures", "run_id"), "field", "nested..id"),
                                     (("captures", "run_id"), "path", "../payload")):
            events = copy.deepcopy(self.events)
            target = events[0]
            for segment in location:
                target = target[segment]
            target[key] = value
            cases.append(events)
        events = copy.deepcopy(self.events)
        events[0]["min"] = 0
        cases.append(events)
        events = copy.deepcopy(self.events)
        events[1]["captures"] = copy.deepcopy(events[0]["captures"])
        cases.append(events)
        for key in ("run-view-${missing}.json", "run-view-${bad-reference}.json"):
            events = copy.deepcopy(self.events)
            events[0]["match"]["key"] = key
            cases.append(events)
        for events in cases + [None, [], [None], [{"match": {}}]]:
            with self.subTest(events=events), self.assertRaises(ValueError):
                objective.run_checks(self._fixture(events) if events is not None else
                                     {"objective_checks": [{"id": "sequence", "type": "log_sequence",
                                                             "paths": [self.log.name], "events": None}]},
                                     str(self.workspace), str(self.workspace))

    @staticmethod
    def _mutate_guard(expression):
        """Compile just the scorer in memory; no repo copy or inherited git reach."""
        tree = ast.parse(Path(objective.__file__).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "log_sequence")
        target = ast.dump(ast.parse(expression, mode="eval").body)
        changed = 0
        for node in ast.walk(function):
            if isinstance(node, ast.If) and ast.dump(node.test) == target:
                node.test = ast.Constant(False)
                changed += 1
            elif isinstance(node, ast.Assign) and ast.dump(node.value) == target:
                node.value = ast.Name(id="indices", ctx=ast.Load())
                changed += 1
        if changed != 1:
            raise AssertionError(f"expected one AST guard mutation, got {changed}")
        namespace = dict(objective.__dict__)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "<log-sequence-mutant>", "exec"), namespace)
        return namespace["log_sequence"]

    def test_source_mutations_demonstrate_guards_are_load_bearing(self):
        # Each mutant is code in a private dictionary, not a modified worktree.
        with mock.patch.dict(objective.CHECKS, {"log_sequence": lambda *args, **kwargs: (False, "mutated")}):
            with self.assertRaises(AssertionError):
                self.test_scripted_correct_loop_passes_and_routes_through_registry()
        for expression, change in (
            ("len(indices) < minimum or (maximum is not None and len(indices) > maximum)",
             lambda: self._write(polls=4)),
            ("[index for index in indices if index > cursor]", lambda: self._write(reversed_order=True)),
        ):
            with self.subTest(guard=expression):
                change()
                self.assertFalse(self._score()[0])
                mutant = self._mutate_guard(expression)
                # The original negative assertion goes red under this mutant.
                with self.assertRaises(AssertionError):
                    self.assertFalse(mutant(str(self.workspace), [self.log.name], events=self.events)[0])
        self._write(run_id="7002")
        with mock.patch.object(objective, "_log_sequence_matches",
                               side_effect=lambda record, match, captures: record["class"] == match.get("class")):
            with self.assertRaises(AssertionError):
                self.assertFalse(self._score()[0])
        self._write()
        malformed = copy.deepcopy(self.events)
        malformed[0]["extra"] = True
        with mock.patch.object(objective, "_validate_log_sequence", return_value=None):
            with self.assertRaises(AssertionError):
                with self.assertRaises(ValueError):
                    self._score(malformed)


if __name__ == "__main__":
    unittest.main()
