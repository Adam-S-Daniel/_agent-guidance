#!/usr/bin/env python3
"""ISO-week arithmetic and RFC 3339 parsing, shared by the roster and the census.

`harness/roster.py` decides on enter and exit windows (2 weeks each as shipped); `scripts/model_usage
_census.py` buckets transcripts into the same weeks. They ran two copies of
this arithmetic and had to agree byte-for-byte on the week LABEL — a
disagreement would not raise, it would silently zero every usage share, which
is the failure mode with no symptom. One implementation, imported by both.

Stdlib only: the census runs on a durable machine from a plain checkout, with
no pip install between it and the transcripts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_ts(value) -> datetime | None:
    """Parse an RFC 3339 timestamp, `Z` included. None on anything unparseable.

    Naive timestamps are read as UTC: every producer in this feature writes
    UTC, and guessing a local zone here would shift a week boundary.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso_week(moment: datetime) -> str:
    """`2026-W36`. ISO year, NOT calendar year — they differ in early January,
    and mixing them puts one week of usage in a bucket nobody queries."""
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def week_monday(label: str) -> str:
    """`2026-W36` -> `2026-08-31`, the Monday (UTC) that ISO week starts on."""
    return datetime.strptime(f"{label}-1", "%G-W%V-%u").date().isoformat()


def window_weeks(now: datetime, count: int) -> list[str]:
    """The `count` ISO week labels ending with the one `now` falls in."""
    return [iso_week(now - timedelta(weeks=offset)) for offset in range(count)]


def window_start(now: datetime, count: int) -> datetime:
    """Midnight-ish lower bound of the `count`-week window.

    Deliberately generous — a whole extra week — because it is used to SKIP
    work (see the census's mtime prefilter), and a bound that is too tight
    drops real data while one that is too loose only costs a file read.
    """
    return now - timedelta(weeks=count + 1)


def complete_weeks_since(start: datetime, end: datetime,
                         most_recent: int) -> tuple[int, list[str]]:
    """(how many, the latest `most_recent` labels) of the ISO weeks that
    began at or after `start` and had ENDED by `end`, oldest label first.

    A week begins Monday 00:00 UTC and ends at the next one; both bounds are
    inclusive. The count is arithmetic rather than a walk, so a `start` years
    back costs nothing — it is the superseded-arm buffer's clock (#202),
    started by a successor's `created_at`, which is an input.
    """
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    first = (datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
             - timedelta(days=start.weekday()))
    if first < start:
        first += timedelta(weeks=1)
    if end < first:
        return 0, []
    count = (end - first) // timedelta(weeks=1)
    labels = [iso_week(first + timedelta(weeks=index))
              for index in range(max(0, count - max(0, most_recent)), count)]
    return count, labels
