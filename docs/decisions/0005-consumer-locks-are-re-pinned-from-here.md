# 0005 — Consumer locks are re-pinned from here, and only when the bundle moved

**Status:** Accepted (2026-08-19). Superseded in part by [0006](0006-bump-prs-land-on-a-sweep.md) (2026-08-19), which reverses "A pull request, never a direct push and never auto-merge" and the *Left open* bullet beginning "Nothing merges these": the bumper now merges its own pull requests on a sweep. The rest of this ADR stands.

## Context

ADR 0001 closed by naming the one thing missing from `skills-bootstrap`
delivery, and by holding the allowlist at two repos until it existed:

> **Nothing re-pins a stale lock.** A lock pinned far behind installs cleanly
> and reports `OK` in-session — by design, since `--check` asserts faithfulness
> to the pinned ref, not currency. The drift report's Notes column now prints
> each lock's pins so staleness stops being *invisible*, but re-pinning belongs
> in `agentskills` (it owns the generator and the digests) as a
> `platform-bump.yml`-shaped per-consumer PR. **Do not widen the allowlist past
> what a human can re-pin by hand until that exists.**

ADR 0004 widened it to ten anyway, on the strength of this landing, and was
explicit that the ordering was the risk: "the mitigation is to merge the
re-pinner promptly rather than to trust that nobody notices."

**The manual cost is measured, not assumed.** `adamdaniel.ai` needed **four**
hand re-pins in the four days after it adopted. That is the two-repo world. Ten
adopters, each federating on its own cadence, is not a bigger chore of the same
kind — it is a chore nobody does, and an undone re-pin is invisible by
construction: the hook installs the pinned commit faithfully and reports `OK`.

**The generator half landed in the right repo, and this ADR is written after
checking that rather than on the strength of a PR having been opened.**
`agentskills` PR #103 added `generate_skills_lock.py --repin`, which loads the
lock at `--output`, inherits its `registry`, `bundles` and whole `sources`
array, and re-resolves only `ref`. `--registry`, `--bundles` and `--source` are
an argparse **error** alongside it, because `--source` did not merely override
the inherited array — it *replaced* it, which is ADR 0001's de-federation trap
reached through the flag written to close it. It is on that repo's default
branch as of `b09532b` (2026-08-19), which is what the workflow checks out,
having pinned no `ref:`. The ordering mattered enough that the bumper does not
assume it: it probes the generator for `--repin` once at startup and exits with
one stated message if it is absent, rather than discovering the shortfall as an
argparse failure per stale consumer, nightly.

**What could not land there is the fan-out, and the reason is a credential.**
`agentskills`' workflows hold `secrets.GITHUB_TOKEN`, scoped to `agentskills`.
A bumper there could not push a branch to a consumer or open a PR on one — not
in the other owner, not in the same one. This repo holds `vars.APP_CLIENT_ID` +
`secrets.APP_PRIVATE_KEY` for the `agents-md-sync` App, which mints a
short-lived installation token **per owner** (`sync.yml`, `drift-report.yml`),
and that is the only credential in the account that reaches ~20 repos across
`Adam-S-Daniel` and `jodidaniel`.

**And one fact decides the whole shape of the thing: being behind is not being
stale.** `--check` asks whether a lock faithfully describes the commit it pins;
`--check-current` asks whether that commit is still the bundle. Most commits in
the registry touch no bundle at all — docs, scripts, workflows, the lock itself
— so "the ref is not HEAD" is true almost always and means almost nothing.

## Decision

**The re-pinner lives here, as its own script and its own workflow, and it
re-pins on bundle content rather than on ref.**

`scripts/bump-consumer-locks.sh` discovers repos exactly as `sync.sh` does,
reads each one's `skills.lock` off its default branch, and for the ones whose
lock names the registry being bumped **as its primary** asks the **generator** —
never a home-grown test — whether a re-pin is needed:

```
generate_skills_lock.py --check-current --repo <the registry checkout> -o <a copy of the lock with `sources` removed>
```

Exit 0 means the bundle content at the pinned ref still matches that
checkout's **working tree** (in CI a fresh `actions/checkout`, so tree and HEAD
coincide; by hand on a dirty clone they need not). **That is a skip: no branch,
no commit, no PR.** Only its own `FAILED:` verdict starts a re-pin; a non-zero
exit without one is a per-repo failure, because the generator reports a broken
lock, an unreachable pinned commit and a mis-pointed `--repo` with the same
exit code, and re-pinning on the strength of one of those writes a lock nobody
asked for.

**The question is scoped to the primary, and that is the design.**
`--check-current` reads *every* source a lock names — pass `--source-repo` for
each and it emits one `FAILED:` for the whole lock if any of them differs —
while `--repin` advances only the primary `ref`. Gate on the combined verdict
and the gate turns on a fact the re-pin cannot change: the moment a federated
checkout sits ahead of its pin that `FAILED:` is permanent, and every commit to
the primary registry, bundle-touching or not, yields a pull request whose whole
diff is `ref` + `generated_from` — precisely the churn the rest of this ADR is
built to avoid, aimed at exactly the federated adopters. Handing the generator a
copy of the lock with `sources` removed asks a *different question* rather than
reinterpreting the combined answer, so it cannot drift with the generator's
wording. The combined question is still asked when the primary is current, but
only to log that a federated source has moved; advancing that pin is a human's
job, and this tool says so rather than doing something else instead.

Five properties follow, and each is the reason for a piece of the shape.

**ADR 0001's invariant survives, structurally rather than by care.** `sync.sh`
still has no code path that writes `skills.lock`, still refuses to commit if the
lock is ever staged, and was not touched. The bumper is a separate script and a
separate workflow. What makes that more than a filing decision is *what the
bumper can write*: `--repin`'s output, which inherits the consumer's own
registry, bundles and sources. What ADR 0001 recorded was that "any canonical
lock pushed fleet-wide would flatten the federated one" — a writer that cannot
express the declaration cannot flatten it. The guard is mirrored rather than
dropped: the bumper refuses to commit if anything **other than** `skills.lock`
is staged. Two further refusals sit on the same side of the same line: a lock
that names this registry only under `sources` is skipped rather than having its
*other* registry's pin advanced, and a re-pin whose `skills` map comes back
empty — a bundle renamed or deleted at the registry's new HEAD — is a per-repo
failure rather than a proposal, because `skills-bootstrap` reaps the installed
skills of a bundle a lock declares but has emptied, and that would land on every
consumer in one run.

**The registry is excluded; this repo is not.** `agentskills` owns the order of
operations (content commit → re-pin → lock commit) and asserts it in its own CI
with `--check-current`; a bot PR would race the author mid-sequence. That is the
same carve-out `drift-report.sh` already makes when it exempts the registry from
`unmanaged`. `$SELF_REPO` is the opposite case and is **deliberately not
filtered**, unlike in `sync.sh` and `drift-report.sh`: those two drop it because
delivering guidance to itself and reporting drift against itself are meaningless,
whereas this repo self-hosts the hook and carries a real lock (ADR 0004) that
goes stale exactly like a consumer's. Filtering it would leave the one repo the
fleet's mechanisms cannot see as also the one repo nothing re-pins.

**The bootstrap allowlist is not consulted; the lock is.** `repos.yml`'s
`skills_bootstrap.repos` governs whether the *sync delivers the hook*. A
committed `skills.lock` is the repo's own statement that something installs
bundles there, and that is the only key currency depends on — which is why this
repo, allowlisted nowhere by design, is still bumped. `exclude:` **is** honoured:
a repo the fleet has decided not to touch is not touched.

**A pull request, never a direct push and never auto-merge.** The App's ruleset
bypass exists so `AGENTS.md` can land unattended; this diff is different in kind.
Its visible content is digests, and what it actually changes is which instruction
text gets installed into every ephemeral session in that repo, with no approval
prompt. The PR body therefore discloses what the diff cannot: which ref moved,
that every digest is re-derived from the newly pinned commit via `git archive`
rather than from anyone's working tree, that federated sources keep their pins,
and that the whole change is `--repin` output and was never hand-edited.

**Nothing is force-pushed, and `--dry-run` fails closed.** A bump branch that
already exists with different content is a warning and a skip, with the recovery
this repo's `AGENTS.md` already records for a stale bot branch: merge or close
its PR to free the name. A *rejected push* is classified on git's own
non-fast-forward wording rather than on the word "rejected", which the server
also prints for a ruleset restricting ref creation (GH013) — diagnosed as a
stale branch, that prints a remedy for a branch nobody created, counts a stale
consumer as a skip and leaves the run green. Arguments are a closed set for the
same reason: `--dry-runn` used to leave the writes switched on, and this job
holds installation tokens across two owners.

A branch that exists carrying *this exact lock* is not re-pushed but still falls
through to the PR step, because a run interrupted between the push and
`gh pr create` otherwise strands that branch forever — every later run would find
the same match and stop.

## Consequences

**Good.** ADR 0001's hold is lifted by the specific thing it named, and ADR
0004's ordering risk closes. The keys still belong to different people: the
registry decides what the bundles contain, each repo decides which bundles it
installs, and the fleet decides only *when to ask the generator*. A registry
commit that touches no bundle produces no pull requests anywhere — for the
federated adopters too, which is the case the anti-churn design is easiest to
get wrong on, because they are the ones a combined verdict would have churned
on every commit.

**Costs, honestly.**

- **One skill edit is up to ten pull requests, and nobody batches them.** That
  is the price of per-consumer locks, and it is the same shape `platform-bump.yml`
  already has. It is cheaper than deriving each diff by hand — which is what the
  four `adamdaniel.ai` re-pins cost — but it is not free, and it lands on
  whoever reviews.
- **A lock can sit on an old `ref` indefinitely, by design.** Anti-churn means
  currency is judged on content, so `agentskills@1e4fe9e` in the drift report's
  Notes column no longer distinguishes "current" from "nobody has looked" without
  running `--check-current`. We chose that over a nightly PR per repo, because a
  fleet that learns to ignore these PRs is worse than a fleet with old-looking
  refs.
- **The workflow has to name every registry a lock might federate.** It checks
  out `cms-platform` at full depth purely for two consumers' federated halves. A
  consumer that federates a third registry is **skipped with a warning** until
  the workflow is edited — never half-re-pinned — and that warning is only in the
  run log, which nobody reads on a green run.
- **Both registry checkouts need `fetch-depth: 0`.** `--repin` proves the
  checkout really is the registry by finding the commit the lock already pins,
  which a shallow clone does not contain, and the resulting error reads as "this
  is not that registry". The script refuses a shallow checkout up front and says
  so, because that message is a wrong-repo hunt otherwise.
- **A failure here is a scheduled failure**, so it surfaces through the
  `scheduled-run-health` tracking issue (ADR 0003's neighbour) rather than as a
  red PR — a slower loop than anything in this repo's CI.
- **The suite exercises a stand-in generator, not the real one.** `ci.yml`
  checks out this repo and nothing else, so the tests supply a faithful
  reimplementation of the flags, exit codes and `FAILED:`/`ERROR:` distinction
  the script branches on, computing real digests over real fixture content. It
  proves the orchestration — anti-churn, isolation, idempotence, non-de-federation
  — and proves nothing about whether those digests equal the real generator's.
  That belongs to `agentskills`' own suite.

**Left open, deliberately.**

- **`--repin` advances only the primary `ref`.** A federated source's own pin is
  still a human's job, and the PR body says so per PR. When only the federated
  half has moved, the run opens nothing and logs a line saying so — which nobody
  reads on a green run, so a federated pin can sit behind indefinitely with the
  log as its only signal. Making that visible (a drift-report column, an issue)
  is the obvious next thing and is deliberately not in this change.
- **Nothing merges these, and nothing notices one going stale.** There is no
  auto-merge by choice. The drift report prints the pin on each repo's **default**
  branch, so an unmerged bump PR is invisible there; ten adopters make an
  accumulating pile more likely than two did.
- **Closing a bump PR is not a way to say no.** The next run re-proposes the same
  change — re-opening the PR if the branch survives, re-pushing it if not. The
  only "no" is removing the repo's lock or taking the repo out of scope, and
  nothing in `repos.yml` expresses "bump-exempt" today. If that is ever wanted it
  should be a key there, not a closed PR anyone can mistake for a decision.
- **Open-PR detection sees open PRs only.** `gh pr list --head` is what both the
  leave-it-alone path and the stranded-branch repair key on, so a *closed* PR on a
  live branch reads as "no PR" and is re-opened. That is the same signal `sync.sh`
  uses, and the same limitation.
