#!/usr/bin/env python3
"""Report which git hook runs were BLOCKED, where, and whether it looks automated.

*** THE OUTPUT OF THIS SCRIPT IS PRIVATE. ***

It names repositories, machines and hours of the day, which together are a
timeline of one person's work. Send it only to an issue in the private repo
that owns the events branch. Never put it in a public repo, a CI log, a job
summary or an artifact of a public workflow.

Inputs:
  * `--events DIR`: a checkout of the private `persistent/usage-events`
    branch. Every `events/<YYYY-MM>/<machine>.jsonl` under it is read. Lines
    of kind "hook" are the fleet's local git hook runner (agent-checks)
    writing one record per run; a run is BLOCKED when `failed` is non-empty.
    Lines of kind "skill" are ignored except to judge freshness.
  * `--weekly-local` / `--weekly-ci`: the public weekly files from the
    `persistent/eval-results` branch (`usage/weekly-local.json`,
    `usage/weekly-ci.json`). The CI file is the only source of CI hook
    blocks; the local file is checked for freshness and for a `hooks` source.

Findings (see `analyze()`): `concentration` (one repo causes at least half the
local blocks), `steady-repeat` (a signature blocked at least four times an
hour for three or more consecutive hours, which looks like a scheduled job
retrying), `concentration-ci`, and `source-stale` / `source-missing` for each
input. A report with findings still exits 0; exit 2 is a usage error.

Usage:
    python3 scripts/hook_block_report.py --events DIR \
        [--weekly-local FILE] [--weekly-ci FILE] [--now ISO8601] \
        [--days 7] [--stale-hours 48] [--json-out FILE] [--markdown-out FILE]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
MIN_BLOCKS = 10
CONCENTRATION_SHARE = 0.5
HIGH_SHARE = 0.8
STEADY_PER_HOUR = 4
STEADY_HOURS = 3
DEFAULT_DAYS = 7
DEFAULT_STALE_HOURS = 48
MAX_HOUR_LINES = 48
TOP_CHECKS = 5
NO_REPO = "(none)"
NO_MACHINE = "(unknown)"


def parse_ts(text) -> datetime | None:
    """Parse an ISO 8601 timestamp (trailing Z allowed) to aware UTC, or None."""
    if not isinstance(text, str) or not text:
        return None
    value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def fmt_ts(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def hour_key(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H")


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _valid(entry) -> bool:
    if not isinstance(entry, dict) or entry.get("v") != 1:
        return False
    if not isinstance(entry.get("id"), str) or not entry["id"]:
        return False
    if parse_ts(entry.get("ts")) is None:
        return False
    if entry.get("kind") == "hook":
        return _str_list(entry.get("failed")) and _str_list(entry.get("checks"))
    return True


def load_events(root) -> tuple[list[dict], list[dict]]:
    """Read every events/*/*.jsonl under `root`.

    Returns (records, sources): records are valid, deduplicated (first id
    wins) dicts with `_ts` (datetime) and `_file_machine` added; sources has
    one entry per machine file group with the newest timestamp of ANY valid
    record, the lines read and the malformed count.
    """
    root = Path(root)
    groups: dict[str, dict] = {}
    records: list[dict] = []
    seen: set[str] = set()
    for path in sorted(root.glob("events/*/*.jsonl")):
        machine = path.stem
        label = f"usage-events/{path.parent.name}/{path.name}"
        group = groups.setdefault(machine, {
            "machine": machine, "paths": [], "newest": None,
            "lines": 0, "malformed": 0})
        group["paths"].append(label)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            group["lines"] += 1
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not _valid(entry):
                group["malformed"] += 1
                continue
            when = parse_ts(entry["ts"])
            if group["newest"] is None or when > group["newest"]:
                group["newest"] = when
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            entry["_ts"] = when
            entry["_file_machine"] = machine
            records.append(entry)
    return records, [groups[k] for k in sorted(groups)]


def _share_finding(kind: str, by_repo: Counter, checks_by_repo, scope: str):
    total = sum(by_repo.values())
    if total < MIN_BLOCKS:
        return None
    top, count = sorted(by_repo.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    share = count / total
    if share < CONCENTRATION_SHARE:
        return None
    finding = {
        "type": kind,
        "severity": "high" if share >= HIGH_SHARE else "medium",
        "repo": top,
        "blocked": count,
        "total_blocked": total,
        "share": share,
        "summary": (f"{top} accounts for {round(share * 100)}% of {scope} "
                    f"blocked hook runs ({count} of {total})."),
    }
    if checks_by_repo is not None:
        ranked = sorted(checks_by_repo.get(top, Counter()).items(),
                        key=lambda kv: (-kv[1], kv[0]))[:TOP_CHECKS]
        finding["top_checks"] = [{"check": c, "blocked": n} for c, n in ranked]
    return finding


def _steady(hours: dict[datetime, int]):
    """Longest run of consecutive hours with enough blocks: (start, end, counts)."""
    busy = sorted(h for h, n in hours.items() if n >= STEADY_PER_HOUR)
    best: list[datetime] = []
    run: list[datetime] = []
    for h in busy:
        if run and h - run[-1] == timedelta(hours=1):
            run.append(h)
        else:
            run = [h]
        if len(run) > len(best):
            best = list(run)
    return best


def _freshness(name, path, newest, now, stale_hours, read, findings, sources):
    age = None if newest is None else (now - newest).total_seconds() / 3600
    sources.append({
        "name": name, "path": path, "read": read,
        "newest": None if newest is None else fmt_ts(newest),
        "age_hours": age})
    if newest is None:
        findings.append({
            "type": "source-missing", "severity": "medium", "source": name,
            "path": path, "detail": "no timestamped records",
            "summary": f"{name} has no readable timestamped records."})
    elif age > stale_hours:
        findings.append({
            "type": "source-stale", "severity": "medium", "source": name,
            "path": path, "newest": fmt_ts(newest), "age_hours": age,
            "stale_hours": stale_hours,
            "summary": f"{name} is {age:.1f} hours old, past the "
                       f"{stale_hours}-hour limit."})


def _weekly_missing(name, path, detail, findings, sources):
    sources.append({"name": name, "path": path, "newest": None,
                    "age_hours": None, "read": {}})
    findings.append({
        "type": "source-missing", "severity": "medium", "source": name,
        "path": path, "detail": detail,
        "summary": f"{name} is unavailable ({detail})."})


def _weekly_doc(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("rows"), list)


def analyze(records, weekly_local, weekly_ci, now, days=DEFAULT_DAYS,
            stale_hours=DEFAULT_STALE_HOURS, event_sources=None) -> dict:
    """Build the report dict. `weekly_*` are parsed documents or None (missing)."""
    start = now - timedelta(days=days)
    findings: list[dict] = []
    sources: list[dict] = []
    event_sources = event_sources or []

    runs = blocked = 0
    by_repo: dict[str, dict] = defaultdict(lambda: {"runs": 0, "blocked": 0})
    by_machine: dict[str, dict] = defaultdict(lambda: {"runs": 0, "blocked": 0})
    by_check: Counter = Counter()
    by_hour: Counter = Counter()
    checks_by_repo: dict[str, Counter] = defaultdict(Counter)
    blocked_by_repo: Counter = Counter()
    sig_hours: dict[tuple, dict[datetime, int]] = defaultdict(
        lambda: defaultdict(int))
    runs_by_file: Counter = Counter()

    for rec in records:
        if rec.get("kind") != "hook":
            continue
        when = rec["_ts"]
        if not (start < when <= now):
            continue
        repo = rec.get("repo") if isinstance(rec.get("repo"), str) and rec.get(
            "repo") else NO_REPO
        machine = rec.get("machine") if isinstance(rec.get("machine"), str) \
            and rec.get("machine") else NO_MACHINE
        runs += 1
        runs_by_file[rec.get("_file_machine")] += 1
        by_repo[repo]["runs"] += 1
        by_machine[machine]["runs"] += 1
        failed = rec["failed"]
        if not failed:
            continue
        blocked += 1
        by_repo[repo]["blocked"] += 1
        by_machine[machine]["blocked"] += 1
        blocked_by_repo[repo] += 1
        for check in set(failed):
            by_check[check] += 1
            checks_by_repo[repo][check] += 1
        hour = when.replace(minute=0, second=0, microsecond=0)
        by_hour[hour_key(when)] += 1
        sig = (machine, str(rec.get("root")), repo, str(rec.get("event")),
               tuple(sorted(set(failed))))
        sig_hours[sig][hour] += 1

    malformed = sum(s["malformed"] for s in event_sources)

    # 1. local concentration
    found = _share_finding("concentration", blocked_by_repo, checks_by_repo,
                           "local")
    if found:
        findings.append(found)

    # 2. steady repeats
    for sig in sorted(sig_hours):
        run = _steady(sig_hours[sig])
        if len(run) < STEADY_HOURS:
            continue
        counts = [sig_hours[sig][h] for h in run]
        machine, root, repo, event, failed = sig
        findings.append({
            "type": "steady-repeat", "severity": "high",
            "machine": machine, "root": root, "repo": repo, "event": event,
            "checks": list(failed),
            "first_hour": hour_key(run[0]), "last_hour": hour_key(run[-1]),
            "hours": len(run), "blocked": sum(counts),
            "median_per_hour": float(statistics.median(counts)),
            "summary": (f"{event} on {machine} for {repo} was blocked "
                        f"{sum(counts)} times over {len(run)} consecutive "
                        f"hours; it looks automated (a scheduled job "
                        f"retrying).")})

    # 3. CI
    ci_blocked: Counter = Counter()
    ci_runs: Counter = Counter()
    ci_rows_used = 0
    if _weekly_doc(weekly_ci):
        for row in weekly_ci["rows"]:
            if not isinstance(row, dict) or row.get("kind") != "hook":
                continue
            try:
                week_start = datetime.strptime(
                    row.get("week"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if not (week_start <= now and week_start + timedelta(days=7) > start):
                continue
            repo = row.get("repo") if isinstance(row.get("repo"), str) and row.get(
                "repo") else NO_REPO
            b, i = row.get("blocked"), row.get("invocations")
            ci_blocked[repo] += b if isinstance(b, int) else 0
            ci_runs[repo] += i if isinstance(i, int) else 0
            ci_rows_used += 1
    found = _share_finding("concentration-ci", ci_blocked, None, "CI")
    if found:
        findings.append(found)

    # 4. sources
    if not event_sources:
        _weekly_missing("usage-events", "usage-events/", "no event files",
                        findings, sources)
    for group in event_sources:
        _freshness(
            f"usage-events:{group['machine']}", ", ".join(group["paths"]),
            group["newest"], now, stale_hours,
            {"lines": group["lines"], "malformed": group["malformed"],
             "hook_runs_in_window": runs_by_file[group["machine"]]},
            findings, sources)

    for name, doc, label in (("weekly-local", weekly_local, "weekly-local.json"),
                             ("weekly-ci", weekly_ci, "weekly-ci.json")):
        if not _weekly_doc(doc):
            _weekly_missing(name, label, "missing or invalid", findings, sources)
            continue
        generated = parse_ts(doc.get("generated_at"))
        info = doc.get("sources") if isinstance(doc.get("sources"), list) else []
        if name == "weekly-local":
            hooks = [s for s in info if isinstance(s, dict)
                     and s.get("scope") == "hooks"]
            scanned = [s for s in info if isinstance(s, dict)
                       and s.get("scope") == "transcripts"]
            read = {"runs_scanned": hooks[0].get("runs_scanned") if hooks else None,
                    "sessions_scanned": scanned[0].get("sessions_scanned")
                    if scanned else None}
        else:
            gh = [s for s in info if isinstance(s, dict)]
            read = {"repos_scanned": gh[0].get("repos_scanned") if gh else None,
                    "hook_rows_used": ci_rows_used}
        if generated is None:
            _weekly_missing(name, label, "no generated_at", findings, sources)
            continue
        _freshness(name, label, generated, now, stale_hours, read, findings,
                   sources)
        if name == "weekly-local" and not hooks:
            findings.append({
                "type": "source-missing", "severity": "medium", "source": name,
                "path": label, "detail": "no hooks source: hook counts unknown",
                "summary": f"{name} has no hooks source: hook counts unknown."})

    return {
        "schema": SCHEMA,
        "generated_at": fmt_ts(now),
        "window": {"from": fmt_ts(start), "to": fmt_ts(now), "days": days},
        "local": {
            "runs": runs, "blocked": blocked, "malformed": malformed,
            "by_repo": {k: dict(v) for k, v in by_repo.items()},
            "by_check": dict(by_check), "by_hour": dict(by_hour),
            "by_machine": {k: dict(v) for k, v in by_machine.items()}},
        "ci": {"blocked_by_repo": dict(ci_blocked),
               "runs_by_repo": dict(ci_runs)},
        "sources": sources,
        "findings": findings,
    }


def _cell(text) -> str:
    return str(text).replace("|", "\\|")


def _read_text(read: dict) -> str:
    return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(read.items())
                     if v is not None) or "-"


def render_markdown(report: dict) -> str:
    win, local = report["window"], report["local"]
    out = [f"## Git hook blocks, {win['from'][:10]} to {win['to'][:10]}", ""]
    out.append(f"{local['runs']} local hook runs, {local['blocked']} blocked, "
               f"{len(report['findings'])} findings, "
               f"{local['malformed']} malformed lines skipped.")
    out += ["", "### Findings", ""]
    if report["findings"]:
        for f in report["findings"]:
            out.append(f"- **{f['severity']}** `{f['type']}`: {f['summary']}")
    else:
        out.append("None.")

    def table(title, head, rows):
        out.extend(["", title, ""])
        if not rows:
            out.append("None.")
            return
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        out.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)

    repos = sorted(local["by_repo"].items(),
                   key=lambda kv: (-kv[1]["blocked"], kv[0]))
    table("### Local blocks by repo", ["repo", "runs", "blocked"],
          [(k, v["runs"], v["blocked"]) for k, v in repos])
    table("### By check", ["check", "blocked runs"],
          sorted(local["by_check"].items(), key=lambda kv: (-kv[1], kv[0])))
    out.extend(["", "### By hour (UTC)", ""])
    hours = sorted(local["by_hour"].items())
    if hours:
        out.extend(f"- {h}: {n}" for h, n in hours[:MAX_HOUR_LINES])
        if len(hours) > MAX_HOUR_LINES:
            out.append(f"… {len(hours) - MAX_HOUR_LINES} more hours")
    else:
        out.append("None.")
    ci = report["ci"]
    table("### CI blocks by repo", ["repo", "invocations", "blocked"],
          [(k, ci["runs_by_repo"].get(k, 0), v) for k, v in
           sorted(ci["blocked_by_repo"].items(), key=lambda kv: (-kv[1], kv[0]))])
    table("### Sources", ["source", "newest", "age h", "read"],
          [(f"{s['name']} ({s['path']})", s["newest"] or "-",
            "-" if s["age_hours"] is None else f"{s['age_hours']:.1f}",
            _read_text(s["read"])) for s in report["sources"]])
    return "\n".join(out) + "\n"


def _load_json(path):
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse_now(text: str) -> datetime:
    when = parse_ts(text)
    if when is None:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text!r}")
    return when


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--events", required=True, help="events branch checkout")
    ap.add_argument("--weekly-local")
    ap.add_argument("--weekly-ci")
    ap.add_argument("--now", type=_parse_now)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--json-out")
    ap.add_argument("--markdown-out")
    args = ap.parse_args(argv)
    if not Path(args.events).is_dir():
        print("error: --events is not a directory", file=sys.stderr)
        return 2
    now = args.now or datetime.now(timezone.utc)
    records, event_sources = load_events(args.events)
    report = analyze(records, _load_json(args.weekly_local),
                     _load_json(args.weekly_ci), now, args.days,
                     args.stale_hours, event_sources=event_sources)
    markdown = render_markdown(report)
    if not (args.json_out or args.markdown_out):
        sys.stdout.write(markdown)
        return 0
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown, encoding="utf-8")
    print(f"hook block report: {report['local']['runs']} runs, "
          f"{report['local']['blocked']} blocked, {len(report['findings'])} findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
