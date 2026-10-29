#!/usr/bin/env python3
"""render-quickstart.py: write README.md's Quick start table from steps.json.

    render-quickstart.py [--check] [--steps <steps.json>] [--readme <README.md>]

steps.json (beside this script) is the one list of the quick start's steps;
status.sh reads it too. This rewrites the table between the
<!-- quickstart:begin --> and <!-- quickstart:end --> markers in the guide's
README.md (one folder up), with the columns Step | Run | Time | Phase
(section), and the fixed "any" row for verify.sh last. Everything outside
the markers is left as it is.

--check writes nothing: it exits 1 with one line when the table is stale.
Python 3 standard library only.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BEGIN = "<!-- quickstart:begin -->"
END = "<!-- quickstart:end -->"
NOTE = "<!-- generated from bootstrap/steps.json by bootstrap/render-quickstart.py: edit steps.json, then rerun it -->"
ANY_ROW = ("| any | `bootstrap/verify.sh --phase <N>` after finishing phase N (it checks only what should exist "
           "by then); with no `--phase` it expects everything, and the required checks once `fleet.env` says "
           "`REQUIRED_CHECKS=on` | | all |")


def cell(text):
    """A table cell: one line, with any | escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


def table(steps):
    rows = [NOTE, "", "| Step | Run | Time | Phase (section) |", "|---|---|---|---|"]
    for s in steps:
        phase = s.get("phase_label", str(s["phase"]))
        rows.append(f"| {cell(s['id'])} | {cell(s['run'])} | {s['minutes']} min | {cell(phase)} ({cell(s['section'])}) |")
    rows.append(ANY_ROW)
    return "\n".join(rows)


def render(readme_text, steps):
    """README text with the table between the markers replaced."""
    if readme_text.count(BEGIN) != 1 or readme_text.count(END) != 1:
        raise ValueError(f"README.md must hold {BEGIN} and {END} exactly once each")
    head, rest = readme_text.split(BEGIN)
    _old, tail = rest.split(END)
    return f"{head}{BEGIN}\n{table(steps)}\n{END}{tail}"


def main():
    ap = argparse.ArgumentParser(description="Write README.md's Quick start table from steps.json.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if README.md is stale")
    ap.add_argument("--steps", default=str(HERE / "steps.json"))
    ap.add_argument("--readme", default=str(HERE.parent / "README.md"))
    a = ap.parse_args()
    readme = pathlib.Path(a.readme)
    try:
        steps = json.loads(pathlib.Path(a.steps).read_text(encoding="utf-8"))["steps"]
        old = readme.read_text(encoding="utf-8")
        new = render(old, steps)
    except (OSError, ValueError, KeyError) as e:
        print(f"render-quickstart.py: {e}", file=sys.stderr)
        return 1
    if a.check:
        if new != old:
            print(f"{readme}: the Quick start table is stale; run bootstrap/render-quickstart.py")
            return 1
        return 0
    if new != old:
        readme.write_text(new, encoding="utf-8")
        print(f"wrote the Quick start table in {readme}")
    else:
        print("Nothing to change: the Quick start table is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
