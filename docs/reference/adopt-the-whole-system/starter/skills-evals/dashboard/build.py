#!/usr/bin/env python3
"""Build the project dashboard's snapshot datasets.

Every dataset is computed from three kinds of input, so nothing on the page is
typed by hand that a read could supply:

  --results  an extracted copy of the `persistent/eval-results` branch
             (test runs, the allowance meter, usage counts)
  --inputs   JSON dumps the refresh session collects each run from GitHub and
             the project (threads, scheduled tasks, workflow runs, issues and
             PRs labeled needs-owner, environment reviews, loop status);
             see README.md, "Inputs"
  --config   the owner's own lists that no read can supply (cleanup items,
             gate conditions, decisions, routine descriptions, loop design);
             kept outside this public repo, see config.example/

The repo's own evals/budget.yml and evals/roster.yml are read directly.

Usage:
  python3 dashboard/build.py --results <dir> --inputs <dir> --config <dir> --out <dir> [--now ISO]
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLACEHOLDER_ID = "none"


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(ts):
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load(path, default=None):
    if not os.path.exists(path):
        if default is None:
            sys.exit(f"missing input {path}")
        return default
    with open(path) as f:
        return json.load(f)


def fired_at(run_name):
    stamp = run_name.split("-")[0]
    return iso(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc))


def arm_model(d):
    model = (d.get("cross_model") or {}).get("model")
    if model:
        return model
    used = d.get("models_used") or []
    return ([m for m in used if "haiku" not in m] or used or [None])[0]


def model_name(model_id):
    """claude-sonnet-5-5 -> Sonnet 5.5"""
    parts = (model_id or "").split("-")
    if len(parts) < 3 or parts[0] != "claude":
        return model_id
    return parts[1].capitalize() + " " + ".".join(parts[2:])


# --- Tests tab -----------------------------------------------------------------

def objective(d):
    if not d.get("aggregate") and d.get("objective_checks") is not None:
        ch = d["objective_checks"]  # single-trial summary: no aggregate block
        return sum(1 for c in ch if c.get("passed")), len(ch)
    o = (d.get("aggregate") or {}).get("objective") or {}
    return o.get("mean_passed"), o.get("mean_total")


def judge(d):
    j = (d.get("aggregate") or {}).get("judge") or {}
    o = j.get("overall")
    return (o.get("mean") if isinstance(o, dict) else o), j.get("status")


def cost(d):
    if not d.get("aggregate"):
        return (d.get("agent") or {}).get("cost_usd")
    return ((d.get("aggregate") or {}).get("cost_usd") or {}).get("sum")


def add_costs(a, b):
    return None if a is None and b is None else (a or 0) + (b or 0)


def cli_version(d):
    return ((d.get("harness") or {}).get("version") or "").replace(" (Claude Code)", "")


def eval_rows(er, report_base):
    """Paired runs (with/without a skill or guidance section) and bare baseline runs."""
    rows, bare = [], []
    root = Path(er) / "routine-results"
    for run in sorted(root.iterdir()) if root.is_dir() else []:
        for rep in run.rglob("report.md"):
            base = rep.parent
            arms = {}
            for s in base.rglob("summary.json"):
                if "trial-" in str(s):
                    continue
                d = json.loads(s.read_text())
                arms[d["arm"]] = (s, d)
            url = report_base + str(rep.relative_to(er))
            rel = base.relative_to(run).parts  # skill/ts[/fixture]
            if "with_guidance_full" in arms and "with_guidance_full_minus_section" in arms:
                # One trial per arm; the section's effect is full vs full minus the section.
                g, gm = arms["with_guidance_full"][1], arms["with_guidance_full_minus_section"][1]
                wp, tot = objective(g)
                wop, _ = objective(gm)
                sec = g.get("section") or base.parent.name
                rows.append({
                    "run_id": run.name, "fired_at": fired_at(run.name),
                    "fixture": "guidance/" + sec + "/" + (g.get("fixture_name") or ""),
                    "skill": "guidance/" + sec, "model": arm_model(g), "trials": 1,
                    "with_skill": wp, "without_skill": wop, "checks": tot, "gain": wp - wop,
                    "with_judge": None, "without_judge": None,
                    "judge": "skipped: checks decided" if (g.get("judge") or {}).get("status") == "skipped" else "not run",
                    "with_share": wp / tot if tot else None, "without_share": wop / tot if tot else None,
                    "cost_usd": add_costs(cost(g), cost(gm)),
                    "meter_points": None, "account_week_after": None,
                    "cli": cli_version(g), "report_url": url,
                })
                continue
            if "without_skill" in arms and "with_skill" not in arms:
                # Bare baseline (ADR 0012 addendum): one isolation arm, nothing of ours loaded.
                path, b = arms["without_skill"]
                bp, tot = objective(b)
                if bp is None:
                    continue
                sub = path.relative_to(base).parts
                bare.append({
                    "run_id": run.name, "fired_at": fired_at(run.name),
                    "fixture": rel[0] + ("/" + sub[0] if len(sub) > 2 else ""), "skill": rel[0],
                    "model": arm_model(b), "trials": b.get("n") or 1, "bare": bp, "checks": tot,
                    "bare_share": bp / tot if tot else None, "cost_usd": cost(b), "report_url": url,
                })
                continue
            if "with_skill" not in arms or "without_skill" not in arms:
                continue
            path, w = arms["with_skill"]
            wo = arms["without_skill"][1]
            wp, tot = objective(w)
            wop, _ = objective(wo)
            if wp is None or wop is None:
                continue
            wj, js = judge(w)
            woj, _ = judge(wo)
            sub = path.relative_to(base).parts
            md = ((w.get("usage") or {}).get("meter_delta") or {}).get("windows", {}).get("seven_day") or {}
            rows.append({
                "run_id": run.name, "fired_at": fired_at(run.name),
                "fixture": rel[0] + ("/" + sub[0] if len(sub) > 2 else ""), "skill": rel[0],
                "model": arm_model(w), "trials": w.get("n"),
                "with_skill": wp, "without_skill": wop, "checks": tot, "gain": wp - wop,
                "with_judge": wj, "without_judge": woj,
                "judge": "ran" if (wj is not None or woj is not None) else ("skipped: checks decided" if js == "skipped" else "not run"),
                "with_share": wp / tot if tot else None, "without_share": wop / tot if tot else None,
                "cost_usd": add_costs(cost(w), cost(wo)),
                "meter_points": None if md.get("delta") is None else md["delta"] * 100,
                "account_week_after": None if md.get("to") is None else md["to"] * 100,
                "cli": cli_version(w), "report_url": url,
            })
    rows.sort(key=lambda r: r["fired_at"], reverse=True)
    # Each bare run beside the newest paired run of the same test and model.
    latest = {}
    for r in rows:
        latest.setdefault((r["fixture"], r["model"]), r)
    for b in bare:
        p = latest.get((b["fixture"], b["model"]))
        if not p:  # older paired runs name only the skill (one fixture, no subfolder)
            q = latest.get((b["skill"], b["model"]))
            p = q if q and q["checks"] == b["checks"] else None
        b["paired_at"] = p["fired_at"] if p else None
        b["with_share"] = p["with_share"] if p else None
        b["without_share"] = p["without_share"] if p else None
    bare.sort(key=lambda r: r["fired_at"], reverse=True)
    if not bare:
        bare = [{"run_id": PLACEHOLDER_ID, "fired_at": None, "fixture": "No bare runs yet", "skill": None,
                 "model": None, "trials": None, "bare": None, "checks": None, "bare_share": None,
                 "cost_usd": None, "report_url": None, "paired_at": None, "with_share": None,
                 "without_share": None}]
    if not rows:
        rows = [{"run_id": PLACEHOLDER_ID, "fired_at": None, "fixture": "No scored runs yet", "skill": None,
                 "model": None, "trials": None, "with_skill": None, "without_skill": None, "checks": None,
                 "gain": None, "with_judge": None, "without_judge": None, "judge": None, "with_share": None,
                 "without_share": None, "cost_usd": None, "meter_points": None, "account_week_after": None,
                 "cli": None, "report_url": None}]
    return rows, bare


def allowance(er, now):
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "harness"))
    import yaml
    import allowance_guard as g
    limits = yaml.safe_load(open(REPO / "evals" / "budget.yml"))
    runs = g.ingested_runs(Path(er), now)
    st = g.account(Path(er), limits, now, (len(runs), 0))
    found = g.newest_snapshot(runs)
    resets = None
    if found:
        w7 = ((found[1].get("info") or {}).get("unifiedWindows") or {}).get("seven_day") or {}
        if w7.get("resetsAt"):
            resets = iso(datetime.fromtimestamp(w7["resetsAt"], timezone.utc))
    return [{
        "measure": "eval_spend_7d", "points": st["spent"] * 100, "runs": st["runs"],
        "account_week_used": None if st["account_used"] is None else st["account_used"] * 100,
        "account_reading_at": iso(found[0]) if found else None, "week_resets_at": resets,
        "week_elapsed": st["week_elapsed"], "pace_room_points": st["pace_room"] * 100,
        "reservation_points": st["reservation"] * 100, "computed_at": iso(now),
    }]


BUDGET_KEYS = ["weekly_cap", "per_run_cap", "per_run_overrun", "stop_when_account_above"]


def budget():
    import yaml
    b = yaml.safe_load(open(REPO / "evals" / "budget.yml"))
    return [{"setting": k, "value": b[k]} for k in BUDGET_KEYS if k in b]


def roster(cfg):
    """Model list rows from evals/roster.yml; `why` text and dropped models come from config."""
    import yaml
    r = yaml.safe_load(open(REPO / "evals" / "roster.yml"))
    why = cfg.get("why", {})
    link = cfg.get("link")
    rows = [{"model": model_name(a["id"]), "id": a["id"], "role": "Tested", "status": "In use",
             "why": why.get(a["id"], " ".join((a.get("reason") or "").split())), "link": link}
            for a in r.get("arms", [])]
    for key, role in (("judge", "Judge"), ("preflight", "Quick check")):
        m = r.get(key) or {}
        if m.get("id"):
            rows.append({"model": model_name(m["id"]), "id": m["id"], "role": role, "status": "In use",
                         "why": why.get(m["id"], " ".join((m.get("reason") or "").split())), "link": link})
    seen = {x["id"] for x in rows}
    rows += [d for d in cfg.get("dropped", []) if d["id"] not in seen]
    return rows


# --- Impact tab ----------------------------------------------------------------

# Outcome keys from _agent-guidance scripts/impact-report.js (kind "repo" rows from CI).
OUTCOME_KEYS = ["prs_opened", "prs_merged", "prs_closed_unmerged", "prs_reverted", "agent_prs_merged", "ci_red_after"]


def usage(er):
    """Concatenate every usage/weekly-*.json (schema 1); counts stay exact."""
    rows, sources = [], []
    for path in sorted(glob.glob(os.path.join(er, "usage", "weekly-*.json"))):
        name = os.path.basename(path)
        d = load(path)
        if d.get("schema") != 1:
            sys.exit(f"{name}: schema {d.get('schema')!r}, expected 1; update build.py and the Impact tab")
        for s in d.get("sources", []):
            sources.append({"file": name, "surface": s.get("surface"), "scope": s.get("scope"), "from": s.get("from"),
                            "to": s.get("to"), "sessions_scanned": s.get("sessions_scanned"),
                            "generated_at": d.get("generated_at")})
        for r in d.get("rows", []):
            o = r.get("outcome") or {}
            # A git hook row named "all" is that week's total across checks; its own kind keeps
            # per-check sums from counting it twice.
            kind = "hook_all" if r["kind"] == "hook" and r["name"] == "all" else r["kind"]
            rows.append({"week": r["week"], "kind": kind, "name": r["name"], "repo": r.get("repo") or "(any repo)",
                         "surface": r.get("surface"), "invocations": r.get("invocations", 0),
                         "sessions": r.get("sessions"), "blocked": r.get("blocked"),
                         "overridden": r.get("overridden"), **{k: o.get(k) for k in OUTCOME_KEYS}})
    if not sources:
        sources = [{"file": PLACEHOLDER_ID, "surface": None, "scope": None, "from": None, "to": None,
                    "sessions_scanned": None, "generated_at": None}]
    if not rows:
        rows = [{"week": None, "kind": PLACEHOLDER_ID, "name": "No usage counts yet", "repo": None, "surface": None,
                 "invocations": 0, "sessions": None, "blocked": None, "overridden": None,
                 **{k: None for k in OUTCOME_KEYS}}]
    return rows, sources


# --- Overview tab: threads, routines, check-ins, waiting -------------------------

THREAD_STATES = {"done": "Done", "working": "Working", "review_ready": "Ready for review",
                 "blocked": "Waiting on you", "failed": "Failed", "stopped": "Stopped"}


def threads(dump, site):
    """Rows from a list_threads dump (its `sessions` list, all pages joined)."""
    titles = site.get("thread_titles", {})
    rows = [{"title": titles.get(s["thread_id"], s["title"]),
             "state": THREAD_STATES.get(s.get("state"), s.get("state")),
             "last_active": s.get("last_activity_at"),
             "link": site["thread_url"] + s["thread_id"]} for s in dump]
    rows.sort(key=lambda r: r["last_active"] or "", reverse=True)
    return rows


def next_cron(expr, after):
    """Next fire time of a five-field UTC cron with numeric minute/hour/day-of-week fields."""
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return None
    if dom != "*" or month != "*":
        return None

    def values(field, lo, hi):
        if field == "*":
            return set(range(lo, hi + 1))
        out = set()
        for part in field.split(","):
            if "-" in part:
                a, b = part.split("-")
                out |= set(range(int(a), int(b) + 1))
            else:
                out.add(int(part))
        return out
    try:
        mins, hours, days = values(minute, 0, 59), values(hour, 0, 23), {d % 7 for d in values(dow, 0, 7)}
    except ValueError:
        return None
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(8 * 24 * 60):
        if t.minute in mins and t.hour in hours and (t.isoweekday() % 7) in days:
            return iso(t)
        t += timedelta(minutes=1)
    return None


TRIGGER_RESULT = {"ROUTINE_RUN_STATUS_SUCCEEDED": "Succeeded", "ROUTINE_RUN_STATUS_FAILED": "Failed",
                  "ROUTINE_RUN_STATUS_RUNNING": "Running", "ROUTINE_RUN_STATUS_PENDING": "Running"}
RUN_RESULT = {"success": "Succeeded", "failure": "Failed", "cancelled": "Cancelled", "timed_out": "Failed",
              "skipped": "Skipped"}


def routines(cfg, triggers, workflow_runs, now):
    """One row per routine. Config gives the words; the last and next run and health come from reads."""
    rows = []
    for r in cfg:
        src = r.get("source") or {}
        last_run = last_result = next_run = None
        enabled = True
        if src.get("kind") == "trigger":
            t = (triggers.get(src["id"]) or {}).get("trigger") or triggers.get(src["id"]) or {}
            lr = t.get("last_run") or {}
            last_run = lr.get("fired_at")
            last_result = TRIGGER_RESULT.get(lr.get("status"), lr.get("status")) if lr else None
            next_run = t.get("next_run_at")
            enabled = t.get("enabled", True)
        elif src.get("kind") == "workflow":
            runs = workflow_runs.get(src["repo"] + ":" + src["workflow"]) or []
            runs = sorted(runs, key=lambda x: x.get("created_at") or "", reverse=True)
            if runs:
                last_run = runs[0].get("created_at")
                last_result = ("Running" if runs[0].get("status") != "completed"
                               else RUN_RESULT.get(runs[0].get("conclusion"), runs[0].get("conclusion")))
            if src.get("cron"):
                next_run = next_cron(src["cron"], now)
        last_run = iso(parse(last_run)) if last_run else r.get("last_run")
        next_run = iso(parse(next_run)) if next_run else None
        if r.get("retired"):
            health = "Retired"
        elif not enabled:
            health = "Paused"
        elif last_result == "Failed":
            health = "Failing"
        elif last_result == "Cancelled":
            health = "Check"
        elif r.get("expect_every_hours") and (
                not last_run or now - parse(last_run) > timedelta(hours=2 * r["expect_every_hours"])):
            health = "Late"
        else:
            health = "OK"
        rows.append({"name": r["name"], "what": r["what"], "schedule": r["schedule"], "last_run": last_run,
                     "last_result": last_result or ("Idle" if r.get("retired") else None), "next_run": next_run,
                     "health": health, "note": r.get("note", ""), "id": src.get("id") or src.get("workflow"),
                     "link": r.get("link")})
    return rows


def checkins(trigger_list, channel_id, now):
    """Pending one-shot scheduled tasks of this project (list_triggers, recurring=false)."""
    rows = [{"when": t["run_once_at"], "what": t.get("name") or "Check-in", "id": t["id"]}
            for t in trigger_list
            if t.get("run_once_at") and t.get("enabled", True) and parse(t["run_once_at"]) > now
            and (not channel_id or t.get("hearth_channel_id") == channel_id)]
    rows.sort(key=lambda r: r["when"])
    return rows or [{"when": None, "what": "None scheduled right now", "id": PLACEHOLDER_ID}]


def waiting(snap, needs_owner, reviews, open_heads, thread_dump, link_states, site):
    """Everything that waits on the owner, from reads first and the manual list last.

    A run waiting on an environment review counts only on the current head of an open
    PR: older runs on the same branch stay `waiting` forever and decide nothing.
    """
    rows = []
    for it in needs_owner:
        labels = {(lb.get("name") if isinstance(lb, dict) else lb) for lb in it.get("labels") or []}
        if "on-hold" in labels or it.get("state", "open") != "open":
            continue
        repo = it.get("repository") or "/".join((it.get("repository_url") or "").split("/")[-2:])
        rows.append({"item": it["title"], "why": "Labeled needs-owner", "since": it.get("created_at"),
                     "link": it["html_url"], "where": repo})
    for rv in reviews:
        if rv.get("head_sha") not in set(open_heads.get(rv["repo"], [])):
            continue
        rows.append({"item": f"Approve the {rv['environment']} review for {rv['name']}",
                     "why": "A required environment review waits on you", "since": rv.get("created_at"),
                     "link": rv["job_url"], "where": rv["repo"]})
    for s in thread_dump:
        if s.get("state") == "blocked" and not s.get("resolved"):
            rows.append({"item": site.get("thread_titles", {}).get(s["thread_id"], s["title"]),
                         "why": "The thread waits on your answer", "since": s.get("last_activity_at"),
                         "link": site["thread_url"] + s["thread_id"], "where": "Thread"})
    groups = {}
    for c in snap.get("cleanup", []):
        if c.get("waiting_on_you") and c["status"] != "Done":
            groups.setdefault(c["waiting_on_you"], []).append(c)
    for reason, items in groups.items():
        rows.append({"item": "Cleanup items " + ", ".join(c["id"] for c in items), "why": reason,
                     "since": min((c.get("waiting_since") or "") for c in items) or None,
                     "link": None, "where": "Cleanup list"})
    for m in snap.get("waiting_manual", []):
        if m.get("link") and link_states.get(m["link"]) in ("closed", "merged"):
            continue
        rows.append(m)
    for r in rows:
        r["id"] = r.get("link") or r["item"]
    return rows or [{"item": "Nothing waiting on you", "why": None, "since": None, "link": None,
                     "where": None, "id": PLACEHOLDER_ID}]


def clean_cleanup(items):
    return [{k: v for k, v in c.items() if k not in ("waiting_on_you", "waiting_since")} for c in items]


# --- Loop tab ------------------------------------------------------------------

GAP_WORDS = {"open": "Not started", "in progress": "In progress", "done": "Done"}


def loop(cfg, status, site):
    gaps = [{"gap": g["gap"], "title": g["title"], "status": GAP_WORDS.get(g["status"], g["status"]),
             "owner": g.get("owner") or "", "link": g.get("pr")} for g in status["gaps"]]
    by = {g["gap"]: g for g in gaps}
    home = site["thread_url"] + cfg["home_thread_id"]

    def derive(ids, override):
        if override:
            return override
        s = [by[i]["status"] for i in ids if i in by]
        if s and all(x == "Done" for x in s):
            return "Done"
        if s and all(x == "Not started" for x in s):
            return "Not started"
        return "In progress"
    stages = []
    for st in cfg["stages"]:
        links = [by[i]["link"] for i in st["gaps"] if i in by and by[i]["link"]]
        stages.append({"order": st["order"], "kind": st["kind"], "stage": st["stage"], "what": st["what"],
                       "status": derive(st["gaps"], st.get("override")), "gaps": ", ".join(st["gaps"]),
                       "link": links[0] if links else home})
    lanes = [{"lane": ln["lane"], "thread": ln["thread"], "link": site["thread_url"] + ln["thread_id"],
              "does": ln["does"], "latest": ln["latest"], "latest_link": ln.get("latest_link")}
             for ln in cfg["lanes"]]
    return stages, cfg["grid"], lanes, gaps


# --- main ----------------------------------------------------------------------

def build(results, inputs, config, now):
    site = load(os.path.join(config, "site.json"))
    snap = load(os.path.join(config, "snapshots.json"))
    inp = lambda name, default=None: load(os.path.join(inputs, name), default)
    thread_dump = inp("threads.json", [])
    trigger_dump = inp("triggers.json", {})
    out = {}
    out["eval_runs"], out["bare_runs"] = eval_rows(results, site["report_base"])
    out["eval_allowance"] = allowance(results, now)
    out["budget"] = budget()
    out["roster"] = roster(load(os.path.join(config, "roster.json"), {}))
    out["usage_rows"], out["usage_sources"] = usage(results)
    out["threads"] = threads(thread_dump, site)
    out["routines"] = routines(load(os.path.join(config, "routines.json")), trigger_dump,
                               inp("workflow_runs.json", {}), now)
    out["checkins"] = checkins(inp("trigger_list.json", []), site.get("project_channel_id"), now)
    out["waiting"] = waiting(snap, inp("needs_owner.json", []), inp("waiting_reviews.json", []),
                             inp("open_pr_heads.json", {}), thread_dump, inp("link_states.json", {}), site)
    out["cleanup"] = clean_cleanup(snap["cleanup"])
    out["gate"] = snap["gate"]
    out["decisions"] = snap["decisions"]
    loop_cfg = os.path.join(config, "loop.json")
    if os.path.exists(loop_cfg):
        (out["loop_stages"], out["loop_grid"], out["loop_lanes"],
         out["loop_gaps"]) = loop(load(loop_cfg), inp("loop_status.json"), site)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("results", "inputs", "config", "out"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--now", help="ISO time to build as of (default: now)")
    a = ap.parse_args(argv)
    now = parse(a.now) if a.now else datetime.now(timezone.utc)
    out = build(a.results, a.inputs, a.config, now)
    os.makedirs(a.out, exist_ok=True)
    for name, rows in out.items():
        if not rows:  # the page reads [] as "a source failed to load"
            sys.exit(f"{name}: empty dataset; give it a placeholder row")
        with open(os.path.join(a.out, name + ".json"), "w") as f:
            json.dump(rows, f, indent=1)
    print(" ".join(f"{k}={len(v)}" for k, v in out.items()))


if __name__ == "__main__":
    main()
