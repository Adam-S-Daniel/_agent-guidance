# 0006 — The bumper's own pull requests land on a sweep, not on a reviewer

**Status:** Accepted (2026-08-19). Supersedes one decision in
[0005](0005-consumer-locks-are-re-pinned-from-here.md) — "A pull request, never
a direct push and never auto-merge", and the *Left open* bullet that begins
"Nothing merges these". Everything else in 0005 stands unchanged, and this ADR
depends on several parts of it.

## Context

**The requirement.** The operator wants the pull requests the lock bumper opens
to land without someone clicking merge.

0005 decided the opposite, and recorded what that would cost:

> **A pull request, never a direct push and never auto-merge.** The App's
> ruleset bypass exists so `AGENTS.md` can land unattended; this diff is
> different in kind.

> **Nothing merges these, and nothing notices one going stale.** There is no
> auto-merge by choice. The drift report prints the pin on each repo's
> **default** branch, so an unmerged bump PR is invisible there; ten adopters
> make an accumulating pile more likely than two did.

That pile is the reason this ADR exists. One skill edit is up to ten pull
requests (0005, *Costs*), and the merge is the only step in the chain still
done by hand — the re-pin is generated, the currency question is the
generator's, the delivery is a hook. A queue of ten identical one-line diffs is
exactly the thing a fleet learns to stop reading, and an unmerged bump PR
delivers the *old* skill to every ephemeral session while reporting `OK`, which
is the failure mode 0005 was written to end.

**The constraint, measured in this repo: native auto-merge cannot arm here.**
`.github/workflows/dependabot-auto-merge.yml`'s header records it —

> the default-branch ruleset here sets `required_approving_review_count: 0` and
> `required_status_checks: []`. With NO required checks configured, GitHub's
> native auto-merge REFUSES to arm — `gh pr merge --auto` errors that the PR is
> already in a clean/mergeable state, because there is nothing for auto-merge
> to hold the merge FOR. […] The workflow that actually lands PRs unattended is
> job `sweep`, which merges DIRECTLY on a schedule.

So the one-flag answer — add `--auto` after `gh pr create` — would have read as
"done" in the diff and changed nothing at all on most of the fleet. Note what
the refusal is *not*, because it is the hazard that looks identical: GitHub does
not merge the PR instead. It declines to arm, and the PR sits.

**What `AGENTS.md` forbids, and what it does not.** "PR + auto-merge is not a
sanctioned bot-write path for fleet repos; the cms-platform-managed repos
(outside the fleet ruleset) use it by their own design." That rules out
*designing* a bot around native auto-merge as its write path — which is also
the design that does not work here. It does not rule out a scheduled job that
merges a pull request the operator has asked to land: that is the pattern this
account already ships for the same problem in the same repo
(`dependabot-auto-merge.yml`'s `sweep` job), for the same measured reason.
**This ADR chooses the sweep, deliberately, and not auto-merge-as-write-path.**

## Decision

**The nightly run makes two passes, and sweeps before it proposes.**

**1. Sweep.** Before anything is proposed, the run lists the bump pull requests
a *previous* run left open, on each repo it discovered, and merges the ones that
pass every gate below.

The ordering is the load-bearing part and the easiest to lose in a later
refactor. A pull request merged seconds after it was opened is merged before any
check has started: the rollup is empty not because the repo has no CI but
because CI has not woken up yet, and the gate below would read that as "nothing
to wait for". Sweeping first instead makes the gap between two nightly runs the
window a consumer's CI gets — a full day — with no polling, no waiting inside a
job, and no state carried between runs. **The open pull request is the state.**

**2. Propose**, exactly as 0005 describes it, unchanged.

**A newly opened PR still asks for native auto-merge**, and the run does not
care whether it takes. It will not take on this fleet, for the reason above; it
costs one API call, and it starts working for free on any repo whose ruleset
ever grows a required check — on that repo the PR then lands when its checks
pass rather than waiting for tomorrow's sweep. A failure to arm is neither a run
failure nor a per-repo failure. This is the same reasoning
`dependabot-auto-merge.yml` gives for keeping its own `--auto` attempt, and the
sweep is what actually lands these in both files.

**`--merge`, never `--squash` or `--rebase`.** Squash and rebase are disabled on
every fleet repo, so `--squash` fails outright rather than falling back; and
squash is actively unsafe for a fleet that pins commits by sha, because it
strands the pre-merge commit on no branch (`AGENTS.md`, *Git practices*,
measured 2026-08-15). `--merge` is the one form that works everywhere.

**The registry is not swept**, on the same carve-out 0005 makes for proposing:
nothing here opens a pull request on it, so anything sitting on that branch name
there is somebody else's. This repo is swept, like every other consumer, for
0005's reason — it self-hosts a lock and nothing else re-pins it.

**The gate, in one function** (`pr_merge_verdict`), because merging is the one
thing here that nobody reviews. It refuses unless *all* of:

- **The pull request is ours — head branch AND author, never one of the two.**
  The branch name is a convention anyone can push to; the author is the part a
  stranger cannot forge. The listing filter (`--head`) is a query, not a
  guarantee, so the branch is re-checked from the PR itself.
- **The diff is `skills.lock` and nothing else.** This is the guard that keeps
  an auto-merging bot from landing someone else's work: push a second file onto
  the bot's branch and the branch is yours now — the sweep skips it and says so.
  It is the mirror of 0005's staged-files refusal on the write side.
- **Not draft, no changes-requested review, `mergeable == MERGEABLE`**, and not
  a `mergeStateStatus` of `DIRTY` / `BLOCKED` / `DRAFT` / `UNKNOWN`. `BLOCKED` —
  a repo whose own rules want a review, or a required check this sweep cannot
  satisfy — is a **skip, not a failure**: attempting the merge would be refused
  every night and paint the scheduled run permanently red for a repo behaving
  exactly as configured.
- **No check is un-green, and none is unfinished.** A check run carries
  `.conclusion` (null until it concludes) and a legacy commit status carries
  `.state` and no conclusion at all; read one and the other's failures come back
  clean, which this account has a real incident about (`AGENTS.md`, *"The watch
  finished" is not "CI passed"*). Both are read, and "pending" and "still
  running" are treated exactly like "not green yet".
- **An ABSENCE of checks is not a failure.** Most consumers in this fleet run no
  CI at all, and a sweep that held out for a green check on those would merge
  nothing, ever. "No checks ran" and "the checks passed" are different sentences
  in the log so the two can never be confused for one another afterwards.

**And the merge is pinned to the commit the gate judged** — `gh pr merge
--match-head-commit <sha>`. Everything above reads a *snapshot*; the merge goes
out afterwards. Every refusal in the list arrives on the branch by a push, and a
push can land in that gap, so without the pin the verdict and the merge are two
decisions and what lands is whatever the branch holds by the time the merge goes
out. With it they are one: GitHub refuses rather than merging a diff nothing
checked. The flag is **probed, not assumed** — unlike `--repin` it is not
load-bearing, so a `gh` too old to have it degrades to a non-atomic merge and
says so once, rather than grounding the fleet. An oid that does not read back as
a sha is refused outright: "cannot pin it" is not permission to merge it
unpinned, the same rule the verdict itself follows.

**The sweep is strictly stricter than native auto-merge would have been**, and
that is worth stating because it inverts the obvious worry. Auto-merge holds a
pull request only for the checks a ruleset marks **required**; this fleet marks
none, which is why it cannot arm at all. The sweep reads the whole
`statusCheckRollup`, so *any* check a repo reports blocks the merge whether the
ruleset requires it or not. Concretely: this repo runs `ci.yml` on
`pull_request` and requires nothing, so its own bump pull request (it
self-hosts a lock — ADR 0004) is gated on that suite going green, where
auto-merge would have had nothing to wait for.

**`--dry-run` reports every merge it would make and makes none**, on the same
fail-closed argument parsing 0005 already describes. **A per-repo failure is
counted and the loop continues**; the run exits non-zero if anything failed, so
a scheduled failure surfaces through `scheduled-run-health`. Merges are reported
per repo in the run log and counted in the summary line alongside the existing
proposed / skipped / failed counts.

**No new credential, and no change to the workflow's `permissions:`.** Merging
(`PUT /repos/{owner}/{repo}/pulls/{n}/merge`) wants **Contents: write** and
**Pull requests: write** — the same pair the branch push and `gh pr create`
already need, and the pair the workflow's own verify step names when it tells
you how the `agents-md-sync` App must be installed. The merge is made with the
per-owner installation token, which already holds it. The workflow's
`permissions: contents: read` block governs `secrets.GITHUB_TOKEN`, which is
scoped to *this* repo and does none of this work; raising it would grant the
script nothing. That distinction is worth keeping straight, because
`contents: read` sitting above a job that merges pull requests across twenty
repos looks like a bug and is not one.

## Consequences

**Good.** The last hand step in the chain closes: a skill edit reaches every
ephemeral session in the fleet within about a day of the registry commit,
without anyone clicking merge ten times. The pile 0005 predicted cannot
accumulate, because the same run that would add to it clears it first. Nothing
about the *proposal* side changed — the anti-churn gate, the primary-scoped
currency question, the federation guarantees and the shrink refusal are all
0005's, untouched.

**Costs, honestly.**

- **A wrong lock now lands without a human, and on most consumers no CI looks
  at it either.** That is the real price and it should not be dressed up. Four
  things limit the damage, none of which is review:
  - `--repin` **inherits** the consumer's `registry`, `bundles` and whole
    `sources` array and re-resolves only the primary `ref` (0005). A writer that
    cannot express the declaration cannot flatten it, so the class of damage
    ADR 0001 named is not reachable by this path however unattended it is.
  - **The anti-churn gate** means a pull request exists at all only because the
    generator said the bundle *content* moved — not because a ref did.
  - **The shrink guard** turns a re-pin whose `skills` map came back empty, or
    that lost every skill of a bundle it still declares, into a per-repo
    **failure** rather than a proposal. That is precisely the fleet-wide
    mistake — a renamed or deleted bundle — that auto-merging would otherwise
    fan out in one night.
  - **The diff guard** means the only bytes that can land this way are the
    lock's.
  - Consumers that *do* have CI still gate on it — on every check they report,
    required or not (above). Most consumers report none, and for those the
    merge is unreviewed by construction; that is the residue.
- **The diff guard is a path guard, not a content guard.** It proves the pull
  request changes `skills.lock` and nothing else; it does not prove the lock on
  that branch is the one `--repin` wrote. Anyone with push access to the
  consumer could rewrite the lock in place on the bump branch and the sweep
  would merge it. Two things bound that, neither of them comforting on its own:
  pushing to that branch already requires write access to that repo, and the
  fleet ruleset gives such a person a zero-approval pull request of their own
  anyway — so this is a quieter capability, not a new one. Note the ordering
  cost, though: the propose pass *does* notice a tampered bump branch (it
  refuses to push over one whose content differs, and warns), but it runs
  second, so on the night the tampering lands the warning arrives after the
  merge. Closing it properly means re-deriving the branch's lock before merging
  — a clone and a generator run per open pull request. Refusing a PR with more
  than one commit is the cheap version and was rejected: it would silently
  strand, forever, any pull request somebody had clicked "Update branch" on.
- **The window to intervene is one day, and it is the only window.** A PR opened
  tonight is merged by tomorrow night's run. 0005 already recorded that closing
  a bump PR is not a way to say no (the next run re-proposes it); what changes
  here is that closing it is now also *time-limited*. The way to say no is still
  to remove the repo's lock or take the repo out of scope, and `repos.yml` still
  has no "bump-exempt" key.
- **Reverting is a human's job.** Nothing here rolls a merged lock back.
- **A repo whose ruleset wants a review is skipped every night**, logged and
  otherwise silent — the same "nobody reads the log on a green run" gap 0005
  named for federated pins.
- **One extra API call per repo per night** (`gh pr list`), including on the many
  repos that have no lock at all. It buys the property that a bump PR is swept
  by whatever run comes next, with nothing remembered between runs.
- **The suite proves the orchestration, not GitHub.** `gh` is a mock: it answers
  PR list / view / merge from fixtures, computes each PR's file list from the
  real branch, and moves the default branch on a merge. So the tests prove which
  merge method was asked for, and which pull requests the gate refuses and why —
  they prove nothing about GitHub's own merge behaviour, exactly as 0005's
  stand-in generator proves nothing about the real generator's digests.
  The mock computes each PR's `headRefOid` from the branch the same way it
  computes `files`, so the pin above is checked against a real commit rather
  than a declared one, and it can be made to disagree on purpose.
- **A negative assertion whose needle starts with a dash was vacuous**, and this
  change is what surfaced it. `assert_not_contains` grepped with the needle in
  option position: `grep -qF "--match-head-commit"` is parsed as a *flag*, exits
  2, and `2>/dev/null` turns that into "no match" — so the assertion passed
  whatever the file held. It failed loudly in the `assert_contains` direction
  (which is why no existing test hid a bug behind it) and silently in the other.
  Every helper now greps with `--` before the pattern. The lesson is the
  house negative-control rule in another costume: a guard's test has to be seen
  failing, or all it proves is that the harness is quiet.

**Left open, deliberately.**

- **A bump PR that can never be merged is swept forever.** A conflict, a
  changes-requested review, a `BLOCKED` ruleset: each is a skip with a log line,
  every night, and nothing escalates. Making that visible — a drift-report
  column, an issue — is the same unfinished business 0005 left for a stale
  federated pin, and is deliberately not in this change.
- **The sweep sees open PRs only**, through `gh pr list`, with the same
  limitation 0005 records for the propose side.
- **Advancing a federated source's pin is still a human's job.** Unchanged.
