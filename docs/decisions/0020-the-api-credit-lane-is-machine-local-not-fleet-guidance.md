# 0020 — The API-credit lane is machine-local, delivered by plugin hooks, not by the fleet guidance

**Status:** Accepted (2026-10-09)

## Context

The owner receives a monthly Claude Platform API credit ($200 per grant, each
grant expiring 30 days after it arrives; use it or lose it). The owner's rule
(2026-10-08): when the subscription's weekly usage is more than half elapsed and
ahead of how much of the week has elapsed, use the credit for work that would
otherwise use subscription tokens. Later decisions refined it: at 98% weekly
usage or more, all remaining credit may be spent; leftover credit is **not**
spent merely because a grant is about to expire; the Fable weekly window counts
alongside the overall one (whichever is worse); local sessions only; at most two
API-billed children at a time across the machine.

The request was to document this here and propagate it to every enrolled repo.
Two facts argued against putting the rule in `agents-md/base.md`:

- Only the owner's laptop can act on it. It needs the local usage collector, a
  TPM-held signing key and Workload Identity Federation (WIF) configuration;
  cloud sessions, Routines, Codex and other machines can never open the lane.
  A `base.md` rule would load in every session in every repo and apply almost
  nowhere.
- The full-mode build had 172 bytes of headroom (`test_agents_md_size_budget`),
  and the rule is conditional. ADR 0002 keeps *unconditional* rules in the
  guidance; a conditional rule belongs with the mechanism that knows the
  condition.

## Decision

The lane is delivered by hooks in the `adam-coding-local` plugin of
[adam-agentskills](https://github.com/Adam-S-Daniel/adam-agentskills), not by
this repo's sync, and `base.md` does not mention it. The design lives in that
repo's ADRs:

- [0016](https://github.com/Adam-S-Daniel/adam-agentskills/blob/main/docs/decisions/0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md):
  plugins may ship hooks and `bin/`; a PR changing them waits for the owner's
  approval (`plugin-runtime-review`, a required check).
- [0017](https://github.com/Adam-S-Daniel/adam-agentskills/blob/main/docs/decisions/0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md):
  Codex loads these plugins too, so hooks are harness-aware and Claude-only
  hooks live in a file Codex never reads.
- [0018](https://github.com/Adam-S-Daniel/adam-agentskills/blob/main/docs/decisions/0018-api-credit-lane-for-claude-code.md):
  the lane itself — the gate, weekly allowance, grants, `claude-credit`, the WIF
  child environment, the fail-closed auth check, and the two cross-OS slots.

The signal comes from [ai-usage-dashboard](https://github.com/Adam-S-Daniel/ai-usage-dashboard)'s
collector, whose local `usage.json` carries both weekly windows (overall and
Fable) and Platform API spend.

How an agent learns of the lane: a SessionStart hook adds one line of context
**only when the lane is open**, and a UserPromptSubmit hook speaks only when it
opens or closes. A closed lane costs a session no tokens. The wrapper re-checks
the gate at the moment of spending, so a stale notice cannot spend.

Propagation is to the owner's Claude Code homes (Windows and WSL) through the
plugin marketplace and `setup.sh --owner-machine`, not to enrolled repos;
nothing in a repo would ever use it.

## Consequences

- No fleet session pays for a rule it cannot act on, and `base.md`'s headroom
  stays for rules that apply everywhere.
- The rule is invisible to anyone reading only this repo; this ADR is the
  pointer.
- Verified live on 2026-10-09: with the federation variables set only in the
  child, `claude auth status` reported `oauth_token` / `firstParty` with no
  subscription, and a `claude -p` run through `claude-credit` succeeded on API
  auth ($0.004 on Haiku), recorded in the lane's local spend ledger. The Admin
  usage report lags, so seeing that run in the `api-credit` workspace's usage
  was still pending when this was written.
- The lane's state (config with the WIF IDs, ledger, slots) lives under the
  owner's Windows profile, `~/.config/claude-credit/`, outside every repo.
