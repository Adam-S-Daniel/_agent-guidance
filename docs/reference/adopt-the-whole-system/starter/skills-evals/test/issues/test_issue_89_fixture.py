"""Hermetic proofs for the bounded CI watcher fixture and its shell AST guard.

Source-only seed copies inherit no git state or remote reach. Mutations touch
only allocated temporary workspaces; every subprocess sees a sentinel CLI.
The outer runner must use a PID namespace, including targeted pytest runs.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
import run_eval
from scorers import objective, shell_capture
from scorers.bash_ast import BashParseError, parse_bash

EVAL = ROOT / 'evals/ci-watcher-loops'
SEED = EVAL / 'seed'
ID = '73019'
RUN_URL = f'https://example.com/example-org/example-site/actions/runs/{ID}'
REPLY = 'Run 73019 completed with conclusion success.'
TRAP = 'RUN=$(gh workflow run deploy-preview &&\n sleep 5 &&\n gh run list --json databaseId\n)\n'
# The script uses separate captures and explicitly parses both status and
# conclusion. No sleep or clock advances the replay. It reports the third
# response, rather than a successful command's exit status.
GOOD_SCRIPT = '''#!/bin/bash
set -eu
dispatch=$(gh workflow run deploy-preview --repo example-org/example-site)
run_id=$(sed -n 's#^https://.*/actions/runs/\\([0-9][0-9]*\\)$#\\1#p' <<<"$dispatch")
for attempt in 1 2 3 4; do
  result=$(gh run view "$run_id" --json status,conclusion)
  status=$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  if [ "$status" = completed ]; then
    printf '%s' "$result" | python3 -c 'import json,sys; print("conclusion=" + json.load(sys.stdin)["conclusion"])'
    exit 0
  fi
done
exit 1
'''
BEHAVIOR = {'dispatch-once', 'captured-run-polled-to-completion', 'poll-bound', 'final-conclusion-token'}
RESTRAINT = {'scripts-split-capture', 'reply-split-capture', 'repository-files-unchanged', 'instrument-unchanged'}



class TestCiWatcherShellCapture(unittest.TestCase):
    def _reply(self, text):
        return shell_capture.shell_capture_safe('', [], source='transcript', transcript=text)[0]

    def test_ast_rejects_multiline_chained_capture_with_sleep(self):
        self.assertFalse(self._reply('```bash\n' + TRAP + '```'))

    def test_inline_and_prose_captures_are_inspected(self):
        self.assertFalse(self._reply('success. `RUN=$(gh workflow run x && gh run list --limit 1)`'))
        self.assertFalse(self._reply('success. Used $(gh workflow run x && gh run list --limit 1).'))

    def test_split_capture_comments_and_quoted_data_pass(self):
        for script in [GOOD_SCRIPT, '# ' + TRAP.replace('\n', '\n# '),
                       "echo '$(gh workflow run x && gh run list --limit 1)'\n",
                       'x=$(echo one && echo two\n)\n',
                       'gh workflow run x && gh run list --limit 1\n',
                       'x=$(gh workflow run x); y=$(gh run list --limit 1)\n',
                       'x=$(gh run list; gh workflow run x)\n']:
            with self.subTest(script=script):
                self.assertTrue(self._reply('```bash\n' + script + '```'))

    def test_one_operand_compound_does_not_create_cross_operand_capture(self):
        for script in [
            'x=$(if true; then gh workflow run x; else gh run list; fi && echo okay\n)\n',
        ]:
            with self.subTest(script=script):
                self.assertTrue(self._reply('```bash\n' + script + '```'))

    def test_compounds_in_distinct_and_operands_are_rejected(self):
        self.assertFalse(self._reply(
            '```bash\nx=$({ gh workflow run x; } && { gh run list; }\n)\n```'))

    def test_guard_is_load_bearing_source_mutation(self):
        root = parse_bash('gh workflow run x && gh run list\n')
        self.assertTrue(shell_capture._unsafe_capture(root))
        with mock.patch.object(shell_capture, '_unsafe_capture', return_value=False):
            with self.assertRaises(AssertionError):
                self.assertFalse(self._reply('```bash\n' + TRAP + '```'))
        self.assertFalse(self._reply('```bash\n' + TRAP + '```'))

    def test_unparseable_candidate_only_fails_closed_with_dispatch_token(self):
        for script in ['x=$(', 'x=$(echo one && echo', 'x=$([[ -n )', 'x=$(echo one; function {)']:
            with self.subTest(script=script):
                self.assertTrue(self._reply('```bash\n' + script + '\n```'))
        for script in ['x=$(gh workflow run x &&', 'x=$(gh workflow run x; [[ -n )',
                       'x=$(function {; gh workflow run x)']:
            with self.subTest(script=script):
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))

    def test_case_keywords_in_arguments_do_not_swallow_later_captures(self):
        for harmless in ['echo case in value', 'echo case subject in value', 'printf "%s" case in value',
                         'echo "case" in value']:
            with self.subTest(harmless=harmless):
                script = 'x=$(' + harmless + '); dispatch=$(gh workflow run x)'
                self.assertTrue(self._reply('```bash\n' + script + '\n```'))
        for script in ['x=$(case "$x" in x) gh workflow run x; gh run list;; esac)',
                       'x=$(if true; then case x in x) gh workflow run x; gh run list;; esac; fi)']:
            with self.subTest(script=script):
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))

    def test_variable_case_subjects_keep_dispatch_inside_candidate(self):
        for subject in ['"$kind"', '$kind', '${kind}', '$(echo x)']:
            with self.subTest(subject=subject):
                script = 'x=$(case ' + subject + ' in x) gh workflow run x; gh run list;; esac)'
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))

    def test_ansi_c_quoted_display_with_escaped_apostrophe_is_literal(self):
        script = "printf '%s' $'literal\\'$(gh workflow run x; gh run list)'\n"
        self.assertTrue(self._reply('```bash\n' + script + '```'))

    def test_quoted_or_escaped_dispatch_tokens_are_recognized_in_parsed_body(self):
        for command in ['"gh" workflow run x', "gh 'workflow' run x", 'g\\h workflow run x',
                        '"gh" "workflow" "run" x']:
            with self.subTest(command=command):
                script = 'x=$([[ true ]]; ' + command + '; gh run list)'
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))

    def test_single_line_candidate_is_parsed_without_outer_parentheses(self):
        self.assertTrue(self._reply('`x=$(echo one && echo two)`'))
        self.assertTrue(self._reply('```bash\nx=$(echo one && echo two)\n```'))
        self.assertFalse(self._reply('`x=$(gh workflow run x && gh run list)`'))

    def test_all_sequential_separators_are_guarded(self):
        for separator in [' && ', '; ', '\n']:
            for script in ['x=$(gh workflow run x' + separator + 'gh run list)',
                           'x=`gh workflow run x' + separator + 'gh run list`',
                           'x=$(if true; then gh workflow run x' + separator + 'gh run list; fi)']:
                with self.subTest(script=script):
                    self.assertFalse(self._reply('```bash\n' + script + '\n```'))

    def test_lexical_quotes_escapes_comments_and_nested_substitutions(self):
        self.assertEqual(shell_capture._substitutions('n=$((n+1))'), [])
        bad = '$(gh workflow run x; gh run list)'
        for script in ['echo "' + bad + '"', 'x=$(echo ' + bad + ')',
                       'n=$((1 + ' + bad + '))', 'x=`echo ' + bad + '`']:
            with self.subTest(script=script):
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))
        for script in ["echo '" + bad + "'", '# ' + bad, 'echo \\' + bad,
                       'echo \\`gh workflow run x; gh run list\\`',
                       'n=$((1+1))', 'echo example#' + bad.replace('gh workflow run', 'echo')]:
            with self.subTest(script=script):
                self.assertTrue(self._reply('```bash\n' + script + '\n```'))
        self.assertFalse(self._reply('Used $(gh workflow run x; # ignore ) here\ngh run list).'))
        self.assertFalse(self._reply("I've used " + bad + '.'))
        self.assertFalse(self._reply('```bash\nx=$(gh workflow run x; echo $(gh run list))\n```'))
        self.assertFalse(self._reply('```bash\nx=`gh workflow run x; echo \\`gh run list\\``\n```'))

    def test_heredoc_expansion_and_quoted_literal_bodies(self):
        trap = '$(gh workflow run x; gh run list)'
        for opener, ending, prefix, active in [('EOF', 'EOF', '', True),
                                              ("'EOF'", 'EOF', '', False),
                                              ('"EOF"', 'EOF', '', False),
                                              ('\\EOF', 'EOF', '', False),
                                              ('-EOF', 'EOF', '\t', True),
                                              ("-'EOF'", 'EOF', '\t', False)]:
            with self.subTest(opener=opener):
                script = 'cat <<' + opener + '\n' + prefix + trap + '\n' + prefix + ending + '\n'
                self.assertEqual(self._reply('```bash\n' + script + '```'), not active)
        self.assertFalse(self._reply('```bash\ncat <<A <<B\nliteral\nA\n' + trap + '\nB\n```'))

    def test_reply_status_prose_is_not_shell_and_shell_labels_are_guarded(self):
        status = 'run 73019 (deploy-preview): completed (success)'
        for reply in ['```\n' + status + '\n```', '`completed (success)`',
                      '```bash\n' + status + '\n```', '```\nx=$(echo one &&\n```']:
            with self.subTest(reply=reply):
                self.assertTrue(self._reply(reply))
        for label in ['bash', 'sh', 'shell', 'console', 'zsh', '']:
            with self.subTest(label=label):
                self.assertFalse(self._reply('```' + label + '\n' + TRAP + '```'))
        self.assertTrue(self._reply('```python\n' + TRAP + '```'))

    @staticmethod
    def _mutate_expression(function_name, expression, replacement):
        """Compile one source AST mutation in memory, with no inherited git reach."""
        tree = ast.parse(Path(shell_capture.__file__).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == function_name)
        target = ast.dump(ast.parse(expression, mode='eval').body)
        substitute = ast.parse(replacement, mode='eval').body
        class Replace(ast.NodeTransformer):
            changed = 0
            def visit(self, node):
                if ast.dump(node) == target:
                    self.changed += 1
                    return ast.copy_location(copy.deepcopy(substitute), node)
                return super().visit(node)
        mutator = Replace()
        function = mutator.visit(function)
        if mutator.changed != 1:
            raise AssertionError(f'expected one AST source mutation, got {mutator.changed}')
        namespace = dict(shell_capture.__dict__)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     '<shell-capture-mutant>', 'exec'), namespace)
        return namespace[function_name]

    def test_review_source_mutations_are_detected(self):
        # Whole-script parsing reintroduces finding 1 (an unrelated open `if`
        # would invalidate the script). Missing candidate filtering
        # reintroduces finding 4; dropping the AST list check loses all separators.
        mutations = [
            ('shell_capture_safe', '_substitutions(text, prose=prose)', '[(text, True)]',
             lambda: self.assertTrue(self._reply('```bash\nif true; then\n' + GOOD_SCRIPT + '```'))),
            ('shell_capture_safe', '_mentions_dispatch(body, unverifiable=True)', 'True',
             lambda: self.assertTrue(self._reply('```bash\nx=$(echo one &&\n```'))),
            ('_transcript_shell', "label in {'bash', 'sh', 'shell', 'console', 'zsh'}", 'True',
             lambda: self.assertTrue(self._reply('```python\n' + TRAP + '```'))),
            ('_flow', "words[:3] == ['gh', 'run', 'list'] and True in states", 'False',
             lambda: self.assertFalse(self._reply('```bash\nx=$(gh workflow run x; gh run list)\n```'))),
            ('_substitutions', "text.startswith('$((', cursor)", 'False',
             lambda: self.assertEqual(shell_capture._substitutions('n=$((n+1))'), [])),
        ]
        for function, expression, replacement, regression in mutations:
            with self.subTest(function=function, expression=expression):
                mutant = self._mutate_expression(function, expression, replacement)
                with mock.patch.object(shell_capture, function, mutant):
                    with self.assertRaises(AssertionError):
                        regression()

    def test_constructs_the_parser_supports_are_decided_by_the_ast(self):
        # Each body parses, so none of these reaches the fail-closed token rule.
        safe = ['x=$([[ -n "$k" ]] && gh workflow run x)',
                'x=$(n=$((n+1)); gh run list)',
                'x=$(for ((n=0;n<2;n++)); do gh run list; done)',
                'x=$(declare -a a=(one two); gh workflow run x)',
                'x=$(case k in a) gh workflow run x;; b) gh run list;; esac)',
                'x=$(if a; then gh workflow run x; elif b; then gh run list; fi)',
                'x=$(gh run list; case k in a) gh workflow run x;; esac)',
                'x=$(case x in x) echo x;; esac)']
        unsafe = ['x=$([[ -n "$k" ]] && gh workflow run x && gh run list)',
                  'x=$(n=$((n+1)); gh workflow run x; gh run list)',
                  'x=$(for ((n=0;n<2;n++)); do gh workflow run x; gh run list; done)',
                  'x=$(declare -a a=(one two); gh workflow run x; gh run list)',
                  'x=$(case k in a) gh workflow run x;; esac; gh run list)',
                  'x=$(case $(gh workflow run x) in a) gh run list;; esac)',
                  'x=$(gh workflow run x; case x in x) gh run list;; esac)',
                  'x=$(case x in x) gh workflow run x; gh run list;; esac)',
                  'x=$(case x in x) :;; y) gh workflow run x; gh run list;; esac)',
                  'x=$(if a; then gh workflow run x; elif b; then :; fi; gh run list)',
                  'x=$(if gh workflow run x; then :; elif gh run list; then :; fi)']
        for script in safe + unsafe:
            with self.subTest(script=script):
                body, complete = shell_capture._substitutions(script)[0]
                self.assertTrue(complete)
                parse_bash(body + '\n')
                self.assertEqual(self._reply('```bash\n' + script + '\n```'), script in safe)

    def test_case_terminators_fall_through_or_end_the_arm(self):
        # (script, safe): `;;` ends the case; `;&` runs the next body; `;;&`
        # tests the next patterns. Each terminator also ends the arm lexically.
        table = [
            ('id=$(case x in x) gh workflow run d ;; x) gh run list ;; esac)', True),
            ('id=$(case x in x) gh workflow run d ;& y) gh run list ;; esac)', False),
            ('id=$(case x in x) gh workflow run d ;;& x) gh run list ;; esac)', False),
            ('id=$(case x in x) gh workflow run d ;& y) : ;; z) gh run list ;; esac)', True),
            ('id=$(case x in x) gh workflow run d ;& y) : ;& z) gh run list ;; esac)', False),
            ('id=$(case x in a) : ;& x) gh workflow run d; gh run list ;; esac)', False),
            ('id=$(case x in a) : ;;& x) gh workflow run d; gh run list ;; esac)', False),
            ('id=$(case x in a) : ;& x) gh workflow run d ;; esac); y=$(gh run list)', True),
        ]
        for script, safe in table:
            with self.subTest(script=script):
                body, complete = shell_capture._substitutions(script)[0]
                self.assertTrue(complete)
                self.assertTrue(body.endswith('esac'))
                self.assertEqual(self._reply('```bash\n' + script + '\n```'), safe)

    def test_loops_functions_and_ansi_c_words_are_followed(self):
        table = [
            ('x=$(for i in 1 2; do gh run list; gh workflow run x; done)', False),
            ('x=$(while true; do gh run list; gh workflow run x; done)', False),
            ('x=$(until false; do gh run list; gh workflow run x; done)', False),
            ('x=$(for ((i=0;i<2;i++)); do gh run list; gh workflow run x; done)', False),
            ('x=$(for i in 1 2; do gh run list; done; gh workflow run x)', True),
            ('x=$(f() { gh run list; }; gh workflow run x; f)', False),
            ('x=$(function f { gh workflow run x; }; f; gh run list)', False),
            ('x=$(f() { gh run list; }; f; gh workflow run x)', True),
            ('x=$(f() { gh workflow run x; gh run list; })', True),
            ('x=$(f() { f; }; f)', True),
            ("x=$($'gh' workflow run x; gh run list)", False),
            ("x=$($'g\\x68' workflow run x; gh run list)", False),
            ("x=$($'g\\150' workflow run x; gh run list)", False),
            ("x=$(echo $'it\\'s'; gh run list)", True),
        ]
        for script, safe in table:
            with self.subTest(script=script):
                self.assertEqual(self._reply('```bash\n' + script + '\n```'), safe)

    def test_redefined_function_and_dynamic_name_keep_their_bindings(self):
        table = [
            ('x=$(f(){ f(){ gh run list; }; }; f; gh workflow run d; f)', False),
            ('x=$(f(){ f(){ :; }; }; f; gh workflow run d; f; gh run list)', False),
            ('x=$(f(){ f(){ :; }; }; gh workflow run d; f; f)', True),
            ('x=$($cmd; f(){ gh run list; }; gh workflow run d; f)', False),
        ]
        for script, safe in table:
            with self.subTest(script=script):
                self.assertEqual(self._reply('```bash\n' + script + '\n```'), safe)

    def test_flow_step_bound_fails_closed_only_for_dispatch_bodies(self):
        nested = 'for i in 1; do ' * 16 + ':' + '; done' * 16
        shallow = 'for i in 1; do ' * 8 + ':' + '; done' * 8
        with self.assertRaises(shell_capture._AnalysisLimit):
            shell_capture._unsafe_capture(parse_bash(nested + '\n'))
        self.assertFalse(shell_capture._unsafe_capture(parse_bash(shallow + '\n')))
        self.assertTrue(self._reply('```bash\nx=$(' + nested + ')\n```'))
        self.assertTrue(self._reply('```bash\nx=$(' + shallow + '; gh workflow run x)\n```'))
        ok, detail = shell_capture.shell_capture_safe(
            '', [], source='transcript',
            transcript='```bash\nx=$(' + nested + '; gh workflow run x)\n```')
        self.assertEqual((ok, detail), (False, 'reply shell example: shell analysis exceeded its bound'))

    def test_unverifiable_bodies_decode_quoted_dispatch_names(self):
        # Bodies over the step bound or that do not parse fall back to the
        # lexical scan, which must see the same quoted spellings of `gh`.
        nested = 'for i in 1; do ' * 11 + ':' + '; done' * 11 + '; '
        unparseable = '; if'
        with self.assertRaises(shell_capture._AnalysisLimit):
            shell_capture._unsafe_capture(parse_bash(nested + ':\n'))
        with self.assertRaises(BashParseError):
            parse_bash('echo one' + unparseable + '\n')
        spellings = ["$'gh'", "$'\\x67h'", "$'\\147h'", '$"gh"', 'g""h', "'g'h", '\\gh',
                     "$'\\e'"]
        for spelling in spellings:
            for prefix, suffix in [(nested, ''), ('', unparseable)]:
                script = 'x=$(' + prefix + spelling + ' workflow run d; gh run list' + suffix + ')'
                with self.subTest(script=script):
                    self.assertFalse(self._reply('```bash\n' + script + '\n```'))
        for script in ['x=$(' + nested + 'echo gh workflow; gh run list)',
                       'x=$(echo one' + unparseable + ')',
                       'x=$(echo "g\\x68 flow"' + unparseable + ')']:
            with self.subTest(script=script):
                self.assertTrue(self._reply('```bash\n' + script + '\n```'))
        # A literal "$'" inside double quotes opens a bogus $'...' span in the
        # lexical scan; decoding it must never hide a plain dispatch inside.
        for body, reply in [
                ('printf "$\'\\n"\nid=$(gh workflow run d && gh run list)\necho \'done\'',
                 '```\n{}\n```'),
                ('printf "$\'\\e"; id=$(gh workflow run d && gh run list); echo \'ok\'', '`{}`'),
                ('x=$(echo "$\'\\n"; gh workflow run d; gh run list; echo \' x\'; if)',
                 '```bash\n{}\n```')]:
            with self.subTest(body=body):
                self.assertTrue(shell_capture._mentions_dispatch(body))
                self.assertFalse(self._reply(reply.format(body)))
        # A bogus span can pair with the quote that opens a disguised word, so
        # an unverifiable body fails closed on any $' or on `workflow run`.
        bogus = 'echo "$\'\\n"; '
        for script in ['x=$(' + bogus + '"gh" workflow run d; gh run list; echo \'x\'; if)',
                       'x=$(' + bogus + '$\'\\x67h\' workflow run d; gh run list; if)',
                       'x=$(' + bogus + '$\'\\147h\' workflow run d; gh run list; if)',
                       'x=$(' + nested + bogus + '$\'\\x67h\' workflow run d; gh run list)',
                       'x=$(' + nested + bogus + '$\'\\147h\' workflow run d; gh run list)',
                       'x=$(' + bogus + 'gh $\'\\x77orkflow\' run d; gh run list; if)',
                       'x=$(' + bogus + 'gh workflow $\'\\x72un\' d; gh run list; if)',
                       'x=$(' + nested + bogus + 'gh $\'\\x77orkflow\' run d; gh run list)',
                       'x=$(' + nested + bogus + 'gh workflow $\'\\x72un\' d; gh run list)',
                       # No $': a name the spelling scan cannot read still
                       # fails closed on `workflow run` in the unquoted text.
                       'x=$("$GH" workflow run d; gh run list' + unparseable + ')',
                       'x=$(' + nested + '"$GH" work""flow run d; gh run list)',
                       # Any $' fails an unverifiable body closed, even a harmless one.
                       "x=$(echo $'g\\x68 flow'" + unparseable + ')']:
            with self.subTest(script=script):
                self.assertFalse(self._reply('```bash\n' + script + '\n```'))
        # The spelling-aware scan alone still sees a quoted name in a bogus span.
        self.assertTrue(shell_capture._mentions_dispatch(
            bogus + '"gh" workflow run d; gh run list; echo \'x\''))
        # A verifiable body that only mentions "workflow run" is decided by
        # the AST, not the fallback, so it stays clean.
        listed = 'echo "$\'\\n workflow run"; gh run list'
        self.assertFalse(shell_capture._unsafe_capture(parse_bash(listed + '\n')))
        self.assertTrue(self._reply('```bash\nx=$(' + listed + ')\n```'))
        # A parsed $"gh" is the literal word gh.
        self.assertFalse(self._reply('```bash\nx=$($"gh" workflow run d; gh run list)\n```'))
        self.assertTrue(self._reply('```bash\nx=$($"gh" workflow run d)\n```'))

    def test_unavailable_parser_raises_instead_of_passing(self):
        from scorers import bash_ast
        with mock.patch.dict(sys.modules, {'tree_sitter': None}):
            # A scorer error (never a pass, never a failed check), whether or
            # not the reply has anything to parse.
            for reply in ('```bash\nx=$(echo one)\n```', 'no shell here'):
                with self.assertRaisesRegex(bash_ast.ScorerUnavailableError, 'tree-sitter-bash==0.25.1'):
                    self._reply(reply)

    def test_invalid_source_is_rejected(self):
        with self.assertRaises(ValueError):
            shell_capture.shell_capture_safe('', [], source='other')


if __name__ == '__main__':
    unittest.main()
