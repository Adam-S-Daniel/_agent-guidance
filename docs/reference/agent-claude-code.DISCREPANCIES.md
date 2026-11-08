# Discrepancies — Claude Code behavior vs. its release log

Cases where observed Claude Code behavior contradicts, or goes beyond, what its
[release log](https://github.com/anthropics/claude-code/releases) says. Sidecar to
[`agent-claude-code.CHANGELOG.md`](agent-claude-code.CHANGELOG.md). Twin file, same
structure: [`agent-codex.DISCREPANCIES.md`](agent-codex.DISCREPANCIES.md).

## How to add an entry

Add one entry per discrepancy at the top of **Entries**, in a PR to this repo,
and link it from the issue or PR where you found it. Consult vendor docs only
to resolve an ambiguity, and quote what you relied on. When the vendor fixes it,
update **Status**; don't delete the entry. Keep evidence public-safe: no tokens,
emails, or personal paths.

In the same PR, follow [`agent-discrepancy-process.md`](agent-discrepancy-process.md):
search the vendor's tracker, decide whether to propose a vendor issue, and draft
it. Merging the PR alerts the repo owner.

```markdown
### YYYY-MM-DD — <one-line summary>

- **Kind:** contradicts changelog | undocumented change | changelog ambiguous | docs disagree with changelog
- **Status:** open | worked around | reported upstream | fixed in <version>
- **Observed on:** `claude --version` output; surface (local CLI, cloud session, CI, SDK); OS
- **Changelog says:** > exact quote — [v2.1.N](https://github.com/anthropics/claude-code/releases/tag/v2.1.N), published YYYY-MM-DDTHH:MMZ, or "nothing" plus the version range where the behavior appeared
- **Docs say:** > exact quote — [page](URL), read YYYY-MM-DD (only if consulted)
- **Observed:** what happened, with the minimal repro commands and the output that shows it
- **Evidence:** link to the commit, test, CI run, or transcript that demonstrates it
- **Found in / action taken:** issue or PR link; what the repo did (workaround, pin, test, upstream report)
- **Vendor issues:** [#N](link) (open | closed, fixed in <version>), or `none found — searched YYYY-MM-DD: "<query>"; "<query>"`
- **Vendor proposal:** `draft: [<file>](vendor-issue-drafts/claude-code/<file>.md)` | `covered by an existing issue` | `not proposed — <reason>` | `submitted: <link>`
```

## Entries

### 2026-11-08 — A repo's own skills never load after `cd` into it from a multi-repo parent folder

- **Kind:** contradicts changelog
- **Status:** open
- **Observed on:** `2.1.319 (Claude Code)`; headless `claude -p` inside a cloud session; Linux
- **Changelog says:** > Fixed per-directory and `paths:` skills not being found after a Bash `cd` deeper into a git repository — [v2.1.312](https://github.com/anthropics/claude-code/releases/tag/v2.1.312), published 2026-10-27T20:24Z
- **Observed:** Layout: a parent folder that is not a git repo, holding `sub/`, its own git repo with `sub/.claude/skills/probe-sub/SKILL.md` (the layout of a multi-repo cloud session). Started in the parent with `claude -p --setting-sources project,local --allowedTools "Bash(cd sub)" Read`:
  - Read `sub/file.txt` with no `cd`: the `system`/`commands_changed` event names `probe-sub` (2 of 2 runs).
  - Bash `cd sub`, then Read `file.txt` there: `probe-sub` never appears in any event (3 of 3 runs). Bash `cd sub` alone: never (2 of 2).
  - Same steps when the parent is the git repo and `sub/` is a plain folder: `cd sub` then Read names `probe-sub` (2 of 2).
  - With `"sandbox":{"enabled":true}`, Read `sub/file.txt` never names it (3 of 3), which matches the 2.1.319 entry on nested skill loading.
- **Evidence:** the probe scripts and counts are in the comment that closed [#358](https://github.com/Adam-S-Daniel/_agent-guidance/issues/358).
- **Found in / action taken:** [#358](https://github.com/Adam-S-Daniel/_agent-guidance/issues/358) and [#459](https://github.com/Adam-S-Daniel/_agent-guidance/issues/459). Workaround: in a multi-repo session, Read a file in the repo before `cd`-ing into it. Repos with their own `.claude/skills`: `adamdaniel.ai` (`embeddable-tool-pages`), `acsi-kit` (`adopt`, `adopt-evals`).
- **Vendor issues:** related, not the same case: [#93403](https://github.com/anthropics/claude-code/issues/93403) (open; nested skills trigger only on Read/Edit/Write, not Bash). Searched 2026-11-08: "nested skills cd"; "skills not found after cd"; ".claude/skills subdirectory git repository cwd".
- **Vendor proposal:** `draft: [2026-11-08-skills-after-cd-into-nested-repo.md](vendor-issue-drafts/claude-code/2026-11-08-skills-after-cd-into-nested-repo.md)`
