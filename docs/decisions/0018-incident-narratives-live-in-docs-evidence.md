# 0018 — Incident narratives live in `docs/evidence/`, and the guidance keeps a pointer

**Status:** Accepted (2026-10-04)

## Context

`/doctor prompt-audit` classes dated incident stories in instruction files as
"History narratives" and proposes deleting them, keeping only the rule. In
this fleet a citation is often why a rule survives review: a rule with no
incident behind it reads as optional, gets proposed for removal again, and
the incident recurs. [`agents-md/base.md`](../../agents-md/base.md) scopes
itself to "what is specific to this account and learned the hard way".

The guidance also has a hard size budget. `base.md` must stay under 25 KiB
(25,600 bytes; it was 25,167 when this was decided), and Codex silently cuts
a full-mode `AGENTS.md` at 32 KiB. A multi-line story costs every session that
loads the guidance, on every turn, while the rule beside it is what the agent
acts on.

The owner chose to move the stories out and leave pointers, rather than keep
them inline under a standing waiver.

## Decision

- **A narrative longer than about two lines moves** to
  `docs/evidence/<yyyy-mm-dd>-<slug>.md`, keeping the full original text and
  its links, plus where it came from (the section, the commit or PR that
  added it).
- **The guidance keeps a one-clause dated pointer** in its place, linked by
  a full `https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/...` URL.
  `base.md` is delivered into `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`
  and `stub.md` into every consumer repo, so a relative link would break.
- **The rule text stays.** Only the story moves; a pointer never replaces a
  rule, a threshold, or the mechanism a rule depends on.
- **A one-clause dated citation stays inline** next to the rule it
  justifies — a date, a repo or PR reference, a count — because a pointer
  would cost about as many bytes and send the reader away for one fact.
- **Each move is a section edit**: it gets its
  [`docs/guidance-impact.md`](../guidance-impact.md) entry and refreshed
  `agents-md/eval-coverage.yml` bytes in the same PR.
- The prompt-audit sweep ([ADR 0017](0017-prompt-audit-runs-as-an-event-triggered-sweep.md))
  reports a "History narratives" finding only for a story over that limit;
  a one-clause citation is not a finding.

The first move (this ADR's PR) took the 2026-10-04 `kill(-1)` story out of
"A test that can signal can kill every session" and the 2026-08-29 inlining
measurement out of `stub.md`. The other dated citations in `base.md` were each
within the limit and stayed.

## Consequences

- `base.md` recovers budget, a little per move: a full URL costs about
  110 bytes, so moving a story shorter than about three lines saves nothing,
  which is why the limit sits there.
- The story is one click away rather than in context. An agent that needs
  the "why" to judge an edge case has to follow the link; one running
  degraded, with no network, does not get it.
- `docs/evidence/` links resolve only after merge to `main`; a pointer added
  in a PR is a dead link until then.
- Reviewers apply one length rule instead of a judgment call per citation.
- Long stories in a consumer repo's own `## Repo-specific additions` (such as
  this repo's `AGENTS.md` note on commits `c86465f`..`7b87581`) are not
  covered here; that text is the repo's, not the fleet's.
