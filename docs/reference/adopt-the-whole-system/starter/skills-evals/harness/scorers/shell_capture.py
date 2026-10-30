"""Reject dispatch/discovery captures using lexical extraction and a shell AST."""

from __future__ import annotations

import glob
from pathlib import Path
import re
import shlex


_ANSI_C_ESCAPE = r"\\(x[0-9A-Fa-f]{1,2}|[0-7]{1,3}|[\\'\"])"


def _ansi_c(body):
    """Decode the $'...' escapes that can spell a command word, else None."""
    if '\\' in re.sub(_ANSI_C_ESCAPE, '', body):
        return None

    def decode(found):
        token = found.group(1)
        if token[0] == 'x':
            return chr(int(token[1:], 16))
        return chr(int(token, 8)) if token[0] in '01234567' else token
    return re.sub(_ANSI_C_ESCAPE, decode, body)


def _literal(node):
    """Return a word's quote-removed literal value, or None if it expands."""
    text = node.text.decode('utf-8')
    if node.type in {'word', 'number'}:
        return re.sub(r'\\(.)', r'\1', text, flags=re.S)
    if node.type == 'raw_string':
        return text[1:-1]
    if node.type == 'ansi_c_string':
        # Any escape _ansi_c cannot decode makes the word non-literal.
        return _ansi_c(text[2:-1])
    if node.type == 'translated_string':
        # $"..." is a locale-translated "..."; without a catalog it is unchanged.
        return _literal(node.named_children[0]) if node.named_children else None
    if node.type == 'string':
        if any(child.type != 'string_content' for child in node.named_children):
            return None
        return re.sub(r'\\([$`"\\\n])', lambda found: '' if found.group(1) == '\n'
                      else found.group(1), text[1:-1])
    if node.type in {'command_name', 'concatenation'}:
        parts = [_literal(child) for child in node.named_children]
        return None if not parts or None in parts else ''.join(parts)
    return None


def _command_words(node):
    name = node.child_by_field_name('name')
    return [_literal(word) for word in [name, *node.children_by_field_name('argument')]
            if word is not None]


# Loops and calls are re-walked, so nesting multiplies work; the bound keeps
# analysis linear in practice and fails closed like an unparseable body.
_MAX_FLOW_STEPS = 32768


class _AnalysisLimit(Exception):
    """The candidate needs more flow steps than the bound allows."""


# Key of the step counter in the `functions` map; no command word equals it.
_STEPS = object()


def _sequence(nodes, states, functions):
    for node in nodes:
        states, unsafe = _flow(node, states, functions)
        if unsafe:
            return states, True
    return states, False


def _flow(node, states, functions):
    """Track dispatch along Tree-sitter paths without joining exclusive branches.

    `functions` maps names defined so far to their bodies; a body is analyzed
    where the function is called, not where it is defined. Its _STEPS entry
    counts flow steps against _MAX_FLOW_STEPS.
    """
    functions[_STEPS] = functions.get(_STEPS, 0) + 1
    if functions[_STEPS] > _MAX_FLOW_STEPS:
        raise _AnalysisLimit
    if node.type == 'function_definition':
        name = node.child_by_field_name('name')
        functions[name.text.decode('utf-8')] = node.child_by_field_name('body')
        return states, False
    if node.type == 'command':
        states, unsafe = _sequence(node.children, states, functions)
        if unsafe:
            return states, True
        words = _command_words(node)
        if words[:3] == ['gh', 'run', 'list'] and True in states:
            return states, True
        if words[:3] == ['gh', 'workflow', 'run']:
            return {True}, False
        if words and words[0] in functions:
            # Remove the definition while its body runs, so recursion ends;
            # restore it afterwards unless the body redefined the function.
            body = functions.pop(words[0])
            try:
                return _flow(body, states, functions)
            finally:
                functions.setdefault(words[0], body)
        return states, False
    if node.type in {'for_statement', 'c_style_for_statement', 'while_statement'}:
        # A second pass carries one iteration's states into the next; with
        # two possible states that reaches a fixed point. Zero iterations
        # keep the incoming states.
        once, unsafe = _sequence(node.children, states, functions)
        if unsafe:
            return states, True
        twice, unsafe = _sequence(node.children, states | once, functions)
        return states | once | twice, unsafe
    if node.type == 'if_statement':
        # Conditions run in order until one selects its body; each body is an
        # exclusive branch entered from the states its condition left behind.
        segments = []
        for child in node.children:
            for part in (child.children if child.type in {'elif_clause', 'else_clause'} else [child]):
                if part.type in {'if', 'elif', 'then', 'else', 'fi'}:
                    segments.append((part.type, []))
                elif segments:
                    segments[-1][1].append(part)
        outputs, remaining, has_else = set(), states, False
        for keyword, parts in segments:
            if keyword in {'if', 'elif'}:
                remaining, unsafe = _sequence(parts, remaining, functions)
            elif keyword in {'then', 'else'}:
                branch, unsafe = _sequence(parts, remaining, functions)
                outputs |= branch
                has_else |= keyword == 'else'
            else:
                continue
            if unsafe:
                return states, True
        return outputs | (set() if has_else else remaining), False
    if node.type == 'case_statement':
        subject = node.child_by_field_name('value')
        states, unsafe = _sequence([] if subject is None else [subject], states, functions)
        if unsafe:
            return states, True
        # Each arm is entered from the incoming states. An arm ending in `;&`
        # falls through to the next arm's body and `;;&` goes on testing the
        # next patterns, so either carries its output into the next arm.
        # No arm may match, so the incoming states also continue past esac.
        outputs, carried = set(states), set()
        for item in node.children:
            if item.type != 'case_item':
                continue
            branch, unsafe = _sequence(item.children, states | carried, functions)
            if unsafe:
                return states, True
            outputs |= branch
            carried = branch if item.children[-1].type in {';&', ';;&'} else set()
        return outputs, False
    return _sequence(node.children, states, functions)


def _unsafe_capture(root):
    return _flow(root, {False}, {})[1]


def _mentions_dispatch(text, *, unverifiable=False):
    r"""Lexical over-approximation of a `gh workflow run` token sequence.

    Used only to select reply snippets and to decide whether a body that
    cannot be verified fails closed; parsed command shape is decided by _flow.
    $'...' spans decode as in _literal (an undecodable one may spell `gh`),
    $"..." reads as "...", and quotes and backslashes are then removed, so
    $'\x67h', $"gh", g""h, 'g'h and \gh all count as `gh`.
    The $'...' scan ignores shell quoting, so a literal "$'" inside double
    quotes can open a bogus span over a real dispatch; decoding therefore
    only adds matches: the raw and quote-stripped texts are searched too.

    For an unverifiable body (unparseable or over the analysis bound) the
    fallback exists to be conservative, and lexical decoding cannot follow
    shell quoting (a literal "$'" inside double quotes pairs with the quote
    opening a later $'\x67h' or $'\x77orkflow'). $'...' is the only quoting
    that decodes escapes, so such a body fails closed if it contains $' at
    all; every other quote and backslash is removed by unquote, so otherwise
    it fails closed when the unquoted text says `workflow run` after any
    command spelling. A legitimate unverifiable body mentioning either is
    rare. The spelling-aware scan stays as an additional match.
    """
    def unquote(value):
        return re.sub(r'''['"\\]''', '', value.replace('$"', '"'))
    pattern = re.compile(r'(?<![A-Za-z0-9_])gh\s+workflow\s+run(?![A-Za-z0-9_])')
    if unverifiable:
        if "$'" in text or re.search(r'workflow\s+run', unquote(text)):
            return True
        return any(pattern.search(form) for form in (text, unquote(text)))

    def ansi(found):
        decoded = _ansi_c(found.group(1))
        return 'gh' if decoded is None else decoded
    decoded = re.sub(r"\$'((?:[^'\\]|\\.)*)'", ansi, text, flags=re.S)
    return any(pattern.search(form) for form in (text, unquote(text), unquote(decoded)))


def _substitutions(text, *, prose=False):
    """Extract active substitution bodies, including nested $() and backticks.

    Quotes, escaped characters, comments and heredocs delimit lexical candidates.
    Arithmetic is traversed for nested substitutions but never parsed itself.
    Prose has no surrounding shell quote state; candidate bodies still do.
    """
    candidates = []

    def scan(cursor, stop=None, depth=1, shell=True, limit=None):
        quote = None
        ansi_quote = False
        heredocs = []
        cases = []
        command_start = True
        limit = len(text) if limit is None else limit
        while cursor < limit:
            char = text[cursor]
            if shell and quote == "'":
                if ansi_quote and char == '\\':
                    cursor += 2
                    continue
                if char == quote:
                    quote = None
                    ansi_quote = False
                    if cases and cases[-1] == 'subject':
                        cases[-1] = 'await-in'
                cursor += 1
                continue
            if shell and quote is None and text.startswith("$'", cursor):
                quote, ansi_quote = "'", True
                command_start = False
                cursor += 2
                continue
            if char == '\\':
                command_start = False
                cursor += 2
                continue
            if shell and quote is None and text.startswith('<<', cursor) and not text.startswith('<<<', cursor):
                start = cursor + 2
                strip_tabs = start < limit and text[start] == '-'
                start += int(strip_tabs)
                while start < limit and text[start] in ' \t':
                    start += 1
                end, delimiter_quote = start, None
                while end < limit:
                    char = text[end]
                    if char == '\\' and delimiter_quote != "'":
                        end += 2
                        continue
                    if delimiter_quote:
                        if char == delimiter_quote:
                            delimiter_quote = None
                    elif char in "'\"":
                        delimiter_quote = char
                    elif char in ' \t\r\n;|&()<>':
                        break
                    end += 1
                raw = text[start:end]
                try:
                    words = shlex.split(raw)
                except ValueError:
                    words = []
                if len(words) == 1:
                    quoted = any(char in raw for char in "'\"\\")
                    heredocs.append((words[0], quoted, strip_tabs))
                    cursor = end
                    continue
            if shell and quote is None and char == '\n' and heredocs:
                cursor += 1
                for delimiter, quoted, strip_tabs in heredocs:
                    body_start = cursor
                    while cursor < limit:
                        newline = text.find('\n', cursor, limit)
                        end = limit if newline < 0 else newline
                        line = text[cursor:end]
                        if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                            break
                        cursor = end + (newline >= 0)
                    if not quoted:
                        scan(body_start, shell=False, limit=cursor)
                    if cursor < limit:
                        newline = text.find('\n', cursor, limit)
                        cursor = limit if newline < 0 else newline + 1
                heredocs = []
                command_start = True
                continue
            if stop == '`' and char == '`' and quote is None:
                return cursor + 1, True
            if shell and quote is None:
                # Case pattern ')' is a lexical delimiter of an arm, not of
                # its enclosing substitution: keep the whole case statement
                # inside its candidate so the parser sees every arm.
                terminator = next((token for token in (';;&', ';;', ';&')
                                   if text.startswith(token, cursor)), None)
                if cases and cases[-1] == 'body' and terminator:
                    # `;;`, `;&` and `;;&` all end an arm; the next word is a
                    # pattern or the `esac` that closes the statement.
                    cases[-1] = 'pattern'
                    command_start = True
                    cursor += len(terminator)
                    continue
                if char.isalpha() and (cursor == 0 or text[cursor - 1] in ' \t\r\n;|&()<>'):
                    end = cursor
                    while end < limit and (text[end].isalnum() or text[end] == '_'):
                        end += 1
                    if end == limit or text[end] in ' \t\r\n;|&()<>':
                        word = text[cursor:end]
                        if command_start and word == 'case':
                            cases.append('subject')
                            command_start = False
                        elif cases and cases[-1] == 'subject':
                            cases[-1] = 'await-in'
                            command_start = False
                        elif word == 'in' and cases and cases[-1] == 'await-in':
                            cases[-1] = 'pattern'
                            command_start = False
                        elif command_start and word == 'esac' and cases:
                            cases.pop()
                            command_start = False
                        elif command_start and word in {'then', 'do', 'else', 'elif', 'if', 'while', 'until', '!'}:
                            command_start = True
                        else:
                            command_start = False
                        cursor = end
                        continue
                if cases and cases[-1] == 'pattern' and char == ')':
                    cases[-1] = 'body'
                    command_start = True
                    cursor += 1
                    continue
            if shell and quote is None and cases and cases[-1] == 'subject' and char == '$' and not text.startswith('$(', cursor):
                cases[-1] = 'await-in'
            if text.startswith('$(', cursor):
                arithmetic = text.startswith('$((', cursor)
                start = cursor + (3 if arithmetic else 2)
                end, complete = scan(start, ')', 2 if arithmetic else 1, limit=limit)
                if not arithmetic:
                    candidates.append((text[start:end - 1 if complete else end], complete))
                cursor = end
                if shell and quote is None and cases and cases[-1] == 'subject':
                    cases[-1] = 'await-in'
                command_start = False
                continue
            if char == '`':
                start = cursor + 1
                end, complete = scan(start, '`', limit=limit)
                body = text[start:end - 1 if complete else end]
                # Traditional nested backticks escape their delimiters in
                # the outer capture; parse the body after that lexical layer.
                candidates.append((body.replace('\\`', '`'), complete))
                cursor = end
                continue
            if shell and quote:
                if char == quote:
                    quote = None
                    if cases and cases[-1] == 'subject':
                        cases[-1] = 'await-in'
            elif shell and char in "'\"":
                quote = char
                command_start = False
            elif shell and char == '#' and (cursor == 0 or text[cursor - 1] in ' \t\r\n;|&()<>'):
                newline = text.find('\n', cursor, limit)
                cursor = limit if newline < 0 else newline + 1
                command_start = True
                continue
            elif stop == ')' and char == '(':
                depth += 1
            elif stop == ')' and char == ')':
                depth -= 1
                if not depth:
                    return cursor + 1, True
            if shell and quote is None and char in ';\n|&(':
                command_start = True
            cursor += 1
        return limit, stop is None

    scan(0, shell=not prose)
    return candidates


def _transcript_shell(transcript):
    from markdown_it import MarkdownIt
    for token in MarkdownIt().parse(transcript):
        if token.type in {'fence', 'code_block'}:
            label = token.info.split()[0].lower() if token.info else ''
            if label in {'bash', 'sh', 'shell', 'console', 'zsh'} or (not label and _mentions_dispatch(token.content)):
                yield token.content, False
        elif token.type == 'inline':
            for child in token.children or []:
                if child.type == 'code_inline' and _mentions_dispatch(child.content):
                    yield child.content, False
                elif child.type == 'text' and _mentions_dispatch(child.content):
                    yield child.content, True


def shell_capture_safe(workspace: str, patterns: list[str], source='files',
                       transcript=None) -> tuple[bool, str]:
    """Reject dispatch followed by discovery in one command substitution.

    Only substitution bodies reach the Tree-sitter parser. Invalid syntax
    elsewhere in a script or prose does not invalidate its independent
    captures. A body with a dispatch token that cannot be parsed fails closed.
    """
    from .bash_ast import BashParseError, parse_bash, require_parser
    if source not in {'files', 'transcript'}:
        raise ValueError('shell_capture_safe source must be files or transcript')
    # Before any candidate is read: whether the parser is installed here must
    # not depend on what the agent wrote (a body without a substitution never
    # reaches the parser). A missing parser is a scorer error, not a failure.
    require_parser('shell_capture_safe')
    if source == 'transcript':
        candidates = [('reply shell example', text, prose)
                      for text, prose in _transcript_shell(transcript or '')]
    else:
        candidates = []
        root = Path(workspace).resolve()
        for pattern in patterns:
            for hit in glob.iglob(str(root / pattern), recursive=True, include_hidden=True):
                path = Path(hit)
                try:
                    relative = path.relative_to(root)
                    if '.git' in relative.parts:
                        continue
                    if not path.resolve().is_relative_to(root):
                        return False, 'shell candidate escapes workspace'
                    if path.is_file():
                        candidates.append((str(relative), path.read_text(), False))
                except (OSError, UnicodeError, ValueError):
                    return False, 'shell candidate could not be read safely'
    for name, text, prose in candidates:
        for body, complete in _substitutions(text, prose=prose):
            try:
                if not complete:
                    raise BashParseError('incomplete_substitution')
                root = parse_bash(body + '\n')
                unsafe = _unsafe_capture(root)
            except BashParseError as error:
                if str(error) == 'parser_unavailable':
                    raise
                reason = 'shell syntax could not be verified'
            except _AnalysisLimit:
                reason = 'shell analysis exceeded its bound'
            else:
                if unsafe:
                    return False, f'{name}: dispatch and discovery share a command substitution'
                continue
            # An unverifiable body fails closed only if it may dispatch.
            if _mentions_dispatch(body, unverifiable=True):
                return False, f'{name}: {reason}'
    return True, 'no concatenated dispatch/discovery capture'
