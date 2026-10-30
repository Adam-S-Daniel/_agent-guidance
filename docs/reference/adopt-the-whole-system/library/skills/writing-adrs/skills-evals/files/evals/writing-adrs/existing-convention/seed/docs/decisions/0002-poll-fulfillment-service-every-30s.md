# 0002. Poll the fulfillment service every 30 seconds

## Status

Superseded-by: 0003

## Context

Order Sync needs to notice a status change soon enough that customer
support isn't the one telling the customer their order shipped. Polling too
often risked tripping the fulfillment service's rate limit.

## Decision

Poll every 30 seconds, comfortably inside the documented rate limit.

## Consequences

Status changes can lag up to 30 seconds behind reality. See 0003 for why
this interval was later shortened.
