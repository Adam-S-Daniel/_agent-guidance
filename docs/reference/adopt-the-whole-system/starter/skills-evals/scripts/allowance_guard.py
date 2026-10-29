#!/usr/bin/env python3
"""Keep automated evals inside their share of the weekly allowance (#68).

The owner's standing decision (2026-10-10,
https://github.com/Adam-S-Daniel/skills-evals/issues/68#issuecomment-6097945979):
all automated evals stay within 30% of the Claude subscription's weekly
allowance. `evals/budget.yml` holds the numbers (read by harness/budget.py);
this script is the layer that runs BEFORE any spend:

  check  routine-eval-fire.yml runs it before the fire step, in every mode,
         and a refusal stops the fire. A fire may go only when
         spent + pending + one run's reservation <= weekly_cap.
  fits   prints how many more fires fit now; scripts/plan_week.py spends
         `room` itself, at each fire's historical cost, so the weekly sweep
         plans only what fits.

What counts, since the account's last weekly reset (`window_start`, at most
seven days back; until 2026-10-26 it was a rolling seven days, which charged
one week's sanctioned push to the next):

  * spent: every ingested routine run on `persistent/eval-results`
    (`routine-results/<run id>/`, scripts/ingest_routine_results.py) whose
    run id's timestamp falls in the window, each charged its measured
    `usage.meter_delta.windows.seven_day.delta` plus `per_run_overrun` (the
    meter is read as a call begins, so the last call is outside the delta),
    or `per_run_cap + per_run_overrun` when there is no measured delta (null,
    a window reset, a run of one agent call, an unreadable summary). Every
    summary of one run_eval invocation carries the same run-level block, so
    an invocation is counted once however many summaries it wrote.
  * pending: successful runs of routine-eval-fire.yml in the window that
    have no ingested results yet. One younger than `PENDING_FULL_HOLD` (45
    minutes) is reserved at `per_run_cap + per_run_overrun`; an older one is
    charged `per_run_overrun` only for the rest of the week. A routine run
    finishes within minutes (healthy runs take 4-12, ingest a few more) and
    the routine's own prompt stops an eval or scaffold run at 30 minutes, so
    a fire with no results 45 minutes later failed or never started (or is a
    scaffold or improve run, which never lands in routine-results/), and the
    in-run stops (`per_run_cap`,
    `stop_when_account_above` in harness/run_eval.py) still bound what it
    could have spent. An ingested run is matched to a fire by its run id's
    timestamp (the fire workflow stamps it at fire time), so a run from the
    last `PENDING_FULL_HOLD` frees one recent fire; runs older than that free
    old fires.

Improve fires (thresholds doc, plumbing gap 2): an improve run's results
never land in `routine-results/`, so until 2026-10-26 it was held at one
eval fire's reservation for `PENDING_FULL_HOLD` and the overrun after that,
while it runs about twelve eval fires' worth of agent trials. Now every
successful improve fire of the window (its run title, `Fire eval routine:
improve <skill>`, says so; scripts/improve_outcomes.py reads it) is charged
`improve_charge` for the whole window and is not matched against ingested
runs: `IMPROVE_FIRE_UNITS` times the mean measured cost of the ingested eval
runs (`eval_fire_cost`, never more than one reservation each), or times the
full reservation while nothing is measured. `check --mode improve` needs room
for that charge before an improve fire goes. A guidance section's improve
fire (`Fire eval routine: guidance-improve <section>`,
scripts/propose_guidance_edit.py: the same baseline-and-candidate loop at
three trials per fixture, and its results never land in `routine-results/`
either) is counted, held and checked exactly as an improve fire
(`improve_outcomes.charged_fires`, `IMPROVE_MODES`).

That charge is an estimate and was never corrected by measurement: on
2026-10-27 two improve fires were held at 21 points against a 30-point cap
while the account meter last read 5% used, and every fire was refused for
the rest of the week. An improve fire that started `IMPROVE_MAX_RUN` or more
before the newest usable meter snapshot had finished, so its real cost is
already inside that snapshot's account-wide `utilization`: those "covered"
fires are held together at no more than that reading. The meter covers the
whole account, so the bound overcounts, the safe direction. Fires not yet
covered, and every fire when no snapshot is usable, keep the full charge.

That reading also holds the measured weekly delta of every ingested eval run
whose snapshots all precede it, and `spent` already charges those runs, so
the covered fires are held at the reading less those deltas (never below
zero). Without that, on 2026-10-28 the eval runs were counted twice and a
guidance-improve fire was refused at 28% account use against a 55-point
cap. It stays safe even when runs overlapped and their deltas count the
same use twice: spent plus the hold is still at least the eval runs' own
`per_run_overrun` plus the whole reading, and each run's unmeasured last
call is what that overrun is for.
Only an ingested eval run brings a newer snapshot, so when those full
charges leave no room for one, one eval fire may still go to bring it
(`refresh`); without that the holds lasted the week (2026-10-27).

Bare fires (ADR 0012, bare baseline addendum, 2026-10-27): `arms: bare`
runs one arm of an in-place fixture. Nothing here is costed per arm: a fire
is one run, reserved at `per_run_cap + per_run_overrun`, the most the in-run
stop lets one run_eval invocation spend whatever its arms, and an ingested
run is charged its own measured delta. So a bare fire is held, checked and
charged as one run, exactly as an eval fire of both arms, never as two.
Its title (`Fire eval routine: eval bare <fixture> <model>`) is not an
improve title, so it is never charged `improve_charge`.

Pace room (the owner, 2026-10-25: "The percentage of weekly should allow for more
usage if I'm approaching end of week and under pace"): the allowance the
account has not used is released as its week ends. The newest account meter
snapshot in those ingested summaries (`usage.measured.meter.snapshots`) gives
the seven-day utilization and when that window resets; the room is
`max(0, stop_when_account_above - utilization)` times the share of the week
gone, less pending (fires still without results, whose spend the snapshot may
not show yet, so every fire shrinks the room until its results are ingested),
and a fire fits when it fits under the larger of that and the fixed cap left.
So the cap is a floor, early in a week the pace room is near nothing,
and the in-run `stop_when_account_above` stop still bounds every run. A
snapshot more than `PACE_MAX_AGE` old, ahead of the clock, with a reset
already past or more than a week out, or with a value out of range gives no
pace room: the fixed cap alone, the safe direction.

The meter is account-wide, so the owner's own use during a run is counted as eval
spend: an overcount, the safe direction.

The results branch is bot-written and untrusted (AGENTS.md, "Automation vs
branch protection"): files are read as data, never executed; a symlink, an
oversize file or a summary that does not parse is charged the full
reservation rather than trusted. Output is numbers only. The GitHub API call
is isolated in `count_successful_fires` (called once per age bound), so tests
pass the counts in; an API
error refuses the fire (fail closed) and prints only the status code. Tests
pass `now` in too: nothing here reads the clock except `main`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import budget as budget_file  # noqa: E402
import improve_outcomes  # noqa: E402

#: The routine run id the fire workflow generates (routine-eval-fire.yml's
#: validate step), and the directory ingest names after it.
RUN_ID_RE = re.compile(r"(?P<stamp>[0-9]{8}T[0-9]{6}Z)-[0-9a-f]{6}")
DEST_ROOT = "routine-results"
WINDOW = timedelta(days=7)
#: One weekly reset of the account's allowance (every meter snapshot of the
#: week ending then read `seven_day.resetsAt` 2026-10-26T17:00:00Z); the
#: others are whole weeks from it. Update it if the account's reset moves.
ACCOUNT_WEEK_RESET = datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
#: A fire without results younger than this is held at the full reservation;
#: an older one failed or never started, and is charged per_run_overrun only.
#: It was 24 hours until 2026-10-26, when fires whose routine never started
#: held a day's reservation each and kept a week-end ramp from using the
#: allowance the owner had left; healthy runs land within about 15 minutes. It was
#: 2 hours until 2026-10-27, when three fires that failed at startup held
#: about 31 points against a 30-point cap while measured use was about 5, and
#: blocked every fire for two hours. The routine stops an eval or scaffold run
#: at 30 minutes, so 45 minutes covers that limit plus the push and ingest.
PENDING_FULL_HOLD = timedelta(minutes=45)
#: ingest_routine_results.SIZE_CAPS["summary.json"]: a bigger file was never
#: ingested by that validator, so it is not read here either.
SUMMARY_MAX_BYTES = 64 * 1024
FIRE_WORKFLOW = "routine-eval-fire.yml"
#: Float noise, not spend: the meter's readings carry six decimals
#: (run_eval._meter_delta rounds to them), so a sum within this of the cap is
#: an exact fit. 0.3 - 0.2 is 0.0999... in floats.
EPSILON = 1e-9
#: A meter snapshot older than this says too little of the account now to
#: release room on; one more than PACE_CLOCK_SKEW ahead of `now` is not read.
PACE_MAX_AGE = timedelta(hours=12)
PACE_CLOCK_SKEW = timedelta(minutes=5)
#: An improve run measures baseline and candidate on every fixture of the
#: skill at three trials each, about 24 agent trials for a four-fixture skill
#: (thresholds doc), where an eval fire (arms both, one trial) runs two.
IMPROVE_FIRE_UNITS = 12
#: The longest an improve fire runs: the routine's improve step gets 90
#: minutes, plus setup, the push and the ingest. A fire that started this long
#: before a meter snapshot is finished, so the snapshot already counts it.
IMPROVE_MAX_RUN = timedelta(hours=2)
#: The fire modes charged `improve_charge`, held all week (see above).
IMPROVE_MODES = ("improve", "guidance-improve")
REFUSAL = ("::error::the weekly eval allowance cap would be exceeded by this "
           "fire; nothing was fired (evals/budget.yml, #68)")


class GuardError(Exception):
    """The guard could not tell what has been spent. Its message is fixed
    text and, for an API failure, a status code: never a response body."""


def _parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _summary_paths(run_dir: Path) -> list[Path]:
    return sorted(path for path in run_dir.rglob("summary.json"))


def _load_summary(path: Path):
    """The parsed summary, or None for a symlink, an oversize file or one
    that does not parse: data that is not trusted."""
    try:
        if path.is_symlink() or path.stat().st_size > SUMMARY_MAX_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None


def _invocation_cost(usage, limits: dict) -> float:
    """One run_eval invocation's charge from its run-level `usage` block."""
    reserve = budget_file.run_reservation(limits)
    delta = usage.get("meter_delta") if isinstance(usage, dict) else None
    windows = delta.get("windows") if isinstance(delta, dict) else None
    week = windows.get("seven_day") if isinstance(windows, dict) else None
    moved = week.get("delta") if isinstance(week, dict) else None
    if isinstance(moved, bool) or not isinstance(moved, (int, float)) \
            or not math.isfinite(moved) or week.get("window_reset") is not False:
        return reserve
    # A meter that went down in a window that did not reset spent nothing
    # measurable; the overrun is still charged.
    return max(0.0, float(moved)) + limits["per_run_overrun"]


def run_cost(run_dir: Path, limits: dict) -> float:
    """What one ingested routine run is charged: the sum over its run_eval
    invocations (one run-level block each, however many summaries carry it),
    or the full reservation when nothing in it can be read."""
    reserve = budget_file.run_reservation(limits)
    blocks = {}
    for path in _summary_paths(run_dir):
        summary = _load_summary(path)
        if summary is None:
            return reserve
        usage = summary.get("usage") if isinstance(summary, dict) else None
        if not isinstance(usage, dict):
            # A summary from before #370 says nothing of its spend.
            return reserve
        level = {key: value for key, value in usage.items() if key != "measured"}
        blocks[json.dumps(level, sort_keys=True)] = usage
    if not blocks:
        return reserve
    return sum(_invocation_cost(usage, limits) for usage in blocks.values())


def _unmeasured(usage) -> bool:
    """Whether one run-level `usage` block has no measured weekly delta."""
    delta = usage.get("meter_delta") if isinstance(usage, dict) else None
    windows = delta.get("windows") if isinstance(delta, dict) else None
    week = windows.get("seven_day") if isinstance(windows, dict) else None
    moved = week.get("delta") if isinstance(week, dict) else None
    return isinstance(moved, bool) or not isinstance(moved, (int, float)) \
        or not math.isfinite(moved) or week.get("window_reset") is not False


def measured_cost(run_dir: Path, limits: dict) -> float | None:
    """`run_cost` of one ingested run when every invocation in it carries a
    measured weekly delta, else None: the runs a historical cost is taken
    from."""
    blocks = {}
    for path in _summary_paths(run_dir):
        summary = _load_summary(path)
        usage = summary.get("usage") if isinstance(summary, dict) else None
        if not isinstance(usage, dict) or _unmeasured(usage):
            return None
        level = {key: value for key, value in usage.items() if key != "measured"}
        blocks[json.dumps(level, sort_keys=True)] = usage
    if not blocks:
        return None
    return sum(_invocation_cost(usage, limits) for usage in blocks.values())


def delta_inside(run_dir: Path, at: datetime) -> float:
    """The measured weekly delta of one ingested run with at least one meter
    snapshot, all of them readable and no later than `at`: use the reading at
    `at` already holds. 0.0 for anything else (an unread summary, an
    unmeasured delta), so nothing unread is subtracted."""
    blocks, seen = {}, 0
    for path in _summary_paths(run_dir):
        summary = _load_summary(path)
        usage = summary.get("usage") if isinstance(summary, dict) else None
        if not isinstance(usage, dict) or _unmeasured(usage):
            return 0.0
        measured = usage.get("measured")
        reading = measured.get("meter") if isinstance(measured, dict) else None
        snapshots = reading.get("snapshots") if isinstance(reading, dict) else None
        for snapshot in snapshots if isinstance(snapshots, list) else ():
            stamp = _snapshot_time(snapshot)
            if stamp is None or stamp > at:
                return 0.0
            seen += 1
        level = {key: value for key, value in usage.items() if key != "measured"}
        blocks[json.dumps(level, sort_keys=True)] = usage
    if not seen:
        return 0.0
    return sum(max(0.0, float(usage["meter_delta"]["windows"]["seven_day"]["delta"]))
               for usage in blocks.values())


def all_runs(tree: Path) -> list[Path]:
    """Every run directory under `<tree>/routine-results/`, any age."""
    root = tree / DEST_ROOT
    if not root.is_dir():
        return []
    return [run_dir for run_dir in sorted(root.iterdir())
            if RUN_ID_RE.fullmatch(run_dir.name) and not run_dir.is_symlink()
            and run_dir.is_dir()]


def eval_fire_cost(tree: Path, limits: dict) -> float:
    """The mean measured cost of one ingested eval run, any age, at most one
    reservation; the reservation when no run is measured."""
    reserve = budget_file.run_reservation(limits)
    costs = [cost for cost in (measured_cost(run_dir, limits) for run_dir in all_runs(tree))
             if cost is not None]
    if not costs:
        return reserve
    return min(reserve, sum(costs) / len(costs))


def improve_charge(tree: Path, limits: dict) -> float:
    """What one improve fire is charged, for the whole window."""
    return IMPROVE_FIRE_UNITS * eval_fire_cost(tree, limits)


def ingested_runs(tree: Path, now: datetime,
                  since: datetime | None = None) -> list[Path]:
    """The run directories under `<tree>/routine-results/` whose run id's
    timestamp is at or after `since` (default: seven days before `now`; a
    later one, from a clock ahead of this one, counts too: the safe
    direction)."""
    root = tree / DEST_ROOT
    if not root.is_dir():
        return []
    if since is None:
        since = now - WINDOW
    found = []
    for run_dir in sorted(root.iterdir()):
        match = RUN_ID_RE.fullmatch(run_dir.name)
        if match is None or run_dir.is_symlink() or not run_dir.is_dir():
            continue
        try:
            stamp = _parse_stamp(match.group("stamp"))
        except ValueError:
            continue
        if stamp >= since:
            found.append(run_dir)
    return found


def _snapshot_time(snapshot):
    """A snapshot's `at` as an aware datetime, or None."""
    at = snapshot.get("at") if isinstance(snapshot, dict) else None
    if not isinstance(at, str):
        return None
    try:
        stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def newest_snapshot(runs: list[Path]):
    """`(at, snapshot)` for the account meter snapshot with the newest `at`
    in the runs' summaries, or None. A snapshot without a readable `at`, and
    a summary `run_cost` would not trust, are skipped."""
    newest = None
    for run_dir in runs:
        for path in _summary_paths(run_dir):
            summary = _load_summary(path)
            usage = summary.get("usage") if isinstance(summary, dict) else None
            measured = usage.get("measured") if isinstance(usage, dict) else None
            reading = measured.get("meter") if isinstance(measured, dict) else None
            snapshots = reading.get("snapshots") if isinstance(reading, dict) else None
            if not isinstance(snapshots, list):
                continue
            for snapshot in snapshots:
                at = _snapshot_time(snapshot)
                if at is not None and (newest is None or at > newest[0]):
                    newest = (at, snapshot)
    return newest


def _number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) \
        and math.isfinite(value)


def window_start(now: datetime) -> datetime:
    """When the window the cap applies to began: the account's last weekly
    reset at or before `now`.

    The owner's caps are per allowance week (the owner, 2026-10-26: the budget follows
    "the weekly reset day/time on my account"), so a sanctioned push in one
    week is not charged to the next. The reset is a fixed instant every seven
    days, `ACCOUNT_WEEK_RESET` plus whole weeks: Monday 17:00 UTC, which is
    1 PM Eastern in summer time and 12 PM after the clocks change. It is
    taken from this code, not from the meter snapshots on the bot-written
    results branch, so a forged `resetsAt` cannot shorten the window."""
    weeks = (now - ACCOUNT_WEEK_RESET) // WINDOW
    return ACCOUNT_WEEK_RESET + weeks * WINDOW


def pace(runs: list[Path], limits: dict, now: datetime):
    """`(pace_room, utilization, week_elapsed)` from the newest snapshot, or
    `(0.0, None, None)` when there is none usable: the allowance below
    `stop_when_account_above` the account has not used, scaled by the share
    of its week gone."""
    found = newest_snapshot(runs)
    if found is None:
        return 0.0, None, None
    at, snapshot = found
    if not now - PACE_MAX_AGE <= at <= now + PACE_CLOCK_SKEW:
        return 0.0, None, None
    info = snapshot.get("info")
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    week = windows.get("seven_day") if isinstance(windows, dict) else None
    used = week.get("utilization") if isinstance(week, dict) else None
    resets = week.get("resetsAt") if isinstance(week, dict) else None
    if not _number(used) or not 0 <= used <= 1 or not _number(resets):
        return 0.0, None, None
    left = resets - now.timestamp()
    if left <= 0 or resets - at.timestamp() > WINDOW.total_seconds():
        return 0.0, None, None
    elapsed = min(1.0, max(0.0, 1 - left / WINDOW.total_seconds()))
    room = max(0.0, limits["stop_when_account_above"] - used) * elapsed
    return room, float(used), elapsed


def account(tree: Path, limits: dict, now: datetime,
            fires: tuple[int, int], improve: tuple[int, int] = (0, 0),
            since: datetime | None = None,
            improve_started: list | None = None) -> dict:
    """{spent, pending, runs, fires, recent, reservation, cap, pace_room,
    account_used, week_elapsed, improve_fires, improve_charge,
    improve_covered, eval_inside} for the
    window from `since` (default `window_start`) up to `now`, given
    `fires`, a pair: the successful fires of that window and of the
    last `PENDING_FULL_HOLD`. An ingested run is matched to a fire by its run
    id's timestamp (the fire workflow stamps it at fire time), so a run from
    the last `PENDING_FULL_HOLD` frees one recent fire and runs older than
    that free old fires; the unresulted recent fires are held in full and the
    rest are charged `per_run_overrun`. `pace_room` is `pace` of the same runs'
    newest meter snapshot; `account_used` and `week_elapsed` are None when
    there is no usable one. `improve` is the same pair for the improve
    fires among them: each is charged `improve_charge` for the whole window
    and is never matched against ingested runs, except that `improve_started`,
    their start times, lets those that started `IMPROVE_MAX_RUN` or more
    before the newest usable snapshot (`improve_covered` of them) be held
    together at no more than its account-wide `account_used`, less
    `eval_inside`, the measured deltas of the ingested runs inside that
    reading."""
    try:
        fires_7d, fires_24h = fires
        improve_7d, improve_24h = improve
    except (TypeError, ValueError):
        raise GuardError("fire counts are not a pair") from None
    for count in (fires_7d, fires_24h, improve_7d, improve_24h):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise GuardError("fire count is not a non-negative integer")
    # Improve fires are among the fires; they never produce routine-results.
    fires_7d = max(0, fires_7d - improve_7d)
    fires_24h = max(0, fires_24h - improve_24h)
    if improve_started is not None and (
            not isinstance(improve_started, list)
            or any(not isinstance(start, datetime) or start.tzinfo is None
                   for start in improve_started)):
        raise GuardError("improve start times are not aware datetimes")
    if since is None:
        since = window_start(now)
    runs = ingested_runs(tree, now, since)
    reserve = budget_file.run_reservation(limits)
    spent = sum(run_cost(run_dir, limits) for run_dir in runs)
    unresulted = max(0, fires_7d - len(runs))
    # A run id stamped after `now` (a clock ahead) counts as recent too, as
    # in ingested_runs. More recent runs than recent fires (a manual run)
    # leave no recent fire unresulted, never a negative count.
    held_since = now - PENDING_FULL_HOLD
    recent_runs = sum(
        1 for run_dir in runs
        if _parse_stamp(RUN_ID_RE.fullmatch(run_dir.name).group("stamp"))
        >= held_since)
    recent = max(0, min(fires_24h - recent_runs, unresulted))
    charge = improve_charge(tree, limits)
    room, used, elapsed = pace(runs, limits, now)
    covered = 0
    eval_inside = 0.0
    if improve_started and used is not None:
        # `pace` accepted the newest snapshot, so `newest_snapshot` is not None.
        at = newest_snapshot(runs)[0]
        covered = min(improve_7d,
                      sum(1 for start in improve_started if start + IMPROVE_MAX_RUN <= at))
        # Already charged in `spent` (module docstring, 2026-10-28).
        eval_inside = sum(delta_inside(run_dir, at) for run_dir in runs)
    improve_held = (improve_7d - covered) * charge + (
        min(covered * charge, max(0.0, used - eval_inside)) if covered else 0.0)
    pending = (recent * reserve + (unresulted - recent) * limits["per_run_overrun"]
               + improve_held)
    # Finished (IMPROVE_MAX_RUN or more ago) but newer than the reading, so
    # still held in full until an ingested run brings a newer snapshot.
    finished = 0
    if improve_started:
        finished = min(improve_7d - covered, max(0, sum(
            1 for start in improve_started if start + IMPROVE_MAX_RUN <= now) - covered))
    return {"spent": spent, "pending": pending, "runs": len(runs),
            "fires": fires_7d, "recent": recent, "reservation": reserve,
            "cap": limits["weekly_cap"], "pace_room": room,
            "account_used": used, "week_elapsed": elapsed,
            "improve_fires": improve_7d, "improve_charge": charge,
            "improve_covered": covered, "eval_inside": eval_inside,
            "improve_finished": finished,
            "improve_finished_held": finished * charge}


def room(state: dict) -> float:
    """The points left now: max(cap - spent, pace_room) - pending (`fits`).
    The weekly planner (scripts/plan_week.py) spends it at each fire's
    historical cost."""
    return max(state["cap"] - state["spent"],
               state.get("pace_room", 0.0)) - state["pending"]


def fits(state: dict) -> int:
    """How many more fires, each reserved in full, fit: the largest n with
    n * reservation <= max(cap - spent - pending, pace_room - pending)
    (within EPSILON). The fixed cap left is a floor; late in an under-pace
    week the pace room can exceed it (the owner, 2026-10-25). Pending comes off
    both: the snapshot behind the pace room may predate those fires, and
    without it every fire would fit until the next snapshot is ingested."""
    left = room(state) + EPSILON
    if left < state["reservation"]:
        return 0
    # Counted by comparison, not trusted from a float quotient alone.
    count = int(left // state["reservation"])
    while (count + 1) * state["reservation"] <= left:
        count += 1
    while count and count * state["reservation"] > left:
        count -= 1
    return count


def count_successful_fires(repo: str, since: datetime, token: str,
                           api: str = "https://api.github.com",
                           opener=urllib.request.urlopen) -> int:
    """Successful runs of routine-eval-fire.yml created at or after `since`:
    the one network call, isolated so tests never make it. Any failure is a
    GuardError carrying at most the HTTP status code."""
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo or ""):
        raise GuardError("repository name is missing or malformed")
    query = urllib.parse.urlencode({
        "created": ">=" + since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "success", "per_page": "1"})
    request = urllib.request.Request(
        f"{api}/repos/{repo}/actions/workflows/{FIRE_WORKFLOW}/runs?{query}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise GuardError(f"the workflow runs API answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise GuardError("the workflow runs API could not be reached") from None
    if status != 200:
        raise GuardError(f"the workflow runs API answered {status}")
    try:
        total = json.loads(body).get("total_count")
    except (ValueError, AttributeError, UnicodeDecodeError):
        raise GuardError("the workflow runs API answered 200 with an unreadable body") from None
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GuardError("the workflow runs API answered 200 with an unreadable body")
    return total


def fires_that_fit(tree: Path, limits: dict, now: datetime,
                   fires: tuple[int, int], improve: tuple[int, int] = (0, 0)) -> int:
    """`fits` of the accounting for `now`. `fires` and `improve` are
    `account`'s pairs."""
    return fits(account(tree, limits, now, fires, improve))


def describe(state: dict) -> str:
    """One non-identifying line, in points (percent of the weekly allowance)."""
    def points(value):
        return f"{value * 100:.1f}"
    hold_hours = f"{PENDING_FULL_HOLD.total_seconds() / 3600:g}"
    line = (f"weekly eval allowance: spent {points(state['spent'])} points over "
            f"{state['runs']} ingested run(s), pending {points(state['pending'])} "
            f"points over {max(0, state['fires'] - state['runs'])} fire(s) without "
            f"results ({state['recent']} from the last {hold_hours}h held in full), this run reserves {points(state['reservation'])}, cap "
            f"{points(state['cap'])} (since the account's weekly reset, at most 7 days)")
    if state.get("improve_fires"):
        line += (f"; {state['improve_fires']} improve fire(s) held at "
                 f"{points(state['improve_charge'])} points each")
        if state.get("improve_covered"):
            line += (f"; {state['improve_covered']} of them finished before the "
                     f"account reading, so held at most at its "
                     f"{points(state['account_used'])} points")
            if state.get("eval_inside"):
                line += (f" less {points(state['eval_inside'])} already charged "
                         f"to eval runs inside it")
    if state.get("improve_finished"):
        line += (f"; {state['improve_finished']} finished after it, held in full "
                 f"until a newer reading")
    used = state.get("account_used")
    if state.get("pace_room", 0.0) > 0 or used is not None:
        return (f"{line}; account at {points(used or 0.0)}% with "
                f"{points(state.get('week_elapsed') or 0.0)}% of its week gone, "
                f"so pace allows {points(state.get('pace_room', 0.0))} points before pending fires")
    return f"{line}; no recent account reading, so no pace room"


def check(state: dict, mode: str = "eval") -> bool:
    """Whether one more fire, reserved in full (an improve fire at
    `improve_charge`), stays within the cap."""
    if mode in IMPROVE_MODES:
        return room(state) + EPSILON >= state["improve_charge"]
    # Only an eval fire's ingested results carry a meter snapshot.
    return fits(state) >= 1 or (mode == "eval" and refresh(state))


def refresh(state: dict) -> bool:
    """Whether one eval fire may go only to bring a newer meter snapshot.

    Improve fires that finished after the newest snapshot keep their full
    charge until an ingested run brings a newer one, and only eval fires
    bring one. On 2026-10-27 two refused improve fires held 21 points that
    way, which left 3.1 points of room against a 3.5-point eval reservation:
    no fire could go, so no snapshot could come, for the rest of the week.
    So an eval fire goes when it fits with those finished fires left out,
    and no eval fire from the last `PENDING_FULL_HOLD` is still without
    results. Its own reservation is held in full, and the in-run stop
    still bounds it."""
    if not state.get("improve_finished") or state.get("recent"):
        return False
    left = room(state) + state["improve_finished_held"] + EPSILON
    return left >= state["reservation"]


def _live_fires(now: datetime, since: datetime | None = None) -> tuple[int, int]:
    """(successful fires since `since`, default seven days before `now`; in
    the last `PENDING_FULL_HOLD`)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    return (count_successful_fires(repo, since or now - WINDOW, token, api=api),
            count_successful_fires(repo, now - PENDING_FULL_HOLD, token, api=api))


def improve_counts(runs: list, now: datetime,
                   since: datetime | None = None) -> tuple[int, int]:
    """(successful improve and guidance-improve fires since `since`, default
    seven days before `now`; in the last `PENDING_FULL_HOLD`) among the runs
    API's rows."""
    since = since or now - WINDOW
    fires = [f for f in improve_outcomes.charged_fires(runs) if f["conclusion"] == "success"]
    return (sum(1 for f in fires if f["started"] >= since),
            sum(1 for f in fires if f["started"] >= now - PENDING_FULL_HOLD))


def improve_starts(runs: list, now: datetime,
                   since: datetime | None = None) -> list[datetime]:
    """The `started` times of the successful improve and guidance-improve
    fires since `since` (default seven days before `now`) among the runs
    API's rows."""
    since = since or now - WINDOW
    return [f["started"] for f in improve_outcomes.charged_fires(runs)
            if f["conclusion"] == "success" and f["started"] >= since]


def _live_improve_runs(now: datetime) -> list:
    """The fire runs of the last seven days, fetched once."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    try:
        runs = improve_outcomes.fetch_fire_runs(
            repo, token, now - WINDOW,
            get=lambda path, tok, **kw: improve_outcomes.github_get(path, tok, api=api, **kw))
    except improve_outcomes.EvidenceError as exc:
        raise GuardError(str(exc)) from None
    return runs


def _live_improve(now: datetime, since: datetime | None = None) -> tuple[int, int]:
    """`improve_counts` of the fire runs since `since` (default the last
    seven days)."""
    return improve_counts(_live_improve_runs(now), now, since)


def _live_improve_both(now: datetime, since: datetime | None = None):
    """(`improve_counts`, `improve_starts`) of one fetch of the fire runs."""
    runs = _live_improve_runs(now)
    return improve_counts(runs, now, since), improve_starts(runs, now, since)


def main(argv: list[str] | None = None, now: datetime | None = None,
         fire_count=None, improve_count=None, improve_starts=None) -> int:
    """`fire_count` and `improve_count`, functions of `now` returning
    `(fires_7d, fires_24h)` (all fires, and the improve fires among them),
    stand in for the API in tests, and `improve_starts`, a function of `now`
    returning the improve fires' start times (used with `improve_count`)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("check", "fits"))
    parser.add_argument("--results-tree", type=Path, required=True,
                        help="a checkout of persistent/eval-results")
    parser.add_argument("--budget", type=Path, default=None,
                        help="the budget file (default: evals/budget.yml)")
    # Every mode routine-eval-fire.yml passes: draft was missing until
    # 2026-10-27, so argparse refused every draft fire at this step.
    parser.add_argument("--mode", choices=("eval", "scaffold", "draft", *IMPROVE_MODES),
                        default="eval",
                        help="the fire's mode: an improve or guidance-improve fire "
                             "needs room for improve_charge")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    try:
        limits = budget_file.load_budget(args.budget)
        since = window_start(now)
        fires = fire_count(now) if fire_count else _live_fires(now, since)
        if improve_count:
            improve = improve_count(now)
            started = improve_starts(now) if improve_starts else None
        else:
            # One fetch feeds both the counts and the start times.
            improve, started = _live_improve_both(now, since)
        state = account(args.results_tree, limits, now, fires, improve, since, started)
    except (GuardError, budget_file.BudgetError) as exc:
        # Fail closed: an unknown spend is never read as room to spend.
        print(f"::error::allowance guard: {exc}; refusing")
        return 1
    line = describe(state)
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"### Weekly allowance guard\n\n- {line}\n")
    if args.command == "fits":
        print(f"fires={fits(state)}")
        return 0
    if not check(state, args.mode):
        print(REFUSAL)
        return 1
    if args.mode not in IMPROVE_MODES and fits(state) < 1:
        print("allowed as the one refresh fire: its results bring a newer account reading")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
