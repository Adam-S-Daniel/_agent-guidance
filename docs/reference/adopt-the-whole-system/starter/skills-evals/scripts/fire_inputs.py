#!/usr/bin/env python3
"""fire_inputs.py: validate a workflow_dispatch event and build the fire payload.

Reads the event JSON from a FILE ($GITHUB_EVENT_PATH), never from `${{ inputs.* }}`
in a `run:` block, so an input cannot become shell text and is not echoed into
the log. Run it on the default branch's checkout, before any secret is in a step.

    fire_inputs.py --event "$GITHUB_EVENT_PATH" --payload-out payload.json \
        [--roster evals/roster.yml] [--repo-root .] [--github-output "$GITHUB_OUTPUT"]

Modes (payloads as docs/routine-prompt.md and docs/routine-prompts.md give them):
  eval      inputs: mode, run_id, fixture, arms, trials, [model]
  improve   inputs: mode, run_id, skill, trials
  scaffold  inputs: mode, run_id, pr ("<owner>/<repo>#<number>")
            payload {"mode","repo","number","run_id"}
  draft     inputs: mode, run_id, lesson_id (64 hex: the sha256 of the subject)
            payload {"mode","lesson_id","run_id"}; the lesson itself never
            passes through this public repo (scripts/pick_lesson.py)
Every input another mode uses must be empty (arms and trials may keep their
defaults). A refusal prints a fixed message. Exit 0 valid, 1 refused.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
SEGMENT = r"[a-z0-9][a-z0-9._-]{0,62}"
FIXTURE = re.compile(rf"^evals/(?:{SEGMENT})/(?:{SEGMENT})(?:/(?:{SEGMENT}))?$")
SKILL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MODEL = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,62}$")
ARMS = {"both", "with", "without", "bare"}
KNOWN = {"mode", "run_id", "fixture", "arms", "trials", "model", "skill", "pr", "lesson_id"}
MODES = ("eval", "improve", "scaffold", "draft")
PR = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})#([1-9][0-9]{0,9})$")
LESSON_ID = re.compile(r"^[0-9a-f]{64}$")
# The inputs each mode reads; any other non-default input is refused.
USES = {"eval": {"fixture", "arms", "trials", "model"}, "improve": {"skill", "trials"},
        "scaffold": {"pr"}, "draft": {"lesson_id"}}
DEFAULTS = {"arms": "both", "trials": "3"}


class Refused(Exception):
    pass


def roster_arm_ids(path: Path) -> list[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return [a["id"] for a in doc["arms"]]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        raise Refused("roster unreadable") from None


def build_payload(event: object, roster: Path | None, root: Path | None) -> dict:
    inputs = event.get("inputs") if isinstance(event, dict) else None
    if not isinstance(inputs, dict) or not set(inputs) <= KNOWN:
        raise Refused("inputs missing or unknown")
    get = lambda k: inputs.get(k) if isinstance(inputs.get(k), str) else ""  # noqa: E731
    mode, run_id = get("mode"), get("run_id")
    if not RUN_ID.fullmatch(run_id):
        raise Refused("run_id shape")
    if mode not in MODES:
        raise Refused("mode")
    for k in KNOWN - {"mode", "run_id"} - USES[mode]:
        if get(k) not in ("", DEFAULTS.get(k, "")):
            raise Refused(f"{k} is not a {mode} input")
    trials = get("trials")
    if trials not in {"1", "2", "3"}:
        raise Refused("trials range")
    if mode == "eval":
        fixture, arms, model = get("fixture"), get("arms"), get("model")
        if not FIXTURE.fullmatch(fixture) or ".." in fixture:
            raise Refused("fixture path shape")
        if root is not None and not (root / fixture / "fixture.yaml").is_file():
            raise Refused("fixture not on the default branch")
        if arms not in ARMS:
            raise Refused("arms value")
        payload = {"mode": "eval", "run_id": run_id, "fixture": fixture, "arms": arms,
                   "trials": int(trials)}
        if model:
            if not MODEL.fullmatch(model) or (roster and model not in roster_arm_ids(roster)):
                raise Refused("model not a roster seat")
            payload["model"] = model
        return payload
    if mode == "improve":
        skill = get("skill")
        if not SKILL.fullmatch(skill):
            raise Refused("skill shape")
        if root is not None and not (root / "evals" / skill).is_dir():
            raise Refused("skill has no fixtures")
        return {"mode": "improve", "run_id": run_id, "skill": skill, "trials": int(trials)}
    if mode == "scaffold":
        m = PR.fullmatch(get("pr"))
        if not m:
            raise Refused("pr shape")
        return {"mode": "scaffold", "repo": m.group(1), "number": int(m.group(2)), "run_id": run_id}
    lesson_id = get("lesson_id")
    if not LESSON_ID.fullmatch(lesson_id):
        raise Refused("lesson_id shape")
    return {"mode": "draft", "lesson_id": lesson_id, "run_id": run_id}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event", required=True)
    p.add_argument("--payload-out", required=True)
    p.add_argument("--roster")
    p.add_argument("--repo-root", help="check that fixture/skill exist under this checkout")
    p.add_argument("--github-output")
    a = p.parse_args(argv)
    try:
        try:
            event = json.loads(Path(a.event).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused("event unreadable") from None
        payload = build_payload(event, Path(a.roster) if a.roster else None,
                                Path(a.repo_root) if a.repo_root else None)
    except Refused as r:
        print(f"REFUSED: {r}", file=sys.stderr)
        return 1
    Path(a.payload_out).write_text(json.dumps(payload, separators=(",", ":")))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:  # values are regex-validated tokens
            f.write(f"mode={payload['mode']}\nrun_id={payload['run_id']}\n")
    print(f"valid: {payload['mode']} {payload['run_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
