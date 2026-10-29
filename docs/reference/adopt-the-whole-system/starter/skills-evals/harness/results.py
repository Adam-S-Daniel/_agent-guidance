"""results.py: the one reader for the harness's run directories.

The gate scripts (compute_gates.py, plan_week.py) read results ONLY through
this module, so if run_eval.py's output changes, this file is the one place to
reconcile.

The ingest workflow copies each run to `routine-results/<run id>/` on
`persistent/eval-results`. A run directory holds (written by run_eval.py,
checked by ingest_validate.py; only these keys are read here):

    run.json              # one per run
      {"schema_version": 1, "run_id": "20261028T120000Z-1a2b3c",
       "skill": "workflow-path-audit",       # or "guidance/<section-id>"
       "fixture": "evals/workflow-path-audit/basic",
       "cli_version": "2.1.300", "models": {"arms": "claude-example-model"},
       "arms": ["with_skill", "without_skill"], ...}
    <arm>/summary.json    # one per arm: with_skill | without_skill | bare
      {"schema_version": 1, "run_id": ..., "skill": ..., "fixture": ...,
       "model": "claude-example-model", "arm": "with_skill",
       "trials": [
         {"trial": 1,
          "status": "ok",     # ok | max_turns | error | timeout | budget_stop
          "errored": false,   # true: scored as zero
          "pass_rate": 0.8,   # share of objective checks passed, 0 to 1
          "tokens": {"input_tokens": 1, ...}},   # summed here; {} when errored
         {"trial": 2, "status": "budget_stop", "errored": false}],
       "judge": {"score": 7.5, "dimensions": {...}} | null}   # ARM level

A budget_stop trial never ran and counts as nothing. An errored trial counts
as zero. The judge score belongs to the arm, not to a trial, and is null when
no judge ran. cli_version lives only in run.json.

Only `<root>/routine-results/<run id>/` is read. A malformed file raises
ResultsError; the caller decides whether that blocks a verdict (the gates
treat it as blocking).
"""
import json
import os
import re
from dataclasses import dataclass

ARMS = ("with_skill", "without_skill", "bare")
STATUSES = ("ok", "max_turns", "error", "timeout", "budget_stop")
RUN_ID_RE = r"\A[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z"


class ResultsError(ValueError):
    pass


@dataclass(frozen=True)
class Trial:
    pass_rate: float
    errored: bool
    tokens: int | None


@dataclass(frozen=True)
class ArmSummary:
    run_id: str
    skill: str
    fixture: str
    model: str
    cli_version: str
    arm: str
    trials: tuple       # trials that ran; budget_stop trials are not here
    judge: float | None = None   # the arm's judge score, 0 to 10

    @property
    def errors(self) -> int:
        return sum(1 for t in self.trials if t.errored)

    @property
    def pass_rate(self) -> float:
        """Mean over trials that ran; an errored trial counts as zero."""
        return sum(0.0 if t.errored else t.pass_rate for t in self.trials) / len(self.trials)

    @property
    def tokens(self) -> float | None:
        vals = [t.tokens for t in self.trials if t.tokens is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass(frozen=True)
class Run:
    """One with/without pair of the same run id, fixture and model."""
    run_id: str
    skill: str
    fixture: str
    model: str
    cli_version: str
    with_arm: ArmSummary
    without_arm: ArmSummary
    errors: int = 0


def _num(v, name, lo, hi, nullable=False):
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ResultsError(f"{name} must be a number")
    if not lo <= v <= hi:
        raise ResultsError(f"{name} out of range")
    return float(v)


def _str(doc, key):
    if not isinstance(doc.get(key), str) or not doc[key]:
        raise ResultsError(f"{key} must be a non-empty string")
    return doc[key]


def parse_run(doc: dict, dir_name: str) -> dict:
    """The fields of run.json the gates use. The directory name is the run id."""
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ResultsError("schema_version must be 1")
    run_id = _str(doc, "run_id")
    if not re.match(RUN_ID_RE, run_id) or run_id != dir_name:
        raise ResultsError("run_id does not match its directory")
    models = doc.get("models")
    if not isinstance(models, dict):
        raise ResultsError("models must be an object")
    arms = doc.get("arms")
    if not isinstance(arms, list) or not arms or not all(a in ARMS for a in arms):
        raise ResultsError("arms must be a non-empty list of known arms")
    return {"run_id": run_id, "skill": _str(doc, "skill"), "fixture": _str(doc, "fixture"),
            "model": _str(models, "arms"), "cli_version": _str(doc, "cli_version"),
            "arms": arms}


def parse_summary(doc: dict, run: dict) -> ArmSummary:
    """One arm's summary.json; `run` is parse_run's result for the same run."""
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ResultsError("schema_version must be 1")
    for k in ("run_id", "skill", "fixture", "model"):
        if _str(doc, k) != run[k]:
            raise ResultsError(f"{k} disagrees with run.json")
    if doc.get("arm") not in run["arms"]:
        raise ResultsError("arm is not one of the run's arms")
    raw = doc.get("trials")
    if not isinstance(raw, list):
        raise ResultsError("trials must be a list")
    trials = []
    for t in raw:
        if not isinstance(t, dict) or t.get("status") not in STATUSES \
                or not isinstance(t.get("errored"), bool):
            raise ResultsError("each trial needs a known status and a boolean errored")
        if t["status"] == "budget_stop":
            continue    # never ran: counts as nothing
        tokens = t.get("tokens")
        if tokens is not None:
            if not isinstance(tokens, dict) or not all(
                    isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in tokens.values()):
                raise ResultsError("tokens must be an object of non-negative integers")
            tokens = sum(tokens.values()) if tokens else None
        pass_rate = 0.0 if t["errored"] else _num(t.get("pass_rate"), "pass_rate", 0, 1)
        trials.append(Trial(pass_rate, t["errored"], tokens))
    judge = doc.get("judge")
    if judge is not None:
        if not isinstance(judge, dict):
            raise ResultsError("judge must be an object or null")
        judge = _num(judge.get("score"), "judge score", 0, 10)
    return ArmSummary(doc["run_id"], doc["skill"], doc["fixture"], doc["model"],
                      run["cli_version"], doc["arm"], tuple(trials), judge)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ResultsError(f"unreadable results file: {type(e).__name__}") from e


def load_arms(root: str) -> list:
    arms = []
    base = os.path.join(root, "routine-results")
    if not os.path.isdir(base):
        return arms
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        run = parse_run(_read(os.path.join(d, "run.json")), name)
        for arm in run["arms"]:
            summary = parse_summary(_read(os.path.join(d, arm, "summary.json")), run)
            if summary.arm != arm:
                raise ResultsError("arm disagrees with its directory")
            arms.append(summary)
    return arms


def pair_runs(arms: list) -> list:
    """Pair with_skill and without_skill arms by (run, skill, fixture, model).
    An unpaired arm is dropped (a run with one arm tells nothing about the
    skill), and so is an arm whose every trial was budget-stopped. Sorted by
    run id, which starts with a UTC timestamp."""
    by = {}
    for a in arms:
        if a.trials:
            by.setdefault((a.run_id, a.skill, a.fixture, a.model), {})[a.arm] = a
    runs = []
    for (run_id, skill, fixture, model), d in by.items():
        if "with_skill" in d and "without_skill" in d:
            w, wo = d["with_skill"], d["without_skill"]
            runs.append(Run(run_id, skill, fixture, model, w.cli_version, w, wo,
                            w.errors + wo.errors))
    runs.sort(key=lambda r: (r.run_id, r.skill, r.fixture, r.model))
    return runs


def load_runs(root: str) -> list:
    return pair_runs(load_arms(root))
