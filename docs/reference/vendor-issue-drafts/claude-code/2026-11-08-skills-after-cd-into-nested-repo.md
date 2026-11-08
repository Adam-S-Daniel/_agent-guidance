# Comment: skills in a nested git repo never load after `cd` into it (2.1.319)

- **Vendor repo:** `anthropics/claude-code`
- **Form:** comment on [#93403](https://github.com/anthropics/claude-code/issues/93403)
- **Discrepancy:** [agent-claude-code.DISCREPANCIES.md](../../agent-claude-code.DISCREPANCIES.md), entry "2026-11-08 — A repo's own skills never load after `cd` into it from a multi-repo parent folder"
- **Status:** draft

## Fields

### Comment

A related case on 2.1.319, where the trigger is a Read and the skills still never load.

Layout: `parent/` is not a git repo. `parent/sub/` is its own git repo with `sub/.claude/skills/probe-sub/SKILL.md`. Start Claude Code in `parent/`.

- Read `sub/file.txt` without `cd`: `probe-sub` is announced (`commands_changed`), 2 of 2 runs.
- Bash `cd sub`, then Read `file.txt`: `probe-sub` is never announced, 3 of 3 runs.
- Same steps when `parent/` is the git repo and `sub/` a plain folder: announced, 2 of 2.

So after `cd` into a nested repo, its own `.claude/skills` are neither loaded as the new project's skills nor discovered as nested ones. 2.1.312's "Fixed per-directory and `paths:` skills not being found after a Bash `cd` deeper into a git repository" does not cover this layout, which is the one multi-repo cloud sessions use.

Repro:

```bash
mkdir -p parent/sub/.claude/skills/probe-sub && cd parent/sub && git init -q && echo hi > file.txt
printf -- '---\nname: probe-sub\ndescription: Use when asked for the probe word.\n---\nMARIGOLD\n' > .claude/skills/probe-sub/SKILL.md
cd .. && claude -p 'Run `cd sub` by itself, then Read file.txt there, then say done.' \
  --allowedTools "Bash(cd sub)" Read --output-format stream-json --verbose | grep -c probe-sub
# prints 0; without the cd step (Read sub/file.txt) it prints 1 or more
```
