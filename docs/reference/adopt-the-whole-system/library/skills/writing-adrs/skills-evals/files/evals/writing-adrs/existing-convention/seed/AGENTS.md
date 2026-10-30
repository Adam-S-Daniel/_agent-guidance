# AGENTS.md

Order Sync polls the upstream fulfillment service and mirrors order status
into the local database.

## Conventions

- Keep `scripts/retry.sh` as the one retry helper for transient upstream
  failures; do not reimplement backoff logic elsewhere.
- Record user-visible changes in `CHANGELOG.md`.

### Architecture Decision Records

Non-obvious decisions live in [`docs/decisions/`](docs/decisions/README.md)
— read the index there before assuming a past choice was arbitrary.
