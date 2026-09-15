# 0013 — A memory note outside a repo names its home, and a session that wrote one without it does not stop

**Status:** Accepted (2026-09-14)

## Context

Claude Code's auto-memory writes one markdown file per fact:

```
${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<encoded-cwd>/memory/<slug>.md
```

plus a `MEMORY.md` index beside it. Each note carries YAML frontmatter with
`name`, `description` and a `metadata` block whose `type` is one of `user`,
`feedback`, `project` or `reference`.

Every one of those directories is **outside every repo**. There are **22** of
them on this machine — one per cwd a session has ever run in — and none of
them is under version control, reachable by another person, visible to CI, or
carried into a fresh container. A fact recorded there is a fact exactly one
agent on exactly one machine can see.

The fleet guidance already said so. `agents-md/base.md`'s "Finding your
unknowns" has carried "durable findings go in the **repo**, not agent memory"
since the section existed, and until this change it ended with the clause *"a
memory note is never the only copy."*

Nothing checked it, and on 2026-09-14 the session writing this ADR proved what
that is worth: it wrote
`~/.claude/projects/-home-passp-repos/memory/codex-agents-md-budget.md`, a note
whose facts — Codex's `project_doc_max_bytes` behaviour, the byte ceilings, the
gate wiring — existed **only** on a PR branch and in that one file. The rule
was in context, in the session that broke it, and a rule with nothing behind it
is a preference.

Three properties of the surface shaped what could be built:

- **Memory is per machine, not per repo.** A session in an unrelated project
  writes notes into the same tree. So a gate delivered per repo would check the
  repos that adopted it and miss the notes written everywhere else, which is
  most of them.
- **Choosing the home is judgment.** A fact belongs in an ADR, or a `docs/`
  page, or that repo's `## Repo-specific additions`, or a skill in the
  registry — and in *which* repo. Nothing mechanical can make that call.
- **Claude Code's `Stop` hook can refuse a stop.** Confirmed against
  https://code.claude.com/docs/en/hooks (2026-09-14): a Stop hook that prints
  `{"decision":"block","reason":"…"}` on stdout and exits 0 prevents Claude
  from stopping and hands `reason` back as the explanation for why it should
  continue. Its input carries `stop_hook_active`, "`true` when Claude Code is
  already continuing as a result of a stop hook", with the docs' own
  instruction to "check this value … to avoid blocking on a condition that will
  never resolve"; Claude Code ends the turn anyway after 8 consecutive blocks.

## Decision

1. **THE CONTRACT: every memory note whose `metadata.type` is not `user`
   carries `metadata.home`.** Its value is `<owner>/<repo>:<path>` (preferred)
   or `https://github.com/<owner>/<repo>/blob/<ref>/<path>`, naming the
   **committed** file that holds the durable copy. With a home, the note is a
   POINTER; the repo copy is the source of truth. A value in neither form names
   nothing resolvable and counts as no home at all.

2. **A `type: user` note is exempt.** Those are about the person — who they
   are, what they prefer, how they want to be written to — not about the work.
   No repo owns them, and demanding a committed copy would push personal
   preferences into a public repo, which is worse than the gap it closes.

3. **A home is DANGLING when a local clone of `<repo>` is found but `<path>`
   does not exist in it**, which catches the promotion that was promised and
   never made. Clone candidates, in order: `$CLAUDE_PROJECT_DIR/<repo>`,
   `$CLAUDE_PROJECT_DIR` itself when its basename is `<repo>`,
   `$(dirname "$CLAUDE_PROJECT_DIR")/<repo>`, `$HOME/repos/<repo>`. **When no
   clone is found the home is accepted unverified, silently** — see the
   residual in Consequences.

4. **SessionStart nudges, and says NOTHING when clean.** `.claude/hooks/memory-home.sh`
   scans every note under `<config>/projects/*/memory/*.md` (never `MEMORY.md`)
   and prints one line naming up to five homeless or dangling notes, `+K more`
   beyond that. A clean scan prints nothing at all. An always-on gate that
   greets the operator with a status line they did not ask for is a gate they
   learn to skim, and the next real finding scrolls past with it.

5. **Stop gates only what THIS session wrote.** SessionStart touches a marker
   at `<config>/memory-home/<session_id>`; Stop treats notes with an mtime at
   or after that marker's as this session's writes. No marker — the hook was
   registered mid-session, or the id sanitized to nothing — means the hook has
   no honest way to tell what this session wrote, so it does nothing. A gate
   that blocks on a guess is worse than no gate.

6. **One nudge, then out of the way.** When `stop_hook_active` is true the hook
   does not block again; it emits a `systemMessage` naming what is still
   unhomed and lets the turn end. The docs' loop-guard instruction is the whole
   reason that field exists, and an unconditional re-block would burn all eight
   of Claude Code's consecutive-block budget on the same sentence.

7. **Registration is USER level**, by `scripts/register-memory-home-hook.sh`
   into `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json` — two SEPARATE groups,
   `SessionStart` with matcher `startup|resume` and `Stop` with none, both
   `type: command`, `timeout: 15`. Memory is per machine, so the gate is per
   machine. The registrar mirrors `register-bootstrap-hook.sh` and
   `register-codex-hook.sh` exactly: append never overwrite, refuse an
   unparseable file with exit 3 and no write, semantic re-parse guard,
   idempotent by the needle `memory-home.sh`, and never `mkdir` the config
   directory (exit 4 instead).

8. **No automatic moving, ever.** Per decision 2 above the mechanism nudges and
   gates; the agent does the promotion. The hook never modifies or deletes a
   note, and it always exits 0: a missing python3 or PyYAML, an unreadable
   note, an unparseable frontmatter block each print one
   `memory-home: DEGRADED — <reason>` line and gate nothing. A note this script
   cannot parse is a note it has no standing to judge.

## Consequences

- **A hosted session without user-level settings gets no gate.** Cloud sessions
  on Claude Code for web do not read a local `~/.claude/settings.json`; hooks
  there come from the repo and from server-managed settings. So exactly the
  sessions whose machines are ephemeral — the ones where a memory note is most
  certainly the only copy — are the ones this does not cover. Delivering it per
  repo would cover them and would miss every note written outside an adopting
  repo, which is the larger set; that trade is decided here and is worth
  revisiting if repo-delivered user-scope hooks ever exist.
- **A killed session's note escapes the Stop gate entirely.** Stop does not run
  on a user interrupt, and it cannot run on a crash or a dropped connection —
  which on `ZENDA` is routine. The note is still caught, one session later, by
  the SessionStart nudge, because that arm scans everything rather than only
  this session's writes. The gate is therefore two-layered by necessity, not by
  belt-and-braces: neither arm alone is sufficient.
- **A home naming a repo with no local clone is unverifiable, and is accepted.**
  The alternative — network resolution — would put an authenticated GitHub call
  in front of every session start and would turn an offline machine into a
  degraded one. The residual is real: `owner/repo:docs/does-not-exist.md`
  passes on a machine that has not cloned `repo`. It is named here so a green
  scan is not mistaken for a proof.
- **Codex's memories are a different system and are out of scope.** Nothing
  here reads or writes anything under `~/.codex`, and ADR 0012's delivery is
  untouched. A Codex-side equivalent would be a new decision.
- **The registered command names an ABSOLUTE path**, so a moved or deleted
  checkout prints `memory-home: DEGRADED — no hook at <path>` at every
  SessionStart until the registrar is re-run. That is deliberate: the obvious
  `[ -f "$h" ] && exec bash "$h"` fails silently, and a policy gate that is off
  without saying so is the failure this whole ADR is about. `--hook` exists so
  a run from a worktree can wire the main checkout's copy rather than a path
  that disappears with the worktree.
- **The gate cannot tell a promotion from a deletion.** Deleting the note
  satisfies it exactly as well as promoting the fact does, and marking a
  work-fact `type: user` satisfies it dishonestly. Both are stated in the block
  reason as legitimate exits, because an agent that cannot end its turn will
  find one of them anyway; naming them is what keeps the choice visible in the
  transcript rather than silent.
