#!/usr/bin/env bash
# export-ledger.sh: write one day's general-ledger entries as a CSV file for
# the bank's reconciliation service.
#
# Usage: export-ledger.sh YYYY-MM-DD
set -euo pipefail

day="${1:?usage: export-ledger.sh YYYY-MM-DD}"
out="${EXPORT_DIR:-/var/exports}/ledger-$day.csv"

# Amounts are integer cents: docs/decisions/0002-write-amounts-as-integer-cents.md
{
  printf '%s\r\n' "date,account,amount_cents,memo"
  ledger-cli entries --date "$day" --format csv-rows |
    while IFS= read -r line; do
      printf '%s\r\n' "$line"
    done
} > "$out"
