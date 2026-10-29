"""DECISIONS X9 for skills-evals: the eval config lists start empty.

`evals/scheduled.yml` schedules fixtures the kit does not ship, and
`evals/non-coverage.yml` records skip decisions about skills the kit does not
ship. Each keeps its header comment (the file's contract) and its one
top-level key, with an empty list, so an adopter adds rows for their own
fixtures and skills. Runs on the substituted tree.
"""

from __future__ import annotations

from pathlib import Path

NOTE = (
    "#\n"
    "# The kit ships this list empty (DECISIONS X9): the rows the source repo\n"
    "# carried named fixtures and skills an adopter does not have. Add your own.\n"
)

LISTS = {
    "evals/scheduled.yml": "fixtures",
    "evals/non-coverage.yml": "skips",
}


def apply(root: Path, ctx) -> None:
    for rel, key in LISTS.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        marker = f"\n{key}:\n"
        head, sep, _rows = text.partition(marker)
        if not sep:
            raise SystemExit(f"empty_config: {rel} has no top-level `{key}:` list")
        path.write_text(head + "\n" + NOTE + f"{key}: []\n", encoding="utf-8")
