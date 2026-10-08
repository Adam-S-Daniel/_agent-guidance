# Prompt-audit sweep: run log

One line per run of the prompt-audit sweep
([`prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md)), including
`DRY_RUN` and `BLOCKED` runs. Each line records the date and start time, what
triggered the run, its switches, the executor probe answers and the model the
audits ran on, the reach of every repo in `cron_coverage.fleet` with its
finding count and any paths left unaudited, the issues filed or deferred, the
PR, and the result. Each run appends its line on the branch its session
assigned, in its own PR to `main`; the line reaches `main` when the owner
merges that PR.

The first three lines are **trial runs** of the spec's **First run**, each
merged from its own PR before the spec gave every run its own PR:
[#259](https://github.com/Adam-S-Daniel/_agent-guidance/pull/259) (`DRY_RUN`),
[#260](https://github.com/Adam-S-Daniel/_agent-guidance/pull/260) (negative
control) and
[#261](https://github.com/Adam-S-Daniel/_agent-guidance/pull/261) (live pass,
which filed
[adam-agentskills#50](https://github.com/Adam-S-Daniel/adam-agentskills/issues/50)).
The branch names they cite were assigned by their sessions; none is a
run-log home.

- 2026-10-05 04:54Z | trigger: manual | DRY_RUN SCOPE=adam-agentskills | probes: claude 2.1.289; auth true/firstParty/oauth_token (billing basis unverified); nested /doctor prompt-audit exit 0, 37 turns, $1.28 API-equivalent, via unshare; user-level files not read | model claude-sonnet-5-5 | Adam-S-Daniel/adam-agentskills: reached bf4e44f, 18 findings kept (3 high, 15 medium), 8 low logged only, 22/22 inventoried paths named in scope, none flagged not audited (report also cites other files read); other 12 fleet repos not in SCOPE | issues: none (DRY_RUN) | PR: draft, see Adam-S-Daniel/_agent-guidance branch persistent/prompt-audit-sweep-e3dq70 | done
- 2026-10-05 05:10 UTC; trigger: manual; switches: `DRY_RUN`, `SCOPE=not-a-fleet-repo` (negative control); probes: not run (stopped before step 0); audits: none, no model; repos: none audited; issues: none (DRY_RUN); PR: draft from `persistent/prompt-audit-sweep-qomcyz`; result: BLOCKED (SCOPE names not-a-fleet-repo, which is not in cron_coverage.fleet).
- 2026-10-05 05:17Z | trigger: manual | SCOPE=adam-agentskills | probes: claude 2.1.289; auth loggedIn=true firstParty oauth_token; nested /doctor prompt-audit exit 0, 21 files, 32 turns, API-equivalent $0.95; user-level files not read | model: claude-sonnet-5-5 | Adam-S-Daniel/adam-agentskills: reached bf4e44f, 7 findings (1 Low-confidence flag set aside), 0 not audited; other fleet repos out of SCOPE, not audited; sweep ran on assigned branch persistent/prompt-audit-sweep-trvu5x (spec branch persistent/prompt-audit-sweep not used); no prior DRY_RUN logged | filed: https://github.com/Adam-S-Daniel/adam-agentskills/issues/50 | PR: see this branch's PR | done
- 2026-10-08 01:09Z | trigger: https://github.com/Adam-S-Daniel/_agent-guidance/issues/279 | switches: none | probes: claude 2.1.293; auth true/firstParty/oauth_token (billing basis unverified); nested /doctor prompt-audit exit 0 on every repo, via unshare, API-equivalent about $9.8 in total over 14 audits; user-level files not read; ANTHROPIC_API_KEY/AUTH_TOKEN unset | model claude-sonnet-5-5 | repos (names resolved by git ls-remote on the attached checkouts, not a dual-owner REST probe; ai-usage-dashboard by anonymous clone): Adam-S-Daniel/_agent-guidance reached d5287fa, 2 findings kept (4 Low logged only) + 1 cross-repo item; Adam-S-Daniel/adam-agentskills reached b552eb2, 15 findings; private, reached, 2 findings; Adam-S-Daniel/adamdaniel.ai reached 6ff477d, 5 findings (4 Low flags logged only); Adam-S-Daniel/ai-usage-dashboard reached 0b396c7, 0 findings (not attached, so no issue could be filed); Adam-S-Daniel/claude-memory-map reached c4e5e1e, 0 findings; Adam-S-Daniel/cms-platform reached 992d449, 17 findings; Adam-S-Daniel/fastmail-actions reached a0522c5, 0 findings; Adam-S-Daniel/GHA-bench reached bf0f987, 5 findings; jodidaniel/jodidaniel.com reached e75f43a, 4 findings; private, reached, 0 findings; private, reached, 0 findings; Adam-S-Daniel/skills-evals reached 9b7f610, 1 finding, 2 seed files only partly read (cms-platform-221 to about line 730, _agent-guidance-196 partway) so not fully audited; Adam-S-Daniel/wsl-automation reached 20f7246, 2 findings; all other paths audited | issues: https://github.com/Adam-S-Daniel/_agent-guidance/issues/282, https://github.com/Adam-S-Daniel/adam-agentskills/issues/56, https://github.com/Adam-S-Daniel/adamdaniel.ai/issues/4169, https://github.com/Adam-S-Daniel/cms-platform/issues/819, https://github.com/Adam-S-Daniel/GHA-bench/issues/102, https://github.com/jodidaniel/jodidaniel.com/issues/396, https://github.com/Adam-S-Daniel/skills-evals/issues/346, https://github.com/Adam-S-Daniel/wsl-automation/issues/54, plus one in a private repo | PR: https://github.com/Adam-S-Daniel/_agent-guidance/pull/280 | done
