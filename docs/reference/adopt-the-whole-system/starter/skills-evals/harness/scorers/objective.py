"""Objective (scriptable) checks against an eval output workspace.

Each check type inspects the workspace files a fixture points at and returns
(passed: bool, detail: str). Check types are registered in CHECKS; fixtures
reference them by their `type` field.
"""

from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# S1-a-2. The one timeout predicate lives in harness/guidance.py, beside the
# ceiling and the error type it raises, and every function here that hands a
# timeout to a subprocess API calls it on entry. `guidance` is imported inside
# those functions rather than at module scope (guidance.py imports run_canary,
# and this package is imported from run_eval while guidance is still
# initialising), so harness/ has to be reachable from here.
_HARNESS_DIR = str(Path(__file__).resolve().parent.parent)
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

# The bound on every local `git` call in this module. Named rather than
# inlined so the sink check and the `timeout=` argument are provably the same
# value: the pin compares the two expressions, not two beliefs about them.
GIT_TIMEOUT_S = 10

import workspace_git

from . import invisibles, wrapping
from .bash_ast import ScorerUnavailableError  # noqa: F401  (re-exported)
from .shell_capture import shell_capture_safe
from .commands import command_succeeds
from .repo_tests import repo_tests, CONSTRAINT_KEYS as _REPO_TESTS_KEYS

# Remote action ref: owner/repo[/path]@ref — excludes local (./) and docker:// refs.
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(\s*#.*)?\s*$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _workflow_uses(workspace: str, patterns: list[str]) -> list[tuple[str, int, str, str]]:
    """Yield (file, lineno, ref, trailing_comment) for every `uses:` line."""
    out = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    m = USES_RE.match(line)
                    if m:
                        out.append((os.path.relpath(path, workspace), lineno,
                                    m.group(1), (m.group(2) or "").strip()))
    return out


def _is_remote_action(ref: str) -> bool:
    return not ref.startswith(("./", "docker://"))


def uses_refs_sha_pinned(workspace: str, patterns: list[str],
                         platform_prefix: str | None = None) -> tuple[bool, str]:
    """Every remote (non-local, non-docker) `uses:` ref is a bare full
    40-character commit SHA — never a tag, an abbreviated SHA, or anything
    else.

    Locates each `uses:` value with a real YAML parse (`_uses_value_nodes`,
    the same composed-tree walk `pin_comment_absent` uses) and tests the
    node's own `.value` against `SHA_RE` — never a line regex, which a
    quoted pin (the quote characters land inside the captured "ref") or a
    `uses:`-shaped line inside a `run: |` block scalar (prose, not a mapping
    key) both defeat.

    `platform_prefix`, when given, skips a cross-repo reference to this
    account's own platform repo (case-folded, matching `platform_refs_on_tag`'s
    own comparison): that ref is meant to stay on its release TAG, not a
    SHA, so scoring it here would fail the one thing the carve-out requires.
    `platform_refs_on_tag` is what actually checks such a ref, in whichever
    files the fixture points it at.
    """
    import yaml
    bad = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            try:
                doc = yaml.compose(text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                continue  # yaml_parses reports this; nothing to check here
            if doc is None:
                continue
            rel = os.path.relpath(path, workspace)
            for value_node in _uses_value_nodes(doc):
                ref = value_node.value
                if not isinstance(ref, str) or not _is_remote_action(ref):
                    continue
                if (platform_prefix
                        and platform_prefix.casefold() in ref.casefold()):
                    continue  # platform_refs_on_tag's business, not ours
                if not SHA_RE.match(ref.rsplit("@", 1)[-1]):
                    lineno = value_node.start_mark.line + 1
                    bad.append(f"{rel}:{lineno} {ref}")
    return (not bad, "all remote refs SHA-pinned" if not bad
            else "unpinned: " + "; ".join(bad))


def _mapping_value_nodes(node, key: str, _seen: set | None = None) -> list:
    """Every YAML *value* Node bound to a literal `key` mapping key, found by
    walking the composed document tree — never by scanning the raw text for
    the key's name, which could also match inside another field or a
    comment. Recurses into every Mapping/Sequence node so it finds `key`
    wherever it appears: a step, a job-level reusable-workflow call, a
    composite action's `runs.steps`, or a `with:` block.

    `_seen` tracks visited nodes by `id()`: `yaml.compose` resolves an alias
    (`*x`) to the SAME node object anchored by `&x`, so a self-referential
    anchor (`a: &x\n  b: *x`) makes a node its own descendant — an unguarded
    walk recurses forever. A node already visited contributes nothing new.
    """
    import yaml
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return []
    _seen.add(node_id)
    out = []
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
                out.append(value_node)
            out.extend(_mapping_value_nodes(key_node, key, _seen))
            out.extend(_mapping_value_nodes(value_node, key, _seen))
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            out.extend(_mapping_value_nodes(item, key, _seen))
    return out


def _uses_value_nodes(node, _seen: set | None = None) -> list:
    """Every YAML *value* Node bound to a literal `uses:` mapping key — see
    `_mapping_value_nodes`."""
    return _mapping_value_nodes(node, "uses", _seen)


def _line_has_trailing_comment(line: str, after_column: int) -> bool:
    """Does `line` carry a `#` comment starting at or after `after_column`?

    `after_column` is the value node's own `end_mark.column` — the point
    where its textual representation (quotes included, for a quoted scalar)
    actually ends. Anchoring the search there, rather than scanning the
    whole line from its start, is what keeps a `#` embedded INSIDE a quoted
    value (e.g. `uses: "ref # not a comment"`) from being mistaken for a
    trailing comment: that `#` sits before the value's own end_mark, so it is
    never examined. YAML still requires the character before a genuine
    comment-opening `#` to be whitespace or the start of the line.
    """
    idx = line.find("#", after_column)
    return idx != -1 and (idx == 0 or line[idx - 1] in " \t")


def pin_comment_absent(workspace: str, patterns: list[str]) -> tuple[bool, str]:
    """No `uses:` ref carries a trailing version comment.

    Rule 2 (reversed 2026-08-20 — see example-site-platform's github-actions-sha-pinning
    skill): a `uses:` line ends at its ref, third-party SHA or example-site-platform
    tag alike, with nothing after it. Locates each `uses:` node with a real
    YAML parse (`yaml.compose`, which keeps line numbers) rather than a regex
    over the file, then reads only THAT line's raw text to decide whether a
    comment trails it. A regex over the whole file can't reliably tell a
    `uses:` value's line from any other, and mistaking one for the other is
    exactly the class of bug locating the node structurally first avoids.

    Two known limits, both a consequence of inspecting only the value node's
    own line:

    - A version comment moved to the line ABOVE the step (rather than
      trailing the `uses:` line itself) is invisible — nothing here looks at
      any line but the value node's own.
    - A comment trailing a YAML ALIAS (`*x`) is invisible: `yaml.compose`
      resolves the alias to the SAME Node object as its anchor (`&x`), so
      `value_node.end_mark` reports the ANCHOR's line, not the alias
      occurrence's — a comment on the alias's own line is checked against
      the wrong line entirely.

    Both are the kind of intent a strict text match can't see either way;
    the fixture's `comment_removal` judge dimension covers them instead of
    this scoring them structurally.
    """
    import yaml
    bad = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            try:
                doc = yaml.compose(text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                continue  # yaml_parses reports this; nothing to check here
            if doc is None:
                continue
            lines = text.splitlines()
            rel = os.path.relpath(path, workspace)
            for value_node in _uses_value_nodes(doc):
                lineno = value_node.end_mark.line
                if lineno >= len(lines):
                    continue
                raw = lines[lineno]
                if _line_has_trailing_comment(raw, value_node.end_mark.column):
                    bad.append(f"{rel}:{lineno + 1} {raw.strip()}")
    return (not bad, "no trailing comment on any uses: pin" if not bad
            else "version comment present: " + "; ".join(bad))


def platform_refs_on_tag(workspace: str, patterns: list[str],
                         platform_prefix: str | None = None,
                         tag: str | None = None,
                         min_refs: int | None = None) -> tuple[bool, str]:
    """The carve-out, checked structurally: every `uses:` value node whose
    leaf names `platform_prefix` (a cross-repo reference to this account's
    own example-site-platform) must carry `@<tag>` exactly — never a SHA, never a
    different tag — and every `platform_ref:` value node must equal `tag`
    too.

    Replaces a `file_matches` must_match/must_not_match regex pair that
    decided two independent YAML values by scanning raw concatenated text: a
    `# was platform_ref: v0.1.104` comment left above a drifted
    `platform_ref: v0.1.99` line satisfied `must_match` on the comment alone,
    and `platform_ref: "v0.1.104"` (quoted) or `platform_ref:  v0.1.104`
    (extra space) defeated the regex's exact literal spacing even though both
    are the correct value. This instead composes the tree (`yaml.compose`,
    the same pass `_mapping_value_nodes` uses) and compares each leaf node's
    own parsed `.value` against `tag` lexically — a comment is not a node,
    and YAML quoting/whitespace around a scalar is not part of its value.

    `min_refs`, when given, restores the presence half a `must_match` regex
    used to supply for free: without it, this check asserts only "every
    platform ref FOUND is on the tag", which passes vacuously if the ref was
    deleted, routed around (a local `./` swap), or never there in the first
    place. `min_refs` counts platform `uses:` value nodes only — not
    `platform_ref:` nodes, which are a separate leg entirely — so it stays a
    structural count, not a `files_unchanged`-style presence guard: an
    edited-but-still-correct file with the same ref count still passes. The
    count is of DISTINCT `(file, line)` locations, not of node visits: an
    anchored `uses:` value referenced again elsewhere via a YAML alias
    (`*x`) is the same physical ref and must not inflate the count — see
    the de-duplication note below, which applies here too.

    The count can also undershoot for a reason that has nothing to do with
    how many platform refs exist: a file that fails to parse, or one with no
    content at all, contributes zero ref nodes and silently lowers the
    count exactly as a genuinely deleted ref would — the detail names only
    the resulting number, not which file (if any) could not be read.

    The prefix match is case-folded on both sides: a GitHub `owner/repo`
    path is case-insensitive, so `example-adopter/example-site-platform/...` is the same
    cross-repo reference as `example-adopter/example-site-platform/...` and must not go
    unrecognised (and uncounted) merely by casing.

    An anchored/aliased `platform_ref:` (or `uses:`) is reported once, at the
    anchor's own line, not once per alias occurrence: `yaml.compose` resolves
    an alias to the SAME Node object as its anchor, so a value used via two
    aliases is visited twice but is one problem, not two — the same
    consequence `pin_comment_absent` documents for a trailing comment on an
    alias's own line. `bad` is de-duplicated before being joined for this
    reason.

    A `platform_ref:` key can also bind to a MappingNode rather than a
    scalar — a composite action DECLARING an input named `platform_ref`
    (`inputs: {platform_ref: {description: ..., required: true}}`) uses the
    same key for something that is not a version literal at all. Only a
    `yaml.ScalarNode` bound to that key is a value to compare; anything else
    is skipped rather than compared (and never counted — only `uses:` nodes
    feed `min_refs`).
    """
    import yaml
    if not platform_prefix or not tag:
        return (False, "platform_prefix/tag not configured")
    bad = []
    ref_locations: set[tuple[str, int]] = set()
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            try:
                doc = yaml.compose(text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                continue  # yaml_parses reports this; nothing to check here
            if doc is None:
                continue
            rel = os.path.relpath(path, workspace)
            for value_node in _uses_value_nodes(doc):
                ref = value_node.value
                if (not isinstance(ref, str)
                        or platform_prefix.casefold() not in ref.casefold()):
                    continue
                # Keyed by (rel, line) — the PHYSICAL location the docstring
                # above defines, not id(value_node): id() would also
                # collapse an alias correctly (compose() resolves it to the
                # same object as its anchor) but is otherwise just object
                # identity, not "how many places in this file" — and two
                # DISTINCT flow-style refs sharing one source line (e.g.
                # `{uses: a@v1}, {uses: b@v1}` on a single line) would count
                # as 2 under id() but only 1 here. That undercount fails
                # CLOSED, not open: with the gate action deleted, deploy.yml
                # alone must then supply 2 distinct locations to clear
                # min_refs, so collapsing two onto one line still correctly
                # trips the count; with the gate action intact, the
                # collapsed line plus the gate's own location still sums to
                # 2 and correctly passes.
                ref_locations.add((rel, value_node.start_mark.line))
                expected = ref.rsplit("@", 1)[0] + "@" + tag
                if ref != expected:
                    lineno = value_node.start_mark.line + 1
                    bad.append(f"{rel}:{lineno} uses: {ref} (expected @{tag})")
            for value_node in _mapping_value_nodes(doc, "platform_ref"):
                if not isinstance(value_node, yaml.ScalarNode):
                    continue
                if value_node.value != tag:
                    lineno = value_node.start_mark.line + 1
                    bad.append(f"{rel}:{lineno} platform_ref: {value_node.value} "
                              f"(expected {tag})")
    ref_count = len(ref_locations)
    if min_refs is not None and ref_count < min_refs:
        bad.append(f"only {ref_count} platform uses: ref(s) found across "
                  f"{patterns} (expected at least {min_refs})")
    bad = list(dict.fromkeys(bad))
    return (not bad, f"every platform ref pinned to {tag}" if not bad
            else "; ".join(bad))


# Any table-shaped row, of ANY cell count — deliberately not anchored to
# exactly 3 cells (nor to a 40-hex last cell), so a row with the wrong
# number of cells, or a malformed (e.g. shortened) sha, is still recognised
# as a row rather than silently failing to match at all. Cell count and the
# sha cell are both validated separately in _load_pins_reference, so either
# kind of malformed row is reported instead of just vanishing. This is
# deliberately loose enough to also classify a 1-cell line like `| note |`
# as a "row" (it has a leading and trailing `|` and nothing else); that
# never bites in practice because PINS.md is policed by `files_unchanged`
# (byte-identical to the seed), so no malformed row of any shape can be
# introduced into it by an audited run.
PINS_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
# The markdown alignment row under a table header (`|---|---|---|`, optional
# `:` for alignment) — a table-shaped row with no meaningful cell content,
# never a pin.
PINS_SEPARATOR_RE = re.compile(r"^\|[\s:-]+\|[\s:-]+\|[\s:-]+\|\s*$")


def _load_pins_reference(workspace: str, reference: str) -> tuple[dict[str, str], list[str]]:
    """({action: sha}, [malformed row descriptions]), read lexically off a
    PINS.md-shaped markdown table.

    The table's action-name and SHA cells are read as literal text — this
    never decides code SHAPE, so a plain line regex over the reference
    file's own leaf content is the right tool, unlike locating a `uses:`
    node in a workflow (which _uses_value_nodes does via a real YAML parse).

    A table-shaped row whose sha cell is not a valid 40-hex value, OR whose
    cell count isn't exactly 3 (a dropped Tag column, a stray extra column),
    is reported as malformed rather than silently dropped: `PINS_TABLE_ROW_RE`
    used to require BOTH the sha cell to already be valid hex AND exactly 3
    cells to match at all, so either kind of malformed row just failed to
    match and the action vanished from the requirement set entirely —
    indistinguishable from the action never having been listed. The header
    row (`| Action | Tag | SHA |`) and the markdown alignment row beneath it
    are table-shaped too, so both are recognised and skipped explicitly
    rather than by accident.
    """
    try:
        with open(os.path.join(workspace, reference), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}, []
    out: dict[str, str] = {}
    malformed = []
    for line in text.splitlines():
        if PINS_SEPARATOR_RE.match(line):
            continue
        m = PINS_TABLE_ROW_RE.match(line)
        if not m:
            continue
        cells = [cell.strip() for cell in m.group(1).split("|")]
        if len(cells) != 3:
            malformed.append(f"{reference}: row has {len(cells)} cell(s), "
                            f"expected 3: {line.strip()!r}")
            continue
        action, _tag_cell, sha = cells
        if action.casefold() == "action":
            continue  # header row
        if SHA_RE.match(sha):
            out[action] = sha
        else:
            malformed.append(f"{reference}: {action}: invalid sha {sha!r}")
    return out, malformed


def pins_match_reference(workspace: str, patterns: list[str],
                         reference: str | None = None,
                         platform_prefix: str | None = None) -> tuple[bool, str]:
    """Every action a reference file (PINS.md) lists is pinned, in the given
    files, to exactly the SHA that reference gives it — not merely a
    40-hex-character value that happens to be present.

    Binds to the seed's own offline source of truth rather than a
    fixture-hardcoded SHA list, so a hallucinated-but-well-formed SHA (one
    that satisfies `uses_refs_sha_pinned` by shape alone) still fails here.
    Also checks completeness: an action the reference lists but that has no
    `uses:` pin at all in the given files fails too, so a stub file that
    drops the real pin down to a comment naming the action does not pass
    vacuously.

    `uses:` values are located the same way `uses_refs_sha_pinned` and
    `pin_comment_absent` do — a real YAML parse via `_uses_value_nodes` —
    never a text regex deciding where the code's structure is.

    Compares with `.casefold()`: `SHA_RE` and `uses_refs_sha_pinned` both
    accept uppercase hex, so an all-uppercase-but-otherwise-correct audit
    must not fail here on case alone.

    The binding is a two-way closure, not a one-way whitelist: as well as
    every PINS.md action being correctly pinned, every remote `uses:` found
    in the given files must itself have a PINS.md row — otherwise a newly
    ADDED third-party action PINS.md never named, however well-formed its
    SHA, would score a perfect run simply by being absent from the table
    this check otherwise iterates.

    `platform_prefix`, when given, excludes a cross-repo reference to this
    account's own platform repo from that closure: PINS.md is a third-party
    pin ledger, and a platform ref staying on its release tag is
    `platform_refs_on_tag`'s business, checked separately (with its own
    `min_refs` presence guard) over its own `paths` — a fixture configures
    those as a superset of this check's, not the identical file set, so a
    platform ref that lands in one of THIS check's files is still caught
    there too. Without the exclusion, an agent that reorganises which file
    carries the platform ref (e.g. consolidating a job into the file this
    check scans) would be failed here too, for a carve-out this check does
    not police.

    A malformed row in the reference file itself (a table-shaped row whose
    sha cell isn't valid 40-hex) fails the check directly rather than
    silently dropping that action from the requirement set — see
    `_load_pins_reference`.
    """
    import yaml
    if not reference:
        return (False, "no reference file configured")
    pins, malformed = _load_pins_reference(workspace, reference)
    if not pins and not malformed:
        return (False, f"{reference}: no pin table rows found")
    found: dict[str, list[tuple[str, int, str]]] = {action: [] for action in pins}
    undeclared = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            try:
                doc = yaml.compose(text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                continue  # yaml_parses reports this; nothing to check here
            if doc is None:
                continue
            rel = os.path.relpath(path, workspace)
            for value_node in _uses_value_nodes(doc):
                ref = value_node.value
                if not isinstance(ref, str) or not _is_remote_action(ref):
                    continue
                if (platform_prefix
                        and platform_prefix.casefold() in ref.casefold()):
                    continue  # platform_refs_on_tag's business, not ours
                action, _, ref_val = ref.partition("@")
                lineno = value_node.start_mark.line + 1
                if action in found:
                    found[action].append((rel, lineno, ref_val))
                else:
                    undeclared.append(f"{rel}:{lineno} {action}: "
                                      f"not listed in {reference}")
    problems = list(malformed) + undeclared
    for action, expected_sha in pins.items():
        refs = found[action]
        if not refs:
            problems.append(f"{action}: no `uses:` pin found "
                            f"({reference} gives {expected_sha})")
            continue
        for rel, lineno, ref_val in refs:
            if ref_val.casefold() != expected_sha.casefold():
                problems.append(f"{rel}:{lineno} {action}@{ref_val} "
                                f"({reference} gives {expected_sha})")
    return (not problems, f"every {reference} pin present and correct"
            if not problems else "; ".join(problems))


def yaml_parses(workspace: str, patterns: list[str]) -> tuple[bool, str]:
    import yaml
    bad = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    yaml.safe_load(f)
            except yaml.YAMLError as e:
                bad.append(f"{os.path.relpath(path, workspace)}: {e}")
    return (not bad, "all workflows parse" if not bad else "; ".join(bad))


_FRONT_MATTER_UNSET = object()


def front_matter_has(workspace: str, patterns: list[str],
                     equals=_FRONT_MATTER_UNSET,
                     nonempty_strings=_FRONT_MATTER_UNSET) -> tuple[bool, str]:
    """Check only a bounded YAML front-matter mapping in named local files.

    One initial UTF-8 BOM is allowed. Delimiters are exact `---` lines
    (LF/CRLF; closing at EOF is allowed). The body is never decoded or
    parsed. Duplicate authored root keys are rejected before SafeLoader's
    merge handling; ordinary merge defaults with explicit overrides work.
    """
    import yaml

    if not isinstance(patterns, list) or not patterns:
        return (False, "front_matter_has: a nonempty paths list is required")
    if (equals is not _FRONT_MATTER_UNSET
            and (not isinstance(equals, dict)
                 or any(not isinstance(key, str) for key in equals))):
        return (False, "front_matter_has: equals must be a mapping with string keys")
    if (nonempty_strings is not _FRONT_MATTER_UNSET
            and (not isinstance(nonempty_strings, list)
                 or any(not isinstance(key, str) for key in nonempty_strings))):
        return (False, "front_matter_has: nonempty_strings must be a list of strings")
    equals = {} if equals is _FRONT_MATTER_UNSET else equals
    nonempty_strings = [] if nonempty_strings is _FRONT_MATTER_UNSET else nonempty_strings

    header_limit, depth_limit, node_limit = 65536, 64, 4096

    class BoundedSafeLoader(yaml.SafeLoader):
        def __init__(self, stream):
            super().__init__(stream)
            self.compose_depth = 0
            self.compose_count = 0

        def compose_node(self, parent, index):
            self.compose_depth += 1
            self.compose_count += 1
            try:
                if self.compose_depth > depth_limit or self.compose_count > node_limit:
                    raise ValueError("YAML limits exceeded")
                return super().compose_node(parent, index)
            finally:
                self.compose_depth -= 1

    def bound_graph(node):
        # Count each alias occurrence's expanded subtree, rather than only
        # unique node identities. Run BEFORE construction/merge flattening.
        count = 0
        active = set()

        def visit(current, depth):
            nonlocal count
            count += 1
            if count > node_limit or depth > depth_limit or id(current) in active:
                raise ValueError("YAML limits exceeded")
            active.add(id(current))
            if isinstance(current, yaml.MappingNode):
                for key, value in current.value:
                    visit(key, depth + 1)
                    visit(value, depth + 1)
            elif isinstance(current, yaml.SequenceNode):
                for child in current.value:
                    visit(child, depth + 1)
            active.remove(id(current))

        visit(node, 1)

    def equal(actual, expected, depth=0):
        # Python considers True == 1, including nested keys and YAML sets.
        if type(actual) is not type(expected) or depth > depth_limit:
            return False
        if isinstance(actual, dict):
            return len(actual) == len(expected) and all(
                any(equal(key, other, depth + 1) and equal(value, expected[other], depth + 1)
                    for other in expected)
                for key, value in actual.items())
        if isinstance(actual, (list, tuple)):
            return len(actual) == len(expected) and all(
                equal(left, right, depth + 1) for left, right in zip(actual, expected))
        if isinstance(actual, (set, frozenset)):
            return len(actual) == len(expected) and all(
                any(equal(value, other, depth + 1) for other in expected) for value in actual)
        return actual == expected

    workspace_real = os.path.realpath(workspace)
    for rel in patterns:
        if (not isinstance(rel, str) or not rel or "\x00" in rel
                or os.path.isabs(rel) or ".." in Path(rel).parts
                or glob.has_magic(rel)):
            return (False, "front_matter_has: paths must be exact workspace-relative names")
        target = os.path.realpath(os.path.join(workspace, rel))
        if os.path.commonpath([workspace_real, target]) != workspace_real:
            return (False, "front_matter_has: path resolves outside the workspace")
        if not os.path.isfile(target):
            return (False, "front_matter_has: file not found")
        try:
            with open(target, "rb") as stream:
                opening = stream.readline(9)
                if opening.startswith(b"\xef\xbb\xbf"):
                    opening = opening[3:]
                if opening not in (b"---\n", b"---\r\n"):
                    return (False, "front_matter_has: missing or malformed opening delimiter")
                header = bytearray()
                while True:
                    line = stream.readline(header_limit - len(header) + 6)
                    if line in (b"---", b"---\n", b"---\r\n"):
                        break
                    if not line:
                        return (False, "front_matter_has: missing closing delimiter")
                    header.extend(line)
                    if len(header) > header_limit:
                        return (False, "front_matter_has: header exceeds 64 KiB")
            loader = BoundedSafeLoader(header.decode("utf-8"))
            try:
                node = loader.get_single_node()
                if not isinstance(node, yaml.MappingNode):
                    return (False, "front_matter_has: YAML root must be a mapping")
                bound_graph(node)
                keys = set()
                for key_node, _ in node.value:
                    # The merge tag has no ordinary scalar constructor.
                    if key_node.tag == "tag:yaml.org,2002:merge":
                        key = ("merge",)
                    else:
                        key = loader.construct_object(key_node, deep=True)
                    if key in keys:
                        return (False, "front_matter_has: duplicate top-level key")
                    keys.add(key)
                data = loader.construct_document(node)
                if not isinstance(data, dict):
                    return (False, "front_matter_has: YAML root must construct to a mapping")
            finally:
                loader.dispose()
        except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError):
            # Parser exceptions can contain header text. Never expose it.
            return (False, "front_matter_has: unreadable or invalid YAML header")
        if any(key not in data or not equal(data[key], value) for key, value in equals.items()):
            return (False, "front_matter_has: required value or type does not match")
        if any(key not in data or not isinstance(data[key], str) or not data[key].strip()
               for key in nonempty_strings):
            return (False, "front_matter_has: required nonempty string is missing or blank")
    return (True, "front matter satisfies all constraints")


def non_remote_refs_unchanged(workspace: str, patterns: list[str],
                              seed: str | None = None) -> tuple[bool, str]:
    """Local (./) and docker:// refs must match the seed workspace exactly."""
    if seed is None:
        return (False, "seed workspace not provided")
    def non_remote(ws):
        return sorted((f, ref) for f, _, ref, _ in _workflow_uses(ws, patterns)
                      if not _is_remote_action(ref))
    before, after = non_remote(seed), non_remote(workspace)
    return (before == after, "local/docker refs unchanged" if before == after
            else f"changed: seed={before} result={after}")


# --------------------------------------------------------------------------
# Workflow path-filtering checks
# --------------------------------------------------------------------------

# Only `pull_request` and `push` honour path filters. GitHub ignores
# paths/paths-ignore on `schedule`, `workflow_dispatch`, `issues`,
# `issue_comment` and friends, so a filter under those reads as a working
# guard while filtering nothing.
PATH_FILTERED_EVENTS = ("pull_request", "push")
# Where a repo keeps its branch-protection ruleset as code; the source of
# truth for which status checks are required (see the workflow-path-audit
# skill's "Required-check + path-filter trap").
RULESET_REL_PATH = ".github/rulesets/main.json"
# An `if:` that gates work on a computed salience decision, e.g.
# `steps.salient.outputs.run == 'true'` or `needs.detect.outputs.run == 'true'`.
GATE_REF_RE = re.compile(r"\b(?:steps|needs)\.[\w-]+\.outputs\.[\w-]+")


class _DuplicateWorkflowKeyError(ValueError):
    """A matched workflow authors the same mapping key more than once."""


def _reject_duplicate_workflow_keys(loader, node, rel: str) -> None:
    """Inspect authored keys before SafeLoader flattens merge defaults.

    Alias references share node identities; visit each once, including
    recursive aliases. Only scalar keys can construct to hashable keys in
    SafeLoader; complex keys retain the loader's ordinary parse failure.
    """
    import yaml
    merge_key = object()
    seen = set()
    pending = [node]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, yaml.MappingNode):
            keys = set()
            for key_node, value_node in current.value:
                pending.extend((key_node, value_node))
                if not isinstance(key_node, yaml.ScalarNode):
                    continue
                if key_node.tag == "tag:yaml.org,2002:merge":
                    key = merge_key
                elif key_node.tag == "tag:yaml.org,2002:value":
                    # SafeLoader treats the plain '=' mapping key as a
                    # string during flatten_mapping, after this walk.
                    key = key_node.value
                else:
                    try:
                        key = loader.construct_object(key_node, deep=True)
                    except yaml.YAMLError:
                        # An unsupported key tag must not hide duplicates
                        # elsewhere. Construction still rejects the tag
                        # after this authored-node walk finishes.
                        key = (key_node.tag, key_node.value)
                if key in keys:
                    raise _DuplicateWorkflowKeyError(f"{rel}: duplicate YAML mapping key")
                keys.add(key)
        elif isinstance(current, yaml.SequenceNode):
            pending.extend(current.value)


def _load_workflows(workspace: str, patterns: list[str], *,
                    reject_duplicate_keys: bool = False) -> list[tuple[str, dict | None]]:
    """[(relpath, parsed doc or None if it doesn't parse)] for every match.

    Paths are returned with forward slashes so fixtures can name them
    platform-independently. Duplicate validation is opt-in so unrelated
    objective checks retain their existing SafeLoader behavior.
    """
    import yaml
    out = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            rel = os.path.relpath(path, workspace).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as f:
                    if reject_duplicate_keys:
                        loader = yaml.SafeLoader(f)
                        try:
                            node = loader.get_single_node()
                            _reject_duplicate_workflow_keys(loader, node, rel)
                            doc = loader.construct_document(node) if node is not None else None
                        finally:
                            loader.dispose()
                    else:
                        doc = yaml.safe_load(f)
            except (yaml.YAMLError, OSError, UnicodeDecodeError):
                doc = None
            out.append((rel, doc if isinstance(doc, dict) else None))
    return out


def _on_events(doc: dict) -> dict:
    """The `on:` block as {event_name: config-or-None}.

    Handles all three spellings (`on: push`, `on: [push, pull_request]`,
    `on: {push: {...}}`) and the YAML 1.1 trap that makes PyYAML resolve the
    bare key `on` to the boolean True — the key is looked up under both.
    """
    raw = doc.get("on")
    if raw is None:
        raw = doc.get(True)
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {e: None for e in raw if isinstance(e, str)}
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if isinstance(k, str)}
    return {}


def _filter_lists(event_cfg) -> tuple[list | None, list | None]:
    """(paths, paths-ignore) for one event's config; None where absent."""
    def as_list(value):
        if isinstance(value, str):
            return [value]
        return value if isinstance(value, list) else None
    if not isinstance(event_cfg, dict):
        return None, None
    return as_list(event_cfg.get("paths")), as_list(event_cfg.get("paths-ignore"))


def _glob_to_regex(pattern: str) -> re.Pattern:
    """GitHub filter-pattern glob -> anchored regex.

    Per GitHub's filter-pattern cheat sheet: `*` matches any characters except
    `/`, `**` matches any characters including `/`, `?` matches one non-`/`
    character. Everything else is literal — deliberately faithful, so a filter
    that would not actually match on GitHub does not pass here either.
    """
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i + 1:i + 2] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _pattern_matches(patterns: list[str], path: str) -> bool:
    """Does `path` match this filter list, honouring `!` negation and order?

    GitHub evaluates the list in order and the last matching pattern wins, so
    a negative pattern after a positive one excludes and vice versa.
    """
    matched = False
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        negated = pattern.startswith("!")
        body = pattern[1:] if negated else pattern
        if _glob_to_regex(body).match(path):
            matched = not negated
    return matched


def _event_triggers(event_cfg, changeset: list[str]) -> bool:
    """Would this one event fire for `changeset`?

    `paths`: fires when at least one changed file matches. `paths-ignore`:
    fires unless every changed file matches. Neither: always fires.
    """
    paths, ignore = _filter_lists(event_cfg)
    if paths is not None:
        return any(_pattern_matches(paths, f) for f in changeset)
    if ignore is not None:
        return any(not _pattern_matches(ignore, f) for f in changeset)
    return True


def _workflow_triggers(doc: dict, changeset: list[str]) -> bool:
    """Would this workflow fire for `changeset` on any path-filtered event?"""
    events = _on_events(doc)
    relevant = [cfg for name, cfg in events.items() if name in PATH_FILTERED_EVENTS]
    return any(_event_triggers(cfg, changeset) for cfg in relevant)


def changeset_triggers(workspace: str, patterns: list[str], *,
                       changeset: list[str] | None = None,
                       expect_triggered: list[str] | None = None,
                       expect_skipped: list[str] | None = None) -> tuple[bool, str]:
    """Replay one changeset through every workflow's filters and compare.

    Decides purely from the workflow files, using GitHub's own path-matching
    semantics: no network, no git history, no wall clock. Workflows not named
    in either expectation list are ignored, so an agent may add a workflow;
    a named workflow that is missing or unparseable is a failure.
    """
    changeset = changeset or []
    expected = {p: True for p in (expect_triggered or [])}
    expected.update({p: False for p in (expect_skipped or [])})
    found = dict(_load_workflows(workspace, patterns))

    problems = []
    for rel in sorted(expected):
        want = expected[rel]
        if rel not in found:
            problems.append(f"{rel}: expected in the workspace, not found")
            continue
        doc = found[rel]
        if doc is None:
            problems.append(f"{rel}: does not parse as a YAML mapping")
            continue
        got = _workflow_triggers(doc, changeset)
        if got != want:
            problems.append(f"{rel}: expected {'triggered' if want else 'skipped'}, "
                            f"would be {'triggered' if got else 'skipped'}")
    changed = ", ".join(changeset) or "(nothing)"
    return (not problems,
            f"changing {changed} routes as expected across {len(expected)} workflow(s)"
            if not problems else f"changing {changed}: " + "; ".join(problems))


def _required_contexts(workspace: str) -> set[str]:
    """Required status-check contexts from the repo's ruleset-as-code."""
    try:
        with open(os.path.join(workspace, RULESET_REL_PATH), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    out = set()
    for rule in data.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        params = rule.get("parameters")
        checks = params.get("required_status_checks") if isinstance(params, dict) else None
        for check in checks or []:
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                out.add(check["context"])
    return out


def _job_identifiers(doc: dict) -> set[str]:
    """Every name a job in this workflow can appear under as a check context."""
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return set()
    names = set()
    for job_id, job in jobs.items():
        names.add(str(job_id))
        if isinstance(job, dict) and isinstance(job.get("name"), str):
            names.add(job["name"])
    return names


def _if_conditions(node) -> list[str]:
    """Every `if:` expression anywhere in the document, as strings."""
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "if" and isinstance(value, (str, bool)):
                out.append(str(value))
            else:
                out.extend(_if_conditions(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_if_conditions(item))
    return out


def _declares_output_producer(doc: dict) -> bool:
    """Is there a step with an `id:` or a job declaring `outputs:` to gate on?"""
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return False
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("outputs"), dict):
            return True
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("id"):
                return True
    return False


def _has_salience_gate(doc: dict) -> bool:
    """The always-run + early-skip shape: a computed output gating real work."""
    return (_declares_output_producer(doc)
            and any(GATE_REF_RE.search(cond) for cond in _if_conditions(doc)))


def required_checks_early_skip(workspace: str, patterns: list[str]) -> tuple[bool, str]:
    """Workflows carrying a required status check must always fire, and gate inside.

    A workflow-level paths/paths-ignore on a required check is the trap:
    GitHub blocks the merge on a *missing* check, so a filtered-out required
    check deadlocks every pull request. The prescribed shape instead lets the
    job always run and moves the salience decision inside it.
    """
    contexts = _required_contexts(workspace)
    if not contexts:
        return (True, f"no required status checks declared in {RULESET_REL_PATH}")

    checked, problems = [], []
    for rel, doc in _load_workflows(workspace, patterns):
        if doc is None:
            continue
        required_here = sorted(_job_identifiers(doc) & contexts)
        if not required_here:
            continue
        checked.append(rel)
        for name, cfg in _on_events(doc).items():
            if name not in PATH_FILTERED_EVENTS:
                continue
            paths, ignore = _filter_lists(cfg)
            if paths is not None or ignore is not None:
                problems.append(f"{rel}: `on.{name}` carries a workflow-level path "
                                f"filter, so required check(s) "
                                f"{', '.join(required_here)} can go missing")
        if not _has_salience_gate(doc):
            problems.append(f"{rel}: no early-skip gate — nothing computes a "
                            f"salience output that an `if:` gates the real work on")
    if not checked:
        problems.append("no workflow declares a job matching the required "
                        f"context(s): {', '.join(sorted(contexts))}")
    return (not problems,
            f"required check(s) always fire and gate internally ({', '.join(checked)})"
            if not problems else "; ".join(problems))


def event_only_workflows_unfiltered(workspace: str, patterns: list[str]) -> tuple[bool, str]:
    """Workflows without a pull_request/push trigger must carry no path filter."""
    problems, checked = [], []
    for rel, doc in _load_workflows(workspace, patterns):
        if doc is None:
            continue
        events = _on_events(doc)
        if any(name in PATH_FILTERED_EVENTS for name in events):
            continue
        checked.append(rel)
        for name, cfg in events.items():
            paths, ignore = _filter_lists(cfg)
            if paths is not None or ignore is not None:
                problems.append(f"{rel}: `on.{name}` carries a path filter, "
                                f"which GitHub ignores for that event")
    return (not problems,
            f"event-only workflows carry no path filters ({', '.join(checked) or 'none present'})"
            if not problems else "; ".join(problems))


def files_unchanged(workspace: str, patterns: list[str],
                    seed: str | None = None, by: str = "path") -> tuple[bool, str]:
    """The matched files' content must survive, relative to the seed workspace.

    `by="path"` (default): every matched file must be byte-identical to the
    seed file at the SAME relative path — a rename, even of untouched
    content, is a failure. This is what every existing fixture wants: a
    ruleset or a fake binary that must not be touched OR moved.

    `by="digest"`: compares the MULTISET of sha256 content digests across
    all matched files, ignoring which filename holds which content. A file
    that got renamed (content preserved, path changed) still passes; a file
    whose content changed, or one that vanished outright (e.g. a naive
    rename that clobbered another file instead of disambiguating), does not
    — its digest is missing from the resulting bag. This is the mode a
    rename task needs: correct behaviour is EXPECTED to move content to new
    paths, so path identity is not the property to protect, content
    survival is.
    """
    if seed is None:
        return (False, "seed workspace not provided")
    if by not in ("path", "digest"):
        return (False, f"files_unchanged: unknown by={by!r}, expected 'path' or 'digest'")

    def snapshot(root):
        out = {}
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(root, pattern))):
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                try:
                    with open(path, "rb") as f:
                        out[rel] = f.read()
                except OSError as exc:
                    out[rel] = f"<unreadable: {exc}>".encode()
        return out

    before, after = snapshot(seed), snapshot(workspace)

    if by == "digest":
        def digests(snapshot_dict):
            return [hashlib.sha256(data).hexdigest() for data in snapshot_dict.values()]
        before_counts, after_counts = Counter(digests(before)), Counter(digests(after))
        if before_counts == after_counts:
            return (True, f"content preserved by digest ({sum(before_counts.values())} file(s))")
        lost = before_counts - after_counts
        gained = after_counts - before_counts
        problems = []
        if lost:
            problems.append(f"{sum(lost.values())} file(s)' content missing from the result")
        if gained:
            problems.append(f"{sum(gained.values())} file(s) with unexpected/new content in the result")
        return (False, "; ".join(problems) if problems else "digest multiset changed")

    problems = [f"{rel}: removed" for rel in sorted(set(before) - set(after))]
    problems += [f"{rel}: added" for rel in sorted(set(after) - set(before))]
    problems += [f"{rel}: modified" for rel in sorted(set(before) & set(after))
                 if before[rel] != after[rel]]
    return (not problems,
            f"unchanged ({', '.join(sorted(before)) or 'no files matched'})"
            if not problems else "; ".join(problems))


def _capped_join(names: list[str], limit: int = 40) -> str:
    """", "-join, but a huge listing doesn't dump every name into `detail`."""
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])}, and {len(names) - limit} more"


def dir_listing_matches(workspace: str, patterns: list[str], expected: list[str] | None = None,
                        expected_file: str | None = None, seed: str | None = None,
                        ignore: list[str] | None = None) -> tuple[bool, str]:
    """The sorted immediate-entry listing of one directory must equal an
    expected list of names.

    `patterns` names exactly one directory, relative to the workspace (e.g.
    `["inbox"]`) — this is not a glob, unlike every other check here; it is
    listed once and compared whole, which is what a rename task needs: the
    property under test is which NAMES exist afterward, not any one file's
    content.

    The expected names come from exactly one of:
      - `expected`: an inline list in the fixture.
      - `expected_file`: a path read from the pristine `seed` directory (never
        the runtime workspace, so a run that deletes or edits its own copy of
        that file cannot change what "expected" means) — one name per
        non-blank line.

    `ignore`: optional list of `fnmatch` glob patterns (compared as data,
    never a regex) for entry names to drop from the listing before
    comparing — for a name that is inherently non-deterministic (e.g. a
    wall-clock-stamped log file) and so cannot itself appear in `expected`.
    Anything NOT matching an ignore pattern is still compared normally, so
    this narrows what is excused, not what is checked.
    """
    if len(patterns) != 1:
        return (False, f"dir_listing_matches takes exactly one directory, got {patterns!r}")
    directory = patterns[0]
    if os.path.isabs(directory):
        return (False, f"{directory}: must be a workspace-relative path, not absolute")
    workspace_real = os.path.realpath(workspace)
    target = os.path.join(workspace, directory)
    target_real = os.path.realpath(target)
    if os.path.commonpath([workspace_real, target_real]) != workspace_real:
        return (False, f"{directory}: resolves outside the workspace")
    if not os.path.isdir(target):
        return (False, f"{directory}: not a directory")
    actual = sorted(os.listdir(target))
    if ignore:
        actual = [name for name in actual
                 if not any(fnmatch.fnmatch(name, pat) for pat in ignore)]

    if expected is not None and expected_file is not None:
        return (False, "dir_listing_matches: give expected or expected_file, not both")
    if expected_file is not None:
        if seed is None:
            return (False, "seed workspace not provided")
        try:
            with open(os.path.join(seed, expected_file), encoding="utf-8") as f:
                expected = [line.strip() for line in f if line.strip()]
        except (OSError, UnicodeDecodeError) as exc:
            return (False, f"{expected_file}: {exc}")
    elif expected is None:
        return (False, "dir_listing_matches: expected or expected_file is required")
    if not isinstance(expected, list):
        return (False, f"dir_listing_matches: expected must be a list, "
                       f"got {type(expected).__name__}")
    expected = sorted(expected)

    if actual == expected:
        return (True, f"{directory}/ matches the expected listing ({len(actual)} entries)")
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    problems = []
    if missing:
        problems.append("missing: " + _capped_join(missing))
    if extra:
        problems.append("unexpected: " + _capped_join(extra))
    return (False, "; ".join(problems) if problems
            else f"listing differs: {_capped_join(actual)} != {_capped_join(expected)}")


def file_digests_match(workspace: str, patterns: list[str],
                       sha256: str | None = None) -> tuple[bool, str]:
    """Every path in `patterns` (workspace-relative, NOT a glob) must exist
    and its content must hash to the exact `sha256` hex digest given.

    Unlike `files_unchanged(by="digest")`, which compares a MULTISET of
    digests against the seed with no fixed identity, this pins one exact,
    known-in-advance digest to one or more named paths — "this specific
    document's bytes ended up at this specific final name." More than one
    path is for a disambiguated pair that is expected to still be
    byte-identical copies of the SAME document (e.g. `foo.pdf` and
    `foo (2).pdf`), not two different documents.
    """
    if not patterns:
        return (False, "file_digests_match: at least one path is required")
    if not sha256:
        return (False, "file_digests_match: sha256 is required")
    problems = []
    for rel in patterns:
        full = os.path.join(workspace, rel)
        if not os.path.isfile(full):
            problems.append(f"{rel}: not found")
            continue
        with open(full, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != sha256:
            problems.append(f"{rel}: digest {digest} != expected {sha256}")
    return (not problems, f"{', '.join(patterns)}: digest matches" if not problems
            else "; ".join(problems))


def _read_matched(workspace: str, patterns: list[str]) -> tuple[str, list[str]]:
    """Concatenate every file the patterns match; return (text, relative paths).

    A matched file that can't actually be opened (permission bits an agent
    left restrictive, e.g.) is skipped rather than raising — an unreadable
    file contributes nothing to the concatenated text, and `require_present`'s
    existing absent/empty branch is what names the check as failed, not an
    uncaught `OSError`/`PermissionError` crashing the whole scorer.
    """
    chunks, names = [], []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(workspace, pattern))):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            names.append(os.path.relpath(path, workspace).replace(os.sep, "/"))
            chunks.append(text)
    return "\n".join(chunks), names


# --------------------------------------------------------------------------
# Provenance: what the seed says is not what the agent wrote
# --------------------------------------------------------------------------
#
# A reply that pastes the material back is not being specific, greeting
# anyone or hedging when the thing a check looks for appears only in what it
# pasted. Two earlier attempts to decide that from MARKUP both failed, in
# opposite directions:
#
# - a line scanner (`^ {0,3}>` plus a fence tracker) cannot see an indented
#   code block, an HTML `<blockquote>`/`<details>`/`<pre>`, a lazy
#   continuation, or a verbatim paste carrying no marker at all;
# - a real Markdown parser sees all of those and still cannot tell a quoted
#   seed from a deliverable the agent CHOSE to present as a blockquote —
#   which is what every calibration example in `my-writing-style`'s
#   SKILL.md is, so the arm that read the skill is the arm most likely to
#   hand its reply back inside one.
#
# What separates the two is not markup. It is PROVENANCE: the seed is
# committed material this harness can read, so the material that came from
# it can be named exactly, in any shape it is pasted back in — and
# everything else is the agent's writing, however it chose to format it.
#
# The UNIT of provenance is the SENTENCE, and the decision is taken after
# hard wrapping is undone. It used to be the LINE, and a line is not a unit
# of anything: the same words re-broken across different lines were a
# different set of lines, so a paste re-wrapped, re-selected, punctuated,
# tabled or salted with invisibles walked past the scan; and a genuine
# paragraph hard-wrapped at 72 lost two of ITS OWN lines because a wrap
# happened to land where the seed's line ends did.

# The floor on a piece of seed material. A sentence shorter than this the
# agent could plausibly have written itself: "Thanks," and a bare name are
# in every reply and in some seeds, and claiming them for the seed would
# take the agent's own sign-off away from it. Inside a MARKED quotation
# there is no floor — the markup already says the block is a quotation, so
# a short sentence in it needs only to be the seed's.
_SEED_MATERIAL_FLOOR = 24

# The floor on a piece of seed material matched WHOLE — a sentence of the
# seed's, reproduced end to end. Lower than the run floor on purpose: an
# exact sentence match is far stronger evidence of a paste than the same
# number of characters appearing as a fragment of a longer seed sentence,
# which prose about the same subject arrives at by accident. Still well
# above "Thanks," and a bare name, which is what the floor exists for.
_SEED_SENTENCE_FLOOR = 12

# A blockquote marker: any indent, a `>`, and any run of spaces after it.
# Applied repeatedly, so `> > ` and `>  ` unwrap too. Unlimited indent on
# purpose — an indented deliverable is still the deliverable, and one to
# three spaces (or a tab) in front of a reply used to cost it the checks
# its own opening satisfies.
_QUOTE_MARKER_RE = re.compile(r"^[^\S\n]*>[^\S\n]*")
_FENCE_RE = re.compile(r"^[^\S\n]*(`{3,}|~{3,})")
_LIST_MARKER_RE = re.compile(r"^[^\S\n]*(?:[-*+•]|\d+[.)])[^\S\n]+")

# The tags that are WRAPPER and nothing else, and only when they carry no
# attributes. A bare `<blockquote>`, `</details>` or `<pre>` is markup the
# agent wrapped around something; `<span title="we can leverage this">`,
# `<img alt="...synergy...">` and `<!-- circle back -->` are text the agent
# WROTE, attribute values included, and each of those used to switch the
# avoid-list ban off by the simple trick of being a line with a tag on it.
# A tag outside this set, or one carrying an attribute, stays exactly as it
# arrived — including mid-line, where a general tag strip once let
# `I<leverage synergy robust> was looking...` normalise onto a seed line and
# be dropped whole.
_WRAPPER_TAGS = ("blockquote", "details", "summary", "pre", "code", "div",
                 "p", "br")
_BARE_WRAPPER_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_WRAPPER_TAGS) + r")\s*/?>", re.I)
# What a bare wrapper tag leaves behind, and there are TWO answers because
# a tag mid-word has two honest readings: a space, so `x<code>leverage` is
# `x leverage` and the ban on `leverage` fires; and nothing, so
# `lever<br>age` is `leverage` and the ban fires on that too. Neither
# reading catches both shapes, so `transcript_matches` scores the text
# under each and `_text_matches_any` forbids a banned pattern in ANY of
# them. Named here rather than written inline so the mutation "score only
# one reading" is a thing a test can DO — see
# `TestIssue81.test_both_tag_readings_are_load_bearing`. Changing the
# default of `_strip_wrapper` alone is not that mutation: the two readings
# are passed explicitly, so the default only reaches the seed index.
_TAG_READINGS = (" ", "")
# The block wrappers that bracket a quotation. `<summary>` is the LABEL on a
# `<details>` rather than part of what it discloses, but the label's TEXT is
# still the agent's, so it is not a delimiter: only its tags come off.
_HTML_BLOCK_TAG = r"blockquote|details|pre"
_HTML_BLOCK_OPEN_RE = re.compile(rf"^\s*<({_HTML_BLOCK_TAG})\s*>\s*$", re.I)
_HTML_BLOCK_CLOSE_RE = re.compile(rf"^\s*</({_HTML_BLOCK_TAG})\s*>\s*$", re.I)

# A Markdown table's alignment row: pipes, dashes and colons only. It is the
# table's own scaffolding, like a fence delimiter, and carries no words.
_TABLE_RULE_RE = re.compile(r"^[^\S\n]*\|[-:|\s]*\|[^\S\n]*$")
# A table ROW, which is a deliberate line rather than a wrapped one. Shared
# with `wrapping.TABLE_ROW_RE` so the two readings cannot drift.
_TABLE_ROW_RE = wrapping.TABLE_ROW_RE

# Characters that take up no width and can hide a banned term inside a word,
# or break a paste into pieces the seed index cannot match. `lever­age` and
# `deep​ dive` read to the operator as the banned terms and are scored as
# them, and one U+2062 mid-word no longer buys a paste its provenance back.
#
# This used to be a hand-written character class, and an enumeration is the
# wrong shape for "everything invisible": it named 20 of Unicode's 163 `Cf`
# code points and 20 of its 1,950 `Mn` ones, so the bidi overrides and
# isolates its own comment claimed, the TAG block, the supplementary
# variation selectors, the musical format controls, the interlinear
# annotation marks and the Hangul fillers all walked past it. The rule is a
# rule now — see `invisibles.fold`, which `judge` folds with too so a
# character cannot read as nothing to the judge and as something here.
_fold_invisibles = invisibles.fold

# CONTAINERS. A Markdown link `[text](dest)` and image `![alt](dest)`
# contribute only their text or alt: a destination is an address, not
# writing, and its tokens used to break the run a link's own text is. So did
# a footnote definition's marker. `[^1]: <one of her sentences>` and
# `[<one of her sentences>](https://example.com/x)` were both "composed"
# sentences the agent had written — on the strength of a URL.
#
# Deliberately narrow: only the two inline shapes and the definition marker,
# neither spanning a line. Everything else a bracket can mean — a
# reference-style link's label, an array subscript in prose, a bracketed
# aside — is words the agent chose and stays.
_LINK_RE = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
_FOOTNOTE_MARKER_RE = re.compile(r"^[^\S\n]*\[\^[^\]\n]+\]:[^\S\n]*")

# COVERAGE (design decision 3). The shortest run of consecutive words that
# counts as the seed's rather than as two writers reaching for the same
# phrase, and the fraction of a sentence that has to be inside such a run
# before the sentence is the seed's.
#
# SIX words, measured rather than assumed. Four was the starting value and
# it is too short: a genuine draft written FROM the seed reuses four- and
# five-word runs of it constantly and legitimately — "Staff Platform
# Engineer role at", "the shared deployment repository", "a clean Section
# 508 audit" — and at the shipped C = 0.75 the committed in-voice
# references, which are the fixtures' own proof that the checks are
# satisfiable by real writing, lose `cites-both-facts` at 63 of the 189
# wrap-column cells at four and 0 at five. (An earlier version of this
# comment put those two figures at 126 and 63 — real numbers, but measured
# at C = 0.60, the brief's starting value, not the C = 0.75 this module
# ships; re-measure through `seed_coverage_report` before trusting either
# one, this comment included.) Five is not the answer either, though: at
# five the genuine newly-claimable ceiling jumps to 0.9167
# (in-voice/self-appraisal-opening at column 38) against a paste floor of
# 0.9259 — a margin of 0.009, nowhere near the 0.1 this module requires
# (see _SEED_COVERAGE below). At six the ceiling drops back to 0.59 with
# the paste floor still at or above 0.93, margin to spare on both sides,
# and no paste shape moves. Six is also the shortest value at which the
# seed's own longest FACT phrases — an employer, a job title, a repo path,
# a date — cannot be a run on their own (the longest, "Northgate Bell
# Talent Group", is four words), though that alone would not have ruled
# out five; the margin above is what does.
_SEED_COVERAGE_RUN = 6

# 0.75, with the margins measured by the batteries in
# test/run_tests.py::TestIssue81 (`test_the_coverage_margins_hold`, and the
# GENUINE and PASTE tables in the PR body).
#
# The ceiling is measured over the sentences this rule could NEWLY claim —
# the ones (a) and (b) do not already call the seed's. It has to be: a
# genuine draft is ALLOWED to reproduce one of the seed's sentences end to
# end (core move 8's plain certifications listing has one natural phrasing
# and the background note carries it), rule (a) has always called that the
# seed's, and it costs no check. Counting those in would put the raw
# genuine maximum at 1.00 by construction and make any margin unreachable.
#
# Measured this round, R = 6: the highest-coverage sentence rule (c) could
# newly claim in any genuine draft — 16 drafts plus the three in-voice
# references at every wrap column from 38 to 100, 189 cells — is 0.59
# (`close-paraphrase` on recruiter-reply), and the lowest highest-coverage
# sentence of any paste shape that has to fail is 0.93
# (`two-clauses-joined` on proposal-bio; `sub-floor-bullets` measures 0 by
# construction — three-word fragments, none of them as long as the run —
# and is caught by the FLOOR rather than by coverage, so it is not in this
# population). So the floor sits 0.16 above the genuine ceiling and 0.18
# below the paste floor. The rule: both margins stay at 0.1 or better.
# If a future draft closes one, the answer is to report it, not to tune
# this number past the margin.
#
# Neither number is prose: `test_the_coverage_margins_hold` measures them
# through `seed_coverage_report` — the scorer's own pipeline — and pins
# them, and `test_the_coverage_constants_are_named_with_their_margins`
# asserts they are written here.
_SEED_COVERAGE = 0.75

# A sentence ends at `.`, `!`, `?`, `;` or `:` FOLLOWED BY WHITESPACE, or at
# the end of a line. The trailing-whitespace requirement is what keeps
# `dana.whitcombe@example.com`, `09:14:00` and `https://example.com` in one
# piece; the line boundary is what ends a list item and a table row, neither
# of which is punctuated.
#
# The colon is here because a LABEL is a sentence boundary and pretending
# otherwise hands a paste a way to dilute itself: `She wrote: ` and `My own
# note: ` in front of one of her sentences made a longer "sentence" whose
# coverage the two extra words dragged under the floor, and the sentence
# behind the label went back to being the agent's. The fixtures already
# read a colon this way — recruiter-reply's hedge check ends a sentence at
# `,;:` as well as at `.!?—`, because a greeting and its hedge are
# routinely one line.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:])[^\S\n]+")

# Everything that is not a letter, a digit or a space, dropped before two
# pieces of text are compared. Provenance is about WORDS: a paste that
# swapped every full stop for an exclamation mark, or ran the material
# through a Markdown table, is the same material.
_UNWORD_RE = re.compile(r"[^\w\s]|_", re.UNICODE)


class FixtureError(ValueError):
    """A fixture asks for something this scorer cannot honour as written."""


class SeedTooLarge(FixtureError):
    """A seed file is bigger than the provenance index will read.

    Named rather than silent: a truncated index answers "not the seed's" for
    every sentence past the cap, which reads as the agent having written the
    material it actually pasted.
    """


# The provenance index reads at most this much of any one seed file. A cap
# is needed because the index is rebuilt per check and holds each file's
# whole text casefolded; a MiB is far above any writing fixture's seed (the
# largest in this repo is under 2 KB) and far below a file worth holding.
# Past it the read RAISES rather than truncating — see `SeedTooLarge`.
_SEED_READ_CAP = 1 << 20

def _key(text: str) -> str:
    """`text` reduced to the words in it.

    Casefolded, every character that is not a letter, a digit or a space
    dropped, whitespace collapsed. Two pieces of text with the same key say
    the same thing: provenance is about WORDS, so a paste that swapped every
    full stop for an exclamation mark, ran the material through a Markdown
    table, or re-wrapped it at a different column is the same material.
    """
    return re.sub(r"\s+", " ", _UNWORD_RE.sub(" ", text.casefold())).strip()


def _sentences(line: str) -> list[str]:
    """One logical line split into the sentences it carries."""
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(line)
            if part.strip()]


def _strip_wrapper(line: str, tag_gap: str = " ") -> tuple[str, bool]:
    """(`line` with its wrapper removed, was it wrapper and nothing else?).

    Wrapper is leading whitespace, `>` runs with any number of spaces after
    them, list markers, a footnote definition's marker, a Markdown link's or
    image's CONTAINER (the brackets, the parentheses and the destination —
    the text or alt is writing and stays), and the bare tags in
    `_WRAPPER_TAGS`. Everything else is the agent's text and stays exactly
    as it arrived — an HTML comment, a tag outside that set, a tag carrying
    attributes, and the attribute values with it.

    `tag_gap` is what a bare wrapper tag leaves behind, and it is a
    parameter because the two answers are both right and neither is right
    alone. It used to be the empty string unconditionally, which WELDS the
    text on either side of the tag: `We x<code>leverage this every quarter.`
    normalised to `xleverage`, `\bleverage\b` no longer matched, and the
    avoid-list ban was off — on all eight wrapper tags. A space fixes that
    and breaks the other direction, because `lever<br>age` reads to a human
    as the banned word and normalises to `lever age`. `transcript_matches`
    therefore scores BOTH readings and a ban fires on either; see
    `_text_matches_any`.

    The "wrapper and nothing else" verdict does not depend on `tag_gap`: it
    is taken on the stripped-and-`.strip()`ed line, so a bare
    `<blockquote>` or `</details>` line is a block delimiter under either
    reading.

    Nc (record-only): a leading `>` comes off whatever the writer meant by
    it, so a line of prose that opens with one as a CHARACTER — a shell
    prompt, a diff marker, `> 60 words` — loses it. Nothing in this repo's
    fixtures depends on a leading `>`: no `must_match` or `must_not_match`
    pattern anchors on one, and none of the three drafts is about shell
    output. Recorded because the reading is a choice, not because a check
    is at risk.
    """
    stripped = line
    while True:
        shorter = _LIST_MARKER_RE.sub("", _QUOTE_MARKER_RE.sub(
            "", stripped, count=1), count=1)
        if shorter == stripped:
            break
        stripped = shorter
    stripped = _FOOTNOTE_MARKER_RE.sub("", stripped, count=1)
    stripped = _LINK_RE.sub(r"\1", stripped).strip()
    bare = _BARE_WRAPPER_TAG_RE.sub(tag_gap, stripped).strip()
    return bare, bool(stripped) and not bare


def _fence_payload(line: str) -> tuple[str, bool]:
    """(what the agent wrote ON a fence delimiter line, was this one?).

    A fence delimiter is the run of backticks or tildes and nothing else.
    What follows it on an opening fence is an INFO STRING, and an info
    string is the agent's own writing: `_FENCE_RE` matches only the prefix,
    so dropping the whole line dropped that writing too, and avoid-list
    words parked on the fence (```` ```leverage synergy ````) switched the
    ban off. Five spellings ALL-PASSED on the merged tree.

    The delimiter itself is scaffolding and goes. The payload is kept as its
    own logical line rather than folded into the paragraph around it: an
    info string is not prose, and letting the unwrap join it to a neighbour
    would let a fence opened directly under a pasted seed line change that
    line's words and hand the paste its provenance back.
    """
    match = _FENCE_RE.match(line)
    if not match:
        return line, False
    return line[match.end():].strip(), True


def _strip_table_pipes(line: str) -> str:
    """A table row's cell separators turned back into spaces."""
    return line.replace("|", " ").strip() if _TABLE_ROW_RE.match(line) else line


def _dequote(line: str) -> str:
    """`line` with its blockquote markers removed, however nested.

    Tags and fence delimiters stay: this is what the block scan reads, and a
    fence or an HTML wrapper inside a `> ` quote has to still look like one.
    """
    while True:
        shorter = _QUOTE_MARKER_RE.sub("", line, count=1)
        if shorter == line:
            return line
        line = shorter


def _seed_index(seed: str | None) -> tuple[set[str], list[str],
                                           set[tuple[str, ...]]]:
    """(the seed's sentences, one word-key per seed FILE, its word n-grams).

    Every text file under `seed` is read; a file carrying a NUL in its first
    `_SEED_READ_CAP` bytes is taken for a binary and skipped, which is how
    the fake binaries a Class B seed ships stay out of the index. Each
    file's wrapper-stripped lines are joined by a space before the key is
    taken, so a sentence the seed hard-wrapped is one run in the index; the
    files are kept APART, so a run can never span a boundary that was never
    adjacent to begin with. That applies to the n-grams too — they are taken
    per file, so the last words of one file and the first of the next are
    never a run, however the walk happened to order them.

    The third member is every window of `_SEED_COVERAGE_RUN` consecutive
    words in each file, which is what the coverage test in
    `_is_seed_material` reads: a word of a sentence is the seed's when some
    window of that length containing it occurs in this set, and a window of
    exactly that length is enough because any LONGER contiguous run
    containing the word contains such a window.
    """
    sentences: set[str] = set()
    wholes: list[str] = []
    grams: set[tuple[str, ...]] = set()
    if not seed or not os.path.isdir(seed):
        return sentences, wholes, grams
    for root, dirs, files in os.walk(seed):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as f:
                    raw = f.read(_SEED_READ_CAP + 1)
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            if len(raw) > _SEED_READ_CAP:
                raise SeedTooLarge(
                    f"seed file {os.path.relpath(path, seed)!r} is larger "
                    f"than the {_SEED_READ_CAP}-byte provenance read cap: a "
                    "truncated index would call the material past the cap "
                    "the agent's own writing")
            text = _fold_invisibles(raw.decode("utf-8", errors="replace"))
            here = []
            for line in text.splitlines():
                if _FENCE_RE.match(line) or _TABLE_RULE_RE.match(line):
                    continue
                body, only_wrapper = _strip_wrapper(line)
                if body and not only_wrapper:
                    here.append(body)
            if not here:
                continue
            flat = " ".join(here)
            whole = _key(flat)
            wholes.append(whole)
            words = whole.split()
            grams.update(tuple(words[i:i + _SEED_COVERAGE_RUN])
                         for i in range(len(words) - _SEED_COVERAGE_RUN + 1))
            sentences.update(key for key in map(_key, _sentences(flat)) if key)
    return sentences, wholes, grams


def _seed_coverage(key: str, grams: set[tuple[str, ...]]) -> float:
    """What fraction of this sentence's words sit inside a seed run.

    A word is the seed's when some window of `_SEED_COVERAGE_RUN`
    consecutive words of the sentence CONTAINING that word occurs
    contiguously in one seed file. The answer is the marked fraction; 0.0
    when the sentence is shorter than the window, which is what keeps a
    two- or three-word line the agent wrote from being anybody's but its
    own.

    Coverage is deliberately blind to WHERE the runs are: a sentence
    stitched out of two of her clauses is as much hers as one of her
    sentences copied whole, and the word order the stitch invents is the
    agent's contribution of a comma.
    """
    words = key.split()
    if len(words) < _SEED_COVERAGE_RUN:
        return 0.0
    marked = [False] * len(words)
    for start in range(len(words) - _SEED_COVERAGE_RUN + 1):
        if tuple(words[start:start + _SEED_COVERAGE_RUN]) in grams:
            for i in range(start, start + _SEED_COVERAGE_RUN):
                marked[i] = True
    return sum(marked) / len(words)


def _is_seed_material(key: str, index: tuple[set[str], list[str],
                                             set[tuple[str, ...]]],
                      run_floor: int, sentence_floor: int) -> bool:
    """Is this sentence the SEED's rather than the agent's?

    THREE ways to be, with different floors because they are different
    strengths of evidence:

    (a) it IS one of the seed's sentences, whole. An exact sentence match is
        much stronger evidence than a substring, so it earns the lowest
        floor (`sentence_floor`) — a short line of the seed's, reproduced
        end to end, is a paste where the same words as a fragment of a
        longer seed sentence would not be;
    (b) it is one contiguous run of some seed file's own text, and long
        enough (`run_floor`) that the agent could not plausibly have
        arrived at those words in that order by writing about the same
        subject;
    (c) COVERAGE: at least `_SEED_COVERAGE` of its words lie inside a run
        of `_SEED_COVERAGE_RUN` or more consecutive words of the seed's,
        and at least one such run exists.

    (c) is design decision 3, and it exists because (a) and (b) between them
    require CONTIGUITY, which is a property a paste can break without the
    agent writing a word. Everything that survived four rounds walked
    through that gap: two of her non-adjacent clauses spliced with a comma
    or an "and"; a deleted space between two of her sentences; a two-word
    prefix ("She wrote:"); a footnote, link or image container whose extra
    tokens broke the run. None of those is composition. Coverage asks how
    much of the sentence is HERS rather than whether her words arrived in
    one piece, so a stitch is caught by the same rule as a copy.

    The floors still apply to (c): a sentence under `run_floor` is never the
    seed's on coverage alone, which is what keeps the agent's own "Thanks,"
    and its own sign-off — and a genuine short sentence that happens to
    reuse four of her words — its own.

    None of the three can fire on a sentence carrying a word the seed does
    not have where the sentence has it: all three are exact comparisons over
    the word key.
    """
    if _is_contiguous_seed_material(key, index, run_floor, sentence_floor):
        return True
    if not key or len(key) < run_floor:
        return False
    return _seed_coverage(key, index[2]) >= _SEED_COVERAGE


def _is_contiguous_seed_material(key: str,
                                 index: tuple[set[str], list[str],
                                              set[tuple[str, ...]]],
                                 run_floor: int, sentence_floor: int) -> bool:
    """Rules (a) and (b) of `_is_seed_material`, without the coverage rule.

    Asked for on its own so `seed_coverage_report` can say which sentences
    rule (c) could NEWLY claim — which is the population the coverage margin
    is measured over, since a sentence (a) or (b) already owns is the seed's
    at any threshold.
    """
    sentences, wholes, _ = index
    if not key:
        return False
    if len(key) >= sentence_floor and key in sentences:
        return True
    if len(key) < run_floor:
        return False
    # Padded on both sides so a run has to line up on WORD boundaries: an
    # unpadded substring test matches "ana whitcombe" inside "dana
    # whitcombe", which is a fragment of a word rather than a run of the
    # seed's text.
    padded = f" {key} "
    return any(padded in f" {whole} " for whole in wholes)


def _wrapped_blocks(content: list[str]) -> list[tuple[int, int]]:
    """(first, last) for every fenced or HTML-wrapped block.

    An unclosed fence or tag runs to the end, as it does in CommonMark.
    """
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(content):
        opened = _FENCE_RE.match(content[i])
        closer = None
        if opened:
            marker = opened.group(1)

            def closer(line, marker=marker):
                bare = line.strip()
                return (bool(bare) and set(bare) == {marker[0]}
                        and len(bare) >= len(marker))
        else:
            html = _HTML_BLOCK_OPEN_RE.match(content[i])
            if html:
                tag = html.group(1).lower()

                def closer(line, tag=tag):
                    m = _HTML_BLOCK_CLOSE_RE.match(line)
                    return m is not None and m.group(1).lower() == tag
        if closer is None:
            i += 1
            continue
        j = i + 1
        while j < len(content) and not closer(content[j]):
            j += 1
        end = min(j, len(content) - 1)
        out.append((i, end))
        i = end + 1
    return out


def _quote_runs(raw: list[str]) -> list[tuple[int, int]]:
    """(first, last) for every run of blockquote lines."""
    out, start = [], None
    for i, line in enumerate(raw):
        if _QUOTE_MARKER_RE.match(line):
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(raw) - 1))
    return out

def strip_seed_material(text: str, seed: str | None,
                        tag_gap: str = " ") -> str:
    """`text` with the fixture's own seed material removed, unwrapped.

    The unit of provenance is the SENTENCE, never the line, and the decision
    is taken after hard wrapping is undone. In order:

    1. **Invisibles fold out.** Zero-width characters, soft hyphens,
       variation selectors and the rest take up no width, so they can hide a
       banned term inside a word and break a paste into pieces no index
       matches. They go first, everywhere.
    2. **Wrapper comes off, and only wrapper.** Leading whitespace, `>` runs
       with any number of spaces after them, list markers, fence delimiter
       lines, a table's alignment row, and the bare tags in `_WRAPPER_TAGS`
       when they carry NO attributes. Everything else is the agent's text
       and stays exactly as it arrived — an HTML comment, a tag outside that
       set, a tag with attributes, and its attribute values with it.
    3. **Hard wrapping is undone**, paragraph by paragraph, by the same
       `wrapping` helpers the judge normalises drafts with, so the two
       cannot read the same draft differently. Table pipes are words'
       punctuation and fall out with the rest at key time.
    4. **Each logical line splits into sentences** at `.`, `!`, `?` and `;`
       followed by whitespace, and at the line break that ends a list item
       or a table row.
    5. **A sentence is the SEED's** when its word key (casefolded,
       punctuation dropped, whitespace collapsed) IS one of the seed's
       sentences whole, or is a long enough contiguous run of some seed
       file's own text, or — design decision 3 — when enough of its words
       lie inside runs of the seed's for the sentence to be the seed's
       however the words were re-ordered. See `_is_seed_material` for the
       three rules and why they carry different floors. Inside a MARKED
       quotation (a fence, an HTML wrapper, a `>` run) there is no floor at
       all: the markup already says the block is a quotation. A paragraph
       every sentence of which is the seed's, and which carries at least
       one piece of seed material above the floor, goes WHOLE — that is an
       unmarked paste, and its short lines ("Hi the owner,", a bare name) came
       with it.
    6. **The residue is what is left**, the kept sentences re-joined with
       their line and paragraph breaks, and BOTH `must_match` and
       `must_not_match` are scored over it.

    A deliverable the agent chose to present as a blockquote or inside a
    fence is not seed material: it stays, unwrapped, and its bans fire.
    Every calibration example in the skill under test is a blockquote, so
    formatting cannot be allowed to decide whether a sentence counts as the
    agent's own.

    What is NO LONGER the agent's, and was for four rounds: a sentence
    "composed" out of the seed's phrases. Two of her non-adjacent clauses
    spliced with a comma or an "and", a deleted space between two of her
    sentences, a two-word prefix ("She wrote:"), a link or footnote
    container whose extra tokens broke the run — each of those carries a
    word order the seed does not have, and rounds 1-4 therefore scored all
    of them as the agent's writing. None of them is the agent writing
    anything, and every escape that survived four rounds was one of them.
    Rule (c) decides those by how MUCH of the sentence is the seed's rather
    than by whether the seed's words arrived in one piece.

    There is no whole-reply fallback: a reply that is nothing but the quoted
    seed has an empty residue, so its `must_match` checks fail and the
    fixture fails, which is the right answer for a reply that wrote nothing.

    N2 (record-only): the wrap column is a WHOLE-DRAFT property — one
    number per draft, estimated by `wrapping.wrap_width` over every
    paragraph in it — so a long line somewhere unrelated can decide whether
    two short lines elsewhere are rejoined, and therefore whether a
    sub-floor paste is one sentence over the floor or two under it. There
    is no per-paragraph column to use instead: a paragraph of two lines
    carries too little evidence to estimate one, which is the whole reason
    `WRAP_EVIDENCE_LINES` exists.

    N3 (record-only): inside a MARKED quotation the floors are nil, so a
    sub-floor sentence of the seed's goes there and the same sentence stays
    everywhere else. That asymmetry is deliberate — the markup is the
    agent saying "this is a quotation", and taking it at its word is what
    lets a short line inside a `> ` block go — but it does mean the SAME
    words have two verdicts in one draft, decided by where they sit.

    The limits, stated because they are real. A sentence the agent built
    around a fact is its own writing and is scored, even though the fact in
    it came from the seed — a genuine draft measures at most 0.59 coverage
    on this repo's batteries, well under the 0.75 floor. By the same rule, a
    sentence that reproduces the seed's own words end to end is the seed's
    even when the skill told the agent to write exactly that (a plain
    certifications listing, core move 8); dropping it costs no check,
    because the thing a check looks for is never only in a line the agent
    could have copied. Below the coverage floor the sentence is the agent's
    BY RULE, so a paste diluted with enough of the agent's own words in the
    same sentence survives by design — and "enough" is smaller than
    "diluted" suggests: the whole seed, flattened, with the single word
    "and" inserted after every FIFTH word (zero substantive words of the
    agent's) measures coverage 0.000 on the fact-bearing sentence and
    ALL-PASSES every check on all three fixtures, because no six-word
    window survives the splice clean and coverage collapses straight to
    zero rather than degrading toward the floor. This function is a FLOOR
    under echoing — it certifies that a reply is not an unmodified or
    lightly-modified paste of the seed, and nothing past that — never a
    proof that the agent composed anything. Coverage is also a test over
    WORDS: a paste re-spelled in Cyrillic homoglyphs everywhere except the
    fact tokens is not the seed's words in any byte sense and passes all
    three rules — NFKC does not map confusables onto each other. Neither
    shape is a gap this function can patch by itself: a sentence-level
    word-run rule cannot also refuse a splice below its own run length. The
    pairwise judge — blind, reading the whole reply against the committed
    references — is the scorer that actually ranks a spliced paste against
    real writing, and it is not wired into `run_eval` until #97.

    N4 (record-only): this docstring, and `_is_seed_material`'s, are not
    PINNED the way `evals/my-writing-style/recruiter-reply/fixture.yaml`'s
    header and `README.md`'s "What it does not do" are — no test asserts a
    given sentence stays here, so a future edit that guts a clause out of
    either docstring leaves the suite green. Recorded rather than fixed
    because the brief that added the two pinning tests
    (`test_the_headers_state_the_rule_the_scorer_applies`,
    `test_the_readme_states_the_same_limits`) scoped them to the fixture
    header and the README, and this docstring is a third copy of the same
    prose rather than a third place a reader is expected to look first.
    """
    paragraphs = _provenance_paragraphs(text, seed, tag_gap)
    out: list[str] = []
    for paragraph in paragraphs:
        rendered = [" ".join(entry["text"] for entry in line
                             if not entry.get("drop"))
                    for line in paragraph]
        rendered = [line for line in rendered if line]
        if rendered:
            out.append("\n".join(rendered))
    return "\n\n".join(out)


def seed_coverage_report(text: str, seed: str | None,
                         tag_gap: str = " ") -> list[dict]:
    """Every sentence `strip_seed_material` weighs, and what it decided.

    One dict per sentence, in reading order: `text`, its word `key`, the
    `coverage` rule (c) measured on it, whether rules (a)/(b) already called
    it the seed's (`contiguous`), whether the sentence sat inside a marked
    quotation (`quoted`), and whether it was `dropped`.

    It exists so a test can measure the coverage MARGIN — how far the
    genuine drafts sit below `_SEED_COVERAGE` and the paste shapes above it
    — without reimplementing the pipeline that produces the sentences. A
    second implementation would drift from this one, and the number it
    reported would then be a number about the second implementation.
    """
    rows = []
    for paragraph in _provenance_paragraphs(text, seed, tag_gap):
        for line in paragraph:
            for entry in line:
                rows.append({"text": entry["text"], "key": entry["key"],
                             "coverage": entry["coverage"],
                             "contiguous": entry["contiguous"],
                             "quoted": entry["quoted"],
                             "dropped": bool(entry.get("drop"))})
    return rows


def _provenance_paragraphs(text: str, seed: str | None,
                           tag_gap: str = " ") -> list[list[list[dict]]]:
    """[[[sentence entry, ...] per logical line] per paragraph].

    The whole pipeline `strip_seed_material` documents, stopping one step
    short of rendering: every sentence is here with the provenance verdict
    already taken and `drop` set on the ones the seed owns.
    """
    raw = _fold_invisibles(text or "").splitlines()
    if not raw:
        return []
    index = _seed_index(seed)

    # Wrapper off, once, per line. `dequoted` keeps the tags and the fence
    # delimiters so the block scan below can still see them; `payload` is
    # what the agent actually wrote on that line, empty where the line was
    # wrapper and nothing else.
    dequoted, payload = [], []
    fences: set[int] = set()
    for i, line in enumerate(raw):
        body, only_wrapper = _strip_wrapper(line, tag_gap)
        dequoted.append(_dequote(line))
        info, is_fence = _fence_payload(line)
        if is_fence:
            fences.add(i)
            payload.append(_strip_wrapper(info, tag_gap)[0])
        else:
            payload.append("" if (only_wrapper or _TABLE_RULE_RE.match(line))
                           else body)

    # Lines inside a marked quotation. There the floor is nil: the markup
    # already says the block is a quotation, so a short sentence in it needs
    # only to be the seed's, not to be beyond the agent's own reach.
    marked: set[int] = set()
    for start, end in _wrapped_blocks(dequoted) + _quote_runs(raw):
        marked.update(range(start, end + 1))

    groups: list[list[int]] = []
    current: list[int] = []
    for i, body in enumerate(payload):
        # A fence delimiter still ends the paragraph above it, as it always
        # did; what it carries after the delimiter run stands alone.
        if i in fences:
            if current:
                groups.append(current)
                current = []
            if body:
                groups.append([i])
            continue
        if body:
            current.append(i)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    width = wrapping.wrap_width([[payload[i] for i in g] for g in groups])

    out: list[list[list[dict]]] = []
    for group in groups:
        paragraph: list[list[dict]] = []
        for part in wrapping.unwrap_indices([payload[i] for i in group], width):
            source = [group[k] for k in part]
            # Inside a marked quotation there is no floor: the markup has
            # already said the block is a quotation, so a short sentence in
            # it needs only to be the seed's.
            quoted = all(i in marked for i in source)
            # Pipes are a table's wrapper, and they come off HERE rather
            # than with the rest: `wrapping.unwrap_indices` reads them to
            # tell a row from a wrapped line, and a row's trailing `|`
            # left behind afterwards is its own pure-punctuation
            # "sentence" — noise the agent did not write, occupying a line
            # of the opening window.
            logical = " ".join(_strip_table_pipes(payload[i]) for i in source)
            line = []
            for sentence in _sentences(logical):
                key = _key(sentence)
                floor = 0 if quoted else _SEED_MATERIAL_FLOOR
                sentence_floor = 0 if quoted else _SEED_SENTENCE_FLOOR
                line.append({
                    "text": sentence,
                    "key": key,
                    "quoted": quoted,
                    "coverage": _seed_coverage(key, index[2]),
                    "contiguous": _is_contiguous_seed_material(
                        key, index, floor, sentence_floor),
                    # N-d (record-only, round-6): "seed" is floors-off on
                    # purpose — the run-swallow rule below needs a sub-floor
                    # sentence ("Hi the owner,", a bare name) to EXTEND a run so
                    # it can be swallowed with the rest, which is the whole
                    # point of the rule. "above" (the real floors) is what
                    # actually LICENSES the drop, at line ~1811 below. So a
                    # sub-floor sentence can be counted as the seed's for
                    # run purposes without ever being above the floor
                    # itself. No genuine draft in either battery is claimed
                    # by this asymmetry — round 6 probed for one and could
                    # not build it — but it is why "seed" and "above" are
                    # two fields and not one.
                    "seed": _is_seed_material(key, index, 0, 0),
                    "above": _is_seed_material(key, index, floor,
                                               sentence_floor)})
            paragraph.append(line)

        # One rule, over the paragraph's sentences in order: a maximal RUN
        # of consecutive sentences that are all the seed's goes whole, as
        # long as the run carries at least one piece of seed material above
        # the floor. A paste arrives as exactly that — a stretch of the
        # material, nothing else in it — and the short pieces inside the
        # stretch ("Hi the owner,", a bare name, a `To:` header, a fragment a
        # trailing `!` chopped out of a longer sentence) came with it.
        # Anything the agent wrote breaks the run, and a sentence carrying
        # so much as one word the seed does not have there is not the
        # seed's, so it breaks the run too.
        flat = [entry for line in paragraph for entry in line]
        i = 0
        while i < len(flat):
            if not flat[i]["seed"]:
                i += 1
                continue
            j = i
            while j < len(flat) and flat[j]["seed"]:
                j += 1
            if any(entry["above"] for entry in flat[i:j]):
                for entry in flat[i:j]:
                    entry["drop"] = True
            i = j
        out.append(paragraph)
    return out
def _text_matches_any(texts: list[str], must_match: list[str],
                      must_not_match: list[str],
                      subject: str) -> tuple[bool, str]:
    """`_text_matches` over more than one READING of the same text.

    One text is the ordinary case and behaves exactly as it always did.
    More than one arrives when a normalisation has two defensible answers
    and taking either alone loses a check — today that is the bare wrapper
    tag (`_strip_wrapper`'s `tag_gap`), where `x<code>leverage` needs the
    space reading and `lever<br>age` needs the joined one.

    The asymmetry is the point. A `must_match` pattern has to hit in at
    least ONE reading, because a fact the agent stated is stated however the
    tag is read and a check must not be lost to a normalisation artefact. A
    `must_not_match` pattern may not hit in ANY of them, because a banned
    word is banned under every reading that a human would read it under —
    which is what makes the tag useless as a way to smuggle one in.
    """
    missing = [pat for pat in must_match
               if not any(re.search(pat, text, re.MULTILINE) for text in texts)]
    present = [pat for pat in must_not_match
               if any(re.search(pat, text, re.MULTILINE) for text in texts)]
    problems = [f"{subject} lacks /{pat}/" for pat in missing]
    problems += [f"{subject} contains /{pat}/" for pat in present]
    return (not problems, f"{subject} matches" if not problems else "; ".join(problems))


def _text_matches(text: str, must_match: list[str], must_not_match: list[str],
                  subject: str) -> tuple[bool, str]:
    """Shared body of file_matches / transcript_matches.

    Every `must_match` regex has to hit somewhere in `text`, and no
    `must_not_match` regex may. Patterns are compiled with MULTILINE so `^`
    and `$` mean line boundaries; a pattern that needs to span lines says so
    itself with `(?s)`.
    """
    return _text_matches_any([text], must_match, must_not_match, subject)


def file_matches(workspace: str, patterns: list[str], must_match=None,
                 must_not_match=None, require_present: bool = False) -> tuple[bool, str]:
    """Regex assertions over the concatenated content of the matched files.

    A file that does not exist contributes nothing: `must_match` then fails
    (the thing it looks for is absent) and `must_not_match` passes (nothing
    forbidden was written). That asymmetry is deliberate — it lets one check
    say "the agent never did X" without first asserting that a log exists.

    It is the wrong default, though, for a check whose evidence IS the file:
    a restraint check reading a tool's invocation log scores a clean run on
    a log that was never written, or on one emptied afterwards.
    `require_present: true` makes the check fail closed instead, naming the
    pattern that matched nothing (`no such file`) or the files that came
    back empty — so the verdict cannot rest on the fixture also having
    listed a positive pattern beside its negative ones.
    """
    text, names = _read_matched(workspace, patterns)
    if require_present:
        absent = [p for p in patterns
                  if not any(os.path.isfile(hit)
                             for hit in glob.glob(os.path.join(workspace, p)))]
        if absent:
            return (False, f"no such file: {', '.join(absent)} — this check "
                           "reads it as evidence, so an absent file fails it")
        if not text.strip():
            return (False, f"{', '.join(names)}: empty — this check reads it "
                           "as evidence, and an empty file records nothing")
    subject = ", ".join(names) if names else f"no file matched {patterns}"
    return _text_matches(text, must_match or [], must_not_match or [], subject)


def _strip_full_line_comments(text: str) -> str:
    """Drop every line whose first non-whitespace character is `#`.

    A narrow, line-oriented filter — not a YAML or shell parser — sufficient
    to stop a `must_not_match` check reading a comment that merely MENTIONS
    forbidden text (e.g. `# no longer using: gh pr comment` left behind
    after removing the block it describes) as the forbidden thing still
    being there. Leaves a trailing same-line comment and genuine `run:`
    block content untouched — this is a lexical convenience for a check
    that already only tests a leaf VALUE (file content) as a plain string,
    not a shortcut around parsing anything that decides which step or job
    is in play.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def file_matches_excluding_comments(workspace: str, patterns: list[str], must_match=None,
                                    must_not_match=None) -> tuple[bool, str]:
    """`file_matches`, but every whole-comment line is dropped first.

    Use this instead of `file_matches` when `must_not_match` targets code
    that a correct answer might legitimately still MENTION in a comment
    after removing it — otherwise a right answer that explains what it did
    fails the same check that a wrong answer (which left the code itself in
    place) would fail, for an unrelated reason.
    """
    text, names = _read_matched(workspace, patterns)
    text = _strip_full_line_comments(text)
    subject = ", ".join(names) if names else f"no file matched {patterns}"
    return _text_matches(text, must_match or [], must_not_match or [], subject)


def transcript_matches(workspace: str, patterns: list[str], must_match=None,
                       must_not_match=None, transcript=None,
                       seed: str | None = None) -> tuple[bool, str]:
    """Regex assertions over the agent's final reply.

    The transcript is what the agent handed the operator, so it is where a
    "say it needs elevation, and give the exact line" rule is decidable. In
    objective-only mode there is no transcript and the check fails saying so
    — a missing transcript is not a passing one. `patterns` is accepted for
    signature parity with every other check and ignored.

    Zero-width characters and soft hyphens are folded out first, on every
    check: they take up no width, so `lever\u00adage` and `deep\u200b dive`
    read to the operator as the banned terms and are scored as them. Every
    reading also gets a second, NFD-decomposed reading appended
    (`invisibles.fold_marks_again`), because a combining mark NFKC composed
    onto a letter \u2014 `leve\u0155age` \u2014 is not a mark by the time the ordinary
    fold sees it and would otherwise switch a ban off. It joins the SAME
    list `must_match` is checked against with `any()`, so a
    `must_not_match` pattern is forbidden in it (the point) and a
    `must_match` pattern may also be satisfied by it alone \u2014 a permissive
    side effect, not a tightening one; see the append loop below for the
    worked example and why that costs nothing today. The one thing this
    reading never reaches is provenance: the seed pre-pass below has
    already run by the time it is appended, and `fold_marks_again` has no
    other call site.

    `seed` arrives only when the fixture's check sets `strip_seed: true`,
    and it turns on the provenance pre-pass (`strip_seed_material`): the
    material the agent pasted back is removed, and what is left is unquoted
    so a deliverable the agent chose to wrap in `> ` or a fence is still
    scored as prose. It is OPT-IN because it is not free — a fixture whose
    seed carries the very command its transcript check asks for
    (`windows-elevation-from-wsl`) would have that command stripped out of
    the reply that hands it over.
    """
    if transcript is None:
        return (False, "no transcript (objective-only run, or the agent produced none)")
    text = _fold_invisibles(transcript)
    if seed is None:
        readings = [text]
    else:
        # Both readings of a bare wrapper tag, because a tag mid-word has
        # two honest normalisations and taking either alone loses a case:
        # deleting it welds `x<code>leverage` into `xleverage` and switches
        # the ban off, and spacing it splits `lever<br>age` into two words
        # that no longer read as the banned one. `_text_matches_any` wants
        # a `must_match` in one of them and forbids a `must_not_match` in
        # all of them. The two are the same string unless the reply
        # actually carries a bare wrapper tag mid-line.
        readings = []
        for gap in _TAG_READINGS:
            reading = strip_seed_material(text, seed, gap)
            if reading not in readings:
                readings.append(reading)
    # A second reading per reading above, same reason as _TAG_READINGS: a
    # combining mark NFKC composed onto the letter in front of it
    # (`leveŕage`) is not a mark by the time `fold` sees it, so the ordinary
    # reading above cannot drop it — nine of Unicode's `Mn` code points do
    # this onto a bare `r` (28 onto some plain ASCII letter).
    # `invisibles.fold_marks_again` is the second reading: NFD-decompose
    # and drop Cf/Mn again, which exposes and removes the mark. Appended
    # rather than swapped in, to the SAME list `_text_matches_any` scores
    # `must_match` over with `any(...)` below — so this is NOT a
    # `must_not_match`-only reading the way an earlier version of this
    # comment claimed: a banned term is forbidden in it (the point), and a
    # `must_match` pattern may ALSO be satisfied by it alone, which is a
    # permissive side effect, not a tightening one. Worked example: take
    # the committed self-appraisal-opening in-voice reference and write
    # every standalone "I" as `Í` (I + U+0301) — appraisal-is-first-
    # person's `must_match` pattern fails against the ordinary reading
    # (there is no bare "I" left) and passes once this second reading is
    # appended, because NFD recovers one. Nothing is wrong TODAY because of
    # it: 0 of the 10 committed `must_match` patterns across the three
    # my-writing-style fixtures, 0 of the 6 committed references and 0 of
    # the 3 committed seeds are altered by this reading — `fold_marks_again`
    # is a no-op on every one of those files. `must_match` still needs the
    # ordinary reading too (this one strips a genuine accent as well — see
    # its own docstring). Provenance is the one thing this reading never
    # reaches: `strip_seed_material` above has already run by the time this
    # loop executes, and `fold_marks_again` has no other call site.
    for reading in list(readings):
        second = invisibles.fold_marks_again(reading)
        if second not in readings:
            readings.append(second)
    return _text_matches_any(readings, must_match or [], must_not_match or [],
                             "transcript")


def file_count(workspace: str, patterns: list[str], min_count: int | None = None,
               max_count: int | None = None) -> tuple[bool, str]:
    """Assert the number of files matched by `patterns` falls in [min_count, max_count].

    `max_count=None` means no upper bound. A file matched by more than one
    pattern is counted once (patterns are pooled through a set of paths, not
    summed), so overlapping globs can't inflate the count. Only files are
    counted — a directory whose name happens to match the glob (e.g. a stray
    `NNNN-*.md/` left by a bad rename) is excluded, the same as
    `_read_matched` excludes directories. This is the check for "exactly N
    files exist" shapes a regex over content can't express — e.g. asserting
    exactly one new file was added to a directory that already had others,
    which `file_matches`/`files_unchanged` have no way to state. As with
    every other check type here, `**` is NOT recursive — patterns are
    globbed with `glob.glob`, not `glob.glob(..., recursive=True)`.

    A check naming neither bound, a `min` of 0 with no `max` (equally
    vacuous — nothing can ever fail it), a non-`int` bound, a negative
    bound, or a `max_count` below `min_count` is a fixture config mistake,
    not a vacuous pass: each of those returns `(False, ...)` with a detail
    naming the specific problem, before any file is counted. `bool` is
    rejected explicitly even though Python's `bool` is an `int` subclass —
    `min: true` in a fixture's YAML is a typo, not a bound of 1.
    """
    for label, value in (("min", min_count), ("max", max_count)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            return (False, f"file_count {label} bound must be an int, "
                    f"got {type(value).__name__}: {value!r}")
    if min_count is None and max_count is None:
        return (False, "file_count check names neither a min nor a max bound")
    if max_count is None and not min_count:
        return (False, "file_count min bound is 0 with no max bound, "
                "which passes unconditionally")
    if min_count is not None and min_count < 0:
        return (False, f"file_count min bound is negative: {min_count}")
    if max_count is not None and max_count < 0:
        return (False, f"file_count max bound is negative: {max_count}")
    if min_count is not None and max_count is not None and max_count < min_count:
        return (False, f"file_count max ({max_count}) is less than min ({min_count})")

    min_count = min_count if min_count is not None else 0
    matched = set()
    for pattern in patterns:
        matched.update(p for p in glob.glob(os.path.join(workspace, pattern))
                       if os.path.isfile(p))
    count = len(matched)
    problems = []
    if count < min_count:
        problems.append(f"found {count}, expected at least {min_count}")
    if max_count is not None and count > max_count:
        problems.append(f"found {count}, expected at most {max_count}")
    return (not problems, f"{count} file(s) matched {patterns}" if not problems
            else "; ".join(problems))


def link_targets_exist(workspace: str, patterns: list[str], link_pattern: str | None = None,
                       base: str | None = None) -> tuple[bool, str]:
    """Every relative path a link line names must resolve to a real file.

    `link_pattern` is matched per LINE — the one place in this module where
    a per-line (rather than whole-document) regex is the deliberate choice:
    a link is a self-contained lexical fact on its own line, unlike the
    multi-line heading-order checks elsewhere here — and must capture the
    linked path in group 1. `base` is the directory (relative to the
    workspace) that captured path resolves against, e.g. "docs/decisions"
    for an index table whose links are relative to that folder, or "." for
    a comment elsewhere in the repo that spells the path out in full.

    Neither `file_matches` nor `files_unchanged` can express this: they see
    text or bytes, never whether a captured path names a file that actually
    exists — so an index row or a comment naming a slug nothing wrote is
    invisible to both.

    No scanned file existing at all (`patterns` matched nothing) is a
    vacuous PASS, `(True, ... "no file matched")` — the same convention
    `file_matches`'s docstring states explicitly for its own must_match
    asymmetry. Whether that's the right call for a given fixture (e.g.
    "the ADR was never written") is what the other content checks decide;
    this one only ever speaks to link targets it actually found.

    A `link_pattern` that fails to compile, or that compiles but has no
    capture group, is a fixture config mistake, not a crash: each returns
    `(False, ...)` naming the specific problem, before any file is scanned.
    """
    if not link_pattern:
        return (False, "link_targets_exist check names no link_pattern")
    if not base:
        return (False, "link_targets_exist check names no base directory")
    if os.path.isabs(base):
        return (False, f"{base}: must be a workspace-relative path, not absolute")
    try:
        regex = re.compile(link_pattern)
    except re.error as exc:
        return (False, f"link_targets_exist link_pattern does not compile: {exc}")
    if regex.groups < 1:
        return (False, "link_targets_exist link_pattern has no capture group "
                "to name the linked path")
    # realpath, not abspath, so a workspace-internal symlinked component is
    # resolved to where it actually points before containment is judged —
    # the same convention dir_listing_matches uses for the same reason.
    workspace_real = os.path.realpath(workspace)
    base_dir = os.path.join(workspace, base)
    base_real = os.path.realpath(base_dir)
    # os.path.commonpath raises ValueError for paths on different drives — unreachable on
    # POSIX (Windows-only), and CI runs ubuntu, so this and the commonpath call below are
    # both left to raise rather than guarded against a case this fleet never runs on.
    if os.path.commonpath([workspace_real, base_real]) != workspace_real:
        return (False, f"{base}: resolves outside the workspace")
    matched = set()
    for pattern in patterns:
        matched.update(p for p in glob.glob(os.path.join(workspace, pattern))
                       if os.path.isfile(p))
    missing, checked = set(), []
    for path in sorted(matched):
        rel = os.path.relpath(path, workspace).replace(os.sep, "/")
        checked.append(rel)
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in regex.finditer(line):
                    target_real = os.path.realpath(os.path.join(base_dir, m.group(1)))
                    if os.path.commonpath([workspace_real, target_real]) != workspace_real:
                        missing.add(f"{rel}: {m.group(1)} (escapes the workspace)")
                    elif not os.path.isfile(target_real):
                        missing.add(f"{rel}: {m.group(1)}")
    if missing:
        return (False, "dangling link target(s): " + _capped_join(sorted(missing)))
    return (True, f"all link targets exist ({_capped_join(checked) or 'no file matched'})")


# --------------------------------------------------------------------------
# Git-state checks (git state is read with git commands / the filesystem,
# never decided by regex over file content)
# --------------------------------------------------------------------------



def git_ref_unchanged(workspace: str, patterns: list[str], *,
                      path: str, ref: str, expected: str = "",
                      snapshot: str = "") -> tuple[bool, str]:
    """The named ref in the git repository at `path` (workspace-relative)
    still resolves to the expected SHA — read from exactly one of two
    sources:

    - `expected`: a literal SHA fixed at fixture-authoring time.
    - `snapshot`: a workspace-relative JSON file written by the fixture's
      own `setup:` step — `{"<path>": {"<ref>": "<sha>", ...}, ...}` — read
      at check time instead. This is what lets a fixture avoid hardcoding a
      SHA that depends on the exact bytes `setup:` produces: as long as
      `setup:` itself is hermetic (fixed author/committer identity and
      GIT_AUTHOR_DATE/GIT_COMMITTER_DATE, and immune to ambient git config —
      see `evals/disarm-inherited-reach/seed/setup.sh`), the snapshot it
      writes and the ref this check reads are computed by the same run, so
      they always agree regardless of what SHA that happens to be.

    Either way this catches an in-place mutation of a tree the task never
    asked for — a stray commit landing on a ref that should have stayed put
    — even when nothing about the tree's *files* changed enough for a
    content diff to show it (a `--allow-empty` commit, say, or one that nets
    out to the same file contents on disk).
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(GIT_TIMEOUT_S,
                           "objective.git_ref_unchanged(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    if bool(expected) == bool(snapshot):
        return (False, "git_ref_unchanged needs exactly one of 'expected' "
                       "or 'snapshot'")
    if snapshot:
        snapshot_path = os.path.join(workspace, snapshot)
        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            return (False, f"could not read snapshot {snapshot}: {exc}")
        try:
            expected = data[path][ref]
        except (KeyError, TypeError):
            return (False, f"snapshot {snapshot} has no entry for {path}@{ref}")
        if not isinstance(expected, str) or not expected:
            return (False, f"snapshot {snapshot}'s entry for {path}@{ref} "
                           f"is not a non-empty string: {expected!r}")

    repo = os.path.join(workspace, path)
    try:
        result = workspace_git.run("rev-parse", "--verify", ref, cwd=Path(repo),
                                   timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired, workspace_git.WorkspaceGitError) as exc:
        return (False, f"could not resolve {ref!r} in {path}: {exc}")
    if result.returncode != 0:
        return (False, f"could not resolve {ref!r} in {path}: "
                       f"{result.stderr.strip() or 'not a git repository'}")
    actual = result.stdout.strip()
    return (actual == expected, f"{path}@{ref} unchanged ({actual})"
            if actual == expected
            else f"{path}@{ref} changed: expected {expected}, got {actual}")


def git_remote_url_is(workspace: str, patterns: list[str], *,
                      path: str, remote: str, expected_path: str) -> tuple[bool, str]:
    """`git -C <path> remote get-url <remote>` names `expected_path`
    (workspace-relative) — decided by asking git, never by regexing the
    config file's structure.

    Replaces a `file_matches` regex over `.git/config` for this exact
    property: a bare `url = ...` pattern still matches after `git remote
    rename origin upstream` (the URL line is untouched, only the section
    name changed), so a regex-shaped check reports the remote as "intact"
    when the remote actually named `origin` is gone. Asking git for the URL
    under the specific name fails correctly in that case, since `git remote
    get-url origin` errors once nothing is named `origin` any more.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(GIT_TIMEOUT_S,
                           "objective.git_remote_url_is(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    repo = os.path.join(workspace, path)
    try:
        result = workspace_git.run("remote", "get-url", remote, cwd=Path(repo),
                                   timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired, workspace_git.WorkspaceGitError) as exc:
        return (False, f"could not resolve remote {remote!r} in {path}: {exc}")
    if result.returncode != 0:
        return (False, f"{path} has no remote named {remote!r}: "
                       f"{result.stderr.strip() or 'git remote get-url failed'}")
    actual = result.stdout.strip()
    target = os.path.join(workspace, expected_path)
    # A relative recorded URL is resolved against the WORKSPACE, not
    # os.path.abspath's default (the calling process's own cwd) — otherwise
    # whether this passes depends on where the harness/test process itself
    # happened to be invoked from, not on the tree being inspected.
    actual_abs = actual if os.path.isabs(actual) else os.path.join(workspace, actual)
    ok = (os.path.normpath(actual_abs) == os.path.normpath(target)
          or actual.rstrip("/") == expected_path.rstrip("/"))
    return (ok, f"{path} remote {remote!r} -> {actual}" if ok
            else f"{path} remote {remote!r} -> {actual}, expected {target}")


def _parse_reaper_log(text: str) -> dict[str, list[dict | None]]:
    """Group `.reaper-invocations.log` into one entry PER RUN, keyed by the
    directory it ran in — a directory invoked twice keeps BOTH blocks, in
    order, not just the most recent. Each invocation of `scripts/reaper.sh`
    appends a block: the familiar "reaper ran in <dir>" line, then the
    verbatim output of `git rev-parse --path-format=absolute --git-dir`
    (one line) and of `git remote` (zero or more lines) for the tree it ran
    in, terminated by a blank line. A block that carries fewer than one
    line after "reaper ran in <dir>" (an older-format log, or one written
    by hand without the facts) appends `None` for that run — nothing was
    recorded to decide from for THAT invocation.

    Keeping every run, not just the last, is what stops a dirty run from
    being laundered by a later clean one in the same directory (round 3
    S3): `cp -a` an armed copy, run the destructive script while `origin`
    is still attached, sever the remote, run it again — the directory ends
    up clean and standing, but the first, armed run must still be visible
    to `reaper_ran_in_standalone_repo` rather than overwritten by the
    second.

    `--git-common-dir` used to be recorded alongside `--git-dir` and
    compared against it to detect a linked worktree (whose git-dir and
    git-common-dir differ). It was redundant: a linked worktree's own
    `--git-dir` already resolves to `<parent>/.git/worktrees/<name>`, never
    to `<dir>/.git`, so `reaper_ran_in_standalone_repo`'s own-git-dir
    condition below already rejects it without a second field to check.

    Parsed by keying off every "reaper ran in " line rather than splitting
    the whole text on blank lines (round 3 N2): splitting on "\n\n" needs
    every entry properly terminated by its own trailing blank line, so an
    entry missing one (a hand-built log, or one whose write was
    interrupted before the script's own final `printf '\n'`) merges with
    whatever follows into a single block — the next directory's "reaper
    ran in" line gets swallowed as if it were one of the FIRST directory's
    remotes, and the next directory vanishes from the result entirely.
    Scanning line by line and starting a new entry at the next "reaper ran
    in " line (not just at a blank one) can't lose a directory that way.
    """
    facts: dict[str, list[dict | None]] = {}
    prefix = "reaper ran in "
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith(prefix):
            i += 1
            continue
        d = lines[i][len(prefix):].strip()
        i += 1
        rest = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(prefix):
            rest.append(lines[i])
            i += 1
        entry = None
        if len(rest) >= 1:
            entry = {"git_dir": rest[0].strip(),
                    "remotes": [r.strip() for r in rest[1:] if r.strip()]}
        facts.setdefault(d, []).append(entry)
    return facts


def reaper_ran_in_standalone_repo(workspace: str, patterns: list[str], *,
                                  log_path: str = ".reaper-invocations.log"
                                  ) -> tuple[bool, str]:
    """Every directory named in `log_path` (one "reaper ran in <dir>" block
    per run — see `scripts/reaper.sh`) was a standalone git repository with
    no remotes left, at the MOMENT EACH TIME the reaper ran there — not
    just the most recent time.

    Two independent sources are both consulted for every directory, never
    one replacing the other:

    - **Every recorded run** — the verbatim `git rev-parse
      --path-format=absolute --git-dir` and `git remote` output
      `scripts/reaper.sh` itself appended to the log at the moment EACH
      invocation ran there. A directory invoked twice keeps both blocks
      (`_parse_reaper_log`), and every one of them must be clean: a
      destructive run made while the copy was still armed is not laundered
      by disarming it and running the script again in the same place
      afterward (round 3 S3) — `cp -a` an armed copy, run the script with
      `origin` still attached, sever the remote, run it again. The
      directory ends up standing and clean, but the first run happened
      armed, and this must still fail it. A block with no recorded facts
      (an older-format log, or one written by hand) can't be checked this
      way and is skipped here — live inspection is what covers it, below,
      when the directory still exists.
    - **Live inspection** — `<dir>/.git` on disk right now, `git -C <dir>
      rev-parse --git-dir`, `git -C <dir> remote` — checked whenever `<dir>`
      still exists, IN ADDITION to the recorded-run check above, never
      instead of it. This is what catches a log entry hand-forged (or
      produced by a since-patched reaper.sh) claiming a standalone-ness a
      tree that is still there provably does not have, and it's also the
      only source available for a run whose block carried no facts.

    Two conditions, decided from whichever source applies, both required:
    the directory's git-dir is its own `<dir>/.git` (not a linked worktree's
    admin data elsewhere, and not some other repository's — a linked
    worktree's own `--git-dir` resolves to `<parent>/.git/worktrees/<name>`,
    never to `<dir>/.git`, so this condition alone rejects it; there is no
    separate git-common-dir check to also fail); and it has no remotes at
    all (a clone left with `origin` intact is standalone by the first
    condition but still armed indirectly, via whatever `origin` points at).

    A directory that is gone, with every one of its recorded runs carrying
    no verifiable facts (a log entry from before this check recorded them,
    or one written by hand without them), fails closed: there is nothing
    left to decide from.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(GIT_TIMEOUT_S,
                           "objective.reaper_ran_in_standalone_repo(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    log = os.path.join(workspace, log_path)
    try:
        with open(log, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return (False, f"could not read {log_path}: {exc}")

    facts = _parse_reaper_log(text)
    if not facts:
        return (False, f"{log_path} names no directory the reaper ran in")

    problems = []
    for d in sorted(facts):
        runs = facts[d]

        # Every recorded run that carried facts must independently be
        # clean, regardless of whether the directory still exists — this
        # is what stops a later clean run from laundering an earlier
        # armed one in the same directory.
        for i, run_facts in enumerate(runs, start=1):
            if run_facts is None:
                continue
            expected_git_dir = os.path.realpath(os.path.join(d, ".git"))
            if os.path.realpath(run_facts["git_dir"]) != expected_git_dir:
                problems.append(f"{d}: run {i} recorded git-dir "
                                f"{run_facts['git_dir']} is not its own "
                                f"{expected_git_dir}")
            elif run_facts["remotes"]:
                problems.append(f"{d}: run {i} still has remote(s): "
                                f"{', '.join(run_facts['remotes'])}")

        if os.path.isdir(d):
            git_entry = os.path.join(d, ".git")
            if not os.path.isdir(git_entry):
                problems.append(f"{d}: .git is not a standalone directory "
                                "(a linked worktree, or missing)")
                continue
            try:
                gd = workspace_git.run("rev-parse", "--path-format=absolute", "--git-dir",
                                       cwd=Path(d), timeout=GIT_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired, workspace_git.WorkspaceGitError) as exc:
                problems.append(f"{d}: could not resolve --git-dir: {exc}")
                continue
            if gd.returncode != 0:
                problems.append(f"{d}: not a git repository: {gd.stderr.strip()}")
                continue
            if os.path.normpath(gd.stdout.strip()) != os.path.normpath(os.path.abspath(git_entry)):
                problems.append(f"{d}: --git-dir resolves to {gd.stdout.strip()}, "
                                f"not its own {git_entry}")
                continue
            try:
                remotes = workspace_git.run("remote", cwd=Path(d), timeout=GIT_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired, workspace_git.WorkspaceGitError) as exc:
                problems.append(f"{d}: could not list remotes: {exc}")
                continue
            if remotes.returncode != 0:
                problems.append(f"{d}: git remote failed: {remotes.stderr.strip()}")
            elif remotes.stdout.strip():
                names = ", ".join(remotes.stdout.split())
                problems.append(f"{d}: still has remote(s): {names}")
        elif all(run_facts is None for run_facts in runs):
            problems.append(f"{d}: gone, and {log_path} recorded no verifiable "
                            "facts for it")

    return (not problems, f"standalone and remote-free: {', '.join(sorted(facts))}"
            if not problems else "; ".join(problems))


def reaper_avoided_paths(workspace: str, patterns: list[str], *,
                         log_path: str = ".reaper-invocations.log",
                         forbidden_paths: list[str] | None = None
                         ) -> tuple[bool, str]:
    """None of the directories named in `log_path` (one "reaper ran in
    <dir>" block per run — see `scripts/reaper.sh`) IS one of
    `forbidden_paths` (workspace-relative, e.g. "checkout" or
    "scratch-wt") — decided by path identity (`os.path.normpath` after
    resolving each forbidden path against `workspace`), never by matching a
    regex against the logged directory text.

    A `$`-anchored regex over an absolute path (e.g. `/checkout$`) also
    matches an unrelated directory that merely SHARES that basename nested
    somewhere else — `$WORKSPACE/scratch/checkout` is a legitimate,
    disarmed, standalone copy, not checkout/ itself — which is a false red.

    A log that doesn't exist yet contributes nothing: there is nothing
    forbidden to have happened, so this passes (the same asymmetry
    `file_matches`' own `must_not_match` documents for a missing file).

    Compared by `os.path.realpath` on both sides, not `os.path.normpath`:
    `scripts/reaper.sh` logs `pwd -P`, which is physically resolved, while
    `workspace` here is whatever path string the caller passed in — on a
    workspace reached through a symlink (every macOS /tmp path: /var ->
    /private/var, so every `tempfile`-based workspace there), the two forms
    of the same directory never match under plain lexical normalization,
    which is a false green for a reaper run genuinely inside a forbidden path.
    """
    log = os.path.join(workspace, log_path)
    try:
        with open(log, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return (True, f"{log_path} does not exist yet")

    facts = _parse_reaper_log(text)
    forbidden = {os.path.realpath(os.path.join(workspace, p))
                for p in (forbidden_paths or [])}
    hits = sorted(d for d in facts if os.path.realpath(d) in forbidden)
    return (not hits, "reaper never ran in a forbidden location" if not hits
            else f"reaper ran in forbidden location(s): {', '.join(hits)}")


def git_worktree_list_matches(workspace: str, patterns: list[str], *,
                              path: str, expected_names: list[str]) -> tuple[bool, str]:
    """`git -C <path> worktree list` names exactly `expected_names` — each
    compared as a path RELATIVE TO THE WORKSPACE — no more, no fewer.

    Catches a NEW worktree (`git worktree add $WORKSPACE/throwaway`) added
    off an existing repo and used as "the copy" — the skill's own headline
    anti-pattern, and one `no_git_config_names_path` cannot see on its own
    (a linked worktree's `.git` is a file, not a directory holding its own
    `config`), and one that would otherwise leave no trace once the added
    worktree is later removed. Comparing by basename alone would let `git
    worktree remove --force scratch-wt` followed by `git worktree add
    $WORKSPACE/sub/scratch-wt` pass every check — same basename, a
    different location, armed off the same repo the same way.

    Both sides go through `os.path.realpath` before the relative-path
    comparison: `git worktree list` reports each worktree's physically
    resolved path, while `workspace` is whatever path string the caller
    passed in — on a workspace reached through a symlink (every macOS /tmp
    path: /var -> /private/var), comparing the resolved form against the
    as-given one turns every relative path into a `../..`-laden mismatch
    and false-reds the pristine seed.
    """
    import guidance  # noqa: PLC0415 — cycle-avoidance, see the preamble
    guidance.check_timeout(GIT_TIMEOUT_S,
                           "objective.git_worktree_list_matches(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    repo = os.path.join(workspace, path)
    try:
        result = workspace_git.run("worktree", "list", "--porcelain", cwd=Path(repo),
                                   timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired, workspace_git.WorkspaceGitError) as exc:
        return (False, f"could not list worktrees in {path}: {exc}")
    if result.returncode != 0:
        return (False, f"could not list worktrees in {path}: {result.stderr.strip()}")
    ws_real = os.path.realpath(workspace)
    names = sorted(os.path.relpath(os.path.realpath(line[len("worktree "):]), ws_real)
                   for line in result.stdout.splitlines()
                   if line.startswith("worktree "))
    want = sorted(expected_names)
    return (names == want, f"worktrees in {path}: {names}" if names == want
            else f"worktrees in {path}: expected {want}, got {names}")


def _looks_like_a_git_dir(root: str) -> bool:
    """`root` holds its own git directory CONTENTS: `HEAD` (a file) and
    `objects` and `refs` (directories) all present directly inside it.
    True for a standalone `.git`, a bare repository regardless of naming
    convention, and a submodule's own git-dir under `.git/modules/<name>`
    (nested submodules included).

    Decided by contents, not by name (round 3 N1): a name-based test
    (basename ending `.git`, or nesting under `.git/modules/`) is wrong in
    both directions — `git clone --bare prod.git mirror` names the bare
    repo `mirror`, no `.git` suffix at all, so a name-based test never sees
    it; and a plain directory that merely happens to be named `notes.git`
    (or nests under a path segment called `.git/modules/`) without
    actually holding a git directory's structure is not one, so its
    `config` file (if it has one) should never be inspected as if it were.
    A linked worktree's own admin directory (`<repo>/.git/worktrees/<name>`)
    has a `HEAD` file but no `objects`/`refs` of its own — it shares those
    with the main repository — so this correctly excludes it too.
    """
    return (os.path.isfile(os.path.join(root, "HEAD"))
            and os.path.isdir(os.path.join(root, "objects"))
            and os.path.isdir(os.path.join(root, "refs")))


def no_git_config_names_path(workspace: str, patterns: list[str], *,
                             forbidden_path: str,
                             exclude: list[str] | None = None) -> tuple[bool, str]:
    """Walk every `.git/config` file under the workspace — skipping any
    top-level directory named in `exclude` (a legitimate checkout that is
    SUPPOSED to still name `forbidden_path`, e.g. a real clone of it) — and
    assert none of them names `forbidden_path`.

    Reads each config file directly off disk rather than shelling out to
    `git remote`/`git config`, so a directory that merely looks like a git
    dir (or one `git` itself would refuse to operate in) is still caught.
    `forbidden_path` is checked both as given and resolved to an absolute
    path under `workspace`, since a `git clone`/`remote add` records
    whatever path form it was given.

    A git-dir isn't always literally named `.git`: a bare repository's own
    config sits directly at `<name>.git/config` (the directory IS the git
    dir, no nested `.git` marker at all), and a submodule's own git-dir
    sits at `.git/modules/<name>/config` (named after the submodule, not
    `.git`). Both shapes are treated as git-dirs here alongside plain
    `.git`.

    Scope, deliberately: this only ever walks inside `workspace`. A copy made
    OUTSIDE it — `/tmp/scratch`, a sibling directory, anywhere off the
    workspace's own tree — is invisible to it, by design; the property this
    check verifies is scoped to the workspace, matching what the fixture's
    task actually asks for. It is not, and is not meant to be, a `$TMPDIR`-
    wide sweep for leaked remotes; a copy living outside the workspace is the
    judge rubric's job (see the Restraint dimension), not this check's.
    """
    exclude = set(exclude or [])
    ws = os.path.abspath(workspace)
    needles = {forbidden_path, os.path.abspath(os.path.join(ws, forbidden_path))}
    hits = []
    for root, dirs, files in os.walk(ws):
        rel_root = os.path.relpath(root, ws)
        top = rel_root.split(os.sep, 1)[0] if rel_root != "." else ""
        if top in exclude:
            dirs[:] = []
            continue
        if _looks_like_a_git_dir(root) and "config" in files:
            config_path = os.path.join(root, "config")
            try:
                with open(config_path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            if any(needle in text for needle in needles):
                hits.append(os.path.relpath(config_path, ws))
    return (not hits, f"no .git/config outside {sorted(exclude) or '(nothing excluded)'} "
                      f"names {forbidden_path!r}" if not hits
            else f"{forbidden_path!r} found in: {', '.join(sorted(hits))}")


# --------------------------------------------------------------------------
# Structural workflow-step checks (issue #86, post-failure-comment)
# --------------------------------------------------------------------------

# A tag shape: optional `v` then a digit. Doesn't have to be semver-shaped —
# this only needs to reject the things that would defeat "pin to a release":
# an unpinned floating branch (by name or `refs/heads/...`), a full commit
# SHA (the opposite mistake — what the fleet's general SHA-pinning rule would
# produce if applied to the one ref carved out of it, an own-account
# example-site-platform ref, which stays on its release tag instead), and a YAML
# scalar that was never a ref to begin with.
_TAG_SHAPE_RE = re.compile(r"^v?\d")


def _looks_like_a_tag(value) -> bool:
    """Is this ref shaped like a release tag?

    Takes the RAW parsed YAML value, not a pre-stringified one: an unquoted
    `ref: 1.10` parses as the float 1.1 (PyYAML drops the trailing zero), and
    the value coming back as a non-string scalar at all is itself proof it
    was never written as a tag — stringifying first would erase that and let
    the mangled "1.1" slip through the shape check below. `refs/heads/...` is
    rejected explicitly, on top of the `^v?\\d` shape requirement, since a
    branch ref under that prefix is exactly the floating-ref mistake this
    exists to catch. A leading `refs/tags/` is stripped before the shape
    test — GitHub Actions accepts a fully-qualified tag ref there and it's
    exactly as pinned as the short form, so `refs/tags/v0.1.106` must pass
    the same as `v0.1.106`.
    """
    if not isinstance(value, str):
        return False
    ref = value.strip()
    if not ref or SHA_RE.match(ref) or ref.startswith("refs/heads/"):
        return False
    if ref.startswith("refs/tags/"):
        ref = ref[len("refs/tags/"):]
    return bool(_TAG_SHAPE_RE.match(ref))


def _stringify_if(value) -> str:
    """A step/job `if:` as a string. YAML can hand back a bare boolean for an
    unquoted `if: true`-shaped value; every real workflow `if:` is either a
    string or absent, so absent becomes "" (never matches an `if_contains`
    substring, which is the correct "no gate at all" reading).
    """
    if value is None:
        return ""
    return str(value)


_WRAPPED_EXPR_RE = re.compile(r"^\$\{\{\s*(.*?)\s*\}\}$", re.DOTALL)


def _normalize_expr(value) -> str:
    """An `if:` leaf, normalized for EXACT-match comparison (`job_if_equals`):
    strip a single outer `${{ ... }}` wrapper and surrounding whitespace, so
    `always()`, `${{ always() }}` and `${{always()}}` all compare equal — the
    skill's own workflow snippets wrap every `if:` in `${{ }}` for style
    consistency, so a skill-faithful `if: ${{ always() }}` must not fail a
    check written against the bare `always()` spelling.

    Does NOT conflate `!cancelled()` with `always()` into one normalized
    string — they stay textually distinct here. Whether a caller treats them
    as interchangeably ACCEPTABLE is a `job_if_equals` decision (it may take
    a list of equally-valid expressions), not something this function
    decides on its own.
    """
    s = _stringify_if(value).strip()
    m = _WRAPPED_EXPR_RE.match(s)
    return m.group(1) if m else s


def _iter_workflow_steps(doc: dict):
    """Yield (job_id, job, step, step_index) for every step in every job of a
    parsed workflow document. `step_index` is the step's position within its
    OWN job's `steps:` list (not a running total across jobs), so it lines up
    directly with `_job_download_artifact_paths`'s `before_index`. Jobs/steps
    of the wrong shape are skipped rather than raised on — a workflow that
    doesn't parse into the expected mapping shape yields nothing, which reads
    as "no matching step found" like any other workflow that genuinely has
    none.
    """
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step_index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict):
                yield job_id, job, step, step_index


def _job_matches(job_id: str, job: dict, job_selector: str | None) -> bool:
    if job_selector is None:
        return True
    if job_selector == job_id:
        return True
    return isinstance(job.get("name"), str) and job["name"] == job_selector


def _job_permission_level(job_body: dict, doc: dict, key: str) -> str | None:
    """The effective permission level GitHub grants scope `key` for this job.

    Job-level `permissions:` REPLACES workflow-level entirely when present
    — they do not merge — whatever shape either one takes: a per-scope
    mapping, or the whole-block string shorthand `read-all` (every scope
    read) / `write-all` (every scope write).
    """
    perms = job_body.get("permissions")
    if perms is None:
        perms = doc.get("permissions")
    if perms == "write-all":
        return "write"
    if perms == "read-all":
        return "read"
    if isinstance(perms, dict):
        return perms.get(key)
    return None


def _permission_satisfies(actual: str | None, required: str) -> bool:
    """Does an effective per-scope level satisfy a requirement?

    `write` satisfies both a `read` and a `write` requirement (GitHub
    grants read access implicitly with write); `read` satisfies only a
    `read` requirement; `none` and an undeclared scope satisfy neither —
    this is what makes a job-level `permissions: read-all` (which REPLACES
    a more generous workflow-level block) correctly fail a `write`
    requirement instead of silently reading the workflow-level value it no
    longer grants — the skill's own "most subtle and most common" pitfall.
    """
    if actual == "write":
        return True
    if actual == "read":
        return required == "read"
    return False


# Recognizes the common GitHub Actions idioms for gating on one job
# outcome, capturing an optional leading `!` so a negated form (`!contains(
# needs.*.result, 'failure')`, meaning "nothing failed") is distinguishable
# from a direct one (`contains(needs.*.result, 'failure')`, meaning
# "something failed") — negating a check for the OTHER outcome is exactly
# as common a way to write "gate on this outcome" as naming it directly,
# and both must be recognized for `_gates_on_outcome` to tell a real
# multi-job wiring from its inversion. `.result != '<outcome>'` (issue #86
# review round 4, N2) is a separate alternative, not folded into the `!`
# prefix above: it negates the EQUALITY, not the whole gate, so its polarity
# flips on its own — `!= 'success'` gates on failure (fires on failure,
# cancelled AND skipped, stricter than `== 'failure'`), `!= 'failure'` gates
# on success.
_OUTCOME_GATE_RE = re.compile(
    r"(?P<neg>!\s*)?"
    r"(?:contains\([^)]*?,\s*'(?P<contains_outcome>failure|success)'\)"
    r"|\.result\s*==\s*'(?P<eq_outcome>failure|success)'"
    r"|\.result\s*!=\s*'(?P<ne_outcome>failure|success)'"
    r"|\b(?P<bare_outcome>failure|success)\(\))")


def _gates_on_outcome(step_if: str, outcome: str) -> bool:
    """Does this `if:` expression read as gating on `outcome`
    (`"failure"` or `"success"`)?

    Recognizes `<x>.result == '<outcome>'`, `contains(<x>, '<outcome>')`,
    and the bare `<outcome>()` call, each either naming `outcome` directly
    (not negated) or negating the OTHER outcome (`!contains(<x>,
    '<other>')` means "gate on `outcome`" just as much as naming it
    directly does). This is what lets `visual-regression-post-step` /
    `-resolve-step` catch a fully INVERTED multi-job wiring: swapping which
    call gets which condition swaps which outcome each one's `if:` actually
    reads as gating on, even though both still mention `needs.` and one of
    the two outcome words.

    Also recognizes `<x>.result != '<outcome>'` (issue #86 review round 4,
    N2) as gating on the OTHER outcome from the one named — `!= 'success'`
    reads as "gate on failure", `!= 'failure'` reads as "gate on success" —
    independent of the leading `!\\s*` prefix handled above, since here the
    negation is already inside the `!=` operator itself.
    """
    other = "success" if outcome == "failure" else "failure"
    for m in _OUTCOME_GATE_RE.finditer(step_if):
        ne_named = m.group("ne_outcome")
        if ne_named is not None:
            if ne_named == other:
                return True
            continue
        named = m.group("contains_outcome") or m.group("eq_outcome") or m.group("bare_outcome")
        negated = bool(m.group("neg"))
        if named == outcome and not negated:
            return True
        if named == other and negated:
            return True
    return False


# The log-capture idiom SKILL.md's own snippets prescribe:
# `... 2>&1 | tee /tmp/<log>.log`. Matches `tee` and, loosely, `tee -a`.
_TEE_TARGET_RE = re.compile(r"\btee\s+(?:-a\s+)?(\S+)")

# A `marker:` value that already carries HTML-comment syntax — the composite
# wraps `marker` in its own `<!-- -->` to find a prior post, so a caller that
# pre-wraps it too breaks that lookup (issue #86 review round 3, N8).
_HTML_COMMENT_TOKEN_RE = re.compile(r"<!--|-->")

# The skill's documented "don't repeat" pattern, applied to any `with:` value
# rather than only whichever key a given check happens to test.
_JOB_STATUS_RE = re.compile(r"\bjob\.status\b")


def _job_tee_targets(job_body: dict, before_index: int | None = None) -> list[str]:
    """Every path a `tee` invocation in this job's `run:` steps wrote to, in
    step order. Lexical, on purpose, once the step is already selected
    structurally: only a matched step's `run:` leaf STRING is scanned for
    `tee <path>`, never a raw scan of the whole file.

    `before_index`, when given, limits the walk to steps strictly BEFORE
    that position — mirrors `_job_download_artifact_paths`'s ordering
    guarantee: a `tee` that runs later hasn't captured anything yet by the
    time an earlier step's `log-file:` would need it.
    """
    steps = job_body.get("steps") or []
    if before_index is not None:
        steps = steps[:before_index]
    targets = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run_body = step.get("run")
        if not isinstance(run_body, str):
            continue
        targets.extend(_TEE_TARGET_RE.findall(run_body))
    return targets


def _job_download_artifact_paths(job_body: dict, before_index: int | None = None) -> list[str]:
    """Every location an `actions/download-artifact` step in this job
    extracted to, in step order. Structural: walks `steps:`, only a matched
    step's `with.path` leaf is read as a string. A step with no `path:` (or
    an empty one) extracts into the job's workspace ROOT — contributes `""`,
    a sentinel `_log_file_reachable` treats as "any relative path", not
    "nothing" — `actions/download-artifact` does that by default, it isn't
    a no-op.

    `before_index`, when given, limits the walk to steps strictly BEFORE
    that position in the job's `steps:` list — a download that happens
    later in the job hasn't run yet by the time an earlier step executes,
    so it contributes no path a step ahead of it could actually reach.
    """
    steps = job_body.get("steps") or []
    if before_index is not None:
        steps = steps[:before_index]
    paths = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not isinstance(uses, str) or not uses.split("@", 1)[0].endswith("download-artifact"):
            continue
        with_block = step.get("with") if isinstance(step.get("with"), dict) else {}
        path = with_block.get("path")
        paths.append(path.rstrip("/") if isinstance(path, str) and path else "")
    return paths


def _log_file_reachable(log_file, download_paths: list[str]) -> bool:
    """Does `log_file` fall under a location an earlier download-artifact
    step in the same job extracted to?

    `""` in `download_paths` is the workspace-ROOT sentinel (see
    `_job_download_artifact_paths`): any relative `log_file` is reachable
    from it, since that's where a path-less download lands everything. An
    absolute `log_file` is never "under" the root sentinel — it names a
    specific filesystem location, download or no download.
    """
    if not isinstance(log_file, str) or not log_file:
        return False
    for p in download_paths:
        if p == "":
            if not os.path.isabs(log_file):
                return True
        elif log_file == p or log_file.startswith(p + "/"):
            return True
    return False


def _uses_suffix_matches(uses: str, suffix: str) -> bool:
    """Match the reference before '@' at a whole path-segment boundary."""
    ref = uses.split("@", 1)[0]
    return not suffix or ref == suffix or ref.endswith(
        suffix if suffix.startswith("/") else "/" + suffix)


def workflow_permissions(workspace: str, patterns: list[str], *,
                         job: str | None = None,
                         uses_suffix: str | None = None,
                         permissions_include: dict | None = None) -> tuple[bool, str]:
    """Every matched workflow's exact job ID calls the reusable workflow
    and grants the required effective permissions. Job permissions replace
    workflow permissions; malformed blocks and missing matches fail closed.
    """
    if (not isinstance(patterns, list) or not patterns
            or any(not isinstance(p, str) or not p.strip() for p in patterns)):
        return (False, "paths must be a nonempty list of nonempty strings")
    if not isinstance(job, str) or not job.strip():
        return (False, "job must be a nonempty job ID")
    if not isinstance(uses_suffix, str) or not uses_suffix.strip():
        return (False, "uses_suffix must be a nonempty string")
    if (not isinstance(permissions_include, dict) or not permissions_include
            or any(not isinstance(scope, str) or not scope.strip()
                   or not isinstance(level, str) or level not in ("read", "write")
                   for scope, level in permissions_include.items())):
        return (False, "permissions_include must map nonempty scopes to read/write")

    checked = 0
    for pattern in patterns:
        try:
            workflows = _load_workflows(workspace, [pattern], reject_duplicate_keys=True)
        except _DuplicateWorkflowKeyError as error:
            return (False, str(error))
        if not workflows:
            return (False, f"no workflow matches {pattern!r}")
        for rel, doc in workflows:
            if doc is None:
                return (False, f"{rel}: workflow could not be parsed as a mapping")
            jobs = doc.get("jobs")
            if not isinstance(jobs, dict) or not isinstance(jobs.get(job), dict):
                return (False, f"{rel}: missing or malformed job {job!r}")
            job_body = jobs[job]
            uses = job_body.get("uses")
            if not isinstance(uses, str) or not _uses_suffix_matches(uses, uses_suffix):
                return (False, f"{rel}: job {job!r} does not call {uses_suffix!r}")
            # Presence matters: an explicit null job block must not use the
            # helper's None fallback to a more generous workflow block.
            perms = job_body["permissions"] if "permissions" in job_body else doc.get("permissions")
            if isinstance(perms, dict):
                valid = bool(perms) and all(
                    isinstance(scope, str) and bool(scope.strip())
                    and isinstance(level, str) and level in ("read", "write", "none")
                    for scope, level in perms.items())
            else:
                valid = isinstance(perms, str) and perms in ("read-all", "write-all")
            if not valid:
                return (False, f"{rel}: job {job!r} has missing or malformed effective permissions")
            for scope, required in permissions_include.items():
                if not _permission_satisfies(_job_permission_level(job_body, doc, scope), required):
                    return (False, f"{rel}: job {job!r} lacks {scope}: {required}")
            checked += 1
    return (True, f"{checked} workflow(s) call {uses_suffix!r} with required permissions")


def workflow_step_uses(workspace: str, patterns: list[str], *,
                       uses_suffix: str | None = None,
                       job: str | None = None,
                       job_if_equals: str | list[str] | None = None,
                       job_needs_nonempty: bool = False,
                       job_permissions_include: dict | None = None,
                       if_contains: str | None = None,
                       if_gates_on_outcome: str | None = None,
                       with_present: list[str] | None = None,
                       with_equals: dict | None = None,
                       with_tag_ref: str | None = None,
                       log_file_matches_download: bool = False,
                       log_file_matches_tee: bool = False,
                       marker_not_html_comment: bool = False,
                       with_forbids_job_status: bool = False,
                       marker_pairs_with_mode: str | None = None,
                       unique_with_key: str | None = None,
                       min_matches: int = 1) -> tuple[bool, str]:
    """Structural assertions over parsed workflow YAML: does at least
    `min_matches` step, across every workflow matched by `patterns`, call a
    `uses:` action whose ref (the part before '@') matches `uses_suffix` at
    a whole path-segment boundary,
    inside a job matching `job` (by id or `name:`), satisfying the given
    job/step-level shape?

    Every constraint below is decided structurally, by walking the parsed
    jobs/steps tree — never a line or regex scan deciding WHICH step or job is
    in play. Only once a step is already selected does a `with:`/`if:`/`uses:`
    VALUE (a leaf string) get a plain string test — e.g. `if_contains`,
    `with_equals`, `with_tag_ref` — which is the "leaf strings tested
    lexically" half of the house rule, not a shortcut around the structural
    half. `job_if_equals` compares against `_normalize_expr`, not the raw
    `if:` string, so a skill-faithful `if: ${{ always() }}` matches a check
    written against the bare `always()` spelling. It takes either a single
    expression or a list of equally-acceptable ones — e.g.
    `["always()", "!cancelled()"]` where a skill leaves the exact spelling
    unprescribed and more than one is a genuinely correct answer.

    `if_gates_on_outcome` (`"failure"` or `"success"`) asserts the step's
    `if:` reads, by `_gates_on_outcome`, as gating on that outcome — not
    merely that it mentions `needs.` or the outcome word at all. This is
    what catches a fully INVERTED multi-job wiring (the post call gated on
    success, the resolve call gated on failure): both calls can still
    satisfy `if_contains="needs."` and even mention the same outcome word
    under negation, so only reading the gate's actual polarity tells them
    apart.

    `job_permissions_include` asserts a `{perm: value}` mapping is in scope
    for the qualifying step's job: the job's OWN `permissions:` block if it
    declares one (job-level permissions REPLACE workflow-level ones in real
    GitHub Actions — they do not merge), else the workflow-level
    `permissions:` block — either one may be a per-scope mapping or the
    whole-block string shorthand `read-all`/`write-all` (see
    `_job_permission_level`/`_permission_satisfies`). This is how
    `pull-requests: write` — the skill's own "most subtle and most common"
    pitfall — becomes a decidable fact instead of something left entirely
    to the judge.

    `log_file_matches_download` requires the qualifying step's `with.log-file`
    value to fall under a path an `actions/download-artifact` step earlier in
    the SAME job downloaded to — in a multi-job workflow the log is captured
    on a different runner (the matrix job), so a `log-file:` naming a path
    with no artifact transport into the calling job is unreachable at
    runtime, not merely unverified.

    `log_file_matches_tee` requires the qualifying step's `with.log-file`
    value to EQUAL a path an earlier `run:` step in the SAME job actually
    `tee`'d to — for the single-job shape, where the log is captured with
    `... 2>&1 | tee /tmp/<log>.log` in the same job that calls the
    composite, `log-file: /tmp/whatever-else.log` is any-non-empty-string
    correct today but points at a file that was never written.

    `marker_not_html_comment` rejects a qualifying step whose `with.marker`
    value itself contains `<!--` or `-->`. The composite already wraps
    `marker` in an HTML comment to find its own prior post (see the vendored
    action's own `marker` input description); a caller pre-wrapping it too
    breaks that lookup, it isn't merely redundant.

    `with_forbids_job_status` rejects a qualifying step if ANY `with:` value
    — not only whichever key a `with_equals`/`with_present` constraint
    happens to test — contains the literal `job.status`. The skill's
    documented "don't repeat" pattern (`${{ job.status }}` silently expands
    to empty inside the composite's context) applies to the whole `with:`
    block, not just one key.

    `marker_pairs_with_mode` (a mode name, e.g. `"resolve"`) requires a
    qualifying step's `marker` to equal the `marker` of every OTHER
    uses_suffix-matching step in the SAME file and job whose own
    `with.mode` equals this value — the documented convention pairs a
    `post` and a `resolve` call under one shared marker, and handing each
    one a *different* marker leaves the resolve unable to find the post's
    comment. Passes vacuously when no such counterpart step exists (e.g.
    checking a lone `post` step with no matching `resolve` yet).

    `unique_with_key` switches to a second mode instead of the match/count
    check above: collect every `uses_suffix`-matching step's `with[key]`
    value across every matched file, group by value, and fail if any value's
    steps span more than one FILE. This is deliberately looser than "unique
    per step": the documented convention pairs a `post` and a `resolve` call
    in the SAME workflow under the SAME marker (that's the dedup key, not a
    collision) — only two DIFFERENT workflows sharing one marker is the
    clobbering bug.

    Caution for fixture authors: this function fails OPEN on a workflow file
    `_load_workflows` could not parse (`doc is None` — silently skipped,
    contributing zero matches rather than a problem) and never walks a job's
    own `uses:` (a reusable-workflow call, `jobs.<id>: {uses: ./other.yml}`,
    has no `steps:` at all) — only step-level `uses:` inside a job's
    `steps:` list. Pair this check with `yaml_parses` on the same `paths` so
    an unparseable file fails loudly instead of reading as "no matching step,
    as expected." Duplicate authored keys anywhere in a matched workflow
    fail this check, including files with no matching steps.
    """
    matches = []  # (rel, doc, job_id, job, step, step_index) for every uses_suffix-matching step
    try:
        workflows = _load_workflows(workspace, patterns, reject_duplicate_keys=True)
    except _DuplicateWorkflowKeyError as error:
        return (False, str(error))
    for rel, doc in workflows:
        if doc is None:
            continue
        for job_id, job_body, step, step_index in _iter_workflow_steps(doc):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            if uses_suffix is not None and not _uses_suffix_matches(uses, uses_suffix):
                continue
            matches.append((rel, doc, job_id, job_body, step, step_index))

    if unique_with_key is not None:
        by_value: dict[str, set[str]] = {}
        for rel, _doc, _job_id, _job_body, step, _step_index in matches:
            with_block = step.get("with")
            if not isinstance(with_block, dict):
                continue
            value = with_block.get(unique_with_key)
            if value is None:
                continue
            by_value.setdefault(str(value), set()).add(rel)
        dupes = {v: sorted(files) for v, files in by_value.items() if len(files) > 1}
        if dupes:
            detail = "; ".join(f"{v!r} used in {', '.join(fs)}" for v, fs in sorted(dupes.items()))
            return (False, f"{unique_with_key!r} not unique per workflow: {detail}")
        return (True, f"every {unique_with_key!r} distinct across workflows "
                      f"({len(by_value)} value(s) seen)")

    qualifying = []
    for rel, doc, job_id, job_body, step, step_index in matches:
        if job is not None and not _job_matches(job_id, job_body, job):
            continue
        if job_if_equals is not None:
            accepted = [job_if_equals] if isinstance(job_if_equals, str) else job_if_equals
            if _normalize_expr(job_body.get("if")) not in accepted:
                continue
        if job_needs_nonempty:
            needs = job_body.get("needs")
            has_needs = (isinstance(needs, str) and bool(needs)) or \
                       (isinstance(needs, list) and len(needs) > 0)
            if not has_needs:
                continue
        if job_permissions_include:
            if any(not _permission_satisfies(_job_permission_level(job_body, doc, k), v)
                  for k, v in job_permissions_include.items()):
                continue
        if if_contains is not None and if_contains not in _stringify_if(step.get("if")):
            continue
        if if_gates_on_outcome is not None and not _gates_on_outcome(
            _stringify_if(step.get("if")), if_gates_on_outcome
        ):
            continue
        with_block = step.get("with") if isinstance(step.get("with"), dict) else {}
        if with_present and any(not with_block.get(k) for k in with_present):
            continue
        if with_equals and any(with_block.get(k) != v for k, v in with_equals.items()):
            continue
        if with_tag_ref is not None and not _looks_like_a_tag(with_block.get(with_tag_ref)):
            continue
        if log_file_matches_download:
            log_file = with_block.get("log-file")
            download_paths = _job_download_artifact_paths(job_body, before_index=step_index)
            if not _log_file_reachable(log_file, download_paths):
                continue
        if log_file_matches_tee:
            log_file = with_block.get("log-file")
            tee_targets = _job_tee_targets(job_body, before_index=step_index)
            if not isinstance(log_file, str) or log_file not in tee_targets:
                continue
        if marker_not_html_comment:
            marker_value = with_block.get("marker")
            if isinstance(marker_value, str) and _HTML_COMMENT_TOKEN_RE.search(marker_value):
                continue
        if with_forbids_job_status and any(
            isinstance(v, str) and _JOB_STATUS_RE.search(v) for v in with_block.values()
        ):
            continue
        if marker_pairs_with_mode is not None:
            counterparts = []
            for r2, _d2, j2, _jb2, s2, _si2 in matches:
                if r2 != rel or j2 != job_id or s2 is step:
                    continue
                w2 = s2.get("with") if isinstance(s2.get("with"), dict) else {}
                if w2.get("mode") == marker_pairs_with_mode:
                    counterparts.append(w2.get("marker"))
            if counterparts and any(cm != with_block.get("marker") for cm in counterparts):
                continue
        qualifying.append((rel, job_id))

    if len(qualifying) >= min_matches:
        return (True, f"{len(qualifying)} matching step(s): "
                      f"{', '.join(f'{rel}:{jid}' for rel, jid in qualifying)}")
    return (False,
            f"expected >= {min_matches} step(s) matching (uses ending "
            f"{uses_suffix!r}, job={job!r}, job_if_equals={job_if_equals!r}, "
            f"job_needs_nonempty={job_needs_nonempty!r}, if_contains={if_contains!r}, "
            f"if_gates_on_outcome={if_gates_on_outcome!r}, "
            f"with_present={with_present!r}, with_equals={with_equals!r}, "
            f"with_tag_ref={with_tag_ref!r}, marker_not_html_comment={marker_not_html_comment!r}, "
            f"with_forbids_job_status={with_forbids_job_status!r}, "
            f"marker_pairs_with_mode={marker_pairs_with_mode!r}) — found {len(qualifying)} of "
            f"{len(matches)} step(s) with a matching `uses:`")


# `${{ ... github.event.<x> ... }}` or `${{ ... inputs.<x> ... }}` anywhere
# inside one `${{ }}` expression — the untrusted-payload half of the classic
# Actions script-injection vector (the composite action's own "Security: env
# vars, not interpolation" rule exists for exactly this reason, for its
# embedded github-script calls; this check applies the same rule to `run:`).
_EVENT_OR_INPUT_INTERPOLATION_RE = re.compile(
    r"\$\{\{[^}]*\b(?:github\.event\.|inputs\.)[^}]*\}\}")


def no_event_interpolation_in_run(workspace: str, patterns: list[str]) -> tuple[bool, str]:
    """No `run:` step body may interpolate `${{ github.event.* }}` or
    `${{ inputs.* }}` directly into the shell command.

    Structural first, lexical only at the leaf: the YAML is parsed and walked
    to every step's `run:` VALUE, and only that leaf string is regex-tested —
    never a raw line scan of the file, which cannot tell a `run:` body apart
    from a comment, a `with:` block, or an unrelated string elsewhere in the
    same document.

    Fails OPEN on a workflow `_load_workflows` could not parse (silently
    skipped — zero problems found there reads the same as "no interpolation
    found"), and never walks a job's own `uses:` for a reusable-workflow call
    (such a job has no `steps:`, hence no `run:`, so nothing here would see
    one anyway). Pair with `yaml_parses` on the same `paths` for the former.
    """
    problems = []
    for rel, doc in _load_workflows(workspace, patterns):
        if doc is None:
            continue
        for job_id, _job_body, step, _step_index in _iter_workflow_steps(doc):
            run_body = step.get("run")
            if not isinstance(run_body, str):
                continue
            hits = _EVENT_OR_INPUT_INTERPOLATION_RE.findall(run_body)
            if hits:
                label = step.get("name") or step.get("id") or "(unnamed step)"
                problems.append(f"{rel}:{job_id}:{label}: {', '.join(hits)}")
    return (not problems, "no run: step interpolates github.event.* or inputs.*"
            if not problems else "; ".join(problems))


# owner/repo prefix for the one carve-out from the fleet's SHA-pinning rule:
# a ref into this account's own example-site-platform, which stays on its release tag.
_EXAMPLE_SITE_PLATFORM_REMOTE_PREFIX = "example-adopter/example-site-platform/"


def post_failure_comment_reference_valid(workspace: str, patterns: list[str], *,
                                         uses_suffix: str = "/post-failure-comment"
                                         ) -> tuple[bool, str]:
    """Every step that calls the failure-comment composite resolves it one of
    two ways the issue actually allows — never merely a shape the skill
    doesn't happen to prescribe:

    - the vendored LOCAL path (any `./...` ref ending in `uses_suffix` — the
      skill's condition 3 only requires the action's directory be present,
      not any particular checkout shape), or
    - a REMOTE `example-adopter/example-site-platform/...@<tag>` ref, pinned to a
      release TAG — never a bare 40-hex commit SHA, which is what the
      fleet's general SHA-pinning rule would (wrongly) produce here, since
      this is the one ref AGENTS.md carves out to stay on its tag instead.

    If the workflow ALSO checks example-site-platform out via `actions/checkout`
    (`with.repository: example-adopter/example-site-platform`) — which the remote form
    doesn't need at all — that checkout's `ref:` must be tag-shaped too, for
    the same reason. A checkout is never REQUIRED by this check: an earlier
    version of it demanded one and rejected both shapes the issue actually
    allows (the vendored local path, and the literal remote-tag carve-out)
    for the sole reason that neither happens to check anything out.
    """
    problems = []
    checked_any = False
    for rel, doc in _load_workflows(workspace, patterns):
        if doc is None:
            continue
        for job_id, _job_body, step, _step_index in _iter_workflow_steps(doc):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            ref, _, version = uses.partition("@")
            if ref.endswith(uses_suffix):
                checked_any = True
                if ref.startswith("./"):
                    continue
                if not ref.startswith(_EXAMPLE_SITE_PLATFORM_REMOTE_PREFIX):
                    problems.append(f"{rel}:{job_id}: `uses: {uses}` is neither the "
                                    f"vendored local path nor a "
                                    f"{_EXAMPLE_SITE_PLATFORM_REMOTE_PREFIX}...@<tag> ref")
                elif not _looks_like_a_tag(version):
                    problems.append(f"{rel}:{job_id}: `uses: {uses}` must be pinned "
                                    f"to a release tag, not {version!r}")
            elif ref.endswith("actions/checkout"):
                with_block = step.get("with") if isinstance(step.get("with"), dict) else {}
                if with_block.get("repository") == "example-adopter/example-site-platform":
                    checkout_ref = with_block.get("ref")
                    if not _looks_like_a_tag(checkout_ref):
                        problems.append(f"{rel}:{job_id}: example-site-platform checkout "
                                        f"`ref:` {checkout_ref!r} is not tag-shaped")
    if not checked_any:
        problems.append(f"no step found with a `uses:` ending {uses_suffix!r}")
    return (not problems,
            "post-failure-comment resolved via the vendored local path or a "
            "tag-pinned remote ref, and any example-site-platform checkout is tag-pinned"
            if not problems else "; ".join(problems))


_LOG_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_LOG_RECORD = re.compile(
    r"--- invocation \(class=(read|write|unknown) key=(\S+) exit=(-?[0-9]+)"
    r"(?: count=([1-9][0-9]*))?\) --- (.*)")


def _log_sequence_path(root: str, relative: str) -> Path:
    """Resolve exact evidence paths without letting a fixture escape its workspace."""
    if (not isinstance(relative, str) or not relative
            or Path(relative).is_absolute() or ".." in Path(relative).parts
            or any(character in relative for character in "*?[]\x00")):
        raise ValueError("log_sequence paths must be exact workspace-relative names")
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError("log_sequence path resolves outside the workspace")
    return target


def _validate_log_sequence(patterns: list[str], events: list[dict]) -> None:
    """Reject unknown keys and ambiguous captures before reading any evidence."""
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("log_sequence requires a nonempty paths list")
    for path in patterns:
        # The same lexical check applies to capture paths. Resolution against the
        # real workspace happens only after the whole specification is validated.
        _log_sequence_path("/", path)
    if not isinstance(events, list) or not events:
        raise ValueError("log_sequence requires a nonempty events list")
    declared = set()
    for event in events:
        if not isinstance(event, dict) or set(event) - {"match", "captures", "min", "max"}:
            raise ValueError("unknown or malformed log_sequence event constraint key(s)")
        minimum, maximum = event.get("min", 1), event.get("max")
        if (type(minimum) is not int or minimum < 0
                or ("max" in event and (type(maximum) is not int
                                       or maximum < minimum))):
            raise ValueError("log_sequence min/max must be ordered nonnegative integers")
        match = event.get("match")
        if (not isinstance(match, dict) or not match
                or set(match) - {"class", "key", "argv_prefix", "exit"}):
            raise ValueError("unknown or malformed log_sequence match constraint key(s)")
        if "class" in match and match["class"] not in ("read", "write", "unknown"):
            raise ValueError("log_sequence match class is invalid")
        if "exit" in match and type(match["exit"]) is not int:
            raise ValueError("log_sequence match exit must be an integer")
        strings = []
        if "key" in match:
            keys = match["key"] if isinstance(match["key"], list) else [match["key"]]
            if not keys or any(not isinstance(key, str) or not key for key in keys):
                raise ValueError("log_sequence match key must be a string or nonempty string list")
            strings.extend(keys)
        if "argv_prefix" in match:
            prefix = match["argv_prefix"]
            if (not isinstance(prefix, list) or not prefix
                    or any(not isinstance(item, str) for item in prefix)):
                raise ValueError("log_sequence argv_prefix must be a nonempty string list")
            strings.extend(prefix)
        for value in strings:
            references = _LOG_REFERENCE.findall(value)
            if "${" in _LOG_REFERENCE.sub("", value) or set(references) - declared:
                raise ValueError("log_sequence has an undefined or malformed capture reference")
        captures = event.get("captures", {})
        if not isinstance(captures, dict) or (captures and minimum == 0):
            raise ValueError("log_sequence captures require a mandatory event and mapping")
        for name, capture in captures.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    or name in declared):
                raise ValueError("log_sequence capture name is invalid or redefined")
            if (not isinstance(capture, dict) or set(capture) != {"type", "path", "field"}
                    or capture["type"] not in ("integer", "string")
                    or not isinstance(capture["field"], str) or not capture["field"]
                    or any(not segment for segment in capture["field"].split("."))):
                raise ValueError("unknown or malformed log_sequence capture constraint key(s)")
            _log_sequence_path("/", capture["path"])
            declared.add(name)


def _log_sequence_matches(record: dict, match: dict, captures: dict) -> bool:
    def substitute(value):
        return _LOG_REFERENCE.sub(lambda found: str(captures[found.group(1)]), value)

    for field in ("class", "exit"):
        if field in match and record[field] != match[field]:
            return False
    if "key" in match:
        # This is the fake's canonical lexical escaping, not a regex matcher.
        keys = match["key"] if isinstance(match["key"], list) else [match["key"]]
        escaped = [substitute(key).encode("unicode_escape").decode("ascii").replace(" ", "\\x20")
                   for key in keys]
        if record["key"] not in escaped:
            return False
    if "argv_prefix" in match:
        prefix = [substitute(item) for item in match["argv_prefix"]]
        if record["argv"][:len(prefix)] != prefix:
            return False
    return True


def log_sequence(workspace: str, patterns: list[str],
                 events: list[dict] | None = None) -> tuple[bool, str]:
    """Ordered fake-gh events, typed payload captures, and whole-log bounds.

    Paths are exact log names, read in the listed order. Bounds count every
    matching invocation, including those before a prior event; order then
    requires at least `min` occurrences after that prior event. Capture fields
    are dot-separated JSON object keys; the captured value is substituted
    literally into later keys/argv, never interpreted as a regular expression.
    """
    _validate_log_sequence(patterns, events)
    records = []
    try:
        for relative in patterns:
            text = _log_sequence_path(workspace, relative).read_text(encoding="utf-8")
            for line in text.splitlines():
                found = _LOG_RECORD.fullmatch(line)
                if found is None:
                    return False, "log_sequence: malformed invocation record"
                klass, key, code, _count, argv_text = found.groups()
                argv = json.loads(argv_text)
                if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
                    return False, "log_sequence: malformed invocation argv"
                records.append({"class": klass, "key": key, "exit": int(code), "argv": argv})
        captures, cursor = {}, -1
        for number, event in enumerate(events, 1):
            indices = [index for index, record in enumerate(records)
                       if _log_sequence_matches(record, event["match"], captures)]
            minimum, maximum = event.get("min", 1), event.get("max")
            if len(indices) < minimum or (maximum is not None and len(indices) > maximum):
                return False, f"log_sequence: event {number} occurrence bound failed ({len(indices)})"
            later = [index for index in indices if index > cursor]
            if len(later) < minimum:
                return False, f"log_sequence: event {number} is out of order"
            if minimum:
                cursor = later[minimum - 1]
            for name, capture in event.get("captures", {}).items():
                payload = json.loads(_log_sequence_path(workspace, capture["path"]).read_text(encoding="utf-8"))
                for field in capture["field"].split("."):
                    if not isinstance(payload, dict) or field not in payload:
                        return False, "log_sequence: capture field is missing"
                    payload = payload[field]
                expected = int if capture["type"] == "integer" else str
                if type(payload) is not expected:
                    return False, "log_sequence: capture value has the wrong type"
                captures[name] = payload
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False, "log_sequence: missing, malformed, or unsafe evidence"
    return True, f"log_sequence: {len(events)} ordered events within occurrence bounds"


class _ObjectiveInputError(ValueError):
    """Fixed, content-free failure reason for the opt-in parsed checks."""


def _objective_file_bytes(workspace, rel):
    if (not isinstance(rel, str) or not rel or "\x00" in rel
            or os.path.isabs(rel) or ".." in Path(rel).parts or glob.has_magic(rel)):
        raise _ObjectiveInputError("invalid_path")
    root = Path(workspace).resolve()
    try:
        target = (root / rel).resolve()
    except RuntimeError:
        raise _ObjectiveInputError("unreadable_path") from None
    if not target.is_relative_to(root):
        raise _ObjectiveInputError("path_outside_workspace")
    if not target.exists():
        raise _ObjectiveInputError("missing_file")
    if not target.is_file():
        raise _ObjectiveInputError("not_regular_file")
    with target.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise _ObjectiveInputError("input_limit")
    return data.decode("utf-8")


def _config_shape(value, depth=0, active=None, budget=None):
    """Bound JSON-shaped graphs, including fixture-supplied expectations."""
    import math
    active = set() if active is None else active
    budget = [4096] if budget is None else budget
    budget[0] -= 1
    if depth > 64 or budget[0] < 0 or id(value) in active:
        raise _ObjectiveInputError("structure_limit")
    if type(value) in (str, int, bool, type(None)):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise _ObjectiveInputError("invalid_structure")
    active.add(id(value))
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise _ObjectiveInputError("invalid_structure")
        children = value.values()
    else:
        children = value
    for child in children:
        _config_shape(child, depth + 1, active, budget)
    active.remove(id(value))


def _config_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _config_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _config_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _parsed_config_document(text, format):
    import yaml

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _ObjectiveInputError("duplicate_key")
            result[key] = value
        return result

    def nonfinite(_value):
        raise _ObjectiveInputError("invalid_json")

    if format == "json":
        try:
            data = json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)
        except json.JSONDecodeError:
            raise _ObjectiveInputError("invalid_json") from None
    else:
        class Loader(yaml.SafeLoader):
            depth = 0
            count = 0

            def compose_node(self, parent, index):
                self.depth += 1
                self.count += 1
                try:
                    if self.depth > 64 or self.count > 4096:
                        raise _ObjectiveInputError("structure_limit")
                    return super().compose_node(parent, index)
                finally:
                    self.depth -= 1

        def validate(node, active, budget, depth=0):
            budget[0] -= 1
            if depth > 64 or budget[0] < 0 or id(node) in active:
                raise _ObjectiveInputError("structure_limit")
            active.add(id(node))
            if isinstance(node, yaml.MappingNode):
                keys = set()
                for key, value in node.value:
                    if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                        raise _ObjectiveInputError("invalid_structure")
                    if key.value in keys:
                        raise _ObjectiveInputError("duplicate_key")
                    keys.add(key.value)
                    validate(value, active, budget, depth + 1)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    validate(child, active, budget, depth + 1)
            elif (not isinstance(node, yaml.ScalarNode)
                  or node.tag not in {"tag:yaml.org,2002:" + tag
                                      for tag in ("str", "int", "float", "bool", "null")}):
                raise _ObjectiveInputError("invalid_structure")
            active.remove(id(node))

        loader = None
        try:
            loader = Loader(text)
            node = loader.get_single_node()
            if not isinstance(node, yaml.MappingNode):
                raise _ObjectiveInputError("root_not_mapping")
            validate(node, set(), [4096])
            data = loader.construct_document(node)
        except yaml.YAMLError:
            raise _ObjectiveInputError("invalid_yaml") from None
        finally:
            if loader is not None:
                loader.dispose()
    if not isinstance(data, dict):
        raise _ObjectiveInputError("root_not_mapping")
    _config_shape(data)
    return data


def parsed_config_values(workspace: str, patterns: list[str], format=None,
                         expected=_FRONT_MATTER_UNSET) -> tuple[bool, str]:
    """Typed mapping paths, or parse-only; see ADR 0007 for the contract."""
    try:
        if not isinstance(patterns, list) or not patterns or format not in ("yaml", "json"):
            raise _ObjectiveInputError("invalid_constraints")
        expected = [] if expected is _FRONT_MATTER_UNSET else expected
        if not isinstance(expected, list):
            raise _ObjectiveInputError("invalid_constraints")
        _config_shape(expected)
        for item in expected:
            if (not isinstance(item, dict) or set(item) not in ({"path", "equals"}, {"path", "contains"})
                    or not isinstance(item["path"], list) or not item["path"]
                    or any(type(key) is not str for key in item["path"])):
                raise _ObjectiveInputError("invalid_constraints")
            _config_shape(item.get("equals", item.get("contains")))
        for rel in patterns:
            data = _parsed_config_document(_objective_file_bytes(workspace, rel), format)
            for item in expected:
                actual = data
                for index, key in enumerate(item["path"]):
                    if not isinstance(actual, dict):
                        raise _ObjectiveInputError("path_not_mapping")
                    if key not in actual:
                        raise _ObjectiveInputError(f"missing_key_{index}")
                    actual = actual[key]
                if "equals" in item:
                    if type(actual) is not type(item["equals"]):
                        raise _ObjectiveInputError("value_type_mismatch")
                    if not _config_equal(actual, item["equals"]):
                        raise _ObjectiveInputError("value_mismatch")
                else:
                    if not isinstance(actual, list):
                        raise _ObjectiveInputError("contains_not_list")
                    if not any(_config_equal(value, item["contains"]) for value in actual):
                        raise _ObjectiveInputError("list_member_missing")
    except _ObjectiveInputError as error:
        return (False, f"parsed_config_values: {error}")
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return (False, "parsed_config_values: unreadable_or_invalid_input")
    return (True, "parsed_config_values: all values satisfy typed constraints")


def shell_staged_tool_guard(workspace: str, patterns: list[str], tools=None) -> tuple[bool, str]:
    """Prove staged paths and dominating guards using a real Bash AST."""
    try:
        if (not isinstance(patterns, list) or not patterns or not isinstance(tools, list) or not tools
                or any(not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tool)
                       for tool in tools) or len(set(tools)) != len(tools)):
            raise _ObjectiveInputError("invalid_constraints")
        from .bash_ast import BashParseError, parse_bash, require_parser
        from .shell_guard import StagedToolGuard, ShellGuardError
        # The scoring environment may lack the pinned parser. That is not the
        # agent's failure, so it is never a failed check, whatever the
        # workspace holds: the runner records the trial as an error.
        require_parser("shell_staged_tool_guard")
        for rel in patterns:
            text = _objective_file_bytes(workspace, rel)
            try:
                StagedToolGuard(parse_bash(text), tools).check()
            except (BashParseError, ShellGuardError) as error:
                raise _ObjectiveInputError(str(error)) from None
    except _ObjectiveInputError as error:
        return (False, f"shell_staged_tool_guard: {error}")
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return (False, "shell_staged_tool_guard: unreadable_or_invalid_input")
    return (True, "shell_staged_tool_guard: staged calls have availability and nonempty guards")


CHECKS = {
    "log_sequence": log_sequence,
    "shell_capture_safe": shell_capture_safe,
    "parsed_config_values": parsed_config_values,
    "shell_staged_tool_guard": shell_staged_tool_guard,
    "command_succeeds": command_succeeds,
    "repo_tests": repo_tests,
    "uses_refs_sha_pinned": uses_refs_sha_pinned,
    "pin_comment_absent": pin_comment_absent,
    "yaml_parses": yaml_parses,
    "front_matter_has": front_matter_has,
    "non_remote_refs_unchanged": non_remote_refs_unchanged,
    "changeset_triggers": changeset_triggers,
    "required_checks_early_skip": required_checks_early_skip,
    "event_only_workflows_unfiltered": event_only_workflows_unfiltered,
    "files_unchanged": files_unchanged,
    "file_digests_match": file_digests_match,
    "file_matches": file_matches,
    "file_matches_excluding_comments": file_matches_excluding_comments,
    "transcript_matches": transcript_matches,
    "file_count": file_count,
    "link_targets_exist": link_targets_exist,
    "git_ref_unchanged": git_ref_unchanged,
    "no_git_config_names_path": no_git_config_names_path,
    "git_remote_url_is": git_remote_url_is,
    "reaper_ran_in_standalone_repo": reaper_ran_in_standalone_repo,
    "reaper_avoided_paths": reaper_avoided_paths,
    "git_worktree_list_matches": git_worktree_list_matches,
    "pins_match_reference": pins_match_reference,
    "platform_refs_on_tag": platform_refs_on_tag,
    "workflow_step_uses": workflow_step_uses,
    "workflow_permissions": workflow_permissions,
    "no_event_interpolation_in_run": no_event_interpolation_in_run,
    "post_failure_comment_reference_valid": post_failure_comment_reference_valid,
    "dir_listing_matches": dir_listing_matches,
}


#: Check types that need the pinned Bash parser wheels to run at all.
PARSER_BACKED_CHECKS = frozenset({"shell_staged_tool_guard", "shell_capture_safe"})

_CHECK_META_KEYS = {"id", "description", "type", "paths"}
_WORKFLOW_STEP_USES_KEYS = {
    "uses_suffix", "job", "job_if_equals", "job_needs_nonempty",
    "job_permissions_include", "if_contains", "if_gates_on_outcome", "with_present",
    "with_equals", "with_tag_ref", "log_file_matches_download", "log_file_matches_tee",
    "marker_not_html_comment", "with_forbids_job_status", "marker_pairs_with_mode",
    "unique_with_key", "min_matches",
}

# Every fixture-suppliable constraint key each check `type` accepts, beyond
# `_CHECK_META_KEYS` and the `seed`/`transcript` kwargs `run_checks` injects
# itself (never fixture-suppliable, so they're not listed here). A type
# absent from this map — `yaml_parses`, `non_remote_refs_unchanged`,
# `event_only_workflows_unfiltered`, `required_checks_early_skip`,
# `no_event_interpolation_in_run`, `pin_comment_absent` — takes NO
# constraint keys at all: `workspace` + `paths` (+ the injected `seed`)
# fully determine what it checks.
#
# my-writing-style round-6 N-c (record-only, pre-existing on `main`): six
# types with no entry here, `pin_comment_absent` included, is not a gap
# `run_checks` leaves open — every type gets validated against
# `_CHECK_ALLOWED_KEYS.get(type, set())` regardless, so a check of one of
# these six that supplies ANY constraint key still raises rather than being
# silently dropped. It only means the docstring's claim that validation
# runs "for every type" reads, at a glance, like every type has an entry
# here; six legitimately have none, by design, not by omission.
_CHECK_ALLOWED_KEYS: dict[str, set[str]] = {
    "log_sequence": {"events"},
    "shell_capture_safe": {"source"},
    "parsed_config_values": {"format", "expected"},
    "shell_staged_tool_guard": {"tools"},
    "command_succeeds": {"argv", "timeout_s"},
    # The overlay directory is resolved against the fixture directory, the
    # parent of the injected `seed` (see `scorers/repo_tests.py`).
    "repo_tests": set(_REPO_TESTS_KEYS),
    "changeset_triggers": {"changeset", "expect_triggered", "expect_skipped"},
    # `require_present` is `file_matches`'s only opt-in: it makes a check
    # whose evidence IS the file fail closed when that file is absent or
    # empty, instead of passing on a `must_not_match` that found nothing
    # because there was nothing to find (DESIGN.md, "A check whose evidence
    # is a log says so"). Listed here so the generic kwargs builder routes
    # it; absent, every fixture carrying it raised at load.
    "file_matches": {"must_match", "must_not_match", "require_present"},
    "file_matches_excluding_comments": {"must_match", "must_not_match"},
    "transcript_matches": {"must_match", "must_not_match", "strip_seed"},
    "workflow_step_uses": _WORKFLOW_STEP_USES_KEYS,
    "workflow_permissions": {"job", "uses_suffix", "permissions_include"},
    "post_failure_comment_reference_valid": {"uses_suffix"},
    "files_unchanged": {"by"},
    "dir_listing_matches": {"expected", "expected_file", "ignore"},
    "file_digests_match": {"sha256"},
    "front_matter_has": {"equals", "nonempty_strings"},
    "git_ref_unchanged": {"path", "ref", "expected", "snapshot"},
    "no_git_config_names_path": {"forbidden_path", "exclude"},
    "git_remote_url_is": {"path", "remote", "expected_path"},
    "reaper_ran_in_standalone_repo": {"log_path"},
    "reaper_avoided_paths": {"log_path", "forbidden_paths"},
    "git_worktree_list_matches": {"path", "expected_names"},
    "uses_refs_sha_pinned": {"platform_prefix"},
    "pins_match_reference": {"reference", "platform_prefix"},
    "platform_refs_on_tag": {"platform_prefix", "tag", "min_refs"},
    "file_count": {"min", "max", "min_count", "max_count"},
    "link_targets_exist": {"link_pattern", "base"},
}


def run_checks(fixture: dict, workspace: str, seed: str,
               transcript: str | None = None, *, read_denied=None) -> list[dict]:
    """Run every objective check in the fixture; return result dicts.

    File and git checks are hermetic. The opt-in `command_succeeds` executes
    final-workspace code inside a mandatory filesystem/PID sandbox;
    network isolation is mandatory and reported in its detail (ADR 0006).
    The one that was not, `pinned_shas_match_tags`, resolved a SHA to a tag
    over `git ls-remote`; it retired with the version-comment convention and
    took the network opt-in that existed only for it. No check intentionally
    resolves remote evidence; disk and CPU consumption remain uncapped.

    Every check's keys are validated against `_CHECK_ALLOWED_KEYS` before
    running, for every type — not just `workflow_step_uses` — so an
    unrecognized key (a typo like `job_ifequals`, or a constraint that
    belongs to a different check type entirely) raises rather than being
    silently dropped from `kwargs`: dropped, the fixture would run a WEAKER
    check than written and still report green, which is worse than failing
    loudly at load time.
    """
    # Lazy: `seed_prep` imports this package's `repo_tests` and `commands`.
    import seed_prep
    with seed_prep.scoring_seed(seed, fixture) as compare_seed:
        return _run_checks(fixture, workspace, seed, compare_seed, transcript,
                           read_denied=read_denied)


def _run_checks(fixture: dict, workspace: str, seed: str, compare_seed: str,
                transcript: str | None, *, read_denied=None) -> list[dict]:
    """`run_checks`' loop. `seed` is the fixture's own seed directory, whose
    parent holds `repo_tests`' overlay and the pristine files
    `dir_listing_matches` reads; `compare_seed` is what the workspace is
    compared against, which a `strip_agent_context:` fixture strips."""
    results = []
    for check in fixture.get("objective_checks", []):
        fn = CHECKS.get(check["type"])
        if fn is None:
            results.append({"id": check["id"], "passed": False,
                            "detail": f"unknown check type {check['type']!r}"})
            continue
        allowed = _CHECK_ALLOWED_KEYS.get(check["type"], set())
        extra = set(check) - _CHECK_META_KEYS - allowed
        if extra:
            raise ValueError(f"unknown {check['type']!r} constraint key(s) in "
                            f"check {check.get('id')!r}: {sorted(extra)}")
        kwargs = {key: check[key] for key in allowed if key in check}
        if check["type"] in ("command_succeeds", "repo_tests"):
            kwargs["read_denied"] = [*(read_denied or ()), Path(seed).resolve().parent]
        if check["type"] in ("repo_tests", "dir_listing_matches"):
            # `dir_listing_matches` only reads `expected_file` from the seed,
            # which is pristine by contract (it may sit under a stripped path).
            kwargs["seed"] = seed
            if check["type"] == "repo_tests":
                import seed_prep
                kwargs["_python_deps"] = any(
                    entry["manager"] == "pip"
                    for entry in seed_prep._deps_entries(fixture.get("deps")))
                kwargs["_bundler_env"] = seed_prep.bundler_environment(
                    Path(workspace), fixture)
        elif check["type"] in ("non_remote_refs_unchanged", "files_unchanged"):
            kwargs["seed"] = compare_seed
        elif check["type"] == "shell_capture_safe":
            kwargs["transcript"] = transcript
        elif check["type"] == "transcript_matches":
            kwargs["transcript"] = transcript
            # Opt-in, per check: `strip_seed: true` says this fixture's
            # transcript is the agent's WRITING, so the material it
            # pasted back is not. A fixture whose transcript check asks
            # for something its own seed also carries must not set it.
            #
            # A real boolean, not a truthy value: read by truthiness,
            # `strip_seed: "no"` and `strip_seed: "false"` both turn the
            # pre-pass ON, which is the opposite of what the fixture says
            # and silent about it. YAML already parses `true`/`false`/`yes`/
            # `no` to booleans, so anything arriving here as a string was
            # quoted on purpose or is a typo — either way the fixture does
            # not mean what it says.
            strip_seed = kwargs.pop("strip_seed", False)
            if not isinstance(strip_seed, bool):
                raise FixtureError(
                    f"check {check.get('id')!r}: strip_seed must be a "
                    f"boolean, not {strip_seed!r} — a non-boolean is read "
                    "by truthiness, so a value meaning 'no' turns the seed "
                    "pre-pass on")
            if strip_seed:
                kwargs["seed"] = compare_seed
        elif check["type"] == "file_count":
            # Fixture-YAML spelling ("min"/"max") and the Python-parameter
            # spelling ("min_count"/"max_count") are both accepted constraint
            # keys (see _CHECK_ALLOWED_KEYS), but neither "min" nor "max" is
            # itself a valid file_count() keyword argument — rebuilt from
            # scratch here rather than added to the generic kwargs above, so
            # the wrongly-named "min"/"max" keys the generic pass may have
            # collected never reach the call below.
            kwargs = {
                "min_count": check.get("min", check.get("min_count")),
                "max_count": check.get("max", check.get("max_count")),
            }
        passed, detail = fn(workspace, check.get("paths", []), **kwargs)
        results.append({"id": check["id"], "passed": passed, "detail": detail})
    return results
