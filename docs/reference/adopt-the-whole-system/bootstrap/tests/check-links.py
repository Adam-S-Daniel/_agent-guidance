#!/usr/bin/env python3
"""check-links.py: every relative link in the kit's own pages resolves.

    check-links.py <kit root> [<file.md> ...]

Checks README.md, docs/*.md, .claude/skills/adopt/SKILL.md,
bootstrap/README.md and starter/README.md (or the files named): each inline
link or image that is not an absolute URL must name a file or folder that
exists, and a #fragment must be a heading anchor of the Markdown file it
points at (GitHub's anchor rules, duplicates numbered). Links inside code
are not links. The exported trees' own pages are the source repos' and are
not checked here.

Prints "N assertions passed" and exits 1 on any failure. Standard library only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdlinks  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
if len(sys.argv) > 2:
    FILES = [Path(p).resolve() for p in sys.argv[2:]]
else:
    FILES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
             ROOT / ".claude" / "skills" / "adopt" / "SKILL.md",
             ROOT / "bootstrap" / "README.md", ROOT / "starter" / "README.md"]
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


total = 0
for f in FILES:
    check(f"{f} exists", f.is_file())
    if not f.is_file():
        continue
    rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
    for line, target in mdlinks.links(f.read_text(encoding="utf-8")):
        total += 1
        why = mdlinks.resolve(f, target)
        check(f"{rel}:{line}: ({target}): {why}", why is None)

check(f"found links to check ({total})", total > 20)
print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
