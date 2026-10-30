#!/usr/bin/env python3
"""Issue #191: the guidance-bridge canary's `agents-only` layout.

Claude Code 2.1.277 reads AGENTS.md natively "in a project with no CLAUDE.md"
(its release note, quoted in the issue). No canary layout had AGENTS.md
without a CLAUDE.md, so native support could never show up, and the README
read the `no-bridge` leg (which keeps a CLAUDE.md) as the leg that would catch
it. `agents-only` is the missing leg.

These tests pin the SHAPE of the fixture and its layouts, hermetically: they
read files and run `harness/run_canary.py`'s own leg builder, never a CLI.
Whether a real CLI at or past 2.1.277 makes the leg pass is NOT tested here
and has not been measured: that is the owed live run.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_191.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
CANARY_DIR = REPO_ROOT / "evals" / "guidance-bridge-canary"
LAYOUTS_DIR = CANARY_DIR / "layouts"

sys.path.insert(0, str(HARNESS_DIR))
import run_canary  # noqa: E402


def _fixture() -> dict:
    return yaml.safe_load((CANARY_DIR / "fixture.yaml").read_text(encoding="utf-8"))


def _layout(name: str) -> dict:
    return next(layout for layout in _fixture()["layouts"]
                if layout["name"] == name)


def _files(layout_dir: Path) -> dict[str, str]:
    """Every file under a layout, relative path -> text."""
    return {path.relative_to(layout_dir).as_posix():
            path.read_text(encoding="utf-8")
            for path in sorted(layout_dir.rglob("*")) if path.is_file()}


class AgentsOnlyLayoutShape(unittest.TestCase):

    def test_the_fixture_lists_it_expecting_visible(self):
        layout = _layout("agents-only")
        self.assertEqual(layout["expect"], "visible")
        self.assertTrue((LAYOUTS_DIR / "agents-only").is_dir())

    def test_the_layout_is_agents_md_and_nothing_else(self):
        # No CLAUDE.md is the whole point, and nothing else (a `.claude/`
        # directory, CLAUDE.local.md, a second Markdown file) may stand in
        # for it as another route the token could take into context.
        self.assertEqual(sorted(_files(LAYOUTS_DIR / "agents-only")),
                         ["AGENTS.md"])

    def test_the_token_is_in_agents_md_in_the_house_phrasing_once(self):
        token = _layout("agents-only")["token"]
        text = _files(LAYOUTS_DIR / "agents-only")["AGENTS.md"]
        self.assertEqual(text.count(token), 1)
        self.assertIn(f"The magic word is {token}.", text)

    def test_the_token_has_the_shape_of_the_others_and_is_unique(self):
        tokens = {layout["name"]: layout["token"]
                  for layout in _fixture()["layouts"]}
        for name, token in tokens.items():
            with self.subTest(layout=name):
                self.assertRegex(token, r"^[A-Z]+-[0-9]{4}$")
        self.assertEqual(len(set(tokens.values())), len(tokens),
                         f"a token is shared between layouts: {tokens}")

    def test_no_layout_mentions_another_layouts_token(self):
        # One unique token per layout so a leak in one cannot satisfy another.
        tokens = {layout["name"]: layout["token"]
                  for layout in _fixture()["layouts"]}
        for name, token in tokens.items():
            for other in tokens:
                if other == name:
                    continue
                for relpath, text in _files(LAYOUTS_DIR / other).items():
                    with self.subTest(token_of=name, found_in=f"{other}/{relpath}"):
                        self.assertNotIn(token, text)

    def test_every_listed_layout_has_a_directory_and_the_reverse(self):
        listed = {layout["name"] for layout in _fixture()["layouts"]}
        on_disk = {path.name for path in LAYOUTS_DIR.iterdir() if path.is_dir()}
        self.assertEqual(listed, on_disk)

    def test_the_other_layouts_still_have_a_claude_md(self):
        # The README's reading depends on this: native AGENTS.md support
        # applies only without a CLAUDE.md, so no-bridge and fence can only
        # stay invisible while they keep one. If one loses it, that layout
        # is an agents-only layout under another name.
        for name in ("bridge", "no-bridge", "fence"):
            with self.subTest(layout=name):
                self.assertIn("CLAUDE.md", _files(LAYOUTS_DIR / name))

    def test_no_bridge_claude_md_links_and_never_imports(self):
        # Parsed by line, not matched as text: an import is a line that
        # STARTS with `@`.
        text = _files(LAYOUTS_DIR / "no-bridge")["CLAUDE.md"]
        self.assertFalse([line for line in text.splitlines()
                          if line.startswith("@")])

    def test_the_runner_builds_a_leg_for_it_from_that_directory(self):
        legs = run_canary._build_legs(_fixture(), CANARY_DIR, use_subagent=False)
        leg = next(leg for leg in legs if leg["name"] == "agents-only")
        self.assertEqual(leg["expect"], "visible")
        self.assertEqual(leg["layout_dir"], LAYOUTS_DIR / "agents-only")
        self.assertEqual(sorted(path.name for path in leg["layout_dir"].iterdir()),
                         ["AGENTS.md"])
        self.assertEqual([leg["name"] for leg in legs],
                         [layout["name"] for layout in _fixture()["layouts"]])

    def test_the_failure_hint_for_the_leg_names_the_cli_version(self):
        hint = run_canary._failure_hint({"name": "agents-only"})
        self.assertIn("2.1.277", hint)
        self.assertIn("claude --version", hint)
        # The release note's claim has not been measured by a real run.
        self.assertIn("unmeasured", hint)
        # And the legs that keep a CLAUDE.md do not blame native support.
        for name in ("no-bridge", "fence"):
            with self.subTest(layout=name):
                self.assertNotIn("native AGENTS.md support shipped",
                                 run_canary._failure_hint({"name": name}))


if __name__ == "__main__":
    unittest.main()
