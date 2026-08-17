# 0002 — Unconditional rules live in the managed guidance, not in a skill

**Status:** Accepted (2026-08-17)

## Context

`agents-md/base.md` opens by declaring itself deliberately short: it carries
what is *specific to this account and learned the hard way*, and explicitly
"does not restate general engineering practice" because "depth lives in each
repo's `docs/` and in the skills registry". Under that rule, a procedure for
hardening GitHub Actions is obviously a skill, and that is where it lived — the
managed block carried a one-line pointer to it and nothing else.

That arrangement quietly failed, for two independent reasons.

**1. A skill only applies in a session that loaded it, and most sessions don't.**
ADR 0001's premise is unchanged: cloud and other ephemeral surfaces get no
plugins from repo-declared settings, so a skill reaches them only through the
`skills-bootstrap` hook — which is opt-in and double-keyed (allowlisted in
`repos.yml` **and** the repo carrying its own `skills.lock`). Today exactly two
repos hold both keys. The fleet is ~20 repos. So for ~18 of them the rule was
reachable only on a developer's own machine with the marketplace installed.

**2. Even where it loads, a skill fires on recognition — and non-recognition is
the failure mode.** A skill is invoked when the agent notices the situation the
skill covers. Writing `uses: some/action@v4` does not feel like a security
decision; it feels like writing a line of YAML. An agent that does not already
know the pin rule exists has no reason to reach for the thing that states it.
Guidance that is always in context does not depend on that recognition.

The pointer in the managed block did not close the gap. It named the skill in
all ~20 repos while most of those sessions could not load it — ADR 0001 already
records this as the fleet "shipping references to skills most sessions cannot
load", and it recorded it as a cost to be disclosed rather than fixed.

A third fact surfaced while fixing the first two. `sync.sh` excludes its own
repo (`SYNC_SELF_REPO`), so `_agent-guidance` had never had an `AGENTS.md` or a
`CLAUDE.md` bridge at all. The repo where the guidance is *written* was the one
repo in the fleet whose agents could not *read* it.

## Decision

**Split by conditionality, not by topic.**

- A **rule that must hold every time**, whose failure mode is not noticing it
  applies, belongs in the managed guidance. It is always in context, in every
  repo, on every surface, at the cost of tokens.
- A **procedure you reach for once you know you need it** belongs in a skill.
  Skills stay the right home for depth.

Applying that split, the SHA-pinning rule moved into `base.md` as its own
section, and the skill that used to carry it was retired rather than left as a
second, divergable copy. What moved is the part that is *policy* — full
40-character SHA, the dated version comment, the 7-day cooling-off, and the
annotated-tag dereference that silently produces a runtime failure if you get it
wrong. What was dropped is the part that is *recoverable procedure*: the
step-by-step walkthrough for enumerating workflow files and resolving each ref,
which any agent with `gh` can reconstruct once it knows the target shape.

Two properties keep this honest and checkable:

- The rule is enforced in code as well as stated in prose:
  `sha_pinning_required: true` is set on every repo by `repo-settings`'
  `fleet.yml` and by `cms-platform`'s `repo-settings.yml`. Guidance that only
  exhorts would rot; guidance that describes an enforced setting cannot drift
  far without something going red.
- **This repo now commits its own rendered `AGENTS.md` and a `CLAUDE.md`
  bridge**, regenerated from `base.md` and diffed by CI. The alternative —
  dropping the `SYNC_SELF_REPO` exclusion so the sync writes here too — was
  rejected: it would deliver the guidance asynchronously *after* merge, leaving
  the repo blind during exactly the PR that changes the guidance. Committing the
  rendered output also means a `base.md` PR shows reviewers the precise text
  ~20 repos are about to receive, in the same diff.

## Consequences

- **Every session in every repo now pays for this in context, forever.** ~30
  lines × ~20 repos, loaded whether or not the session ever touches a workflow
  file. That is the real price, and it is why this is an ADR rather than a
  commit: the same argument ("it's important, put it in base.md") applies to
  almost anything, and if it is accepted twice more the block stops being short
  enough to be read. The bar to clear is the split above — *unconditional rule,
  failure mode is non-recognition, enforced somewhere in code* — and a proposal
  that cannot clear all three stays a skill.
- **We lost a batch-audit procedure.** Sweeping every workflow in a repo and
  fixing each ref used to be a documented sequence; it is now something an agent
  constructs from the stated target shape. Acceptable: the rule is what prevents
  the bad state, the sweep only cleans it up, and Dependabot already re-pins on
  the same cadence.
- **`base.md` now contains a fenced code block**, which no section did before.
  Harmless in the consumers (it is markdown inside markdown), but worth knowing
  before anyone writes a parser over the managed block.
- **The bridge is no longer hypothetical here.** `_agent-guidance` previously
  relied on whoever was editing it already knowing the fleet's conventions. That
  worked because it was usually the same person; it was never true for an agent.
- **The self-guidance CI check duplicates `sync.sh`'s composition** (managed
  content, then the marker line and everything below it, then one newline). If
  that composition ever changes, two places must change. The alternative was
  exporting the composition into a shared script used by both, which is more
  machinery than a three-line `printf` justifies — but the duplication is real
  and the CI step carries a comment saying so.
