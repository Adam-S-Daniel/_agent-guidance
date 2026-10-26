# 0021 — The sync delivers the agent-checks runner, CI check and --no-verify guard to every fleet repo

**Status:** Accepted (2026-10-21)

## Context

adam-agentskills' ADR 0020 piloted commit-time checks for agents: one bash
runner, `.githooks/agent-checks`, run by Git 2.54 config-based hooks on owner
machines and cloud sessions, and by a CI job that is the real enforcement. The
pilot merged and behaved on the owner's Windows and WSL machines and in all four
cloud environments. Two things were left for later: a guard against the easy
skip (`--no-verify`), and the same checks in every other repo.

The machine-level install already covers every repo of both owners: the global
`includeIf "hasconfig:remote.*.url:..."` include runs `.githooks/agent-checks`
in any clone whose remote is Adam-S-Daniel or jodidaniel, and does nothing
where that file is absent. So what each repo lacks is only the runner and the
CI check. This repo's sync already reaches every repo of both owners, and
already ships managed files (`fleet-memory.sh`), an add-only workflow (the
Dependabot auto-merge one) and a `.gitattributes` line (ADR 0019).

## Decision

A fifth sync lane, on when `repos.yml` has `agent_checks: deliver: true`, takes
its files from `templates/agent-checks/`:

1. **The runner** `.githooks/agent-checks` is **managed**: overwritten whenever
   it differs from the template, so a check added here reaches every repo on
   the next sync. This repo now holds the canonical copy; adam-agentskills'
   pilot copy is overwritten like any other.
2. **The CI workflow** `.github/workflows/agent-checks.yml` is **added once**
   and never overwritten, so Dependabot can keep its pinned checkout current.
   It is not added where any workflow already runs `.githooks/agent-checks`
   (adam-agentskills runs it as a job in `ci.yml`), where `actions.lock` pins
   the repo's actions as tags, in a cms-platform consumer (a root
   `platform.lock`), or where the path is gitignored.
3. **The guard** `.claude/hooks/agent-checks-guard.sh` is a Claude Code
   PreToolUse hook (matcher `Bash|Edit|Write|MultiEdit|NotebookEdit`), managed
   like the runner and registered through the same registrar as the other
   hooks. It refuses `--no-verify` and `git commit -n`, hook config overridden
   with `-c`, `--config-env` or `GIT_CONFIG_*`, `git config` writes to
   `core.hooksPath`, `hook.*` or `includeIf.*`, and deleting or editing
   `.githooks/` or `agent-hooks.inc`. It is delivered wherever fleet-memory is
   (same refusals: `.claude/` gitignored, unparseable `settings.json`).
4. **A second `.gitattributes` line**, `.githooks/* text eol=lf`, written by
   the same helper as ADR 0019's line, because the runner has no `.sh` suffix
   and bash cannot parse it from a CRLF checkout.

## Consequences

- Every fleet repo gets the same checks, and a new check is one change here.
- The guard stops habit, not intent: a string check can be got around, and it
  fails open on any error of its own (no python3, input it cannot parse) so a
  broken guard never stops work. CI stays the enforcement, and it is only that
  once `agent-checks` is a required status check, which repo-settings declares.
- Codex and people at a terminal get no guard; for them the CI check is the
  only stop after a skipped hook.
- The workflow template pins `actions/checkout` by SHA, so repos that lock
  actions with `actions.lock` get the runner but must add the workflow by hand.
- A cms-platform consumer's `platform-pin-consistency` check fails on any
  workflow the platform does not dictate (measured on adamdaniel.ai and
  jodidaniel.com, 2026-10-21), so those sites get the runner and guard only.
  Their CI check has to come from the platform's own
  `examples/site/.github/workflows/`.
- The runner's pre-push check refuses a push to the default branch. This
  sync pushes to default branches directly, so a run on an owner machine with
  the hooks installed falls back to its PR path; CI runs have no hooks and are
  unaffected.

## Addendum (2026-10-26): the run log

Every `problem` call names a stable kebab-case check id (`rules-dir`,
`secret-file`, `staged-index`, `push-to-default`, `repo-local`, a lesson's
`lesson-<id>`, ...). Each local pre-commit and pre-push run appends one JSON
line, `{"v":1,"ts","event","repo","checks","failed"}`, to `$AGENT_CHECKS_LOG`
(default `${XDG_STATE_HOME:-$HOME/.local/state}/agent-checks/runs.jsonl`), so
a dashboard can show how often each check runs and refuses per repo. `repo`
is origin's GitHub `owner/name` or null; the line holds no paths, messages,
branches or user names. CI and `tree` runs write nothing.
`AGENT_CHECKS_LOG=off` turns it off, and a failed write is silent. The
runner's header comment is the reference.

## Addendum (2026-10-26): the guard refuses persistent/* deletes

The guard also refuses deleting a remote `persistent/*` branch
(`git push <remote> --delete`/`-d`, a `:persistent/...` refspec, or
`gh api -X DELETE .../git/refs/heads/persistent/...`). Those branches outlive
their pull requests and hold data, and a private repo has no ruleset to stop
the delete.
