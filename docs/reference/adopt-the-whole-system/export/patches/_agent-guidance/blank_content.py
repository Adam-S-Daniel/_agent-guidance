"""DECISIONS X9: the kit ships the machinery with its content blank.

Runs on the substituted tree, before the regenerate steps (which rebuild the
fleet payload mirror, the section bytes and AGENTS.md from what this leaves).

- agents-md/base.md keeps its preamble (title and the paragraph about what
  the file is for) and one empty slot, `## Fleet rules`, which holds no rule.
  The slot is there because the tooling needs a heading:
  check-guidance-coverage.js refuses (exit 2) a payload with zero `##`
  headings, and CI runs it.
- agents-md/sections/*.md keep their `##` heading (the slot a language's
  rules go under) and no rules: build-agents-md.sh still takes a section
  name and appends the file, and sync.sh's tests check that the heading
  arrives in the built AGENTS.md.
- agents-md/eval-coverage.yml keeps its header comments and one `gap` row
  per empty slot (the gate needs a row per heading).
- docs/guidance-impact.md keeps its header and entry format, with no entries.
- docs/agent-checks-lessons.md keeps its header and table head, with no rows.
- templates/agent-checks/agent-checks (and this repo's own copy in
  .githooks/) keeps the runner: events, the problem/note_ran bookkeeping,
  the run log, the empty lesson slot (check_lessons) and the repo-local
  .agent-checks/local.sh mechanism. It ships none of the fleet checks.
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


SLOT = """## Fleet rules

None yet: the kit starts with no guidance rules. Add a lesson as a bullet
under a `##` heading here, give the heading a row in
`agents-md/eval-coverage.yml`, and record the change in
`docs/guidance-impact.md`.
"""

SLOT_ROW = """- id: fleet-rules
  heading: Fleet rules
  file: agents-md/base.md
  status: gap
  bytes: 0
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

RUNNER_EDITS = [
    # The run-log example names checks the kit no longer ships.
    ('#    "checks":["rules-dir","secret-file","skill-shell"],"failed":["secret-file"]}\n',
     '#    "checks":["repo-local"],"failed":["repo-local"]}\n'),
    # The path checks: the loop and the lesson slot stay, the arms go.
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
      .env.example|.env.sample|.env.template) ;;
      .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa|id_ecdsa|id_ed25519)
        problem secret-file "$path: looks like a secret or credential file, and git history in a public repo is public. Unstage it (git restore --staged -- '$path') and add it to .gitignore." ;;
    esac
    # A skill or command file that runs a shell command when it loads.
    is_skill_file=false
    case "${path##*/}" in SKILL.md) is_skill_file=true ;; esac
    case "$path" in
      .claude/commands/*.md|*/.claude/commands/*.md|plugins/*/commands/*.md) is_skill_file=true ;;
    esac
    # A here-string, not a pipe: grep -q exits at the first match, which can
    # kill a writer still filling the pipe.
    if $is_skill_file \\
        && grep -Eq '(^|[[:space:]])!`|^[[:space:]]*```!' <<<"$(file_content "$path")"; then
      problem skill-shell "$path: a line starts a shell command that Claude Code runs when the skill loads (an exclamation mark then a backtick, at line start or after a space, or a code fence opened with a backtick fence and an exclamation mark). It runs before the model reads the skill, so no review and no prompt-injection defense sees it. Have the skill tell the agent to run the command, or move the command into a script the skill calls."
    fi
    check_lessons "$path" < /dev/null
  done
}
''',
     '''# Reads NUL-separated paths on stdin and runs the path checks on each one.
# The kit ships no checks: each lesson becomes a `case` arm in check_lessons.
check_paths() {
  local path
  while IFS= read -r -d '' path; do
    [ -n "$path" ] || continue
    check_lessons "$path" < /dev/null
  done
}
'''),
    ('''  local list id
  note_ran rules-dir
  note_ran secret-file
  note_ran skill-shell
  for id in $lesson_checks; do note_ran "$id"; done
''',
     '''  local list id
  for id in $lesson_checks; do note_ran "$id"; done
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


def apply(root: Path, ctx) -> None:
    # base.md: the preamble only.
    base = root / "agents-md" / "base.md"
    base.write_text(_cut_before(base.read_text(encoding="utf-8"), r"^## ", "base.md").rstrip("\n") + "\n\n" + SLOT, encoding="utf-8")

    sections = sorted((root / "agents-md" / "sections").glob("*.md"))
    if not sections:
        raise SystemExit("blank_content: no agents-md/sections/*.md")
    rows = [SLOT_ROW]
    for f in sections:
        heading = f.read_text(encoding="utf-8").splitlines()[0]
        if not heading.startswith("## "):
            raise SystemExit(f"blank_content: {f.name} does not start with a ## heading")
        f.write_text(heading + "\n\n" + SECTION_BODY, encoding="utf-8")
        rows.append(SECTION_ROW.format(id=f"section-{f.stem}", heading=heading[3:], file=f"agents-md/sections/{f.name}"))

    cov = root / "agents-md" / "eval-coverage.yml"
    head = _cut_before(cov.read_text(encoding="utf-8"), r"^- id:", "eval-coverage.yml")
    cov.write_text(head.rstrip("\n") + "\n\n" + "\n".join(rows), encoding="utf-8")

    impact = root / "docs" / "guidance-impact.md"
    impact.write_text(_cut_before(impact.read_text(encoding="utf-8"), r"^## \d{4}-\d{2}-\d{2} ", "guidance-impact.md"), encoding="utf-8")

    ledger = root / "docs" / "agent-checks-lessons.md"
    text = ledger.read_text(encoding="utf-8")
    m = re.search(r"^\|-+\|.*\n", text, re.M)
    if not m:
        raise SystemExit("blank_content: agent-checks-lessons.md has no table")
    ledger.write_text(text[: m.end()], encoding="utf-8")

    runner = root / "templates" / "agent-checks" / "agent-checks"
    text = runner.read_text(encoding="utf-8")
    for old, new in RUNNER_EDITS:
        text = _sub_once(text, old, new, "templates/agent-checks/agent-checks")
    runner.write_text(text, encoding="utf-8")
    # This repo's own hook is the same file the sync delivers.
    (root / ".githooks" / "agent-checks").write_text(text, encoding="utf-8")
