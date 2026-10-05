# Prompt-audit sweep: run log

One line per run of the prompt-audit sweep
([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md)), including
`DRY_RUN` and `BLOCKED` runs. Each line records the date and start time, what
triggered the run, its switches, the executor probe answers and the model the
audits ran on, the reach of every repo in `cron_coverage.fleet` with its
finding count and any paths left unaudited, the issues filed or deferred, the
PR, and the result. Runs write this file on `persistent/prompt-audit-sweep`;
it reaches `main` when the owner merges that branch's PR.

- 2026-10-05 05:10 UTC; trigger: manual; switches: `DRY_RUN`, `SCOPE=not-a-fleet-repo` (negative control); probes: not run (stopped before step 0); audits: none, no model; repos: none audited; issues: none (DRY_RUN); PR: draft from `persistent/prompt-audit-sweep-qomcyz`; result: BLOCKED (SCOPE names not-a-fleet-repo, which is not in cron_coverage.fleet).
