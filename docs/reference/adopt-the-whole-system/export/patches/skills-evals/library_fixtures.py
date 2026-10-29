"""Content tiers (2026-10-29): the optional items' fixtures and tests move to the library.

Runs on the substituted tree, after owner_constants.py and before
machinery_tests.py (which removes, by AST extent, the tests named in
removed_tests.txt; the optional items' tests are among them). For each
recommended or opinionated skill or guidance section
(bootstrap/content-catalog.json), this moves to
library/<kind>/<id>/skills-evals/:

- its fixture folders (evals/<skill>/, evals/guidance/<section>/), whole;
- its weekly rows from evals/scheduled.yml (empty_config.py keeps only the
  core rows), appended again by add-content.sh;
- the test modules that only test those fixtures, whole;
- the test classes and methods inside shared modules that test them, copied
  here (machinery_tests.py then removes them from the tree), with ops that
  insert them again at the class or module they came from.

A test module that tests two optional skills' fixtures is copied only when
both are installed (`copy-if`). The core items' fixtures and tests stay in
the tree.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

TREE = "skills-evals"
G = "evals/guidance"

# id -> kind, fixture folders, whole test modules, run_tests.py classes,
#       (module, class, method) methods, (module, class) classes in other modules
ITEMS = {
    "review-bash-ci-reliability": ("skill", ["evals/review-bash-ci-reliability"],
                                   ["test/issues/test_issue_bash_ci_drift_report_capture.py",
                                    "test/issues/test_issue_bash_ci_empty_repo_discovery.py",
                                    "test/issues/test_issue_bash_ci_test_runner_assertions.py"],
                                   ["TestIssue74"], [], []),
    "debug-github-workflows": ("skill", ["evals/debug-github-workflows"],
                               ["test/issues/test_issue_76.py", "test/issues/test_issue_76_git_identity.py",
                                "test/issues/test_issue_76_secret_name.py",
                                "test/issues/test_issue_76_swallowed_failure.py"], [], [], []),
    "disarm-inherited-reach": ("skill", ["evals/disarm-inherited-reach"], [], ["TestIssue77"], [], []),
    "writing-adrs": ("skill", ["evals/writing-adrs"], ["test/issues/test_issue_80_why.py"], ["TestIssue80"], [],
                     [("test/issues/test_issue_80_supersede.py", "TestIssue80Supersede")]),
    "finding-unknowns": ("skill", ["evals/finding-unknowns"], ["test/issues/test_issue_78.py"], [], [], []),
    "security": ("guidance", [f"{G}/security"], [], [], [("ThirdBatchFixtureTests", "test_search_query")], []),
    "github-404-means-not-authorized": ("guidance", [f"{G}/github-404-means-not-authorized"], [], [],
                                        [("SecondBatchFixtureTests",
                                          "test_a_404_reported_as_deletion_fails_and_a_scope_gap_passes")], []),
    "test-that-can-signal-kills-every-session": ("guidance", [f"{G}/test-that-can-signal-kills-every-session"], [],
                                                 [], [("ThirdBatchFixtureTests", "test_stop_worker")], []),
    "name-becomes-scanner-data": ("guidance", [f"{G}/name-becomes-scanner-data"], [], [],
                                  [("SecondBatchFixtureTests",
                                    "test_secret_named_entry_fails_and_a_purpose_name_passes")], []),
    "finding-your-unknowns": ("guidance", [f"{G}/finding-your-unknowns"], [], [],
                              [("FifthBatchFixtureTests", "test_changelog_check")], []),
    "anything-you-name-gets-its-link": ("guidance", [f"{G}/anything-you-name-gets-its-link"], [], [],
                                        [("TextFixtureTests", "test_done_summary_needs_every_link"),
                                         ("TextFixtureTests", "test_handoff_with_bare_numbers_fails_and_with_links_passes"),
                                         ("TextFixtureTests", "test_no_url_says_so"),
                                         ("TextFixtureTests", "test_waiting_on_review_is_read_from_the_reply")], []),
    "subagent-delegation": ("guidance", [f"{G}/subagent-delegation"], [], [],
                            [("SecondBatchFixtureTests",
                              "test_verifier_trap_accepts_the_report_and_the_runner_finds_the_failure")], []),
    "dependency-updates": ("guidance", [f"{G}/dependency-updates"], [], [],
                           [("ThirdBatchFixtureTests", "test_dependabot_cooldown")], []),
}

GATES = "test/issues/test_issue_124_guidance_gates.py"
RUN_TESTS = "test/run_tests.py"

# A module that tests the fixtures of two optional skills: copied when both are installed.
JOINT = {"test/issues/test_issue_71_trigger_set.py": ["writing-adrs", "review-bash-ci-reliability"]}

# run_tests.py points these two names at invented test fixtures for the
# machinery tests that stay (retargets.txt). The writing-adrs tests read the
# real fixtures, so their copy names the real folder instead.
REAL_PATHS = {
    "ADRS_BOOTSTRAP_DIR": '(REPO_ROOT / "evals" / "writing-adrs" / "bootstrap")',
    "ADRS_EXISTING_DIR": '(REPO_ROOT / "evals" / "writing-adrs" / "existing-convention")',
}


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def _extent(text: str, node) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(lines[_start(node) - 1:node.end_lineno])


def _rename(src: str, names: dict) -> str:
    """Replace NAME tokens (never text inside strings or comments)."""
    lines = src.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    pieces, prev = [], 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NAME and tok.string in names:
            a = starts[tok.start[0] - 1] + tok.start[1]
            b = starts[tok.end[0] - 1] + tok.end[1]
            pieces += [src[prev:a], names[tok.string]]
            prev = b
    pieces.append(src[prev:])
    return "".join(pieces)


def _classes(text: str) -> dict:
    return {n.name: n for n in ast.parse(text).body if isinstance(n, ast.ClassDef)}


def _scheduled_rows(root: Path) -> dict:
    text = (root / "evals/scheduled.yml").read_text(encoding="utf-8")
    rows = {}
    for m in re.finditer(r"^  - path: (\S+)\n(?:    .*\n)*", text, re.M):
        rows[m.group(1)] = m.group(0)
    return rows


def apply(root: Path, ctx) -> None:
    lib = ctx["library"]
    rows = _scheduled_rows(root)
    run_tests = (root / RUN_TESTS).read_text(encoding="utf-8")
    gates = (root / GATES).read_text(encoding="utf-8")
    rt_classes, gate_classes = _classes(run_tests), _classes(gates)

    for item, (kind, folders, modules, rt_cls, methods, other_cls) in sorted(ITEMS.items()):
        ops = []
        for folder in folders:
            if not (root / folder).is_dir():
                raise SystemExit(f"library_fixtures: {folder} is not in the exported tree")
            lib.move_tree(kind, item, TREE, root, folder)
        sched = [rows[p] for p in sorted(rows) if p == folders[0] or p.startswith(folders[0] + "/")]
        if sched:
            lib.write_fragment(kind, item, TREE, "scheduled-rows.yml", "".join(sched))
            ops.append({"op": "append-lines", "file": "evals/scheduled.yml", "fragment": "scheduled-rows.yml"})
        for module in modules:
            lib.move_tree(kind, item, TREE, root, module)
        for name in rt_cls:
            node = rt_classes.get(name)
            if node is None:
                raise SystemExit(f"library_fixtures: {RUN_TESTS} has no class {name}")
            frag = f"{name}.py"
            lib.write_fragment(kind, item, TREE, frag, _rename(_extent(run_tests, node), REAL_PATHS))
            ops.append({"op": "insert-class", "file": RUN_TESTS, "class": name, "fragment": frag})
        for module, name in other_cls:
            text = (root / module).read_text(encoding="utf-8")
            node = _classes(text).get(name)
            if node is None:
                raise SystemExit(f"library_fixtures: {module} has no class {name}")
            frag = f"{name}.py"
            lib.write_fragment(kind, item, TREE, frag, _extent(text, node))
            ops.append({"op": "insert-class", "file": module, "class": name, "fragment": frag})
        for cls, method in methods:
            node = gate_classes.get(cls)
            found = [b for b in (node.body if node else []) if isinstance(b, ast.FunctionDef) and b.name == method]
            if not found:
                raise SystemExit(f"library_fixtures: {GATES} has no {cls}.{method}")
            frag = f"{cls}.{method}.py"
            lib.write_fragment(kind, item, TREE, frag, _extent(gates, found[0]))
            ops.append({"op": "insert-method", "file": GATES, "class": cls, "method": method, "fragment": frag})
        if ops:
            lib.add_ops(kind, item, TREE, ops)

    for module, needs in sorted(JOINT.items()):
        data = (root / module).read_text(encoding="utf-8")
        (root / module).unlink()
        frag = Path(module).name
        for item in needs:
            kind = ITEMS[item][0]
            lib.write_fragment(kind, item, TREE, frag, data)
            lib.add_ops(kind, item, TREE, [{"op": "copy-if", "requires": sorted(needs), "fragment": frag, "to": module}])
