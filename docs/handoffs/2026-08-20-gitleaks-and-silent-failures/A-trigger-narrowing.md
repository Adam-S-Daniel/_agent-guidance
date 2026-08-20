# Session A — cut unnecessary CI work in cms-platform and its consumers

Read `SHARED-CONTEXT.md` first. Use workflows and adversarial review. Pull
emergent issues into scope recursively rather than listing them for later.
Merge what you land; if you touch a cms-platform reusable, **cut a release and
confirm the consumer bumps land** (SHARED-CONTEXT §3).

## The one evidenced saving — SHIPPED, and three things below it were wrong

**Corrected 2026-08-20 from
[`_agent-guidance#52`](https://github.com/Adam-S-Daniel/_agent-guidance/issues/52).**
This section has since been implemented — adamdaniel.ai carries the site-tuned
`paths-ignore` and jodidaniel.com deliberately does not — and doing it exposed
three errors in the paragraph that motivated it. All three are corrected below
and all three are NAMED, because each is the kind of thing a fresh reader
re-derives from first principles and gets wrong the same way.

### It is `pin-consistency / pin-consistency`, not `platform-pin-consistency`

`platform-pin-consistency.yml` is the **file**. The check-run context is
`<caller job id> / <reusable job id>` = **`pin-consistency / pin-consistency`**
(verified on cms-platform `origin/main`: the caller template's job id is
`pin-consistency` and so is the reusable's). A workflow's `name:` never enters a
reusable-call context — see `SHARED-CONTEXT.md` §6.

This matters beyond pedantry: **any grep, lint or ruleset check written against
the file-name string matches nothing and reads as safe.** A "we verified no
ruleset requires it" that searched for `platform-pin-consistency` proved
precisely nothing.

Still true, and re-confirmed: it is **not** a required context (the
`consumer-main` ruleset requires `editorial / validate-content`, `scan / scan`,
`parity / parity`, `preview-media / preview-media`, `e2e / e2e`,
`visual-regression / approve-regression` — and nothing else), so a `paths`
filter cannot hang a PR here.

### The frequency numbers were adamdaniel.ai-only

This used to read *"runs **~18×/day per consumer**, avg 47s, and **80–90% of
those runs are zero-signal**"* — as if it described both sites. It described
one. Measured over the 90 most recent runs of the workflow on **each** site
(Actions API, 2026-08-20):

| | adamdaniel.ai | jodidaniel.com |
|---|---|---|
| runs/day (recent) | 20.4 | **2.0** |
| mean / median | 47.4s / 46.0s | **17.2s / 17.0s** |
| from `cms/*` | 64/90 (71%) | **0/90** |
| from `agents-md-sync/*` | 9/90 (10%) | 19/90 (21%) |
| from `platform/*` | 4/90 (4%) | **43/90 (48%)** |

jodidaniel.com is gated (`_data/settings.yml` `site_live: false`), so it has no
editorial traffic at all, and its dominant driver is `platform/*` bump PRs —
which by definition **do** edit pin-bearing files and must keep running. The
whole saving available there is ~136 runs × 17s ≈ 39 minutes across 76 days.
**It therefore got no filter, deliberately**, and the reason is written into its
caller so nobody "finishes the job" by copying adamdaniel.ai's across.

### "Those automation branches structurally cannot touch a pin-bearing file" is FALSE

The old text listed four pin-bearing inputs. **The checker reads six**, and the
sixth is the trap:

1. `platform.lock` · 2. `.github/workflows/**/*.y{a,}ml` (recursive) ·
3. `Gemfile` · 4. `Gemfile.lock` ·
5. **`assets/images/uploads/e2e-preview-media-probe.png`** — the `preview-media`
   sentinel, gated on git blob sha1 `62a5f8f4` (cms-platform#84) ·
6. `.cms-platform/examples/site/.github/workflows/*` (fetched, not ours)

Number 5 lives **inside the Decap `media_folder`** (`media_folder:
"assets/images/uploads"`), beside real editorial uploads. So a `cms/*` media
upload DOES touch a pin-bearing path, and the `assets/**` entry an author
naturally reaches for would have **blinded the check**. `paths-ignore` has no
negation syntax, so an un-ignorable subtree cannot be carved back out — the
ignore list must enumerate only content subtrees that hold none of the six.

That is also why `paths-ignore` beats a `paths:` allow-list here, for a second
reason the original gave only in the abstract: GitHub skips the workflow only
when **every** changed file matches the ignore list, so a mixed PR (a post plus
a workflow) still runs.

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

**This has since been done for jodidaniel.com, and it mattered.** A live
`GET /repos/jodidaniel/jodidaniel.com/rulesets` on 2026-08-20 returned ruleset
`main` (id **17032014**) requiring **six** contexts. Its
`cms-automerge-nudge.yml` header had asserted the live ruleset *"requires ZERO
status checks"* — the stated reason its `required_contexts` list held one entry
— and the claim came from conflating `main` with `cms-feature-branches`
(id 17032043), a separate ruleset over feature-branch refs. Corrected in
jodidaniel.com#157; both false claims are recorded in that header rather than
deleted. Note **neither MCP connector can read rulesets** — this needs direct
REST (`SHARED-CONTEXT.md` §1).

**And read the checker with a parser, not a regex.**
`check-platform-pin-consistency.js`'s `collectPins` walks YAML `Pair` nodes and
matches `k === "platform_ref"`, so prose containing the literal
`` `platform_ref:` `` inside a `#` comment is invisible to it. A survey regex
`platform_ref:\s*(\S+)` DID match a comment added by adamdaniel.ai#3222 and
reported a bogus second value — a phantom skew, discovered by an audit script
rather than by the checker. Confirmed harmless empirically:
`pin-consistency / pin-consistency` = success on #3222's head (`2105aab4`), the
very commit that introduced the comment.

**Corollary, freshly demonstrated:** `pin-consistency` is **absent** on
adamdaniel.ai#3223 (docs-only). That is the new `paths-ignore` doing its job —
and it is exactly why this context must never be made required.

## DO NOT DO — each of these looks like a saving and is a trap

1. **Adding `concurrency` to `cms-editorial-workflow.yml`'s `validate-content`.**
   It re-runs 3–6× per SHA (measured: 30 runs, 8 distinct SHAs, repeat counts
   6/6/4/4/3/3/3/1) because `types:` includes `labeled`. **This was already tried
   and caused a production incident** — nondeterministic
   `405 Required status check is cancelled`, hard-blocking merges, documented in
   the reusable's own header with incident PR numbers. The extra ~30s runs are
   the deliberately accepted cost.
2. ~~**Narrowing `dependabot-comment-sync.yml`'s `push: branches: ['**']`.** It
   suppresses GitHub's phantom red zero-job runs; narrowing trades ~73 cheap
   green no-ops/day for red rows on every other push.~~
   **OBSOLETE — corrected 2026-08-20. There is no such trigger any more, and
   this item now points the wrong way.** `dependabot-comment-sync` existed to
   regenerate the trailing `# vX.Y.Z (YYYY-MM-DD)` label on SHA pins. That
   convention was retired fleet-wide (agentskills ADR 0004 / this repo's ADR
   0007), the reusable and `scripts/sync-action-pin-comments.sh` were deleted
   from cms-platform `main`, and both consumer callers had every trigger
   removed except `workflow_dispatch`. Struck rather than deleted because the
   phantom-run rationale was correct on its own terms and a claim that merely
   disappears gets re-derived — but read the polarity as INVERTED: re-adding a
   trigger here does not restore a saving, it re-arms the retired convention.
   The script does not merely refresh a label, it GROWS one (`USES_RE` captures
   the comment as an optional `(#.*)?`; `build_new_line()` emits one
   unconditionally), and the callers pin the immutable tag `@v0.1.88`, which
   still ships both, so trigger removal is the entire disarm. The caller files
   are deleted at the next `platform_ref` bump past v0.1.88 and not before —
   deleting one alone reds `pin-consistency / pin-consistency` as
   `workflow-set: MISSING (platform-dictated)` (jodidaniel.com#161).
3. **Adding `paths:` filters to the required PR checks** (`parity-preview`,
   `preview-media`, `e2e-tests`, `visual-regression`) so canary/content-only PRs
   skip them. That defeats the canary loops, which exist precisely to prove a
   content-only PR clears the same gates a real editor's PR hits.
4. **Adding `reopened` to `cms-editorial-workflow.yml` "for consistency".** No
   evidence of a real gap — required checks key on SHA, not PR state.

   **`reopened` is not the lever in the other direction either — measured.**
   The natural carve-out move for a required-context concurrency hazard is to
   DROP `reopened` from a caller's `pull_request.types`, and it does not work.
   The one collision actually observed in production was an **`opened` +
   `synchronize` burst around a force-push**: adamdaniel.ai PR #3006 opened
   `01:57:10Z`, `head_ref_force_pushed` `01:57:38Z`, runs `31289327061`
   (`cancelled`) and `31289327099` (`skipped`) both created `01:57:41Z` on head
   sha `68d7c777`; jodidaniel.com hit the same wave on `bf49581a`. `opened` and
   `synchronize` are both required for the check to exist at all, so there is
   nothing left to narrow. **SUPERSEDED — the fix shipped anyway, as removal
   rather than narrowing**: cms-platform#285 (closed `completed` 2026-08-20)
   took the `concurrency` block out of `visual-regression.yml` and
   `secrets-scan.yml` in `v0.1.87`. See `SHARED-CONTEXT.md` §6 for the
   outcome-level invariant that replaced it, and for the second cause
   (`timeout-minutes`) that the concurrency-shaped framing missed.

   That instance was a **near-miss, not an outage**: the required context
   `visual-regression / approve-regression` concluded `success` and the PR
   merged; the cancelled check-run was `visual-regression / generate`, which is
   not required. Report it as the precondition occurring in production on both
   sites — which is precisely the thing AGENTS.md says is non-deterministic —
   never as an incident.

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

**Half of this has moved — read the update at the end of the section first.**

`e2e-tests.yml`/`e2e-stub.yml`'s paths mirror and `cms-automerge-nudge.yml`'s
`required_contexts` list both carry comments claiming they are lint-locked. They
are locked **only on the platform's template** —
`e2e/required-check-stub-paths.test.js` scopes itself to
`examples/site/.github/workflows/` and skips in a consumed checkout. **Neither
consumer's copy is checked by anything.** ~~They currently match (verified by hand),
so this is a latent gap, not a live bug.~~ Close it — a consumer-side lint, or
extend the platform spec to run against `<site>/.github/workflows` — and make the
comments stop claiming coverage that does not exist.

**Update, 2026-08-20 (#52): it was NOT latent — it had already fired.** The
struck sentence above is the error worth keeping visible, because "verified by
hand, so latent" is how an unchecked invariant gets downgraded to a chore.
jodidaniel.com's `cms-automerge-nudge.yml` was carrying a `required_contexts`
list justified by two claims in its own header that argued the **opposite** of
its own body, and both were false:

1. *"The live `main` ruleset here requires ZERO status checks."* It requires
   **six** (see "Verify the required-context list" above).
2. *"`e2e / e2e` reports under a DIFFERENT name on docs-only PRs
   (`E2E (docs-only stub) / e2e`)."* It does not — a reusable-call context is
   `<caller job id> / <reusable job id>` and the workflow's `name:` never enters
   it. Both `e2e-stub.yml` and `e2e-required-stub.yml` use job id `e2e`.
   Observed on adamdaniel.ai#3223 (run 32321556637) and true by construction:
   `e2e / e2e` is required, so a differently-named stub context would hang
   every docs-only PR forever.

Fixed in **jodidaniel.com#157** (merged), with both claims recorded in the
header rather than deleted. Six platform bumps had touched that file and none
revisited it. The remaining work — a consumer-side lint so the *template*
comment stops claiming coverage a consumed checkout does not get — is still
open, and cms-platform `baf742b` (see `SHARED-CONTEXT.md` §7) is the proof that
the template itself can regress into exactly this shape.
