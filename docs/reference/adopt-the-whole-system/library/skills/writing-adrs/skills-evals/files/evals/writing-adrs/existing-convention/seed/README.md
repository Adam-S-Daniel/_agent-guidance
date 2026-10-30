# Order Sync

Order Sync polls the upstream fulfillment service and mirrors order status
into the local database. See `scripts/retry.sh` for the retry helper used by
every poller.
