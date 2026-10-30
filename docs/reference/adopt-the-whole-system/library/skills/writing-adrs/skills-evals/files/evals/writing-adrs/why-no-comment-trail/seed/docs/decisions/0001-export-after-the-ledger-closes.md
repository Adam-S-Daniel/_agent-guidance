# 0001. Export after the ledger closes, not at midnight

- **Status:** Accepted
- **Date:** 2025-09-15

## Context

Late postings for a day keep arriving until the ledger closes at 01:30 UTC.
An export taken at midnight missed them, and the bank flagged the missing
entries as unreconciled the next morning.

## Decision

Run the export for a day at 02:00 UTC the following morning, after the
ledger has closed.

## Consequences

Every posting for the day is in its export. The bank receives the file two
hours later than before, which is still well ahead of its 06:00 UTC pickup.

## Alternatives considered

Exporting at midnight and sending a correction file for late postings was
rejected: the bank's service has no way to apply a correction to a file it
has already reconciled.
