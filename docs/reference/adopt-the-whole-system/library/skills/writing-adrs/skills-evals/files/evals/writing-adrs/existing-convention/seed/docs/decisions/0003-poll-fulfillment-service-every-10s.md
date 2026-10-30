# 0003. Poll the fulfillment service every 10 seconds

## Status

Accepted

## Context

Support tickets showed customers noticing a shipped order before Order Sync
did, with the 30-second interval from 0002 landing near the middle of that
gap often enough to matter. The fulfillment service's documented rate limit
turned out to have headroom for a shorter interval than 0002 assumed.

## Decision

Poll every 10 seconds instead of 30.

## Consequences

Status changes surface roughly 3x faster. Request volume to the fulfillment
service triples too, though it stays well under the rate limit. Supersedes
0002.
