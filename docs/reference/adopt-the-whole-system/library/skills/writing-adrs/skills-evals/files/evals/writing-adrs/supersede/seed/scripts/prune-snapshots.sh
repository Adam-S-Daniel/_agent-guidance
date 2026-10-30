#!/usr/bin/env bash
# prune-snapshots.sh: keep the newest 14 snapshots and delete the rest.
#
# Changed after the 2026-05 incident (PR #58): the nightly snapshot job
# failed silently for five weeks while this script kept deleting everything
# older than 30 days, so by the time anyone looked there was not a single
# snapshot left to restore from. Keeping the age rule and adding an alert on
# a failed snapshot job was ruled out - the alert is one more thing that can
# fail silently, and the age rule would still delete the last good snapshot
# behind it. Never pruning at all was ruled out too - the backup volume
# fills in about four months. Counting snapshots instead of days means a
# stalled snapshot job can no longer prune its way down to nothing.
set -euo pipefail

snapshot_dir="${SNAPSHOT_DIR:-/var/backups/snapshots}"

# Retention policy: docs/decisions/0002-prune-snapshots-older-than-30-days.md
keep=14

# Snapshot names start with their UTC timestamp, so a reverse sort is newest
# first.
mapfile -t snapshots < <(find "$snapshot_dir" -maxdepth 1 -name '*.snap' -printf '%f\n' | sort -r)

for snapshot in "${snapshots[@]:keep}"; do
  rm -f -- "$snapshot_dir/$snapshot"
done
