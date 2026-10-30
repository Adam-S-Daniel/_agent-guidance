# Architecture decisions

This log records decisions worth remembering: what we picked, why, and what
it costs us. Skip it when a one-line code comment would do the job.

## Format

Each entry lives at `docs/decisions/NNNN-kebab-title.md`, numbered
sequentially from `0001` and zero-padded to four digits. Every entry has
these sections, in this order:

- **Status** - `Proposed`, `Accepted`, `Superseded-by: NNNN`, or `Rejected`.
- **Context** - the situation and constraints that forced a choice.
- **Decision** - what we picked, in one or two sentences.
- **Consequences** - what it costs us, good and bad.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-cache-order-status-in-redis.md) | Cache order status in Redis, not in-process memory | Accepted |
| [0002](0002-poll-fulfillment-service-every-30s.md) | Poll the fulfillment service every 30 seconds | Superseded-by: 0003 |
| [0003](0003-poll-fulfillment-service-every-10s.md) | Poll the fulfillment service every 10 seconds | Accepted |
