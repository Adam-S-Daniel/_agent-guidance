# ADR 0004: The scheduled eval runs on the roster its own run just merged

- **Status:** accepted (2026-09-30)
- **Issue:** none filed; the owner's decision of 2026-09-30.
- **Deciders:** the owner, directly: "Remove the need for me to approve roster
  changes and have the roster update occur immediately before evals run."
  This amends [ADR 0001](0001-roster-trusted-on-main.md) (the eval runs on
  the committed roster, so a proposal applies from the next run) and
  [ADR 0003](0003-roster-merges-automatically.md) (`roster-pr` arms the
  roster pull request last, after `eval` and `publish`).

## Context

Under ADR 0003 the weekly run computed a roster, published it to
`roster/proposal`, and under `roster_mode: auto` armed the pull request for
auto-merge with `--match-head-commit`. It did that in the `roster-pr` job,
which ran LAST (`needs: [roster, eval, publish]`), for one reason given in
round 5 on #209: `--match-head-commit` is checked only when auto-merge is
enabled, so no job holding a write credential may still be running once the
PR is armed, and `publish` holds `contents: write`.

The consequence was a one-week lag. The eval runs on the COMMITTED
`evals/roster.yml` (ADR 0001), so a retirement or a new seat a Tuesday run
proposed merged after that run's eval and only applied the following
Tuesday. With `claude-opus-5` due to retire off the census of 2026-10-05, the
2026-10-06 eval would still have run four arms, a week of paid spend on a
seat already decided.

Two facts constrain any fix. The eval job runs the bypass-permissions agent
and must hold no GitHub write credential (B1, round 4 on #209), and nothing
may be armed while that agent runs (the `disarm` job's rationale). The
roster data may change under a run, but the harness CODE must stay at the
run's own `$GITHUB_SHA`: `main` can move for other reasons between the run's
start and the merge.

## Decision

1. **The job order becomes roster -> disarm -> roster-pr -> roster-wait ->
   eval -> publish.** `roster-pr` needs `[roster, disarm]` and publishes and
   arms exactly as before: same admission checks, same App token, same
   `verify_publish`, same fallbacks to a human for a dirty probe, a rejected
   proposal or `roster_mode: proposal`. [ADR 0002](0002-roster-follows-vendor-defaults.md)'s
   guarantee that one bad probe run changes no seat a clean run would not
   is untouched: nothing new can arm.
2. **`roster-pr` hands on what it armed.** Only once auto-merge is enabled
   on its own published commit does it emit `armed=true`, the PR number and
   the pushed sha. It no longer reads `needs.eval.result`, so its tracking
   issue says what will happen (the eval follows, on the merged roster if
   the merge lands within the wait, else on the committed one) and points at
   the run summary for what did.
3. **A new `roster-wait` job waits, bounded.** It holds exactly
   `pull-requests: write` and `contents: read`, no App token, no checkout,
   and runs only on `main` and never on a `roster_only` dispatch. It polls
   the armed PR read-only every 30 s for at most 60 polls (30 minutes; the
   PR's required `test` takes about 7 to 12). The interval and cap are
   environment variables the workflow does not set, so tests run the loop
   with no sleep. It stops early on a terminal state: closed unmerged,
   auto-merge turned off, head moved off the armed commit, or a PR that is
   not this repository's `roster/proposal` onto `main`. It cannot stop early
   on a failed `test`, since reading check runs needs `checks: read`; such a
   PR simply does not merge and the wait ends at the cap.
4. **A merge is used only if it verifies.** The merged head is the armed
   commit; the PR's files are exactly `evals/roster.yml`, modified;
   `evals/roster.yml` at the merge commit equals the proposal the `roster`
   job rendered this run, byte for byte; and the merge commit is on live
   `main`. Anything else, including any API error during verification, is
   reported as the committed roster with a fixed reason.
5. **The eval and `publish` do not run unless the arming is confirmed
   cleared (fail closed).** On a timeout the wait step asks for auto-merge
   off and reads the PR once more; a merge that landed in between is still
   used if it verifies. The wait job's second step runs `if: always()`,
   after a merge, a timeout, a failure or a cancellation of the wait step:
   it finds any open roster PR with the same owner-filtered lookup
   `disarm` uses, and also takes the PR number `roster-pr` armed (which
   still finds a PR whose base was moved off `main`); for each it turns
   auto-merge off if it is on and RE-READS it. It emits `cleared=true` only
   when every re-read shows auto-merge off (or no roster PR is open or
   armed). A failed lookup, read or disable, or a disable that did not
   take, is `cleared=false` with a warning and a summary line saying the
   eval and `publish` do not run, and the step then exits 1: that is a
   failed `roster-wait` job and a red run. `eval` and `publish` both
   require `cleared == 'true'`, unless `roster-wait` was skipped (off
   `main`); a `roster-wait` that failed or was cancelled before that step
   leaves `cleared` empty, so neither runs then either, and that failed
   job is the signal. `disarm` stays, and still clears an earlier run's
   stale arming before anything else runs.
6. **The eval takes one file from the merge, never the code.** Its first
   step after the checkout fetches the merge commit by sha (no credential;
   the checkout persists none) and re-verifies it with local `git`: it
   descends from the run's own commit, it changes exactly `evals/roster.yml`
   against its first parent, and that file equals the proposal byte for
   byte. Then it writes that one file over the checkout's copy, before the
   preflight and the eval read it. The rest of the tree stays at
   `$GITHUB_SHA`; a newer `main` is never checked out. Every other outcome
   leaves the committed file and says why in the step summary.
7. **`publish` keeps `contents: write` and runs after `eval`**, gated on the
   same confirmation. That is what keeps round 5's invariant: within an eval
   run, no job holding a write credential runs while a PR is armed.
   `roster-wait` holds only `pull-requests: write` and runs no code that
   could move `roster/proposal`.

## Consequences

- **A proposal can apply in the run that proposed it.** On 2026-10-06 the
  run that retires `claude-opus-5` also evaluates without it, if its roster
  PR's `test` passes and it merges within the wait.
- **An unconfirmed disarm costs the week's eval, and the run is red.** If
  `roster-wait` cannot confirm no roster PR is left armed, the eval and
  `publish` are skipped rather than run beside a possibly armed PR, and
  `roster-wait` fails so the run shows it. `disarm` stays fail-open (it
  concerns an earlier run's arming, and the sweep is the gate); it now
  leaves a PR with no auto-merge alone, so an unarmed proposal-mode PR
  raises no warning there.
- **A run gets longer.** Up to 30 minutes of waiting, plus the `test` run on
  the PR, sit between the roster job and the eval. A PR whose `test` fails
  costs the full cap before the run falls back, because the wait job has no
  `checks: read`.
- **The tracking issue cannot report the wait's outcome.** The wait job has
  no `issues: write`, and `roster-pr` writes the issue before the wait. The
  issue says what will happen in each case; the run summary (`roster-wait`
  and the eval job's roster step) says what did. A `roster_only` dispatch's
  issue and PR body say no eval follows and nothing waits. A PR whose wait timed out
  stays open with auto-merge off, and the next run re-arms it.
- **The census reaches the eval one run sooner.** ADR 0003 already let the
  untrusted census move which tiers are seated with no human reading the
  change; now that change also shapes the same run's paid eval. The same
  gates still apply (admission, a clean probe, the PR's required `test`,
  byte equality with this run's proposal).
- **The eval job fetches once more.** The fetch is anonymous and by sha, on
  a runner that holds no GitHub credential. If the repository ever becomes
  private, the fetch fails and the eval runs on the committed roster with a
  warning, until the step is given a read credential.

## Alternatives considered

- **Merge the proposal in the `roster` job, or have the eval run on the
  proposal before it merges.** Rejected: ADR 0001's point is that the eval
  runs on a commit on `main`. The PR's required `test` is the gate.
- **Do the wait inside `roster-pr`.** Rejected: that job holds the App
  token (`contents: write` on the App), so waiting there would keep a write
  credential alive beside an armed PR, the exact case round 5 forbids.
- **Give the wait job `checks: read` to stop early on a failed `test`.**
  Not taken: the spec for this change fixed the job's permissions at
  `pull-requests: write` and `contents: read`. A failed `test` costs the
  cap, once, and the fallback is the committed roster.
- **Check out the newer `main` in the eval job.** Rejected: `main` may have
  moved for other reasons, and the eval's code must stay the run's own.
