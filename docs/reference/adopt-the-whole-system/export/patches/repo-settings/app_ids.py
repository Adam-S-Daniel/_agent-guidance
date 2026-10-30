"""C12: the decision records name the adopter's Apps by role, not the source's App ids.

Five ADRs give the numeric id of the source's two bypass Apps beside the
substituted App slug ("`example-adopter-skills-evals-roster` (App id N)"),
which pairs an adopter's name with an id that belongs to the source's
account. Every id that follows "App id " is found in the ADRs at run time
(so this file spells none), named by the App its line describes (the
roster App or the guidance sync App), and every standalone use of it in
docs/decisions/ becomes that placeholder: `<roster App id>` or
`<guidance sync App id>`. An adopter's real ids live in fleet.env and
repo-settings' variables (bootstrap REPO_VARIABLES). Runs on the
substituted tree.
"""

from __future__ import annotations

import re
from pathlib import Path

DIR = "docs/decisions"
FOUND = re.compile(r"App id ([1-9][0-9]{4,11})\b")


def _role(line: str) -> str:
    if "roster" in line:
        return "<roster App id>"
    if "sync" in line:
        return "<guidance sync App id>"
    raise RuntimeError(f"{DIR}: an App id on a line naming neither App: {line[:60]!r}")


def apply(root: Path, ctx) -> None:
    files = sorted((root / DIR).glob("*.md"))
    ids = {}
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            for m in FOUND.finditer(line):
                role = _role(line)
                if ids.setdefault(m.group(1), role) != role:
                    raise RuntimeError(f"{DIR}: App id {m.group(1)[:2]}... names two Apps")
    if len(ids) != 2:
        raise RuntimeError(f"{DIR}: expected the two bypass Apps' ids, found {len(ids)}")
    pattern = re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, ids)) + r")(?![\w-])")
    for path in files:
        text = path.read_text(encoding="utf-8")
        out = pattern.sub(lambda m: ids[m.group(1)], text)
        if out != text:
            path.write_text(out, encoding="utf-8")
