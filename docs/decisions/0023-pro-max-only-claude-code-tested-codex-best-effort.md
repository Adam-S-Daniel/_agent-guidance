# 0023 — Pro/Max only, Claude Code tested, Codex best effort, and the Projects-era strategy

> Note: ADR 0023 is also used by [0023-mark-agent-contributions.md](0023-mark-agent-contributions.md) (marking agent-made GitHub contributions); the two are distinct decisions, numbered in parallel by mistake.

**Status:** Accepted (2026-10-24)

## Context

Claude Code Projects (a coordinator session plus thread sessions, a
server-side project memory, project instructions set in the claude.ai UI,
and a shared folder) shipped as a public beta for Pro and Max plans in
October 2026. The owner asked on 2026-10-20 what that changes for this
fleet's guidance, skills, memory harvesting and evals, and whether any of it
should be retired. The resulting strategy was reviewed adversarially and then
weighed against wrapper tools, open-source harnesses and a control of
scrapping everything and using Projects as shipped.

What was true when the decisions were made:

- **Plan.** The account is on a Pro/Max plan. Team and Enterprise add
  features this fleet does not have and does not design for: server-managed
  settings, organization skills, analytics, Code Review and the Compliance
  API.
- **Training and retention.** "Allow training on chats" has been off since
  the account was created. With it off, cloud and Remote Control sessions
  stay listed, transcripts included, until deleted: the oldest on the
  account, from 2026-01-23, was still readable on 2026-10-21. The "30 days"
  in Anthropic's docs applies to deleted chats.
- **Harnesses.** Only Claude Code and Codex show up in the 2026-10-20
  survey of the owner's sessions. Of the 25 KB guidance, four sections
  (about 7.2 KB) apply only to Claude Code; 15 of 20 skills are portable.
- **Delivery.** In multi-repo cloud sessions, hooks come from the cloud
  environment's setup script, which lives only in the claude.ai UI, not from
  any repo's `.claude/settings.json`.
- **Evals.** The improvement loop had never opened an improvement PR. Eval
  spend is capped by [skills-evals#388](https://github.com/Adam-S-Daniel/skills-evals/pull/388).

## Decision

1. **Plans: Pro/Max only.** Nothing is built for, or depends on, a
   Team- or Enterprise-only feature.
2. **Training is off and sessions are kept until deleted.** Past sessions
   are a durable source for mining; nothing needs to copy them out before a
   retention window closes.
3. **Harnesses: Claude Code is tested, Codex is best effort.** Test, measure
   and verify in Claude Code only until the owner decides otherwise. Keep
   Codex delivery working where it costs little (the guidance still reaches
   `~/.codex/AGENTS.md`, and the changelog routine tracks Codex again,
   [#315](https://github.com/Adam-S-Daniel/_agent-guidance/pull/315)), but no
   eval or check gates on Codex.
4. **Gate: build nothing new** until one improvement PR has been merged or
   rejected on its eval numbers and two weekly eval sweeps have run inside
   the cap. Cleanup (item 6) is not new building and may run alongside.
5. **Portability: neutral content, same delivery (Option B) now.** Move the
   Claude-Code-only guidance into a section delivered only to
   `~/.claude/CLAUDE.md`, move incident prose to `docs/evidence/` (as
   [ADR 0018](0018-incident-narratives-live-in-docs-evidence.md) already
   does), commit the cloud setup script with a drift check, and label
   Claude-only skills with `compatibility:`. Delivering the guidance only
   through each harness's native files (Option A) is deferred; serving it
   over MCP (Option C) is rejected.
6. **Memory mining stays local.** There is no cloud miner. Project memory is
   handled by hand.
7. **Cleanup is a fixed list,** time-boxed to two weeks, then one small
   sweep per repo. Items: this ADR; retiring adam-agentskills' in-repo
   memory and the docs that teach it; duplicate ADR numbers (two `0015`
   here, two `0002` in skills-evals); committing the cloud setup script and
   fixing its hook matcher; leftover ZIP uploads in the account skill
   store; stale `agent-working` claims; unpushed work on the owner's
   machine; skills-evals' handoff and status board; superseded ADRs that
   name retired channels.
8. **Skill saves get one guidance line** and a one-time removal of the old
   ZIP uploads, not a sync job.

## Consequences

- Anything that needs Team or Enterprise is out of scope; a proposal that
  depends on one starts with a new ADR reversing decision 1.
- Codex can regress silently. That is accepted: nothing measures it, and a
  report from a Codex session is the only signal. Reversing decision 3 means
  adding Codex arms to the evals first.
- The gate stops new automation, including work that looks cheap, until the
  eval loop has shown it can change a skill. It also means the strategy's
  later steps (a single `base.md` PR for Option B, the path-scoped-rule
  pilot in [skills-evals#115](https://github.com/Adam-S-Daniel/skills-evals/issues/115),
  delivery fixes) wait.
- Keeping mining local leaves cloud-only sessions unmined until someone
  mines them by hand.
- Revisit after the gate opens, and in any case by 2026-12-15, when the
  owner decides whether to keep the system or unwind it in favor of Projects
  as shipped.
