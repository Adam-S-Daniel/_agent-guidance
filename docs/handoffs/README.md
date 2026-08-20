# Handoffs

A handoff is the durable form of a session's unfinished work: what was
established, what is still true, and what to do next — written so a session that
was never in the room can pick it up.

It exists because the expensive part of a task is the investigation, not the
diff. A fresh session regenerates a patch quickly; it cannot cheaply re-derive
why the obvious fix was wrong, which lane a green check was actually testing, or
which of two plausible mechanisms the evidence ruled out. Chat scrollback does
not survive; a scratchpad does not survive; a repo file does.

## Conventions

- One directory per handoff, `YYYY-MM-DD-<short-topic>/`.
- A `README.md` that states current state **with the timestamp it was verified**,
  and says plainly what is merged, what is open, and what is blocked on what.
- Task files a session can be pointed at directly.
- Where several tasks share hard-won facts, a `SHARED-CONTEXT.md` so each task
  file stays about its task.

## Two rules that make them worth writing

**Date the state and say it was verified, not recalled.** A handoff whose status
block is written from memory is worse than none: it reads authoritative and sends
someone to re-do finished work, or to skip a step that never happened.

**Record the corrections, not just the conclusions.** The claims that turned out
wrong are the ones a fresh session is most likely to re-derive and act on. Every
handoff here carries a section for them.

## Index

- [`2026-08-20-gitleaks-and-silent-failures/`](2026-08-20-gitleaks-and-silent-failures/README.md)
  — a gitleaks false positive traced to a skill's NAME, and the several
  mechanisms that reported green while checking nothing.
