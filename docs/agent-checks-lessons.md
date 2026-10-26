# Agent-checks lessons ledger

One row per lesson the lesson-check lane has handled
([ADR 0027](decisions/0027-lessons-become-agent-checks-through-the-fleet-worker.md);
procedure in [`fleet-worker.md`](routines/fleet-worker.md), "The lesson-check
pass"). A run skips any lesson id already listed here, so this file is also
the record of what was turned down and why.

**Privacy.** This repo is public and some lessons come from a private repo.
A row holds the lesson id and a generic reason only, never a lesson's
examples or subject text.

- **Source:** `private proposal`, an issue URL, or `memory audit`.
- **Verdict:** `added`, `rejected: <reason>`, or `not checkable`.

| Date | Lesson id | Source | Verdict | PR |
|------|-----------|--------|---------|----|
| 2026-10-26 | closing-keyword-in-commit-message | https://github.com/Adam-S-Daniel/skills-evals/issues/424 | not checkable | none |
