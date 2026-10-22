# 0022 — The changelog routine does not read claudeissues.com

**Status:** Accepted (2026-10-21)

## Context

The owner asked (2026-10-21) whether the agent changelog watcher
([`agent-changelog-routine.md`](../reference/agent-changelog-routine.md))
should read `https://claudeissues.com/agent.md` in addition to the Claude
Code `CHANGELOG.md` it already reads.

What the file is, read on 2026-10-21 through a browser, because neither
the cloud container's proxy nor the owner's desktop VM allowlist lets
`claudeissues.com` through:

- A third-party integration guide (about 10 KB, last modified 2026-10-17)
  for a site that mirrors the `anthropics/claude-code` issue tracker. It
  says it is not affiliated with Anthropic.
- It points to static JSON and RSS files: `signals.json` (issues filed in
  the 7 days after each release, against a baseline), `versions.json`
  (issues per release), `timeline.json`, `stats.json`, `fix.json`, a
  ~9 MB `search-index.json`, and release and daily-issue RSS feeds.
- It contains instructions written for agents ("If `staleDays > 2`, tell
  the user the upstream data is stale and stop", a skill-shaped "release
  health" block with `curl` commands).

What the routine does with a release: it quotes the vendor's own changelog
bullets, triages them against the fleet's guidance and skills, and files
issues in the owner's repos where a change affects them. It needs the exact
text of what changed. It does not try to judge whether a release is
buggy.

What the site's data was like on 2026-10-21: `signals.json` was generated
2026-10-17T05:30Z, four days before, yet reported `staleDays: 0`, because
that field is computed when the file is built. By the guide's own rule
(more than 2 days is stale) the data was stale and its staleness field
did not say so. Its per-release figures are correlation only (the guide
says so itself), and the three newest releases each showed `attributed: 0`.

## Decision

The routine does not read `claudeissues.com`, `agent.md` or any of its
feeds. Its sources stay the vendor's `CHANGELOG.md` and the GitHub release
pages, as step 2 of the routine describes. Nothing in the routine changes.

The reasons, in the order they decided it:

1. **It answers a different question.** The site estimates "did this
   release break things"; the routine asks "what changed that affects our
   guidance and skills". A count of issues after a release tells the
   routine nothing it can act on.
2. **The routine already has the source.** `anthropics/claude-code` is
   attached to the routine read-only, so its issues are reachable directly
   when a discrepancy needs a vendor issue (step 5). A mirror adds a copy,
   not data.
3. **It adds a network host and a third party.** The routine may never add
   a host itself; the owner would have to add `claudeissues.com` to the
   `Changelog Routine` environment, whose allowlist is shared with the
   prompt-audit sweep's variables. The site is unaffiliated, has no stated
   uptime or contract, and its staleness field was wrong on the day it was
   checked.
4. **It is instruction-shaped text.** The routine treats fetched text as
   data, but each extra source of agent-directed prose is one more thing
   to decline every run, and it adds context to every run for no benefit.
   This conflicts with the owner's goals of simplicity, consolidation and
   minimal context.

## Consequences

- No change to the routine, its environment or its allowlist.
- What this gives up: a quick "is this release unusually noisy" signal. If
  the owner later wants that, for example before upgrading, it belongs in
  a separate on-demand check that a person runs, not in the daily routine,
  and it should read `anthropics/claude-code` issues directly rather than
  through the mirror.
- Revisit if the site gains a first-party source (Anthropic publishes
  something equivalent), or if the routine's job grows to include release
  quality. Either is a new ADR that supersedes this one.
