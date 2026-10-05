# 0017 — Prompt-audit runs as an event-triggered sweep, never as a check or a schedule

**Status:** Proposed (2026-10-05)

## Context

Claude Code
[v2.1.283](https://github.com/anthropics/claude-code/releases/tag/v2.1.283)
(published 2026-09-25T21:50Z) added `/doctor prompt-audit`, which audits
CLAUDE.md files, skills, agents and commands for prompting patterns written
for older models, and lists stale paths, stale commands and contradicting
instruction files first.

One measured run (CLI 2.1.289, against `adam-agentskills`, plan mode with
read-only tools, 2026-10-04):

- It runs headless and exits 0 whether or not it finds anything. Its output
  is Markdown prose and a proposed diff, with no machine-readable verdict.
- It took about 96 s and an API-equivalent $1.45. It judges for the model
  running the session.
- It also read the user-level `~/.claude/CLAUDE.md` and `AGENTS.md`, which
  in this fleet are `agents-md/base.md` delivered by the fleet-memory hook,
  so a per-repo sweep sees that text once per repo.
- Its findings mixed real defects (a wrong file count, a dangling section
  reference, a British spelling) with proposals to delete the dated incident
  evidence this guidance keeps on purpose.

The fleet's own rules rule out the obvious integrations:

- A required check must be deterministic, and a required check cannot
  filter on paths, so a prompt-audit check would run on every PR, including
  each sync-bot PR that rewrites every consumer's `AGENTS.md`.
- The only CI credential for Claude is skills-evals' OIDC federation rule,
  pinned to that repo's `main`.
- CI logs on public repos are public, and the report quotes user-level
  configuration.

What the audit measures against changes only when the model or the tool
changes, and the vendor changelog routine
([`agent-changelog-routine.md`](../reference/agent-changelog-routine.md))
already reads every Claude Code release, daily.

The owner's decisions of 2026-10-05: dated incident evidence moves out of
the instruction files with a pointer left behind; sweep issues get the
`agent-ready` label; the cost is acceptable as subscription usage, with a
Claude routine triggered by the changelog routine when it detects a relevant
change; deterministic checks that grow out of the sweep warn and never block.

## Decision

Prompt-audit runs as a sweep under
[`docs/routines/prompt-audit-sweep.md`](../routines/prompt-audit-sweep.md),
as a claude.ai Routine with **no schedule**. It starts two ways:

- **The changelog routine fires it** through the sweep's API trigger (the
  documented `/fire` endpoint), when a Claude Code release in its window:
  - adds a model or changes the default model or a model alias;
  - changes `/doctor`, `/doctor prompt-audit` (or `/checkup`) or
    `/skill-doctor`;
  - changes how instruction files load: `CLAUDE.md`, `AGENTS.md`,
    `@`-imports, rules files, skill or `SKILL.md` loading, or memory files.

  The changelog routine first files one trigger issue in this repo, then
  fires with that issue's URL as the payload, so every fire has a durable,
  deduplicated record.
- **The owner starts it by hand** (**Run now**), for example after a model
  change seen elsewhere, such as a reviewed `skills-evals` roster change.

The sweep:

- covers `repos.yml`'s `cron_coverage.fleet`, across both `Adam-S-Daniel`
  and `jodidaniel`, read at run time;
- is read-only, and runs on the account's default model, which is the model
  it should judge for;
- runs only on the claude.ai subscription: the routine draws subscription
  usage, and it refuses to run a nested audit unless `claude auth status`
  reports `loggedIn` true, `apiProvider` `firstParty` and an `authMethod` of
  `claude.ai` or `oauth_token` (a cloud container's injected OAuth token
  reports `oauth_token`; 2026-10-05 probe);
- routes findings on user-level or managed-block text to
  `agents-md/base.md`, from the `_agent-guidance` run only;
- files at most one issue per repo, labeled `agent-ready`, deduplicated
  against open issues;
- never applies a proposed diff and never merges or auto-merges anything;
- logs every run in
  [`prompt-audit-runs.md`](../reference/prompt-audit-runs.md), naming each
  repo reached or not reached;
- names a private repo (by its `private` field at run time) and its finding
  count **only** in every public output: the run log, the PR body, issues
  in public repos including `_agent-guidance`'s cross-repo item, and
  notifications. No quote, path or finding text from a private repo leaves
  that repo's own issue.

When a defect class recurs in two or more repos in one sweep, the sweep
proposes a deterministic check for it, in this repo's scripts or the
registry's census. Such a check **warns and never blocks**: its CI job may
be a required check, as fleet policy asks of every PR job, but it reports
findings as warnings and exits 0.

## Consequences

- Model-specific prompt drift is caught when it can first appear, without a
  recurring bill or recurring noise. Between sweeps nothing LLM-backed
  watches instruction files; the warn-only checks and the guidance
  centralization Routine cover that gap.
- The sweep's fire token is an environment variable, readable by any
  session in its environment. The agent proxy's API credentials cannot hold
  it, because they are never attached to `api.anthropic.com`. So the owner
  decided (2026-10-05) that it lives in a dedicated `Changelog Routine`
  environment that only the changelog routine uses, never in `My
  Whitelist`, which every other cloud session shares. A leaked token can
  still start a sweep but cannot steer one: the sweep reads only a
  trigger-issue URL it re-verifies and two switches from the payload.
  Revoke or regenerate it on the routine's API trigger.
- Subscription usage is the cost, not API billing. When the account has
  usage credits turned on, a run that reaches the subscription limit
  continues on metered overage instead of stopping (Routines docs, "Usage
  and limits"); a sweep is about a dozen nested audits, so check usage
  credits before a full run if that matters.
- The in-session `RemoteTrigger` tool would avoid the stored token, but
  Claude Code 2.1.289 disables it when `CLAUDE_CODE_REMOTE` is set, which
  is every cloud session, so a routine cannot use it.
- `agent-ready` sweep issues reach the laptop issue worker. The issue body
  says to verify each finding, never apply the audit's diff, never delete
  dated evidence and never edit a managed block, but the worker still acts
  on an LLM's judgment. Low-confidence findings stay in the run log for that
  reason.
- Answered by the owner on 2026-10-05: the default allowlist is on, so
  `api.anthropic.com` is reachable; the fire token gets its own environment;
  a single-repo trial after merge is approved; a model change seen only in
  a `skills-evals` roster change is swept by a manual **Run now**.
- A 2026-10-05 probe in a cloud session found the nested `claude` logged in
  with `authMethod` `oauth_token`, not `claude.ai`, and `api.anthropic.com`
  answering with an Anthropic 401 (not a proxy 403), so the step 0 auth probe
  accepts both values.
- Two things are unverified until that single-repo trial: that a routine's
  sandbox has an authenticated nested `claude` that can run a nested audit, and that
  `/doctor prompt-audit` honors an explicit path list. If the nested audit
  cannot run, a laptop session runs the same spec.
- Revisit this if prompt-audit gains a machine-readable verdict and the
  fleet gains a Claude credential in CI outside skills-evals.
