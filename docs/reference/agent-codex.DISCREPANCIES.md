# Discrepancies — Codex behavior vs. its release log

Cases where observed Codex behavior contradicts, or goes beyond, what its
[release log](https://github.com/openai/codex/releases) says. Sidecar to
[`agent-codex.CHANGELOG.md`](agent-codex.CHANGELOG.md). Twin file, same
structure: [`agent-claude-code.DISCREPANCIES.md`](agent-claude-code.DISCREPANCIES.md).

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
- **Observed on:** `codex --version` output; surface (local CLI, cloud session, CI, SDK); OS
- **Changelog says:** > exact quote — [0.N.0](https://github.com/openai/codex/releases/tag/rust-v0.N.0), published YYYY-MM-DDTHH:MMZ, or "nothing" plus the version range where the behavior appeared
- **Docs say:** > exact quote — [page](URL), read YYYY-MM-DD (only if consulted)
- **Observed:** what happened, with the minimal repro commands and the output that shows it
- **Evidence:** link to the commit, test, CI run, or transcript that demonstrates it
- **Found in / action taken:** issue or PR link; what the repo did (workaround, pin, test, upstream report)
- **Vendor issues:** [#N](link) (open | closed, fixed in <version>), or `none found — searched YYYY-MM-DD: "<query>"; "<query>"`
- **Vendor proposal:** `draft: [<file>](vendor-issue-drafts/codex/<file>.md)` | `covered by an existing issue` | `not proposed — <reason>` | `submitted: <link>`
```

## Entries

### 2026-10-06 — `/import` session availability: docs disagree with release notes

- **Kind:** docs disagree with changelog
- **Status:** open
- **Observed on:** source/documentation read of `rust-v0.160.1` on 2026-10-06,
  not a CLI reproduction. No `codex --version` invocation was run for this
  recheck; no live remote or local background-server session was exercised.
- **Changelog says:** [0.157.0 release notes](https://github.com/openai/codex/releases/tag/rust-v0.157.0),
  published 2026-09-25T02:31Z:

  ```text
  Made `/import` available in remote sessions and local background-server sessions. (#47317)
  ```

- **Docs say:** “It’s unavailable while a task is running, in remote sessions,
  and while connected to the local app-server daemon.” — [developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli#import-claude-code-or-cursor-setup-with-import),
  read 2026-10-06.
- **Observed:** the current documentation contradicts the release notes about
  session availability. In [0.160.1 slash dispatch](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/tui/src/chatwidget/slash_dispatch.rs#L500-L503),
  `/import` sends `OpenExternalAgentConfigMigration`.
  [Event dispatch](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/tui/src/app/event_dispatch.rs#L428-L460)
  handles that event with remote cwd selection and calls the shared migration
  prompt; the [migration flow](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/tui/src/external_agent_config_migration/flow.rs)
  communicates through `AppServerSession`. These source reads suggest the
  release-note path remains implemented, but do not prove live availability
  or remote import correctness. No reproduction commands or runtime output
  are claimed.
- **Evidence:** [dated source/documentation recheck](../evidence/codex-memory-129.md#2026-10-06-current-source-and-documentation-recheck).
- **Found in / action taken:** [issue 183](https://github.com/Adam-S-Daniel/_agent-guidance/issues/183);
  recorded the disagreement and retained the open import experiment. Use a
  local TUI with a disposable profile, the common ground across the docs and
  release notes, until the discrepancy is settled. No issue writes or vendor
  filing were performed.
- **Vendor issues:** no matching issue found — searched 2026-10-06 with
  `gh search issues --repo openai/codex --limit 20 '"/import" "daemon"'`
  and `gh search issues --repo openai/codex --limit 20 '"/import" "remote"'`.
  The first returned no results; the second returned one unrelated issue.
  These queries do not establish comprehensive absence.
- **Vendor proposal:** not proposed — source/documentation disagreement has
  not been reproduced in a live current-release session.
