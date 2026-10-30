# Session capture — build VM, 2026-10-22

Copied off `build-01.example.net`, the VM I SSH into for long builds, so the
delivery question can be answered away from it.

- `session/skills/` — the personal skill store (`~/.claude/skills/`) as it
  stood.
- `session/skills.lock` — the lock in the example-api checkout the session was
  opened on.
- `session/surface.txt` — the surface check, run inside the session.
- `session/plugin-list.json` — `claude plugin list --json`, run on the VM.
- `session/skill-listing.txt` — the skill names the session offered to its
  Skill tool, copied out of that session's context.

The session-start lines scrolled away before I copied them.

## What prompted the capture

The entrypoint says `ssh-remote`, so this is a remote session, and remote
sessions are where the bootstrap hook installs the locked bundle into
`~/.claude/skills/`. None of the three locked skills is in there, and there is
no `.skills-bootstrap-installed.json` either, so it looks as though the hook
never ran and the VM is getting no skills from the lock at all.
