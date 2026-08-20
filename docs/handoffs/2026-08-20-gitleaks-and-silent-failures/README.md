# Fanout set — paste `README.md` into one session, or run A/B/C in parallel

Produced 2026-08-19. Every factual claim here was measured; where something is
uncertain it says so.

**Re-derived 2026-08-20 against live data**, because the state moved under this
pack overnight: `C`'s gate conditions, the bare-hex lock list, and the issue
states below. Anything carrying a `2026-08-20` date is the re-derivation;
anything without one is as first written. `C-history-rewrite.md` was corrected
in the same pass. The verdict on `C` did not change — every reason behind it
did, which is the more dangerous kind of staleness: a session acting on the old
reasons would have spent its first cycle re-doing work that was already done and
then read a green condition 1 as a green gate.

## Files

| file | what | can run in parallel? |
|---|---|---|
| `SHARED-CONTEXT.md` | facts and traps every session needs | read first, always |
| `A-trigger-narrowing.md` | cut unnecessary CI work; one evidenced saving, four traps, two data-gated questions | yes |
| `B-detection-gaps.md` | make the remaining silent failures loud; includes adding `workflow_dispatch` where a manual run is meaningful (and where it is hazardous) | yes |
| `C-history-rewrite.md` | retire the gitleaks exclusions by rewriting history | **NO — gated** |

**C is gated — still, but no longer for the reason first written here.** The
`consumer-repo-provisioning` rename HAS now reached both sites' committed
`skills.lock`, so gate condition 1 passes. What blocks C is gate condition 3
plus two blockers the gate never listed: `refs/pull/*/head` keeps the offending
commit reachable on 117 + 24 pull requests and nobody can delete those refs, and
both `main` rulesets forbid the force-push outright with no bypass actor.
See "The blocking fact for `C`" below and C's own prerequisite gate, both
re-derived against live data on 2026-08-20.

A and B are independent of each other and of C's gate.

---

## The paste-once prompt

> Read `SHARED-CONTEXT.md`, then work `A-trigger-narrowing.md` and
> `B-detection-gaps.md`. Do not start `C-history-rewrite.md` until its
> prerequisite gate passes — check the gate, and if it does not pass, say so and
> leave C alone.
>
> Use workflows and adversarial review throughout. **Handle emergent issues
> recursively**: if fixing X reveals Y, fix Y in the same effort, and if Y
> reveals Z, keep going — do not defer findings to a list.
>
> Take everything through to merge yourself. Where a change touches a
> cms-platform reusable, **cut the release and confirm the consumer bumps
> landed** — a change that has not propagated has not been verified, and a theory
> that needs it live has not been tested.
>
> Verify every claim you rely on, including the ones in these files and the ones
> subagents report back. Report parsed CI conclusions, never "the watch
> finished". A count that disagrees with what a spec expects is a stop-and-report,
> not a rounding difference.
>
> Say which GitHub connector you used for each verification.

---

## State at handoff (verified 2026-08-20T00:35Z, not recalled)

**Everything from the originating session is MERGED.** 13 PRs across six repos:
the gitleaks regression fix on both sites (push-to-main verified green, ending 8
consecutive failures on adamdaniel.ai), the `consumer-repo-provisioning` rename,
the default-branch push lane, the Dependabot cooldown fix + lint, the AGENTS.md
structural guard + `sync.sh` hardening, the naming-pitfall guidance, the
skills-evals digest-label fix, and the expression-lint fix.

**`v0.1.86` is cut and both consumers are bumped** — jodidaniel.com#153 and
adamdaniel.ai#3220 both merged. Note `platform-bump` arms auto-merge itself at
release time, with method **SQUASH** (legitimate on the three cms-platform-managed
repos); do not assume a bump PR needs manual arming.

**Filed and still open by design:** cms-platform#279 (the push-lane blind spot),
agentskills#87 (see `B-detection-gaps.md` §B3 for what must be true first).

### The blocking fact for `C` — re-derived 2026-08-20, and it MOVED

The federated advance this section used to ask for **has been done.** Both
consumers' `skills.lock` on `main` (read through `mcp__github__`
`get_file_contents` at `refs/heads/main`; both files are the same blob,
`a83236d4`):

| | |
|---|---|
| contains `cms-platform/cms-platform-secrets` (OLD) | **NO** |
| contains `cms-platform/consumer-repo-provisioning` (NEW) | **YES** |
| `sources[0].ref` (federated cms-platform pin) | `a59763c6` — **post-rename** |
| top-level `ref` (agentskills primary pin) | `42d1b929` |
| the pack's own keyword check, run on both | exit **0**, no keyword-bearing keys |

**So gate condition 1 PASSES.** Do not spend a cycle re-doing the federated
advance, and do not read a green condition 1 as "the gate is met" — conditions
2 and 3 are separate questions and only one of them passes.

**Condition 2 PASSES**: nothing in the fleet pins a commit of either site. No
`skills.lock` or `platform.lock` names them, no `uses:` references either as a
workflow or action source, no `platform_ref:` input names them. (Method: grep
over every tracked lock and workflow in the nine local clones. Neither site is
a registry or publishes a reusable, so there is nothing for a lock to pin.)

**Condition 3 FAILS, and it is not a wait — it is structural.** Measured
2026-08-20 by fetching `refs/pull/*/head` into throwaway bare blobless clones
and asking `git merge-base --is-ancestor` per ref:

| | adamdaniel.ai | jodidaniel.com |
|---|---|---|
| offending commit (the `.gitleaksignore` fingerprint) | `39d92503` | `fee19ee4` |
| PR refs total | 3197 | 148 |
| PR refs **containing** it | **117** | **24** |
| of those, still OPEN | **1** (#3198) | **0** |
| branches containing it | `main` **+ 1** (below) | `main` **+ 1** (below) |
| tags containing it | 0 | 0 |

**Correction, same day: `main` is NOT the only branch.** An earlier revision of
this table said it was, on both repos. Re-measured by enumerating `git ls-remote
--heads <origin>` and testing `merge-base --is-ancestor` against every head,
there is one more per repo — and each one's TIP TREE still carries the offending
line, so these are not merely reachability, they are the live line:

| repo | branch | tip `skills.lock` offending lines |
|---|---|---|
| adamdaniel.ai | `cms/posts/delete-5a7734ca-1787068075722` | 1 |
| jodidaniel.com | `claude/fix-history-secrets-scan` | 1 |

Unlike the PR refs these ARE deletable, so they belong in C's step 2 rather than
in this blocker. The miss is worth naming because of HOW it happened: the counts
above came from **bare blobless clones fetched for `refs/pull/*`**, and a clone
narrowed to the refs you asked for shows you the branches you asked for. That is
the same failure mode as the shallow-clone trap in `C-history-rewrite.md`
("Unshallow first, or the verification lies to you") — a narrowed view
under-reporting in the direction of "all clear". Enumerate branches from
`ls-remote` against the origin, never from a clone you shaped yourself.

The earlier count in these files (112 / 20) was not wrong, it was **earlier** —
every new PR branched off `main` adds another permanent ref, so this number only
grows. Deferring C makes C harder, monotonically.

**Closing a pull request does not delete `refs/pull/N/head`.** 116 of
adamdaniel.ai's 117 and all 24 of jodidaniel.com's are already closed or merged,
and their refs are still live — verified directly with `git ls-remote` against
each origin on 2026-08-20 (`refs/pull/{3111,3150,3191}/head` and
`refs/pull/{134,149,158}/head` all resolve). GitHub owns that namespace and a
client cannot write or delete into it — stated from documented behaviour, NOT
measured here, since measuring it would be a write; the 140 already-closed refs
that are still live are the same conclusion by observation. So C's step
"Delete every stale ref found in gate step 3" is
**unperformable for PR refs**, and while they exist the old commits stay
*reachable* — which means GitHub's own GC will never collect them either. The
inventory that condition 3 asks for is above; what does not exist is a way to
act on it.

Do not read "agentskills#87 is open" as "the labelling never landed". It landed;
the issue is simply not closed. Re-verified 2026-08-20: still `open`, as is
cms-platform#279.

### Verified side effect worth knowing

`skills-evals` now has **3 open Dependabot PRs** and `GHA-bench` **1** — those
repos had produced **zero** before the cooldown fix. That is the fix confirmed
working, not merely plausible.

### The bare-hex locks — all healed 2026-08-20, by hand, pins preserved

This section used to say `cms-platform`, `GHA-bench` and `_agent-guidance` still
carried bare-hex digests. Re-derived 2026-08-20 across every fleet `skills.lock`
on `main`: **zero bare digests anywhere.** Five repos — `_agent-guidance`,
`cms-platform`, `GHA-bench`, `repo-settings`, `claude-memory-map` — are labelled
and *still pinned at `94cdcc81`*, which is the point: the repair was a RELABEL
and it moved nobody's pin. (`skills-evals` has no `skills.lock` on `main` at
all; `agentskills`' own lock is at `f92569e2`.)

That the heals were done by hand is the finding, not a footnote. The nightly
`bump-consumer-locks.sh` would have repaired the same shape by re-pinning
WITHOUT `--ref`, moving all eight pins to whatever commit its registry checkout
was sitting on — a fleet-wide content advance wearing a shape repair's PR body.
`B-detection-gaps.md` §B3's "verify this self-heals rather than assume it" was
the right instruction and the verification is what found it. The bumper's format
branch now passes `--ref <the lock's own pin>`; see
`scripts/bump-consumer-locks.sh`, "A SHAPE repair is never a CONTENT advance".

### Two open PRs from elsewhere that may overlap this work

- `_agent-guidance` **#45** — "a GitHub 404 means 'not authorized', not 'not
  there'". This overlaps `SHARED-CONTEXT.md` §1's closing point. Read it before
  editing that section; reconcile rather than duplicating.
- `GHA-bench` **#44** — "Run the benchmark on Claude Code on the web". That repo's
  AGENTS.md names an unresolved hazard in it: `runner.py` inherits the launching
  session's `$HOME/.claude/skills`, which a SessionStart hook may have populated,
  making the skill set a silent between-run variable no `metrics.json` records.
  Do not merge it leaving that unaddressed.

## Three corrections carried forward, so they are not re-learned

- **`setup.sh` is not involved in the skill rename.** It walks only
  `plugins/*/skills/*/SKILL.md` inside agentskills; cms-platform's bundle is
  federated live from its own repo. The consequence is a plugin-cache refresh,
  not a fleet-wide `setup.sh` re-run.
- **`uses:@<sha>` action pins were never at gitleaks risk.** `@` is not a
  separator in `generic-api-key`. A sweep of the real regex plus entropy gate
  across every tracked file in seven repos found **zero** hex matches outside the
  one known lock line — so a general "prefix every SHA" policy would solve a
  problem that does not exist.
- **A federated skill rename does not reach consumer locks by itself.** Neither
  the nightly `--repin` nor `platform-bump` advances a lock's federated
  `sources[].ref`. That manual step is prerequisite #1 of `C`, and it was missed
  in the first draft of these files. It has since been DONE — both locks pin
  `a59763c6` — so the lesson stands and the task does not.
- **Cutting a release needs a version-bump PR first** — `release.yml` refuses to
  tag a tree that does not already declare the version. Five files move together.
- **`.gitleaksignore` must not propagate and cannot be shared.** A fingerprint is
  `<commit>:<file>:<rule>:<line>` with repo-unique SHAs. Only 3 of 8 repos run
  gitleaks at all, and only the 2 sites can ever produce this finding.
