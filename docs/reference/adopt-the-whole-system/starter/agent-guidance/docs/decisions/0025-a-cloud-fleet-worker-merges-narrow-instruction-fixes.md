# 0025 — A cloud fleet worker merges narrow instruction fixes and tells the owner about the rest

**Status:** Accepted (2026-10-25)

## Context

The prompt-audit sweep ([ADR 0017](0017-prompt-audit-runs-as-an-event-triggered-sweep.md))
files issues, a worker turns them into fix PRs, and the owner merges. On
2026-10-25 that chain had stalled for 17 days:

- The 2026-10-08 sweep filed 9 issues, and the laptop issue worker opened a
  fix PR for 7 of them that morning. One was merged.
- The laptop worker, a Claude Desktop task, runs only while the laptop is
  awake and the app is open. It had not run since about 2026-10-09.
- The sweep skips a repo while an earlier sweep issue is open there, so the
  2026-10-21 and 2026-10-23 sweeps filed 2 issues and 0 issues.
- `agent-checks` became a required check
  ([ADR 0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md))
  after four of those PRs were opened. It never ran on them, so they sat
  blocked with nothing red to fix until someone merged `main` in by hand.
- Nothing told the owner that any of these PRs needed him.

The owner reviewed this on 2026-10-25 and approved five changes, a job that
brings open PRs up to date when a required check is added, and an alert when
a PR needs his approval.

## Decision

1. **The sweep's run log moves to `persistent/prompt-audit-runs`**, a branch
   nothing merges, and the sweep opens no PR. An open earlier sweep issue
   gets a comment with the new findings instead of deferring the repo. A
   trigger issue whose "To check" list has hands-on items stays open and
   gets `agent-ready`. Details: [`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md).
2. **The issue worker runs in the cloud**, as the issue pass of a scheduled
   task, `Fleet worker`, every 2 hours
   ([`fleet-worker.md`](../routines/fleet-worker.md)). The laptop task is
   turned off.
3. **The fleet worker may merge one kind of PR:** a fix for a prompt-audit
   sweep issue that changes only `CLAUDE.md` files, the repo-specific half
   of `AGENTS.md` files, and new files under `docs/evidence/`, after every
   check is green and a separately prompted subagent, given only the issue,
   the diff and the repo's `AGENTS.md`, has stamped the exact head
   `CLEAN`. Everything else, skills and anything about how plugins run
   included, stays with the owner. This makes permanent, for that one kind
   of change, the merge permission the owner gave for the week ending
   2026-10-26.
4. **The fleet worker brings a PR up to date** (the REST `update-branch`
   call, a merge commit) when a required check has never run on its head and
   the PR is behind its base. Dependabot PRs are left to Dependabot.
5. **The fleet worker tells the owner, once per PR,** when a PR waits on his
   approval of an environment deployment or is green and ready for a merge
   only he may make. It labels the PR `needs-owner` and leads its final
   message, which push notifications deliver, with `Needs you:`. It never
   approves a deployment or submits an APPROVE review.

## Consequences

- A merged sweep fix no longer waits on the owner, and a blocked PR no
  longer waits for someone to notice a check that never ran. Each sweep
  audits every repo again, so a fix that misses something is caught by the
  next sweep.
- The merge rule rests on an LLM review. A wrong `CLEAN` lands a bad edit
  to a repo's own instructions. That is the cost the owner accepted: the
  edit is small, Markdown only, reverted with one PR, and outside the
  managed block the sync writes to every repo.
- The run log on `main` stops at the 2026-10-23 run. The full log is on
  `persistent/prompt-audit-runs`, guarded from deletion by the
  `persistent/**` ruleset (repo-settings ADR 0007).
- One scheduled task every 2 hours costs a cloud session per fire, even
  when there is nothing to do.
- Alerts depend on the scheduled task's push notifications. If those stop,
  the `needs-owner` label is still on each PR, so a GitHub search for it
  shows what waits on the owner.
- The scheduled task is created from a project thread, so agents can read,
  edit and fire it, unlike the sweep, which was created through the API.
