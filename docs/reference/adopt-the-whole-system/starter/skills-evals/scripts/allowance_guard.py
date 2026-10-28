#!/usr/bin/env python3
"""allowance_guard.py: the budget check that runs before every fire (guide 9.5).

It runs in Actions with no Claude login, so it reads files only:

  --results DIR   a checkout of the results branch (persistent/eval-results),
                  holding routine-results/<run id>/run.json for each ingested run
  --fires FILE    JSON list of the fire workflow's runs this week, from the
                  GitHub API (the wrapper step builds it with `gh api`):
                  [{"name": "fire eval 20261028T120000Z-1a2b3c",
                    "created_at": "2026-10-28T12:00:05Z", "fired": true}, ...]
  --budget FILE   evals/budget.yml
  --now ISO       the current instant (a flag so tests need no clock)

What it adds up, for the allowance week that holds --now:
  * an ingested run: its measured cost, (meter_after - meter_before) on the
    seven_day window, plus per_run_overrun;
  * an eval, scaffold or draft fire without ingested results: the full
    per_run_cap, for the rest of the week, however it ended (a failed fire
    may have spent it all; budget.yml has no separate key for these lanes);
  * an improve fire: improve_reservation, whatever its outcome.
It refuses when one more reservation (improve_reservation for an improve
fire, per_run_cap for any other) does not fit under weekly_cap, or
when the newest meter reading this week is at stop_when_account_above.

Subcommands:  reset  prints the week window;  check  decides;
              room   prints {"room", "per_run_cap", "improve_reservation"},
                     the guard file plan_week.py reads.
Exit codes:   0 admitted, 1 refused, 2 error (fail closed: the caller must not fire).
Output is JSON with numbers and fixed codes only, never file contents.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.budget import (BudgetError, isoformat, last_fallback_reset, load_budget,  # noqa: E402
                            parse_instant, week_window)

RUN_ID = re.compile(r"^([0-9]{8}T[0-9]{6}Z)-[0-9a-f]{6}$")
FIRE_NAME = re.compile(r"^fire (eval|improve|scaffold|draft) ([0-9]{8}T[0-9]{6}Z-[0-9a-f]{6})$")
KINDS = ["eval", "improve", "scaffold", "draft"]
EPS = 1e-9


class ReadError(Exception):
    """Anything unreadable or malformed. The guard fails closed."""


def run_time(run_id: str) -> datetime:
    m = RUN_ID.fullmatch(run_id)
    if not m:
        raise ReadError("bad run id")
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _frac(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
        raise ReadError("bad meter value")
    return float(v)


def read_run(results: Path, run_id: str) -> dict | None:
    """The ingested run.json for `run_id`, or None when it was never ingested."""
    path = results / "routine-results" / run_id / "run.json"
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReadError("unreadable run.json") from None
    if not isinstance(doc, dict) or doc.get("run_id") != run_id:
        raise ReadError("run.json does not match its directory")
    for key in ("meter_before", "meter_after"):
        m = doc.get(key)
        if m is not None:
            if not isinstance(m, dict):
                raise ReadError("bad meter")
            _frac(m.get("seven_day"))
    return doc


def measured_cost(doc: dict, budget: dict) -> float:
    """(after - before) on the weekly window plus per_run_overrun. A run whose
    meter is missing, or crossed a reset, cannot be measured: it costs the cap."""
    b, a = doc.get("meter_before"), doc.get("meter_after")
    if not b or not a or b.get("seven_day") is None or a.get("seven_day") is None \
            or b.get("resets_at") != a.get("resets_at"):
        return budget["per_run_cap"]
    return max(0.0, a["seven_day"] - b["seven_day"]) + budget["per_run_overrun"]


def run_dirs(results: Path) -> list[str]:
    base = results / "routine-results"
    if not base.exists():
        return []
    try:
        names = sorted(p.name for p in base.iterdir() if p.is_dir())
    except OSError:
        raise ReadError("cannot list results") from None
    if any(not RUN_ID.fullmatch(n) for n in names):
        raise ReadError("unexpected directory in routine-results")
    return names


def find_reset(results: Path, budget: dict, now: datetime) -> datetime:
    """resetsAt from the newest ingested summary's meter, else the fallback."""
    for rid in reversed(run_dirs(results)[-8:]):
        doc = read_run(results, rid)
        for key in ("meter_after", "meter_before"):
            m = (doc or {}).get(key)
            if m and m.get("resets_at"):
                try:
                    return parse_instant(m["resets_at"])
                except (ValueError, OverflowError, OSError):
                    raise ReadError("bad resets_at") from None
    return last_fallback_reset(budget["weekly_reset_fallback"], now)


def read_fires(path: Path) -> list[dict]:
    try:
        fires = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReadError("unreadable fires file") from None
    if not isinstance(fires, list):
        raise ReadError("fires must be a list")
    out = []
    for f in fires:
        if not isinstance(f, dict) or not isinstance(f.get("fired"), bool):
            raise ReadError("bad fire entry")
        m = FIRE_NAME.fullmatch(f.get("name") if isinstance(f.get("name"), str) else "")
        if not m:
            raise ReadError("bad fire name")
        try:
            created = parse_instant(f.get("created_at"))
        except (ValueError, TypeError):
            raise ReadError("bad fire time") from None
        out.append({"mode": m.group(1), "run_id": m.group(2), "created": created, "fired": f["fired"]})
    return out


def tally(results: Path, fires: list[dict], budget: dict, start: datetime, end: datetime) -> dict:
    used = 0.0
    counted: set[str] = set()
    seen: set[tuple[str, str]] = set()
    n = {"ingested": 0, "unresolved_fires": 0, "improve_fires": 0}
    for f in sorted(fires, key=lambda x: x["created"]):
        if not f["fired"] or not start <= f["created"] < end:
            continue
        key = (f["mode"], f["run_id"])
        if f["mode"] == "improve":
            used += budget["improve_reservation"]
            n["improve_fires"] += 1
            continue
        doc = read_run(results, f["run_id"]) if key not in seen else None
        seen.add(key)
        if doc is not None:
            used += measured_cost(doc, budget)
            counted.add(f["run_id"])
            n["ingested"] += 1
        else:
            used += budget["per_run_cap"]
            n["unresolved_fires"] += 1
    newest = None
    for rid in run_dirs(results):
        if not start <= run_time(rid) < end:
            continue
        doc = read_run(results, rid)
        if doc is None:
            continue
        newest = doc
        if rid not in counted:  # ingested with no matching fire entry
            used += measured_cost(doc, budget)
            n["ingested"] += 1
    return {"used": used, "newest": newest, **n}


def check(results: Path, fires_file: Path, budget: dict, now: datetime, kind: str,
          reset_at: datetime | None = None) -> tuple[int, dict]:
    reset = reset_at or find_reset(results, budget, now)
    start, end = week_window(reset, now)
    t = tally(results, read_fires(fires_file), budget, start, end)
    reservation = budget["improve_reservation"] if kind == "improve" else budget["per_run_cap"]
    out = {"window_start": isoformat(start), "window_end": isoformat(end), "kind": kind,
           "used": round(t["used"], 6), "reservation": reservation,
           "weekly_cap": budget["weekly_cap"], "ingested": t["ingested"],
           "unresolved_fires": t["unresolved_fires"], "improve_fires": t["improve_fires"]}
    meter = ((t["newest"] or {}).get("meter_after") or {}).get("seven_day")
    if meter is not None and meter >= budget["stop_when_account_above"]:
        out.update(admit=False, reason="account_above", account_meter=meter)
        return 1, out
    if t["used"] + reservation > budget["weekly_cap"] + EPS:
        out.update(admit=False, reason="weekly_cap")
        return 1, out
    out.update(admit=True, reason="fits")
    return 0, out


def room(results: Path, fires_file: Path, budget: dict, now: datetime,
         reset_at: datetime | None = None) -> dict:
    """What plan_week.py reads: the share of weekly_cap still free this week
    (0 once the account meter is at stop_when_account_above), and the two
    reservations it plans with."""
    reset = reset_at or find_reset(results, budget, now)
    start, end = week_window(reset, now)
    t = tally(results, read_fires(fires_file), budget, start, end)
    free = max(0.0, budget["weekly_cap"] - t["used"])
    meter = ((t["newest"] or {}).get("meter_after") or {}).get("seven_day")
    if meter is not None and meter >= budget["stop_when_account_above"]:
        free = 0.0
    return {"room": round(free, 6), "per_run_cap": budget["per_run_cap"],
            "improve_reservation": budget["improve_reservation"],
            "window_start": isoformat(start), "window_end": isoformat(end)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("reset", "check", "room"):
        s = sub.add_parser(name)
        s.add_argument("--results", required=True)
        s.add_argument("--budget", required=True)
        s.add_argument("--now", required=True)
        if name in ("check", "room"):
            s.add_argument("--fires", required=True)
            s.add_argument("--reset-at", help="override the reset instant (tests)")
        if name == "check":
            s.add_argument("--kind", choices=KINDS, default="eval")
    args = p.parse_args(argv)
    try:
        budget = load_budget(args.budget)
        now = parse_instant(args.now)
        results = Path(args.results)
        reset_at = parse_instant(args.reset_at) if getattr(args, "reset_at", None) else None
        if args.cmd == "reset":
            start, end = week_window(reset_at or find_reset(results, budget, now), now)
            print(json.dumps({"window_start": isoformat(start), "window_end": isoformat(end)}))
            return 0
        if args.cmd == "room":
            print(json.dumps(room(results, Path(args.fires), budget, now, reset_at)))
            return 0
        code, out = check(results, Path(args.fires), budget, now, args.kind, reset_at)
        print(json.dumps(out))
        return code
    except (ReadError, BudgetError, ValueError, OSError) as exc:
        print(json.dumps({"admit": False, "reason": "error", "error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
