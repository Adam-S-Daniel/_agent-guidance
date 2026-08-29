# Probe result: hosted harness user-memory behavior

Session: hosted Claude Code remote session, repo `_agent-guidance`, 2026-08-29.

## STEP 1 — introspection (recorded before any tool call)

### (a) Was the literal string `CRIMSON-BADGER-4242` present in session-start context?

**YES.**

It appeared in the session-start `claudeMd` block, as the final section of the
content attributed to `/root/.claude/CLAUDE.md`:

```
## Cloud probe canary

The fleet cloud canary is CRIMSON-BADGER-4242.
```

I knew the string from context at session start, not from reading a file
afterwards. (The `wc`/`grep` in STEP 2 below were run only to corroborate.)

### (b) Does session-start context include content labeled as coming from a USER-level memory file?

**YES.**

The `claudeMd` block explicitly labels `/root/.claude/CLAUDE.md` as the user's
private global instructions, distinct from the repo-path entries. Its full text
was inlined into context.

### (c) Verbatim `Contents of <path> ...` header lines visible in context

```
Contents of /root/.claude/CLAUDE.md (user's private global instructions for all projects):
Contents of /home/user/_agent-guidance/CLAUDE.md (project instructions, checked into the codebase):
Contents of /home/user/_agent-guidance/AGENTS.md (project instructions, checked into the codebase):
```

Those are the only three such header lines present.

### Additional observation (unprompted, relevant to interpreting (a) and (b))

The session opened with a SessionStart hook result line:

```
SessionStart:startup hook success: fleet-guidance: installed (52350 bytes) -> /root/.claude/CLAUDE.md
```

So the user-level memory file was **written by this repo's own
`.claude/hooks/fleet-memory.sh` at session start**, and the harness then loaded
it as user memory in the same session. The canary reaching my context is
therefore evidence that (i) the hook wrote the file and (ii) the harness reads
`/root/.claude/CLAUDE.md` as user-level memory *after* SessionStart hooks run —
not evidence that a pre-existing user memory file survived container creation.

A second SessionStart hook also reported: `skills: 8/8 from
Adam-S-Daniel/agentskills@b0b93b3 — OK`.

Note the byte discrepancy: the hook reports 52350 bytes installed, while
`wc -c` reports 52455 (a 105-byte difference). Not investigated.

## STEP 2 — environment facts

`$HOME` is `/root`, so `~/.claude/` is `/root/.claude/`.

### `ls -la ~/.claude/`

```
total 136
drwxr-xr-x  9 root root  4096 Aug 29 22:32 .
drwx------ 15 root root  4096 Aug 29 22:32 ..
-rw-r--r--  1 root root    24 Aug 29 22:32 .last-cleanup
-rw-------  1 root root 52455 Aug 29 22:32 CLAUDE.md
drwxr-xr-x  2 root root  4096 Aug 29 22:32 backups
-rw-------  1 root root   716 Aug 29 22:32 launcher-settings.json
drwxr-xr-x  3 root root  4096 Aug 29 22:32 plugins
-rw-------  1 root root   214 Aug 29 22:32 policy-limits.json
drwx------  3 root root  4096 Aug 29 22:32 projects
-rw-------  1 root root     2 Aug 29 22:32 remote-settings.json
drwxr-xr-x  4 root root  4096 Aug 29 22:32 session-env
-rwxr-xr-x  1 root root  4766 Aug 29 22:32 session-start-git-identity.sh
drwx------  2 root root  4096 Aug 29 22:32 sessions
drwxr-xr-x  2 root root  4096 Aug 29 22:32 shell-snapshots
drwxr-xr-x 12 root root  4096 Aug 29 22:32 skills
-rwxr-xr-x  1 root root  6395 Aug 29 22:32 stop-hook-git-check.sh
-rwxr-xr-x  1 root root 11699 Aug 29 22:32 stop-hook-reply-gate.py
-rwxr-xr-x  1 root root  3630 Aug 29 22:32 user-prompt-submit-reply-reminder.py
```

### `wc -c ~/.claude/CLAUDE.md`

```
52455 /root/.claude/CLAUDE.md
```

### `head -3 ~/.claude/CLAUDE.md`

```
(blank line)
(blank line)
<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->
```

(The first two lines are empty; the third is the managed-block BEGIN marker.)

### `grep -c CRIMSON-BADGER-4242 ~/.claude/CLAUDE.md`

```
1
```

### `cat .claude/settings.json`

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/fleet-memory.sh\"",
            "timeout": 30
          }
        ]
      },
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh\"",
            "timeout": 90
          }
        ]
      }
    ]
  }
}
```
