"""DECISIONS X9 for skills-evals: the eval config lists start nearly empty.

`evals/scheduled.yml` schedules fixtures and `evals/non-coverage.yml` records
skip decisions about skills. Each keeps its header comment (the file's
contract) and its one top-level key.

- scheduled.yml keeps the rows of the core items' fixtures, each as the
  source has it (the owner's 2026-10-29 tiers). The weekly planner has
  something to run on day one. The optional items' rows went to the library
  (library_fixtures.py); every other row named a fixture the kit does not
  ship and is dropped.
- non-coverage.yml ships `skips: []`: its rows named skills the kit does not
  ship.

Runs on the substituted tree.
"""

from __future__ import annotations

import re
from pathlib import Path

# The core items' scheduled fixtures (Content tiers, 2026-10-29). The
# recommended and opinionated items' rows went to the library with their
# fixtures (library_fixtures.py, which runs first).
KEEP_SCHEDULED = (
    "evals/workflow-path-audit/required-check-routing",
    "evals/skills-doctor/bucketed-account-store",
    "evals/guidance/watch-finished-is-not-ci-passed/legacy-status-failure",
    "evals/guidance/git-push-does-not-mean-commit-exists/hook-refused-commit",
    "evals/guidance/pinning-github-actions/new-workflow",
    "evals/guidance/git-practices/merge-method-and-closing-keyword",
    "evals/guidance/automation-vs-branch-protection/required-check-concurrency",
    "evals/guidance/working-in-these-repos/on-hold-pull-request",
    "evals/guidance/data-exposure-in-ci/dispatch-email-input",
    "evals/guidance/agent-contribution-markers/open-pull-request",
    "evals/guidance/two-setup-gaps/multi-repo-cloud-session",
    "evals/guidance/skills-ecosystem/push-fails-everywhere",
)

SCHEDULED_NOTE = (
    "#\n"
    "# The kit ships the rows of its core items' fixtures (DECISIONS X9 and\n"
    "# Content tiers); bootstrap/add-content.sh appends an optional item's rows\n"
    "# when you choose it. Add a row when a fixture is ready to run weekly.\n"
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
        raise SystemExit("empty_config: evals/scheduled.yml no longer names every kept fixture")
    path.write_text(head + "\n" + SCHEDULED_NOTE + "fixtures:\n" + "".join(kept), encoding="utf-8")

    path = root / "evals/non-coverage.yml"
    text = path.read_text(encoding="utf-8")
    head, sep, _rows = text.partition("\nskips:\n")
    if not sep:
        raise SystemExit("empty_config: evals/non-coverage.yml has no top-level `skips:` list")
    path.write_text(head + "\n" + EMPTY_NOTE + "skips: []\n", encoding="utf-8")
