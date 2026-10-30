# 0001. Take snapshots with pg_basebackup, not pg_dump

- **Status:** Accepted
- **Date:** 2025-11-04

## Context

The application database is about 40 GB. A logical dump of it takes over
two hours and holds a long-running transaction open the whole time, which
bloats the busiest tables while the dump runs.

## Decision

Take the nightly snapshot as a physical base backup with `pg_basebackup`,
not as a logical dump with `pg_dump`.

## Consequences

A snapshot finishes in about fifteen minutes and holds no long transaction
open. A base backup restores only onto the same major PostgreSQL version,
so a major-version upgrade needs its own dump at upgrade time.

## Alternatives considered

A nightly `pg_dump` was rejected for the two-hour transaction described
above. Filesystem-level volume snapshots were rejected because the hosting
provider does not offer them on this volume class.
