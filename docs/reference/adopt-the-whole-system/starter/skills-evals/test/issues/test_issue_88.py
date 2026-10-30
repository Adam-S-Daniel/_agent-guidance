"""Hermetic, mutation-covered contracts for the two opt-in checks in ADR 0007."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
from scorers import bash_ast, objective


WIDTH_PATH = ["linters", "settings", "lll", "line-length"]
EXPECT = [{"path": WIDTH_PATH, "equals": 100},
          {"path": ["linters", "enable"], "contains": "lll"}]
CONFIG = "linters:\n  settings:\n    lll:\n      line-length: 100\n  enable: [lll, govet]\n"
SOURCE = "files=$(git diff --cached --name-only -- '*.go')\n"
CALL = "gofmt -w $files"
GUARDED = 'if [ -n "$files" ] && command -v gofmt >/dev/null 2>&1; then\n  ' + CALL + '\nelse\n  echo skipped\nfi\n'


class CheckWorkspace(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.target = self.workspace / "input"

    def config(self, text=CONFIG, **kwargs):
        if text is not None:
            self.target.write_text(text, encoding="utf-8")
        return objective.parsed_config_values(
            str(self.workspace), kwargs.pop("paths", ["input"]),
            **({"format": "yaml", "expected": EXPECT} | kwargs))

    def shell(self, text=SOURCE + GUARDED, **kwargs):
        if text is not None:
            self.target.write_text(text, encoding="utf-8")
        return objective.shell_staged_tool_guard(
            str(self.workspace), kwargs.pop("paths", ["input"]),
            **({"tools": ["gofmt"]} | kwargs))

    def assert_reason(self, result, reason):
        self.assertFalse(result[0], result[1])
        self.assertEqual(result[1].split(": ", 1)[1], reason)


class ParsedConfigValues(CheckWorkspace):
    def test_yaml_typed_width_and_linter_list(self):
        self.assertTrue(self.config()[0])

    def test_json_typed_width_and_linter_list(self):
        self.assertTrue(self.config(json.dumps(yaml.safe_load(CONFIG)), format="json")[0])

    def test_parse_only_json_manifest(self):
        self.assertTrue(self.config('{"scripts": {"lint": "echo lint"}}', format="json", expected=[])[0])

    def test_omitted_expectations_parse_only(self):
        self.target.write_text('{}')
        self.assertTrue(objective.parsed_config_values(str(self.workspace), ["input"], format="json")[0])

    def test_typed_recursive_equality(self):
        self.assertTrue(self.config('{"x": [{"n": 100, "b": true, "nil": null}]}', format="json",
                                    expected=[{"path": ["x"], "equals": [{"n": 100, "b": True, "nil": None}]}])[0])

    def test_benign_yaml_alias(self):
        self.assertTrue(self.config('x: &x [100, true]\ny: *x\n', expected=[{"path": ["y"], "equals": [100, True]}])[0])

    def test_internal_symlink(self):
        (self.workspace / "data").write_text(CONFIG)
        self.target.symlink_to(self.workspace / "data")
        self.assertTrue(self.config(None)[0])

    def test_multiple_files_all_must_pass(self):
        (self.workspace / "other").write_text(CONFIG.replace('100', '80'))
        self.assert_reason(self.config(paths=["input", "other"]), "value_mismatch")

    def test_bool_list_member_does_not_equal_integer(self):
        self.assert_reason(self.config('x: [true]\n', expected=[{"path": ["x"], "contains": 1}]), "list_member_missing")

    def test_nested_bool_does_not_equal_integer(self):
        self.assert_reason(self.config('x: {n: true}\n', expected=[{"path": ["x"], "equals": {"n": 1}}]), "value_mismatch")

    def test_parse_error_does_not_expose_file_contents(self):
        result = self.config('private-content: [unterminated')
        self.assert_reason(result, "invalid_yaml")
        self.assertNotIn("private-content", result[1])

    def test_symlink_outside_workspace(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        data = Path(outside.name) / "data"
        data.write_text(CONFIG)
        self.target.symlink_to(data)
        self.assert_reason(self.config(None), "path_outside_workspace")

    def test_regular_file_required(self):
        self.target.mkdir()
        self.assert_reason(self.config(None), "not_regular_file")

    def test_recursive_expectation_rejected(self):
        value = []
        value.append(value)
        self.assert_reason(self.config(expected=[{"path": ["x"], "equals": value}]), "structure_limit")

    def test_symlink_loop_rejected(self):
        self.target.symlink_to(self.target)
        self.assert_reason(self.config(None), "unreadable_path")


CONFIG_FAILURES = {
    "missing_file": (None, {}, "missing_file"),
    "width_80": (CONFIG.replace('100', '80'), {}, "value_mismatch"),
    "commented_width": (CONFIG.replace('line-length: 100', '# line-length: 100\n      other: 80'), {}, "missing_key_3"),
    "unrelated_nested_key": ('unrelated:\n' + ''.join('  ' + line + '\n' for line in CONFIG.splitlines()), {}, "missing_key_0"),
    "string_width": (CONFIG.replace('100', '"100"'), {}, "value_type_mismatch"),
    "duplicate_width": (CONFIG.replace('line-length: 100', 'line-length: 80\n      line-length: 100'), {}, "duplicate_key"),
    "invalid_json_accepted_by_yaml": ('{linters: {settings: {lll: {line-length: 100}}, enable: [lll]}}', {"format": "json"}, "invalid_json"),
    "missing_linter": (CONFIG.replace('[lll, govet]', '[govet]'), {}, "list_member_missing"),
    "linter_not_list": (CONFIG.replace('[lll, govet]', 'lll'), {}, "contains_not_list"),
    "float_width": (CONFIG.replace('100', '100.0'), {}, "value_type_mismatch"),
    "bool_width": (CONFIG.replace('100', 'true'), {}, "value_type_mismatch"),
    "duplicate_unrelated_yaml_key": (CONFIG + 'extra: a\nextra: b\n', {}, "duplicate_key"),
    "duplicate_json_key": ('{"x": {"width": 80, "width": 100}}', {"format": "json", "expected": []}, "duplicate_key"),
    "json_nan": ('{"x": NaN}', {"format": "json", "expected": []}, "invalid_json"),
    "json_infinity": ('{"x": 1e999}', {"format": "json", "expected": []}, "invalid_structure"),
    "json_trailing_comma": ('{"x": 100,}', {"format": "json", "expected": []}, "invalid_json"),
    "yaml_control_character": ('x: \x00', {"expected": []}, "invalid_yaml"),
    "json_escaped_duplicate_key": ('{"x": 80, "\\u0078": 100}', {"format": "json", "expected": []}, "duplicate_key"),
    "nonmapping_root": ('[100]', {"expected": []}, "root_not_mapping"),
    "empty_yaml": ('', {"expected": []}, "root_not_mapping"),
    "complex_key": ('? [a, b]\n: 100\n', {"expected": []}, "invalid_structure"),
    "numeric_key": ('1: 100\n', {"expected": []}, "invalid_structure"),
    "merge_key": ('x: &x {width: 80}\ny: {<<: *x, width: 100}', {"expected": []}, "invalid_structure"),
    "custom_tag": ('x: !custom 100', {"expected": []}, "invalid_structure"),
    "yaml_timestamp": ('x: 2026-01-01', {"expected": []}, "invalid_structure"),
    "recursive_alias": ('x: &x [*x]', {"expected": []}, "structure_limit"),
    "too_deep": ('x: ' + '[' * 65 + '1' + ']' * 65, {"expected": []}, "structure_limit"),
    "too_many_nodes": ('x: [' + ','.join(['0'] * 4100) + ']', {"expected": []}, "structure_limit"),
    "oversize": ('#' + 'x' * 65536, {}, "input_limit"),
    "nonmapping_path": ('linters: []', {}, "path_not_mapping"),
    "null_expected": (CONFIG, {"expected": None}, "invalid_constraints"),
    "both_operators": (CONFIG, {"expected": [{"path": WIDTH_PATH, "equals": 100, "contains": 100}]}, "invalid_constraints"),
    "unknown_operator": (CONFIG, {"expected": [{"path": WIDTH_PATH, "equal": 100}]}, "invalid_constraints"),
    "empty_key_path": (CONFIG, {"expected": [{"path": [], "equals": 100}]}, "invalid_constraints"),
    "nonstring_key_path": (CONFIG, {"expected": [{"path": [1], "equals": 100}]}, "invalid_constraints"),
    "no_paths": (CONFIG, {"paths": []}, "invalid_constraints"),
    "absolute_path": (CONFIG, {"paths": ["/input"]}, "invalid_path"),
    "parent_path": (CONFIG, {"paths": ["../input"]}, "invalid_path"),
    "glob_path": (CONFIG, {"paths": ["*"]}, "invalid_path"),
    "missing_format": (CONFIG, {"format": None}, "invalid_constraints"),
}


def config_failure(text, kwargs, reason):
    def test(self):
        self.assert_reason(self.config(text, **kwargs), reason)
    return test


for case, (text, kwargs, reason) in CONFIG_FAILURES.items():
    setattr(ParsedConfigValues, "test_" + case, config_failure(text, kwargs, reason))


class ShellStagedToolGuard(CheckWorkspace):
    def test_guard_and_staged_paths(self):
        self.assertTrue(self.shell()[0])

    def test_guards_in_nested_ifs(self):
        text = SOURCE + 'if command -v gofmt; then\nif [ -n "$files" ]; then ' + CALL + '; fi\nfi'
        self.assertTrue(self.shell(text)[0])

    def test_real_availability_helper(self):
        text = 'have() { command -v "$1" >/dev/null 2>&1; }\n' + SOURCE + GUARDED.replace('command -v gofmt', 'have gofmt')
        self.assertTrue(self.shell(text)[0])

    def test_type_helper(self):
        text = 'have() { type -P "$1"; }\n' + SOURCE + GUARDED.replace('command -v gofmt', 'have gofmt')
        self.assertTrue(self.shell(text)[0])

    def test_type_availability(self):
        self.assertTrue(self.shell(SOURCE + GUARDED.replace('command -v', 'type'))[0])

    def test_hash_availability(self):
        self.assertTrue(self.shell(SOURCE + GUARDED.replace('command -v', 'hash'))[0])

    def test_staged_flag_and_diff_filter(self):
        self.assertTrue(self.shell((SOURCE + GUARDED).replace('--cached', '--staged --diff-filter=ACM'))[0])

    def test_grep_filter(self):
        self.assertTrue(self.shell(SOURCE.replace("-- '*.go'", "| grep '\\.go$'") + GUARDED)[0])

    def test_grep_extended_filter(self):
        self.assertTrue(self.shell(SOURCE.replace("-- '*.go'", "| grep -E '\\.go$'") + GUARDED)[0])

    def test_array_paths_and_count(self):
        text = 'files=($(git diff --cached --name-only -- "*.go"))\n'
        text += 'if [[ ${#files[@]} -gt 0 ]] && command -v gofmt; then gofmt -w "${files[@]}"; fi'
        self.assertTrue(self.shell(text)[0])

    def test_braced_scalar_paths(self):
        self.assertTrue(self.shell((SOURCE + GUARDED).replace('$files', '${files}'))[0])

    def test_successful_missing_and_empty_early_skips(self):
        text = SOURCE + 'if ! command -v gofmt; then exit 0; fi\n[ -z "$files" ] && exit 0\n' + CALL
        self.assertTrue(self.shell(text)[0])

    def test_negated_empty_condition(self):
        self.assertTrue(self.shell(SOURCE + GUARDED.replace('[ -n "$files" ]', '! [ -z "$files" ]'))[0])

    def test_and_chain_dominates_call(self):
        self.assertTrue(self.shell(SOURCE + '[ -n "$files" ] && command -v gofmt && ' + CALL + '\nexit 0')[0])

    def test_two_configured_tools(self):
        text = SOURCE + GUARDED + GUARDED.replace('gofmt', 'golangci-lint').replace('golangci-lint -w', 'golangci-lint run')
        self.assertTrue(self.shell(text, tools=["gofmt", "golangci-lint"])[0])

    def test_each_path_must_qualify(self):
        (self.workspace / "other").write_text(SOURCE + CALL)
        self.assert_reason(self.shell(paths=["input", "other"]), "availability_guard_missing")

    def test_parser_dependency_absent_is_named(self):
        original = builtins.__import__
        def without_parser(name, *args, **kwargs):
            if name == "tree_sitter":
                raise ImportError("deliberately absent")
            return original(name, *args, **kwargs)
        with mock.patch("builtins.__import__", side_effect=without_parser):
            with self.assertRaises(bash_ast.BashParseError) as ctx:
                bash_ast.parse_bash("true")
        self.assertEqual(str(ctx.exception), "parser_unavailable")

    def test_parser_dependency_absent_is_a_scorer_error_not_a_failed_check(self):
        original = builtins.__import__
        def without_parser(name, *args, **kwargs):
            if name == "tree_sitter":
                raise ImportError("deliberately absent")
            return original(name, *args, **kwargs)
        self.target.write_text(SOURCE + GUARDED, encoding="utf-8")
        with mock.patch("builtins.__import__", side_effect=without_parser):
            with self.assertRaises(objective.ScorerUnavailableError) as ctx:
                self.shell(None)
            # Whatever the workspace holds, including nothing at all.
            with self.assertRaises(objective.ScorerUnavailableError):
                self.shell(None, paths=["missing"])
            fixture = {"objective_checks": [{"id": "g", "type": "shell_staged_tool_guard",
                                             "paths": ["input"], "tools": ["gofmt"]}]}
            with self.assertRaises(objective.ScorerUnavailableError):
                objective.run_checks(fixture, str(self.workspace), str(self.workspace))
        self.assertNotIsInstance(ctx.exception, ValueError)
        for needle in ("tree-sitter==0.26.0", "tree-sitter-bash==0.25.1", "pip install"):
            self.assertIn(needle, str(ctx.exception))

    def test_parser_present_scores_unchanged(self):
        self.assertTrue(self.shell()[0])
        self.assert_reason(self.shell(SOURCE + CALL), "availability_guard_missing")

    def test_external_symlink(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        data = Path(outside.name) / "script"
        data.write_text(SOURCE + GUARDED)
        self.target.symlink_to(data)
        self.assert_reason(self.shell(None), "path_outside_workspace")


SHELL_FAILURES = {
    "missing_file": (None, {}, "missing_file"),
    "comments_only": (SOURCE + '# ' + CALL, {}, "tool_not_invoked"),
    "string_only": (SOURCE + 'echo "gofmt -w $files"', {}, "tool_not_invoked"),
    "heredoc_only": (SOURCE + "cat <<'EOF'\n" + CALL + '\nEOF\n', {}, "tool_not_invoked"),
    "unrelated_guard": (SOURCE + GUARDED.replace('command -v gofmt', 'command -v other'), {}, "availability_guard_missing"),
    "always_false_branch": (SOURCE + 'if false; then\n' + GUARDED + '\nfi', {}, "tool_not_invoked"),
    "disconnected_guard": (SOURCE + 'if command -v gofmt; then echo ready; fi\nif [ -n "$files" ]; then ' + CALL + '; fi', {}, "availability_guard_missing"),
    "unconditional_call": (SOURCE + CALL, {}, "availability_guard_missing"),
    "unstaged_scan": ('files=$(find . -name "*.go")\n' + GUARDED, {}, "unsupported_staged_source"),
    "unstaged_git_diff": (SOURCE.replace('--cached ', '') + GUARDED, {}, "unsupported_staged_source"),
    "unfiltered_staged_files": (SOURCE.replace(" -- '*.go'", '') + GUARDED, {}, "unsupported_staged_source"),
    "grep_nonanchored_suffix": (SOURCE.replace("-- '*.go'", "| grep 'go'") + GUARDED, {}, "unsupported_staged_source"),
    "helper_always_true": ('have() { true; }\n' + SOURCE + GUARDED.replace('command -v gofmt', 'have gofmt'), {}, "unsupported_helper"),
    "helper_check_disconnected": ('have() { command -v "$1"; true; }\n' + SOURCE + GUARDED, {}, "unsupported_helper"),
    "missing_tool_nonzero_exit": (SOURCE + 'if command -v gofmt; then\nif [ -n "$files" ]; then ' + CALL + '; fi\nelse exit 1; fi', {}, "missing_tool_nonzero_exit"),
    "missing_nonempty_guard": (SOURCE + 'if command -v gofmt; then ' + CALL + '; fi', {}, "nonempty_guard_missing"),
    "unrelated_nonempty_variable": (SOURCE + GUARDED.replace('[ -n "$files" ]', '[ -n "$other" ]'), {}, "nonempty_guard_missing"),
    "wrong_argument_variable": (SOURCE + GUARDED.replace(CALL, 'gofmt -w $other'), {}, "staged_paths_missing"),
    "extra_unstaged_scan": (SOURCE + GUARDED.replace(CALL, CALL + ' ./...'), {}, "unsupported_tool_arguments"),
    "stale_provenance": (SOURCE + 'files="all.go"\n' + GUARDED, {}, "staged_paths_missing"),
    "append_unstaged_prefix": ('files="unstaged.go"\n' + SOURCE.replace('files=', 'files+=') + GUARDED, {}, "unsupported_staged_source"),
    "suppressed_source_output": (SOURCE.replace("'*.go'", "'*.go' >/dev/null") + GUARDED, {}, "unsupported_staged_source"),
    "stale_nonempty_fact": (SOURCE + 'if [ -n "$files" ] && command -v gofmt; then\n' + SOURCE + CALL + '\nfi', {}, "nonempty_guard_missing"),
    "missing_tool_false_status": (SOURCE + 'if command -v gofmt; then\nif [ -n "$files" ]; then ' + CALL + '; fi\nelse false; fi', {}, "missing_tool_nonzero_exit"),
    "one_unguarded_call": (SOURCE + GUARDED + CALL, {}, "availability_guard_missing"),
    "missing_configured_tool": (SOURCE + GUARDED, {"tools": ["gofmt", "golangci-lint"]}, "tool_not_invoked"),
    "eval": (SOURCE + GUARDED + '\neval "gofmt $files"', {}, "unsupported_dynamic_form"),
    "source": (SOURCE + GUARDED + '\nsource extra.sh', {}, "unsupported_dynamic_form"),
    "dot_source": (SOURCE + GUARDED + '\n. extra.sh', {}, "unsupported_dynamic_form"),
    "alias": (SOURCE + GUARDED + '\nalias gofmt=true', {}, "unsupported_dynamic_form"),
    "indirect_expansion": (SOURCE + GUARDED + '\necho "${!files}"', {}, "unsupported_dynamic_form"),
    "dynamic_command": (SOURCE + GUARDED + '\n"$tool" $files', {}, "unsupported_dynamic_form"),
    "heredoc_substitution": (SOURCE + GUARDED + '\ncat <<EOF\n$(gofmt $files)\nEOF', {}, "unsupported_dynamic_form"),
    "loop": (SOURCE + 'for f in $files; do\n' + GUARDED + '\ndone', {}, "unsupported_control_flow"),
    "background_call": (SOURCE + GUARDED.replace(CALL, CALL + ' &'), {}, "unsupported_control_flow"),
    "negated_availability": (SOURCE + GUARDED.replace('command -v gofmt', '! command -v gofmt'), {}, "availability_guard_missing"),
    "or_guard": (SOURCE + GUARDED.replace(' && ', ' || '), {}, "nonempty_guard_missing"),
    "semicolons_in_condition": (SOURCE + GUARDED.replace(' && ', '; '), {}, "unsupported_condition"),
    "unquoted_nonempty_scalar": (SOURCE + GUARDED.replace('"$files"', '$files'), {}, "unsupported_condition"),
    "invalid_bash": (SOURCE + 'if command -v gofmt; then ' + CALL, {}, "invalid_bash"),
    "no_tools": (SOURCE + GUARDED, {"tools": []}, "invalid_constraints"),
    "dynamic_tool_constraint": (SOURCE + GUARDED, {"tools": ["$tool"]}, "invalid_constraints"),
    "duplicate_tools": (SOURCE + GUARDED, {"tools": ["gofmt", "gofmt"]}, "invalid_constraints"),
    "no_paths": (SOURCE + GUARDED, {"paths": []}, "invalid_constraints"),
    "glob_path": (SOURCE + GUARDED, {"paths": ['*']}, "invalid_path"),
}


def shell_failure(text, kwargs, reason):
    def test(self):
        self.assert_reason(self.shell(text, **kwargs), reason)
    return test


for case, (text, kwargs, reason) in SHELL_FAILURES.items():
    setattr(ShellStagedToolGuard, "test_" + case, shell_failure(text, kwargs, reason))


SHELL_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "issue88"
ARRAY_SOURCE = "mapfile -t files < <(git diff --cached --name-only -- '*.go')\n"
ARRAY_GUARDED = 'if [ "${#files[@]}" -gt 0 ] && command -v gofmt; then gofmt -l "${files[@]}"; fi\n'


class ShellReviewRegressions(CheckWorkspace):
    def fixture(self, name):
        return (SHELL_FIXTURES / name).read_text(encoding="utf-8")

    def test_reference_hook_with_go_branch(self):
        result = self.shell(self.fixture("lint-staged-go.sh"), tools=["gofmt", "golangci-lint"])
        self.assertTrue(result[0], result[1])

    def test_while_read_style(self):
        result = self.shell(self.fixture("while-read-go.sh"))
        self.assertTrue(result[0], result[1])

    def test_and_or_capture_style(self):
        result = self.shell(self.fixture("and-or-capture-go.sh"))
        self.assertTrue(result[0], result[1])

    def test_xargs_rc_style(self):
        result = self.shell(self.fixture("xargs-rc-go.sh"))
        self.assertTrue(result[0], result[1])

    def test_terminal_and_list_missing_tool_status(self):
        self.assert_reason(self.shell(SOURCE + '[ -n "$files" ] && command -v gofmt >/dev/null && gofmt -l $files'),
                           "missing_tool_nonzero_exit")

    def test_terminal_or_missing_tool_status(self):
        text = SOURCE + GUARDED + 'command -v gofmt >/dev/null || false'
        self.assert_reason(self.shell(text), "missing_tool_nonzero_exit")

    def test_negation_cannot_erase_missing_branch_status(self):
        text = SOURCE + 'if [ -n "$files" ]; then if command -v gofmt; then ' + CALL + '; else ! true; fi; fi'
        self.assert_reason(self.shell(text), "missing_tool_nonzero_exit")

    def test_successful_other_tool_cannot_erase_missing_failure(self):
        text = SOURCE + 'if [ -n "$files" ]; then if command -v gofmt; then gofmt -l $files; else '
        text += 'if command -v golangci-lint; then golangci-lint run $files; exit 1; else exit 0; fi; fi; fi; exit 0'
        self.assert_reason(self.shell(text, tools=["gofmt", "golangci-lint"]), "missing_tool_nonzero_exit")

    def test_saved_other_linter_failure_can_exit_nonzero(self):
        text = SOURCE + 'RC=0\n' + GUARDED + GUARDED.replace('gofmt', 'golangci-lint').replace('golangci-lint -w $files', 'golangci-lint run $files || RC=1')
        text += 'exit "$RC"'
        self.assertTrue(self.shell(text, tools=["gofmt", "golangci-lint"])[0])

    def test_standalone_query_with_errexit_blocks_missing_tool(self):
        self.assert_reason(self.shell('set -e\n' + SOURCE + 'command -v gofmt >/dev/null\n' + GUARDED),
                           "missing_tool_nonzero_exit")

    def test_if_without_else_has_success_status(self):
        self.assertTrue(self.shell(SOURCE + 'if [ -n "$files" ] && command -v gofmt; then gofmt -l $files; fi')[0])

    def test_set_builtins_and_stderr_notice(self):
        self.assertTrue(self.shell('set -e\n' + SOURCE + GUARDED.replace('echo skipped', 'echo skip >&2'))[0])

    def test_scalar_quoted_paths_rejected(self):
        self.assert_reason(self.shell(SOURCE + GUARDED.replace(CALL, 'gofmt -l "$files"')), "scalar_paths_quoted")

    def test_braced_scalar_quoted_paths_rejected(self):
        self.assert_reason(self.shell(SOURCE + GUARDED.replace(CALL, 'gofmt -l "${files}"')), "scalar_paths_quoted")

    def test_nonzero_rc_from_missing_tool_rejected(self):
        self.assert_reason(self.shell(SOURCE + 'RC=0\n' + GUARDED.replace('echo skipped', 'RC=1') + 'exit "$RC"'),
                           "missing_tool_nonzero_exit")

    def test_missing_branch_comparison_does_not_erase_cause(self):
        self.assert_reason(self.shell(SOURCE + 'RC=0\n' + GUARDED.replace('echo skipped', '[ "$RC" -eq 0 ]; exit 1')),
                           "missing_tool_nonzero_exit")

    def test_output_capture_call_still_needs_guards(self):
        self.assert_reason(self.shell(ARRAY_SOURCE + 'bad=$(gofmt -l "${files[@]}")\n'), "availability_guard_missing")

    def test_output_capture_call_still_needs_nonempty(self):
        self.assert_reason(self.shell(ARRAY_SOURCE + 'if command -v gofmt; then bad=$(gofmt -l "${files[@]}"); fi'),
                           "nonempty_guard_missing")

    def test_unstaged_while_read_rejected(self):
        self.assert_reason(self.shell(self.fixture("while-read-go.sh").replace('--cached ', '')),
                           "unsupported_staged_source")

    def test_while_read_pipeline_subshell_rejected(self):
        text = 'files=()\ngit diff --cached --name-only -z -- "*.go" | while IFS= read -r -d "" f; do files+=("$f"); done\n'
        self.assert_reason(self.shell(text + ARRAY_GUARDED), "unsupported_control_flow")

    def test_filter_helper_cannot_supply_unstaged_paths(self):
        text = self.fixture("lint-staged-go.sh").replace('&& printf \'%s\\n\' "$f"', '&& printf \'%s\\n\' unstaged.go')
        self.assert_reason(self.shell(text, tools=["gofmt", "golangci-lint"]), "unsupported_helper")

    def test_wrapper_does_not_hide_call_using_other_array(self):
        text = self.fixture("lint-staged-go.sh").replace('node_modules/.bin/eslint "${JS[@]}"', 'env gofmt ./... "${JS[@]}"')
        self.assert_reason(self.shell(text, tools=["gofmt", "golangci-lint"]), "unsupported_command")

    def test_helper_cannot_shadow_exit(self):
        text = SOURCE + 'exit() { echo harmless; }; command -v gofmt || exit 0; [ -n "$files" ] && gofmt -l $files'
        self.assert_reason(self.shell(text), "unsupported_helper")

    def test_helper_cannot_shadow_printf_source(self):
        text = 'printf() { command -v "$1"; }\n' + self.fixture("xargs-rc-go.sh")
        self.assert_reason(self.shell(text), "unsupported_helper")

    def test_printf_helper_cannot_hide_dynamic_assignment(self):
        text = 'note() { printf "$@"; }\n' + SOURCE + GUARDED + 'note -v PATH /not-here'
        self.assert_reason(self.shell(text), "unsupported_helper")

    def test_printf_dynamic_format_rejected(self):
        self.assert_reason(self.shell(SOURCE + GUARDED + 'printf "$mode" PATH /not-here'), "unsupported_dynamic_form")

    def test_printf_write_format_rejected(self):
        self.assert_reason(self.shell(SOURCE + GUARDED + "printf '%n' PATH"), "unsupported_dynamic_form")

    def test_printf_write_format_helper_rejected(self):
        self.assert_reason(self.shell("note() { printf '%n' PATH; }\n" + SOURCE + GUARDED), "unsupported_helper")

    def test_source_output_redirection_rejected(self):
        self.assert_reason(self.shell(ARRAY_SOURCE.replace("'*.go')", "'*.go' >/dev/null)") + ARRAY_GUARDED),
                           "unsupported_staged_source")

    def test_xargs_source_output_redirection_rejected(self):
        text = self.fixture("xargs-rc-go.sh").replace('"$files" | xargs', '"$files" >/dev/null | xargs')
        self.assert_reason(self.shell(text), "unsupported_staged_source")

    def test_heredoc_cannot_overwrite_process_source(self):
        text = ARRAY_SOURCE.rstrip() + ' <<EOF\nunstaged.go\nEOF\n' + ARRAY_GUARDED
        self.assert_reason(self.shell(text), "unsupported_staged_source")

    def test_process_source_requires_input_redirection(self):
        self.assert_reason(self.shell(ARRAY_SOURCE.replace('< <(', '> <(') + ARRAY_GUARDED), "unsupported_staged_source")

    def test_mapfile_target_cannot_change_environment(self):
        self.assert_reason(self.shell(ARRAY_SOURCE.replace(' files ', ' PATH ') + ARRAY_GUARDED), "unsupported_dynamic_form")

    def test_git_environment_cannot_redirect_staged_provenance(self):
        self.assert_reason(self.shell('GIT_INDEX_FILE=other-index\n' + SOURCE + GUARDED), "unsupported_dynamic_form")

    def test_indexed_assignment_cannot_change_environment(self):
        self.assert_reason(self.shell('PATH[0]=not-here\n' + SOURCE + GUARDED), "unsupported_dynamic_form")

    def test_read_append_cannot_use_indexed_target(self):
        text = self.fixture("while-read-go.sh").replace('files+=("$f")', 'files[0]+=("$f")')
        self.assert_reason(self.shell(text), "unsupported_dynamic_form")

    def test_noncanonical_numeric_condition_rejected(self):
        text = SOURCE + 'RC=00\nif [ "$RC" -eq 0 ]; then gofmt -l $files; fi\n' + GUARDED
        self.assert_reason(self.shell(text), "unsupported_condition")

    def test_read_target_cannot_change_environment(self):
        text = self.fixture("while-read-go.sh").replace("'' f;", "'' PATH;").replace('"$f"', '"$PATH"')
        self.assert_reason(self.shell(text), "unsupported_dynamic_form")

    def test_nul_source_not_scalar(self):
        self.assert_reason(self.shell(SOURCE.replace('--name-only ', '--name-only -z ') + GUARDED), "unsupported_staged_source")

    def test_grep_cannot_filter_nul_stream_as_lines(self):
        text = SOURCE.replace("-- '*.go'", "-z | grep '\\.go$'") + GUARDED
        self.assert_reason(self.shell(text), "unsupported_staged_source")

    def test_nul_source_not_split_array_substitution(self):
        text = 'files=($(git diff --cached --name-only -z -- "*.go"))\n' + ARRAY_GUARDED
        self.assert_reason(self.shell(text), "unsupported_staged_source")

    def test_mapfile_nul_delimiter_must_match(self):
        self.assert_reason(self.shell(ARRAY_SOURCE.replace('--name-only ', '--name-only -z ') + ARRAY_GUARDED),
                           "unsupported_staged_source")

    def test_mapfile_newline_delimiter_must_match(self):
        self.assert_reason(self.shell(ARRAY_SOURCE.replace('mapfile -t', "mapfile -d ''") + ARRAY_GUARDED),
                           "unsupported_staged_source")

    def test_read_nul_delimiter_must_match(self):
        self.assert_reason(self.shell(self.fixture("while-read-go.sh").replace('--name-only -z ', '--name-only ')),
                           "unsupported_staged_source")

    def test_read_newline_delimiter_must_match(self):
        self.assert_reason(self.shell(self.fixture("while-read-go.sh").replace("-r -d ''", '-r')),
                           "unsupported_staged_source")

    def test_shared_parser_entrypoint(self):
        from scorers.bash_ast import parse_bash
        self.assertEqual(parse_bash('echo hello').named_children[0].type, "command")

    def run_mock_hook(self, text):
        """Exercise argv using two staged files and inert local tool doubles."""
        bin_dir = self.workspace / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "git").write_text('''#!/usr/bin/python3
import sys
if sys.argv[1:] == ["rev-parse", "--show-toplevel"]:
    print(".")
else:
    separator = "\\0" if "-z" in sys.argv else "\\n"
    sys.stdout.write(separator.join(["first.go", "second.go"]) + separator)
''')
        (bin_dir / "gofmt").write_text('''#!/usr/bin/python3
import json, os, sys
with open(os.environ["HOOK_ARGV"], "w") as stream:
    json.dump(sys.argv[1:], stream)
''')
        for path in bin_dir.iterdir():
            path.chmod(0o755)
        for tool in ("golangci-lint", "ruff", "rubocop", "shellcheck", "shfmt"):
            stub = bin_dir / tool
            stub.write_text('#!/bin/sh\nexit 0\n')
            stub.chmod(0o755)
        local_calls = self.workspace / "claude-calls"
        sentinel = bin_dir / "claude"
        sentinel.write_text('#!/usr/bin/python3\nimport os\nwith open(os.environ["LOCAL_CLAUDE_CALLS"], "a") as stream:\n    stream.write("called\\n")\nraise SystemExit(97)\n')
        sentinel.chmod(0o755)
        script = self.workspace / "hook.sh"
        script.write_text(text)
        argv = self.workspace / "argv.json"
        env = dict(os.environ, HOOK_ARGV=str(argv), LOCAL_CLAUDE_CALLS=str(local_calls))
        sentinel_dir = str(Path(env["CLAUDE_BIN"]).parent) if env.get("CLAUDE_BIN") else str(bin_dir)
        env["PATH"] = os.pathsep.join([sentinel_dir, str(bin_dir), env["PATH"]])
        result = subprocess.run(["bash", str(script)], cwd=self.workspace, env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(local_calls.exists())
        return json.loads(argv.read_text())

    def test_correct_styles_pass_two_separate_staged_paths(self):
        for name in ("lint-staged-go.sh", "while-read-go.sh", "and-or-capture-go.sh", "xargs-rc-go.sh"):
            with self.subTest(style=name):
                self.assertEqual(self.run_mock_hook(self.fixture(name)), ["-l", "first.go", "second.go"])

    def test_quoted_scalar_really_joins_two_paths(self):
        text = SOURCE + GUARDED.replace(CALL, 'gofmt -l "$files"')
        self.assertEqual(self.run_mock_hook(text), ["-l", "first.go\nsecond.go"])
        self.assert_reason(self.shell(text), "scalar_paths_quoted")


HOUSE_HOOK = (SHELL_FIXTURES / "lint-staged-go.sh").read_text(encoding="utf-8")
STATICCHECK = 'if [ -n "$files" ] && command -v staticcheck >/dev/null; then staticcheck $files || RC=1; fi\n'
GO_VET = 'if command -v go >/dev/null; then go vet ./... || RC=1; fi\n'

# Guarded unconfigured tools earn no credit but must not reject a correct hook.
NEIGHBOR_PASSES = {
    "house_hook_gofmt_only": HOUSE_HOOK,
    "guarded_staticcheck_scalar": SOURCE + 'RC=0\n' + GUARDED + STATICCHECK + 'exit "$RC"\n',
    "guarded_go_vet_packages": SOURCE + 'RC=0\n' + GUARDED + GO_VET + 'exit "$RC"\n',
    "early_exit_neighbor_guard": SOURCE + GUARDED + 'command -v go >/dev/null || exit 0\ngo vet ./...\n',
    "helper_guarded_neighbor_array": HOUSE_HOOK.replace("golangci-lint", "staticcheck").replace(
        'staticcheck run "${GO[@]}"', 'staticcheck -checks=all "${GO[@]}"'),
}

NEIGHBOR_FAILURES = {
    "unguarded_unknown_command": (SOURCE + GUARDED + 'staticcheck $files\n', "unsupported_command"),
    "disconnected_neighbor_guard": (SOURCE + GUARDED + 'command -v staticcheck && echo ok\nstaticcheck $files\n',
                                    "unsupported_command"),
    "other_tool_guard": (SOURCE + GUARDED + 'if command -v go; then staticcheck $files; fi\n', "unsupported_command"),
    "nonliteral_command_name": (SOURCE + GUARDED + 'if command -v staticcheck; then $cmd $files; fi\n',
                                "unsupported_dynamic_form"),
    "path_literal_argument": (SOURCE + GUARDED + 'if command -v rm; then rm -rf /; fi\n', "unsupported_command"),
    "flag_path_value": (SOURCE + GUARDED + 'if command -v rm; then rm --one-file-system=/ $files; fi\n',
                        "unsupported_command"),
    "later_bare_word_argument": (SOURCE + GUARDED + 'if command -v rm; then rm -rf src; fi\n', "unsupported_command"),
    "quoted_string_argument": (SOURCE + GUARDED + "if command -v python3; then python3 -c 'print(1)'; fi\n",
                               "unsupported_command"),
    "command_substitution_argument": (SOURCE + GUARDED + 'if command -v staticcheck; then staticcheck $(cat list); fi\n',
                                      "unsupported_dynamic_form"),
    "quoted_scalar_argument": (SOURCE + GUARDED + 'if command -v staticcheck; then staticcheck "$files"; fi\n',
                               "unsupported_command"),
    "unrelated_variable_argument": (SOURCE + GUARDED + 'if command -v staticcheck; then staticcheck $other; fi\n',
                                    "unsupported_command"),
    "configured_tool_as_subcommand": (SOURCE + GUARDED + 'if command -v nice; then nice gofmt -w; fi\n',
                                      "unsupported_command"),
    "reserved_command_guarded": (SOURCE + GUARDED + 'if command -v git; then git stash; fi\n', "unsupported_command"),
}


class GuardedNeighborCalls(CheckWorkspace):
    """Tables below cover guarded unconfigured calls."""


def neighbor_pass(text):
    def test(self):
        result = self.shell(text)
        self.assertTrue(result[0], result[1])
    return test


def neighbor_failure(text, reason):
    def test(self):
        self.assert_reason(self.shell(text), reason)
    return test


for case, text in NEIGHBOR_PASSES.items():
    setattr(GuardedNeighborCalls, "test_" + case, neighbor_pass(text))
for case, (text, reason) in NEIGHBOR_FAILURES.items():
    setattr(GuardedNeighborCalls, "test_" + case, neighbor_failure(text, reason))


class AnalysisBounds(CheckWorkspace):
    """ADR 0007 amendment: the reference hook with extra guarded Go tools fits."""

    EXTRA = ["go vet ./...", "staticcheck ./...", "revive ./...",
             "gosec ./...", "errcheck ./...", "ineffassign ./..."]
    END = "    note golangci-lint\n  fi\nfi\n"

    def hook(self, extra_tools):
        text = (SHELL_FIXTURES / "lint-staged-go.sh").read_text()
        self.assertEqual(text.count(self.END), 1)
        lines = "".join(
            f"  if have {call.split()[0]}; then {call} || RC=1; else note {call.split()[0]}; fi\n"
            for call in self.EXTRA[:extra_tools])
        return text.replace(self.END, "    note golangci-lint\n  fi\n" + lines + "fi\n")

    def analyze(self, text):
        from scorers.bash_ast import parse_bash
        from scorers import shell_guard
        peak = []
        original = shell_guard.StagedToolGuard.deduplicate

        def deduplicate(guard, states):
            result = original(guard, states)
            peak.append(len(result))
            return result

        guard = shell_guard.StagedToolGuard(parse_bash(text), ["gofmt", "golangci-lint"])
        with mock.patch.object(shell_guard.StagedToolGuard, "deduplicate", deduplicate):
            guard.check()
        return max(peak), guard.steps

    def test_three_four_and_five_go_tools_pass(self):
        for extra in (1, 2, 3):
            with self.subTest(tools=2 + extra):
                result = self.shell(self.hook(extra), tools=["gofmt", "golangci-lint"])
                self.assertTrue(result[0], result[1])

    def test_four_tool_hook_stays_well_inside_the_bounds(self):
        # Steps are the deterministic stand-in for analysis time.
        from scorers import shell_guard
        states, steps = self.analyze(self.hook(2))
        self.assertLessEqual(states, shell_guard.MAX_STATES // 2)
        self.assertLessEqual(steps, shell_guard.MAX_STEPS // 4)

    def test_bounds_are_the_amended_values(self):
        from scorers import shell_guard
        self.assertEqual((shell_guard.MAX_STATES, shell_guard.MAX_STEPS), (2048, 65536))

    def test_the_old_bound_reproduces_the_third_tool_failure(self):
        from scorers import shell_guard
        with mock.patch.object(shell_guard, "MAX_STATES", 256):
            self.assert_reason(self.shell(self.hook(1), tools=["gofmt", "golangci-lint"]),
                               "analysis_limit")

    def test_the_state_bound_still_fails_closed(self):
        self.assert_reason(self.shell(self.hook(6), tools=["gofmt", "golangci-lint"]),
                           "analysis_limit")


class TestedCapture(CheckWorkspace):
    """ADR 0007 amendment: `[ -z "$(TOOL ...)" ]` reads as capture and test."""

    HEAD = ("set -euo pipefail\n"
            "mapfile -t GO < <(git diff --cached --name-only --diff-filter=ACM -- '*.go')\nRC=0\n")
    GUARD = 'if [ "${#GO[@]}" -gt 0 ] && command -v gofmt >/dev/null; then\n'

    def script(self, body, guard=GUARD):
        return self.HEAD + guard + body + 'fi\nexit "$RC"\n'

    def test_z_and_n_forms_pass(self):
        for body in ('  [ -z "$(gofmt -l "${GO[@]}")" ] || RC=1\n',
                     '  if [ -n "$(gofmt -l "${GO[@]}")" ]; then RC=1; fi\n',
                     '  [[ -z "$(gofmt -l "${GO[@]}")" ]] || RC=1\n'):
            with self.subTest(body):
                result = self.shell(self.script(body))
                self.assertTrue(result[0], result[1])

    def test_reference_hook_with_tested_capture(self):
        text = (SHELL_FIXTURES / "lint-staged-go.sh").read_text()
        old = '    bad=$(gofmt -l "${GO[@]}")\n    if [ -n "$bad" ]; then\n      printf \'%s\\n\' "$bad"\n      RC=1\n    fi\n'
        self.assertEqual(text.count(old), 1)
        text = text.replace(old, '    [ -z "$(gofmt -l "${GO[@]}")" ] || RC=1\n')
        result = self.shell(text, tools=["gofmt", "golangci-lint"])
        self.assertTrue(result[0], result[1])

    def test_the_call_inside_still_needs_every_guard(self):
        body = '  [ -z "$(gofmt -l "${GO[@]}")" ] || RC=1\n'
        self.assert_reason(self.shell(self.script(body, 'if [ "${#GO[@]}" -gt 0 ]; then\n')),
                           "availability_guard_missing")
        self.assert_reason(self.shell(self.script(body, "if command -v gofmt >/dev/null; then\n")),
                           "nonempty_guard_missing")
        self.assert_reason(self.shell(self.script('  [ -z "$(gofmt -l .)" ] || RC=1\n')),
                           "unsupported_tool_arguments")

    def test_a_missing_tool_exit_is_still_caught(self):
        text = self.HEAD + ('if [ "${#GO[@]}" -gt 0 ]; then\n  command -v gofmt >/dev/null || exit 1\n'
                            '  [ -z "$(gofmt -l "${GO[@]}")" ] || RC=1\nfi\nexit "$RC"\n')
        self.assert_reason(self.shell(text), "missing_tool_nonzero_exit")

    def test_other_substitutions_still_fail_closed(self):
        for body in ('  gofmt -l "${GO[@]}"\n  [ -z "$(date)" ] || RC=1\n',
                     '  test -z "$(gofmt -l "${GO[@]}")" || RC=1\n',
                     '  [ -z "$(gofmt -l "${GO[@]}" 2>&1)" ] || RC=1\n',
                     '  [ -z "x$(gofmt -l "${GO[@]}")" ] || RC=1\n',
                     '  [ "$(gofmt -l "${GO[@]}")" = "" ] || RC=1\n'):
            with self.subTest(body):
                result = self.shell(self.script(body))
                self.assertFalse(result[0], result[1])


class ParsedCheckIntegration(CheckWorkspace):
    def test_registry_and_constraints_route_config(self):
        self.target.write_text(CONFIG)
        fixture = {"objective_checks": [{"id": "width", "type": "parsed_config_values",
                                         "paths": ["input"], "format": "yaml", "expected": EXPECT}]}
        self.assertTrue(objective.run_checks(fixture, str(self.workspace), str(self.workspace))[0]["passed"])

    def test_registry_and_constraints_route_shell(self):
        self.target.write_text(SOURCE + GUARDED)
        fixture = {"objective_checks": [{"id": "hook", "type": "shell_staged_tool_guard",
                                         "paths": ["input"], "tools": ["gofmt"]}]}
        self.assertTrue(objective.run_checks(fixture, str(self.workspace), str(self.workspace))[0]["passed"])

    def test_config_rejects_unknown_constraint(self):
        fixture = {"objective_checks": [{"id": "bad", "type": "parsed_config_values", "formatt": "yaml"}]}
        with self.assertRaisesRegex(ValueError, 'constraint key'):
            objective.run_checks(fixture, str(self.workspace), str(self.workspace))

    def test_shell_rejects_unknown_constraint(self):
        fixture = {"objective_checks": [{"id": "bad", "type": "shell_staged_tool_guard", "tool": ["gofmt"]}]}
        with self.assertRaisesRegex(ValueError, 'constraint key'):
            objective.run_checks(fixture, str(self.workspace), str(self.workspace))

    def test_every_objective_harness_install_has_exact_parser_pins(self):
        from tree_sitter import Language, Parser
        import tree_sitter_bash
        parser = Parser(Language(tree_sitter_bash.language()))
        root = Path(__file__).resolve().parents[2]
        installs = []
        for workflow in ("ci.yml", "eval.yml"):
            document = yaml.safe_load((root / ".github" / "workflows" / workflow).read_text())
            for job in document["jobs"].values():
                for step in job.get("steps", []):
                    run = step.get("run", "").encode()
                    pending = [parser.parse(run).root_node]
                    while pending:
                        node = pending.pop()
                        pending.extend(node.named_children)
                        if node.type != "command":
                            continue
                        name = node.child_by_field_name("name").text.decode()
                        args = [arg.text.decode() for arg in node.children_by_field_name("argument")]
                        # Every install pins PyYAML exact, in ci.yml and eval.yml alike.
                        if (name == "pip" and len(args) > 2
                                and args[0] == "install"
                                and args[1].split("==")[0] == "pyyaml"
                                and args[2] == "markdown-it-py==4.2.0"):
                            installs.append(args)
                            self.assertEqual(args[1], "pyyaml==6.0.3")
                            self.assertIn("tree-sitter==0.26.0", args)
                            self.assertIn("tree-sitter-bash==0.25.1", args)
        self.assertEqual(len(installs), 3)
        for args in installs:
            self.assertEqual({a for a in args if a.startswith("tree-sitter")},
                             set(bash_ast.PARSER_REQUIREMENTS))
