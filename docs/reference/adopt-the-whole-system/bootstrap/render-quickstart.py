#!/usr/bin/env python3
"""render-quickstart.py: write docs/quick-start.md's table from steps.json.

    render-quickstart.py [--check] [--steps <steps.json>] [--readme <quick-start.md>]

steps.json (beside this script) is the one list of the quick start's steps;
status.sh reads it too. This rewrites the table between the
<!-- quickstart:begin --> and <!-- quickstart:end --> markers in the kit's
docs/quick-start.md, with the columns Step | What | Run | Time | Read, where Read
links each step's docs section (steps.json's kit-relative link, made
relative to the page), and the fixed "any" row for verify.sh last.
Everything outside the markers is left as it is.

--check writes nothing: it exits 1 with one line when the table is stale.
Python 3 standard library only.
"""
import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BEGIN = "<!-- quickstart:begin -->"
END = "<!-- quickstart:end -->"
NOTE = "<!-- generated from bootstrap/steps.json by bootstrap/render-quickstart.py: edit steps.json, then rerun it -->"
ANY_ROW = ("| any | Check | `bootstrap/status.sh` for where you are; `bootstrap/verify.sh` for the fast checks, "
           "`--only settings` or `--only regen:<task>` for the slow ones | | [quick start](#where-you-are) |")


def cell(text):
    """A table cell: one line, with any | escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


def read_link(s, page):
    """The Read cell: the step's part, linked relative to the page."""
    target, _, anchor = s["link"].partition("#")
    rel = os.path.relpath(HERE.parent / target, page.parent).replace(os.sep, "/")
    if rel == page.name:
        rel = ""
    label = s["part"] + (" (optional)" if s.get("optional") else "")
    return f"[{cell(label)}]({rel}{'#' + anchor if anchor else ''})"


def table(steps, page):
    rows = [NOTE, "", "| Step | What | Run | Time | Read |", "|---|---|---|---|---|"]
    for s in steps:
        rows.append(f"| {cell(s['id'])} | {cell(s['title'])} | {cell(s['run'])} | {s['minutes']} min | {read_link(s, page)} |")
    rows.append(ANY_ROW)
    return "\n".join(rows)


def render(readme_text, steps, page):
    """The page's text with the table between the markers replaced."""
    if readme_text.count(BEGIN) != 1 or readme_text.count(END) != 1:
        raise ValueError(f"{page.name} must hold {BEGIN} and {END} exactly once each")
    head, rest = readme_text.split(BEGIN)
    _old, tail = rest.split(END)
    return f"{head}{BEGIN}\n{table(steps, page)}\n{END}{tail}"


def main():
    ap = argparse.ArgumentParser(description="Write docs/quick-start.md's table from steps.json.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if the table is stale")
    ap.add_argument("--steps", default=str(HERE / "steps.json"))
    ap.add_argument("--readme", default=str(HERE.parent / "docs" / "quick-start.md"))
    a = ap.parse_args()
    readme = pathlib.Path(a.readme).resolve()
    try:
        steps = json.loads(pathlib.Path(a.steps).read_text(encoding="utf-8"))["steps"]
        old = readme.read_text(encoding="utf-8")
        new = render(old, steps, readme)
    except (OSError, ValueError, KeyError) as e:
        print(f"render-quickstart.py: {e}", file=sys.stderr)
        return 1
    if a.check:
        if new != old:
            print(f"{readme}: the quick start table is stale; run bootstrap/render-quickstart.py")
            return 1
        return 0
    if new != old:
        readme.write_text(new, encoding="utf-8")
        print(f"wrote the quick start table in {readme}")
    else:
        print("Nothing to change: the quick start table is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
