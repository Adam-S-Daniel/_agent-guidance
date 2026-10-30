#!/usr/bin/env bash
# retry.sh: retry a transient command failure with capped exponential backoff.
#
# Added after the 2026-03 outage (PR #41): a single 503 from
# https://example.com/api/orders was retried in a tight loop with no cap and
# no delay, pinning a worker for 40 minutes and starving the queue behind it.
# Retrying forever with a fixed delay was ruled out - it would have masked
# the same runaway-retry failure again, just slower. Failing fast with no
# retry at all was ruled out too - it would turn every transient blip into a
# customer-facing error. Capping attempts at 5 and backing off exponentially
# (1s, 2s, 4s, 8s, 16s) bounds the damage either way.
set -euo pipefail

max_attempts=5
delay=1
attempt=1

while true; do
  if "$@"; then
    exit 0
  fi
  if (( attempt >= max_attempts )); then
    echo "retry.sh: giving up after ${attempt} attempts: $*" >&2
    exit 1
  fi
  sleep "$delay"
  delay=$(( delay * 2 ))
  attempt=$(( attempt + 1 ))
done
