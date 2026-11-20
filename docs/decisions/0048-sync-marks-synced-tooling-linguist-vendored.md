# 0048 — The sync marks its tooling `linguist-vendored` in each repo's `.gitattributes`

**Status:** Accepted (2026-11-20)

## Context

The sync copies tooling into every consumer: `.githooks/agent-checks` (about
45 KB of shell), `.githooks/dated-phrasing.js` and `.claude/hooks/*.sh`.
GitHub's linguist counts those bytes as the repo's own code. On 2026-11-20,
four fleet repos read exactly "Shell 80%, JavaScript 19%" with almost no code of their own,
and the synced files dominated the language bar of most of the rest.
`jodidaniel/squarespacetemp` showed Shell 54,368 bytes and JavaScript 13,020:
exactly `agent-checks`, `agent-checks-guard.sh` and `dated-phrasing.js`.

That bar is now an input. The nightly language-fit check reads GitHub's
languages to decide which `.agents-sync.yml` sections a repo should request,
and repo-settings compares it with CodeQL's languages. Neither can trust a map
in which the fleet's own tooling outweighs the repo.

## Decision

`scripts/sync.sh` appends two more managed lines to every synced repo's root
`.gitattributes`, through the same append-only helper as ADR 0019's line, each
with a comment above it:

```
.githooks/** linguist-vendored
.claude/hooks/** linguist-vendored
```

Linguist then leaves those files out of the language statistics. Diffs,
search and blame are unaffected (that is `linguist-generated`, which this does
not set). The source repo, `_agent-guidance`, is not synced, so its own copies
still count, which is right: there they are the repo's code.

## Consequences

- A repo's own hooks under those two directories also stop counting. They are
  tooling too, so this is accepted rather than enumerating the synced files,
  which would need a line per file and an edit whenever the sync adds one.
- A repo that removes a line by hand gets it back on the next sync, as with
  ADR 0019.
- Other misdetections (fixtures, generated output, docs) stay per repo: the
  language-fit section of the drift report suggests the lines, and the fleet
  worker opens the pull requests.
