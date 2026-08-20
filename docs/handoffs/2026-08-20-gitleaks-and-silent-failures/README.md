# Fanout set — paste `README.md` into one session, or run A/B/C in parallel

Produced 2026-08-19. Every factual claim here was measured; where something is
uncertain it says so.

## Files

| file | what | can run in parallel? |
|---|---|---|
| `SHARED-CONTEXT.md` | facts and traps every session needs | read first, always |
| `A-trigger-narrowing.md` | cut unnecessary CI work; one evidenced saving, four traps, two data-gated questions | yes |
| `B-detection-gaps.md` | make the remaining silent failures loud; includes adding `workflow_dispatch` where a manual run is meaningful (and where it is hazardous) | yes |
| `C-history-rewrite.md` | retire the gitleaks exclusions by rewriting history | **NO — gated** |

**C is gated.** It must not start until the `consumer-repo-provisioning` rename
has reached both consumer sites' committed `skills.lock` (merge → release →
consumer bump). Running it earlier just bakes a fresh keyword-bearing commit into
the freshly rewritten history. Its own prerequisite gate spells out the checks.

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

### The blocking fact for `C`

Both consumers' `skills.lock` on `main`:

| | |
|---|---|
| contains `cms-platform/cms-platform-secrets` (OLD) | **YES** |
| contains `cms-platform/consumer-repo-provisioning` (NEW) | **NO** |
| `sources[0].ref` (federated cms-platform pin) | `3264e159` — pre-rename |
| top-level `ref` (agentskills primary pin) | `42d1b929` |

So **`C` is still gated.** The digests ARE `sha256:`-labelled — that half landed —
but the federated pin has not moved, and neither `platform-bump` nor the nightly
`--repin` will ever move it. That manual advance is the first thing to do.

Do not read "agentskills#87 is open" as "the labelling never landed". It landed;
the issue is simply not closed.

### Verified side effect worth knowing

`skills-evals` now has **3 open Dependabot PRs** and `GHA-bench` **1** — those
repos had produced **zero** before the cooldown fix. That is the fix confirmed
working, not merely plausible.

### Three more locks still bare-hex

`cms-platform`, `GHA-bench` and `_agent-guidance` all carry bare-hex digests and
all pin agentskills at `94cdcc81` — an OLDER ref than the consumers' `42d1b929`,
i.e. predating the labelling commit. The nightly bumper has not advanced them.
`B-detection-gaps.md` §B3 says to verify this self-heals rather than assume it.

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
  in the first draft of these files.
- **Cutting a release needs a version-bump PR first** — `release.yml` refuses to
  tag a tree that does not already declare the version. Five files move together.
- **`.gitleaksignore` must not propagate and cannot be shared.** A fingerprint is
  `<commit>:<file>:<rule>:<line>` with repo-unique SHAs. Only 3 of 8 repos run
  gitleaks at all, and only the 2 sites can ever produce this finding.
