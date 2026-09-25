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

None yet.
