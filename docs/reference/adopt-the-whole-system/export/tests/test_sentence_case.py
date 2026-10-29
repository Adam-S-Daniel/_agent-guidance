"""A `sentence_case` substitution capitalizes only at a sentence's start.

The full-name rules (manifests/_common.yml, C10) replace the owner's name
with "the owner" and capitalize it where a sentence starts. A line that
only wraps the sentence before it is not a start: the export once wrote
"idiosyncratic to\\n  The owner and still be public".

Run: python3 -m pytest KIT/export/tests -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXPORT_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kit_export", EXPORT_DIR / "export.py")
export = sys.modules.get("kit_export") or importlib.util.module_from_spec(_spec)
if "kit_export" not in sys.modules:
    sys.modules["kit_export"] = export
    _spec.loader.exec_module(export)


@pytest.mark.parametrize("before, start", [
    ("", True),
    ("Done here. ", True),
    ("ends a sentence.\n", True),
    ("asks this:\n", True),
    ("(closed.)\n", True),
    ("\n\n", True),
    ("# a comment that ends.\n# ", True),
    ("- one item\n- ", True),
    ("a paragraph\n## ", True),
    ("| a | row |\n| ", True),
    ("a skill can be idiosyncratic to\n  ", False),
    ("([ADR 0004](docs/decisions/0004-x.md),\n", False),
    ("# a comment that wraps\n# ", False),
    ("- a list item that wraps\n  ", False),
    ("in the middle of ", False),
])
def test_sentence_start(before, start):
    text = before + "X rest"
    assert export._at_sentence_start(text, len(before)) is start


def test_a_wrapped_line_keeps_the_lowercase_form():
    rule = export.compile_rules([{"regex": r"\bPat Q\b", "to": "the owner", "sentence_case": True}])
    sub = export.Substituter(rule)
    out = sub.sub("idiosyncratic to\n  Pat Q and public. Pat Q said so.\nPat Q", "x.md")
    assert out == "idiosyncratic to\n  the owner and public. The owner said so.\nThe owner"
