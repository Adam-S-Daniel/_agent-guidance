#!/usr/bin/env python3
"""The weekly usage file `usage/weekly-local.json`: its schema, and the reader
the improvement planner calls.

`scripts/model_usage_census.py --weekly-out` writes it and
`scripts/publish_usage_census.sh` publishes it on the `persistent/eval-results`
branch (see `evals/usage/CENSUS.md`). The planner reads it through `load`,
`is_fresh` and `weight`, so a skill used often is improved first.

THE SCHEMA LIVES HERE, and only here: the census builds every row with
`row()` (or `hook_row()`) and every document with `document()`. The file
holds rows of more than one kind, so the reader ignores any kind it is not
asked about.

    {"generated_at": "2026-09-04T12:00:00Z", "schema": 1,
     "sources": [{"surface": "local", "scope": "transcripts",
                  "from": "<window start>Z", "to": "<now>Z",
                  "sessions_scanned": n}],
     "rows": [{"week": "2026-08-31", "kind": "skill", "name": "<name|other>",
               "repo": null, "surface": "local", "invocations": n,
               "sessions": n, "blocked": null, "overridden": null,
               "outcome": null}]}

A `hook` row counts local git-hook runs from the agent-checks log: `name`
is a check id (or `other`, or `all` for every run that week), `repo` an
allowlisted public `owner/name`, `other` or null, `invocations` the runs in
which the check ran and `blocked` the runs in which it failed; `sessions` is
null. A second source, `scope: "hooks"` with `runs_scanned`, is present only
when a hook log was read, so an absent one means unknown, not zero.

`week` is the Monday (UTC) of the ISO week. Rows are sorted by week, kind,
name, repo. `sessions` is the number of distinct transcript files that invoked the
name that week; `sessions_scanned` is how many transcript files were read
inside the window. Neither carries an id.

UNKNOWN IS NOT ZERO. `weight()` returns None when there is no census or it is
stale, and 0 only when a fresh census saw no invocation of the skill. A
planner that read a missing census as "nobody uses anything" would rank every
skill last on the strength of a machine that did not run.

Stdlib only, no network; the caller supplies `now`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from timeweeks import parse_ts, week_monday, window_weeks

SCHEMA = 1
SURFACE_LOCAL = "local"
ROW_FIELDS = ("week", "kind", "name", "repo", "surface", "invocations",
              "sessions", "blocked", "overridden", "outcome")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def row(iso_week_label: str, kind: str, name: str, invocations: int,
        sessions: int) -> dict:
    """One local row; the fields this census does not know are null."""
    return {"week": week_monday(iso_week_label), "kind": kind, "name": name,
            "repo": None, "surface": SURFACE_LOCAL, "invocations": invocations,
            "sessions": sessions, "blocked": None, "overridden": None,
            "outcome": None}


def hook_row(iso_week_label: str, name: str, repo: str | None, runs: int,
             blocked: int) -> dict:
    """One local hook row: runs in which check `name` ran, and failed."""
    return {**row(iso_week_label, "hook", name, runs, None), "repo": repo,
            "blocked": blocked}


def document(rows: list[dict], now: datetime, window_from: datetime,
             sessions_scanned: int, hook_runs_scanned: int | None = None) -> dict:
    """The whole file: exactly `generated_at`, `schema`, `sources`, `rows`."""
    window = {"surface": SURFACE_LOCAL, "from": window_from.strftime(STAMP),
              "to": now.strftime(STAMP)}
    sources = [{**window, "scope": "transcripts", "sessions_scanned": sessions_scanned}]
    if hook_runs_scanned is not None:
        sources.append({**window, "scope": "hooks", "runs_scanned": hook_runs_scanned})
    return {
        "generated_at": now.strftime(STAMP),
        "schema": SCHEMA,
        "sources": sources,
        "rows": sorted(rows, key=lambda r: (r["week"], r["kind"], r["name"],
                                            r["repo"] or "")),
    }


def _count(value) -> bool:
    return type(value) is int and value >= 0


def load(path) -> dict | None:
    """The document at `path`, or None when it is missing, is not JSON, or
    does not have the published shape."""
    try:
        with open(Path(path), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    if not isinstance(doc.get("generated_at"), str) or parse_ts(doc["generated_at"]) is None:
        return None
    sources, rows = doc.get("sources"), doc.get("rows")
    if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
        return None
    if not isinstance(rows, list):
        return None
    for entry in rows:
        if not isinstance(entry, dict):
            return None
        if not all(isinstance(entry.get(field), str) for field in ("week", "kind", "name")):
            return None
        if not _count(entry.get("invocations")):
            return None
    return doc


def is_fresh(doc, now: datetime, max_age_days: int = 14) -> bool:
    """True when `doc` was generated no more than `max_age_days` before `now`.

    A `generated_at` in the future is not fresh: every week of usage would
    fall outside the window (the roster's census check reads it the same way).
    """
    if not isinstance(doc, dict):
        return False
    generated = parse_ts(doc.get("generated_at"))
    if generated is None or generated > now:
        return False
    return now - generated <= timedelta(days=max_age_days)


def weight(doc, skill: str, now: datetime, weeks: int = 4) -> int | None:
    """Invocations of `skill` over the `weeks` ISO weeks ending with `now`'s,
    summed across every surface and repo.

    None when `doc` is None or stale (unknown); 0 when a fresh census has no
    row for it.
    """
    if doc is None or not is_fresh(doc, now) or not isinstance(doc.get("rows"), list):
        return None
    mondays = {week_monday(label) for label in window_weeks(now, weeks)}
    return sum(entry["invocations"] for entry in doc["rows"]
               if isinstance(entry, dict) and entry.get("kind") == "skill"
               and entry.get("name") == skill and entry.get("week") in mondays
               and _count(entry.get("invocations")))


def session_rate(doc, now: datetime, days: int = 28) -> float | None:
    """Transcript sessions scanned, scaled to `days` by the source window
    (`from` to `to`): the expected number of sessions in `days`, and so the
    expected uses of anything every session loads, such as the guidance.

    None when `doc` is None or stale, has no local transcripts source, or its
    window or count is unreadable (unknown, never 0).
    """
    if doc is None or not is_fresh(doc, now):
        return None
    for source in doc.get("sources", []):
        if source.get("scope") != "transcripts" or source.get("surface") != SURFACE_LOCAL:
            continue
        start, end = parse_ts(source.get("from")), parse_ts(source.get("to"))
        scanned = source.get("sessions_scanned")
        if start is None or end is None or end <= start or not _count(scanned):
            return None
        return scanned * (timedelta(days=days) / (end - start))
    return None
