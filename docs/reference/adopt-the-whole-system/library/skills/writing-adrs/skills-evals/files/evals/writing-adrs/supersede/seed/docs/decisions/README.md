# Architecture decisions

This log records the choices a reader of the code would otherwise
second-guess: what we picked, why, and what it costs us. A one-line code
comment is enough for everything else.

## Format

Each entry lives at `docs/decisions/NNNN-kebab-title.md`, numbered
sequentially from `0001` and zero-padded to four digits. Every entry opens
with a `# NNNN. Title` line and two metadata lines:

- **Status:** `Proposed`, `Accepted`, `Superseded by NNNN`, or `Rejected`.
- **Date:** the day the entry was written, as `YYYY-MM-DD`.

Then come these sections, in this order:

- **Context** - the situation and constraints that forced a choice.
- **Decision** - what we picked, in one or two sentences.
- **Consequences** - what it costs us, good and bad.
- **Alternatives considered** - what else was on the table, and why not.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-take-snapshots-with-pg-basebackup.md) | Take snapshots with pg_basebackup, not pg_dump | Accepted |
| [0002](0002-prune-snapshots-older-than-30-days.md) | Prune snapshots older than 30 days | Accepted |
| [0003](0003-store-snapshots-on-a-separate-volume.md) | Store snapshots on a separate volume | Accepted |
