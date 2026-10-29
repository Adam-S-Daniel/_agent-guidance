# Session capture — cloud session on example-service, 2026-10-20

Copied out of the container before it was recycled.

- `session/skills.lock` — the lock committed in example-service, exactly as
  the session read it.
- `session/session-start.txt` — every line the session printed at start.

The hook's log file was not copied, and the container is gone.

## What prompted the capture

None of the skills example-service locks loaded in that session. The skill
listing held only the built-in ones. The personal store had nothing in it:

```
$ ls -la ~/.claude/skills/
ls: cannot access '/root/.claude/skills/': No such file or directory
```

The lock is valid JSON (`python3 -m json.tool session/skills.lock` prints it
back without complaint), and the registry commit it names exists. The last
session that loaded every skill was on 2026-10-16; the lock has had one commit
since then.
