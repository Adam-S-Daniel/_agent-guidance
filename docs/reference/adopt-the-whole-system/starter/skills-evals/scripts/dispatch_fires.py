#!/usr/bin/env python3
"""dispatch_fires.py <plan.json>: fire a plan's fires one at a time.

Calls `gh workflow run routine-eval-fire.yml -f <input>=<value> ...` for each
fire, with the fire workflow's own workflow_dispatch inputs (inputs_for), so
fire_inputs.py on the other side validates exactly what was sent. Keeps at
most 3 fires in flight (waits while 3 are queued or running), and waits 60
seconds between fires. Never retries a fire: there is no idempotency key, so a
retry after a lost response could start a second run.

plan.json is {"fires": [...]}; a fire is one of
  {"mode": "eval", "skill", "fixture", "model", "arms", "trials"}   (plan_week.py)
  {"mode": "improve", "skill"}                                     (plan_week.py)
  {"mode": "scaffold", "repo", "number"}                            (fixture supply)
  {"mode": "draft", "lesson_id"}                                    (pick_lesson.py)
Needs GH_TOKEN with actions: write on this repo (the workflow's own token).
With --dry-run it prints the inputs and calls nothing. A draft fire carries
only the lesson's opaque id, never its subject or text (pick_lesson.py).
"""
import datetime as dt
import json
import secrets
import subprocess
import sys
import time

WORKFLOW = "routine-eval-fire.yml"
MAX_IN_FLIGHT = 3
SPACING_SECONDS = 60


def new_run_id():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(3)


def inputs_for(fire, run_id):
    """The workflow_dispatch inputs for one fire, as strings. Inputs left out
    take the workflow's defaults."""
    mode = fire["mode"]
    if mode == "improve":
        return {"mode": "improve", "skill": fire["skill"], "run_id": run_id}
    if mode == "scaffold":
        return {"mode": "scaffold", "pr": f"{fire['repo']}#{int(fire['number'])}", "run_id": run_id}
    if mode == "draft":
        return {"mode": "draft", "lesson_id": fire["lesson_id"], "run_id": run_id}
    if mode == "eval":
        out = {"mode": "eval", "fixture": f"evals/{fire['skill']}/{fire['fixture']}",
               "arms": fire["arms"], "trials": str(fire["trials"]), "run_id": run_id}
        if fire.get("model"):
            out["model"] = fire["model"]
        return out
    raise ValueError("unknown mode")


def gh_in_flight():
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "50",
         "--json", "status"], capture_output=True, check=True, text=True).stdout
    return sum(1 for r in json.loads(out) if r["status"] in ("queued", "in_progress", "waiting"))


def gh_dispatch(inputs):
    argv = ["gh", "workflow", "run", WORKFLOW]
    for k, v in inputs.items():
        argv += ["-f", f"{k}={v}"]
    subprocess.run(argv, check=True, capture_output=True)


def dispatch(fires, in_flight=gh_in_flight, run=gh_dispatch, sleep=time.sleep,
             run_id=new_run_id, max_in_flight=MAX_IN_FLIGHT, spacing=SPACING_SECONDS,
             poll_seconds=30):
    sent = []
    for i, fire in enumerate(fires):
        while in_flight() >= max_in_flight:
            sleep(poll_seconds)
        inputs = inputs_for(fire, run_id())
        run(inputs)
        sent.append(inputs)
        if i < len(fires) - 1:
            sleep(spacing)
    return sent


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")]
    with open(paths[0], encoding="utf-8") as f:
        fires = json.load(f)["fires"]
    if dry:
        for fire in fires:
            print(json.dumps(inputs_for(fire, new_run_id())))
        return 0
    sent = dispatch(fires)
    print(f"dispatched {len(sent)} fires")
    return 0


if __name__ == "__main__":
    sys.exit(main())
