"""Invisible-character folding, shared by two callers that must agree.

`objective` scores the agent's reply against the fixture's patterns and
decides provenance over it; `judge` renders every draft for the pairwise
prompt. A character one of them folds and the other does not is a character
that reads as nothing to the judge and as something to the objective column,
on the same draft — so there is one implementation and both import it.

It used to be an ENUMERATION on each side, kept in step by a test comparing
the two patterns. The enumeration was the defect: measured on the merged
tree, it named 20 of Unicode's 163 `Cf` code points and 20 of its 1,950
`Mn` ones, so a paste salted with a bidi override (U+202A-U+202E), a
directional isolate (U+2066-U+2069), a TAG character (U+E0000-U+E007F), a
supplementary variation selector (U+E0100-U+E01EF), a musical format
control (U+1D173-U+1D17A), an interlinear annotation mark (U+FFF9-U+FFFB)
or a Hangul filler walked past both — one such character inside `leverage`
switched the avoid-list ban off, and one mid-word in a pasted seed sentence
defeated provenance. All three `my-writing-style` fixture headers assert
that a paste "salted with invisibles" is covered, so the enumeration was
also a documented property that was false.

The rule is a rule now, not a list:

1. **NFKC first.** Compatibility normalisation folds the width and ligature
   variants of a word onto the word itself, and — the part that matters
   here — it COMPOSES a base letter and its combining mark into the single
   precomposed character, so the acute in `cafe` + U+0301 becomes `café`
   and survives step 2 as an ordinary letter. Without it, dropping `Mn`
   would take the accent off every genuinely accented word.
2. **Then every `Cf` and every `Mn` code point goes** — of the ones still
   standing alone. That is the whole format-control and non-spacing-mark
   space, not a sample of it: whatever a future Unicode release adds to
   either category is covered the day the interpreter's tables carry it.
   A mark step 1 composed onto a letter is no longer standing alone; see
   the residual below.
3. **Plus the handful that render as nothing without being either**: the
   Hangul fillers (`Lo`), the Braille pattern blank (`So`) — none of which
   `\\s` matches — and a stray NUL (`Cc`), which is not a control anybody
   types but is what an arm that produced nothing sometimes hands back.

What is deliberately NOT dropped: anything with a width. A confusable — a
Cyrillic `а` standing in for a Latin `a` — is a different letter, not an
invisible one, and NFKC does not map confusables onto each other. See the
`strip_seed` header in `evals/my-writing-style/recruiter-reply/
fixture.yaml` for where that lands.

One more residual, in the "every `Mn` goes" claim above: step 1 COMPOSES a
mark onto the preceding letter before step 2 gets a chance to drop it, and
nine of Unicode's `Mn` code points compose onto a bare `r` this way (28
onto some plain ASCII letter) — `r` + U+0301 becomes `ŕ`, an ordinary `Ll`
letter, not a mark standing alone. `fold` never sees a mark to drop; it
sees an accented letter, the same shape `café` is and must stay. So
`fold("lever" + "́" + "age")` is `"leveŕage"`, not `"leverage"`, and a
`must_not_match` pattern anchored to the unaccented spelling misses it.
`fold_marks_again` below is the second reading that catches this — see
its own docstring for who else reads it, and for the one caller that
never does.
"""

from __future__ import annotations

import unicodedata

# Renders as nothing, categorised as something else. The Hangul fillers are
# `Lo` and the Braille pattern blank is `So`, so neither the category rule
# below nor `\s` touches them; NUL is `Cc`, and a draft of nothing but one
# used to pass the judge's non-empty guard.
ZERO_WIDTH_OTHERS = "ᅟᅠㅤﾠ⠀\x00"

# The two categories that carry no width by definition: format controls and
# non-spacing marks.
INVISIBLE_CATEGORIES = ("Cf", "Mn")

# `unicodedata.category` is a call per character and this runs over whole
# seed files; the answer depends on nothing but the character.
_DECIDED: dict[str, bool] = {}


def _drops(char: str) -> bool:
    verdict = _DECIDED.get(char)
    if verdict is None:
        verdict = (char in ZERO_WIDTH_OTHERS
                   or unicodedata.category(char) in INVISIBLE_CATEGORIES)
        _DECIDED[char] = verdict
    return verdict


def fold(text: str) -> str:
    """`text` NFKC-normalised, with every zero-width code point removed.

    The one function both `objective` and `judge` fold with. Applied to the
    agent's reply, to every draft the judge is shown, and to the seed files
    the provenance index is built from — a paste salted with an invisible
    the index was not salted with would otherwise not be a run of anything.
    """
    if not text:
        return ""
    return "".join(char for char in unicodedata.normalize("NFKC", text)
                   if not _drops(char))


def fold_marks_again(text: str) -> str:
    """A second reading of `fold`'s own output, appended by its one
    caller to whatever list of readings it is already scoring — not a
    reading scoped to `must_not_match` alone.

    `fold` composes before it drops, so a combining mark NFKC composes onto
    the letter in front of it — `r` + U+0301 becomes `ŕ`, an ordinary `Ll`
    letter — survives the fold as an accented letter rather than being read
    as a mark. Nine of Unicode's `Mn` code points compose onto a bare `r`
    this way (28 onto some plain ASCII letter), and `lever` + one of them +
    `age` folds to `leveŕage`, not `leverage` — the same pattern
    `objective._TAG_READINGS` already uses for a bare wrapper tag mid-word
    (two honest normalisations, so score both and let `must_not_match`
    object to either).

    NFD is the second reading: it decomposes `ŕ` back into `r` + U+0301,
    and dropping `Cf`/`Mn` a second time removes the mark this exposes —
    `fold_marks_again(fold("leveŕage"))` is `"leverage"`. Applied to
    `fold`'s output, not to raw text, so this reading keeps NFKC's width and
    ligature folding and only spends the mark a second time.

    `objective.transcript_matches` appends it to the SAME list `any()`
    checks `must_match` against, so a `must_match` pattern may also be
    satisfied by it alone — a permissive side effect, not a reading
    scoped to bans; see that function's append-loop comment for the
    worked example and why nothing is wrong today. NFD is why it stays
    additive rather than replacing the ordinary reading either way: it
    strips the accent off a genuinely accented word too (`café` reads
    as `cafe` here), which is exactly the letter `fold`'s NFKC-first step
    exists to protect, so `must_match` keeps the ordinary reading scored
    as well. Provenance is the one reader that never sees this one at
    all: `strip_seed_material` has already run by the time
    `transcript_matches` calls this function, and nothing else calls it.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if not _drops(char))
