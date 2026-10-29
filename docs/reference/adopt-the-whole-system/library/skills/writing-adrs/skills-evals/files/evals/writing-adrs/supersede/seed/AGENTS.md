# AGENTS.md

Snapshot Keeper takes a nightly snapshot of the application database and
keeps a rolling set of them on the backup volume.

## Conventions

- Keep `scripts/prune-snapshots.sh` as the only place a snapshot is deleted;
  do not prune from the snapshot job itself.
- Record user-visible changes in `CHANGELOG.md`.

### Architecture Decision Records

Non-obvious decisions live in [`docs/decisions/`](docs/decisions/README.md)
— read the index there before assuming a past choice was arbitrary.
