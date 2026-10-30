# 0002. Prune snapshots older than 30 days

- **Status:** Accepted
- **Date:** 2025-11-18

## Context

Nightly snapshots accumulate on the backup volume until something deletes
them. The volume holds roughly four months of snapshots, and nobody has
ever asked to restore from one more than two weeks old.

## Decision

Delete every snapshot older than 30 days, measured from the snapshot's own
timestamp, each night after the snapshot job runs.

## Consequences

The backup volume stays well under half full, and a month of history covers
every restore anyone has asked for. Anything older than 30 days is gone for
good.

## Alternatives considered

Keeping every snapshot forever was rejected: the volume fills in about four
months. Pruning by hand whenever the volume fills was rejected too: nobody
notices until a snapshot job fails for lack of space.
