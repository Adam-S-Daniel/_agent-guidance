# Snapshot Keeper

Snapshot Keeper takes a nightly snapshot of the application database and
keeps a rolling set of them on the backup volume. See
`scripts/prune-snapshots.sh` for which snapshots are deleted, and when.
