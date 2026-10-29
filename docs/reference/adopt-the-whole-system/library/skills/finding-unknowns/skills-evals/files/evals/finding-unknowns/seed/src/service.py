"""Small service example with an established export route."""

from collections.abc import Iterable, Mapping
from itertools import chain

from export.table import csv_chunks

COLUMNS = ("id", "label")
OLD_EXPORT_ROUTE = "/api/export.csv"


def export_response(rows: Iterable[Mapping[str, object]]) -> tuple[str, Iterable[str]]:
    """Return the existing route's content type and streamed body."""
    return "text/csv; charset=utf-8", csv_chunks(rows, COLUMNS)


def proposed_export_response(rows: Iterable[Mapping[str, object]]) -> tuple[str, Iterable[str]]:
    """Prototype route for the new export."""
    row_iter = iter(rows)
    first = next(row_iter, None)
    if first is None:
        return "text/csv; charset=utf-8", iter(())
    return export_response(chain((first,), row_iter))
