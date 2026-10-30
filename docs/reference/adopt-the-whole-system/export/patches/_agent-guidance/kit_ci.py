"""The source runs the adoption kit's own tests from its CI; the exported repo has no kit.

The source `_agent-guidance` holds the kit under
docs/reference/adopt-the-whole-system/, which the manifest excludes, and its
CI runs the kit's tests: three steps in ci.yml's `checks` job, the
setup-python pin they use in actions.lock, the root .gitignore block that
keeps every kit file tracked, and the test in test/run-tests.sh that pins
those steps. In an adopter's `_agent-guidance` there is no kit, so the steps
would fail on a missing directory. Each is cut whole; nothing else changes.

Runs on the substituted tree, after the manifest's edits.
"""

from __future__ import annotations

from pathlib import Path


def _cut(text: str, start: str, end: str, where: str) -> str:
    """Remove from `start` (inclusive) to `end` (exclusive), each once."""
    if text.count(start) != 1:
        raise SystemExit(f"kit_ci: {where}: expected 1 of {start[:60]!r}, found {text.count(start)}")
    i = text.index(start)
    j = text.find(end, i + len(start))
    if j < 0:
        raise SystemExit(f"kit_ci: {where}: {end[:60]!r} not found after the start")
    return text[:i] + text[j:]


def _drop(text: str, old: str, where: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"kit_ci: {where}: expected 1 of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, "")


def _edit(root: Path, rel: str, fn) -> None:
    p = root / rel
    p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")


def apply(root: Path, ctx) -> None:
    _edit(root, ".github/workflows/ci.yml", lambda t: _cut(
        t, "      # THE ADOPTION KIT (docs/reference/adopt-the-whole-system/): its own\n",
        "  # THE REQUIRED CONTEXT.", "ci.yml"))
    _edit(root, ".github/workflows/actions.lock", lambda t: _drop(_drop(
        t, "        - 'actions/setup-python@v7.0.0'\n", "actions.lock workflows"),
        "    'actions/setup-python@v7.0.0':\n        ref: 'v7.0.0'\n"
        "        commit: 'sha1-5fda3b95a4ea91299a34e894583c3862153e4b97'\n"
        "        owner_id: 44036562\n        repo_id: 192625525\n", "actions.lock dependencies"))
    _edit(root, ".gitignore", lambda t: _cut(
        t, "\n# The adoption kit's starter trees and export overlays are exported copies of\n",
        "/docs/reference/adopt-the-whole-system/**/.pytest_cache/\n", ".gitignore").replace(
        "/docs/reference/adopt-the-whole-system/**/.pytest_cache/\n", "", 1))

    def tests(t: str) -> str:
        t = _cut(t, "# ── The adoption kit's step in ci.yml's checks job ─", "# ── fleet-memory.sh ─", "run-tests.sh")
        return _drop(t, "    test_ci_adoption_kit_step\n", "run-tests.sh group list")
    _edit(root, "test/run-tests.sh", tests)

    for rel in (".github/workflows/ci.yml", ".gitignore", "test/run-tests.sh"):
        if "adopt-the-whole-system" in (root / rel).read_text(encoding="utf-8"):
            raise SystemExit(f"kit_ci: {rel} still names the kit")
