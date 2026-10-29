#!/usr/bin/env python3
"""evals/writing-adrs/supersede: the third writing-adrs fixture (issue #80).

In the adoption kit this module is a helper only (its DECISIONS X9): the
fixture is not shipped, so the test class that pinned it is removed, and
`anchoring_problems` stays because the workflow-path-audit fixture's test
(test_issue_wpa_workflow_doc_in_step.py) imports it.

The seed is a repo whose decision log has three accepted ADRs and whose
script already does the opposite of ADR 0002. The skill's answer is to write
the replacement as ADR 0004, change 0002's Status line to name it, leave
0002's text alone, and keep the index and the script's comment in step.

What is pinned here, and how:

  * the pristine seed FAILS every behavior check and PASSES every restraint
    check, read from the harness's own command line;
  * one hand-written good result passes every check, and for each check a
    minimally wrong result fails exactly that check;
  * the formats the skill and this seed's README prescribe, and the
    reasonable variants of them, all pass;
  * a result that does not do the task (a seed ADR pasted under the new
    number, the required words inside an HTML comment or on the wrong line,
    an empty file with the right name) does not pass the check it aims at,
    and what the checks do NOT certify is pinned too, so the fixture's
    account of its own limits stays true;
  * every must_match / must_not_match pattern in the fixture, as parsed
    YAML, is anchored at both ends in each top-level alternative and cannot
    run across a line.

Every score below is read from `harness/run_eval.py --arm objective-only`,
the entry point an eval run uses, never from a private call into the scorer.
The child gets an environment built here (nothing is taken from the caller's
shell, and HOME is a throwaway directory), and every path a test creates
lives under that test's own mkdtemp.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_80_supersede.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
ADRS_DIR = REPO_ROOT / "evals" / "writing-adrs"
EVAL_DIR = ADRS_DIR / "supersede"
SEED = EVAL_DIR / "seed"
SIBLING_FIXTURES = (ADRS_DIR / "bootstrap", ADRS_DIR / "existing-convention")

sys.path.insert(0, str(HARNESS_DIR))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402

DECISIONS = Path("docs") / "decisions"
ADR_0002 = DECISIONS / "0002-prune-snapshots-older-than-30-days.md"
ADR_0004 = DECISIONS / "0004-keep-the-newest-14-snapshots.md"
INDEX = DECISIONS / "README.md"
SCRIPT = Path("scripts") / "prune-snapshots.sh"

# Every check in the fixture, in file order, with what the UNTOUCHED seed
# scores: False for a behavior check (the task is not done yet), True for a
# restraint check (nothing has been damaged yet).
EXPECTED_PRISTINE = {
    "new-adr-0004-is-a-house-format-entry": False,
    "new-adr-0004-names-0002": False,
    "adr-0002-status-says-superseded-by-0004": False,
    "adr-0002-keeps-what-it-recorded": True,
    "index-gained-a-row-for-0004": False,
    "index-row-for-0002-says-superseded-by-0004": False,
    "index-rows-for-0001-and-0003-kept": True,
    "decision-log-links-resolve": True,
    "script-comment-points-at-0004": False,
    "script-adr-pointers-resolve": True,
    "script-still-keeps-the-newest-14": True,
    "exactly-one-new-adr-file": False,
    "nothing-else-touched": True,
}

# ---- the hand-written good result ------------------------------------------

GOOD_ADR_0004 = """\
# 0004. Keep the newest 14 snapshots instead of pruning by age

- **Status:** Accepted
- **Date:** 2026-06-09

## Context

In 2026-05 the nightly snapshot job failed silently for five weeks. Pruning
kept running the whole time and deleted everything older than 30 days, so by
the time anyone looked there was no snapshot left to restore from. The
backup volume still fills in about four months if nothing is deleted.

## Decision

Keep the newest 14 snapshots and delete the rest, counting snapshots rather
than days. This supersedes 0002.

## Consequences

A stalled snapshot job can no longer prune its way down to nothing: the 14
newest snapshots stay however old they get. The window those 14 cover is no
longer fixed at a month; with a healthy nightly job it is two weeks.

## Alternatives considered

Keeping the age rule and adding an alert on a failed snapshot job was
rejected: the alert is one more thing that can fail silently, and the age
rule would still delete the last good snapshot behind it.

Never pruning at all was rejected: the backup volume fills in about four
months.
"""

SUPERSEDES_SENTENCE = "This supersedes 0002."

SEED_STATUS_0002 = "- **Status:** Accepted\n- **Date:** 2025-11-18\n"
GOOD_STATUS_0002 = "- **Status:** Superseded by 0004\n- **Date:** 2025-11-18\n"

SEED_ROW_0002 = ("| [0002](0002-prune-snapshots-older-than-30-days.md) | Prune "
                 "snapshots older than 30 days | Accepted |\n")
GOOD_ROW_0002 = SEED_ROW_0002.replace("| Accepted |", "| Superseded by 0004 |")
SEED_ROW_0003 = ("| [0003](0003-store-snapshots-on-a-separate-volume.md) | Store "
                 "snapshots on a separate volume | Accepted |\n")
GOOD_ROW_0004 = ("| [0004](0004-keep-the-newest-14-snapshots.md) | Keep the "
                 "newest 14 snapshots instead of pruning by age | Accepted |\n")

SEED_POINTER = ("# Retention policy: docs/decisions/"
                "0002-prune-snapshots-older-than-30-days.md\n")
GOOD_POINTER = ("# Retention policy: docs/decisions/"
                "0004-keep-the-newest-14-snapshots.md\n")

SEED_DECISION_0002 = (
    "Delete every snapshot older than 30 days, measured from the snapshot's own\n"
    "timestamp, each night after the snapshot job runs.\n")

DECOY_ADR_0005 = """\
# 0005. Fix the dry-run help text

- **Status:** Accepted
- **Date:** 2026-06-09

## Context

CHANGELOG.md records a typo fix in the snapshot job's help text.

## Decision

Spell the flag's description correctly.

## Consequences

The help text reads correctly.

## Alternatives considered

None.
"""


def replace_once(path: Path, old: str, new: str) -> None:
    """Replace `old` with `new` in `path`, where `old` occurs exactly once.

    Every edit a test makes goes through here, so an edit whose anchor has
    drifted out of the seed (or out of the good result) is an error naming
    the anchor, never a silent no-op that leaves the test asserting on a
    workspace it did not change.
    """
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise AssertionError(f"{path.name}: expected exactly one {old!r}, "
                             f"found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# ---- the anchoring property -------------------------------------------------

def regex_tokens(pattern: str):
    """Yield (text, kind) left to right; kind is "escape", "class" or "char".

    An escape pair and a whole `[...]` class are each one opaque token, so a
    `|`, `(`, `)`, `.`, `^` or `$` inside either is never read as structure.
    """
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            yield pattern[i:i + 2], "escape"
            i += 2
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" and j + 1 < n else 1
            j = min(j + 1, n)
            yield pattern[i:j], "class"
            i = j
        else:
            yield c, "char"
            i += 1


def top_level_alternatives(pattern: str) -> list[list[tuple[str, str]]]:
    """The pattern's tokens, split at every `|` that sits at depth 0."""
    alternatives, current, depth = [], [], 0
    for token in regex_tokens(pattern):
        text, kind = token
        if kind == "char" and text == "(":
            depth += 1
        elif kind == "char" and text == ")":
            depth -= 1
        elif kind == "char" and text == "|" and depth == 0:
            alternatives.append(current)
            current = []
            continue
        current.append(token)
    alternatives.append(current)
    return alternatives


def anchoring_problems(pattern: str) -> list[str]:
    """Why `pattern` could match text other than whole lines it spells out.

    Empty when: no inline flag changes what `^`, `$` or a class means; every
    top-level alternative starts with `^` and ends with `$`; there is no
    unescaped `.`; every negated class excludes a newline; and none of the
    newline-matching shorthands (`\\s`, `\\W`, `\\D`) stands outside a class.
    A literal newline in the pattern is allowed: it is spelled out, not a
    run.
    """
    problems = []
    compiled = re.compile(pattern)
    if compiled.flags & (re.DOTALL | re.IGNORECASE | re.VERBOSE | re.MULTILINE):
        problems.append("carries an inline flag")
    for alternative in top_level_alternatives(pattern):
        text = "".join(t for t, _ in alternative)
        if not alternative or alternative[0] != ("^", "char"):
            problems.append(f"alternative {text!r} does not start with ^")
        if not alternative or alternative[-1] != ("$", "char"):
            problems.append(f"alternative {text!r} does not end with $")
    for text, kind in regex_tokens(pattern):
        if kind == "char" and text == ".":
            problems.append("has an unescaped .")
        if kind == "class" and text.startswith("[^") and not (
                "\\n" in text or "\\s" in text):
            problems.append(f"negated class {text} admits a newline")
        if kind == "escape" and text in ("\\s", "\\W", "\\D"):
            problems.append(f"{text} outside a class can match a newline")
    return problems



class AnchoringHelperTests(unittest.TestCase):
    """The helper the workflow-path-audit fixture's test reuses, pinned alone.

    The kit removes this module's fixture test class (its DECISIONS X9), and
    the runner requires every discovered module to contribute a test, so the
    helper carries its own.
    """

    def test_a_whole_line_pattern_has_no_anchoring_problem(self):
        self.assertEqual(anchoring_problems(r"^needs: \[a, b\]$"), [])

    def test_a_loose_pattern_is_named(self):
        problems = anchoring_problems(r"needs.*")
        self.assertTrue(any("does not start with ^" in p for p in problems))
        self.assertTrue(any("does not end with $" in p for p in problems))
        self.assertTrue(any("unescaped ." in p for p in problems))

    def test_an_inline_flag_is_named(self):
        self.assertIn("carries an inline flag",
                      anchoring_problems(r"(?i)^a$"))


if __name__ == "__main__":
    unittest.main()
