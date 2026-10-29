# 0029 — Skills never run shell commands on load

**Status:** Accepted (2026-10-27)

## Context

Claude Code runs a line in a `SKILL.md` or custom command `.md` file of the
form ``!`cmd` `` (when the `!` is at line start or right after whitespace), and
a fenced block opened with ```` ```! ````, as a shell command the moment the
skill is invoked, before the model sees the text
([Inject dynamic context](https://code.claude.com/docs/en/skills)). Nothing
reviews that command at run time: no permission prompt, and no prompt-injection
defense that works on what the model reads. A skill is also instruction text
the fleet installs into sessions automatically, so one pulled-in line would run
on every machine that loads it.

No skill in the fleet uses this feature today. The owner asked for two things:
a git hook that refuses it, and the setting `"disableSkillShellExecution": true`
delivered fleet-wide.

## Decision

1. **A fleet check, `skill-shell`, in the agent-checks runner**
   (`templates/agent-checks/agent-checks`, copied to `.githooks/agent-checks`;
   [ADR 0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md)). For
   a path named `SKILL.md`, or a `.md` file under `.claude/commands/` or
   `plugins/*/commands/` (nested too), it reads the blob with `file_content`
   and refuses a line with ``!`` where the `!` is at line start or preceded by
   whitespace, or a line that starts, after indentation, with ```` ```! ````.
   The match is a lexical token, so it uses `grep -E`. A `!` inside a code span
   (preceded by a backtick) does not match, and a file elsewhere (a README that
   documents the syntax) is not checked. The message names the path, never the
   content, and says to have the skill tell the agent to run the command or to
   move it into a script the skill calls.
2. **The sync delivers `"disableSkillShellExecution": true`** to each fleet
   repo's `.claude/settings.json` with `scripts/set-skill-shell-setting.sh`,
   in the registrar's posture
   (`scripts/register-bootstrap-hook.sh`): it adds or corrects one key, keeps
   every other key and their order, re-serializes with 2-space indent, proves
   by re-parse that the write equals the original plus that key, refuses a
   file that is not a JSON object, and does nothing when the key is already
   true. A repo that gitignores `.claude/` is skipped with a warning, as the
   hooks are. The helper prints `already-set`, `set`, `refused-unparseable`,
   `refused-guard` or `refused-unwritable`, which the sync logs. This repo's own
   `.claude/settings.json` carries the key too, since the sync excludes it.

## Consequences

- A project setting covers only sessions started in a repo that carries it.
  It does not cover a skill loaded from a repo outside the fleet, or a plugin
  skill used in an unrelated project. On durable machines the user-level
  setting is converged separately, by `setup.sh` in `acsi-agentskills`.
- The check reads only files whose path says they are skills or commands. A
  skill installed another way (an account upload, a path the globs miss) is not
  seen; the setting is the backstop for those.
- A skill that wants live context has to say so in prose ("run `git status`
  and read it"), which costs a tool call the injection would have saved. That
  trade is the point.
- The setting key is shipped by the sync only. A repo that hand-edits it to
  `false` is set back to `true` on the next sync.
