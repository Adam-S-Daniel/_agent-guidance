# 0051 — The fleet worker merges the kit's re-sync pull requests

**Status:** Accepted (2026-11-25). Widens
[0041](0041-the-fleet-worker-merges-reviewed-agent-made-changes.md) to one
more kind of pull request, in one repo.

## Context

The adoption kit, `acsi-kit`, follows this fleet's public repos through pull
requests that a GitHub App opens: the daily re-sync's `resync/auto`, and a
`resync-fix/<date>` branch that a cloud routine pushes when a source's new
head breaks the export and has to be followed by hand-written manifest or
patch changes.

On 2026-11-23 the fix routine opened
[acsi-kit#100](https://github.com/Adam-S-Daniel/acsi-kit/pull/100) with every
check green. Nothing merged it:

- 0041's rule merges a PR "authored by `Adam-S-Daniel`". These are authored
  by the App, so the rule never applied and they waited for a person.
- While a `resync-fix/` PR was open, the re-sync did not fire the routine
  again. The sources moved on 2026-11-24 and 2026-11-25, the open fix went
  stale, and the kit held two sources back for two days
  ([acsi-kit#105](https://github.com/Adam-S-Daniel/acsi-kit/issues/105),
  [#644](https://github.com/Adam-S-Daniel/_agent-guidance/issues/644)).

The kit's first outside adopter was setting up from it that week, so a kit
two days behind its sources was a kit missing two days of fixes.

A re-sync PR is not shaped like the changes 0041's review was written for.
Almost all of its diff is `starter/` and `library/`, generated from the
sources, far too large to read, and full of things 0041's review refuses on
sight (a source's hooks, workflows and merge rules arriving as copies).

## Decision

**The fleet worker's maintenance role merges the kit's re-sync pull requests
behind the same independent review as 0041, adapted to what a re-sync is**
(spec: the block between the `kit-only` markers in step 2d of
[`fleet-worker.md`](../routines/fleet-worker.md)). The owner chose this on
2026-11-25, together with the kit closing a stale fix PR and firing its
routine again.

1. **Which PRs.** In the kit's repo only, head `resync/auto` or
   `resync-fix/*` on a branch of that repo, authored by the re-sync App's
   bot account, labeled `agent-authored`, auto-merge not armed.
2. **The App and the repo are configuration.** `repos.yml` names them under
   `kit_resync`; the spec carries neither name. With the key missing or
   empty the worker merges none of these PRs, says so in its summary and
   sends them to the owner.
3. **Only files a re-sync writes.** Manifests, `starter/` and `docs/` for
   `resync/auto`; for a fix also patches, overlays, `library/`, the
   decisions log, the content catalog and the tests' text lists. A PR
   touching a workflow, the export's code, a test or a setup script goes to
   the owner.
4. **Nothing private.** A change to the private registry's manifest or its
   `starter/` tree goes to the owner: the kit's CI cannot read that source,
   so nothing proves those files, and moving that pin is his decision.
5. **Every moved pin is a commit its source merged**, checked against the
   source's default branch with the compare API, the way 0041 checks the
   hook pin.
6. **Everything else 0041 requires:** not a draft, no `on-hold`, a head at
   least an hour old, every check green with a required check and a passing
   test run, the eval-protection gate, a `CLEAN` verdict on the exact head
   from a subagent the same run started, and a `FINDINGS` stamp ending
   review for that head.
7. **The review reads what a person wrote, not what the export generated.**
   The kit's required `test` check proves each public `starter/` tree equals
   a fresh export of its pinned source, and item 5 shows each pin passed its
   own source's review. So 0041's refusals apply to the diff outside
   `starter/` and `library/`, and the reviewer also checks that manifest and
   patch changes still do what their `why` says, that no guard or list is
   loosened, that a fix records its departures, that the pages the PR lists
   for review are still true, that new content has a tier, and that nothing
   quotes the private source.

**The kit does not ship this rule.** An adopter's fleet has no kit, so the
kit's export removes the block and the `kit_resync` key from its copies.
That is why the rule lives in one marked block instead of being woven into
step 2d's items.

## Consequences

- A green re-sync or fix PR merges within one fleet worker interval after
  its head is an hour old, so the kit stays about a day behind its sources
  with no owner step.
- The generated trees are merged unread. What stands in for reading them:
  the export is deterministic, CI compares its output with the committed
  trees, the pins are commits the sources' own reviews merged, and the
  manifests and patches that shape the output are read. A defect in the
  export code itself is not caught by this review; changes to that code
  never merge under this rule.
- `library/` and the private tree are not covered by the kit's CI
  comparison. The private tree sends the PR to the owner; `library/` rests
  on the kit's bootstrap tests, which read it. That is weaker than the
  `starter/` proof.
- The kit is public and adopters clone it, so a wrong `CLEAN` reaches other
  people's setups, not only this fleet. One revert PR undoes a merge, but
  not a clone made in between. The owner accepted that on 2026-11-25.
- A fix PR that gets `FINDINGS` waits for the owner; if its sources move on,
  the kit closes it and fires the routine again, so a refused fix is
  replaced by a new one with a new head and a new review. A refusal cannot
  be re-rolled on the same head.
- The spec now has one block written for a repo most readers of the kit's
  copy will never have. The markers keep it out of their copy; a block that
  loses a marker would ship, and the kit's export fails on an unbalanced
  pair.
- This ADR widens what the worker may merge, so the PR that adds it is one
  0041's review refuses: it goes to the owner.

## Alternatives considered

- **Auto-merge armed by the kit's own workflow once checks pass.** Needs
  `allow_auto_merge` on the kit and a repo-settings ADR, and gives up the
  independent review: a fix routine that reads untrusted source text would
  merge its own work on green CI alone.
- **Keep the human merge and only remove the staleness** (close a stale fix
  and fire again). The kit does this too, but alone it still waits on a
  person every time a source breaks the export.
- **Treat the App's PRs as ordinary agent-made changes** by adding the bot
  to 0041's author list. The review would refuse every one of them for what
  the generated trees contain, or would have to be loosened for all repos.
- **Name the App in the spec**, as the hook pin bump names its bot. The spec
  is prose an agent follows, and the kit's export rewrites owner names in
  it; a name in `repos.yml` is one place to change and one place to check.

## References

- [#644](https://github.com/Adam-S-Daniel/_agent-guidance/issues/644): the
  root cause and the options put to the owner.
- [acsi-kit#105](https://github.com/Adam-S-Daniel/acsi-kit/issues/105),
  [acsi-kit#100](https://github.com/Adam-S-Daniel/acsi-kit/pull/100): the
  two days.
- The kit's `MAINTAINING.md` (Re-pin) and `export/FIX-ROUTINE.md`: what a
  re-sync and a fix change.
