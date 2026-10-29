# ADR 0007: Parse configuration values and staged shell guards before scoring

- **Status:** accepted (2026-10-04); amended 2026-10-05 to raise the
  continuing-state bound to 2,048 and the step bound to 65,536 (see
  "Amendment: analysis bounds" below) and to read a configured tool's
  output tested by `[ -z "$(...)" ]` as a capture.
- **Issue:** Part of [the code-quality eval, #88](https://github.com/Adam-S-Daniel/skills-evals/issues/88).
- **Decider:** the owner approved both check types and a Bash parser dependency
  on 2026-10-04; the C58 worker package records that approval.

## Context

The code-quality eval needs evidence that a configuration contains an integer
width of 100 and that local hooks pass staged Go files to available tools.
Text matching credits comments, unrelated nested keys, string-valued numbers,
and disconnected shell guards. YAML's acceptance of JSON-looking text is not
evidence that a JSON manifest parses. Existing fixtures must keep their scores.

## Decision

Add two opt-in objective types, without changing existing checks:

- `parsed_config_values`: exact workspace-relative `paths`, explicit `format`
  (`yaml` or `json`), and optional `expected` entries shaped as
  `{path: [mapping, keys], equals: <typed value>}` or
  `{path: [mapping, keys], contains: <typed list member>}`. An omitted or empty
  `expected` list checks parsing only. Roots must be mappings; keys must be
  strings and values must be finite JSON-shaped scalars, lists, or mappings.
  PyYAML's composed tree rejects duplicate keys at every depth before
  construction; JSON uses `json.loads` with duplicate-key and nonfinite-number
  rejection. Recursive aliases, merge keys, custom tags, excessive depth or
  size, and nonmapping roots fail. Type and value must match recursively:
  booleans, integers, floats, and strings remain distinct.
- `shell_staged_tool_guard`: exact workspace-relative `paths` and a nonempty
  list of literal `tools`. Parse Bash with Tree-sitter and analyze paths through
  the AST; never execute the script. Each tool must have a reachable direct
  invocation, and every reachable invocation must receive a proven staged Go
  variable and be dominated by availability and nonempty-file facts. A branch
  where a tool is missing may skip with `exit 0`, but cannot exit nonzero.

Recognized staged streams come from `git diff --cached --name-only` (also
`--staged`), with optional `--diff-filter=ACM` and either a literal `-- '*.go'`
pathspec or a newline stream piped to `grep '\.go$'` / `grep -E '\.go$'`.
Scalar `files=$(...)` and split-array `files=($(...))` accept newline streams.
`mapfile -t files < <(...)` accepts newline streams; `mapfile -d '' files
< <(... -z)` accepts NUL streams. A while-read collector starts with an empty
array and uses `while IFS= read -r -d '' f; do files+=("$f"); done < <(... -z)`;
`read -r` is the newline variant. Delimiters must agree; NUL output cannot be
credited through command substitution, a line-oriented grep, or `mapfile -t`.
Only input process substitution is accepted. A pipeline-to-while subshell
cannot supply parent-array provenance. Source output redirection and a heredoc
that replaces the input fail.

The reference [example-site-platform staged hook](https://github.com/example-adopter/example-site-platform/blob/main/scripts/lint-staged.sh), documented by its [code-quality skill](https://github.com/example-adopter/example-site-platform/blob/main/skills/code-quality/SKILL.md), first collects all staged files and then filters them. Its `filter()` helper is recognized by the complete parsed
structure: a local loop variable, iteration over the staged array, a
`printf '%s\n'` stream tested by `grep -qE "$1"`, and conditional emission of
that same path. A literal `\.go$` filter establishes Go provenance. Other
language filters do not supply Go facts. The full reference hook with a Go
branch and three alternative correct styles are committed under
`test/fixtures/issue88/` as regression inputs.

Tool calls accept unquoted scalar `$files` / `${files}`, quoted array
`"${files[@]}"`, or a guarded scalar `printf '%s\n' "$files" | xargs TOOL ...`
(`echo` and `xargs -r` are also recognized). A quoted scalar is one argument
containing newline-joined names and fails `scalar_paths_quoted`; tests exercise
two staged paths with inert command doubles. Array counts use `-gt 0`, `-ne 0`,
or an inverted `-eq 0` early skip. Scalar nonempty tests use `-n "$files"` or
an inverted `-z`. These scalar/split-array/xargs forms do not claim safe handling
of every filename; the NUL collector plus quoted arrays is the safe recognized
form for whitespace-containing names.

Availability is `command -v TOOL`, `type TOOL` / `type -P TOOL`, or `hash TOOL`.
An availability helper's entire body must be that query, optionally redirected.
A diagnostic helper may only echo or printf a literal safe format (`%s`,
`%s\n`, or plain text); dynamic formats, `printf -v`, and `%n` are rejected.
Helpers cannot shadow interpreted builtins or tools. The reference filter,
availability, and diagnostic helpers may coexist. An unconfigured literal
command (for example golangci-lint, staticcheck, or `go vet`) is a neighboring
call when the same availability query or helper dominates it. It earns no
credit, its status splits like an installed linter's, and its arguments are
limited to flags without a `/`, decimal option values, one leading literal
subcommand that is not a configured tool, the Go package pattern `./...`, and
staged arrays or unquoted Go scalars. Unguarded or dynamically named commands,
interpreted builtins, other argument shapes, and command substitutions still
fail. The reference hook's local eslint, prettier, and stylelint executables,
guarded by `[ -x ... ]` of a sibling path, remain the only listed exceptions.
Neighboring calls cannot establish configured-tool facts.

Two related shapes keep their current scoring. After a passing availability
guard, `[ -n "$files" ] && gofmt -l $files` as the last line passes although
the script then exits 1 on a commit without Go files: this check rejects a
nonzero exit only when it is caused by a missing configured tool, so an
empty-list exit status is left to review. A configured-tool substitution
that is the whole quoted operand of `-z` or `-n` inside `[ ]` or `[[ ]]`,
such as `[ -z "$(gofmt -l "${files[@]}")" ] || RC=1`, is read as a capture
followed by a test (amended 2026-10-05): the call needs every guard, and
either outcome is an installed tool's verdict. Any other command
substitution outside an assignment, including the `test -z "$(...)"`
command form and a substitution with redirections, still fails closed as
`unsupported_dynamic_form`.

`if`/`else`, nested guards, negation, `&&`/`||` lists, stderr/null redirections,
`set -e`/`-u`/`pipefail`, output capture followed by a nonempty test, and numeric
RC accumulators propagate separate continuing states. The last command's
status is the script's implicit exit status; a terminal availability `&&` list
therefore fails when a configured tool is missing. Errexit is suppressed in
conditions, tested list elements, and negation. An `if` with no selected branch
has status zero. Missing-tool failures remain distinct from failures of an
installed linter, including a saved RC value from another language. Numeric
comparison supports canonical decimal literals and fails closed on other forms.
Reassignment invalidates provenance and nonempty facts; false paths cannot
supply calls. Assignments to shell or Git environment selectors are rejected.

Fail closed on `eval`, source/`.` commands, aliases, indirect expansions,
dynamic command names, arbitrary command substitutions, unsupported redirects,
general loops, case statements, subshells, background execution, wrappers, and
helper bodies outside the recognized structures. Comments, literal strings,
and literal heredoc bodies never supply calls or guards. The implementation is
a bounded recognizer, not a general Bash interpreter: at most 4,096 AST nodes,
64 levels of nesting, 2,048 continuing states, and 65,536 analysis steps. Fixed
named failures identify unsupported forms. `scorers/bash_ast.py` exposes
`parse_bash` independently of the objective scorer and shell interpreter for
other structural checks.

Use exact pins `tree-sitter==0.26.0` and `tree-sitter-bash==0.25.1`. PyPI JSON
was checked on 2026-10-04: the latest non-prerelease releases were published
2026-06-30 and 2025-12-02, respectively, both older than seven days.
Install both in every workflow that installs the objective harness; there is
no requirements file or Python package manifest in this repository.

## Amendment: analysis bounds (2026-10-05)

The first bounds were 256 states and 32,768 steps. Every guarded tool call
splits the continuing states in two, and the reference example-site-platform hook's
six language branches already peak at 240 states with the two-tool Go branch.
A third correctly guarded Go tool (`go vet`, `staticcheck`), which the
code-quality skill tells agents to add, therefore failed `analysis_limit` on
a correct hook. Measured on that hook: three Go tools peak at 448 states and
6,334 steps, four at 864 and 13,342, five at 1,696 and 27,742. The bounds are
now 2,048 states and 65,536 steps, which admit five tools; the four-tool hook
analyzes in well under a second, and a test bounds its step count (a
deterministic stand-in for time). The node, depth
and input-size bounds are unchanged.

## Consequences

The new types reject deceptive evidence without altering old fixtures. Paths
must resolve to present regular files inside the workspace; symlinks escaping
it fail. Input is capped at 64 KiB, with bounded tree depth and node counts.
Failure details use fixed reasons and never echo file contents or parser errors.

The parser adds two small binary-wheel downloads to Linux CI (approximately
668 KiB runtime and 232 KiB grammar), and compiled extensions instead of a
pure-Python dependency. Supported wheels avoid compiler setup on ordinary
CPython runners; platforms without wheels require a build toolchain. Syntax
recovery nodes fail rather than being credited as valid code.

## Alternatives considered

**bashlex 0.18:** pure Python and cheap to install, but its latest PyPI release
is from 2023. Its documented limitations include complex parameter expansions
that remain literal text without child nodes. This check needs to distinguish
array references, array counts, and indirect expansions structurally, along
with command and heredoc boundaries. Tree-sitter's maintained Bash grammar
represents those constructs directly.

**Regular expressions or token matching:** cannot establish typed mapping
paths, control-flow dominance, or distinguish executable calls from strings.

**Executing hook scripts:** would cross a trust boundary, depend on installed
tools and Git state, and could run agent-authored commands. Parsing is hermetic.

## References

- [Tree-sitter Python runtime release](https://pypi.org/project/tree-sitter/0.26.0/)
- [Bash grammar release and wheel inventory](https://pypi.org/project/tree-sitter-bash/0.25.1/)
- [Bash grammar source](https://github.com/tree-sitter/tree-sitter-bash)
- [bashlex limitations](https://github.com/idank/bashlex#limitations)
