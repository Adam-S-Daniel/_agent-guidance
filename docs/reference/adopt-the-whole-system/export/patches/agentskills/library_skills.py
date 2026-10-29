"""Content tiers (2026-10-29): the optional skills move to the kit's library.

Runs on the substituted tree, first of this repo's patches. The owner's
tiers (bootstrap/content-catalog.json): four CORE skills stay in the
registry (blank_registry.py's KEPT_SKILLS); the RECOMMENDED and OPINIONATED
skills below are moved, whole, to library/skills/<skill>/agent-skills/files/,
with the ops bootstrap/add-content.sh runs when one is chosen: its entry in
scripts/plugin_layout.json and a docs/skill-impact.md entry. The README
table and skills.lock are rebuilt by add-content.sh after it copies a skill,
the same way this export's regenerate steps build them. Every other skill
is left out by the manifest.
"""

from __future__ import annotations

from pathlib import Path

# skill -> (plugin, tier)
OPTIONAL = {
    "review-bash-ci-reliability": ("acsi-coding-anywhere", "recommended"),
    "debug-github-workflows": ("acsi-coding-anywhere", "recommended"),
    "disarm-inherited-reach": ("acsi-coding-anywhere", "recommended"),
    "writing-adrs": ("acsi-anything-anywhere", "opinionated"),
    "finding-unknowns": ("acsi-anything-anywhere", "opinionated"),
}

IMPACT_DATE = "2026-10-29"
IMPACT_ENTRY = """## {date} — {plugin}/{skill} — create

- Motivation: added from the adoption kit's library ({tier} tier), chosen with `bootstrap/add-content.sh`.
- Change: `plugins/{plugin}/skills/{skill}/` is added, with its entry in `scripts/plugin_layout.json`; its eval fixtures come with it in skills-evals (`evals/{skill}/`).
- Eval: none run at install; run the skill's fixtures in skills-evals to get its first result.
- Outcome: merged with the starter's pull request, or with the pull request that added it.
"""


def apply(root: Path, ctx) -> None:
    lib = ctx["library"]
    for skill, (plugin, tier) in sorted(OPTIONAL.items()):
        rel = f"plugins/{plugin}/skills/{skill}"
        if not (root / rel / "SKILL.md").is_file():
            raise SystemExit(f"library_skills: {rel}/SKILL.md is not in the exported tree")
        lib.move_tree("skill", skill, "agent-skills", root, rel)
        entry = IMPACT_ENTRY.format(date=IMPACT_DATE, plugin=plugin, skill=skill, tier=tier)
        lib.write_fragment("skill", skill, "agent-skills", "impact-entry.md", entry)
        lib.add_ops("skill", skill, "agent-skills", [
            {"op": "layout-skill", "file": "scripts/plugin_layout.json", "plugin": plugin, "skill": skill},
            {"op": "impact-entry", "file": "docs/skill-impact.md", "fragment": "impact-entry.md",
             "heading": entry.splitlines()[0]},
        ])
