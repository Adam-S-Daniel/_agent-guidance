#!/usr/bin/env python3
"""Generate a shields.io endpoint-format JSON badge from a window of run summaries.

Reads the `--window` newest runs under `<results-dir>/<skill>/` (run dirs are
UTC timestamps, so lexicographic order == chronological), averages the
with_skill and without_skill arms over them on objective-check pass counts and
judge overall scores, and writes `badges/<skill>.json` for shields.io's
endpoint badge. A run of a skill with nested fixtures holds one A/B pair per
fixture, and a run made with `--trials N` holds N trials per arm (#66); the
average is over every trial of every fixture of every run in the window:

    https://img.shields.io/endpoint?url=<raw URL of badges/<skill>.json>

Color semantics (objective checks are the primary signal; the judge can
only demote, never promote):
  green      — with_skill strictly better on objective checks, and not
               worse on judge overall
  yellow     — objective tied (regardless of judge advantage — a judge
               delta never produces green), or mixed signals (objective
               better but judge worse)
  red        — with_skill worse on objective checks, or objective tied
               with a worse judge overall
  lightgrey  — data missing (no runs yet, an arm errored, or a summary is
               absent/unreadable/malformed)

A single run is scheduling luck, not a measurement: the arms have been
observed several checks apart from one run to the next, so a badge built from
one run reports noise. The window averages that out. An A/B pair where either
arm is missing or errored — at `--trials N`, where ANY trial of either arm
errored — is dropped from the window rather than blanking the badge, so one
bad night does not erase a week of signal; a window with no usable pair at
all still goes lightgrey.

The message carries the sample size (`n=N`) whenever more than one trial was
averaged: N is the number of trials per arm behind each mean, which is trials
x fixtures x runs, and for single-trial runs of a flat fixture is the number
of runs, as it always was. At n=1 there is no average and the marker is
omitted, which is exactly the pre-window message. Means print as integers
when integral (`7/7`, never `7.0/7`) and to one decimal otherwise.

The message always carries a run's date (from the run directory's timestamp,
NOT the wall clock) so a stale badge is self-evident: the newest run that
contributed to the aggregate, or — when nothing was usable — the newest run
directory found. Output is deterministic for the same inputs. Stdlib only.

Runs made under different CLI permission modes (harness/run_eval.py
`--permission-mode`, recorded as `harness.permission_mode`) are never averaged
together: the window keeps only the pairs whose mode matches the newest usable
pair's, and drops the rest the way it drops an errored pair. A summary that
records no mode predates the setting and ran under `bypassPermissions`.

Also accepts a nested skill fixture as `<skill>/<fixture>`; its summaries
are under results/<skill>/<timestamp>/<fixture>/, and only that fixture
contributes to its own badge and run window.

Also accepts a guidance eval by its results-tree name, `guidance/<section id>`
(#97), whose arms are with_guidance/without_guidance rather than
with_skill/without_skill and whose label reads "guidance eval: <id>".

And a guidance SECTION fixture as `guidance/<section id>/<fixture>` (#124),
its fixture path without `evals/`: its runs are under
results/guidance/<section id>/<timestamp>/ beside the section's other
fixtures, so only run dirs whose summary records that `fixture_name` count,
and its pair is the in-situ one the retirement gate decides on,
with_guidance_full against with_guidance_full_minus_section.

Usage:
    python3 scripts/make_badge.py workflow-path-audit
    python3 scripts/make_badge.py guidance/<section id>
    python3 scripts/make_badge.py <name> [--results-dir results] [--out PATH]
                                         [--window N]
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

# Runs are weekly-ish, so five is roughly a month of signal: long enough to
# average out the run-to-run spread, short enough that a real regression shows
# up within a couple of runs instead of being buried by history.
DEFAULT_WINDOW = 5


# The two subjects this badge can describe. A skill eval's arms are
# with_skill/without_skill; a guidance eval (#97) names its subject
# `guidance/<section id>` — the same shape as its results path — and its A/B
# pair is with_guidance/without_guidance. A guidance fixture with some other
# arm set (the five-mode delivery canary, say) simply has no A/B pair here and
# reads as "no data", which is honest: a canary is not a comparison.
GUIDANCE_PREFIX = "guidance/"
_NAME_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_name(name: str) -> list[str]:
    """`name` becomes a path under results/ and under badges/, so it is
    checked before either is built: one or two safe segments, never absolute,
    never containing `..`."""
    parts = str(name).split("/")
    if (not 1 <= len(parts) <= (3 if parts[0] == "guidance" else 2)
            or not all(_NAME_SEGMENT_RE.fullmatch(p) for p in parts)
            or any(p in (".", "..") for p in parts)):
        raise ValueError(
            f"invalid eval name {name!r}: expected `<skill>` or "
            "`<skill>/<fixture>` or `guidance/<section id>[/<fixture>]` with no path "
            "metacharacters")
    return parts


def arm_names(name: str) -> tuple[str, str]:
    """(treatment, control) arm directory names for this subject."""
    if name.startswith(GUIDANCE_PREFIX) and name.count("/") == 2:
        return ("with_guidance_full", "with_guidance_full_minus_section")
    if name.startswith(GUIDANCE_PREFIX):
        return ("with_guidance", "without_guidance")
    return ("with_skill", "without_skill")


#: Written by scripts/local_eval.py at the root of a local exhibit's results
#: dir and of each trial dir.
LOCAL_EXHIBIT_MARKER = "LOCAL_EXHIBIT"


def badge_label(name: str) -> str:
    if name.startswith(GUIDANCE_PREFIX):
        return f"guidance eval: {name[len(GUIDANCE_PREFIX):]}"
    return f"skill eval: {name}"


def _fixture_name(arm_dir: Path) -> str | None:
    """The `fixture_name` a guidance arm's summary records, or None."""
    try:
        summary = json.loads((arm_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return summary.get("fixture_name") if isinstance(summary, dict) else None


def runs_newest_first(results_dir: Path, skill: str) -> list[Path]:
    """Run dirs under results/<skill>/, newest first.

    Run dirs are UTC timestamps (%Y%m%dT%H%M%SZ), so reverse-lexicographic
    order is reverse-chronological order — no stat() calls, no wall clock.
    """
    parts = skill.split("/")
    nested = len(parts) == 2 and not skill.startswith(GUIDANCE_PREFIX)
    if len(parts) == 3:
        skill_dir = results_dir / parts[0] / parts[1]
        if not skill_dir.is_dir():
            return []
        arm = arm_names(skill)[0]
        return sorted((d for d in skill_dir.iterdir()
                       if d.is_dir() and _fixture_name(d / arm) == parts[2]),
                      reverse=True)
    skill_dir = results_dir / (parts[0] if nested else skill)
    if not skill_dir.is_dir():
        return []
    return sorted((d for d in skill_dir.iterdir()
                   if d.is_dir() and (not nested or (d / parts[1]).is_dir())),
                  reverse=True)


def run_date(run_dir: Path) -> str:
    """YYYY-MM-DD from a %Y%m%dT%H%M%SZ run-dir name; the raw name otherwise."""
    name = run_dir.name
    if len(name) >= 8 and name[:8].isdigit():
        return f"{name[:4]}-{name[4:6]}-{name[6:8]}"
    return name


def _count(value) -> int | None:
    """`value` as a non-negative integer count, or None. A bool is not a
    count, and neither is a float that happens to be whole."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number(value) -> float | None:
    """`value` as a finite real number, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


#: What a summary that records no `harness.permission_mode` ran under: every
#: run before the setting existed launched its agent with bypassPermissions.
#: Mirrors harness/guidance.py's LEGACY_PERMISSION_MODE (this script is
#: stdlib-only and imports nothing from the harness).
LEGACY_PERMISSION_MODE = "bypassPermissions"


def permission_mode(summary: dict) -> str | None:
    """The mode a summary's run launched its agents with; the legacy mode
    when it records none, and None when the field is there but malformed."""
    harness = summary.get("harness")
    if not isinstance(harness, dict) or "permission_mode" not in harness:
        return LEGACY_PERMISSION_MODE
    mode = harness["permission_mode"]
    return mode if isinstance(mode, str) and mode else None


def arm_stats(unit_dir: Path, arm: str) -> dict | None:
    """One arm's totals, or None if missing, errored or malformed.

    Returns `{"n", "passed", "total", "judge_sum", "judge_n", "mode"}`: the
    `mode` is the run's permission mode (`permission_mode()`), and the rest
    are the number of
    trials, the objective checks passed and run summed over them, and the sum
    and count of the trials' numeric judge overalls. Sums rather than means,
    so that averaging several of these is one division of integer counts by
    the number of trials.

    Two summary shapes (harness/run_eval.py, "Trials"):

      * single-trial — `objective_checks` is a list. This is every summary
        written before #66 (no `n`) and every `--trials 1` summary since
        (`n: 1`); both read as one trial.
      * aggregate — `--trials N`, N > 1: `objective_checks` is null and the
        counts are under `aggregate`.

    An arm whose `error` is set is unusable in both shapes, and in the
    aggregate that means ANY errored trial: a mean over the surviving trials
    is not the measurement its sibling arm made.

    Defensive against malformed summaries (non-dict payloads, non-list
    objective_checks, non-dict check entries or judge, counts that are not
    counts, an `n` that disagrees with the shape): anything that isn't the
    expected shape reads as missing data — the badge goes lightgrey rather
    than the job crashing.
    """
    summary_path = unit_dir / arm / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(summary, dict) and summary.get("local_exhibit"):
        # scripts/local_eval.py stamps every per-trial summary it keeps. That
        # run was a local exhibit under the owner's own login: never badge
        # input, so refuse loudly rather than read it as missing data.
        raise SystemExit(f"{summary_path}: a local exhibit "
                         "(local_exhibit: true), not badge input")
    if not isinstance(summary, dict) or summary.get("error"):
        return None
    n = _count(summary.get("n", 1))
    mode = permission_mode(summary)
    if not n or mode is None:
        return None

    checks = summary.get("objective_checks")
    if isinstance(checks, list):
        if not checks or n != 1:
            return None
        judge = summary.get("judge")
        overall = judge.get("overall") if isinstance(judge, dict) else None
        if not isinstance(overall, (int, float)):
            overall = None
        return {
            "n": 1,
            "passed": sum(1 for c in checks
                          if isinstance(c, dict) and c.get("passed")),
            "total": len(checks),
            "judge_sum": overall,
            "judge_n": 0 if overall is None else 1,
            "mode": mode,
        }

    block = summary.get("aggregate")
    objective = block.get("objective") if isinstance(block, dict) else None
    if not isinstance(objective, dict) or summary.get("errors"):
        return None
    passed, total = _count(objective.get("passed")), _count(objective.get("total"))
    if passed is None or not total or passed > total or objective.get("n") != n:
        return None
    judge = block.get("judge")
    overall = judge.get("overall") if isinstance(judge, dict) else None
    judge_sum = _number(overall.get("sum")) if isinstance(overall, dict) else None
    judge_n = _count(overall.get("n")) if isinstance(overall, dict) else None
    if judge_sum is None or not judge_n:
        judge_sum, judge_n = None, 0
    return {"n": n, "passed": passed, "total": total,
            "judge_sum": judge_sum, "judge_n": judge_n, "mode": mode}


def _cmp(a: float, b: float) -> int:
    return (a > b) - (a < b)


def compare_arms(with_stats: dict, without_stats: dict) -> str:
    """green/yellow/red per the with-vs-without comparison.

    Objective checks are primary; the judge can only demote. Green requires
    with_skill strictly better on objective checks — on an objective tie a
    judge advantage never promotes to green (it caps at yellow), while a
    judge disadvantage demotes (tie -> red, objective-better -> yellow).
    """
    objective = _cmp(with_stats["passed"] / with_stats["total"],
                     without_stats["passed"] / without_stats["total"])
    judge = None
    if with_stats["judge"] is not None and without_stats["judge"] is not None:
        judge = _cmp(with_stats["judge"], without_stats["judge"])

    if objective < 0:
        return "red"
    if objective == 0:
        return "red" if judge == -1 else "yellow"
    return "yellow" if judge == -1 else "green"  # objective strictly better


def unit_dirs(run_dir: Path, treatment: str, control: str) -> list[Path]:
    """The directories inside one run that hold an A/B pair's arm dirs.

    A flat fixture's arms sit directly in the run directory, which is every
    run published before #66. A nested fixture's sit one level down, in
    `<run>/<fixture>/`. A directory counts when it holds either arm's
    directory, so a pair with one arm missing is still found — and then
    dropped as unusable, which is what happened to it before. Sorted by
    name, the run directory itself first.
    """
    def holds_an_arm(directory: Path) -> bool:
        return any((directory / arm).is_dir() for arm in (treatment, control))

    found = [run_dir] if holds_an_arm(run_dir) else []
    found += sorted(child for child in run_dir.iterdir()
                    if child.is_dir() and holds_an_arm(child))
    return found


def usable_units(results_dir: Path, skill: str,
                 window: int) -> list[tuple[Path, dict, dict]]:
    """(run_dir, with_stats, without_stats) for each usable A/B pair in the
    window.

    The window is the `window` NEWEST run dirs; pairs where either arm is
    missing or errored are then dropped. Deliberately not "scan back until you
    find `window` good runs": the window names a time span, so a bad night
    shrinks the sample rather than silently pulling in an older run. Newest
    run first, a run's fixtures in name order.

    A pair whose arms report different trial counts is dropped too. One
    invocation gives both arms the same `--trials`, so a mismatch means the
    pair was assembled from two, and there is then no single `n` for the
    badge to print.

    So is a pair whose arms ran under different permission modes, and then
    every pair whose mode differs from the NEWEST usable pair's: an average
    across modes compares two different agents. The newest mode wins so a
    mode change shows up in the badge as soon as it lands, with `n` shrinking
    until the window has filled with runs under the new mode.
    """
    treatment, control = arm_names(skill)
    out = []
    for run_dir in runs_newest_first(results_dir, skill)[:max(1, window)]:
        nested = "/" in skill and not skill.startswith(GUIDANCE_PREFIX)
        units = ([run_dir / skill.split("/", 1)[1]] if nested else
                 unit_dirs(run_dir, treatment, control))
        for unit_dir in units:
            with_stats = arm_stats(unit_dir, treatment)
            without_stats = arm_stats(unit_dir, control)
            if with_stats is None or without_stats is None:
                continue
            if with_stats["n"] != without_stats["n"]:
                continue
            if with_stats["mode"] != without_stats["mode"]:
                continue
            out.append((run_dir, with_stats, without_stats))
    return [unit for unit in out if unit[1]["mode"] == out[0][1]["mode"]]


def aggregate(stats: list[dict]) -> dict:
    """Mean {"passed", "total", "judge"} per trial, plus the trial count `n`,
    over one arm's per-pair totals.

    `total` is averaged too rather than assumed constant: a fixture that gains
    a check mid-window — or two fixtures of one skill with different numbers
    of checks — would otherwise print a pass count against a denominator no
    trial actually had. `judge` averages only the trials that carried a
    numeric judge overall, and is None when none did.

    Every trial weighs the same. For single-trial pairs that is the plain
    mean over runs this function always took.
    """
    n = sum(s["n"] for s in stats)
    judge_n = sum(s["judge_n"] for s in stats)
    return {
        "n": n,
        "passed": sum(s["passed"] for s in stats) / n,
        "total": sum(s["total"] for s in stats) / n,
        "judge": (sum(s["judge_sum"] for s in stats if s["judge_n"]) / judge_n
                  if judge_n else None),
    }


def _fmt_mean(value: float) -> str:
    """One decimal, but an integral mean prints as an integer (`7`, not `7.0`)."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"


def refuse_local_exhibit_tree(results_dir: Path) -> None:
    """Exit when `results_dir` holds scripts/local_eval.py's marker, or any
    parent does: a local trial tree is never badge input, stamped or not."""
    resolved = results_dir.resolve()
    for directory in (resolved, *resolved.parents):
        try:
            marked = (directory / LOCAL_EXHIBIT_MARKER).exists()
        except OSError:
            continue
        if marked:
            raise SystemExit(f"{results_dir}: inside a local exhibit "
                             f"({directory / LOCAL_EXHIBIT_MARKER}), "
                             "not badge input")


def build_badge(results_dir: Path, skill: str,
                window: int = DEFAULT_WINDOW) -> dict:
    _validate_name(skill)
    refuse_local_exhibit_tree(results_dir)
    label = badge_label(skill)
    runs = runs_newest_first(results_dir, skill)
    if not runs:
        return {"schemaVersion": 1, "label": label,
                "message": "no runs yet", "color": "lightgrey"}

    usable = usable_units(results_dir, skill, window)
    if not usable:
        # Nothing in the window was scorable; date the badge from the newest
        # run dir so the reader still sees how stale the attempt is.
        return {"schemaVersion": 1, "label": label,
                "message": f"no data · {run_date(runs[0])}", "color": "lightgrey"}

    with_agg = aggregate([w for _, w, _ in usable])
    without_agg = aggregate([wo for _, _, wo in usable])
    # Newest CONTRIBUTING run, not newest run dir: the date must belong to the
    # data being reported.
    date = run_date(usable[0][0])
    # n=1 is not an average, and omitting the marker there keeps the
    # single-run message byte-identical to the pre-window badge. `n` is the
    # trials per arm behind each mean; a usable pair's arms agree on it.
    sample = f"n={with_agg['n']} · " if with_agg["n"] > 1 else ""

    message = (f"with {_fmt_mean(with_agg['passed'])}/{_fmt_mean(with_agg['total'])} vs "
               f"without {_fmt_mean(without_agg['passed'])}/{_fmt_mean(without_agg['total'])} "
               f"· {sample}{date}")
    return {"schemaVersion": 1, "label": label, "message": message,
            "color": compare_arms(with_agg, without_agg)}


def _positive_int(value: str) -> int:
    """argparse type: reject `--window 0` loudly instead of silently clamping."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", help="skill name (workflow-path-audit) or a "
                                      "guidance section (guidance/<id>)")
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="root of committed run summaries (default: results)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output path (default: badges/<skill>.json)")
    parser.add_argument("--window", type=_positive_int, default=DEFAULT_WINDOW,
                        help=f"average over the N newest runs — every "
                             f"fixture and every trial in them "
                             f"(default: {DEFAULT_WINDOW}; 1 = newest run only)")
    args = parser.parse_args()

    badge = build_badge(args.results_dir, args.skill, args.window)
    out = args.out or Path("badges") / f"{args.skill}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(badge, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"{out}: {badge['message']} ({badge['color']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
