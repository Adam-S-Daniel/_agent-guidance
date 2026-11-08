# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

guidance-marker: fleet-stub (transcript counts use this line; keep it)

## Fleet guidance is delivered once per session — not by this file

The full guidance, floor rules included, is installed into user memory
(`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) by the `fleet-memory`
SessionStart hook, whose verdict line says whether it loaded. `installed` or
`current`: it is in context. `DEGRADED`, `skipped` or no verdict: it is not,
so read [`agents-md/base.md`](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/agents-md/base.md)
before non-trivial work and say you ran degraded (not for `skipped`, the
owner's choice). Until you have read it: never push to a default branch,
force-push a shared branch, commit a secret, or print personal data to a CI
log.

**Your first reply to the user in a session begins**
`fleet-guidance check: <value>`, copied from the guidance's
`fleet-guidance check value:` line, or `fleet-guidance check: none` when no
such line is in your context. Never guess one; only in replies to the user.
