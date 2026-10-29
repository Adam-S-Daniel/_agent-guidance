"""Hard-wrap reconstruction, shared by two callers that must agree.

`judge._normalize_draft_text` levels every draft to one line shape before the
judge sees it, and `objective.strip_seed_material` decides provenance a
SENTENCE at a time. Both first have to answer the same question: which line
breaks did the writer MEAN, and which did a wrap column put there? A wrap is
not a fact in the text — it has to be reconstructed — and two
reconstructions that disagreed would put the judge and the objective column
on different readings of the same draft, so there is one implementation and
both import it.
"""

from __future__ import annotations

import re
import statistics

LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s")
# A Markdown table row: a leading `|` with something after it. A row is a
# deliberate line, never a wrap — the writer ended it because the row ended.
TABLE_ROW_RE = re.compile(r"^\s*\|.*\S")

# The shortest column anybody hard-wraps prose at. A pooled sample under this
# is not measuring a wrap; it is measuring deliberate short lines — a
# sign-off, a run of one-sentence lines, an address block — and using it as
# the column would join lines the writer meant to break.
MIN_WRAP_COLUMN = 40

# A paragraph shows evidence of having been wrapped, ON ITS OWN, only once
# it runs to three lines: two could be a sign-off ("Thanks," / "Alex
# Rivera") or a heading and its one-line body, and pooling those in put the
# sign-off's own 7-character line into the sample. `wrap_width` falls back
# to paragraphs of two when NOTHING in the draft reaches three — a draft
# whose paragraphs all come out two lines long is still a wrapped draft —
# and the `MIN_WRAP_COLUMN` test below is what keeps that fallback honest:
# a draft whose only two-line paragraph IS the sign-off samples [7], which
# is under the floor, and nothing is joined.
WRAP_EVIDENCE_LINES = 3


def paragraphs(lines: list[str]) -> list[list[str]]:
    """`lines` grouped into blank-line-delimited paragraphs."""
    out: list[list[str]] = []
    block: list[str] = []
    for line in lines:
        if line.strip():
            block.append(line)
        elif block:
            out.append(block)
            block = []
    if block:
        out.append(block)
    return out


def _sample(blocks: list[list[str]], evidence: int) -> list[int]:
    """The non-final line lengths of every paragraph that runs to `evidence`
    lines or more."""
    return [len(line) for block in blocks
            if len(block) >= evidence
            for line in block[:-1]]


def wrap_width(blocks: list[list[str]]) -> float:
    """The draft's estimated wrap column.

    Three questions, and they want different statistics, which is what this
    function got wrong twice:

    1. **Was anything wrapped at all?** Only a paragraph that runs to
       `WRAP_EVIDENCE_LINES` lines is evidence on its own: two could be a
       sign-off ("Thanks," / "Alex Rivera") or a heading and its one-line
       body. When NO paragraph reaches three lines the draft still has to be
       levelled — a reply hard-wrapped at 62 whose paragraphs all happen to
       come out two lines long is exactly the draft a line-shape tell picks
       out of a blind set — so the sample drops to paragraphs of two.
       Measured before this: such a draft, carrying one long careers URL,
       rendered as 12 lines with hard wraps of 63 and 66 surviving while
       both references rendered as 10 with none, deterministically, on every
       trial, on a dimension the rubric scores.
    2. **Is the sample plausible as a column?** The MEDIAN answers this and
       nothing else answers it as well: it ignores one odd line in either
       direction, so neither a sign-off's 7 nor an unbreakable URL's 89
       decides the verdict. Under `MIN_WRAP_COLUMN` the lines are short
       because the writer meant them to be — a paragraph of one-sentence
       lines, an address block — and joining on them would erase breaks the
       writer made, so the width goes up out of reach instead and nothing is
       joined.
    3. **Which column?** The MINIMUM of the lines that are long enough to be
       wraps at all. Every non-final line of a paragraph wrapped at column W
       is at most W, so the smallest of them is the most conservative
       estimate of W that the sample supports — and under-estimating is the
       safe direction: it can only join lines inside a paragraph that IS
       wrapped, while a deliberately short line (a 7-character sign-off) is
       never full enough to trigger the join at any of these widths. The
       median was the width as well as the plausibility test, and that
       under-joins RAGGED writing: a hand-wrapped paragraph of 58/72/49
       columns has a median of 65, the 58-character line is not "full" at
       65, and one hard wrap survived into the prompt.
    """
    sample = _sample(blocks, WRAP_EVIDENCE_LINES) or _sample(blocks, 2)
    if not sample:
        # Every paragraph is one line: there is nothing to join, whatever
        # this says. It is not the draft's longest line any more, because
        # that is the URL the writer hyperlinked and it switched the unwrap
        # off for the one draft that had one.
        return MIN_WRAP_COLUMN
    typical = statistics.median(sample)
    if typical < MIN_WRAP_COLUMN:
        longest = max(len(line) for block in blocks for line in block)
        return max(typical, longest)
    return min(length for length in sample if length >= MIN_WRAP_COLUMN)


def unwrap_block(lines: list[str], width: float) -> list[str]:
    """One paragraph's lines, with hard wrapping — and only that — undone.

    A line is a continuation of the one above it when the line above was too
    full for this line's first word to have fitted on it. That is what hard
    wrapping IS, and `width` is the estimated wrap column: the ragged last
    line of a wrapped paragraph ("than through a form.") is just as short as
    a sign-off, and only reconstructing the wrap tells them apart — the line
    above a ragged tail is full, the line above a sign-off is not.

    A line that STARTS a list item or a table row is never joined into the
    line above it, and nothing is joined into a table row: a list can follow
    a full line ("...easiest to see as a list:") and a row ends because the
    row ended. A list item's OWN continuation line is joined, though: it
    used not to be, and a hard-wrapped list therefore normalised to twice as
    many lines as its unwrapped twin — the exact line-shape tell this
    reconstruction exists to remove, surviving inside every draft that uses
    a list.

    Joined lines are collected and joined once at the end rather than
    appended onto a growing string: `out[-1] += ...` is quadratic in the
    paragraph's length, and a 12 MB draft took 94 seconds in it.
    """
    return [" ".join(lines[i] for i in part)
            for part in unwrap_indices(lines, width)]


def unwrap_indices(lines: list[str], width: float) -> list[list[int]]:
    """`unwrap_block`'s grouping, as indices into `lines`.

    The same reconstruction, handed back as the mapping rather than the
    joined text, for the caller that has to know which SOURCE lines a
    logical line came from — `objective.strip_seed_material` reads the
    floor it applies to a sentence off whether every line behind it sat
    inside a marked quotation.
    """
    # No lines, no groups. This used to seed the list with `[[0]]`
    # unconditionally, so an empty paragraph came back as one group holding
    # index 0 — an index into an empty list, which `unwrap_block` turns
    # into an IndexError the moment it renders it. No caller passes an
    # empty paragraph today (`paragraphs` never emits one), which is
    # exactly why it went unnoticed.
    if not lines:
        return []
    groups: list[list[int]] = [[0]]
    for index, (previous, line) in enumerate(zip(lines, lines[1:]), start=1):
        first_word = line.split(" ", 1)[0]
        wrapped = len(previous) + 1 + len(first_word) > width
        if (wrapped and not LIST_ITEM_RE.match(line)
                and not TABLE_ROW_RE.match(line)
                and not TABLE_ROW_RE.match(previous)):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups
