"""Bounded CSV rendering for exports already used by the service."""

import csv
import io
from collections.abc import Iterable, Iterator, Mapping


def csv_chunks(rows: Iterable[Mapping[str, object]], columns: tuple[str, ...]) -> Iterator[str]:
    """Yield a header and then one CSV record at a time."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
