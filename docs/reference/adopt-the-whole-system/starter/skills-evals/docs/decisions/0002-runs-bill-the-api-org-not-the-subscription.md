# 0002. Real runs bill the API organisation; subscription credentials are not adopted

> Note: ADR 0002 is also used by [0002-roster-follows-vendor-defaults.md](0002-roster-follows-vendor-defaults.md) (the roster follows vendor defaults); the two are distinct decisions, numbered in parallel by mistake.

- Status: accepted (2026-09-23)
- **Decision 1 superseded for routine runs only by
  [ADR 0010](0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md)
  (2026-10-06):** arms may run as nested `claude -p` inside a Claude Code
  routine, because there the sandbox supplies auth and the arm holds no
  readable long-lived subscription credential (ADR 0010, Probe 1), and only
  default-branch content from the `agentskills`, `example-site-platform` and
  `example-site-a` registries and `_agent-guidance` runs in them. The
  supersession is conditional on that staying true; ADR 0010 lists an open
  risk (whether an arm can read the session's ingress token file or any
  on-disk GitHub token) and proposes, pending the owner, closing it before the
  first scheduled run. For every other path, `eval.yml`, any
  Actions runner and a workstation included, decision 1 holds as written.
- Related: [0001](0001-roster-trusted-on-main.md),
  [#170](https://github.com/Adam-S-Daniel/skills-evals/issues/170)

## Context

Every real run of [`.github/workflows/eval.yml`](../../.github/workflows/eval.yml)
bills the API workspace `skills-evals-ci` through a workload-identity-federated
bearer: minted per run from the runner's GitHub OIDC token, valid at most an
hour, scoped to that one spend-capped workspace, and existing nowhere but
`$RUNNER_TEMP` (the workflow's header, "Auth", states the whole posture). A
measured run cost **$1.71** on the one-arm roster and is expected to cost
**$5–7** on the two-arm roster with the Fable 5.1 judge, merged on 2026-09-22
as [PR #172](https://github.com/Adam-S-Daniel/skills-evals/pull/172); of that,
the judge is roughly 45%.

The account also holds a Claude subscription with a weekly allowance — the
"points" [`../how-it-works.md`](../how-it-works.md) § 7 prices agent work in —
and that allowance is flat-rate: work run under it costs no dollars. The
harness invokes the **unmodified `claude` CLI** for both arms
(`harness/run_eval.py`, `claude -p … --model`) and the judge
(`harness/scorers/judge.py`), so pointing a run at the subscription instead of
the API organisation is an environment change, not a new client. This record
answers whether to make it.

The alternatives were assessed on 2026-09-23 against Anthropic's own
documentation, read the same day:
[Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance),
[Authentication](https://code.claude.com/docs/en/authentication),
[GitHub Actions](https://code.claude.com/docs/en/github-actions),
[Routines](https://code.claude.com/docs/en/routines) and the
[Consumer Terms](https://www.anthropic.com/legal/consumer-terms) (effective
2025-10-08).

### What the terms permit, and what they forbid

- Consumer Terms § 3 forbids accessing the Services "through automated or
  non-human means, whether through a bot, script, or otherwise", "Except when
  you are accessing our Services via an Anthropic API Key **or where we
  otherwise explicitly permit it**".
- The explicit permission exists: `claude setup-token` mints a **one-year**
  OAuth token that the docs describe as "For CI pipelines, scripts, or other
  environments where interactive browser login isn't available", consumed as
  `CLAUDE_CODE_OAUTH_TOKEN`; the GitHub Actions page documents the matching
  `claude_code_oauth_token` input, including scheduled automation, and says
  that with it "runs use your Claude subscription instead of API billing".
  The token "can only make model requests" — no Remote Control, no claude.ai
  connectors — and bare mode does not read it.
- The legal page bounds that permission: OAuth is for "ordinary use of Claude
  Code and other native Anthropic applications", "Advertised usage limits for
  Pro and Max plans assume **ordinary, individual usage**", and developers
  "may not collect, store, or intermediate Claude.ai credentials or session
  tokens — sign-in to a Claude account must complete through Anthropic's own
  flow". § 2 of the Consumer Terms forbids sharing account credentials; § 12
  reserves suspension without notice.

So a subscription token minted by `setup-token` and used by the unmodified CLI
is permitted. A credential obtained any other way is not.

### Credential precedence, and what an arm can read

Claude Code's precedence list puts `ANTHROPIC_AUTH_TOKEN` (2nd) **above**
`CLAUDE_CODE_OAUTH_TOKEN` (5th), so a step exporting both runs on the API
organisation whatever the intent: a split has to be enforced per step, not per
run.

The decisive fact is what an arm can read. Arms execute skill content from
four checked-out registries under `--permission-mode bypassPermissions`, and
the skill-arm environment allowlist (`harness/run_eval.py`'s `_ALLOWED_ENV`
and `_ALLOWED_ENV_PREFIXES`) passes through **every** variable whose name
begins `ANTHROPIC_` or `CLAUDE_`. Today that exposes the hour-long,
spend-capped, workspace-scoped bearer described above. A subscription token in
the same place would expose a **one-year credential to the account's whole
subscription**, uncapped, to an agent running third-party-authored content —
and would put a long-lived stored secret on a workflow whose header records
that none exists.

## Decision

1. **Scheduled and dispatched real runs keep billing the API organisation**
   through the WIF bearer. Arms are never run on a subscription credential in
   CI, because the arm environment is exactly where such a credential must not
   be.
2. **The judge is not moved to a subscription credential now**, although doing
   so would be permitted. On the two-arm roster it saves roughly $6–13 a month
   against a 3–6 point build on a one-way-door workflow, and it would cost the
   dollar accounting that the
   [#170](https://github.com/Adam-S-Daniel/skills-evals/issues/170) revisit
   depends on. It is recorded instead as a third column in that revisit,
   beside the status quo and the Batch API.
3. **Credentials are obtained only through Anthropic's own flow.** If a
   subscription credential is ever adopted, it comes from `claude setup-token`
   run by the account holder in their own terminal, pasted straight into an
   Actions secret — never printed into an agent's session. Reading the
   claude.ai session cookie out of a browser profile, copying the CLI's or the
   desktop app's stored OAuth credential out of `~/.claude/.credentials.json`,
   and driving the OAuth consent screen with browser automation are all
   **prohibited** by the terms quoted above and are not to be built, in this
   repository or beside it.
4. **Ad-hoc bulk runs may be run locally** by the account holder under their
   own `/login` — ordinary use of Claude Code, no credential handling — when
   dollars are scarcer than points that week. Results so produced are a local
   exhibit, not badge input: they did not come from the `main`-pinned CI path,
   and on a workstation a `bypassPermissions` arm inherits the real `HOME`,
   where the account's own credentials live.

### Alternatives considered

| Alternative | Verdict |
|---|---|
| `setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` for **arms** in `eval.yml` | Permitted, but disqualified: a one-year subscription credential in reach of a `bypassPermissions` agent running registry content |
| The same for the **judge only** | Permitted and buildable (it needs the token scrubbed from the arm environment and the WIF bearer kept out of the judge step). Deferred to #170 on payback, not on principle |
| A claude.ai/code cloud session, or a [routine](https://code.claude.com/docs/en/routines) fired from `eval.yml` | Permitted, but it moves the run outside the `main`-pinned WIF trust model, a routine may push only `claude/`-prefixed branches or branches carrying nobody else's commits (`eval-results` carries the bot's), and it is a research preview with a daily run cap |
| The desktop app's Remote Control | Not a CI surface, and a `setup-token` credential cannot establish one |
| A self-hosted runner on the workstation under `/login` | The schedule fires at 07:00 UTC into a laptop that sleeps and drops sessions; a public repository is the wrong place for a self-hosted runner |
| Browser-read claude.ai session cookie; copied stored OAuth credential; automated OAuth consent | Prohibited (see decision 3). Each also fails on its own terms: the cookie is not an API credential, a copied refresh grant is rotated out from under one holder, and the consent screen is the sign-in that must complete through Anthropic's flow |

## Consequences

- The dollar cost of a run stays real, itemised and exportable from the
  organisation's usage export. That export is the measuring instrument for
  #170 and for every "what would this cost" figure in
  [`../how-it-works.md`](../how-it-works.md) § 7; a subscription leg would be
  invisible to it, and `total_cost_usd` in the CLI's JSON would become a
  notional list-price figure rather than something billed.
- The weekly allowance stays reserved for agent work, which is the scarcer
  currency: the backlog is 60–120 points, three to six weekly allowances, and
  the five-hour rate-limit window — not the dollar spend — has been the
  binding constraint on this programme.
- `eval.yml` keeps its "no stored secret exists" property, and the blast
  radius of a compromised run stays one spend-capped workspace for at most an
  hour.
- The saving forgone is small and known: about $6–13 a month at the two-arm
  roster for a judge-only move, about $22–30 a month if the arms moved too.
- **Revisit triggers.** Reopen this with #170 around 2026-10-13, and reopen it
  regardless if the matrix runner ([#68](https://github.com/Adam-S-Daniel/skills-evals/issues/68))
  or N trials per arm ([#66](https://github.com/Adam-S-Daniel/skills-evals/issues/66))
  land and weekly judge spend passes about $25. At that scale the "ordinary,
  individual usage" wording above starts to bear on the question, which is an
  argument for the API organisation, not against the saving.
- The smallest experiment that would decide the judge column, when it is
  revisited, needs no token and no secret: on a workstation with no
  `ANTHROPIC_*` variable set, run the preflight shape
  (`claude -p 'Reply with exactly: ok' --model <id> --output-format json`) for
  every roster and fixture-pinned model id to see which ones the subscription
  serves, then run one fixture a few times with nothing else running and read
  the weekly and Fable meters before and after, to convert points into dollars
  against a measured CI run instead of the § 7 extrapolation.
