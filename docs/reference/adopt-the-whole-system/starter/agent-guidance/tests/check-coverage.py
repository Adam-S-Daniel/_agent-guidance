#!/usr/bin/env python3
"""check-coverage.py: agents-md/eval-coverage.yml agrees with agents-md/base.md.
One row per `##` heading of base.md (in order, `heading` equal to the text),
and each row's `bytes` equals the UTF-8 byte count from its heading line
through the line before the next `##` heading, or the end of the file. A `##`
inside a fenced code block is not a heading. Parsed with PyYAML. Prints
"N assertions passed, M failed" and exits 1 on any failure.

    python3 tests/check-coverage.py [<base.md> <eval-coverage.yml>]"""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, failed = 0, 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def sections(data: bytes):
    """[(heading text, byte extent)] for each `##` heading, in file order."""
    lines = data.splitlines(keepends=True)
    starts, fenced = [], False
    for i, raw in enumerate(lines):
        line = raw.rstrip(b"\r\n")
        if line.lstrip().startswith((b"```", b"~~~")):
            fenced = not fenced
        elif not fenced and line.startswith(b"## "):
            starts.append(i)
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        text = lines[i].rstrip(b"\r\n")[3:].decode("utf-8").strip()
        out.append((text, sum(len(l) for l in lines[i:end])))
    return out


def main(argv):
    base = argv[1] if len(argv) > 1 else os.path.join(ROOT, "agents-md", "base.md")
    manifest = argv[2] if len(argv) > 2 else os.path.join(ROOT, "agents-md", "eval-coverage.yml")
    with open(base, "rb") as f:
        found = sections(f.read())
    with open(manifest, encoding="utf-8") as f:
        rows = yaml.safe_load(f) or []
    rows = [r for r in rows if r.get("file", "agents-md/base.md") == "agents-md/base.md"]
    check(f"base.md has at least one ## heading", len(found) > 0)
    check(f"one row per ## heading: {len(rows)} rows, {len(found)} headings", len(rows) == len(found))
    check("row headings equal the base.md headings, in order "
          f"(missing {sorted({h for h, _ in found} - {r.get('heading') for r in rows})}, "
          f"stale {sorted({r.get('heading') for r in rows} - {h for h, _ in found})})",
          [r.get("heading") for r in rows] == [h for h, _ in found])
    ids = [r.get("id") for r in rows]
    check("row ids are unique", len(set(ids)) == len(ids))
    extent = dict(found)
    for r in rows:
        h = r.get("heading")
        if h in extent:
            check(f"row {r.get('id')}: bytes {r.get('bytes')} equals the section's {extent[h]}",
                  r.get("bytes") == extent[h])
        if r.get("status") == "skipped":
            check(f"row {r.get('id')}: a skipped row has a reason and a since date",
                  bool(r.get("reason")) and bool(r.get("since")))
    print(f"{passed} assertions passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
