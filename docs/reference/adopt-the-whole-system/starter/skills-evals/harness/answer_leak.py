"""The answer-leak lint for real-work fixtures, and `interface_strings:`.

A real-work fixture's task text is the issue its pull request closed. Some of
those issues were edited after the fix and describe or quote it, so the task
text must not carry any run of four or more consecutive words that also
appears in a line the merged diff added (the same four-word rule #98 applies
to a guidance section). Words are lowercase letter and digit runs: the rule is
lexical, so a regex is the right tool here.

A hidden test sometimes checks an exact identifier or message (a status name,
an exported function, an `::error::` text). The task text has to name it, or
no fix can pass however correct. Each fixture declares those strings in
`interface_strings:` (the owner, 2026-10-06: "Exempt interface strings"), and a
four-word run made only of one declared string's own consecutive words is
not a leak.

The diff often quotes the issue rather than the reverse: a fix's comments,
test data and ADR prose restate the issue's evidence. So text the issue
already held before the pull request's first commit is not a leak either
(the owner, 2026-10-06: "Exempt pre-existing issue text"). A fixture records that
text in the file `issue_before_fix:` names, and the lint flags only runs the
task text shares with the diff that the snapshot lacks: the task text's own
additions, such as an interface paragraph, and any issue text written after
the fix began. How a snapshot is established, and its limits, is in DESIGN.md.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone
from pathlib import Path

import guidance

RUN_WORDS = 4
KEY = "interface_strings"
SNAPSHOT_KEY = "issue_before_fix"
MAX_SNAPSHOT_BYTES = 256 * 1024
# What "pre-existing" rests on, recorded beside the snapshot from GitHub.
CREATED_KEY = "issue_created_at"
EDITED_KEY = "issue_last_edited_at"
FIRST_COMMIT_KEY = "first_commit_at"
PROVENANCE_KEYS = (CREATED_KEY, EDITED_KEY, FIRST_COMMIT_KEY)
_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
MAX_STRINGS = 32
MAX_STRING_CHARS = 200
_WORD_RE = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _runs(tokens: list[str]) -> list[tuple[str, ...]]:
    return [tuple(tokens[i:i + RUN_WORDS]) for i in range(len(tokens) - RUN_WORDS + 1)]


def added_lines(patch: str) -> list[str]:
    """The lines a unified diff adds, without the `+` and without file headers."""
    return [line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")]


def fixture_added_lines(fixture_dir, overlay: str = "checker") -> list[str]:
    """Every line a real-work fixture's pull request added: the added lines
    of its `solution.patch`, and each overlay (hidden test) file's additions
    over the seed's copy of it. This is the `answer` the lint compares the
    task text with, for the committed fixtures' test module and for
    scripts/scaffold_real_work.py alike."""
    fixture_dir = Path(fixture_dir)
    added = added_lines((fixture_dir / "solution.patch").read_text(encoding="utf-8"))
    checker = fixture_dir / overlay
    for path in sorted(p for p in checker.rglob("*") if p.is_file() and not p.is_symlink()):
        before = fixture_dir / "seed" / path.relative_to(checker)
        old = (before.read_text(encoding="utf-8").splitlines()
               if before.is_file() and not before.is_symlink() else [])
        new = path.read_text(encoding="utf-8").splitlines()
        added += [line[1:] for line in difflib.unified_diff(old, new, lineterm="", n=0)
                  if line.startswith("+") and not line.startswith("+++")]
    return added


def leaked_runs(prompt: str, answer: str, interface_strings=(),
                preexisting: str = "") -> list[str]:
    """Every four-word run of `prompt` that `answer` also contains, in prompt
    order and once each, except runs inside one declared interface string and
    runs the issue already held before the fix (`preexisting`).

    `answer` is the merged diff's added text. A run is matched within one
    added line, so two unrelated lines never join into a phrase. `preexisting`
    is matched as running text, so rewrapping the issue does not make it new.
    """
    answer_runs = {run for line in answer.splitlines() for run in _runs(words(line))}
    exempt = {run for text in interface_strings for run in _runs(words(text))}
    exempt |= set(_runs(words(preexisting)))
    found: list[str] = []
    for run in _runs(words(prompt)):
        text = " ".join(run)
        if run in answer_runs and run not in exempt and text not in found:
            found.append(text)
    return found


def _snapshot_path(fixture: dict, fixture_dir: Path) -> Path | None:
    """The resolved `issue_before_fix:` file, None when the key is absent."""
    if SNAPSHOT_KEY not in fixture:
        return None
    value = fixture[SNAPSHOT_KEY]
    problem = f"`{SNAPSHOT_KEY}:` must name a regular file in the fixture directory, outside seed/"
    if (not isinstance(value, str) or not value.strip() or "\x00" in value
            or Path(value).is_absolute() or ".." in Path(value).parts):
        raise guidance.GuidanceError(problem)
    root = Path(fixture_dir).resolve()
    path = root / value
    if path.is_symlink() or not path.is_file():
        raise guidance.GuidanceError(problem)
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved.is_relative_to(root / "seed"):
        raise guidance.GuidanceError(problem)
    if resolved.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise guidance.GuidanceError(f"`{SNAPSHOT_KEY}:` is over {MAX_SNAPSHOT_BYTES} bytes")
    try:
        resolved.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise guidance.GuidanceError(f"`{SNAPSHOT_KEY}:` is not UTF-8 text") from None
    return resolved


def _utc_time(fixture: dict, key: str, nullable: bool = False) -> datetime | None:
    if key not in fixture:
        raise guidance.GuidanceError(
            f"`{SNAPSHOT_KEY}:` needs `{key}:` beside it: what the snapshot's "
            "\"before the fix\" rests on")
    value = fixture[key]
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        raise guidance.GuidanceError(
            f"`{key}:` must be a quoted ISO-8601 UTC time like "
            f"\"2026-10-05T15:59:07Z\"" + (", or null" if nullable else "")
            + f", got {value!r}")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise guidance.GuidanceError(f"`{key}:` is not a real time: {value!r}") from None


def _check_provenance(fixture: dict) -> None:
    """The snapshot predates the fix: the issue was created, and last edited
    (or never), no later than the pull request's first commit."""
    if SNAPSHOT_KEY not in fixture:
        present = [key for key in PROVENANCE_KEYS if key in fixture]
        if present:
            raise guidance.GuidanceError(
                f"{', '.join(f'`{k}:`' for k in present)} describe an "
                f"`{SNAPSHOT_KEY}:` snapshot, and the fixture has none")
        return
    created = _utc_time(fixture, CREATED_KEY)
    edited = _utc_time(fixture, EDITED_KEY, nullable=True)
    first = _utc_time(fixture, FIRST_COMMIT_KEY)
    if created > first:
        raise guidance.GuidanceError(
            f"`{CREATED_KEY}:` is after `{FIRST_COMMIT_KEY}:`: the issue did not "
            "exist before the fix began, so none of its text is pre-existing")
    if edited is not None and not created <= edited <= first:
        raise guidance.GuidanceError(
            f"`{EDITED_KEY}:` must fall between `{CREATED_KEY}:` and "
            f"`{FIRST_COMMIT_KEY}:`: an issue edited after the fix began needs "
            "the revision from before it as its snapshot, recorded with that "
            "revision's time")


def preexisting_text(fixture: dict, fixture_dir) -> str:
    """The issue's text as it stood before the fix, or "" with no snapshot."""
    path = _snapshot_path(fixture, Path(fixture_dir))
    return "" if path is None else path.read_text(encoding="utf-8")


def validate_fixture(fixture: dict, fixture_path) -> None:
    """`interface_strings:` is absent, or a list of 1-32 nonblank strings;
    `issue_before_fix:` is absent, or a file in the fixture outside seed/."""
    _snapshot_path(fixture, Path(fixture_path).parent)
    _check_provenance(fixture)
    if KEY not in fixture:
        return
    value = fixture[KEY]
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_STRINGS
            or any(not isinstance(item, str) or not item.strip()
                   or len(item) > MAX_STRING_CHARS for item in value)):
        raise guidance.GuidanceError(
            f"{fixture_path}: `{KEY}:` must be a list of 1 to {MAX_STRINGS} "
            f"nonblank strings of at most {MAX_STRING_CHARS} characters: the "
            "identifiers and messages a hidden test checks verbatim")
