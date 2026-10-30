"""Cold-start fixes for skills-evals (DECISIONS "Cold-start fixes (2026-10-29)").

Two findings from running the exported system against an empty account:

1. The coverage census refused the kit's empty private registry ("yields no
   skills ... a wrong path or a wrong layout, not a zero") and failed every
   day. A `harness/registries.yml` entry may now declare `may_be_empty: true`;
   the census resolves that registry with 0 skills. Every registry without
   the key keeps the source's refusal. The kit sets it for the private
   registry only.

2. The invented example sites of C3 (`example-site-platform`,
   `example-site-a`) were checked out by live workflows and listed in
   `harness/registries.yml`, repos an adopter does not have; a checkout of a
   missing repo fails the job, and a registry with no `--registry` flag is
   "unresolved". Their checkouts, flags and registry entries are dropped. The
   code that handles a platform repo stays (C3).

Runs on the substituted tree. Every replacement states how many times it
must match, so a source change that moves the text fails the export.
"""

from __future__ import annotations

import re
from pathlib import Path

SITE_REPO = re.compile(r"repository: \S+/example-site-(?:platform|a)\n")
SITE_FLAG = re.compile(r"^\s*--registry \"?example-site-(?:platform|a)=[^\n]*\n", re.M)


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


def _sub(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    text = _read(root, rel)
    got = text.count(old)
    if got != count:
        raise SystemExit(f"cold_start_registries: {rel}: expected {count} occurrence(s), found {got}: {old[:70]!r}")
    _write(root, rel, text.replace(old, new))


def _drop_site_checkouts(root: Path, rel: str, count: int) -> None:
    """Remove each step (an item of a `steps:` list at six spaces) that checks
    out an example site, with the blank line after it."""
    lines = _read(root, rel).splitlines(keepends=True)
    out, i, dropped = [], 0, 0
    while i < len(lines):
        if re.match(r" {6}- name: ", lines[i]):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > 6):
                j += 1
            block = lines[i:j]
            if SITE_REPO.search("".join(block)):
                dropped += 1
                i = j
                continue
        out.append(lines[i])
        i += 1
    if dropped != count:
        raise SystemExit(f"cold_start_registries: {rel}: dropped {dropped} example-site checkout step(s), expected {count}")
    _write(root, rel, "".join(out))


def _drop_site_flags(root: Path, rel: str, count: int) -> None:
    text = _read(root, rel)
    text, n = SITE_FLAG.subn("", text)
    if n != count:
        raise SystemExit(f"cold_start_registries: {rel}: dropped {n} example-site --registry flag(s), expected {count}")
    _write(root, rel, text)


def apply(root: Path, ctx) -> None:
    # --- harness/registries.yml: no example sites; the private registry may be empty
    reg = "harness/registries.yml"
    text = _read(root, reg)
    text, n = re.subn(
        r"  - name: example-site-(?:platform|a)\n    url: \S+\n    layout: \S+\n", "", text)
    if n != 2:
        raise SystemExit(f"cold_start_registries: {reg}: dropped {n} example-site entries, expected 2")
    _write(root, reg, text)
    _sub(root, reg,
         '    url: https://github.com/example-adopter/acsi-agentskills-private\n'
         '    layout: "plugins/*/skills/*/SKILL.md"\n',
         '    url: https://github.com/example-adopter/acsi-agentskills-private\n'
         '    layout: "plugins/*/skills/*/SKILL.md"\n'
         '    may_be_empty: true\n')
    _sub(root, reg,
         "# wildcard.\nregistries:\n",
         "# wildcard.\n"
         "#\n"
         "# `may_be_empty: true` (optional, a boolean, false when absent) says the\n"
         "# registry may hold no skill yet: the coverage census then resolves it with\n"
         "# 0 skills instead of refusing a layout that matches nothing (the kit's\n"
         "# private registry starts that way). Without the key an empty registry is\n"
         "# still refused as a wrong path or a wrong layout.\n"
         "registries:\n")

    # --- harness/run_eval.py: validate the key and carry it into the resolved entry
    _sub(root, "harness/run_eval.py",
         "        if Path(layout).is_absolute():\n"
         "            raise ValueError(\n"
         "                f\"{path} entry {name!r} has an absolute layout {layout!r} — \"\n",
         "        if not isinstance(entry.get(\"may_be_empty\", False), bool):\n"
         "            raise ValueError(\n"
         "                f\"{path} entry {name!r}: 'may_be_empty' must be true or false\")\n"
         "        if Path(layout).is_absolute():\n"
         "            raise ValueError(\n"
         "                f\"{path} entry {name!r} has an absolute layout {layout!r} — \"\n")
    _sub(root, "harness/run_eval.py",
         "        resolved[name] = {\"name\": name, \"path\": path, \"layout\": entry[\"layout\"],\n"
         "                          \"url\": entry[\"url\"], \"source\": source}\n",
         "        resolved[name] = {\"name\": name, \"path\": path, \"layout\": entry[\"layout\"],\n"
         "                          \"url\": entry[\"url\"], \"source\": source,\n"
         "                          \"may_be_empty\": entry.get(\"may_be_empty\", False)}\n")

    # --- scripts/eval_coverage.py: a declared-empty registry resolves with 0 skills
    _sub(root, "scripts/eval_coverage.py",
         "    if not rows:\n"
         "        raise CensusRefusal(\n"
         "            f\"registry {entry['name']!r} yields no skills for layout \"\n",
         "    if not rows:\n"
         "        if entry.get(\"may_be_empty\") is True:\n"
         "            return rows  # harness/registries.yml says this one may be empty\n"
         "        raise CensusRefusal(\n"
         "            f\"registry {entry['name']!r} yields no skills for layout \"\n")
    _sub(root, "scripts/eval_coverage.py",
         "directory, a layout that matches no skill, one skill name in two bundles of one\n",
         "directory, a layout that matches no skill (unless the registry's entry in\n"
         "`harness/registries.yml` says `may_be_empty: true`), one skill name in two bundles of one\n")

    # --- workflows: no checkout, flag or comment about the example sites
    wf = ".github/workflows/"
    _drop_site_checkouts(root, wf + "routine-eval-weekly.yml", 2)
    _drop_site_flags(root, wf + "routine-eval-weekly.yml", 2)
    _drop_site_checkouts(root, wf + "skill-coverage.yml", 4)
    _drop_site_flags(root, wf + "skill-coverage.yml", 6)
    _drop_site_checkouts(root, wf + "propagation.yml", 2)
    _drop_site_flags(root, wf + "propagation.yml", 2)

    ev = wf + "eval.yml"
    _drop_site_checkouts(root, ev, 2)
    _sub(root, ev,
         "            --registry acsi-agentskills=../acsi-agentskills \\\n"
         "            --registry example-site-platform=../example-site-platform \\\n"
         "            --registry example-site-a=../example-site-a\n",
         "            --registry acsi-agentskills=../acsi-agentskills\n")
    _sub(root, ev,
         "#   The trust boundary equally includes the three registries checked out\n"
         "#   below (acsi-agentskills, example-site-platform, example-site-a): the with_skill arm\n"
         "#   installs skill content from whichever registry the fixture names, from\n"
         "#   that registry's default branch, and executes it under bypassPermissions\n"
         "#   with the key in env — write access to any of the three checked-out\n"
         "#   registries is equivalent to key access here. All four repos (this one\n"
         "#   included) are maintainer-only; keep it that way or drop the key.\n"
         "#   example-site-platform and example-site-a also land commits on their default\n"
         "#   branches through automated lanes — Decap CMS editorial publish loops,\n"
         "#   auto-merge nudges, dependabot auto-merge — not only direct maintainer\n"
         "#   pushes. A commit landed by one of those lanes is inside this trust\n"
         "#   boundary just the same as a maintainer's own push: treat it as trusted,\n"
         "#   or drop the key.\n",
         "#   The trust boundary equally includes the registry checked out\n"
         "#   below (acsi-agentskills): the with_skill arm\n"
         "#   installs skill content from whichever registry the fixture names, from\n"
         "#   that registry's default branch, and executes it under bypassPermissions\n"
         "#   with the key in env — write access to the checked-out\n"
         "#   registry is equivalent to key access here. Both repos (this one\n"
         "#   included) are maintainer-only; keep it that way or drop the key.\n"
         "#   A commit landed on the registry's default branch by one of the\n"
         "#   automated lanes (auto-merge nudges, dependabot auto-merge) is inside this trust\n"
         "#   boundary just the same as a maintainer's own push: treat it as trusted,\n"
         "#   or drop the key.\n")
    _sub(root, ev, "# * All five checkouts (distinct repositories)", "# * All three checkouts (distinct repositories)")

    # --- README: the prose follows the registry list
    _sub(root, "README.md",
         "`../<name>` next to this repo — so `acsi-agentskills`, `example-site-platform`, and\n"
         "`example-site-a` checkouts next to `skills-evals/` resolve with no flags at\n"
         "all.",
         "`../<name>` next to this repo — so `acsi-agentskills` and\n"
         "`acsi-agentskills-private` checkouts next to `skills-evals/` resolve with no flags at\n"
         "all.")
    _sub(root, "README.md",
         "`plugins/<bundle>/skills/<skill>/` shape), `skills/<skill>` (example-site-platform),\n"
         "and `.claude/skills/<skill>` (example-site-a) all resolve through the same\n"
         "code path.",
         "`plugins/<bundle>/skills/<skill>/` shape), `skills/<skill>` (a flat registry),\n"
         "and `.claude/skills/<skill>` (a dot-dir registry) all resolve through the same\n"
         "code path.")
    _sub(root, "README.md",
         "fixture content; fixtures here — and the three checked-out registries\n"
         "(acsi-agentskills, example-site-platform, example-site-a) whose skill content the\n"
         "with-skill arm executes — are trusted because only maintainers can push to\n"
         "any of the four repos involved, directly or through an automated lane such\n"
         "as example-site-platform's Decap CMS publish loop or dependabot auto-merge; see\n",
         "fixture content; fixtures here — and the checked-out registry\n"
         "(acsi-agentskills) whose skill content the\n"
         "with-skill arm executes — are trusted because only maintainers can push to\n"
         "either of the two repos involved, directly or through an automated lane such\n"
         "as dependabot auto-merge; see\n")
