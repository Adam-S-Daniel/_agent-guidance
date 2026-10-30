"""DECISIONS X9 and Content tiers for the public registry: the core skills.

Runs on the substituted tree, after library_skills.py has moved the
optional skills to the library (the manifest leaves out the skills that are
not shipped), before the regenerate steps, which rebuild the README table
and skills.lock from what this leaves. The skills that stay are the owner's
four core skills (2026-10-29), all in the coding-anywhere plugin.

The machinery stays and is made to accept a registry with empty plugins,
because three of the four start that way:

- scripts/plugin_layout.json keeps its four plugins and their defaultEnabled
  values; the coding-anywhere plugin lists the core skills, the others
  have empty skill lists (canonical json.dumps(indent=2) + newline).
- generate_readme_table.py renders a plugin with no skills as ONE row that
  says so and names where its skills go. Its rule, "a published bundle is
  never silence in the README", is kept: before, an empty bundle broke that
  rule and was refused; now it renders a row, so the README still names it.
- The marketplace and plugin manifests drop the descriptions and keywords of
  skills the kit does not ship, and name the owner with the kit's
  `<your name>` placeholder (copy-starter.sh fills it from OWNER_NAME).

The tests that follow these changes are in machinery_tests.py.
"""

from __future__ import annotations

import json
from pathlib import Path


def _sub(root: Path, rel: str, pairs) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"blank_registry: expected 1 occurrence in {rel}, found {n}: {old[:70]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


# Per plugin: (old description after substitution, new description, old
# keywords as written inline in marketplace.json, new keywords).
PLUGINS = {
    "acsi-anything-anywhere": (
        "the owner's skills for any Claude surface, coding or not: the claude.ai web and iOS apps, "
        "Claude in Chrome, the Claude Desktop app, and Claude Code. Writing ADRs and resolving task ambiguity.",
        "Skills for any Claude surface, coding or not: the claude.ai web and iOS apps, "
        "Claude in Chrome, the Claude Desktop app, and Claude Code.",
        ["adr", "documentation", "requirements", "ambiguity", "planning"],
        ["skills", "writing", "planning"],
    ),
    "acsi-coding-anywhere": (
        "the owner's coding skills for Claude Code terminals and Claude Code cloud sessions: GitHub Actions "
        "debugging and hardening, settings-as-code, bash CI reliability review, skill provenance, disarming "
        "inherited reach, and filing vendor release-impact issues.",
        "Coding skills for Claude Code terminals and Claude Code cloud sessions.",
        ["github-actions", "ci-cd", "debugging", "security", "supply-chain", "settings-as-code", "bash",
         "code-review", "skills", "changelog", "release-notes"],
        ["skills", "coding", "claude-code"],
    ),
    "acsi-coding-local": (
        "the owner's machine-bound coding skills for Claude Code on the owner's own machines (WSL and Windows "
        "homes): Claude Code settings sync, session and memory management, and elevation from WSL.",
        "Machine-bound coding skills and hooks for Claude Code on your own machines (WSL and Windows homes): "
        "clone sync and the API-credit lane.",
        ["claude-code", "wsl", "windows", "sync", "settings", "memory", "skills"],
        ["claude-code", "wsl", "windows", "sync", "skills"],
    ),
    "acsi-non-coding-local": (
        "the owner's non-coding skills that need local files or a signed-in browser, for the Claude Desktop "
        "app's local Cowork: PDF OCR, audit, renaming and comparison, and Fastmail email and "
        "sending-identity workflows.",
        "Non-coding skills that need local files or a signed-in browser, for the Claude Desktop app's local "
        "Cowork.",
        ["pdf", "ocr", "fastmail", "email", "identities", "from-address", "browser", "claude-in-chrome"],
        ["skills", "documents", "browser"],
    ),
}


# The core skills the kit ships (DECISIONS X9 and Content tiers), per plugin.
# The optional ones are in the library (library_skills.py), and
# bootstrap/add-content.sh adds each chosen one to this list.
KEPT_SKILLS = {"acsi-coding-anywhere": ["github-actions-repo-settings", "skills-doctor",
                                        "vendor-release-impact-issues", "workflow-path-audit"]}


def _inline(words) -> str:
    return "[" + ", ".join(json.dumps(w) for w in words) + "]"


def _block(words) -> str:
    return "[\n" + ",\n".join(f"    {json.dumps(w)}" for w in words) + "\n  ]"


def apply(root: Path, ctx) -> None:
    # ── scripts/plugin_layout.json ─────────────────────────────────────────
    layout_path = root / "scripts" / "plugin_layout.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    for name, entry in layout["plugins"].items():
        entry["skills"] = list(KEPT_SKILLS.get(name, []))
    layout_path.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")

    # ── the manifests ──────────────────────────────────────────────────────
    market = [
        ('"owner": { "name": "the owner" }', '"owner": { "name": "<your name>" }'),
        ('"description": "the owner\'s reusable agent skills, packaged',
         '"description": "Reusable agent skills, packaged'),
    ]
    for name, (old_desc, new_desc, old_kw, new_kw) in PLUGINS.items():
        market += [(f'"description": {json.dumps(old_desc)}', f'"description": {json.dumps(new_desc)}'),
                   (f'"keywords": {_inline(old_kw)}', f'"keywords": {_inline(new_kw)}')]
        _sub(root, f"plugins/{name}/.claude-plugin/plugin.json", [
            (f'"description": {json.dumps(old_desc)}', f'"description": {json.dumps(new_desc)}'),
            ('"author": { "name": "the owner" }', '"author": { "name": "<your name>" }'),
        ])
        _sub(root, f"plugins/{name}/plugin.json", [
            (f'"description": {json.dumps(old_desc)}', f'"description": {json.dumps(new_desc)}'),
            ('"author": { "name": "the owner" }', '"author": { "name": "<your name>" }'),
            (f'"keywords": {_block(old_kw)}', f'"keywords": {_block(new_kw)}'),
        ])
    _sub(root, ".claude-plugin/marketplace.json", market)

    # ── scripts/generate_readme_table.py ───────────────────────────────────
    _sub(root, "scripts/generate_readme_table.py", [
        ('''def local_rows(plugin: dict, plugins_dir: Path) -> List[str]:
    """One row per plugins/<plugin>/skills/<skill>/ that has a SKILL.md."""
''', '''def local_rows(plugin: dict, plugins_dir: Path) -> List[str]:
    """One row per plugins/<plugin>/skills/<skill>/ that has a SKILL.md.

    A plugin with no skills yet (a new registry starts that way) renders ONE
    row that says so and names where its skills go, so the README still
    mentions it: an empty bundle is a state to show, not a row to omit.
    """
'''),
        ('''        rows.append(_row(plugin_name, f"`/{plugin_name}:{skill_dir.name}`", description))
    return rows
''', '''        rows.append(_row(plugin_name, f"`/{plugin_name}:{skill_dir.name}`", description))
    if not rows:
        rows.append(_row(plugin_name, f"none yet — add skills under `plugins/{plugin_name}/skills/`",
                         _first_sentence(fallback_desc)))
    return rows
'''),
    ])
