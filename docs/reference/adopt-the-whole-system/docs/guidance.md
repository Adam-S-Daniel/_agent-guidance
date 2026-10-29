# Guidance, checks and memory

## What _agent-guidance holds

[`_agent-guidance`](../starter/agent-guidance/README.md) is the guidance
layer: the always-on rules, the machinery that delivers them to every repo
and every session, and the specs the standing routines follow.

- `agents-md/base.md` is the fleet guidance, starting with a
  `## Fleet rules` heading that says "None yet". `agents-md/sections/` holds
  per-stack sections (python, javascript, typescript, go, dotnet, rust,
  docker), each empty too; a repo asks for one in its `.agents-sync.yml`.
- `agents-md/stub.md` is the short floor each consumer's `AGENTS.md` gets:
  a pointer to the full guidance, the session-start verdict and a few rules
  restated for a session that lost the rest.
- `repos.yml` lists repos excluded from the sync, default sections, the
  skills-bootstrap pin, and `cron_coverage.fleet`, the repos the routines
  visit.
- `.claude/hooks/` holds the hooks the sync delivers: `fleet-memory.sh` (the
  session-start delivery), `memory-home.sh`, `skills-bootstrap.sh`,
  `github-write-marker.sh`, `codex-session-start.sh`.
- `templates/` holds the files the sync delivers besides `AGENTS.md`: the
  agent-checks runner and its CI workflow, and the Dependabot auto-merge
  sweep.
- `docs/routines/` and `docs/reference/` hold the routine specs
  ([routines](routines.md)), `docs/decisions/` the ADRs.

## The sync

On every merge that changes the guidance, `sync.yml` runs `scripts/sync.sh`
over every repo of both owners in `SYNC_OWNERS`: it rebuilds each repo's
managed `AGENTS.md` (everything under `## Repo-specific additions` is kept),
writes the `CLAUDE.md` bridge (`@AGENTS.md`) where there is none, delivers
the hooks and templates, and pushes straight to the default branch with a
`guidance-sync` token. That push works on a protected branch because
`fleet.yml` names the App as the ruleset's one bypass actor. The nightly
`drift-report.yml` writes `drift-report.md` to `persistent/drift-report`.

**Delivered once per session.** A consumer's `AGENTS.md` carries only the
stub. The full guidance arrives through `fleet-memory.sh`, a SessionStart
hook that installs it into user memory (`~/.claude/CLAUDE.md`, and
`~/.codex/AGENTS.md` when `~/.codex` exists) and prints one verdict line:
`installed`, `current`, `DEGRADED` or `skipped`. Inlined in every repo
instead, it once took 332.3k tokens in one multi-repo session.

**Changing guidance.** Edit `base.md` (or a section) in a pull request on
`_agent-guidance`. In the same pull request: copy `base.md` byte for byte
to `.claude/hooks/fleet-guidance.md` (CI checks the mirror), give a new
`##` heading a row in `agents-md/eval-coverage.yml`, add an entry to
`docs/guidance-impact.md` for a changed section, and regenerate this repo's
own `AGENTS.md`. The repo's `AGENTS.md`, "Repo-specific additions", has
the commands; CI fails on each one you miss.

## Checks before skills, skills before guidance

A rule that a commit can show is broken becomes a check, not text:

- **agent-checks** is one runner, `templates/agent-checks/agent-checks`,
  delivered to every fleet repo as `.githooks/agent-checks` with its CI
  workflow `agent-checks.yml`. It ships with no checks of its own; a repo's
  own go in `.agent-checks/local.sh`, and fleet-wide ones in the template.
  Each refusal says what is wrong, why, and how to fix it, so the rule does
  not need to sit in always-on guidance.
- **On your machine** a config-based git hook (Git 2.54+) runs it on every
  commit and push, in clones of your two owners' repos only
  ([machines](machine.md#install-on-each-durable-machine)).
- **In CI** the required `agent-checks` job runs it over a pull request's
  commits. A hook can be skipped with `--no-verify`; the required check
  cannot.
- `templates/agent-checks/agent-checks-guard.sh` is a PreToolUse hook that
  stops an agent from skipping the hooks.

The order of preference for any new rule: a check that a commit can fail;
then a skill; then a line of guidance. The fleet worker turns lessons into
checks this way ([routines](routines.md#fleet-worker)).

## Memory that points at its home

A Claude Code memory note outside a repo must name the repo file that holds
the fact (`metadata.home` as `<owner>/<repo>:<path>`); the note is a pointer
([ADR 0013](../starter/agent-guidance/docs/decisions/0013-a-memory-note-outside-a-repo-names-its-home.md)).
`memory-home.sh`, registered once per machine at user level by
`install-machine.sh`, nudges at session start and blocks a session's stop
while a note it wrote has no home. A weekly routine curates the notes and
promotes facts into issues the fleet worker picks up
([memory curation](routines.md#memory-curation)).

## Where to read more

`_agent-guidance`'s [README](../starter/agent-guidance/README.md) (the
sync, the skills-bootstrap hook, the `CLAUDE.md` bridge, the required
secrets, the drift report, cron coverage), its `AGENTS.md`, and its ADRs.
