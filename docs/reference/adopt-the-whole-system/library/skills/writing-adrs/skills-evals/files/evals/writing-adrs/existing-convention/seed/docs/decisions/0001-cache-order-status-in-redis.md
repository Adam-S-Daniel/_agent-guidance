# 0001. Cache order status in Redis, not in-process memory

## Status

Accepted

## Context

Order Sync runs as multiple worker processes behind a load balancer. Each
worker polled the fulfillment service independently and kept the last-seen
status in memory, so a request routed to a different worker than the one
that last polled could see stale or missing status.

## Decision

Cache order status in a shared Redis instance, keyed by order ID, so every
worker reads the same value regardless of which one last polled.

## Consequences

Status reads are now consistent across workers. It adds a dependency on
Redis being reachable; a Redis outage degrades status reads instead of
serving stale data, which is an acceptable trade for consistency.
