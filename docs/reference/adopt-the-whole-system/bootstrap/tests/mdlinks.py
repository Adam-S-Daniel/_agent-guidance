"""mdlinks.py: GitHub heading anchors and relative links in Markdown files.

Shared by check-links.py and check-steps.py. Standard library only.
"""
import re
from pathlib import Path

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
LINK = re.compile(r"(?<!!)\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
IMAGE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def _prose_lines(text):
    """(line number, line) for each line outside fenced code blocks."""
    fenced = None
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if fenced is None:
                fenced = m.group(1)
            elif m.group(1) == fenced:
                fenced = None
            continue
        if fenced is None:
            yield n, line


def slug(heading):
    """The anchor GitHub gives one heading's text (before de-duplication)."""
    text = CODE_SPAN.sub(lambda m: m.group(2), heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link keeps its text
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def anchors(text):
    """Every anchor GitHub gives the headings of a Markdown text, duplicates numbered."""
    seen, out = {}, set()
    for _, line in _prose_lines(text):
        m = HEADING.match(line)
        if not m:
            continue
        s = slug(m.group(1))
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return out


def links(text):
    """(line number, target) for each inline link and image outside code."""
    for n, line in _prose_lines(text):
        prose = CODE_SPAN.sub("", line)
        for rx in (LINK, IMAGE):
            for m in rx.finditer(prose):
                yield n, m.group(1)


def resolve(source: Path, target: str):
    """None when a relative link from source resolves, else why it does not.

    Absolute URLs (a scheme or //) and mailto: are not checked here.
    """
    if re.match(r"^[a-z][a-z0-9+.-]*:|^//", target, re.I):
        return None
    path, _, frag = target.partition("#")
    dest = source if not path else (source.parent / path)
    if not dest.exists():
        return f"{path} does not exist"
    if frag:
        if dest.is_dir() or dest.suffix.lower() != ".md":
            return f"#{frag} on {path or source.name}, which is not a Markdown file"
        if frag not in anchors(dest.read_text(encoding="utf-8")):
            return f"{path or source.name} has no heading #{frag}"
    return None
