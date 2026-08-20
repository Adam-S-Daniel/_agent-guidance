# Session A — cut unnecessary CI work in cms-platform and its consumers

Read `SHARED-CONTEXT.md` first. Use workflows and adversarial review. Pull
emergent issues into scope recursively rather than listing them for later.
Merge what you land; if you touch a cms-platform reusable, **cut a release and
confirm the consumer bumps land** (SHARED-CONTEXT §3).

## The one evidenced saving

`platform-pin-consistency.yml` runs **~18×/day per consumer**, avg 47s, and
**80–90% of those runs are zero-signal**: in a 30-run sample only 4 came from
real change branches; the rest were `cms/*` and `agents-md-sync/*` automation
branches, which structurally cannot touch a pin-bearing file
(`.github/workflows/**`, `Gemfile`, `Gemfile.lock`, `platform.lock`).

It is **not** a required context (the `consumer-main` ruleset requires
`editorial / validate-content`, `scan / scan`, `parity / parity`,
`preview-media / preview-media`, `e2e / e2e`,
`visual-regression / approve-regression` — and nothing else), so a `paths`
filter cannot hang a PR here.

Do this:

- Add a **`paths-ignore`** (not a narrow `paths:` allow-list). The check's own
  purpose is "a skew can be introduced by editing ANY of the many pin-bearing
  files"; an allow-list that misses a future pin-bearing file type silently
  stops checking it, while an over-narrow ignore-list just costs a few harmless
  runs. Fail open.
- **Hand-tune per site.** adamdaniel.ai and jodidaniel.com have different
  content-collection layouts (blog/posts/tags vs
  `_accomplishments/_education/_experience/_expertise/_media`). Do not
  copy-paste one into the other.
- This block is **caller-owned**: it ships as a direct PR to each consumer, with
  **no** platform release and **no** `platform.lock` bump. Optionally update
  `examples/site/.github/workflows/platform-pin-consistency.yml` for future
  sites — that is a template-only change, still no release needed.
- **Verify after merge**, don't assume: on the next Decap content-only PR the
  workflow must NOT fire, and on the next PR touching a workflow file /
  `Gemfile*` / `platform.lock` it MUST. Report both observations with PR numbers.

## Verify the required-context list before you rely on it

The list above came from `cms-platform/repo-settings.yml`'s `ruleset_library`
plus the absence of an open repo-settings-drift issue — **not** from a live API
read. Before narrowing anything, confirm it against live branch protection. If
you cannot, say so loudly rather than proceeding on a manifest that may have
drifted.

## DO NOT DO — each of these looks like a saving and is a trap

1. **Adding `concurrency` to `cms-editorial-workflow.yml`'s `validate-content`.**
   It re-runs 3–6× per SHA (measured: 30 runs, 8 distinct SHAs, repeat counts
   6/6/4/4/3/3/3/1) because `types:` includes `labeled`. **This was already tried
   and caused a production incident** — nondeterministic
   `405 Required status check is cancelled`, hard-blocking merges, documented in
   the reusable's own header with incident PR numbers. The extra ~30s runs are
   the deliberately accepted cost.
2. **Narrowing `dependabot-comment-sync.yml`'s `push: branches: ['**']`.** It
   suppresses GitHub's phantom red zero-job runs; narrowing trades ~73 cheap
   green no-ops/day for red rows on every other push.
3. **Adding `paths:` filters to the required PR checks** (`parity-preview`,
   `preview-media`, `e2e-tests`, `visual-regression`) so canary/content-only PRs
   skip them. That defeats the canary loops, which exist precisely to prove a
   content-only PR clears the same gates a real editor's PR hits.
4. **Adding `reopened` to `cms-editorial-workflow.yml` "for consistency".** No
   evidence of a real gap — required checks key on SHA, not PR state.

## Two items that need DATA before any decision — do not guess

- **e2e browser-matrix trim.** One failed run showed 8 of 9 projects failing
  together, consistent with a broad regression rather than any project uniquely
  catching something. **One sample proves nothing.** If you want to trim the
  matrix, first tabulate per-project failure attribution across all historical
  failed `e2e-tests` runs and find projects that never failed alone. Report the
  table. (Related but out of scope: in several projects the
  *"Install Playwright browser + system deps"* step takes 6–9 min while the
  suite itself takes 20–40s — a caching question, not a trigger question.)
- **Canary/publish-loop cadence.** ~87% of PR-triggered runs come from the
  platform's own daily self-test loops. Whether daily is excessive is the site
  owner's call and needs failure-history depth nobody has pulled. Surface it;
  do not change it unilaterally.

## Also fix, while you are here (F7)

`e2e-tests.yml`/`e2e-stub.yml`'s paths mirror and `cms-automerge-nudge.yml`'s
`required_contexts` list both carry comments claiming they are lint-locked. They
are locked **only on the platform's template** —
`e2e/required-check-stub-paths.test.js` scopes itself to
`examples/site/.github/workflows/` and skips in a consumed checkout. **Neither
consumer's copy is checked by anything.** They currently match (verified by hand),
so this is a latent gap, not a live bug. Close it — a consumer-side lint, or
extend the platform spec to run against `<site>/.github/workflows` — and make the
comments stop claiming coverage that does not exist.
