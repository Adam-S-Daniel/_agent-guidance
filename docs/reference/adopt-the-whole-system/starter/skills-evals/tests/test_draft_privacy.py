"""The draft lane never puts a lesson's text or subject anywhere public.

skills-evals is public and lessons come from the private registry. A canary
subject and canary text go in at the pick step; every string the lane hands
to a public place comes back out and must hold neither: the job's log and
outputs, plan.json, the gh workflow-dispatch arguments (its inputs and so its
run name), the fire's validated payload, log and GITHUB_OUTPUT, and
drafted-subjects.json on persistent/eval-results.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

import dispatch_fires as D
import fire_inputs as fi
import pick_lesson as L

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WF = ROOT / ".github" / "workflows"
CANARY_SUBJECT = "zq-canary-subject-71"
CANARY_TEXT = "Zq canary text 9f3k: the agent pushed before the checks ran."
RUN_ID = "20261028T120000Z-1a2b3c"


def leaks(text):
    low = text.lower()
    return [c for c in (CANARY_SUBJECT, CANARY_TEXT, "canary") if c.lower() in low]


def run(*args, env=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=env)


def test_no_lesson_text_or_subject_reaches_any_public_place(tmp_path, monkeypatch):
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / f"{CANARY_SUBJECT}.json").write_text(json.dumps(
        {"subject": CANARY_SUBJECT, "count": 9, "lesson": CANARY_TEXT}))
    (lessons / "other.json").write_text(json.dumps(
        {"subject": "other", "count": 1, "lesson": "Another lesson."}))
    drafted, plan = tmp_path / "drafted-subjects.json", tmp_path / "plan.json"
    drafted.write_text("[]")
    public = {}

    # The pick step: its log and its one output.
    r = run(SCRIPTS / "pick_lesson.py", "pick", "--lessons", lessons, "--drafted", drafted, "--plan-out", plan)
    assert r.returncode == 0, r.stderr
    lid = r.stdout.strip()
    assert lid == L.lesson_id(CANARY_SUBJECT)     # the canary lesson was the one picked
    public["pick log"] = r.stdout + r.stderr
    public["plan.json"] = plan.read_text()

    # dispatch_fires.py: the gh arguments carry every input, and the run name
    # is built from them; its dry run prints the inputs to the log.
    calls = []
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: calls.append(argv))
    sent = D.dispatch(json.loads(plan.read_text())["fires"], in_flight=lambda: 0,
                      sleep=lambda s: None, run_id=lambda: RUN_ID)
    monkeypatch.undo()
    assert len(calls) == 1 and sent[0] == {"mode": "draft", "lesson_id": lid, "run_id": RUN_ID}
    public["gh workflow run argv"] = json.dumps(calls)
    r = run(SCRIPTS / "dispatch_fires.py", plan, "--dry-run")
    assert r.returncode == 0, r.stderr
    public["dispatch dry-run log"] = r.stdout + r.stderr

    # The fire workflow: its run name uses only mode and run_id, and the
    # inputs GitHub shows on the run page are the sent ones over the defaults.
    fire = yaml.safe_load((WF / "routine-eval-fire.yml").read_text())
    assert fire["run-name"] == "fire ${{ inputs.mode }} ${{ inputs.run_id }}"
    spec = fire[True]["workflow_dispatch"]["inputs"]
    event = {"inputs": {k: str(v.get("default", "")) for k, v in spec.items()} | sent[0]}
    public["run page inputs"] = json.dumps(event)
    (tmp_path / "event.json").write_text(json.dumps(event))
    out, ghout = tmp_path / "payload.json", tmp_path / "github_output"
    r = run(SCRIPTS / "fire_inputs.py", "--event", tmp_path / "event.json", "--payload-out", out,
            "--roster", ROOT / "evals" / "roster.yml", "--github-output", ghout)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text()) == {"mode": "draft", "lesson_id": lid, "run_id": RUN_ID}
    public["fire log"] = r.stdout + r.stderr
    public["fire GITHUB_OUTPUT"] = ghout.read_text()
    public["fire payload"] = out.read_text()

    # The record job: drafted-subjects.json on persistent/eval-results.
    r = run(SCRIPTS / "pick_lesson.py", "record", "--drafted", drafted, "--lesson-id", lid)
    assert r.returncode == 0, r.stderr
    assert json.loads(drafted.read_text()) == [lid]
    public["drafted-subjects.json"] = drafted.read_text()
    public["record log"] = r.stdout + r.stderr

    found = {where: hit for where, text in public.items() if (hit := leaks(text))}
    assert not found, found


def test_the_draft_lane_workflows_expose_only_the_id():
    pick_wf = yaml.safe_load((WF / "routine-draft-skill.yml").read_text())
    pick = pick_wf["jobs"]["pick"]
    assert set(pick["outputs"]) == {"lesson_id"}
    # Nothing in the dispatcher job uploads an artifact or writes a summary.
    text = (WF / "routine-draft-skill.yml").read_text()
    assert "upload-artifact" not in text and "GITHUB_STEP_SUMMARY" not in text
    step = next(s for s in pick["steps"] if s.get("id") == "pick")["run"]
    assert "lesson_id=$id" in step and "jq" not in step and ".lesson" not in step and ".subject" not in step
    fire = yaml.safe_load((WF / "routine-eval-fire.yml").read_text())
    names = set(fire[True]["workflow_dispatch"]["inputs"])
    assert "lesson_id" in names and not names & {"lesson", "subject"}
