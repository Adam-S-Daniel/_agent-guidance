"""C12: two per-account ids left in _agent-guidance's text.

* A handoff note gives the numeric id of one of the source's rulesets; it
  becomes a description ("the source system's ruleset id").
* Two memory-note fixtures in test/run-tests.sh carry a real Claude Code
  session id as `originSessionId`; the value only has to be present, so it
  becomes an invented uuid.

Both are matched by shape, so this file spells neither. Runs on the
substituted tree.
"""

from __future__ import annotations

import re
from pathlib import Path

INVENTED_SESSION = "00000000-0000-4000-8000-00000000c0de"
EDITS = [
    ("docs/handoffs/2026-08-20-gitleaks-and-silent-failures/A-trigger-narrowing.md",
     re.compile(r"\(id [1-9][0-9]{5,11}\), a separate ruleset"),
     "(the source system's ruleset id), a separate ruleset", 1),
    ("test/run-tests.sh",
     re.compile(r"originSessionId: (?!00000000-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
     f"originSessionId: {INVENTED_SESSION}", 2),
]


def apply(root: Path, ctx) -> None:
    for rel, pattern, new, want in EDITS:
        path = root / rel
        text, n = pattern.subn(new, path.read_text(encoding="utf-8"))
        if n != want:
            raise RuntimeError(f"{rel}: expected {want} id(s), found {n}")
        path.write_text(text, encoding="utf-8")
