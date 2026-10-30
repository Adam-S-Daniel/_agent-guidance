# AGENTS.md

Ledger Export writes each day's general-ledger entries to a CSV file that
the bank's reconciliation service picks up the next morning.

## Conventions

- Keep `scripts/export-ledger.sh` as the only writer of the export file.
- Record user-visible changes in `CHANGELOG.md`.

### Architecture Decision Records

Non-obvious decisions live in [`docs/decisions/`](docs/decisions/README.md)
— read the index there before assuming a past choice was arbitrary.
