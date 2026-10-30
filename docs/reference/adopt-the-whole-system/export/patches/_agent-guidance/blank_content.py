"""DECISIONS X9 and Content tiers (2026-10-29): what the guidance and the git hook ship.

Runs on the substituted tree, before the regenerate steps (which rebuild the
fleet payload mirror, the section bytes and AGENTS.md from what this leaves).

The owner sorted the content into tiers on 2026-10-29. CORE items are
exported here, as the source has them; RECOMMENDED and OPINIONATED items are
moved to the kit's library (ctx["library"]), where bootstrap/add-content.sh
finds them; the rest is not shipped. bootstrap/content-catalog.json lists the
tiers, and the tests check this file against it.

- agents-md/base.md keeps its preamble, an HTML-commented template section
  (it sits before the first heading, so no section's byte extent counts it,
  and it has no line that starts with `## `), and the CORE sections, each
  byte for byte, in the source's order. Two source sections mix core and
  general rules and are split (SPLITS): the core bullets stay under the
  original heading, the general ones move to a new section of their own in
  the library, and two bullets are not shipped. Moved bullets keep their
  text; the merge-commit bullet loses one clause (the source's own repos
  that squash).
- Each optional section goes to library/guidance/<id>/agent-guidance/: its
  text, its eval-coverage.yml row (the source's, or a `gap` row for a new
  part), a guidance-impact.md entry, and where it goes (the headings and row
  ids that come before it in the source, nearest first).
- agents-md/sections/*.md keep their `##` heading (the slot a language's
  rules go under) and no rules: build-agents-md.sh still takes a section
  name and appends the file, and sync.sh's tests check that the heading
  arrives in the built AGENTS.md.
- agents-md/eval-coverage.yml keeps its header comments, the core sections'
  rows as the source has them and one `gap` row per empty section file (the
  gate needs a row per heading).
- docs/guidance-impact.md keeps its header and entry format, with no entries.
- docs/agent-checks-lessons.md keeps its header and table head, with no rows.
- templates/agent-checks/agent-checks (and this repo's own copy in
  .githooks/) keeps the runner: events, the problem/note_ran bookkeeping,
  the run log, the empty lesson slot (check_lessons) and the repo-local
  .agent-checks/local.sh mechanism. Of the fleet checks it keeps the two
  core ones, `secret-file` and `skill-shell`, exactly as the source has
  them, and a commented-out example check (`.orig`/`.rej` leftovers). The
  opinionated `rules-dir` check goes to library/git-hooks/rules-dir/ with
  its test cases (machinery_tests.py writes the test half). The pre-push
  checks are not shipped.
"""

from __future__ import annotations

import re
from pathlib import Path


def _sub_once(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"blank_content: expected 1 occurrence in {where}, found {n}: {old[:70]!r}")
    return text.replace(old, new)


def _cut_before(text: str, pattern: str, where: str) -> str:
    m = re.search(pattern, text, re.M)
    if not m:
        raise SystemExit(f"blank_content: {where}: no match for {pattern!r}")
    return text[: m.start()]


# The source's sections in order, as (heading text, coverage row id, tier).
# tier: core | recommended | opinionated | none (not shipped).
SECTIONS = [
    ("Working in these repos", "working-in-these-repos", "core"),
    ("Anything you name gets its link", "anything-you-name-gets-its-link", "opinionated"),
    ("Finding your unknowns", "finding-your-unknowns", "opinionated"),
    ("Workstation layout", "workstation-layout", "none"),
    ("Security", "security", "recommended"),
    ("Data exposure in CI and public repos", "data-exposure-in-ci", "core"),
    ("Read before you start", "read-before-you-start", "none"),
    ("Automation vs branch protection", "automation-vs-branch-protection", "core"),
    ("Two GitHub connectors, and which one you are holding", "two-github-connectors", "none"),
    ("Agent-made GitHub contributions carry a marker", "agent-contribution-markers", "core"),
    ('A GitHub 404 means "not authorized", not "not there"', "github-404-means-not-authorized", "recommended"),
    ("The fleet spans TWO owners, and a scoped search will not say so", "fleet-spans-two-owners", "none"),
    ('"The watch finished" is not "CI passed"', "watch-finished-is-not-ci-passed", "core"),
    ("A successful `git push` does not mean your commit exists", "git-push-does-not-mean-commit-exists", "core"),
    ("Dependency updates", "dependency-updates", "opinionated"),
    ("A name you choose becomes data a scanner reads", "name-becomes-scanner-data", "recommended"),
    ("Pinning GitHub Actions", "pinning-github-actions", "core"),
    ("A test that can signal can kill every session", "test-that-can-signal-kills-every-session", "recommended"),
    ("Subagent delegation (model routing)", "subagent-delegation", "opinionated"),
    ("Skills ecosystem", "skills-ecosystem", "core"),
    ("Two setup gaps you may close, and must not nag about", "two-setup-gaps", "core"),
    ("Git practices", "git-practices", "core"),
]

# The two mixed sections. Each bullet is named by the start of its first
# line and sent to the core part (kept under the source heading), the
# general part (a new section placed right after it) or nowhere.
SPLITS = {
    "Working in these repos": {
        "general": ("Working habits", "working-habits", "recommended"),
        "bullets": [
            ("- **An issue or PR labeled `on-hold` is paused by the owner.**", "core"),
            ("- Fix what was asked:", "general"),
            ("- A public interface change updates its tests;", "general"),
            ("- **American English spelling**", "none"),
            ("- Tests are deterministic:", "general"),
            ("- **Keep full numeric precision until the final output:**", "general"),
            ("- **A lint or check that reasons about code SHAPE**", "general"),
            ("- Writing that goes out under the owner's name:", "none"),
            ("- Text to paste:", "general"),
        ],
    },
    "Git practices": {
        "general": ("Git habits", "git-habits", "recommended"),
        "bullets": [
            ("- Concise commit messages that explain *why*;", "general"),
            ("- **Merge with a merge commit — `gh pr merge --merge`.**", "core"),
            ("- **A closing keyword before `#N` or an issue/PR URL, any repo, closes it,", "general"),
            ("- **Before a PR, `git log origin/<base>..HEAD --oneline` lists only this", "general"),
            ("- **One worktree per independent coding session**", "general"),
        ],
    },
}

# The one clause dropped from a moved bullet: the source owner's own three
# repos that squash for their CMS (not shipped, the owner's 2026-10-29 list).
MERGE_CLAUSE = (
    " are off (`--squash` fails; do not offer it) except the three\n"
    "  example-site-platform-managed repos (`example-site-platform`, `example-site-a`,\n"
    "  `example-site-b`), kept for Decap publishing. Squash strands commits a\n"
    "  lockfile pins by sha (`cannot resolve ref`).\n",
    " are off (`--squash` fails; do not offer it). Squash strands commits a\n"
    "  lockfile pins by sha (`cannot resolve ref`).\n",
)

IMPACT_DATE = "2026-10-29"

# A template section as an HTML comment: the coverage checker's markdown parse
# sees one html block, not a heading. No line starts with `## ` (a line-regex
# reader would take it for a heading), so the heading is shown indented.
TEMPLATE = """<!-- TEMPLATE: copy the block below outside this comment to add a section.

    ## A short title naming the situation, in the words an agent would search for

    One line of why: the incident or pattern behind the rules, with a date.

    - **The rule, in bold.** One sentence on what to do instead.
    - **A second rule.** Keep each rule checkable by an eval fixture.

Then, in the same pull request, add a row for the heading to
`agents-md/eval-coverage.yml` (status `gap` until a skills-evals fixture
exists, then `covered`), add an entry to `docs/guidance-impact.md`, and run
`node scripts/check-guidance-coverage.js --write-bytes`.
-->
"""

SECTION_BODY = """None yet: add this stack's rules as bullets here. A repo gets them by
naming the section in its `.agents-sync.yml`.
"""

SECTION_ROW = """- id: {id}
  heading: {heading}
  file: {file}
  status: gap
  bytes: 0
"""

GAP_ROW = """- id: {id}
  heading: {heading}
  file: agents-md/base.md
  status: gap
  bytes: 0
"""

IMPACT_ENTRY = """## {date} — {id} — create
- Motivation: added from the adoption kit's library ({tier} tier), chosen with `bootstrap/add-content.sh`.
- Change: the section "{heading}" is added to `agents-md/base.md`, with its row in `agents-md/eval-coverage.yml`{origin}.
- Eval: {eval}
- Outcome: merged with the starter's pull request, or with the pull request that added it.
"""

RUNNER_EDITS = [
    # The run-log example names only checks the kit ships.
    ('#    "checks":["rules-dir","secret-file","skill-shell"],"failed":["secret-file"]}\n',
     '#    "checks":["secret-file","skill-shell"],"failed":["secret-file"]}\n'),
    # The path checks: the opinionated rules-dir arm goes to the library.
    ('''# Reads NUL-separated paths on stdin and refuses the ones that must never be
# committed.
check_paths() {
  local path
  while IFS= read -r -d '' path; do
    [ -n "$path" ] || continue
    case "$path" in
      .claude/rules/*)
        problem rules-dir "$path: path-scoped rules (.claude/rules/) are not used in this fleet. Only Claude Code reads them. Put the rule in AGENTS.md or a skill, or make it a check in .githooks/agent-checks." ;;
    esac
    case "${path##*/}" in
''',
     '''# Reads NUL-separated paths on stdin and refuses the ones that must never be
# committed: the fleet checks below, then each lesson (a `case` arm in
# check_lessons). A check of your own goes beside the fleet checks (the
# commented example below).
check_paths() {
  local path
  while IFS= read -r -d '' path; do
    [ -n "$path" ] || continue
    case "${path##*/}" in
'''),
    ('''      problem skill-shell "$path: a line starts a shell command that Claude Code runs when the skill loads (an exclamation mark then a backtick, at line start or after a space, or a code fence opened with a backtick fence and an exclamation mark). It runs before the model reads the skill, so no review and no prompt-injection defense sees it. Have the skill tell the agent to run the command, or move the command into a script the skill calls."
    fi
    check_lessons "$path" < /dev/null
''',
     '''      problem skill-shell "$path: a line starts a shell command that Claude Code runs when the skill loads (an exclamation mark then a backtick, at line start or after a space, or a code fence opened with a backtick fence and an exclamation mark). It runs before the model reads the skill, so no review and no prompt-injection defense sees it. Have the skill tell the agent to run the command, or move the command into a script the skill calls."
    fi
    # TEMPLATE, commented out: a check of your own. Uncomment it, give it a
    # kebab-case id, add `note_ran <id>` in run_checks so the run log counts
    # it, and make the message say what is wrong, why, and how to fix it.
    # Name the path; never print file content.
    #
    #   case "${path##*/}" in
    #     *.orig|*.rej)
    #       problem leftover-patch "$path: a .orig or .rej file is what a failed merge or patch leaves behind, and it does not belong in the repo. Delete it (git rm --cached -- '$path') and add its pattern to .gitignore." ;;
    #   esac
    check_lessons "$path" < /dev/null
'''),
    ('''  local list id
  note_ran rules-dir
  note_ran secret-file
''',
     '''  local list id
  note_ran secret-file
'''),
    ('''default_branch() {
  local remote=$1 head
  head=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null) || head=""
  if [ -n "$head" ]; then
    printf '%s\\n' "${head#"$remote"/}"
  else
    printf '%s\\n' main master
  fi
}

''', ''),
    ('''    remote=${2:-origin}
    note_ran staged-index
    note_ran push-to-default
    note_ran persistent-delete
    if ! git diff --cached --quiet; then
      problem staged-index "the index has staged changes that are in no commit. If your last 'git commit' was refused by a hook, this push would publish the commit before it and report success. Commit or unstage them first (git status), then push."
    fi
    defaults=$(default_branch "$remote")
    while read -r _ local_oid remote_ref remote_oid; do
      [ -n "${remote_ref:-}" ] || continue
      for branch in $defaults; do
        if [ "$remote_ref" = "refs/heads/$branch" ]; then
          problem push-to-default "refusing to push to $branch: fleet default branches take changes only through pull requests, and GitHub would reject it (GH013). Push a feature branch and open a PR."
        fi
      done
      if is_zero "$local_oid"; then
        case "$remote_ref" in
          refs/heads/persistent/*)
            problem persistent-delete "refusing to delete ${remote_ref#refs/heads/}: persistent/* branches outlive their PRs and hold data (results, usage records), and deleting one loses it. Private repos without rulesets have nothing else to stop this. If it really must go, ask the owner." ;;
        esac
        continue
      fi
''',
     '''    remote=${2:-origin}
    while read -r _ local_oid remote_ref remote_oid; do
      [ -n "${remote_ref:-}" ] || continue
      # A delete pushes no content to check.
      is_zero "$local_oid" && continue
'''),
]

# The rules-dir check, as the library item puts it back (inverse of the edits above).
RULES_DIR_ARM = '''    case "$path" in
      .claude/rules/*)
        problem rules-dir "$path: path-scoped rules (.claude/rules/) are not used in this fleet. Only Claude Code reads them. Put the rule in AGENTS.md or a skill, or make it a check in .githooks/agent-checks." ;;
    esac
'''
RULES_DIR_ANCHOR = '''    case "${path##*/}" in
      .env.example|.env.sample|.env.template) ;;
'''


def _sections(source: str) -> dict:
    """heading text -> its source extent (heading line through the line before the next `## `)."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"^## (.+)$", source, re.M)]
    out = {}
    for i, (pos, heading) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(source)
        out[heading] = source[pos:end].rstrip("\n") + "\n"
    return out


def _bullets(text: str, heading: str) -> list:
    body = text.split("\n", 2)[2]  # after "## H\n\n"
    items = re.split(r"(?m)^(?=- )", body)
    if items[0].strip():
        raise SystemExit(f"blank_content: {heading!r} has text before its first bullet")
    return [b if b.endswith("\n") else b + "\n" for b in items[1:]]


def _rows(cov_text: str) -> dict:
    return {m.group(1): m.group(0) for m in re.finditer(r"^- id: (\S+)\n(?:  .*\n)+", cov_text, re.M)}


def _layout():
    """The final order: (heading, id, tier, source text or None) with the general parts placed."""
    order = []
    for heading, rid, tier in SECTIONS:
        order.append((heading, rid, tier))
        if heading in SPLITS:
            order.append(SPLITS[heading]["general"])
    return order


def apply(root: Path, ctx) -> None:
    lib = ctx["library"]
    base = root / "agents-md" / "base.md"
    source = base.read_text(encoding="utf-8")
    found = _sections(source)
    if list(found) != [h for h, _, _ in SECTIONS]:
        raise SystemExit(f"blank_content: base.md's sections changed: {sorted(set(found) ^ {h for h, _, _ in SECTIONS})}")

    cov = root / "agents-md" / "eval-coverage.yml"
    cov_text = cov.read_text(encoding="utf-8")
    rows = _rows(cov_text)

    texts = {}  # heading -> text of the section as shipped (core or library)
    for heading, rid, tier in SECTIONS:
        text = found[heading]
        if heading in SPLITS:
            spec = SPLITS[heading]
            bullets = _bullets(text, heading)
            if len(bullets) != len(spec["bullets"]):
                raise SystemExit(f"blank_content: {heading!r} has {len(bullets)} bullets, expected {len(spec['bullets'])}")
            parts = {"core": [], "general": [], "none": []}
            for bullet, (start, part) in zip(bullets, spec["bullets"]):
                if not bullet.startswith(start):
                    raise SystemExit(f"blank_content: {heading!r}: a bullet does not start with {start!r}")
                parts[part].append(bullet)
            core = "".join(parts["core"])
            if heading == "Git practices":
                core = _sub_once(core, *MERGE_CLAUSE, "the merge-commit bullet")
            texts[heading] = f"## {heading}\n\n{core}"
            g_heading = spec["general"][0]
            texts[g_heading] = f"## {g_heading}\n\n{''.join(parts['general'])}"
        else:
            texts[heading] = text

    order = _layout()
    shipped = [(h, rid, tier) for h, rid, tier in order if tier != "none"]
    core_sections = [texts[h] for h, _, tier in shipped if tier == "core"]
    preamble = _cut_before(source, r"^## ", "base.md").rstrip("\n")
    base.write_text(preamble + "\n\n" + TEMPLATE + "\n" + "\n".join(core_sections), encoding="utf-8")

    # The optional sections, each with where it goes.
    for i, (heading, rid, tier) in enumerate(shipped):
        if tier == "core":
            continue
        before = shipped[:i][::-1]
        if rid in rows:
            row = rows[rid]
            if heading not in row:
                raise SystemExit(f"blank_content: the {rid} row does not name {heading!r}")
            fixture = re.search(r"^  fixture: (\S+)$", row, re.M)
            evl = (f"not run at install (n=0); its fixtures under `{fixture.group(1)}` in skills-evals come with it."
                   if fixture else "none — no fixture yet")
            origin = ""
        else:
            row = GAP_ROW.format(id=rid, heading=heading)
            evl = "none — no fixture yet"
            split_from = next(h for h, s in SPLITS.items() if s["general"][1] == rid)
            origin = f'; its rules were the general half of the source\'s "{split_from}"'
        lib.write_fragment("guidance", rid, "agent-guidance", "section.md", texts[heading])
        lib.write_fragment("guidance", rid, "agent-guidance", "coverage-row.yml", row)
        entry = IMPACT_ENTRY.format(date=IMPACT_DATE, id=rid, tier=tier, heading=heading, origin=origin, eval=evl)
        lib.write_fragment("guidance", rid, "agent-guidance", "impact-entry.md", entry)
        lib.add_ops("guidance", rid, "agent-guidance", [
            {"op": "insert-section", "file": "agents-md/base.md", "fragment": "section.md",
             "heading": f"## {heading}", "follows": [f"## {h}" for h, _, _ in before]},
            {"op": "coverage-row", "file": "agents-md/eval-coverage.yml", "fragment": "coverage-row.yml",
             "id": rid, "follows": [r for _, r, _ in before]},
            {"op": "impact-entry", "file": "docs/guidance-impact.md", "fragment": "impact-entry.md",
             "heading": entry.splitlines()[0]},
        ])

    # eval-coverage.yml: the core rows, then a gap row per empty section file.
    sections = sorted((root / "agents-md" / "sections").glob("*.md"))
    if not sections:
        raise SystemExit("blank_content: no agents-md/sections/*.md")
    out_rows = []
    for heading, rid, tier in shipped:
        if tier != "core":
            continue
        row = rows.get(rid)
        if row is None or "  status: covered\n" not in row or "  fixture: " not in row:
            raise SystemExit(f"blank_content: the {rid} row is not a covered row with a fixture")
        out_rows.append(row)
    for f in sections:
        heading = f.read_text(encoding="utf-8").splitlines()[0]
        if not heading.startswith("## "):
            raise SystemExit(f"blank_content: {f.name} does not start with a ## heading")
        f.write_text(heading + "\n\n" + SECTION_BODY, encoding="utf-8")
        out_rows.append(SECTION_ROW.format(id=f"section-{f.stem}", heading=heading[3:], file=f"agents-md/sections/{f.name}"))

    head = _cut_before(cov_text, r"^- id:", "eval-coverage.yml")
    cov.write_text(head.rstrip("\n") + "\n\n" + "\n".join(out_rows), encoding="utf-8")

    impact = root / "docs" / "guidance-impact.md"
    impact.write_text(_cut_before(impact.read_text(encoding="utf-8"), r"^## \d{4}-\d{2}-\d{2} ", "guidance-impact.md"), encoding="utf-8")

    ledger = root / "docs" / "agent-checks-lessons.md"
    text = ledger.read_text(encoding="utf-8")
    m = re.search(r"^\|-+\|.*\n", text, re.M)
    if not m:
        raise SystemExit("blank_content: agent-checks-lessons.md has no table")
    ledger.write_text(text[: m.end()], encoding="utf-8")

    runner_rel = "templates/agent-checks/agent-checks"
    runner = root / runner_rel
    text = runner.read_text(encoding="utf-8")
    for old, new in RUNNER_EDITS:
        text = _sub_once(text, old, new, runner_rel)
    if text.count(RULES_DIR_ANCHOR) != 1 or text.count("  note_ran secret-file\n") != 1:
        raise SystemExit("blank_content: the rules-dir item's anchors are not each once in the runner")
    runner.write_text(text, encoding="utf-8")
    # This repo's own hook is the same file the sync delivers.
    (root / ".githooks" / "agent-checks").write_text(text, encoding="utf-8")

    lib.write_fragment("git-hook", "rules-dir", "agent-guidance", "check.sh", RULES_DIR_ARM)
    lib.write_fragment("git-hook", "rules-dir", "agent-guidance", "note-ran.sh", "  note_ran rules-dir\n")
    lib.add_ops("git-hook", "rules-dir", "agent-guidance", [
        {"op": "insert-before", "file": runner_rel, "anchor": RULES_DIR_ANCHOR, "fragment": "check.sh"},
        {"op": "insert-before", "file": runner_rel, "anchor": "  note_ran secret-file\n", "fragment": "note-ran.sh"},
        {"op": "replace", "file": runner_rel,
         "old": '#    "checks":["secret-file","skill-shell"],"failed":["secret-file"]}\n',
         "new": '#    "checks":["rules-dir","secret-file","skill-shell"],"failed":["secret-file"]}\n'},
    ])
