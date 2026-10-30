"""DECISIONS C11: repos.yml names only the kit's repos.

repos.yml is live config: sync.sh delivers to `skills_bootstrap.repos`,
check-registry.js holds `repos` and `out_of_scope` to a partition, and
check-cron-coverage.js expects every `cron_coverage.fleet` name checked out.
The source names the owner's other repos (personal tools, scratch repos,
forks); an adopter has none of them. Each block below is cut whole, with the
comment that explains that entry. The comments about the lists as a whole
stay, as history.

Runs on the substituted tree. After the cuts it checks that every repo the
file names is in `kit_repos` (manifests/_common.yml).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _cut(text: str, start: str, end: str | None, where: str) -> str:
    """Remove from `start` (inclusive) to `end` (exclusive); each once."""
    if text.count(start) != 1:
        raise SystemExit(f"kit_repos: {where}: expected 1 of {start[:60]!r}, found {text.count(start)}")
    i = text.index(start)
    if end is None:
        return text[:i]
    j = text.find(end, i + len(start))
    if j < 0 or text.count(end) != 1:
        raise SystemExit(f"kit_repos: {where}: expected 1 of {end[:60]!r} after the start")
    return text[:i] + text[j:]


def _replace(text: str, old: str, new: str, where: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"kit_repos: {where}: expected 1 of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


# (start, end): each removes one or more entries with their comments.
REPOS_YML_CUTS = [
    # skills_bootstrap.repos
    ("    # lock: committed — Adam-S-Daniel/ai-usage-dashboard#7",
     "    # example-site-platform gitignores"),
    ("    # lock: committed — PR #8 merged 2026-08-19.\n    - fastmail-actions\n",
     "    # lock: committed — PR #134 merged 2026-08-16"),
    ("    # The `scratch-` prefix is a misnomer",
     "    # lock: committed — PR #9 merged 2026-08-19.\n    - wsl-automation\n"),
    # skills_bootstrap.out_of_scope: the scratch, dormant and feed repos.
    ("    - repo: scratch-claude-001\n",
     "  # Forks and archived repos are NOT listed above"),
]

REPOS_YML_LINES = [
    # cron_coverage.fleet
    ("    - ai-usage-dashboard\n    - claude-memory-map\n", ""),
    ("    - fastmail-actions\n", ""),
    ("    - rss-inator\n", ""),
]


# A test that pinned the list's size to the source fleet's.
TEST_EDITS = [
    ("test/test-prompt-audit-sweep.js",
     "fleet.includes('_agent-guidance') && fleet.length >= 10);",
     "fleet.includes('_agent-guidance') && fleet.length >= 6);  // the kit's repos (DECISIONS C11)"),
]


def apply(root: Path, ctx) -> None:
    kit = set(ctx["common"]["kit_repos"])
    path = root / "repos.yml"
    text = path.read_text(encoding="utf-8")
    for start, end in REPOS_YML_CUTS:
        text = _cut(text, start, end, "repos.yml")
    for old, new in REPOS_YML_LINES:
        text = _replace(text, old, new, "repos.yml")
    # cron_coverage.out_of_scope: every entry is a repo outside the kit.
    head, sep, tail = text.partition("  out_of_scope:\n    # Scratch and unexamined.")
    if not sep:
        raise SystemExit("kit_repos: repos.yml: cron_coverage.out_of_scope not found")
    text = head + ("  # The kit's repos are all audited, so nothing is out of scope yet (DECISIONS\n"
                   "  # C11 dropped the source's scratch repos and forks). Add a repo here, with\n"
                   "  # a comment saying why, when it should not be audited.\n"
                   "  out_of_scope: []\n")
    path.write_text(text, encoding="utf-8")

    for rel, old, new in TEST_EDITS:
        p = root / rel
        p.write_text(_replace(p.read_text(encoding="utf-8"), old, new, rel), encoding="utf-8")

    doc = yaml.safe_load(text)
    sb = doc["skills_bootstrap"]
    named = list(sb["repos"]) + [e["repo"] for e in sb["out_of_scope"]]
    named += list(doc["cron_coverage"]["fleet"]) + list(doc["cron_coverage"]["out_of_scope"])
    stray = sorted(set(named) - kit)
    if stray:
        raise SystemExit(f"kit_repos: repos.yml still names repos outside the kit: {stray}")
