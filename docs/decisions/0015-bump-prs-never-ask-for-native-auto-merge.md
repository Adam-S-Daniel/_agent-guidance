# 0015 — Bump pull requests never ask for native auto-merge

**Status:** Accepted (2026-09-22). Supersedes one decision in
[0006](0006-bump-prs-land-on-a-sweep.md) — "A newly opened PR still asks for
native auto-merge" — and corrects that ADR's Context claim that GitHub "does
not merge the PR instead. It declines to arm, and the PR sits." Everything
else in 0006 stands unchanged, and this ADR depends on it.

## Context

**The claim 0006 made, and why it was wrong.** 0006 kept a `gh pr merge --auto
--merge` call immediately after `gh pr create` in the propose pass, on the
strength of one sentence: with `required_status_checks: []` on this fleet's
default-branch rulesets, "GitHub refuses to arm auto-merge at all — it errors
that the PR is already in a clean/mergeable state, because there is nothing for
auto-merge to hold the merge FOR" — and then: "Note what the refusal is *not*
[...]: GitHub does not merge the PR instead. It declines to arm, and the PR
sits."

That second sentence is the bug. `gh`'s own source settles it:
`autoMerge: opts.AutoMergeEnable && !isImmediatelyMergeable(pr.MergeStateStatus)`
(cli/cli `pkg/cmd/pr/merge/merge.go`) — auto-merge is armed only when the PR is
**not** already mergeable, and `isImmediatelyMergeable` answers true for
`CLEAN`, `HAS_HOOKS` and `UNSTABLE`. A PR whose checks have not concluded yet is
`UNSTABLE` (or `CLEAN` before any check has reported), so on a repo with
nothing required, `gh pr merge --auto` does not error and does not sit — it
merges the PR immediately, the very call `isImmediatelyMergeable` would
otherwise have skipped over in order to arm. "Refuses to arm" was read as
"refuses to merge"; the two are opposite failure modes, and the comment took
the friendlier one on faith.

**Measured, nine times, across three repos.** Nine bump pull requests merged
3-15 seconds after `gh pr create`, before CI had concluded on any of them:
claude-memory-map #27, #28, #29; `_agent-guidance` #68, #87, #104; GHA-bench
#62, #64, #65 (full table in #141). Every one of them landed by the exact
mechanism 0006 said could not happen.

**The same defect, in six copies of a workflow.** The inline
`dependabot-auto-merge.yml` carried by six fleet repos — this one,
claude-memory-map, skills-evals, fastmail-actions, GHA-bench and one private
repo — ran `gh pr merge --auto --merge` on every Dependabot pull request the
moment it opened, under a header making the identical claim. That is
[cms-platform#437](https://github.com/Adam-S-Daniel/cms-platform/issues/437),
corrected by removing the attempt outright, so that the scheduled sweep which
already waited for every check became the only merge path. This ADR makes the
same correction here, for the same reason: re-deriving when `gh` arms
auto-merge from a comment, rather than from the one conditional that decides
it, is how the same bug ships twice.

**Native auto-merge would in any case be a weaker gate than the sweep this
repo already runs.** 0006 established that GitHub's auto-merge holds a PR only
for checks a ruleset marks **required**, while `pr_merge_verdict` (called from
the sweep, `sweep_bump_prs`) reads the whole `statusCheckRollup` and blocks the
merge on *any* check a repo reports, required or not. So even on a repo that
later grows a required check, arming native auto-merge there would not have
waited for anything the sweep does not already wait for — the "starts working
for free" benefit 0006 credited the attempt with does not exist.

## Decision

**The propose pass makes no merge call of any kind for the PR it just
opened.** `bump-consumer-locks.sh` creates the pull request and stops; nothing
after `gh pr create` calls `gh pr merge`, `--auto` or otherwise. The bumper's
own sweep (`sweep_bump_prs`, gated by `pr_merge_verdict`) is the only path by
which a bump PR is ever merged — the same path 0006 already designed for every
PR a *previous* run had opened. This closes the one gap 0006 left open: a PR
that could merge itself before the sweep, the run that opened it, ever saw it.

## Consequences

**A bump PR now waits one full cycle before it can land, on every repo —
not only the ones a native-auto-merge arming would have missed.** A PR opened
tonight lands at the earliest on tomorrow night's sweep. 0006's own design
already assumed this was the window a consumer's CI gets ("the gap between two
nightly runs is what gives a consumer's CI a full day to report"); removing
the attempt makes that the *only* window rather than a fallback for the repos
where arming happened to fail. Nothing is lost on a repo that later adds a
required check: per the Context section above, native auto-merge there would
have waited only for the required subset, and the sweep already waits for
more.

**The test suite's `gh` mock encoded the same false belief, and is corrected
alongside the script.** `test/run-tests.sh`'s `merge)` branch treated `--auto`
as pure arming with no merge side effect, and `MOCK_AUTO_MERGE_FAILS`
simulated "GitHub refuses to arm" with no case for "GitHub merges it instead"
ever existing to be simulated. A suite built on that belief could not have
caught the regression this ADR describes; the corrected suite instead asserts
that the propose pass makes no merge call at all, and that every merge in a
run carries `--match-head-commit` — the sweep's own signature.

**`sync.sh`'s `gh pr merge --auto` in its protected-branch fallback is
deliberately left in place.** That call sits behind a genuine behavioural
difference from the bump scripts: the fallback PR stands in for the direct
push `sync.sh` makes to every repo whose branch protection allows it, and that
direct push is gated by no check either. An immediate merge there loses
nothing a direct push would already have skipped past. The reasoning is
specific to a bot PR standing in for an already-ungated write, and does not
extend to `bump-consumer-locks.sh`, whose PRs are the only place this fleet
lets a consumer's own CI object to what a re-pin changed.
