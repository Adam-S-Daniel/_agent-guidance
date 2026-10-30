# 0003. Store snapshots on a separate volume

- **Status:** Accepted
- **Date:** 2026-02-10

## Context

Snapshots were written to a directory on the same volume as the database's
own data files. A disk fault on that volume would have taken the database
and every snapshot of it at once.

## Decision

Write snapshots to a dedicated backup volume attached to the same host.

## Consequences

Losing the data volume no longer loses the snapshots. The backup volume is
one more thing to monitor and pay for, and losing the whole host still
loses both.

## Alternatives considered

Shipping each snapshot to object storage in another region was rejected for
now: restore time from there is hours, not minutes, and the off-host copy is
tracked as separate work.
