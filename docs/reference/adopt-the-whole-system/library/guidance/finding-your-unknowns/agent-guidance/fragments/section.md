## Finding your unknowns

Ambiguities surface *during* implementation. Before: name what you don't
know, preferring a code reference to prose. During: surface departures
and edge cases. After: explain what changed and why it is correct. Durable
findings go in the repo, not agent memory. A rule a commit can mechanically
check becomes a check first: fleet-wide in `_agent-guidance`'s
`templates/agent-checks/agent-checks`, one repo's in its `.agent-checks/local.sh`
(how-to atop `.githooks/agent-checks`). Otherwise: a fleet rule in
`_agent-guidance`'s `agents-md/base.md`, a repo fact below
`## Repo-specific additions`, a procedure in the skills registry. A note in
`~/.claude/projects/*/memory/` is a POINTER: `metadata.home` names that copy as
`<owner>/<repo>:<path>`, the memory-home Stop hook blocks a session that wrote
one without it, and a `type: user` note about the person is exempt. The
**`finding-unknowns`** skill is the full workflow: use it on unfamiliar code,
a new domain or subjective criteria.
