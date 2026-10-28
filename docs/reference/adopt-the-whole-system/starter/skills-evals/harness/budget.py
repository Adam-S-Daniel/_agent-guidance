"""Budget file loader and the account meter helpers (guide 9.5).

evals/budget.yml, in fractions of the weekly allowance:

  weekly_cap, per_run_cap, per_run_overrun, stop_when_account_above,
  improve_reservation, weekly_reset_fallback

Used by run_eval.py (inside a run) and scripts/allowance_guard.py (before a
fire). No clock is read here: callers pass `now`.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import yaml

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_RESET_FALLBACK = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun) ([01][0-9]|2[0-3]):[0-5][0-9]$")
WEEK = timedelta(days=7)

BUDGET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["weekly_cap", "per_run_cap", "per_run_overrun",
                 "stop_when_account_above", "improve_reservation",
                 "weekly_reset_fallback"],
    "properties": {
        "weekly_cap": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "per_run_cap": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "per_run_overrun": {"type": "number", "minimum": 0, "maximum": 1},
        "stop_when_account_above": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "improve_reservation": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "weekly_reset_fallback": {"type": "string", "pattern": _RESET_FALLBACK.pattern[:-1] + r"\Z"},
    },
}


class BudgetError(Exception):
    """budget.yml is unreadable or invalid. Callers fail closed."""


def validate_budget(doc: object) -> dict:
    if not isinstance(doc, dict):
        raise BudgetError("budget must be a mapping")
    try:
        jsonschema.validate(doc, BUDGET_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise BudgetError(f"budget invalid at {list(exc.absolute_path)}: {exc.validator}") from None
    for key, value in doc.items():
        if isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)):
            raise BudgetError(f"{key}: not a finite number")
    if doc["per_run_cap"] > doc["weekly_cap"]:
        raise BudgetError("per_run_cap must be at most weekly_cap")
    if doc["improve_reservation"] > doc["weekly_cap"]:
        raise BudgetError("improve_reservation must be at most weekly_cap")
    return doc


def load_budget(path: str | Path) -> dict:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BudgetError(f"cannot read budget: {type(exc).__name__}") from None
    return validate_budget(doc)


# --- the weekly window -----------------------------------------------------

def last_fallback_reset(fallback: str, now: datetime) -> datetime:
    """The most recent `<weekday> HH:MM` (UTC) at or before `now`."""
    m = _RESET_FALLBACK.fullmatch(fallback)
    if not m:
        raise BudgetError("bad weekly_reset_fallback")
    day = WEEKDAYS.index(m.group(1))
    hour, minute = int(fallback[4:6]), int(fallback[7:9])
    now = now.astimezone(timezone.utc)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate -= timedelta(days=(candidate.weekday() - day) % 7)
    if candidate > now:
        candidate -= WEEK
    return candidate


def week_window(reset: datetime, now: datetime) -> tuple[datetime, datetime]:
    """The allowance week [start, end) holding `now`, given any one reset
    instant (past or future). Weeks are exactly seven days apart."""
    reset, now = reset.astimezone(timezone.utc), now.astimezone(timezone.utc)
    start = reset + ((now - reset) // WEEK) * WEEK
    return start, start + WEEK


def parse_instant(value: object) -> datetime:
    """An ISO-8601 string or epoch seconds (milliseconds when huge) to UTC."""
    if isinstance(value, bool):
        raise ValueError("not an instant")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("not an instant")
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("instant needs a timezone")
        return dt.astimezone(timezone.utc)
    raise ValueError("not an instant")


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the meter in a `claude -p --output-format json --verbose` message list --

def meter_reading(messages: object) -> dict | None:
    """The newest weekly reading, {"seven_day": float, "resets_at": iso|None},
    from the `rate_limit_event` messages, or None when there is none."""
    reading = None
    for msg in messages if isinstance(messages, list) else ():
        if not isinstance(msg, dict) or msg.get("type") != "rate_limit_event":
            continue
        info = msg.get("rate_limit_info")
        windows = info.get("unifiedWindows") if isinstance(info, dict) else None
        week = windows.get("seven_day") if isinstance(windows, dict) else None
        util = week.get("utilization") if isinstance(week, dict) else None
        if isinstance(util, bool) or not isinstance(util, (int, float)) \
                or not math.isfinite(util) or not 0 <= util <= 1:
            continue
        resets = None
        try:
            if week.get("resetsAt") is not None:
                resets = isoformat(parse_instant(week["resetsAt"]))
        except (ValueError, OverflowError, OSError):
            resets = None
        reading = {"seven_day": float(util), "resets_at": resets}
    return reading
