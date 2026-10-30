# 0002. Write amounts as integer cents, not decimal strings

- **Status:** Accepted
- **Date:** 2025-10-01

## Context

The ledger stores amounts as integer cents. Formatting them as decimal
strings went through a float and produced off-by-one-cent values on a few
large entries.

## Decision

Write every amount in the export as an integer number of cents, in a column
named `amount_cents`.

## Consequences

No amount passes through a float on its way to the file. Anyone reading the
file by eye has to divide by 100.

## Alternatives considered

Formatting decimals with integer arithmetic instead of a float was rejected:
the bank's column spec accepts cents directly, so the formatting code would
exist only to be parsed back.
