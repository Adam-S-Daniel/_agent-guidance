"""Bounded symbolic execution of the staged-hook idioms in ADR 0007.

This recognizes a deliberately small Bash language. It does not execute shell
commands, trust arbitrary function bodies, or infer provenance from text.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .bash_ast import parse_bash

# Analysis bounds (ADR 0007). Each guarded tool call splits the continuing
# states in two. The reference example-site-platform hook with a four-tool Go branch
# peaks at 864 states and about 13,000 steps; five tools peak near 1,700
# states and 28,000 steps, which these bounds still admit.
MAX_STATES = 2048
MAX_STEPS = 65536


class ShellGuardError(ValueError):
    """A fixed scoring failure reason."""


@dataclass(frozen=True)
class _Value:
    kind: str
    literal: str | None = None
    missing: frozenset[str] = frozenset()
    tool_failure: bool = False


@dataclass
class _State:
    variables: dict[str, _Value] = field(default_factory=dict)
    available: frozenset[str] = frozenset()
    missing: frozenset[str] = frozenset()
    nonempty: frozenset[str] = frozenset()
    empty: frozenset[str] = frozenset()
    status: int = 0
    status_missing: frozenset[str] = frozenset()
    tool_failure: bool = False
    errexit: bool = False

    def copy(self, **changes):
        return replace(self, variables=dict(self.variables), **changes)

    def key(self):
        return (tuple(sorted(self.variables.items())), self.available, self.missing,
                self.nonempty, self.empty, self.status, self.status_missing,
                self.tool_failure, self.errexit)


class StagedToolGuard:
    _ENVIRONMENT_NAMES = {"PATH", "IFS", "BASH_ENV", "ENV", "CDPATH", "SHELLOPTS", "BASHOPTS",
                          "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
    # The reference hook guards these local executables with `[ -x ... ]` of
    # a sibling path, which records no availability fact.
    _NEIGHBOR_TOOLS = {"node_modules/.bin/eslint", "node_modules/.bin/prettier",
                       "node_modules/.bin/stylelint"}
    _RESERVED_HELPERS = {"command", "type", "hash", "git", "grep", "mapfile", "readarray", "xargs",
                         "read", "exit", "set", "echo", "printf", "cat", ":", "true", "false", "cd",
                         "eval", "source", ".", "alias", "unalias", "exec", "env", "bash", "sh"}
    def __init__(self, root, tools):
        self.root, self.tools = root, set(tools)
        self.helpers, self.found = {}, set()
        self.steps = 0

    @staticmethod
    def text(node):
        return node.text.decode("utf-8")

    def literal(self, node):
        if node is None:
            return None
        if node.type in ("word", "number", "string_content", "variable_name"):
            text = self.text(node)
            return text if "\\" not in text else None
        if node.type == "raw_string":
            return self.text(node)[1:-1]
        if node.type in ("command_name", "string"):
            children = node.named_children
            return self.literal(children[0]) if len(children) == 1 else ("" if not children else None)
        return None

    def reference(self, node):
        if node is None:
            return None
        if node.type == "string" and len(node.named_children) == 1:
            return self.reference(node.named_children[0])
        if node.type == "simple_expansion" and len(node.named_children) == 1:
            return (self.text(node.named_children[0]), "scalar")
        if node.type == "expansion":
            children = node.named_children
            if len(children) == 1 and children[0].type == "variable_name":
                name = self.text(children[0])
                if self.text(node) == "${" + name + "}":
                    return (name, "scalar")
            if len(children) == 1 and children[0].type == "subscript":
                name = self.text(children[0].child_by_field_name("name"))
                if self.text(node) == "${" + name + "[@]}":
                    return (name, "array")
                if self.text(node) == "${#" + name + "[@]}":
                    return (name, "count")
        return None

    def default_reference(self, node):
        if node.type == "string" and len(node.named_children) == 1:
            node = node.named_children[0]
        if node.type != "expansion":
            return None
        operator = node.child_by_field_name("operator")
        children = node.named_children
        if (operator and self.text(operator) == ":-" and children
                and children[0].type == "variable_name"
                and all(self.literal(c) is not None for c in children[1:])):
            return (self.text(children[0]), "scalar")
        return None

    def redirects(self, node, process=False):
        if node.type != "redirected_statement":
            return node, None
        source = None
        heredoc = False
        for redirect in node.children_by_field_name("redirect"):
            if redirect.type == "heredoc_redirect":
                heredoc = True
                continue
            dest = redirect.child_by_field_name("destination")
            if process and dest and dest.type == "process_substitution":
                operators = [self.text(c) for c in redirect.children if not c.is_named]
                if source is not None or self.text(dest.children[0]) != "<(" or operators != ["<"]:
                    raise ShellGuardError("unsupported_staged_source")
                source = dest
            elif (redirect.type != "file_redirect"
                  or self.text(redirect).replace(" ", "") not in
                  (">/dev/null", "1>/dev/null", "2>/dev/null", "2>&1", ">&2", "1>&2")):
                raise ShellGuardError("unsupported_redirect")
        if source is not None and heredoc:
            raise ShellGuardError("unsupported_staged_source")
        return node.child_by_field_name("body"), source

    def command(self, node):
        node, _ = self.redirects(node)
        if node.type != "command" or any(c.type == "variable_assignment" for c in node.named_children):
            return None, []
        return self.literal(node.child_by_field_name("name")), node.children_by_field_name("argument")

    def availability(self, node, parameter=False):
        name, args = self.command(node)
        words = [self.literal(a) for a in args]
        target = None
        if name == "command" and len(args) == 2 and words[0] == "-v":
            target = args[1]
        elif name == "type" and len(args) == 1:
            target = args[0]
        elif name == "type" and len(args) == 2 and words[0] == "-P":
            target = args[1]
        elif name == "hash" and len(args) == 1:
            target = args[0]
        elif self.helpers.get(name, (None,))[0] == "availability" and len(args) == 1:
            target = args[0]
        if target is None:
            return None
        if parameter:
            return "$1" if self.reference(target) == ("1", "scalar") else None
        return self.literal(target)

    def source(self, node, state, allow_unfiltered=False):
        """Return staged stream kind. Redirecting its output is never credited."""
        if node.type in ("command_substitution", "process_substitution"):
            children = [c for c in node.named_children if c.type != "comment"]
            if len(children) != 1:
                return None
            node = children[0]
        filtered = False
        if node.type == "pipeline":
            if len(node.named_children) != 2:
                return None
            node, grep = node.named_children
            if grep.type != "command":
                return None
            name, args = self.command(grep)
            filtered = name == "grep" and [self.literal(a) for a in args] in (
                ["\\.go$"], ["-E", "\\.go$"])
            if not filtered:
                return None
        if node.type != "command":
            return None
        name, args = self.command(node)
        words = [self.literal(a) for a in args]
        if self.helpers.get(name, (None,))[0] == "filter" and len(words) == 1:
            staged_name = self.helpers[name][1]
            staged = state.variables.get(staged_name)
            if staged and staged.kind in ("staged_array", "go_array") and words[0] is not None:
                return ("go" if words[0] == "\\.go$" else "other", False)
            return None
        if name != "git" or not words or words[0] != "diff" or None in words:
            return None
        words = words[1:]
        flags, tail = (words[:words.index("--")], words[words.index("--") + 1:]) if "--" in words else (words, [])
        if (sum(w in ("--cached", "--staged") for w in flags) != 1
                or flags.count("--name-only") != 1
                or any(w not in ("--cached", "--staged", "--name-only", "--diff-filter=ACM", "-z") for w in flags)
                or len(flags) != len(set(flags))):
            return None
        if tail == ["*.go"] or (filtered and not tail):
            if filtered and "-z" in flags:
                return None
            return ("go", "-z" in flags)
        return ("staged", "-z" in flags) if allow_unfiltered and not tail and not filtered else None

    def shape(self, node):
        """Compare executable AST structure, ignoring layout and comments."""
        children = [c for c in node.children if c.type != "comment" and c.type != ";"]
        return (node.type, tuple(self.shape(c) for c in children)) if children else (node.type, self.text(node))

    def diagnostic_printf(self, args):
        fmt = self.literal(args[0]) if args else None
        return (fmt is not None and not fmt.startswith("-")
                and (fmt in ("%s", "%s\\n", "%s\n") or ("%" not in fmt and "\\" not in fmt)))

    def register_helper(self, node):
        name = self.literal(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        children = [c for c in body.named_children if c.type != "comment"]
        if (node.parent.type != "program" or not name or name in self.helpers
                or name in self.tools | self._RESERVED_HELPERS | self._NEIGHBOR_TOOLS):
            raise ShellGuardError("unsupported_helper")
        if len(children) == 1 and self.availability(children[0], parameter=True):
            self.helpers[name] = ("availability",)
            return
        if len(children) == 1:
            command, args = self.command(children[0])
            if command == "echo" or (command == "printf" and self.diagnostic_printf(args)):
                self.helpers[name] = ("note",)
                return
        if len(children) == 2 and children[0].type == "declaration_command" and children[1].type == "for_statement":
            loop = children[1]
            variable = self.text(loop.child_by_field_name("variable"))
            values = loop.children_by_field_name("value")
            ref = self.reference(values[0]) if len(values) == 1 else None
            # Names come from parsed variable-name nodes, not source interpolation.
            if ref and ref[1] == "array" and values[0].type == "string" and variable not in self._ENVIRONMENT_NAMES:
                template = parse_bash('filter() { local f; for f in "${STAGED[@]}"; do '
                                      'printf \'%s\\n\' "$f" | grep -qE "$1" && printf \'%s\\n\' "$f"; done; }')
                expected = template.named_children[0].child_by_field_name("body")
                def normalized(n):
                    if n.type == "variable_name":
                        text = self.text(n)
                        return (n.type, "f" if text == variable else "STAGED" if text == ref[0] else text)
                    cs = [c for c in n.children if c.type not in ("comment", ";")]
                    return (n.type, tuple(normalized(c) for c in cs)) if cs else (n.type, self.text(n))
                if normalized(body) == self.shape(expected):
                    self.helpers[name] = ("filter", ref[0])
                    return
        raise ShellGuardError("unsupported_helper")

    def success(self, state, **changes):
        return state.copy(status=0, status_missing=frozenset(), **changes)

    def split(self, state, missing=frozenset(), tool_failure=False):
        return [self.success(state, tool_failure=False),
                state.copy(status=1, status_missing=missing, tool_failure=tool_failure)]

    def deduplicate(self, states):
        states = list({state.key(): state for state in states}.values())
        if len(states) > MAX_STATES:
            raise ShellGuardError("analysis_limit")
        return states

    def sequence(self, nodes, states, tested=False):
        for node in nodes:
            if node.type == "comment":
                continue
            states = self.deduplicate([new for state in states for new in self.execute(node, state, tested)])
        return states

    def condition(self, node, state):
        states = self.execute(node, state, tested=True)
        return [s for s in states if s.status == 0], [s for s in states if s.status != 0]

    def test(self, node, state):
        if len(node.named_children) != 1:
            raise ShellGuardError("unsupported_condition")
        expr = node.named_children[0]
        operator = expr.child_by_field_name("operator")
        op = self.text(operator) if operator else None
        ref, positive = None, True
        if expr.type == "unary_expression" and op in ("-n", "-z"):
            operands = [c for c in expr.named_children if c != operator]
            captured = self.tested_capture(operands, state)
            if captured is not None:
                return captured
            ref = self.reference(operands[0]) if len(operands) == 1 and operands[0].type == "string" else None
            positive = op == "-n"
            if ref and ref[1] != "scalar":
                ref = None
        elif expr.type == "binary_expression" and op in ("-gt", "-eq", "-ne"):
            ref = self.reference(expr.child_by_field_name("left"))
            if not ref or ref[1] != "count" or self.literal(expr.child_by_field_name("right")) != "0":
                ref = None
            positive = op != "-eq"
        if ref:
            name, kind = ref
            value = state.variables.get(name)
            if value and value.kind in ("staged_array", "other_array"):
                # These neighboring-language facts cannot guard a Go call.
                # Dropping them prevents irrelevant linter combinations from
                # multiplying the configured-tool analysis.
                return self.split(state)
            if value and ((kind == "count" and value.kind.endswith("_array"))
                          or (kind == "scalar" and not value.kind.endswith("_array"))):
                yes = self.success(state, nonempty=state.nonempty | {name}, empty=state.empty - {name},
                                   tool_failure=value.kind == "capture")
                no = state.copy(status=1, status_missing=frozenset(), nonempty=state.nonempty - {name},
                                empty=state.empty | {name}, tool_failure=value.kind == "capture")
                nonempty = [yes] if name not in state.empty else []
                empty = [no] if name not in state.nonempty else []
                if not positive:
                    nonempty = [s.copy(status=1) for s in nonempty]
                    empty = [self.success(s) for s in empty]
                return nonempty + empty
            # A test of an unrelated scalar supplies no staged-file fact.
            return self.split(state)
        if expr.type == "unary_expression" and op == "-x":
            operands = [c for c in expr.named_children if c != operator]
            if len(operands) == 1 and self.literal(operands[0]) is not None:
                return self.split(state)
        if expr.type == "binary_expression":
            left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
            ref = self.reference(left) or self.default_reference(left)
            literal = self.literal(right)
            if ref and op in ("=", "==", "!=", "-eq", "-ne") and literal is not None:
                value = state.variables.get(ref[0])
                if value and value.literal is not None:
                    if op in ("-eq", "-ne"):
                        try:
                            a, b = int(value.literal, 10), int(literal, 10)
                        except ValueError:
                            raise ShellGuardError("unsupported_condition") from None
                        if str(a) != value.literal or str(b) != literal:
                            raise ShellGuardError("unsupported_condition")
                        matches = a == b
                    else:
                        matches = value.literal == literal
                    matches = not matches if op in ("!=", "-ne") else matches
                    return [state.copy(status=0 if matches else 1, status_missing=frozenset(), tool_failure=value.tool_failure)]
                return self.split(state)
        raise ShellGuardError("unsupported_condition")

    def tested_capture_node(self, node):
        """A substitution that is the whole quoted operand of `-n`/`-z` in `[ ]`."""
        string = node.parent
        unary = string.parent if string else None
        test = unary.parent if unary else None
        if (not string or string.type != "string" or len(string.named_children) != 1
                or not unary or unary.type != "unary_expression"
                or not test or test.type != "test_command"):
            return False
        operator = unary.child_by_field_name("operator")
        return (operator is not None and self.text(operator) in ("-n", "-z")
                and len(node.named_children) == 1 and node.named_children[0].type == "command"
                and self.literal(node.named_children[0].child_by_field_name("name")) in self.tools)

    def tested_capture(self, operands, state):
        """`[ -z "$(TOOL ...)" ]`: the configured call runs, its output is tested.

        Equivalent to capturing into a variable and testing that: the call
        must satisfy every guard, and either test outcome is an installed
        tool's verdict, never a missing-tool failure.
        """
        if (len(operands) != 1 or operands[0].type != "string"
                or len(operands[0].named_children) != 1
                or operands[0].named_children[0].type != "command_substitution"):
            return None
        source = operands[0].named_children[0]
        if len(source.named_children) != 1 or self.command(source.named_children[0])[0] not in self.tools:
            return None
        out = []
        for current in self.execute(source.named_children[0], state, True):
            out.append(self.success(current, tool_failure=True))
            out.append(current.copy(status=1, status_missing=frozenset(), tool_failure=True))
        return out

    def assign(self, node, state, tested):
        target = node.child_by_field_name("name")
        if target.type != "variable_name":
            raise ShellGuardError("unsupported_dynamic_form")
        name = self.text(target)
        if name in self._ENVIRONMENT_NAMES:
            raise ShellGuardError("unsupported_dynamic_form")
        if any(c.type == "+=" for c in node.children):
            raise ShellGuardError("unsupported_staged_source")
        value = node.child_by_field_name("value")
        states = [self.success(state)]
        result = _Value("literal", self.literal(value) if value else "")
        source = value
        if source and source.type == "string" and len(source.named_children) == 1:
            source = source.named_children[0]
        if value and value.type == "array":
            if not value.named_children:
                result = _Value("empty_array")
            elif len(value.named_children) == 1 and value.named_children[0].type == "command_substitution":
                stream = self.source(value.named_children[0], state)
                if stream != ("go", False):
                    raise ShellGuardError("unsupported_staged_source")
                result = _Value("go_array")
            else:
                raise ShellGuardError("unsupported_staged_source")
        elif source and source.type == "command_substitution":
            stream = self.source(source, state)
            if stream == ("go", False):
                result = _Value("go_scalar")
            elif len(source.named_children) == 1 and self.command(source.named_children[0])[0] in self.tools:
                states = self.execute(source.named_children[0], state, tested)
                result = _Value("capture", tool_failure=True)
            else:
                command = source.named_children[0] if len(source.named_children) == 1 else None
                # Changing to the repository root cannot fabricate staged provenance.
                if command and self.command(command)[0] == "git" and [self.literal(a) for a in self.command(command)[1]] == ["rev-parse", "--show-toplevel"]:
                    result = _Value("root")
                else:
                    raise ShellGuardError("unsupported_staged_source")
        elif result.literal is None:
            raise ShellGuardError("unsupported_staged_source")
        if result.literal and result.literal.isdigit() and result.literal != "0":
            result = replace(result, missing=state.status_missing or (frozenset() if state.tool_failure else state.missing),
                             tool_failure=state.tool_failure)
        out = []
        for current in states:
            current = current.copy(nonempty=current.nonempty - {name}, empty=current.empty - {name})
            current.variables[name] = result
            if result.kind == "empty_array" or result.literal == "":
                current.empty |= {name}
            out.append(current)
        return out

    def mapfile(self, body, source, state):
        name, args = self.command(body)
        words = [self.literal(a) for a in args]
        if name not in ("mapfile", "readarray") or source is None:
            raise ShellGuardError("unsupported_staged_source")
        if len(words) < 2 or words[:-1] not in (["-t"], ["-d", ""]):
            raise ShellGuardError("unsupported_staged_source")
        variable = words[-1]
        if args[-1].type != "word" or not variable or not variable.isascii() or not variable.isidentifier():
            raise ShellGuardError("unsupported_staged_source")
        if variable in self._ENVIRONMENT_NAMES:
            raise ShellGuardError("unsupported_dynamic_form")
        stream = self.source(source, state, allow_unfiltered=True)
        if stream is None or stream[1] != (words[:-1] == ["-d", ""]):
            raise ShellGuardError("unsupported_staged_source")
        out = self.success(state, nonempty=state.nonempty - {variable}, empty=state.empty - {variable})
        out.variables[variable] = _Value(stream[0] + "_array")
        return [out]

    def read_loop(self, body, source, state):
        if source is None:
            raise ShellGuardError("unsupported_control_flow")
        conditions = [c for c in body.children_by_field_name("condition") if c.is_named]
        group = body.child_by_field_name("body")
        statements = [c for c in group.named_children if c.type != "comment"]
        if len(conditions) != 1 or conditions[0].type != "command" or len(statements) != 1:
            raise ShellGuardError("unsupported_control_flow")
        condition = conditions[0]
        command = self.literal(condition.child_by_field_name("name"))
        words = [self.literal(a) for a in condition.children_by_field_name("argument")]
        assignments = [c for c in condition.named_children if c.type == "variable_assignment"]
        if (command != "read" or not words or words[:-1] not in (["-r"], ["-r", "-d", ""])
                or any(self.text(a) != "IFS=" for a in assignments) or len(assignments) > 1):
            raise ShellGuardError("unsupported_control_flow")
        item = words[-1]
        if not item or not item.isascii() or not item.isidentifier():
            raise ShellGuardError("unsupported_dynamic_form")
        append = statements[0]
        if append.type != "variable_assignment" or not any(c.type == "+=" for c in append.children):
            raise ShellGuardError("unsupported_control_flow")
        target = append.child_by_field_name("name")
        if target.type != "variable_name":
            raise ShellGuardError("unsupported_dynamic_form")
        variable = self.text(target)
        if variable in self._ENVIRONMENT_NAMES or item in self._ENVIRONMENT_NAMES:
            raise ShellGuardError("unsupported_dynamic_form")
        value = append.child_by_field_name("value")
        if (not value or value.type != "array" or len(value.named_children) != 1
                or value.named_children[0].type != "string"
                or self.reference(value.named_children[0]) != (item, "scalar")
                or state.variables.get(variable) != _Value("empty_array")):
            raise ShellGuardError("unsupported_staged_source")
        if self.source(source, state) != ("go", words[:-1] == ["-r", "-d", ""]):
            raise ShellGuardError("unsupported_staged_source")
        out = self.success(state, nonempty=state.nonempty - {variable}, empty=state.empty - {variable})
        out.variables[variable] = _Value("go_array")
        return [out]

    def invoke(self, name, args, state, streamed=False):
        refs = [self.reference(arg) for arg in args]
        variables = set()
        for arg, ref in zip(args, refs):
            value = state.variables.get(ref[0]) if ref else None
            if value and value.kind == "go_scalar" and ref[1] == "scalar":
                if arg.type == "string":
                    raise ShellGuardError("scalar_paths_quoted")
                variables.add(ref[0])
            elif value and value.kind == "go_array" and ref[1] == "array":
                if arg.type != "string":
                    raise ShellGuardError("unsupported_tool_arguments")
                variables.add(ref[0])
            else:
                literal = self.literal(arg)
                if literal != "run" and (literal is None or not literal.startswith("-")):
                    raise ShellGuardError("staged_paths_missing" if ref else "unsupported_tool_arguments")
        if not variables and not streamed:
            raise ShellGuardError("staged_paths_missing")
        if name not in state.available:
            raise ShellGuardError("availability_guard_missing")
        if not variables <= state.nonempty:
            raise ShellGuardError("nonempty_guard_missing")
        self.found.add(name)
        return self.split(state, tool_failure=True)

    def xargs(self, node, state):
        if len(node.named_children) != 2:
            raise ShellGuardError("unsupported_control_flow")
        producer, consumer = node.named_children
        name, args = self.command(consumer)
        words = [self.literal(a) for a in args]
        if name != "xargs" or not words:
            raise ShellGuardError("unsupported_control_flow")
        if words[0] in ("-r", "--no-run-if-empty"):
            words = words[1:]
            args = args[1:]
        if not words or words[0] not in self.tools:
            raise ShellGuardError("unsupported_control_flow")
        output_name, output_args = self.command(producer)
        if producer.type != "command":
            raise ShellGuardError("unsupported_staged_source")
        refs = [self.reference(a) for a in output_args]
        if output_name == "printf" and len(output_args) == 2 and self.literal(output_args[0]) == "%s\\n":
            ref = refs[1]
        elif output_name == "echo" and len(output_args) == 1:
            ref = refs[0]
        else:
            ref = None
        value = state.variables.get(ref[0]) if ref else None
        if not value or value.kind != "go_scalar" or ref[1] != "scalar":
            raise ShellGuardError("staged_paths_missing")
        if ref[0] not in state.nonempty:
            raise ShellGuardError("nonempty_guard_missing")
        return self.invoke(words[0], args[1:], state, streamed=True)

    def neighbor_arguments(self, args, state):
        """Bound a guarded unconfigured call to flags, one subcommand, and staged paths."""
        for index, arg in enumerate(args):
            ref = self.reference(arg)
            value = state.variables.get(ref[0]) if ref else None
            if value and ref[1] == "array" and arg.type == "string" and value.kind in (
                    "staged_array", "go_array", "other_array"):
                continue
            if value and ref[1] == "scalar" and arg.type != "string" and value.kind == "go_scalar":
                continue
            if ref or arg.type not in ("word", "number"):
                return False
            word = self.literal(arg)
            if word is None or "/" in word and word != "./...":
                return False
            # Lexical token classes only: a flag, a numeric option value, a
            # Go package pattern, or a leading subcommand that is not a tool.
            if word == "./..." or word.isdecimal() or (
                    word.startswith("-") and word.lstrip("-")[:1].isalnum()):
                continue
            if (index == 0 and word.isascii() and word[:1].isalpha()
                    and word.replace("-", "").replace("_", "").isalnum() and word not in self.tools):
                continue
            return False
        return True

    def exit_status(self, state, status, missing, tool_failure=None):
        origin = state.tool_failure if tool_failure is None else tool_failure
        if status != 0 and (missing or (state.missing and not origin)):
            raise ShellGuardError("missing_tool_nonzero_exit")

    def execute(self, node, state, tested=False):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ShellGuardError("analysis_limit")
        states = self._execute(node, state, tested)
        if state.errexit and not tested and node.type not in ("list", "if_statement", "function_definition", "negated_command"):
            for current in states:
                if current.status != 0:
                    self.exit_status(current, current.status, current.status_missing)
            states = [s for s in states if s.status == 0]
        return states

    def _execute(self, node, state, tested):
        if node.type == "comment":
            return [state]
        if node.type == "redirected_statement":
            body, source = self.redirects(node, process=True)
            if source is None and body.type != "command":
                return self.execute(body, state, tested)
        if node.type == "function_definition":
            self.register_helper(node)
            return [self.success(state)]
        if node.type == "variable_assignment":
            return self.assign(node, state, tested)
        if node.type == "if_statement":
            conditions = [c for c in node.children_by_field_name("condition") if c.is_named]
            if len(conditions) != 1:
                raise ShellGuardError("unsupported_condition")
            yes, no = self.condition(conditions[0], state)
            children = [c for c in node.named_children if c not in conditions]
            alternate = [c for c in children if c.type in ("else_clause", "elif_clause")]
            if any(c.type == "elif_clause" for c in alternate):
                raise ShellGuardError("unsupported_control_flow")
            return (self.sequence([c for c in children if c not in alternate], yes, tested)
                    + (self.sequence(alternate[0].named_children, no, tested) if alternate
                       else [self.success(s) for s in no]))
        if node.type == "list":
            operators = [self.text(c) for c in node.children if not c.is_named]
            if operators not in (["&&"], ["||"]) or len(node.named_children) != 2:
                raise ShellGuardError("unsupported_control_flow")
            left, right = node.named_children
            yes, no = self.condition(left, state)
            run, skip = (yes, no) if operators == ["&&"] else (no, yes)
            return self.sequence([right], run, tested) + skip
        if node.type == "negated_command" and len(node.named_children) == 1:
            return [s.copy(status=1 if s.status == 0 else 0, status_missing=frozenset())
                    for s in self.execute(node.named_children[0], state, tested=True)]
        if node.type == "test_command":
            return self.test(node, state)
        if node.type == "pipeline":
            return self.xargs(node, state)
        body, source = self.redirects(node, process=True)
        if body.type == "while_statement":
            return self.read_loop(body, source, state)
        name, args = self.command(body)
        if name in ("mapfile", "readarray"):
            return self.mapfile(body, source, state)
        if source is not None:
            raise ShellGuardError("unsupported_staged_source")
        if name in self.tools:
            return self.invoke(name, args, state)
        tool = self.availability(body)
        if tool:
            if tool in state.available:
                return [self.success(state)]
            if tool in state.missing:
                return [state.copy(status=1, status_missing=frozenset({tool}), tool_failure=False)]
            yes, no = self.split(state, frozenset({tool}) if tool in self.tools else frozenset())
            # An unconfigured tool's fact only permits a guarded neighbor call.
            yes.available |= {tool}
            if tool in self.tools:
                no.missing |= {tool}
            return [yes, no]
        if name == "exit":
            if len(args) > 1:
                raise ShellGuardError("unsupported_exit")
            literal = self.literal(args[0]) if args else str(state.status)
            ref = self.reference(args[0]) if args else None
            value = state.variables.get(ref[0]) if ref else None
            if value:
                literal = value.literal
            if literal is None or not literal.isdigit() or not 0 <= int(literal) <= 255:
                raise ShellGuardError("unsupported_exit")
            missing = (value.missing if value else state.status_missing or
                       (frozenset() if state.tool_failure else state.missing))
            self.exit_status(state, int(literal), missing, value.tool_failure if value else None)
            return []
        if self.helpers.get(name, (None,))[0] == "note":
            return [self.success(state)]
        if name in ("echo", "printf", "cat", ":", "true", "false"):
            if name == "printf" and not self.diagnostic_printf(args):
                raise ShellGuardError("unsupported_dynamic_form")
            if name == "cat" and (node.type != "redirected_statement" or args):
                raise ShellGuardError("unsupported_command")
            if name == "false":
                return [state.copy(status=1, status_missing=frozenset() if state.tool_failure else state.missing)]
            return [self.success(state)]
        if name == "set":
            words = [self.literal(a) for a in args]
            if words not in (["-e"], ["-eu"], ["-euo", "pipefail"], ["-uo", "pipefail"], ["-u"], ["-o", "pipefail"]):
                raise ShellGuardError("unsupported_command")
            return [self.success(state, errexit=state.errexit or any(w.startswith("-") and "e" in w for w in words))]
        if name == "cd" and len(args) == 1:
            ref = self.reference(args[0])
            if ref and state.variables.get(ref[0]) == _Value("root"):
                return [self.success(state)]
        # Actual lint-staged.sh also invokes tools outside the configured Go
        # set. Only its established availability helper and path-array idiom
        # permit these calls; unrelated commands do not become arbitrary code.
        if name in self._NEIGHBOR_TOOLS and args and any(
                self.reference(a) and state.variables.get(self.reference(a)[0], _Value("")).kind == "other_array" for a in args):
            return self.split(state, tool_failure=True)
        if (name in state.available and name not in self._RESERVED_HELPERS and name not in self.helpers
                and self.neighbor_arguments(args, state)):
            return self.split(state, tool_failure=True)
        raise ShellGuardError("unsupported_command" if name else "unsupported_control_flow")

    def check(self):
        pending = [(self.root, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > 4096 or depth > 64:
                raise ShellGuardError("analysis_limit")
            if node.type == "command":
                name = self.literal(node.child_by_field_name("name"))
                if name is None or name in ("eval", "source", ".", "alias", "unalias", "exec", "bash", "sh"):
                    raise ShellGuardError("unsupported_dynamic_form")
            if node.type == "expansion" and self.reference(node) is None and self.default_reference(node) is None:
                raise ShellGuardError("unsupported_dynamic_form")
            if node.type == "command_substitution":
                parent = node.parent
                while parent and parent.type in ("array", "string"):
                    parent = parent.parent
                if (not parent or parent.type != "variable_assignment") and not self.tested_capture_node(node):
                    raise ShellGuardError("unsupported_dynamic_form")
            if any(c.type == "&" for c in node.children):
                raise ShellGuardError("unsupported_control_flow")
            pending.extend((child, depth + 1) for child in node.named_children)
        states = self.sequence(self.root.named_children, [_State()])
        for state in states:
            self.exit_status(state, state.status, state.status_missing)
        if self.found != self.tools:
            raise ShellGuardError("tool_not_invoked")
