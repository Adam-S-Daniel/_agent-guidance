# 0009 — A federated pin advances, and only on a question scoped to that source

**Status:** Accepted (2026-08-22). Supersedes one decision in
[0005](0005-consumer-locks-are-re-pinned-from-here.md) — the first *Left open*
bullet, "`--repin` advances only the primary `ref`. A federated source's own
pin is still a human's job" — and the sentence in its *Decision* section that
says the combined question is asked "only to log that a federated source has
moved; advancing that pin is a human's job". Everything else in 0005 stands,
including the anti-churn rule this ADR is careful not to weaken, and this ADR
depends on most of it.

## Context

**What 0005 left open, and what it cost.** 0005 shipped a bumper that could
advance a consumer lock's primary `ref` and nothing else. A lock that federates
a second registry — today `cms-platform` — carried that source's pin forward by
inheritance, forever, because the generator had no way to move one. The bumper
noticed when a source had moved and wrote a line about it:

> When only the federated half has moved, the run opens nothing and logs a line
> saying so — which nobody reads on a green run, so a federated pin can sit
> behind indefinitely with the log as its only signal.

**The generator half now exists.** `agentskills` adds two flags:
`--check-current --only <REGISTRY>`, which scopes the currency question to one
registry the lock plans, and `--repin --repin-source '<REGISTRY>@[<ref>]'`,
which merges one source's pin into the inherited `sources` array by registry
key. The second is deliberately not `--source`: `--source` *replaces* the
array, which is ADR 0001's de-federation trap, and stays an argparse error
alongside `--repin` forever.

**The obvious way to wire that up is catastrophic, and it was measured rather
than argued.** The tempting shape is: run `--check-current` once over the whole
lock, and if it fails, the federated half has moved. It does not follow.
`check_current` returns one flat list of differences across every source and
`main` prints ONE headline anchored on the lock's top-level `ref`. Built as a
real two-source scenario (a primary plus two federated registries, all real git
repos) and run three ways:

- **only the primary edited, both sources exactly at their pins** — exit 1,
  one `FAILED:` naming the PRIMARY's sha, and one detail line, `changed:
  'adam/alpha' differs from its content at <primary sha>`. Zero federated
  differences, and a combined verdict that says `FAILED` all the same.
- **only a source edited** — the same headline, still naming the primary's
  clean sha, with the only true fact in a detail line below it.
- **all three drifted** — still exactly one headline.

So a gate keyed on the combined verdict reads "the primary moved" as "the
federated half moved". Every consumer lock in the fleet would have every
federated pin advanced to that source's HEAD on any night the primary registry
had a bundle-touching commit — which is the routine case the bumper exists to
handle. That is not churn; it is an unasked-for advance of a pin 0005 reserved
for a human, fanned out across the fleet, in a diff whose visible content is
digests.

The argument against it was already written down in this repo, one paragraph
above the code that would have done it. 0005's *Decision* says of the primary
half: "Handing the generator a copy of the lock with `sources` removed asks a
*different question* rather than reinterpreting the combined answer, so it
cannot drift with the generator's wording."

## Decision

**The bumper advances a federated source's pin, and it decides which sources to
advance by asking one scoped question per source — never by reinterpreting a
combined verdict.**

For each registry in the lock's `sources`:

```
generate_skills_lock.py --check-current --only <that registry> --repo <primary checkout> \
    --source-repo ... -o <this run's copy of the full lock>
```

A registry is appended to the advance list only when **its own** run prints
`^FAILED:`. Then, and only then, `--repin` is invoked with
`--repin-source '<that registry>@'` for exactly those registries. Drift
attribution is a property of **which question was asked**, never of what the
answer said — the same discipline 0005 applied to the primary half, made a
first-class primitive instead of a shell-side lock rewrite.

The existing else-fail discipline is kept verbatim: a non-zero exit **without**
`^FAILED:` is an ERROR the script refuses to act on. A bad `--only` value lands
on exactly that path, because the generator raises for it and exits 1 with an
`ERROR:` line and no verdict, so the refusal composes with a guard that was
already there.

**Both new flags are SOFT-probed.** The bumper resolves its generator from a
checkout of the registry's **default branch**, and these flags arrive there in
their own pull request. A hard probe — the shape the load-bearing `--repin`
probe uses — would ground every nightly run in the window between that merge
and this one, a fleet-wide outage produced by the ordering of two green PRs.
A shortfall is a `::warning::` and a degrade to 0005's behaviour: the federated
half is reported and acted on by nothing. The two flags are probed as one
capability, because a scoped question with no way to act on it is that same
report-only behaviour, and acting without the scoped question is the failure
above.

**The primary's pin is HELD when only a source moved.** `--repin` is given
`--ref <the lock's own ref>` whenever the reason is anything but primary content
drift — the same anchor 0008's neighbour, the shape gate, already uses, and for
the same reason: advancing a source is not a primary content advance, and the
PR body must be able to reproduce its own diff.

**A cross-registry failure reached by an advance is a red nightly, not a
retry.** Advancing a source re-derives that source's digests at a ref that
MOVED, which makes two refusals reachable that could not fire before — the
generator's cross-registry basename collision, and the bumper's own
`skills_shrink_reason` guard on a renamed or emptied bundle. Both already count
a per-repo failure and leave the lock alone, and that is the right answer: a
basename collision between two registries is an adjudication *between* them,
and neither this script nor a retry can make it. The alternative considered and
rejected was for the bumper to drop that source's `--repin-source` and re-run,
which would land a green PR that silently declines the advance it was opened to
make.

## Consequences

**Good.** 0005's first *Left open* bullet closes with the mechanism it asked
for. A federated adopter's pin now moves on the same nightly cadence as its
primary, on the same evidence — the source's own bundle content — and lands as
a reviewable PR whose body lists each source old → new or states plainly that it
did not move. The anti-churn property is untouched: a source whose bundles have
not moved produces nothing, no matter how far its ref sits behind its HEAD.

**Costs, honestly.**

- **N sources means N+1 generator invocations per consumer, per night.** Each
  scoped run does a `git archive` of that source's bundles. Locks federate one
  source today and the cap is eight, so this is bounded and small — but it is
  strictly more work than the one combined call it replaces, and that call is
  precisely the cheap wrong answer.
- **A scoped run asserts nothing about the sources it did not select.** The
  generator's bundle-uniqueness check still runs, but over the selected set
  alone, so a conflict between two un-selected sources goes unreported by the
  gate. Correct for a scoped question and wrong to lean on: `--check` and the
  bootstrap hook remain the place a lock's whole-document validity is
  established.
- **`--check-current` compares against a WORKING TREE.** In the nightly that is
  a fresh `fetch-depth: 0` clone, so the answer is a true staleness check. Run
  by hand against a stale sibling clone, a scoped question will confidently
  report that source current — and a pin that silently never advances is the
  quiet failure. Unchanged by this ADR, but reaching one source at a time is
  when a single stale sibling stops being obvious.
- **Two newly reachable red nights**, per the decision above. Neither is
  reachable today; both become reachable the first time a pin advances.
- **The suite still exercises a stand-in generator.** The stub in
  `test/run-tests.sh` grew both flags, their refusals and the per-source
  `FAILED:` block in the same commit as the assertions that depend on them —
  because an assertion written against a stub that lacks the flag is green
  against something that cannot fail. It proves the orchestration and proves
  nothing about the real generator's bytes, exactly as 0005 recorded.

**Left open, deliberately.**

- **`repos.yml` still cannot say "hold this source back".** If a consumer ever
  needs a federated pin deliberately frozen, the cheapest home is a
  `bump_exempt_sources:` key there — fleet policy belongs in the fleet's config,
  never in a generator flag. Nothing expresses it today, and the five consumer
  locks this session could not read were not checked for one.
- **The primary half still scopes itself by rewriting the lock.** The
  `primary_only` helper writes a copy with `sources` removed, which
  `--check-current --only <the primary>` now does through the generator
  instead. Replacing it would make the two halves symmetric and remove a
  lock-mutating shell hack; it is not done here, so that a regression in the
  gate and a regression in the refactor stay separately bisectable.
