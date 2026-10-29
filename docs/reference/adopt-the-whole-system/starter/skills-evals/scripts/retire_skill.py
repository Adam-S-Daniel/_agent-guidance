#!/usr/bin/env python3
"""retire_skill.py: turn a retire verdict into a removal commit in a registry
checkout.

    retire_skill.py --registry DIR --skill NAME --verdict verdict.json
                    [--listing PATH ...] [--today YYYY-MM-DD] [--dry-run]

verdict.json is compute_gates.py's output. Without --dry-run it makes the
changes on a new branch retire-skill/NAME and commits them (no push, no
credential). With --dry-run it changes nothing and prints, as JSON, exactly
what it would do.

Changes: delete plugins/<p>/skills/NAME/; raise <p>'s plugin.json major
version; append an entry to docs/skill-impact.md; remove NAME from each
--listing file (default: scripts/plugin_layout.json and README.md, when they
exist). In a .json listing, list items equal to NAME and keys named NAME are
removed. In other files, list or table lines that name NAME are removed.

Exit codes: 0 done (or dry-run printed); 2 bad arguments or unreadable input;
3 the verdict for NAME is not "retire"; 4 NAME not found in exactly one plugin.
Output JSON has "draft": true when the plugin runs code (hooks/ or bin/); the
caller opens that removal as a draft, never auto-merging.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry_edit as R  # noqa: E402

DEFAULT_LISTINGS = ("scripts/plugin_layout.json", "README.md")
SKILL_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")


def find_plugin(registry, skill):
    base = os.path.join(registry, "plugins")
    found = [p for p in sorted(os.listdir(base))
             if os.path.isdir(os.path.join(base, p, "skills", skill))] if os.path.isdir(base) else []
    return found[0] if len(found) == 1 else None


def _strip_json(node, skill):
    if isinstance(node, list):
        return [_strip_json(x, skill) for x in node if x != skill]
    if isinstance(node, dict):
        return {k: _strip_json(v, skill) for k, v in node.items() if k != skill}
    return node


def listing_edit(path, skill):
    """Return (new_text, removed_count) for one listing file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.endswith(".json"):
        doc = json.loads(text)
        stripped = _strip_json(doc, skill)
        return json.dumps(stripped, indent=2) + "\n", int(stripped != doc)
    pat = re.compile(r"(?<![\w-])" + re.escape(skill) + r"(?![\w-])")
    keep, removed = [], 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("-", "*", "|")) and pat.search(line):
            removed += 1
        else:
            keep.append(line)
    return "".join(keep), removed


def impact_entry(skill, plugin, today, classes, old, new):
    return (f"## {today}: removed `{skill}`\n\n"
            f"- Change: deleted `plugins/{plugin}/skills/{skill}/`; `{plugin}` {old} -> {new}.\n"
            f"- Why: every roster cell was no-benefit at two looks ({len(classes)} cells), "
            f"by the retirement gate (`evals/gate-policy.yml`).\n"
            f"- Eval: cell classes: {', '.join(sorted(set(classes)))}.\n"
            f"- Tried and turned down: none.\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--listing", action="append")
    ap.add_argument("--today", default=None)
    ap.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if not SKILL_RE.match(a.skill) or not os.path.isdir(a.registry):
        print("retire_skill: bad arguments", file=sys.stderr)
        return 2
    try:
        with open(a.verdict, encoding="utf-8") as f:
            verdict = json.load(f)["skills"].get(a.skill)
    except (OSError, ValueError, KeyError):
        print("retire_skill: cannot read the verdict file", file=sys.stderr)
        return 2
    if not verdict or verdict.get("verdict") != "retire":
        print("retire_skill: the verdict for this skill is not retire", file=sys.stderr)
        return 3
    plugin = find_plugin(a.registry, a.skill)
    if plugin is None:
        print("retire_skill: skill not found in exactly one plugin", file=sys.stderr)
        return 4
    today = a.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    plugin_dir = os.path.join(a.registry, "plugins", plugin)
    old = R.read_version(a.registry, plugin)
    new = R.bumped(old, "major")
    skill_dir = os.path.join(plugin_dir, "skills", a.skill)
    deletes = sorted(os.path.relpath(os.path.join(d, f), a.registry)
                     for d, _, fs in os.walk(skill_dir) for f in fs)
    listings = []
    for rel in a.listing or [p for p in DEFAULT_LISTINGS
                             if os.path.exists(os.path.join(a.registry, p))]:
        _, removed = listing_edit(os.path.join(a.registry, rel), a.skill)
        listings.append({"file": rel, "removed": removed})
    entry = impact_entry(a.skill, plugin, today, verdict["classes"], old, new)
    out = {"dry_run": a.dry_run, "skill": a.skill, "plugin": plugin,
           "delete": deletes, "version": {"from": old, "to": new},
           "impact_entry": entry, "listings": listings,
           "draft": R.runs_code(plugin_dir) is not None,
           "branch": f"retire-skill/{a.skill}"}
    if not a.dry_run:
        R.git(a.registry, "checkout", "-b", out["branch"])
        shutil.rmtree(skill_dir)
        R.bump_plugin(a.registry, plugin, "major")
        R.append_impact(a.registry, entry)
        for item in listings:
            path = os.path.join(a.registry, item["file"])
            new_text, _ = listing_edit(path, a.skill)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)
        R.git(a.registry, "add", "-A")
        R.git(a.registry, "commit", "-m", f"Remove {a.skill}: no benefit in evals\n\n"
              "Agent: claude-code")
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
