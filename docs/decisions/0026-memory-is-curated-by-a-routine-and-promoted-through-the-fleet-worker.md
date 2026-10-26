# 0026 — Memory is curated by a routine, and its durable facts are promoted through the fleet worker

**Status:** Accepted (2026-10-26)

## Context

[ADR 0013](0013-a-memory-note-outside-a-repo-names-its-home.md) made every
Claude Code memory note that is not about the person name its repo home, and
gated a session that wrote one without it. It deliberately stopped there:
"no automatic moving", because choosing the home is judgment.
[ADR 0015](0015-audit-codex-memories-after-generation-not-at-stop.md) rejected
"automatically promote or delete every finding" for the same reason.

That left memory with no loop. Nothing checked notes for duplicates, for
claims that had expired, or for an index out of step with its files, and a
fact whose home was named was never checked to be in that home. Facts that
stayed in memory were invisible to the guidance loop: the prompt-audit sweep
([ADR 0017](0017-prompt-audit-runs-as-an-event-triggered-sweep.md)) and the
evals read repo files, never memory. Project memory, which a claude.ai
project's threads share, had no gate at all, because the ADR 0013 hooks are
registered per machine.

On 2026-10-26 the owner asked for a closed self-improvement loop covering
every aspect, with no human in it. This is the memory part of that loop.

## Decision

1. **An audit script reports; it changes nothing.**
   [`scripts/memory_audit.py`](../../scripts/memory_audit.py) reads one memory
   directory and reports notes with no home, unparseable notes,
   near-duplicates, lines whose dates are older than a cutoff, references the
   caller's resolver says are closed, merged or missing, an index out of step
   with the files, and files over 4 KB. Its resolver is a JSON file of
   states, so the script never touches the network and its tests run
   offline. Home parsing matches `memory-home.sh`.
2. **It proposes promotions.** For a note whose home is
   `<owner>/<repo>:<path>`, it compares each paragraph and list item of the
   note with the home file and emits the missing ones as a proposed edit:
   target file, section, text, and whether the edit is narrow (an existing
   `AGENTS.md` or `CLAUDE.md`).
3. **A routine acts on the report with no human**
   ([`memory-curation.md`](../routines/memory-curation.md)). A weekly
   project-scoped scheduled task curates project memory; a weekly task on
   the owner's durable machine curates
   `~/.claude/projects/*/memory/`. Each merges duplicates, drops claims the
   note itself marked temporary once they lapse, repairs the index, and
   flags everything else for the owner. It never deletes a note: it moves
   one into `archive/<date>/` inside the memory directory.
4. **Promotions go through the fleet worker, not straight into a repo.** The
   routine files one `agent-ready` issue per repo, titled
   `Memory promotion <date>: ...`, after a privacy rewrite. The fleet
   worker's issue pass turns it into a PR. A PR that only edits the
   repo-specific half of an `AGENTS.md` or a `CLAUDE.md` is a narrow
   instruction fix under
   [ADR 0025](0025-a-cloud-fleet-worker-merges-narrow-instruction-fixes.md):
   the fleet worker merges it after green checks and an independent `CLEAN`
   review, which also checks the text for personal data. Any other target
   goes to the owner.
5. **A git hook comes first.** The owner's rule of 2026-10-26: a git hook is
   the first choice whenever possible, ahead of a skill, guidance text or
   memory. A promoted rule that is mechanically checkable at commit or push
   time becomes an item of kind `agent-check`, a sketched new check for
   `.githooks/agent-checks`
   ([ADR 0021](0021-the-sync-delivers-agent-checks-to-every-fleet-repo.md)),
   filed in this repo, which holds the runner's canonical template. Text is
   promoted only for a rule no such check can decide. An `agent-check` PR
   changes what every fleet repo runs, so it is never a narrow fix: it goes
   to the owner.

This supersedes ADR 0013's "no automatic moving" and ADR 0015's rejection of
automatic promotion for Claude Code memory, and only through this routine:
the hooks still never modify a note.

## Consequences

- A durable fact reaches its repo file, where the sweep and the evals can
  measure it, without the owner. A merged promotion is then found in the
  file, so the audit stops proposing it.
- The home is still chosen by whoever wrote the note or by the routine. A
  wrong home lands a fact in the wrong file. A narrow edit is small and
  reverted with one PR, as ADR 0025 accepted.
- Promotion publishes memory text into repos that may be public. The privacy
  rewrite in the routine and the reviewer's check are the guards, and both
  are LLM judgment. A fact that cannot be stated without personal data stays
  in memory.
- Duplicate detection is lexical (word-sequence similarity at 0.8 by
  default). Two notes saying the same thing in different words are missed;
  two notes sharing a template may be paired. The routine reads both notes
  before merging.
- Which rules are checkable is the routine's judgment; the audit script
  only finds text missing from a home and does not classify it. A rule
  wrongly kept as text is caught by nothing; a wrong `agent-check` sketch
  is caught by the owner's review.
- A date is stale by age alone, and an old date on a decision is history, so
  most stale findings are flagged, not changed. The flag list can grow; the
  owner prunes it or a later decision tightens the drop rule.
- The machine task runs only while that machine is on. Missed runs delay
  curation; they lose nothing.
- An archived note is out of every session's context but stays on disk, so
  the archive grows until someone clears it.
