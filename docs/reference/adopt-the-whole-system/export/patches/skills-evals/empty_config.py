"""DECISIONS X9 for skills-evals: the eval config lists start nearly empty.

`evals/scheduled.yml` schedules fixtures and `evals/non-coverage.yml` records
skip decisions about skills. Each keeps its header comment (the file's
contract) and its one top-level key.

- scheduled.yml keeps the rows of the two fixtures the kit ships, each as the
  source has it (the owner's 2026-10-29 12:29 UTC decision): the
  workflow-path-audit skill's `required-check-routing` and the
  git-push-does-not-mean-commit-exists guidance section's
  `hook-refused-commit`. The weekly planner has something to run on day one.
  Every other row named a fixture the kit does not ship and is dropped.
- non-coverage.yml ships `skips: []`: its rows named skills the kit does not
  ship.

Runs on the substituted tree.
"""

from __future__ import annotations

import re
from pathlib import Path

KEEP_SCHEDULED = (
    "evals/workflow-path-audit/required-check-routing",
    "evals/guidance/git-push-does-not-mean-commit-exists/hook-refused-commit",
)

SCHEDULED_NOTE = (
    "#\n"
    "# The kit ships the rows of its two fixtures (DECISIONS X9): the rows the\n"
    "# source repo carried for fixtures an adopter does not have are dropped.\n"
    "# Add a row when a fixture is ready to run weekly.\n"
)

EMPTY_NOTE = (
    "#\n"
    "# The kit ships this list empty (DECISIONS X9): the rows the source repo\n"
    "# carried named skills an adopter does not have. Add your own.\n"
)


def apply(root: Path, ctx) -> None:
    path = root / "evals/scheduled.yml"
    text = path.read_text(encoding="utf-8")
    head, sep, rows = text.partition("\nfixtures:\n")
    if not sep:
        raise SystemExit("empty_config: evals/scheduled.yml has no top-level `fixtures:` list")
    # One row is a "  - path:" line and the indented lines under it.
    entries = re.findall(r"  - path: (\S+)\n(?:    .*\n)*", rows)
    blocks = [m.group(0) for m in re.finditer(r"  - path: (\S+)\n(?:    .*\n)*", rows)]
    kept = [b for b, p in zip(blocks, entries) if p in KEEP_SCHEDULED]
    if sorted(p for p in entries if p in KEEP_SCHEDULED) != sorted(KEEP_SCHEDULED):
        raise SystemExit("empty_config: evals/scheduled.yml no longer names both kept fixtures")
    path.write_text(head + "\n" + SCHEDULED_NOTE + "fixtures:\n" + "".join(kept), encoding="utf-8")

    path = root / "evals/non-coverage.yml"
    text = path.read_text(encoding="utf-8")
    head, sep, _rows = text.partition("\nskips:\n")
    if not sep:
        raise SystemExit("empty_config: evals/non-coverage.yml has no top-level `skips:` list")
    path.write_text(head + "\n" + EMPTY_NOTE + "skips: []\n", encoding="utf-8")
