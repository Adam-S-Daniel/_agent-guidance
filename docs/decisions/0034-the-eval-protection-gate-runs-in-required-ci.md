# 0034 — The eval-protection gate runs in required CI

**Status:** Accepted (2026-11-01). Extends [ADR 0033](0033-audits-leave-eval-protected-subjects-alone-except-for-correctness.md).

## Context

ADR 0033 had the prompt-audit sweep and the fleet worker leave eval-protected
subjects alone except for correctness fixes. It named two gaps. First, a PR
merged by hand, such as one from a project thread, never passes through the
fleet worker; `Adam-S-Daniel/adam-agentskills#96` landed that way and reset
`writing-adrs`. Second, private-registry skills are not in the public
explorer, so the gate could not see them. The owner asked for both gaps to
be closed with a check, not a written rule.

## Decision

`scripts/eval-protected.js ci` runs as a step inside a check that each repo
already requires, on every pull request:

- `_agent-guidance`: the `checks` job of `ci.yml`, which the required `test`
  context aggregates. It covers guidance sections and reads the public
  explorer.
- `adam-agentskills`: its required `pytest` job. It covers public skills and
  reads the public explorer.
- `adam-agentskills-private`: its required `agent-checks` job. It covers
  private skills. The job builds the gate's input from its own
  `persistent/eval-results` with skills-evals' `scripts/protection_doc.py`,
  so private results never leave that repo. Until that branch exists the
  job runs with `--no-evidence`, which protects nothing, because there is no
  evidence to protect.

The step sits inside an existing required job. A new job would need its own
ruleset entry, and a check that is not required is a check a hand merge can
ignore.

The step reads the PR body and file list from the API, so re-running the
check after an edit to the body picks up the change. A PR that edits a
protected subject passes if its body has
`Correctness fix: <subject> — <class>: <what was wrong>`, with one of the
four classes. skills-evals' eval-lane PRs are exempt: improve, retirement
and new-skill PRs, on `eval-improve/`, `eval-guidance-improve/`,
`retire-skill/`, `retire-guidance/` or `draft-skill/`, opened by a GitHub
App. Evals judge their changes. A branch with one of those names opened by
a person is not exempt.

## Consequences

- No path into these three repos, by hand or by routine, makes a wording
  edit to a protected subject without the marker. The marker is
  self-declared, so CI cannot tell a false one. The fleet worker's
  independent review checks it for the PRs that routine merges. For a hand
  merge, the marker is a speed bump that leaves a record.
- The public repos' checks fetch the explorer from
  `raw.githubusercontent.com`. When a PR touches a subject and that fetch
  fails, the check fails closed, and a re-run fixes it.
- A change to the script reaches `adam-agentskills` and the private
  registry only when their pinned `_agent-guidance` commit is bumped.
