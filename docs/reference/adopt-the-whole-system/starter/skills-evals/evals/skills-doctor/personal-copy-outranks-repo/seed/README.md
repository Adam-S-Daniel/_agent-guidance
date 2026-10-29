# Session capture — cloud session on example-app, 2026-10-14

Copied out of the container so the delivery question can be answered after it
was gone.

- `session/skills/` — the personal skill store (`~/.claude/skills/`) as it
  stood, including the bootstrap hook's install record.
- `repo/` — the example-app checkout the session was opened on: its
  `skills.lock`, and its own committed skills. The repo's `.claude/` directory
  is copied as `repo/dot-claude/` so that opening this capture does not load
  its skills into whoever opens it.

The session-start line read:

```
skills: 1/1 from example-adopter/acsi-agentskills@2f5987b — OK
```

and the surface check read:

```
$ echo "entry=$CLAUDE_CODE_ENTRYPOINT remote=$CLAUDE_CODE_REMOTE_SESSION_ID force=$SKILLS_BOOTSTRAP_FORCE"
entry=remote remote=session_00000000example force=
```

## What prompted the capture

Pull request 214 in example-app changed the repo's own `release-notes` skill
to require a new section, merged on 2026-10-09. Every release-notes draft
written in a session since still leaves that section out. The drafts follow
the skill's layout otherwise, so the skill is triggering — it just reads as
though the change never landed.
