# ADR 0003: The roster proposal merges automatically, behind `roster_mode`

- **Status:** accepted (2026-09-28); amended 2026-09-29 by
  [Round 6](#round-6-the-roster-pr-is-the-apps): the roster PR is opened,
  reopened and armed by a dedicated GitHub App, and the `ci.yml` dispatch
  in decision 3 is gone.
- **Amended by [ADR 0004](0004-eval-runs-on-the-roster-its-run-merged.md) (2026-09-30):** `roster-pr` arms the pull request BEFORE the eval (`needs: [roster, disarm]`), not last, and a `roster-wait` job resolves the arming (merged, or auto-merge turned off) before `eval` and `publish` start.
- **Extended 2026-10-26** by the [addendum](#addendum-2026-10-26-fixture-pull-requests-merge-the-same-way)
  below: real-work scaffold pull requests and fixture promotion pull
  requests use the same App and auto-merge.
- **Issue:** none filed; the owner's decision of 2026-09-28. Settings-as-code
  follow-up for the "Allow GitHub Actions to create and approve pull
  requests" repo setting this depends on:
  [repo-settings#51](https://github.com/Adam-S-Daniel/repo-settings/issues/51)
  (another session).
- **Deciders:** the owner, directly — this amends
  [ADR 0001](0001-roster-trusted-on-main.md)'s human-merge decision rather
  than following from any measurement in this repo.

## Context

[ADR 0001](0001-roster-trusted-on-main.md) decided that a computed roster is
a proposal, not the running set: the "Propose a roster change" step in
`.github/workflows/eval.yml` pushes a differing proposal to the bot-owned
branch `roster/proposal` and files one tracking issue, and — in its own
words — "a human opens the PR from a valid branch and merges it after CI."
That decision's Consequences section is explicit that this is deliberate:
"This is the fleet's sanctioned bot-write path — a branch and a PR a human
merges — not `PR + auto-merge` and not a bypass actor."

The fleet guidance repeats the same rule in general form (`AGENTS.md`,
"Automation vs branch protection"): "PR + auto-merge is not a sanctioned
bot-write path for fleet repos." the owner's decision here is to use it anyway,
for this one workflow, behind a flag — he considers a ruleset bypass actor
(the alternative ADR 0001 priced at 6–15 points and rejected) a semantic
workaround around the same protection PR + auto-merge is meant to route
around, not a materially different guarantee: both let a bot's own commit
reach `main` with no human reading it first, unless a human is the one
merging. `PR + auto-merge` is the cheaper way to get the property he
actually wants (a required, green `test` check on the exact commit that
lands) without asking for a `repo-settings` bypass-actor change.

Facts measured on this repo before this decision: the `main` ruleset
requires a PR with 0 approvals, merge commits only, and the required status
check context `test` (the `test` job in `.github/workflows/ci.yml`); the
repo's `allow_auto_merge` is `true`; `ci.yml` has a bare `workflow_dispatch:`
trigger and no concurrency group; a PR opened with `GITHUB_TOKEN` does not
fire `pull_request` workflows, so nothing dispatches `ci.yml` on
`roster/proposal` unless this workflow does it itself, and a `GITHUB_TOKEN`
`workflow_dispatch` does create a run whose `test` check lands on the head
sha and satisfies the ruleset. The owner enabled "Allow GitHub Actions to create
and approve pull requests" on this repo for this change. (Round 6, below:
the last of those "facts" proved false on the first live run — a dispatched
`test` does not count for the PR's required check, and the PR's own
`pull_request` run is held for approval instead of not firing.)

## Decision

1. **`roster_mode` in `evals/roster-policy.yml`** (`auto` or `proposal`)
   selects the behavior. Set to `auto` here. A missing key, or any value
   other than the two, is `proposal` — silently for a missing key, with a
   fixed `::warning::` for a present, wrong value. `harness/roster.py` never
   reads this key; it is machinery for the "Propose a roster change" step
   alone, read from the trusted `main` checkout.
2. **`auto` changes only what happens to an already-valid, already-pushed
   proposal, and only on a clean run.** The render + admission checks, the
   `roster/proposal` branch push, and the tracking issue are unchanged.
   Auto-merging is attempted only after the branch push succeeds AND this
   run's vendor-default probe was clean (`defaults_failed` = 0 and
   `defaults_mismatched` = 0, [ADR 0002](0002-roster-follows-vendor-defaults.md)'s
   freeze). A probe failure or mismatch, or a rejected proposal, keeps
   today's behavior exactly — issue filed, human decides — and the issue
   says why the automatic merge did not run.
3. **The auto path:** find or open a pull request from `roster/proposal`
   onto `main`; dispatch `ci.yml` on that branch so its `test` job reports on
   the pushed sha (nothing else would, per the Context section above); enable
   `gh pr merge --auto --merge --match-head-commit <pushed sha>` on it. Any
   of those `gh` calls failing is a fixed `::warning::`, never a failed job —
   the roster and its branch already exist either way — and the tracking
   issue then says a human must open or merge the PR, the same as
   `roster_mode: proposal`. A "same" run under `roster_mode: auto` closes an
   open `roster/proposal` PR alongside the tracking issue, for the same
   reason it closes the issue: the roster no longer differs.
4. **Auto-merge is turned OFF on every run that does not (re-)enable it,
   whenever a pull request is open.** `--match-head-commit` is checked only
   when auto-merge is *enabled*, not at merge time — so a PR an earlier
   clean run opened would otherwise keep auto-merge across a later push it
   never approved (a probe going dirty, `roster_mode` switching back to
   `proposal`, or a mid-attempt `gh` failure). On every one of those paths —
   including a proposal REJECTED before the branch was even touched — an
   open PR from `roster/proposal` has `gh pr merge --disable-auto` run on
   it, wrapped the same way (`::warning::`, never a failed job), and the
   tracking issue says whose merge was turned off and why.
5. **Permissions:** `pull-requests: write` and `actions: write`, used only by
   the auto path above. **Revised by the adversarial round 1 review on #209
   (F2):** these two scopes live on a separate job, `roster-pr`, never on
   `eval` itself — `eval` is the job that runs the bypass-permissions agent,
   and giving the whole job a PR/workflow-dispatch scope for one late step
   would have handed that scope to the agent too. `roster-pr` runs
   `pull-requests: write, actions: write, issues: write, contents: write`
   (the `contents: write` reason is round 2's revision, immediately below);
   `eval` keeps exactly its pre-#209 scopes
   (`contents: write, id-token: write, issues: write`) and never calls `gh
   pr`/`gh workflow run` at all. `roster-pr` needs `issues: write` too: the
   tracking issue's final wording (a PR number, "merging automatically",
   "could not be turned off") depends on what `roster-pr` did to the PR, so
   it finishes editing the SAME issue the `eval` job's propose step found or
   left with neutral text, rather than splitting one issue's text across two
   writers with no shared view of the outcome. Workflow-level `permissions:`
   is `{}`. `roster-pr` runs on EVERY eval run (`if: !cancelled()`),
   `roster_mode: proposal` included — a stale `roster/proposal` PR's
   auto-merge must come off, or the PR must be closed if the roster no
   longer differs, whether or not this run's propose step reached the auto
   path (F3, below). **Revised again by adversarial round 2 on #209 (S2):**
   `roster-pr` carries `contents: write`, not `contents: read` —
   `enablePullRequestAutoMerge`/an immediate merge both need write access to
   contents even though this job performs no checkout and runs no
   repository code; only `gh` calls happen here. **Revised again by B1
   (round 3 on #209, blocker):** the compute/render/admit/push path — and
   with it `issues: write` — moved off `eval` entirely, onto a third job,
   `roster` (see the Round 3 section below); `eval` keeps only `contents:
   write, id-token: write`, and `roster-pr` now `needs: [roster, eval]`
   rather than `needs: eval` alone.

## Round 2 (adversarial review on #209, spec-roster-mode-r2.md): `roster-pr`
## trusts nothing the `eval` job produced

Round 1 (F2, above) moved the PR/workflow-dispatch scopes off the job that
runs the bypass-permissions agent. It did not go far enough: `roster-pr`
still READ its auto/proposal decision, and the sha it enabled auto-merge
on, from that same job's own outputs — `needs.eval.outputs.*` — which are
just as reachable by a compromised `eval` job as a `pull-requests: write`
scope would have been, through `$GITHUB_OUTPUT` (any earlier step, or a
`BASH_ENV`/shell-startup trick, can append to it) or through the checkout
that step's `git rev-parse HEAD` reads.

**Threat model:** treat EVERYTHING the `eval` job produces — its job
outputs, the commit it pushed to `roster/proposal`, its workspace — as
untrusted. `roster-pr` (no checkout of its own) is the only place trust is
established, and it establishes it from two sources only: GitHub's own API,
and the triggering commit `$GITHUB_SHA` on `main` (`github.sha`, fixed at
dispatch time and never touched by whatever the `eval` job's checkout later
does — the same property that makes a `GITHUB_TOKEN` PR immune to a
`pull_request` re-trigger, reused here as the one thing on the runner a
compromised `eval` job cannot rewrite).

Four checks, all in `roster-pr`, all before `gh pr create/edit/merge --auto`
and the `ci.yml` dispatch:

1. **The decision itself is re-read, not re-used.** `roster_mode` is read
   fresh from `evals/roster-policy.yml` at `$GITHUB_SHA` via the contents
   API (`gh api "repos/$REPO/contents/evals/roster-policy.yml?ref=$GITHUB_SHA"
   --jq .content | base64 -d`, parsed with `yaml.safe_load`; the same
   missing-key/unreadable-file/bad-value fallback rules as the `eval` job's
   own read). The `eval` job's `ROSTER_MODE` output is still read and
   logged, but never used for the decision — a compromised `eval` job
   forging `roster_mode=auto` over `$GITHUB_OUTPUT` while the committed
   policy still says `proposal` (the kill switch) no longer works.
2. **The pushed branch's actual head is re-read from the API**
   (`gh api repos/$REPO/git/ref/heads/roster/proposal --jq .object.sha`,
   40-hex validated) and required to equal `$PUSHED_SHA`. `$PUSHED_SHA`
   itself is still the `eval` job's own claim, so this is what makes that
   claim mean something: the job cannot enable auto-merge on a sha it has
   not independently confirmed IS the branch's current head.
3. **The shape, the content and the commit's own identity are all
   re-verified, not re-trusted (round 3 on #209 added the last of these).**
   `gh api "repos/$REPO/compare/$GITHUB_SHA...$PUSHED_SHA"` must show
   `status == "ahead"`, `ahead_by == 1`, `behind_by == 0`,
   `[.files[].filename] == ["evals/roster.yml"]` with that file's status
   `modified` — mirroring the path-allowlist precedent in
   `dependabot-auto-merge.yml`'s `auto-merge` job (a fixed set of paths a
   bot's own change may touch, checked server-side rather than trusted from
   the bot's own diff) applied here to a single fixed path rather than a
   manifest glob — AND that the one commit's own message and author email
   equal exactly what the propose step commits with
   (`roster: proposed model roster (run <RUN_ID>)`,
   `skills-evals@users.noreply.github.com`). An extra commit riding along on
   the `eval` job's checkout (ahead_by 2), an extra file in the same commit,
   `main` having moved on since this run started (behind_by > 0), or a
   planted commit message/author (round 3 repro `test_B1_hostile_roster_
   content_single_file_is_auto_merged`: a hostile `render_roster_yaml.py`
   in the untrusted workspace rewrites the FILE content while a git
   `commit-msg` hook plants a closing keyword — the file-shape checks alone
   never catch either) each fail this check.
4. **Only on `main`.** `roster-pr`'s own `if:` gains
   `&& github.ref == 'refs/heads/main'` — a dispatch on any other ref never
   has a `$GITHUB_SHA` worth trusting as "the committed policy", so this job
   (and the auto-merge surface it alone holds) never runs for one.

If any of 1–3 fails on a run that would otherwise attempt auto-merge: the
attempt is not made; an open `roster/proposal` PR (from an earlier run) has
its auto-merge disabled the same way every other "did not (re-)enable"
path in decision 4 above does; and the tracking issue's text names which
check failed, in fixed wording per check (never anything read off the
failing API response) — "the pushed branch's head does not match this
run's proposal" for check 2, "the pushed commit did not verify as a single
`evals/roster.yml` change ahead of this run's checkout" for check 3.

**Cancellation window (superseded by round 4 below).** As originally
written: a run cancelled after the `roster` job's push but before
`roster-pr` runs could leave an EARLIER run's auto-merge still armed —
cancellation does not itself disable anything, and nothing disabled it until
`roster-pr` next ran (the next scheduled or dispatched `eval` run) and
performed checks 1–3 against whatever `$GITHUB_SHA` was current then. That
window was accepted rather than closed, on the reasoning that closing it
would need either a `roster-pr` that runs even when `eval` is cancelled
before pushing (nothing to verify) or a third job whose only purpose is
"disable on cancel" — which is exactly what round 4's `disarm` job below
turns out to be worth building: it disables any earlier run's still-armed
auto-merge immediately after `roster` pushes, before `eval`'s agent starts at
all, so this run's own cancellation can only ever widen the already-small
window between `roster`'s push and `disarm`'s own run — never the whole
length of `eval`.

## Round 3 (adversarial review on #209, spec-roster-mode-r3.md): the compute/
## render/admit/push path moves off the agent's own job entirely

Round 1 (F2) moved the PR/workflow-dispatch scopes off the `eval` job; round 2
made `roster-pr` re-verify rather than re-trust what that job produced. Both
left the computation, rendering, admission and push of the proposal itself —
"Refresh the model roster" and "Propose a roster change" — running as later
STEPS in the SAME job as the bypass-permissions agent. That job's runner is
one filesystem: even though the `contents`/`issues` write credential is
step-scoped `env:` and never exists while the agent step runs, anything the
agent could have planted on that runner (a modified `git`, a shell rc file, a
`bash -c` alias) is still there when the later, credentialed step runs. B1's
fix is a third job, `roster`, that computes, renders, admits and pushes the
proposal on a runner the agent never touches at all — before, after or
alongside `eval`, never depending on it.

**The new job boundary.** `roster` (`contents: write`, `id-token: write`,
`issues: write`): its own checkout of `skills-evals` only (none of the four
fixture/registry repositories), its own Node/Claude-CLI/Python setup, its own
WIF mint, then "Refresh the model roster" and "Propose a roster change"
unchanged in substance. It must never `needs: eval` — the whole point is
independence, not sequencing. `eval` keeps its own checkout, its own separate
WIF mint (never shared with `roster`'s — two jobs, two runners, no credential
in common), the fixture run and (as of round 3; moved to `publish` in
round 4 below) the badge/commit; `issues: write` came off
it since nothing here writes the tracking issue any more. `eval` DOES
`needs: roster`, in one direction only, so its badge step can commit an
EXHIBIT copy of `roster`'s own computed roster
(`needs.roster.outputs.roster_latest_json`, ADR 0001 decision 3: read by no
decision) without either job reaching into the other's runner; `if:
${{ !cancelled() && !inputs.roster_only }}` keeps a `roster` failure
non-fatal to the eval, exactly as before. `roster-pr` becomes the only job
depending on BOTH (`needs: [roster, eval]`): the only place both an
independent re-verification of what `roster` published (B1.1–B1.3, now read
via `needs.roster.outputs.*`) AND the `eval` job's own outcome
(`needs.eval.result`) are both in reach at once — the tracking issue's eval
sentence, which used to read `steps.eval.outcome` inside the single shared
job, is computed here now instead, since `roster` structurally cannot know
it (see below).

**What "the eval job's outcome" costs.** Because `roster` never depends on
`eval`, its own "same"/frozen-same tracking-issue writes can no longer say
whether the eval succeeded, failed, or didn't run this dispatch — only
whether `roster_only` was set (known synchronously from the event payload,
same as before). The fuller success/failure/unknown sentence — read from
`needs.eval.result`, a fixed GitHub Actions word, never trusted as free text
— lives in `roster-pr` alone now, for the "differs"/"rejected" bodies it
writes. `needs.eval.result == "skipped"` reads unambiguously as "this was a
`roster_only` dispatch" because the `eval` job's own `if:` is now EXACTLY
`!inputs.roster_only` — no other reason skips that job.

**Should-fix 1 (this round): the commit itself, not just its shape, is
verified.** Round 2's check 3 (above) confirmed the pushed commit's file
diff — one file, `evals/roster.yml`, modified. It never confirmed the
commit's own MESSAGE or AUTHOR, so a hostile `scripts/render_roster_yaml.py`
planted in the `roster` job's untrusted-in-principle-but-previously-
unverified workspace, or a `commit-msg` git hook, could ride along inside a
file-shape-clean commit and land arbitrary content — a closing keyword, an
`@mention` — in a pull request `roster-pr` then auto-merges
(`test_B1_hostile_roster_content_single_file_is_auto_merged`, the round-3
adversarial repro this fixes). `verify_publish` in `roster-pr` now also
requires the compare's one commit to carry EXACTLY the message the propose
step commits with (`roster: proposed model roster (run <RUN_ID>)`) and the
bot's own author email (`skills-evals@users.noreply.github.com`); anything
else fails the same way an extra file or a wrong status would. Separately,
`harness/roster.py`'s rendered `summary.md` — the one piece of this run's
own text that legitimately varies, shaped in part by the untrusted census —
is neutralised in place (a zero-width space after `@` and before a `#`-digit
run) the moment it is read, before it can reach any PR or issue body, so a
closing keyword or mention embedded in the SUMMARY TEXT itself (rather than
smuggled via the commit) is defused too.

**B2: a planted future `catalogue_seen.last_seen` can't be used to poison
history.** `_update_catalogue_seen`'s ageing math (`now - seen_at > max_age`)
never ages out an entry whose `last_seen` is in the future — the round-3
repro `test_B2_future_catalogue_seen_never_ages_out` shows one planted at
`2099-12-31` still present after 4+ simulated years. Rather than changing
that ageing math (which only ever runs on data already inside the trusted
`evals/roster.yml`, i.e. already reviewed), `committed_roster_problems` — the
one gate every proposal and every committed file must pass — now also
rejects any `catalogue_seen` entry whose `last_seen` is later than the
document's own `generated_at`. A hostile proposal carrying such an entry is
rejected at admission, before it can ever be pushed or merged.

**Should-fix 2–6 (mutant-proofing and wording), briefly:** the round-3
mutation run found five single-fault gaps in `verify_publish`'s compare
check (dropping `ahead_by == 1`, the filename check, the `modified` status
check, or loosening either to a prefix/inequality) that survived only
because every existing test happened to violate more than one clause at
once; dedicated single-fault tests close each. A failed policy read
combined with a forged `roster_mode: auto` claim from the untrusted job
still had to fall back to `proposal` (it already did; a test now proves it,
fail-closed). The PR lookup's `--jq` filter now also requires
`.head.ref == "roster/proposal"`, not just the same repository, so a
same-repo PR opened from some other branch can never be matched. And three
tracking-issue/PR sentences that used to say "merge it after CI" regardless
of WHY the automatic path didn't run now distinguish: a push that never
succeeded this run ("this run's proposal was not pushed; review before
merging" — never implying this run's content is on the PR), and a
verification failure ("inspect before merging" plus the specific failed
check — never implying the content is safe to just merge).

## Round 4 (adversarial review on #209, spec-roster-mode-r4.md): the badge
## commit-and-push moves off the agent's own job, and the armed PR is
## disarmed before the agent runs

Round 3 moved the roster compute/render/admit/push path off the `eval` job
entirely, onto `roster`. It left one thing behind: `eval` still ran the
badge/results commit-and-push itself, as its LAST step, after the
bypass-permissions agent had already finished — same threat as round 3's,
applied to a different pair of steps. The agent can plant code (a git hook,
an edited repo script, a background daemon, a `/proc` read) that a later step
of the SAME job then executes with whatever credential that step holds; the
badge/commit/push step held `contents: write` and the run's `GITHUB_TOKEN`.
So `eval` must hold NO write scope of any kind, ever — not even one it only
uses after the agent is done, because "after" is a step boundary on the same
runner, not a credential boundary.

**The fix: two more jobs, `publish` and `disarm`, neither running the
agent.** `publish` (`contents: write` only) takes over the badge/commit/push
logic exactly as it ran before, on a FRESH `actions/checkout` the agent never
touched — it downloads the `eval` job's raw output as a build artifact
(`actions/download-artifact`, pinned to the same major as this repo's already
-pinned `actions/upload-artifact`) rather than inheriting `eval`'s workspace,
and it runs `scripts/make_badge.py` from its OWN checkout, never from the
downloaded artifact — the artifact is `eval`'s raw output, untrusted exactly
like everything else `eval-results` already carries, and it must never be
able to substitute its own copy of the very script that builds the badge.
`eval` itself drops to `{contents: read, id-token: write}` — read only for
its checkout steps, and its own separate WIF mint for the preflight and the
run. `publish` runs `needs: [roster, eval]`, `if: !cancelled() &&
needs.eval.result == 'success'`, reproducing the old `roster_only` skip
structurally (a `roster_only` dispatch never runs `eval` at all, so
`needs.eval.result` reads `skipped`, never `success`).

**The armed-PR retarget attack.** Auto-merge is armed on the pull request,
not on a head sha: whatever commit lands on `roster/proposal` next, and passes
`test`, merges. While `eval` held `contents: write`, code planted by its agent
could force-push a hostile `evals/roster.yml` onto `roster/proposal` while an
EARLIER run's auto-merge was still armed, and the automation would merge it
without `roster-pr`'s checks 1-3 ever running. Two changes close it together:
`eval` holds no write scope, so it cannot push at all, and `disarm` turns any
armed auto-merge off before the agent starts, so no earlier arming is exposed
to the agent's run. `roster-pr` re-arms only after verifying this run's own
pushed head, and (round 5) `needs: [roster, eval, publish]`: `--match-head-commit`
is checked only when auto-merge is enabled, so no job holding a write
credential (`publish` holds `contents: write`) may still be running after the
arming. That is the whole guarantee: within a run, no write-credential job
runs after `roster-pr` arms the PR, and `disarm` clears it before the agent.
Between arming and the merge (which waits on a green `test`, possibly for
days) any other write-access actor that pushes `roster/proposal` retargets
the armed PR, and that is outside what this workflow controls. The known
instance is `.github/workflows/dependabot-auto-merge.yml`'s `auto-merge` job,
which holds `contents: write` and whose checkout keeps the default persisted
credential (pre-existing, not changed here). The hardening is a ruleset
restricting updates to `refs/heads/roster/proposal`; round 7 below makes it
possible (the roster App, not `GITHUB_TOKEN`, now pushes that branch), and
this gap is **closed once that ruleset is live** in repo-settings (link to be
added by the repo-settings PR). Until then it stands as written here.

**`disarm` closes the cancellation window the round-3 text above accepted.**
It runs `needs: roster` ONLY, never `eval` — so it completes (or fails
harmlessly; see below) before the agent so much as starts, not after. It
finds whatever pull request is currently open from `roster/proposal` (the
same validated, owner-filtered lookup `roster-pr` uses) and unconditionally
disables its auto-merge. `eval` then gains `needs: [roster, disarm]`, with
its `if:` staying non-fatal on either dependency (`!cancelled()`) so neither
a `roster` failure nor a `disarm` warning ever blocks the eval from running
on the committed roster. `roster-pr` still re-arms auto-merge afterward, but
only for a proposal ITS OWN independent verification (checks 1–3 above)
admits for THIS run's pushed sha — `disarm`'s job is only ever to turn
something off, never to decide what gets turned back on.

**Why disarm-then-rearm, not "make roster-pr run earlier instead."**
`roster-pr`'s whole re-arming logic depends on knowing `eval`'s outcome
(`needs.eval.result`, for the tracking issue's eval sentence) and on
independently verifying what THIS run's `roster` job published — both of
which are only available once `eval` has finished. Splitting "disarm"
(needs nothing from `eval`) from "verify and re-arm" (needs both) into two
jobs is what lets the disarming half run before the agent while the
re-arming half still runs after it, rather than forcing one job to do
both and therefore run twice, or forcing the safety property to wait on
information it does not need.

**`disarm`'s own permissions are `{pull-requests: write, contents: read}`**
— nothing else: it performs no checkout, and reading or disabling a pull
request's auto-merge needs no other scope. A failed lookup, or a failed
`gh pr merge --disable-auto` call, is a fixed `::warning::`, exit 0 — this
job must never fail the run, and a `disarm` failure must never block `eval`.

## Round 6: the roster PR is the App's

**The live finding (2026-09-29).** The first `roster_only` dispatch on
`main` after #209,
[run 36509251840](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/36509251840),
succeeded and opened
[PR #214](https://github.com/Adam-S-Daniel/skills-evals/pull/214) with
`GITHUB_TOKEN`, auto-merge armed. It never merged:

- the PR's own `pull_request` CI run,
  [36509304069](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/36509304069),
  went `action_required` — GitHub holds workflow runs on a pull request
  `github-actions[bot]` opened until someone approves them;
- the `test` run `roster-pr` dispatched,
  [36509302288](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/36509302288),
  passed on the same head sha, but a dispatched run is not the PR's check:
  the PR's status rollup stayed empty and the PR stayed `BLOCKED` on the
  ruleset's required `test`.

So the Context section's claim that a dispatched `test` satisfies the
ruleset, and decision 3's dispatch step built on it, were wrong.

**Decision (the owner, 2026-09-29): a new, dedicated GitHub App** — installed on
skills-evals only, with Contents read/write and Pull requests read/write,
and NOT a ruleset bypass actor. Its client id is the repository variable
`ROSTER_APP_CLIENT_ID`; its private key is the repository secret
`ROSTER_APP_PRIVATE_KEY`.

1. `roster-pr`'s first step mints an installation token with
   `actions/create-github-app-token` (pinned to a full commit sha, v3.2.0),
   for `example-adopter/skills-evals` alone and exactly
   `permission-contents: write` + `permission-pull-requests: write`, under
   `continue-on-error: true`.
2. Under `roster_mode: auto`, and only after checks 1–3 of round 2 pass for
   this run's pushed sha, the App — never `GITHUB_TOKEN` — does every write
   to the PR: with no open roster PR it runs `gh pr create`; with one open,
   it edits it, then `gh pr close` and `gh pr reopen`s it. The reopen is
   there because the `roster` job moved that PR's head with `GITHUB_TOKEN`,
   and a `GITHUB_TOKEN` push fires no `synchronize` run; the App's reopen
   fires a `reopened` `pull_request` event (ci.yml's bare `pull_request:`
   trigger includes it) on the new head. Then the App runs
   `gh pr merge --auto --merge --match-head-commit <pushed sha>`. The PR's
   own `pull_request` run of `test` — the check the ruleset reads — is
   therefore triggered by the App, and needs no approval.
3. The `gh workflow run ci.yml` dispatch is deleted, and `actions: write`
   with it. `roster-pr`'s `GITHUB_TOKEN` drops to
   `{pull-requests: write, issues: write, contents: read}`: `pull-requests:
   write` to turn a stale PR's auto-merge off and close a stale PR (paths
   every mode reaches, `proposal` included), `issues: write` for the
   tracking issue, `contents: read` for the policy/ref/compare reads.
   `contents: write` (round 2's S2) moved to the App token with the arming.
4. **No App token, no automatic PR.** If the mint step failed or produced
   nothing under `roster_mode: auto`, `roster-pr` neither creates nor arms
   a PR with `GITHUB_TOKEN`. It warns, turns off any open PR's auto-merge
   with `GITHUB_TOKEN` as every non-arming path does, and the tracking issue
   says the roster App token was unavailable and the proposal is on
   `roster/proposal` for a human to open as a pull request. The job exits 0.
   `roster_mode: proposal` never uses the App at all; `disarm` stays on
   `GITHUB_TOKEN`, since disabling auto-merge needs only `pull-requests:
   write`.
5. **The key stays out of every job that runs untrusted code.** `roster`
   (which installs the npm-latest CLI), `disarm`, `eval` (which runs the
   agent) and `publish` never reference `ROSTER_APP_*` or the token;
   `roster` keeps pushing `roster/proposal` with its own `GITHUB_TOKEN`
   exactly as before (until round 7, below, moved that push onto the App).
   Inside `roster-pr`, only the mint step reads the key,
   and the token reaches the managing step as `ROSTER_APP_TOKEN`, handed to
   one `gh` call at a time — never as the step's `GH_TOKEN`, since an empty
   `GH_TOKEN` makes `gh` fall back to `GITHUB_TOKEN` silently.
   `TestRosterAppTokenIsConfined` pins all of this on the parsed workflow.

**Why an App, and why a new one.**

- *Not the dispatch:* run 36509302288 is the proof that it cannot satisfy
  the required check.
- *Not a weekly human approval of the held run:* that is `roster_mode:
  proposal` with extra steps.
- *Not a ruleset bypass actor:* nothing here needs to bypass anything. The
  PR still needs a green `test` to merge; keeping the App off the bypass
  list means its token cannot push to `main` either.
- *Not an existing App:* the fleet's `agents-md-sync` App is an
  always-bypass actor on every fleet repo, so its key here would be a
  direct-push credential to every default branch; the `repo-settings` App
  administers every repository; the CMS automation App's key would reach
  the CMS site repos. Each would put a far wider credential in reach of this
  repo's workflows than a PR in this one repo needs.

**Residual.** The key is a stored secret now, readable by any workflow
someone with write access adds to this repo — the same maintainer-only
trust boundary the header of `eval.yml` already draws around the WIF
binding. The App's token holds `contents: write` on skills-evals, so it can
push any unprotected branch (`roster/proposal` included) and merge a PR
that satisfies the ruleset — never `main` directly. `repo-settings`'
`actions.can_approve_pull_request_reviews: true` override for skills-evals
(repo-settings ADR 0003) is no longer needed once this lands, since
`GITHUB_TOKEN` no longer creates pull requests; it will be reverted in
repo-settings separately.

## Round 7: the App publishes the proposal branch

**Why.** The residual gap recorded in rounds 4–6 (see "The armed-PR retarget
attack" above): between `roster-pr` arming the PR and GitHub merging it, any
write-access actor that pushes `roster/proposal` retargets the armed PR. The
known instance is `dependabot-auto-merge.yml`'s `auto-merge` job, whose
persisted checkout credential is `GITHUB_TOKEN` with `contents: write`. The
fix is a ruleset on `refs/heads/roster/proposal` (rules: creation, update,
deletion, non-fast-forward) whose only bypass actors are the roster App
(`example-adopter-skills-evals-roster`, App id `<roster App id>`) and the repository
admin role. But the `roster` job pushed that branch with its own
`GITHUB_TOKEN` — the same `github-actions` identity as the Dependabot job's
credential — so `github-actions` could not be left out of the bypass list
without breaking the roster, and could not be put on it without letting the
Dependabot job through too. So the push moves to the App.

**Decision (the owner, 2026-09-29).**

1. **`roster` no longer commits or pushes.** It still renders
   (`render_roster_yaml.py`) and admission-checks the proposal and still
   owns the "same"/frozen-same issue text, but it hands the rendered file to
   `roster-pr` as a job output — `proposed_roster_b64`, base64 of the exact
   bytes, capped at 32768 bytes (far under the 1 MB outputs limit and
   GitHub's 65536-character issue body; over the cap, or empty, fails closed
   to no proposal) — plus `base_sha`, the validated sha it rendered
   against. A byte-identical render still emits `rendered_identical` and
   nothing else. Its permissions drop to
   `{contents: read, id-token: write, issues: write}`, and its propose step
   loses `GITHUB_TOKEN`/`SERVER_URL` and the push-auth header.
2. **`roster-pr` publishes, with the App token only.** On a "differs" that
   was not rejected and not byte-identical, it re-validates the payload at
   its own job boundary (canonical base64 within the cap, 1–32768 bytes,
   UTF-8, no NUL, parses as a YAML mapping) and a digits-only run id, then
   through the git-data REST API — no checkout — creates a blob, a tree
   (live `main`'s tree plus `evals/roster.yml`, mode `100644`), a commit
   (parent: live `main`, resolved and validated as round 4's S1 does; the
   exact author `skills-evals real-eval bot
   <skills-evals@users.noreply.github.com>` and message
   `roster: proposed model roster (run <run id>)` that verification
   checks), and then `PATCH`es `refs/heads/roster/proposal` with
   `force: true`, or `POST`s it when the ref does not exist. Every write is
   an `app_gh` call; reads stay on `GITHUB_TOKEN`. The created commit is
   `$pushed_sha`: `verify_publish` (head equals it; one commit ahead of its
   parent, none behind, exactly `evals/roster.yml` modified, author and
   message) and `--match-head-commit` both use it. The compare base is now
   that parent, live `main`, rather than `$GITHUB_SHA`.
3. **A stale base never reverts `main`.** If `base_sha` is not live `main`
   and `evals/roster.yml` differs between the two (or cannot be read at
   either), nothing is published: publishing a file rendered against the
   old base on top of the new `main` could silently revert a concurrent
   change to it. The tracking issue says `main` moved and the next run
   re-proposes. If `main` moved without touching `evals/roster.yml`, the
   commit is parented on live `main` and everything else proceeds.
4. **Every mode publishes through the App.** Under `roster_mode: proposal`
   the App pushes the branch too (the ruleset will refuse `GITHUB_TOKEN`),
   but never touches the PR — no create, no arm; the human-merge flow is
   otherwise unchanged. Under `auto` the round-6 flow follows: create, or
   edit/close/reopen, then arm. The App's ref update is itself an App push,
   so an open PR also gets a `synchronize` run; the close/reopen is kept
   because it clears any stale arming, and a second `test` run on the same
   head cancels nothing (ci.yml declares no `concurrency` group).
5. **No App token, nothing published.** In any mode: nothing is pushed —
   never with `GITHUB_TOKEN` instead — the job warns and exits 0, and the
   tracking issue says the proposal could not be published because the
   roster App token was unavailable and carries the rendered file inline,
   fenced longer than any backtick run in it, for a human to apply.
6. **Unchanged:** `disarm`, and the rule that `eval`, `publish`, `disarm`
   and `roster` never reference `ROSTER_APP_*` or the App's token
   (`TestRosterAppTokenIsConfined`).

**What it closes, and what it does not.** Once the repo-settings ruleset is
live, nothing but the App and an admin can move `roster/proposal`, so the
Dependabot job — and any other `GITHUB_TOKEN` holder — can no longer
retarget an armed PR. An admin can still push the branch; that is the same
trust as an admin's push to `main`. It also narrows N3 (Consequences): the
`roster` job, which installs the npm-latest CLI, no longer holds any write
scope on the repository's contents. A side effect: no job pushes
`roster/proposal` before `disarm` runs any more, so the window `disarm`
leaves is its own run alone. Merge this change before the ruleset, and
verify it with a `roster_only` dispatch; it works with or without the
ruleset in place.

## Addendum 2026-10-26: fixture pull requests merge the same way

**Decision (the owner, 2026-10-26: a "closed loop with no human in the loop").**
Eval fixtures are the improvement queue (improve needs several per skill,
and only writing-adrs has enough), and until now each one waited on a
person twice: a scaffolded real-work fixture landed as a draft pull request
a person reviewed and merged, and a merged fixture stayed `draft: true`
until a person removed the key and scheduled it. Both now merge through
this ADR's mechanism, with no human review:

1. **Scaffold pull requests** (`routine-scaffold-gate.yml`). When
   `scripts/scaffold_real_work.py gate` passes, the roster App opens the
   pull request ready for review (or marks an open draft ready) and runs
   `gh pr merge --auto --merge --match-head-commit <validated sha>`. Decision
   4's rule carries over as a `disarm` job that turns auto-merge off on the
   branch's open pull request on every gate run, before validation, and
   `routine-scaffold-pushed.yml` loses its path filter so that every push to
   the branch reaches it; only a push that passes the gate is re-armed.
2. **Promotion pull requests** (`routine-fixture-supply.yml`, daily).
   `scaffold_real_work.py promote` flips `draft: true` to `draft: false` and
   adds the fixture to `evals/scheduled.yml` once it has two ingested runs
   with both arms and no errored arm for its committed content (never under
   an `onboarding` or `browser-testing` directory). The App pushes the change
   to `fixture-promotion/<run id>`, a new branch each time, opens the pull
   request and arms it the same way. One is open at a time.
3. **The same App and the same fallbacks** (round 6): the token is minted in
   the one job that writes the pull request, reaches only `gh` and the one
   push, and is never GH_TOKEN. No token: the scaffold gate opens a draft
   with GITHUB_TOKEN and arms nothing (the flow before this addendum), and
   promotion publishes nothing.
4. **What a person used to check, and what checks it now.** The checker
   files must be the merge commit's own (the gate compares them with trusted
   Git bytes, replacing the reviewer's `CHECKER_BLOBS` entry); ci.yml and
   `scripts/dev_setup.sh` derive the dependency installs from each fixture's
   own `deps:` (`scaffold_real_work.py deps-dirs`), so a scaffold changes
   nothing outside its directory; and `test` scores every real-work fixture
   red on the seed and green with the fix. A fixture that fails any of them
   leaves its pull request open, armed, and unmerged.

**Residuals.** The prompt and `interface_strings:` are model-authored and now
reach `main` unread; the gate lints them but does not judge them. Between
arming and the merge, a write-access actor that pushes the scaffold branch
retargets the armed pull request until that push's own `disarm` runs, the
gap round 4 names for `roster/proposal`; a `test` run takes far longer than
`disarm`, and no ruleset restricts `claude/scaffold-*` yet. There is no
policy flag: switching back is reverting the two workflows' App steps.

## Consequences

- **The `roster` job installs the npm-latest Claude Code CLI while holding
  its write scopes** (N3, round 4 on #209): `roster`'s "Install Claude Code
  CLI" step is the same unpinned-and-always-latest install the `eval` job
  runs (the owner's decisions of 2026-09-27/28, #202/#203) — it runs BEFORE
  the WIF mint, so nothing it installs runs with a credential in the
  environment at install time (same guarantee `eval`'s copy already has),
  but `roster` still goes on to hold `id-token: write`/`issues: write`
  (and, until round 7, `contents: write`) for the rest of its own steps, on
  the SAME runner that install just ran on. This is the owner's existing unpinned-CLI decision,
  extended by inheritance to a second job rather than reconsidered by this
  round. Optional hardening — a separate CLI-install job with no write
  scope, feeding `roster` only the resolved version string — is noted here,
  not implemented; the probe's own reasoning (a version-pin's stale audit
  trail vs. a per-run recorded version) applies here as much as in `eval`.
- **The untrusted census can now move which tiers are on the roster with no
  human reading the diff first**, when the probe is clean. `test` still
  gates shape (the committed-roster contract, admission, the harness's own
  suite) but not the reviewer judgment ADR 0001 reserved. This is the
  decision's whole cost, and the owner took it knowingly rather than as an
  oversight.
- **A bot merge on `main` does not trigger `push`-triggered workflows** (as
  written for `GITHUB_TOKEN`; since Round 6 the App enables the merge, and
  App-token events do trigger workflows — expected, not yet observed) the
  way a human's merge commit does, for the same reason a `GITHUB_TOKEN`-
  opened PR does not fire `pull_request` workflows: `GITHUB_TOKEN` actions
  do not re-trigger `on:` events by design, to prevent runaway recursion.
  Nothing in this repo currently depends on a `push`-to-`main` trigger for
  roster changes, so this is recorded rather than mitigated.
- **A bot merge gets no `test` run on the merge commit itself** (N2,
  adversarial round 1 on #209): the same `GITHUB_TOKEN`-does-not-retrigger
  rule above means `main`'s post-merge sha never gets its own `test` run.
  What actually satisfies the ruleset's required check is the PR's own
  `pull_request` run of `test` on its HEAD (`roster/proposal`'s pushed
  sha) — since Round 6, the run the App's open or reopen triggers; the
  dispatched run this bullet originally named never counted — which ran
  against `main` as it stood when that run started — not
  against whatever else may have merged to `main` between this run starting
  and GitHub performing the merge. That gap is the same one any PR merge
  carries between its last CI run and the merge button; `roster_mode: auto`
  does not widen it, it just removes the human who would otherwise notice.
- **The toggle also lets Actions approve pull requests generally**, not only
  merge them, on this repo — a side effect of the one GitHub setting this
  depends on. Harmless at the ruleset's 0 required approvals: there is
  nothing to approve away. Since Round 6 nothing here depends on the
  toggle; it is to be reverted in repo-settings.
- **Switching back is one edit.** Setting `roster_mode: proposal` in
  `evals/roster-policy.yml` restores ADR 0001's human-merge flow exactly;
  no code path is deleted, so the switch is reversible without a revert.
- **Managing "Allow GitHub Actions to create and approve pull requests" as
  code**, rather than by hand in repo settings, is out of scope here and
  tracked on [repo-settings#51](https://github.com/Adam-S-Daniel/repo-settings/issues/51).

## How to switch back

Set `roster_mode: proposal` in `evals/roster-policy.yml` and merge that one-
line change. The next differing proposal is published to `roster/proposal`
(by the roster App, since round 7) and filed as a tracking issue exactly as
before this ADR; nothing else changes.
