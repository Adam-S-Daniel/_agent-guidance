#!/usr/bin/env python3
"""The skill retirement verdict (#70, part 1): cells, their classes, and the
verdict over them. Thresholds live in `evals/gate-policy.yml`.

Guidance sections (#124) get the same verdict over their ablation pair
(`guidance_verdict`, at the end of this module): the rules below apply with
"skill" read as "section".

POOLED EVIDENCE (the owner, 2026-10-25), replacing #70's four weekly sweeps.

  * A CELL is (skill, fixture, current roster arm model), for every fixture
    of the skill ON MAIN (scripts/compute_gates.py enumerates them from the
    evals directory) and every arm model of the roster it is given
    (`--roster`). A cell with no run is `missing`; runs of a fixture no
    longer on main, or of a model no longer on the roster, are not read: the
    roster follows current use (the owner, 2026-10-25), so a superseded model
    leaves at once and no evidence is asked of it. A fixture moved to a
    new name with its content unchanged keeps its runs (`FIXTURE_ALIASES`,
    digest-checked by `pooled_fixture`). The arm
    model is the REQUESTED one: `cross_model.model`, which
    harness/run_eval.py writes from the arm's own `--model`
    (`_write_summary`'s `agent_model`) and `run_eval._arm_model` reads back;
    never `models_used`, which includes subagent models.
  * THE UNIT IS THE RUN. Trials within one run are near-identical, so each
    arm of each run is reduced to its mean trial pass rate (and mean judge
    figure), and every interval is over those run means. A run counts for an
    arm only with at least `MIN_TRIALS_PER_RUN` (1, round 5: the weekly
    sweep fires one trial per run) scored trials in that arm.
  * THE ARMS ARE TWO INDEPENDENT SAMPLES of ONE comparison, pooled
    (`pool_cells`, the one function that decides which runs a cell holds)
    against the content ON MAIN, never the newest run's:
      - with arm: the skill's `tested_digest` as the registry checkout gives
        it and the fixture's `fixture_digest` as the evals directory gives it
        (both computed by compute_gates with the functions run_eval uses);
      - without arm: the same fixture digest. It does not hold the subject,
        so it pools across `tested_digest` changes.
    Their arm contexts (`arm_context.skills_digest` and `guidance_digest`)
    are chosen JOINTLY (`_joint_context`, round 5, B6): only a with group
    and a without group sharing a pair key, the same non-subject skills
    (`arm_context.others_digest`) and frozen guidance
    (`context.guidance.digest`), can form the cell, and of those pairs the
    one with the most runs in its smaller arm, on roster models, is chosen;
    a tie, or no such pair, chooses none and the cell is `insufficient`. So
    a without arm from older guidance cannot be pooled against a with arm
    from newer guidance. The arm context cannot be computed from main (it
    is the frozen context a run deployed), so the pair with the most runs
    chooses it, and an honest context change counts once its pair
    outnumbers the old one.
    Any key unknown (no registry given, the skill not found in one, the
    fixture not tracked in Git) makes the cell `insufficient`; there is no
    pool-everything fallback.
  * WHICH RUNS COUNT. Every validated run, whatever its billing, runner or
    location (#370); the verdict records each used run's `run` block and
    links its report. Validation is scripts/compute_gates.py's: a summary of
    the wrong shape skips its run with a reason (`MalformedSummary`), never a
    crash.
  * FIXTURE CLASS. No fixture carries a machine-readable class; DESIGN.md
    ("Four instruments, one harness") says a Class C fixture "says so in its
    `judge:` block: `mode: pairwise`". So a fixture is JUDGE-CARRIED (Class
    C) when its `judge.mode` is pairwise (`run_eval._judge_is_pairwise`) or
    it has no objective checks; when the fixture file is not available, when
    any trial's judge result is pairwise. compute_gates sets
    `judge_carried` on each run.

THE NUMBERS. A trial's pass rate is its checks passed over checks scored; its
judge figure is `overall` of an absolute result and `dimensions_mean` of a
pairwise one, a number from 0 to 10 (a skipped or failed judge result has
none). A trial with a FAILED check whose detail starts with the scorer's
own marker (`INFRASTRUCTURE_DETAIL_PREFIX`), or whose error type is one
(`INFRASTRUCTURE_ERRORS`), is an infrastructure error and is left out;
run_eval still scores it as it does today, the reclassification is made
here. Any other errored trial (an agent failure, a timeout, a budget stop)
is the agent's: it scores 0 on every check of the cell and has no judge
figure, since dropping it, or its run, would compare only the trials that
went well. A cell with any such trial can never be `no-benefit` (at most
`insufficient`; it can still show `benefit`), so errors can only help keep.
An arm of a run COUNTS when it has `MIN_TRIALS_PER_RUN` scored or
agent-errored trials and no scored trial without check results on a fixture
that has checks (`NO_CHECKS_REASON`, which also keeps the cell from
`no-benefit`); each cell records, under `skipped`, why its other runs did
not count. An arm's run mean is the mean of those trials' pass rates. Its
run judge figure is the mean of its trials' judge figures, and exists only
when the run judged unconditionally (`judge_conditional` false: run_eval
skips the judge when the with arm is already ahead on the objective, so
conditional scores are a sample biased toward no benefit) and every one of
those trials has a figure. A delta is mean(with run means) - mean(without
run means).

THE BOUNDS (`bounds`): at a one-sided `level`, the wider of a deterministic
percentile bootstrap over run means (`bootstrap_deltas`: each arm resampled
with replacement within itself, `bootstrap_resamples` times, a fixed seed
over sorted values) and a Welch t bound with Welch-Satterthwaite df floored
(at least 1; when its denominator underflows to 0 or is not finite, the
smaller arm's n - 1, at least 1), over FLOORED variances, so near-identical runs cannot make a
bound tighter than the trials behind them allow. For an objective delta,
overall or per check, each arm's variance of the mean is at least
p(1 - p)/n with p the arm's pooled pass rate and n its scored trials
(`binomial_floor`, p clipped to [1/(2n), 1 - 1/(2n)]). For a judge delta, it
is at least the pooled within-run variance of the trial judge figures over
the number of runs (`judge_floor`), pooled from the runs with two or more
judged trials. An arm with none (one trial per run, round 5, N1) has no
floor: the run-level standard error alone bounds its judge delta, and its run
judge figures must not all be equal (`_judge_spread_ok`); its objective floor
is still the binomial one with n its scored trials. With the committed margins, the objective
floor means an objective no-benefit needs hundreds of scored trials per arm
at a look (twenty runs of three trials at a 0.8 pass rate are not enough);
judge-carried cells are decided on the judge and its own floor. The t quantile is computed (`t_quantile`, the regularized incomplete
beta inverted by bisection), so Holm's adjusted levels need no table.
Levels: benefit uses the two-sided `benefit_confidence` interval's lower
end; no-benefit the one-sided `retire_confidence` upper bound; a check the
one-sided `check_confidence` lower bound, Holm-corrected across the
fixture's checks (`confident_checks`).

A CELL'S CLASS (`_classify`), first match wins:

  1. `insufficient`: an arm has fewer than `min_runs` counted runs, or the
     counted runs show fewer than `min_cli_versions` `harness.version`s.
  2. `cannot-discriminate`: some check fails in every scored trial of BOTH
     arms, or both arms' pooled pass rate <= `floor_fraction`.
  3. Zero variance: when every run mean of each arm is identical (the judge
     figures, for a judge-carried fixture), nothing is decided on it:
     `ceiling` if it qualifies, else `insufficient`. For a Class A or B
     fixture a judge figure that does vary may still show `benefit` first,
     since that only keeps.
  4. `benefit`: the objective delta's lower bound > 0; or some check
     confidently better with the skill (Holm); or, when the objective shows
     no confident gain, the judge delta's lower bound > 0. A judge-carried
     fixture keeps on any of the three too: its judge decides, but its
     objective checks are evidence of benefit. The judge figure is usable with
     `min_runs` judged runs per arm, at least two of them per arm with
     judge figures that differ within the run.
  5. `ceiling`: both arms' pooled pass rate >= `ceiling_fraction`; for a
     judge-carried fixture, both arms' mean judge figure >= `judge_ceiling`.
  6. `no-benefit`: the objective delta's `retire_confidence` upper bound <
     `objective_margin`, and the judge delta's < `judge_margin`; the judge
     figure must be usable and not of zero variance, since rule 4 only
     reached it when the objective showed no confident gain. A
     judge-carried fixture needs the judge's bound, and the objective's too
     whenever it has objective checks (a varying objective with `min_runs`
     runs per arm); without checks, the judge's alone.
  7. `insufficient`: the evidence is not tight yet.

STATELESS CONFIRMATION AT FIXED LOOKS (`classify_cell`, round-2 review,
2026-10-25). Nothing is carried from an earlier evaluation. Each arm's
counted runs are ordered by (timestamp, path). The class is computed at
every reachable look: each of `retire_looks` <= the smaller arm's counted
runs, on each arm's first N runs. `benefit` may be declared at any count, on
the whole pool. Otherwise the largest look decides: a class other than
`no-benefit` there is the cell's class; `no-benefit` at BOTH of the two
largest reachable looks is `no-benefit`; `no-benefit` at the largest alone
(one look reached, or the one before it was not) is `no-benefit-pending`.
So nothing retires before ten counted runs per arm, and since every look is
within the current pooling key, new content starts over. Before the first
look the whole pool's class stands. scripts/compute_gates.py reads no run
stamped after its `--as-of`, so an evaluation can be repeated as of a
time. A run is named by `run_key`,
`<path>#<fixture>#<role>`.

THE VERDICT (`retirement`), over every fixture on main x every current roster
arm model: any `benefit` -> `keep`; any `insufficient` or missing cell, or no
fixture on main -> `insufficient`; any `cannot-discriminate` ->
`cannot-discriminate`; any `ceiling` -> `ceiling`; any `no-benefit-pending`
-> `retire-pending`; `retire` only when there is at least one cell and
every cell is `no-benefit` (no roster model means no cell, never a
retirement); anything else `insufficient`. scripts/compute_gates.py then lets
one skill retire per evaluation. A standing retirement's stickiness, and
holding the one slot while a removal pull request is open, are part 2's: it
reads the open pull requests from GitHub rather than trusting an earlier
gates file.

KNOWN LIMITS, left for a later part:

  * Holm's m counts every check the fixture has, including checks constant
    in both arms that can never be confident, so the per-check keep is
    harder than it needs to be (Tarone's method would leave them out).
  * Scorer and harness changes are not part of the pooling key: runs scored
    before and after a scorer fix pool together.
  * An adopted skill's `tested_digest` is its frozen context's deployed copy,
    not the registry checkout's; when the two differ its runs do not pool
    (insufficient, never a wrong retire). Real-work fixtures
    (evals/real-work/, `subject: any`) are not a skill's fixtures on main,
    so their runs are not read.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "evals" / "gate-policy.yml"
#: Where a report path in the results tree is published.
RESULTS_BLOB = ("https://github.com/example-adopter/skills-evals/blob/"
                "persistent/eval-results/")
MODES = ("auto", "proposal")
#: The bootstrap's seed: fixed, so the same runs give the same bounds.
BOOTSTRAP_SEED = 70
#: Scored trials an arm of one run needs to count (#70's 3).
MIN_TRIALS_PER_RUN = 1
#: Trial error types that are the harness's, not the agent's.
INFRASTRUCTURE_ERRORS = ("scorer_unavailable", "scorer_sandbox_unavailable",
                         # A guidance arm whose delivery was not proved
                         # (harness/run_eval.py `_guard_error`, and a hook
                         # that never wrote its block): INCONCLUSIVE, never
                         # the agent's failure.
                         "delivery_failed", "guard_error", "guard_miss",
                         "guard_contaminated")
#: The start of a FAILED check's detail that names a scorer failure rather
#: than anything the agent did: harness/scorers/commands.py's
#: `ScorerSandboxUnavailable` message, `scorer_sandbox_unavailable exit=<n>`,
#: which command_succeeds and repo_tests return as the check's detail. Only
#: the start counts: an agent's own text elsewhere in a detail cannot make
#: its trial an infrastructure error. (A missing bash parser,
#: `parser_unavailable`, already errors the trial in run_eval as
#: `scorer_unavailable`.)
INFRASTRUCTURE_DETAIL_PREFIX = "scorer_sandbox_unavailable exit="
#: Why a run with a scored trial that has no check result does not count.
NO_CHECKS_REASON = "a trial without objective checks on a fixture that has them"
#: Fixtures moved from the flat `evals/<skill>/` into a named subdirectory
#: with their content unchanged (#422, #427): {(skill, old name, "" for
#: flat): new name}. A run recorded under the old name pools with the new
#: one only while its `fixture_digest` equals the new fixture's digest on
#: main (`pooled_fixture`), so an alias never joins different content.
FIXTURE_ALIASES = {
    ("review-bash-ci-reliability", ""): "release-pipeline",
    ("vendor-release-impact-issues", ""): "issue-hygiene",
    ("workflow-path-audit", ""): "required-check-routing",
}


class GatePolicyError(ValueError):
    """The gate policy is missing, unreadable or breaks a rule."""


def _number(low, high, *, low_open=False, high_open=False):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            return False
        above = value > low if low_open else value >= low
        below = value < high if high_open else value <= high
        return above and below
    return check


def _count(low):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= low


_LEVEL = ("a number above 0.5 and below 1",
          _number(0.5, 1, low_open=True, high_open=True))
#: Each scalar key, its rule in words, and the rule.
SKILL_KEYS = {
    "objective_margin": ("a number from 0 to 1", _number(0, 1)),
    "judge_margin": ("a number from 0 to 10", _number(0, 10)),
    "benefit_confidence": _LEVEL,
    "retire_confidence": _LEVEL,
    "check_confidence": _LEVEL,
    "min_runs": ("an integer of at least 2", _count(2)),
    "min_cli_versions": ("an integer of at least 1", _count(1)),
    "ceiling_fraction": ("a number above 0 and at most 1",
                         _number(0, 1, low_open=True)),
    "judge_ceiling": ("a number above 0 and at most 10",
                      _number(0, 10, low_open=True)),
    "floor_fraction": ("a number from 0 and below 1", _number(0, 1, high_open=True)),
    "bootstrap_resamples": ("an integer of at least 100", _count(100)),
}


def parse_gate_policy(text: str) -> dict:
    """{"skills": {...}, "guidance": {...} or None} from `text`, or
    GatePolicyError naming the key. `guidance:` (skills-evals#124) is
    optional and has the same keys and rules as `skills:`, kept apart so the
    two can diverge; without it no guidance verdict is computed.

    A missing or invalid `retirement_mode` is `proposal`, the mode that
    removes nothing on its own."""
    try:
        document = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError):
        # PyYAML raises a bare ValueError for a date-shaped scalar that is
        # not a date (2026-13-05).
        raise GatePolicyError("gate policy is not valid YAML") from None
    if not isinstance(document, dict) or "skills" not in document \
            or not set(document) <= {"skills", "guidance"}:
        raise GatePolicyError("gate policy must be a mapping with the key skills "
                              "and optionally guidance")
    return {"skills": _policy_block(document["skills"], "skills"),
            "guidance": (_policy_block(document["guidance"], "guidance")
                         if "guidance" in document else None)}


def _policy_block(block, name: str) -> dict:
    """One block of thresholds (`skills:` or `guidance:`), checked."""
    if not isinstance(block, dict):
        raise GatePolicyError(f"gate policy {name} must be a mapping")
    unknown = sorted(set(block) - set(SKILL_KEYS)
                     - {"retire_looks", "retirement_mode"})
    if unknown:
        raise GatePolicyError(f"gate policy {name} has unknown keys {unknown}")
    out = {}
    for key, (words, valid) in SKILL_KEYS.items():
        if key not in block:
            raise GatePolicyError(f"gate policy {name} is missing {key}")
        if not valid(block[key]):
            raise GatePolicyError(f"gate policy {name}.{key} must be {words}")
        out[key] = block[key]
    looks = block.get("retire_looks")
    if not isinstance(looks, list) or not looks \
            or not all(_count(out["min_runs"])(n) for n in looks) \
            or any(a >= b for a, b in zip(looks, looks[1:])):
        raise GatePolicyError(f"gate policy {name}.retire_looks must be a "
                              "strictly increasing list of integers, each at "
                              "least min_runs")
    out["retire_looks"] = list(looks)
    mode = block.get("retirement_mode")
    out["retirement_mode"] = mode if mode in MODES else "proposal"
    return out


def load_gate_policy(path: Path | None = None) -> dict:
    """`parse_gate_policy` of the file at `path` (default evals/gate-policy.yml)."""
    path = Path(path) if path is not None else DEFAULT_POLICY
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise GatePolicyError(f"gate policy {path.name} could not be read") from None
    return parse_gate_policy(text)


# ---------------------------------------------------------------------------
# Statistics.

def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _mean(values) -> float:
    return math.fsum(values) / len(values)


def _finite_sum(values) -> float | None:
    """`math.fsum` of `values`, or None when one is None or the sum is not a
    finite number (two costs of 1e308 overflow)."""
    if any(v is None for v in values):
        return None
    try:
        total = math.fsum(values)
    except OverflowError:
        return None
    return total if math.isfinite(total) else None


def _constant(values) -> bool:
    return min(values) == max(values)


def _betacf(a: float, b: float, x: float) -> float:
    """The incomplete beta function's continued fraction (modified Lentz)."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        for numerator in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                          -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def t_cdf(t: float, df: int) -> float:
    """Student's t distribution function."""
    tail = 0.5 * _betainc(df / 2, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def t_quantile(p: float, df: int) -> float:
    """The `p` quantile (p > 0.5) of Student's t with `df` degrees of
    freedom, by bisection on `t_cdf`."""
    low, high = 0.0, 1e7
    for _ in range(200):
        mid = (low + high) / 2
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _percentile(ordered: list, p: float) -> float:
    position = p * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_deltas(a: list, b: list, resamples: int) -> list:
    """Sorted bootstrap replicates of mean(a) - mean(b): whole values
    resampled within each list, a fixed seed over the sorted values."""
    a, b = sorted(a), sorted(b)
    rng = random.Random(BOOTSTRAP_SEED)
    return sorted(_mean(rng.choices(a, k=len(a))) - _mean(rng.choices(b, k=len(b)))
                  for _ in range(resamples))


def _variance(values: list) -> float:
    if _constant(values):
        return 0.0  # exactly, not the rounding residue of the mean
    mean = _mean(values)
    return math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1)


def binomial_floor(p: float | None, n: int) -> float:
    """p(1 - p)/n for a pooled pass rate `p` over `n` scored trials, p clipped
    to [1/(2n), 1 - 1/(2n)] so that all passed or none is not zero: the least
    variance of a mean pass rate those trials allow. The trial, not the
    check, is the count, since the checks of one trial move together. 0 when
    n is 0."""
    if n <= 0 or p is None:
        return 0.0
    p = min(max(p, 1 / (2 * n)), 1 - 1 / (2 * n))
    return p * (1 - p) / n


def judge_floor(figures: list) -> float:
    """The pooled within-run variance of the judge figures, divided by the
    number of runs: the least variance of a mean of run judge figures that
    the trials' own spread allows. `figures` holds each run's trial figures."""
    ss = math.fsum(math.fsum((x - _mean(run)) ** 2 for x in run) for run in figures)
    df = sum(len(run) - 1 for run in figures)
    return ss / df / len(figures) if df > 0 else 0.0


def welch(a: list, b: list, floors: tuple | None = None) -> tuple | None:
    """(delta, standard error, Welch-Satterthwaite df floored, at least 1),
    or None when an arm has fewer than two values. `floors`, when given, is
    each arm's least variance of the mean (`binomial_floor`)."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = _variance(a) / len(a), _variance(b) / len(b)
    if floors is not None:
        va, vb = max(va, floors[0]), max(vb, floors[1])
    se2 = va + vb
    # Welch-Satterthwaite, unless its denominator underflows (variances
    # around 1e-100 square to 0) or is not finite: then the smaller arm's
    # n - 1, the conservative df. Products, not `**`: a float power raises
    # OverflowError where a product is inf.
    denominator = va * va / (len(a) - 1) + vb * vb / (len(b) - 1)
    ratio = se2 * se2 / denominator if denominator > 0 and math.isfinite(denominator) \
        else math.nan
    df = (max(1, math.floor(ratio)) if math.isfinite(ratio)
          else max(1, min(len(a), len(b)) - 1)) if se2 else 1
    return _mean(a) - _mean(b), math.sqrt(se2), df


def bounds(a: list, b: list, level: float, resamples: int,
           deltas: list | None = None, floors: tuple | None = None) -> tuple[float, float]:
    """(lower, upper) bounds of mean(a) - mean(b), each one-sided at `level`:
    the wider of the bootstrap percentiles and the Welch t bounds (over
    `floors`, when given)."""
    deltas = deltas if deltas is not None else bootstrap_deltas(a, b, resamples)
    low, high = _percentile(deltas, 1 - level), _percentile(deltas, level)
    w = welch(a, b, floors)
    if w is not None:
        half = t_quantile(level, w[2]) * w[1]
        low, high = min(low, w[0] - half), max(high, w[0] + half)
    return low, high


def confident_checks(with_rates: dict, without_rates: dict, level: float,
                     resamples: int, floors: dict | None = None) -> list:
    """Check keys whose with-minus-without delta of per-run pass rates has a
    lower bound above 0 at one-sided `level`, Holm-corrected across every
    check in both arms: ordered by the Welch one-sided p-value, the k-th
    (from 0) is tested at 1 - (1 - level) / (m - k) and the procedure stops
    at the first that fails. `floors` maps a check to its two arms' binomial
    floors. A check of zero variance on both arms is never confident, nor
    one with fewer than two runs per arm."""
    floors = floors or {}
    keys = sorted(set(with_rates) & set(without_rates))
    alpha, m = 1 - level, len(keys)
    ranked = []
    for key in keys:
        a, b = with_rates[key], without_rates[key]
        w = welch(a, b, floors.get(key))
        if w is None or (_constant(a) and _constant(b)):
            p = 1.0
        else:
            p = 1 - t_cdf(w[0] / w[1], w[2])
        ranked.append((p, key))
    confident = []
    for k, (p, key) in enumerate(sorted(ranked)):
        if p == 1.0 or bounds(with_rates[key], without_rates[key],
                              1 - alpha / (m - k), resamples,
                              floors=floors.get(key))[0] <= 0:
            break
        confident.append(key)
    return confident


# ---------------------------------------------------------------------------
# Trials and runs.

class MalformedSummary(ValueError):
    """A trial summary that is not the shape run_eval writes: its run is
    skipped, never scored around and never a crash."""


def check_key(check_id) -> str:
    """A check id as a string key: a fixture may write any YAML value as one."""
    return json.dumps(check_id, sort_keys=True)


def trial_record(summary: dict) -> dict:
    """What a cell needs from one trial summary, or MalformedSummary. A
    cost that is not a finite number from 0 up is unknown."""
    agent = summary.get("agent")
    if agent is not None and not isinstance(agent, dict):
        raise MalformedSummary("agent is not a mapping")
    cost = agent.get("cost_usd") if isinstance(agent, dict) else None
    out = {"cost_usd": cost if _is_number(cost) and cost >= 0 else None}
    checks = summary.get("objective_checks")
    if checks is None:
        checks = []
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise MalformedSummary("objective_checks is not a list of mappings")
    error = summary.get("error")
    infra = any(c.get("passed") is False and isinstance(c.get("detail"), str)
                and c["detail"].startswith(INFRASTRUCTURE_DETAIL_PREFIX)
                for c in checks) or (isinstance(error, dict)
                                     and error.get("type") in INFRASTRUCTURE_ERRORS)
    if infra:
        return {**out, "scored": False, "infrastructure": True, "errored": False}
    if error:
        # The agent's failure (a timeout, a budget stop): it scores 0 on
        # every check and has no judge figure (`run_numbers`).
        return {**out, "scored": False, "infrastructure": False, "errored": True,
                "checks": {}, "judge": None, "pairwise": False}
    if not all(isinstance(c.get("passed"), bool) for c in checks):
        raise MalformedSummary("a check's passed is not true or false")
    judge = summary.get("judge")
    if judge is not None and not isinstance(judge, dict):
        raise MalformedSummary("judge is not a mapping")
    pairwise = isinstance(judge, dict) and judge.get("mode") == "pairwise"
    figure = None
    if isinstance(judge, dict) and "error" not in judge \
            and judge.get("status") != "skipped":
        figure = judge.get("dimensions_mean") if pairwise else judge.get("overall")
        if figure is not None and not (_is_number(figure) and 0 <= figure <= 10):
            raise MalformedSummary("judge figure is not a number from 0 to 10")
    return {**out, "scored": True, "infrastructure": False, "errored": False,
            "checks": {check_key(c.get("id")): c["passed"] for c in checks},
            "judge": figure, "pairwise": pairwise}


def run_numbers(trials: list, judge_unconditional: bool, check_ids=(),
                has_checks: bool = False) -> dict:
    """One arm of one run, reduced: whether it counts (`skip_reason` None:
    `MIN_TRIALS_PER_RUN` scored or agent-errored trials, and no scored trial
    without check results when the fixture has checks, `has_checks`), its
    run mean, its run judge figure (only when `judge_unconditional` and every
    one of those trials has one) and whether those figures differ, its
    per-check pass rates and its pooled counts. An agent-errored trial scores
    0 on every check of `check_ids` (the cell's checks) and has no judge
    figure; an infrastructure error is left out."""
    check_ids = sorted(check_ids)
    errored = [t for t in trials if t.get("errored")]
    scored = [t for t in trials if t.get("scored")] + [
        {**t, "checks": {k: False for k in check_ids}} for t in errored]
    if (has_checks or check_ids) and any(not t["checks"] for t in scored
                                         if not t.get("errored")):
        skip = NO_CHECKS_REASON
    elif len(scored) < MIN_TRIALS_PER_RUN:
        skip = f"fewer than {MIN_TRIALS_PER_RUN} scored or errored trials"
    else:
        skip = None
    rates = [sum(t["checks"].values()) / len(t["checks"]) for t in scored if t["checks"]]
    figures = [t["judge"] for t in scored]
    judged = (judge_unconditional and len(scored) >= MIN_TRIALS_PER_RUN
              and None not in figures)
    per_check: dict = {}
    for t in scored:
        for key, passed in t["checks"].items():
            per_check.setdefault(key, []).append(passed)
    costs = [t["cost_usd"] for t in trials if t.get("cost_usd") is not None]
    cost = _finite_sum(costs)
    return {"scored_trials": len(scored), "errored_trials": len(errored),
            "counted": skip is None, "skip_reason": skip,
            "run_mean": _mean(rates) if rates else None,
            "judge_mean": _mean(figures) if judged else None,
            "judge_spread": bool(judged and not _constant(figures)),
            "judge_figures": figures if judged else None,
            "check_rates": {k: sum(v) / len(v) for k, v in per_check.items()},
            "check_passed": {k: sum(v) for k, v in per_check.items()},
            "check_n": {k: len(v) for k, v in per_check.items()},
            "passed": sum(sum(t["checks"].values()) for t in scored),
            "total": sum(len(t["checks"]) for t in scored),
            "cost_usd": cost,
            "cost_unknown_trials": (len(trials) - len(costs)) if cost is not None
            else len(trials)}


def _numbers(run: dict) -> dict:
    return run.setdefault("numbers", run_numbers(run["trials"],
                                                 run.get("judge_conditional") is False))


def prime(runs: list) -> None:
    """Reduce every run of one cell (`run_numbers`) against the cell's own
    checks: the check ids any scored trial of either arm reports, and
    whether the fixture has checks at all (a run's `fixture_checks`, which
    compute_gates reads from fixture.yaml, or any check result)."""
    check_ids = {k for r in runs for t in r["trials"] if t.get("scored")
                 for k in t["checks"]}
    has_checks = bool(check_ids) or any(r.get("fixture_checks") for r in runs)
    for run in runs:
        run["numbers"] = run_numbers(run["trials"], run.get("judge_conditional") is False,
                                     check_ids, has_checks)


# ---------------------------------------------------------------------------
# Cells.

def _arm_summary(runs: list) -> dict:
    passed = sum(r["numbers"]["passed"] for r in runs)
    total = sum(r["numbers"]["total"] for r in runs)
    means = [r["numbers"]["run_mean"] for r in runs if r["numbers"]["run_mean"] is not None]
    judged = [r["numbers"]["judge_mean"] for r in runs
              if r["numbers"]["judge_mean"] is not None]
    return {"runs": len(runs),
            "scored_trials": sum(r["numbers"]["scored_trials"] for r in runs),
            "checks_passed": passed, "checks_scored": total,
            "pass_rate": passed / total if total else None,
            "run_means": means, "mean_of_run_means": _mean(means) if means else None,
            "judge_runs": len(judged), "judge_run_means": judged,
            "judge_floor": judge_floor([r["numbers"]["judge_figures"] for r in runs
                                        if r["numbers"]["judge_figures"] is not None]),
            "judge_spread_runs": sum(1 for r in runs if r["numbers"]["judge_spread"]),
            "judge_multi_trial_runs": sum(
                1 for r in runs if r["numbers"]["judge_figures"] is not None
                and len(r["numbers"]["judge_figures"]) >= 2),
            "judge_figure": _mean(judged) if judged else None}


def _pooled(runs: list, field: str) -> dict:
    out: dict = {}
    for run in runs:
        for key, value in run["numbers"][field].items():
            out[key] = out.get(key, 0) + value
    return out


#: Runs per arm whose judge figures must differ within the run.
JUDGE_SPREAD_RUNS = 2


def _judge_spread_ok(arm: dict) -> bool:
    """Whether an arm's judge figures can bound a delta (N1, round 5). With
    runs of two or more judged trials, `JUDGE_SPREAD_RUNS` of them must
    differ within the run, so the floor (`judge_floor`, their pooled
    within-run variance) is not zero. With only one-trial runs there is no
    within-run spread to pool: the run-level standard error alone bounds
    the delta, so the run judge figures must not all be equal."""
    if arm["judge_multi_trial_runs"]:
        return arm["judge_spread_runs"] >= JUDGE_SPREAD_RUNS
    return len(set(arm["judge_run_means"])) > 1


def _classify(with_runs: list, without_runs: list, policy: dict,
              judge_carried: bool) -> dict:
    """Rules 1 to 7 of the module docstring, over counted runs."""
    arms = {"with": _arm_summary(with_runs), "without": _arm_summary(without_runs)}
    cli = sorted({v for r in with_runs + without_runs for v in r["cli_versions"]})
    out = {**arms, "cli_versions": cli, "objective": None, "judge": None,
           "confident_checks": []}

    def done(cls, reason):
        return {**out, "class": cls, "reason": reason}

    least = min(arms["with"]["runs"], arms["without"]["runs"])
    if least < policy["min_runs"]:
        return done("insufficient", f"an arm has {least} counted runs, under "
                    f"{policy['min_runs']}")
    if len(cli) < policy["min_cli_versions"]:
        return done("insufficient", f"{len(cli)} CLI version(s), under "
                    f"{policy['min_cli_versions']}")

    with_n, with_passed = _pooled(with_runs, "check_n"), _pooled(with_runs, "check_passed")
    without_n = _pooled(without_runs, "check_n")
    without_passed = _pooled(without_runs, "check_passed")
    for key in sorted(set(with_n) & set(without_n)):
        if not with_passed[key] and not without_passed[key]:
            return done("cannot-discriminate", f"check {key} fails in every "
                        "scored trial of both arms")
    rates = [arms[a]["pass_rate"] for a in arms]
    if all(r is not None and r <= policy["floor_fraction"] for r in rates):
        return done("cannot-discriminate", "both arms' pass rate at or below "
                    f"{policy['floor_fraction']}")

    resamples = policy["bootstrap_resamples"]
    benefit_level = (1 + policy["benefit_confidence"]) / 2
    objective_ceiling = all(r is not None and r >= policy["ceiling_fraction"]
                            for r in rates)
    wj, oj = arms["with"]["judge_run_means"], arms["without"]["judge_run_means"]
    judge_ok = (min(len(wj), len(oj)) >= policy["min_runs"]
                and all(_judge_spread_ok(arms[a]) for a in arms))
    judge_ceiling = judge_ok and all(arms[a]["judge_figure"] >= policy["judge_ceiling"]
                                     for a in arms)

    def delta(a, b, floors=None):
        deltas = bootstrap_deltas(a, b, resamples)
        return {"delta": _mean(a) - _mean(b),
                "benefit_lower": bounds(a, b, benefit_level, resamples, deltas,
                                        floors)[0],
                "retire_upper": bounds(a, b, policy["retire_confidence"],
                                       resamples, deltas, floors)[1]}

    judge_floors = (arms["with"]["judge_floor"], arms["without"]["judge_floor"])
    judge_words = (f"the judge figure needs {policy['min_runs']} unconditionally "
                   f"judged runs per arm, {JUDGE_SPREAD_RUNS} of them with judge "
                   "figures that differ within the run (an arm of one-trial runs: "
                   "run judge figures that are not all equal)")
    wm, om = arms["with"]["run_means"], arms["without"]["run_means"]
    floors = tuple(binomial_floor(arms[a]["pass_rate"], arms[a]["scored_trials"])
                   for a in ("with", "without"))
    objective_varies = (min(len(wm), len(om)) >= policy["min_runs"]
                        and not (_constant(wm) and _constant(om)))

    def objective_benefit():
        """The objective delta and the per-check keep (Holm): the reason a
        varying objective shows benefit, or None."""
        out["objective"] = delta(wm, om, floors)
        if out["objective"]["benefit_lower"] > 0:
            return "objective delta's lower bound is above 0"
        by_check = [{k: [r["numbers"]["check_rates"][k] for r in runs
                         if k in r["numbers"]["check_rates"]]
                     for k in {k for r in runs for k in r["numbers"]["check_rates"]}}
                    for runs in (with_runs, without_runs)]
        check_floors = {k: (binomial_floor(with_passed[k] / with_n[k], with_n[k]),
                            binomial_floor(without_passed[k] / without_n[k], without_n[k]))
                        for k in set(with_n) & set(without_n)}
        out["confident_checks"] = confident_checks(*by_check, policy["check_confidence"],
                                                   resamples, check_floors)
        if out["confident_checks"]:
            return "a check is confidently better with the skill"
        return None

    if judge_carried:
        # Decided on the judge, but a judge-carried fixture with objective
        # checks keeps on them too, and is no-benefit only when they are.
        reason = objective_benefit() if objective_varies else None
        if reason:
            return done("benefit", reason)
        if not judge_ok:
            return done("insufficient", judge_words)
        if _constant(wj) and _constant(oj):
            return (done("ceiling", "zero variance at the judge ceiling") if judge_ceiling
                    else done("insufficient", "zero variance: nothing to decide on"))
        out["judge"] = delta(wj, oj, judge_floors)
        if out["judge"]["benefit_lower"] > 0:
            return done("benefit", "judge delta's lower bound is above 0")
        if judge_ceiling:
            return done("ceiling", "both arms' judge figure at or above "
                        f"{policy['judge_ceiling']}")
        if out["judge"]["retire_upper"] >= policy["judge_margin"]:
            return done("insufficient", "the interval is not tight enough yet")
        if wm or om:
            if not objective_varies:
                return done("insufficient", "the objective checks decide nothing yet: "
                            "too few runs with them, or zero variance")
            if out["objective"]["retire_upper"] >= policy["objective_margin"]:
                return done("insufficient", "the objective interval is not tight "
                            "enough yet")
            return done("no-benefit", "the judge and objective deltas' upper bounds "
                        "are under their margins")
        return done("no-benefit", "judge delta's upper bound is under its margin")

    if min(len(wm), len(om)) < policy["min_runs"]:
        return done("insufficient", "too few runs with objective checks")
    judge_usable = judge_ok and not (_constant(wj) and _constant(oj))
    if _constant(wm) and _constant(om):
        # Nothing is decided on the objective; a judge figure that varies may
        # still show a benefit, which only keeps.
        if judge_usable:
            out["judge"] = delta(wj, oj, judge_floors)
            if out["judge"]["benefit_lower"] > 0:
                return done("benefit", "judge delta's lower bound is above 0")
        return (done("ceiling", "zero variance at the ceiling") if objective_ceiling
                else done("insufficient", "zero variance: nothing to decide on"))
    reason = objective_benefit()
    if reason:
        return done("benefit", reason)
    if judge_usable:
        out["judge"] = delta(wj, oj, judge_floors)
        if out["judge"]["benefit_lower"] > 0:
            return done("benefit", "judge delta's lower bound is above 0")
    if objective_ceiling:
        return done("ceiling", "both arms' pass rate at or above "
                    f"{policy['ceiling_fraction']}")
    if not judge_usable:
        return done("insufficient", f"the judge is needed: {judge_words}, and not "
                    "of zero variance")
    if out["objective"]["retire_upper"] < policy["objective_margin"] \
            and out["judge"]["retire_upper"] < policy["judge_margin"]:
        return done("no-benefit", "every delta's upper bound is under its margin")
    return done("insufficient", "the interval is not tight enough yet")


def run_key(run: dict) -> str:
    """A run's name in `runs_used`:
    `<path>#<fixture>#<role>`, since one `<timestamp>` holds every fixture."""
    return f"{run['path']}#{run['fixture'] or ''}#{run['role']}"


def classify_cell(with_runs: list, without_runs: list, policy: dict,
                  judge_carried: bool) -> dict:
    """A cell's class under stateless confirmation (module docstring)."""
    prime(with_runs + without_runs)
    counted, skipped = [], {}
    for runs in (with_runs, without_runs):
        keep = []
        for run in sorted(runs, key=_order):
            reason = run["numbers"]["skip_reason"]
            if reason is None:
                keep.append(run)
            else:
                skipped[reason] = skipped.get(reason, 0) + 1
        counted.append(keep)
    full = _classify(*counted, policy, judge_carried)
    available = min(map(len, counted))
    looks = []
    for n in (n for n in policy["retire_looks"] if n <= available):
        result = _classify(counted[0][:n], counted[1][:n], policy, judge_carried)
        looks.append({"n": n, "class": result["class"], "reason": result["reason"]})
    if full["class"] == "benefit" or not looks:
        cls, reason = full["class"], full["reason"]
        if cls == "no-benefit":
            cls, reason = "insufficient", "no-benefit is declared only at a look"
    elif looks[-1]["class"] != "no-benefit":
        cls = looks[-1]["class"]
        reason = f"at the {looks[-1]['n']}-run look: {looks[-1]['reason']}"
    elif len(looks) >= 2 and looks[-2]["class"] == "no-benefit":
        cls = "no-benefit"
        reason = f"no-benefit at the {looks[-2]['n']}- and {looks[-1]['n']}-run looks"
    else:
        cls = "no-benefit-pending"
        reason = (f"no-benefit at the {looks[-1]['n']}-run look; it must also be "
                  "no-benefit at the next look")
    errored = sum(r["numbers"]["errored_trials"] for r in with_runs + without_runs)
    if cls in ("no-benefit", "no-benefit-pending") and (errored or NO_CHECKS_REASON in skipped):
        # An agent's errors can only help keep: dropping or scoring around
        # them would compare only the trials that went well.
        cls, reason = "insufficient", (f"{cls} on the numbers, but the cell has "
                                       f"{errored} agent-errored trial(s) or a run "
                                       "skipped as anomalous, so it cannot retire")
    return {**full, "class": cls, "reason": reason, "full_class": full["class"],
            "looks": looks, "judge_carried": judge_carried, "skipped": skipped}


# ---------------------------------------------------------------------------
# Pooling and the verdict.

def _order(run: dict) -> tuple:
    """A run's place: its timestamp, then its path."""
    return run["timestamp"], run["path"]


def _joint_context(with_runs: list, without_runs: list, models: list):
    """(with context, without context, pair key) chosen JOINTLY (round 5,
    B6), or (None, None, None). Runs on a roster model are grouped by (arm context digest,
    `pair_key`): the pair key is what both arms of one comparison must share,
    the non-subject skills (`arm_context.others_digest`) and the frozen
    guidance (`context.guidance.digest`). A candidate is a with group and a
    without group of one pair key, scored by its smaller arm's run count; the
    best candidate is the cell's, and a tie or no candidate chooses none. No
    single run's self-report chooses either (round 4), and the two arms can
    no longer be pooled from different guidance or skill sets."""
    def groups(runs):
        counts: dict = {}
        for run in runs:
            if run["model"] in models and run["arm_context_digest"] and run.get("pair_key"):
                key = (run["arm_context_digest"], run["pair_key"])
                counts[key] = counts.get(key, 0) + 1
        return counts
    candidates = sorted(((min(n_with, n_without), with_ctx, without_ctx, pair)
                         for (with_ctx, pair), n_with in groups(with_runs).items()
                         for (without_ctx, other), n_without in groups(without_runs).items()
                         if pair == other), reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0]):
        return None, None, None
    return candidates[0][1:]


def pooled_fixture(run: dict, skill: str, expected: dict) -> str:
    """The fixture on main a run of `skill` counts for: its own name ("" for
    flat), or its `FIXTURE_ALIASES` target when the old name is no longer on
    main and the run's fixture digest equals the target's there."""
    name = run["fixture"] or ""
    target = FIXTURE_ALIASES.get((skill, name))
    if target is None or name in expected or target not in expected:
        return name
    digest = expected[target].get("fixture_digest")
    return target if digest is not None and run["fixture_digest"] == digest else name


def pool_cells(runs: list, skill: str, models: list, expected: dict) -> dict:
    """Which arm runs each (fixture, model) cell of `skill` holds, each arm
    pooled by its own key against the content on main (module docstring).
    `expected` maps each fixture on main ("" for a flat one) to its
    {"fixture_digest", "tested_digest"}, either None when unknown. A run's
    fixture is `pooled_fixture`'s. The one place runs are grouped; swap it
    to change the window."""
    own = [r for r in runs if r["skill"] == skill]
    cells = {}
    for fixture, main in sorted(expected.items()):
        mine = [r for r in own if pooled_fixture(r, skill, expected) == fixture]
        tested, fdig = main.get("tested_digest"), main.get("fixture_digest")
        current_with = [r for r in mine if r["role"] == "with" and tested is not None
                        and r["tested_digest"] == tested and fdig is not None
                        and r["fixture_digest"] == fdig]
        current_without = [r for r in mine if r["role"] == "without" and fdig is not None
                           and r["fixture_digest"] == fdig]
        with_context, without_context, pair = _joint_context(current_with, current_without,
                                                             models)
        key = {"tested_digest": tested, "fixture_digest": fdig,
               "with_context": with_context, "without_context": without_context}
        for model in models:
            cells[(fixture, model)] = {
                "key": key, "on_model": sum(1 for r in mine if r["model"] == model),
                "with": [r for r in current_with if r["model"] == model
                         and r["arm_context_digest"] == with_context
                         and r.get("pair_key") == pair],
                "without": [r for r in current_without if r["model"] == model
                            and r["arm_context_digest"] == without_context
                            and r.get("pair_key") == pair]}
    return cells


def _link(path: str) -> dict:
    return {"path": path, "url": RESULTS_BLOB + path}


def _run_entry(run: dict) -> dict:
    numbers = _numbers(run)
    return {"path": run["path"], "timestamp": run["timestamp"],
            "report": _link(run["report"]), "run": run["run"],
            "counted": numbers["counted"], "scored_trials": numbers["scored_trials"],
            "run_mean": numbers["run_mean"], "judge_mean": numbers["judge_mean"],
            "cost_usd": numbers["cost_usd"]}


def retirement(index: dict, models: list, skill: str, policy: dict,
               expected: dict) -> dict:
    """The verdict for `skill` over every fixture on main (`expected`, see
    `pool_cells`) x roster model cell."""
    cells, used = [], set()
    for (fixture, model), pool in sorted(pool_cells(index.get("runs", []), skill,
                                                    models, expected).items()):
        arms = {"with": pool["with"], "without": pool["without"]}
        runs = arms["with"] + arms["without"]
        prime(runs)
        used.update(run_key(r) for r in runs)
        missing = sorted(k for k, v in pool["key"].items() if v is None)
        if not pool["on_model"]:
            cell = {"class": "missing", "reason": "no run on this model", "looks": []}
        elif missing:
            cell = {"class": "insufficient", "looks": [],
                    "reason": f"pooling key incomplete: {', '.join(missing)}"}
        else:
            carried = any(r["judge_carried"] for r in runs)
            cell = classify_cell(arms["with"], arms["without"], policy, carried)
        cells.append({"fixture": fixture or None, "model": model, "key": pool["key"],
                      **cell,
                      "cost_usd": _finite_sum([r["numbers"]["cost_usd"] for r in runs]),
                      "cost_unknown_trials": sum(r["numbers"]["cost_unknown_trials"]
                                                 for r in runs),
                      "runs": {role: [_run_entry(r) for r in sorted(arms[role], key=_order)]
                               for role in arms}})
    classes = {c["class"] for c in cells}
    if not expected:
        verdict, reason = "insufficient", "no fixture on main"
    elif "benefit" in classes:
        verdict, reason = "keep", "a cell shows benefit"
    elif classes & {"insufficient", "missing"}:
        verdict, reason = "insufficient", "a cell is insufficient or missing"
    elif "cannot-discriminate" in classes:
        verdict, reason = "cannot-discriminate", "a cell cannot discriminate"
    elif "ceiling" in classes:
        verdict, reason = "ceiling", "a cell is at the ceiling"
    elif "no-benefit-pending" in classes:
        verdict, reason = ("retire-pending", "every cell shows no benefit, but a cell "
                           "has yet to show it at two looks")
    elif cells and classes == {"no-benefit"}:
        verdict, reason = "retire", "every cell shows no benefit"
    else:
        # No cell (no roster model) or a class this verdict does not know:
        # never a retirement without evidence.
        verdict, reason = "insufficient", "no cell shows no benefit"
    return {"verdict": verdict, "reason": reason, "cells": cells,
            "runs_used": sorted(used)}


# ---------------------------------------------------------------------------
# Guidance sections (skills-evals#124).

#: Each of a section's two comparisons, and the role of an arm in it by its
#: delivery mode. `in_situ` is the ablation, the whole guidance with and
#: without the section, and decides removal; `alone` is the section against
#: no guidance, and only says whether the section teaches the behavior.
GUIDANCE_PAIRS = {"in_situ": {"full": "with", "full-minus-section": "without"},
                  "alone": {"section": "with", "none": "without"}}


def guidance_verdict(index: dict, models: list, section: str, policy: dict,
                     expected: dict) -> dict:
    """The verdict for one guidance section: `retirement` over its in-situ
    cells (every run of `index` whose `pair` is `in_situ`), and over its
    alone cells for contrast. `expected` is as for `pool_cells`, with the
    section's own extent digest as `tested_digest`, and a run's `pair_key`
    and arm context are the digest of the rest of the guidance it ran
    beside, so the two arms of a cell always ran beside the same rest.

    THE TWIST: a section whose in-situ verdict is `retire` while some alone
    cell shows `benefit` teaches the behavior, but the rest of the guidance
    already covers it. Its verdict is `shorten-or-merge`, never a removal.
    Every other in-situ verdict stands as it is."""
    runs = index.get("runs", [])
    out = {pair: retirement({"runs": [r for r in runs if r.get("pair") == pair]},
                            models, section, policy, expected)
           for pair in GUIDANCE_PAIRS}
    in_situ, alone = out["in_situ"], out["alone"]
    verdict, reason = in_situ["verdict"], in_situ["reason"]
    if verdict == "retire" and any(c["class"] == "benefit" for c in alone["cells"]):
        verdict, reason = ("shorten-or-merge", "no marginal benefit inside the whole "
                           "guidance, but a cell shows benefit alone (section vs "
                           "none): the rest of the guidance covers it")
    return {"verdict": verdict, "reason": reason, "in_situ_verdict": in_situ["verdict"],
            "cells": in_situ["cells"],
            "alone": {"verdict": alone["verdict"], "reason": alone["reason"],
                      "cells": alone["cells"]},
            "runs_used": sorted(set(in_situ["runs_used"]) | set(alone["runs_used"]))}
