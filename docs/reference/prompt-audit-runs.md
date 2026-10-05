# Prompt-audit sweep: run log

One line per run of the prompt-audit sweep
([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md)), including
`DRY_RUN` and `BLOCKED` runs. Each line records the date and start time, what
triggered the run, its switches, the executor probe answers and the model the
audits ran on, the reach of every repo in `cron_coverage.fleet` with its
finding count and any paths left unaudited, the issues filed or deferred, the
PR, and the result. Runs write this file on `persistent/prompt-audit-sweep`;
it reaches `main` when the owner merges that branch's PR.

- 2026-10-05 04:54Z | trigger: manual | DRY_RUN SCOPE=adam-agentskills | probes: claude 2.1.289; auth true/firstParty/oauth_token (billing basis unverified); nested /doctor prompt-audit exit 0, 37 turns, $1.28 API-equivalent, via unshare; user-level files not read | model claude-sonnet-5-5 | Adam-S-Daniel/adam-agentskills: reached bf4e44f, 18 findings kept (3 high, 15 medium), 8 low logged only, 22/22 inventoried paths named in scope, none flagged not audited (report also cites other files read); other 12 fleet repos not in SCOPE | issues: none (DRY_RUN) | PR: draft, see Adam-S-Daniel/_agent-guidance branch persistent/prompt-audit-sweep-e3dq70 | done
- 2026-10-05 05:10 UTC; trigger: manual; switches: `DRY_RUN`, `SCOPE=not-a-fleet-repo` (negative control); probes: not run (stopped before step 0); audits: none, no model; repos: none audited; issues: none (DRY_RUN); PR: draft from `persistent/prompt-audit-sweep-qomcyz`; result: BLOCKED (SCOPE names not-a-fleet-repo, which is not in cron_coverage.fleet).
- 2026-10-05 05:17Z | trigger: manual | SCOPE=adam-agentskills | probes: claude 2.1.289; auth loggedIn=true firstParty oauth_token; nested /doctor prompt-audit exit 0, 21 files, 32 turns, API-equivalent $0.95; user-level files not read | model: claude-sonnet-5-5 | Adam-S-Daniel/adam-agentskills: reached bf4e44f, 7 findings (1 Low-confidence flag set aside), 0 not audited; other fleet repos out of SCOPE, not audited; sweep ran on assigned branch persistent/prompt-audit-sweep-trvu5x (spec branch persistent/prompt-audit-sweep not used); no prior DRY_RUN logged | filed: https://github.com/Adam-S-Daniel/adam-agentskills/issues/50 | PR: see this branch's PR | done
