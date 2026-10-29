#!/usr/bin/env python3
"""ingest_validate.py: judge a pushed results branch as DATA (guide 9.4 step 5).

The ingest workflow checks the branch out to a separate directory and never
runs anything from it. This script decides whether its files may be copied to
`persistent/eval-results` under routine-results/<run id>/.

    ingest_validate.py --branch claude/eval-results-<run id> \
        --root <branch checkout> --changed <NUL-separated paths> [--dest <results checkout>]

Rules (every one refuses; there is no partial ingest):
  * the branch name matches claude/eval-results-<run id> exactly;
  * every path the branch changed is eval-results/<run id>/<allowed file>;
  * allowed files: run.json, report.md, judge.json, <arm>/summary.json,
    <arm>/trace.json for arm in with_skill, without_skill, bare;
  * regular files only (no symlinks), each at most 256 KiB, 2 MiB in all,
    at most 20 files;
  * each JSON file parses and matches a closed schema (unknown keys refused),
    and its run id and arm agree with its directory.
Exit 0 valid (and copied when --dest is given), 1 refused. The refusal says
which rule, never the file's text.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import jsonschema

BRANCH = re.compile(r"^claude/eval-results-([0-9]{8}T[0-9]{6}Z-[0-9a-f]{6})$")
RUN_ID = r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z"
ARMS = ("with_skill", "without_skill", "bare")
MAX_FILE = 256 * 1024
MAX_TOTAL = 2 * 1024 * 1024
MAX_FILES = 20
ALLOWED = re.compile(r"^(run\.json|report\.md|judge\.json|(with_skill|without_skill|bare)/(summary|trace)\.json)$")


class Refused(Exception):
    pass


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


STR = {"type": "string", "maxLength": 300}
NUM = {"type": "number", "minimum": 0}
INT = {"type": "integer", "minimum": 0}
FRAC = {"type": "number", "minimum": 0, "maximum": 1}
METER = {"anyOf": [{"type": "null"},
                   _obj({"seven_day": {"anyOf": [FRAC, {"type": "null"}]},
                         "resets_at": {"anyOf": [STR, {"type": "null"}]}})]}
STOP = {"anyOf": [{"type": "null"},
                  _obj({"reason": {"enum": ["per_run_cap", "account_above", "meter_unreadable", "meter_reset"]},
                        "before_trial": INT})]}
CHECK = _obj({"id": STR, "passed": {"type": "boolean"}, "error": {"type": "boolean"}, "detail": STR})
TRIAL = _obj({"trial": INT, "status": {"enum": ["ok", "max_turns", "error", "timeout", "budget_stop"]},
              "errored": {"type": "boolean"}, "error_kind": {"anyOf": [STR, {"type": "null"}]},
              "num_turns": INT, "cost_usd": NUM, "duration_s": NUM,
              "tokens": {"type": "object", "additionalProperties": INT, "maxProperties": 8},
              "pass_rate": FRAC, "checks": {"type": "array", "items": CHECK, "maxItems": 50}},
             ["trial", "status", "errored"])
JUDGE_SCORE = {"anyOf": [{"type": "null"},
                         _obj({"score": {"type": "number", "minimum": 0, "maximum": 10},
                               "dimensions": {"type": "object", "additionalProperties": {"type": "number"},
                                              "maxProperties": 16}})]}

SCHEMAS = {
    "run": _obj({
        "schema_version": {"const": 1}, "run_id": {"type": "string", "pattern": RUN_ID},
        "mode": {"const": "eval"}, "fixture": STR, "skill": STR,
        "fixture_digest": {"type": "string", "pattern": "^[0-9a-f]{64}\\Z"}, "cli_version": STR,
        "models": _obj({"arms": STR, "judge": {"anyOf": [STR, {"type": "null"}]},
                        "reported": {"type": "array", "items": STR, "maxItems": 16}}),
        "arms": {"type": "array", "items": {"enum": list(ARMS)}, "minItems": 1, "maxItems": 3},
        "trials": {"type": "integer", "minimum": 1, "maximum": 3},
        "started_at": STR, "finished_at": STR,
        "meter_before": METER, "meter_after": METER,
        "meter_delta": {"anyOf": [NUM, {"type": "null"}]},
        "allowance_cost": {"anyOf": [NUM, {"type": "null"}]},
        "budget_stop": STOP, "judged": {"type": "boolean"},
        "guards": _obj({"config_dir_clean": {"const": True}, "sandbox_available": {"const": True},
                        "fixture_unchanged": {"const": True}}),
    }),
    "summary": _obj({
        "schema_version": {"const": 1}, "run_id": {"type": "string", "pattern": RUN_ID},
        "fixture": STR, "skill": STR, "arm": {"enum": list(ARMS)}, "model": STR,
        "trials_requested": INT, "trials_run": INT, "trials_skipped": INT,
        "errored_trials": INT, "check_errors": INT, "pass_rate": FRAC,
        "pass_rate_trials": {"type": "array", "items": FRAC, "maxItems": 3},
        "tokens_total": INT, "cost_usd_total": NUM, "turns_total": INT,
        "trials": {"type": "array", "items": TRIAL, "maxItems": 3},
        "judge": JUDGE_SCORE, "budget_stop": STOP,
    }),
    "trace": _obj({"arm": {"enum": list(ARMS)},
                   "trials": {"type": "array", "maxItems": 3, "items": _obj({
                       "trial": INT, "tool_errors": INT,
                       "tools": {"type": "array", "maxItems": 200,
                                 "items": {"type": "string", "pattern": r"^[A-Za-z0-9_:.-]{1,64}\Z"}}})}}),
    "judge": _obj({
        "error": {"type": "boolean"},
        "mapping": {"type": "object", "additionalProperties": {"enum": ["A", "B", "C"]}, "maxProperties": 3},
        "scores": {"type": "object", "additionalProperties": JUDGE_SCORE, "maxProperties": 3}}),
}


def check_branch(branch: str) -> str:
    m = BRANCH.fullmatch(branch or "")
    if not m:
        raise Refused("branch name")
    return m.group(1)


def check_changed(paths: list[str], run_id: str) -> None:
    if not paths:
        raise Refused("no changed files")
    prefix = f"eval-results/{run_id}/"
    for p in paths:
        if not p.startswith(prefix) or not ALLOWED.fullmatch(p[len(prefix):]):
            raise Refused("changed path outside eval-results/<run id>/ allowlist")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"{path.name} is not valid JSON") from None


def check_tree(root: Path, run_id: str) -> list[Path]:
    """Validate eval-results/<run id>/ under `root`; return its relative files."""
    base = root / "eval-results" / run_id
    if base.is_symlink() or not base.is_dir():
        raise Refused("results directory missing")
    top = root / "eval-results"
    if top.is_symlink() or [p.name for p in top.iterdir()] != [run_id]:
        raise Refused("eval-results holds more than the run directory")
    files, total = [], 0
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        if p.is_symlink():
            raise Refused("symlink")
        if p.is_dir():
            if rel not in ARMS:
                raise Refused("unexpected directory")
            continue
        if not p.is_file() or not ALLOWED.fullmatch(rel):
            raise Refused("unexpected file")
        size = p.stat().st_size
        total += size
        if size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
            raise Refused("size limit")
        files.append(Path(rel))
    names = {f.as_posix() for f in files}
    if not {"run.json", "report.md"} <= names:
        raise Refused("run.json and report.md are required")
    try:
        (base / "report.md").read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        raise Refused("report.md is not text") from None
    run = _load(base / "run.json")
    _validate(run, "run")
    if run["run_id"] != run_id:
        raise Refused("run.json run id differs from the branch")
    for arm in run["arms"]:
        if f"{arm}/summary.json" not in names:
            raise Refused("an arm in run.json has no summary")
    for rel in sorted(names):
        if rel in ("run.json", "report.md"):
            continue
        doc = _load(base / rel)
        kind = "judge" if rel == "judge.json" else rel.split("/")[1].split(".")[0]
        _validate(doc, kind)
        if kind in ("summary", "trace"):
            if doc["arm"] != rel.split("/")[0] or doc["arm"] not in run["arms"]:
                raise Refused("arm differs from its directory or run.json")
            if kind == "summary" and doc["run_id"] != run_id:
                raise Refused("summary run id differs from the branch")
    return sorted(files)


def _validate(doc: object, kind: str) -> None:
    try:
        jsonschema.validate(doc, SCHEMAS[kind])
    except jsonschema.ValidationError:
        raise Refused(f"{kind} JSON shape") from None


def copy_to(root: Path, run_id: str, dest: Path, files: list[Path]) -> Path:
    target = dest / "routine-results" / run_id
    if target.exists():
        raise Refused("run already ingested")
    for rel in files:
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "eval-results" / run_id / rel, out)
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--branch", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--changed", required=True, help="file of NUL-separated changed paths")
    p.add_argument("--dest")
    a = p.parse_args(argv)
    try:
        run_id = check_branch(a.branch)
        raw = Path(a.changed).read_bytes().decode("utf-8", "strict")
        check_changed([x for x in raw.split("\0") if x], run_id)
        files = check_tree(Path(a.root), run_id)
        if a.dest:
            copy_to(Path(a.root), run_id, Path(a.dest), files)
    except Refused as r:
        print(f"REFUSED: {r}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError):
        print("REFUSED: unreadable input", file=sys.stderr)
        return 1
    print(f"valid: {run_id}, {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
