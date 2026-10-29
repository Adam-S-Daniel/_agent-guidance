# 0027 — Lessons become agent-checks through the fleet worker, and their PRs auto-merge when green

**Status:** Accepted (2026-10-26)

## Context

Agents learn the same lessons again and again: a path that must never be
committed, a file that must change with another. Each lesson ended up as
guidance text or a skill, which an agent can skip or forget. The fleet
already has a better home for a rule a commit can check: the agent-checks
runner ([ADR 0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md)).
`templates/agent-checks/agent-checks` is canonical, `.githooks/agent-checks`
is a byte-identical copy, the sync delivers it to every fleet repo, and the
required `agent-checks` CI job enforces it. Adding a check was a hand edit
nobody was asked to make.

The owner decided on 2026-10-26:

- 14:00 UTC: build the closed loop from lesson to check with no human in it.
- 14:14 UTC: accepted loop changes auto-merge when green. Claude merges its
  own green loop PRs, except changes to how a plugin runs code.
- 14:21 UTC: a git hook is the first choice for any lesson an agent learns,
  ahead of a skill, and a skill ahead of guidance text.

Lessons arrive from three places: proposals in
`example-adopter/agentskills-private`, `hook-proposal` issues in
`example-adopter/skills-evals`, and the memory curation routine's
`Memory promotion <date>: <n> agent-checks` issues in this repo.

## Decision

1. **The fleet worker gets a third pass, the lesson-check pass**
   ([`fleet-worker.md`](../routines/fleet-worker.md)). For each checkable
   lesson it adds a `case` arm to the runner's `check_lessons` function,
   tagged `# lesson: <id>`, plus at least one refuse case and one near-miss
   pass case in `test/fixtures/agent-checks-cases.jsonl`, and records the
   lesson in [`docs/agent-checks-lessons.md`](../agent-checks-lessons.md).
2. **Gates, all in `scripts/agent_check_lane.py` or the test suite, all must
   pass before a PR is opened:**
   - `red-green`: every refuse case tagged with the lesson fails under the
     runner on `origin/main`, and every case in the table holds under the
     candidate.
   - `cases`: the full case table holds.
   - `TEST_GROUPS_ONLY=agent_checks ./test/run-tests.sh` passes.
   - `fleet-scan`: on a fresh clone of every repo in `cron_coverage.fleet`,
     the candidate prints no refusal on main that the base runner did not.
     Zero new refusals; a repo not reached stops the pass.
   - `scope`: the PR adds or modifies only the two runner copies, the case
     table and the ledger, deletes and renames nothing, and the two runner
     copies are byte-identical.
3. **The PR auto-merges.** The worker opens it ready for review and arms
   native auto-merge with a merge commit, so it lands when every required
   check is green. This is an exception to repo-settings ADR 0001's rule
   that bots do not merge through auto-merge; the exception is declared in
   [repo-settings ADR 0013](https://github.com/example-adopter/repo-settings/blob/main/docs/decisions/0013-lesson-check-prs-auto-merge-in-agent-guidance.md).
4. **The runner gives checks what they need:** a `tree [<rev>]` event that
   checks every path of a revision (what `fleet-scan` runs), and a
   `file_content <path>` helper that reads the content being checked (the
   index for pre-commit, the pushed commit for pre-push, the head for ci,
   the revision for tree). A refusal names the path and the rule, never the
   content, which could be a secret.

## Consequences

- Every fleet repo runs a new check on the next sync, so a wrong check
  blocks commits fleet-wide. That is why the bar is zero new refusals on
  every fleet repo's main, measured with the same runner the repos will get,
  and why a repo that could not be scanned stops the pass rather than
  counting as clean.
- `fleet-scan` measures only what is on main today. A check can still
  refuse a reasonable future commit. The near-miss pass case is the guard
  against an over-broad pattern; the remedy for a bad check is a revert PR.
- The `--no-verify` guard (`agent-checks-guard.sh`), the CI workflow and
  `sync.sh` are out of scope: `scope` refuses any change to them. A lesson
  that needs them goes to the owner.
- A lesson no commit-time check can decide is recorded `not checkable` and
  left to the skill lane.
- The fleet worker opens and auto-merges these PRs as the owner's account,
  not as a narrow GitHub App, so the gates and the `scope` allowlist are
  what keep a lesson PR small. A narrow App is not built yet.
- The ledger is public. It holds lesson ids and generic reasons only, never
  a private proposal's examples or subject.
