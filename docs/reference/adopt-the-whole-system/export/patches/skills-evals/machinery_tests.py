"""DECISIONS X4, X5, X9 and C8 for skills-evals: the tests follow the fixtures.

The export ships no skill or guidance fixtures and no real-work seeds. The
tests that read them fall in two groups:

  * tests that only lock a left-out fixture (its checks catch its trap, its
    seed has the right shape). They are removed. Whole test modules that are
    nothing else are left out by the manifest's `exclude`; single tests and
    classes inside kept modules are named, one per line, in
    removed_tests.txt beside this file, and taken out here by their AST
    extent (decorators included). A name that no longer exists fails the
    export, so the list stays exact.
  * tests of the machinery (harness, scorers, fakes, scripts, workflows) that
    used a fixture only as an input. They are pointed at the invented
    test-only fixtures under test/fixtures/ (the overlay), by the RETARGET
    edits below, each an exact old/new pair.

Runs on the substituted tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sub(root: Path, rel: str, pairs) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    for old, new, *count in pairs:
        want = count[0] if count else 1
        n = text.count(old)
        if n != want:
            raise SystemExit(f"machinery_tests: expected {want} occurrence(s) in {rel}, found {n}: {old[:80]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def _removals():
    by_file: dict[str, dict[str, set | None]] = {}
    for line in (HERE / "removed_tests.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("::")
        path, cls = parts[0], parts[1]
        tests = by_file.setdefault(path, {})
        if len(parts) == 2:
            tests[cls] = None  # the whole class
        else:
            got = tests.setdefault(cls, set())
            if got is None:
                raise SystemExit(f"machinery_tests: {line} is inside a class already removed whole")
            got.add(parts[2])
    return by_file


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def _remove_from(root: Path, rel: str, spec: dict) -> list[str]:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    spans = []
    for cls, tests in spec.items():
        node = classes.get(cls)
        if node is None:
            raise SystemExit(f"machinery_tests: {rel} has no class {cls}")
        methods = {b.name: b for b in node.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))}
        test_names = {n for n in methods if n.startswith("test")}
        if tests is not None:
            missing = tests - test_names
            if missing:
                raise SystemExit(f"machinery_tests: {rel}::{cls} has no {sorted(missing)}")
        if tests is None or tests == test_names:
            # Every test goes: the class goes too, unless another class in
            # the module names it (a base or mixin).
            others = text[:_start(node) - 1] + "\n".join(text.splitlines()[node.end_lineno:])
            if tests is None or cls not in others:
                spans.append((_start(node), node.end_lineno))
                continue
        for name in sorted(tests):
            m = methods[name]
            spans.append((_start(m), m.end_lineno))
    lines = text.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        # Take one blank line that followed the removed block with it.
        while end < len(lines) and not lines[end].strip() and (end + 1 < len(lines) and not lines[end + 1].strip()):
            end += 1
        del lines[start - 1:end]
    out = "".join(lines)
    ast.parse(out)  # still valid Python
    path.write_text(out, encoding="utf-8")
    return [f"{rel}::{c}" for c in spec]


# ── Retargets: machinery tests pointed at the invented test-only fixtures ──
# The pairs live in retargets.txt beside this file, one section per test
# file: a `=== <path>` line, a `# why:` line, then pairs written as
#   @@@ old / <exact old text> / @@@ new / <its replacement> / @@@ end
# applied in order; each old text must occur exactly once when it applies.
def _retargets() -> dict[str, list]:
    pairs: dict[str, list] = {}
    path = HERE / "retargets.txt"
    if not path.exists():
        return pairs
    rel = None
    mode = None
    old: list[str] = []
    new: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        marker = line.rstrip("\n")
        if mode is None:
            if marker.startswith("=== "):
                rel = marker[4:].strip()
                pairs.setdefault(rel, [])
            elif marker == "@@@ old":
                if rel is None:
                    raise SystemExit("machinery_tests: retargets.txt pair before any `=== <path>`")
                mode, old, new = "old", [], []
            elif marker and not marker.startswith("# why:"):
                raise SystemExit(f"machinery_tests: retargets.txt stray line {marker[:80]!r}")
        elif marker == "@@@ new" and mode == "old":
            mode = "new"
        elif marker == "@@@ end" and mode == "new":
            pairs[rel].append(("".join(old), "".join(new)))
            mode = None
        else:
            (old if mode == "old" else new).append(line)
    if mode is not None:
        raise SystemExit("machinery_tests: retargets.txt ends inside a pair")
    return pairs


RETARGET = _retargets()


def _prune_shard_weights(root: Path) -> None:
    """test/shard_weights.json (P3, written by `test/shard.py --refresh`)
    keys each slow test as `<module stem>::<class>::<method>`; drop the keys
    of tests this patch removed or the manifest left out, keeping the rest
    and the file's own serialization (two-space indent, trailing newline)."""
    import json

    path = root / "test" / "shard_weights.json"
    weights = json.loads(path.read_text(encoding="utf-8"))
    modules = {p.stem: p for p in (root / "test").rglob("test*.py")}
    modules.setdefault("run_tests", root / "test" / "run_tests.py")
    names: dict[str, set] = {}

    def exists(key: str) -> bool:
        stem, cls, method = key.split("::")
        if stem not in modules or not modules[stem].is_file():
            return False
        if stem not in names:
            tree = ast.parse(modules[stem].read_text(encoding="utf-8"))
            names[stem] = {f"{c.name}::{m.name}" for c in tree.body if isinstance(c, ast.ClassDef)
                           for m in c.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        return f"{cls}::{method}" in names[stem]

    kept = {key: value for key, value in weights.items() if exists(key)}
    path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


def apply(root: Path, ctx) -> None:
    for rel, spec in sorted(_removals().items()):
        _remove_from(root, rel, spec)
    for rel, pairs in sorted(RETARGET.items()):
        _sub(root, rel, pairs)
    _prune_shard_weights(root)
