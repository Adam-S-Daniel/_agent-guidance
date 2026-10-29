"""Exact-anchor text edits shared by the wsl-automation patches.

Every helper fails the export (SystemExit) unless each anchor it uses occurs
exactly once, so a source refresh that moves or rewrites an anchor stops the
export rather than skipping an edit silently.
"""

from __future__ import annotations

from pathlib import Path


class PatchError(SystemExit):
    pass


def _once(text: str, needle: str, where: str, start: int = 0) -> int:
    n = text.count(needle, start)
    if n != 1:
        raise PatchError(f"{where}: expected 1 occurrence, found {n}: {needle[:80]!r}")
    return text.index(needle, start)


def sub_once(text: str, old: str, new: str, where: str) -> str:
    i = _once(text, old, where)
    return text[:i] + new + text[i + len(old):]


def replace_between(text: str, start: str, end: str, new: str, where: str, keep_end: bool = True) -> str:
    """Replace from `start` (inclusive) up to `end` (kept unless keep_end is False)."""
    i = _once(text, start, where)
    j = _once(text, end, where, i + len(start))
    if not keep_end:
        j += len(end)
    return text[:i] + new + text[j:]


def drop_block(text: str, opener: str, where: str, indent: str) -> str:
    """Remove a brace block whose first line starts with indent+opener.

    The block ends at the first following line that is exactly indent + "}";
    one blank line after it is removed too, so the file keeps one blank line
    between neighbors.
    """
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(indent + opener)]
    if len(starts) != 1:
        raise PatchError(f"{where}: expected 1 block starting {opener[:70]!r}, found {len(starts)}")
    s = starts[0]
    for e in range(s + 1, len(lines)):
        if lines[e] == indent + "}":
            break
    else:
        raise PatchError(f"{where}: block {opener[:70]!r} never closes")
    if e + 1 < len(lines) and lines[e + 1] == "":
        e += 1
    elif s > 0 and lines[s - 1] == "":
        s -= 1
    return "\n".join(lines[:s] + lines[e + 1:])


def edit_file(root: Path, rel: str, fn) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    new = fn(text, rel)
    if new == text:
        raise PatchError(f"{rel}: patch changed nothing")
    path.write_text(new, encoding="utf-8")


def check_ascii(root: Path, rel: str) -> None:
    """AGENTS.md: every tracked .ps1/.psm1/.psd1 stays pure ASCII."""
    data = (root / rel).read_bytes()
    bad = [i for i, b in enumerate(data) if b > 0x7F]
    if bad:
        raise PatchError(f"{rel}: non-ASCII byte at offset {bad[0]}")

