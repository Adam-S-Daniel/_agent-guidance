#!/usr/bin/env python3
"""Propose one fleet-guidance section edit, measure it, keep the record.

The guidance analog of scripts/propose_skill_edit.py (skills-evals#71, ADR
0005), for one `##` section of _agent-guidance's `agents-md/base.md` (or a
file under `agents-md/sections/`), keyed by its `eval-coverage.yml` id.

    python3 scripts/propose_guidance_edit.py <section-id> --guidance PATH \\
        [--ref REF] [--trials 3] [--rotation K] [--holdout FIXTURE] \\
        [--results-dir DIR] [--no-judge] [--dry-run]

WHAT IT DOES, in order:

 1. FIXTURES: every guidance fixture of the section that declares the
    `full` ablation arm (`fixture_layout`, the one place that knows the
    layout). A section with fewer than four is refused (exit 2, "needs more
    fixtures"): train, validation and a fixed holdout each need one, and one
    train fixture alone is a coin toss.
 2. SPLIT: holdout is `--holdout` or the last fixture in name order, never
    seen by the proposer; validation is `remaining[rotation % n]`, rotating
    with the number of records already written; train is the rest.
 3. Two scratch guidance copies from `git archive <ref>` of the checkout
    (no `.git`, so no push path), and the section's manifest row. A
    `skipped` row is refused: its owner decided it is not measured.
 4. PROPOSAL, before any measurement: one call (the roster's judge model,
    no tools) given the section's current text, the whole delivered corpus,
    the TRAIN fixtures' prompts, check descriptions and judge rubrics, and
    the checkout's `.githooks/agent-checks`. Deliberately NOT baseline
    transcripts: measuring baseline first and candidate later would break
    the interleaving below, and a separate evidence pass would double the
    cost. The reply is `{rationale, section, hook_candidates}`: the full
    replacement extent with its identical heading line, no new `##`, and
    the rules a commit-time check could enforce instead (recorded, never
    implemented here). An invalid reply is recorded and nothing is measured.
 5. MEASURE, interleaved (thresholds design rule B1): for each trial k, for
    each fixture, the baseline's trial k then the candidate's, each its own
    `run_eval.py --ablation --arm ablation_full` invocation. When any arm's
    passed count varies across trials, or the train decision is within one
    check of its threshold, the same number of trials runs again, once.
 6. DECIDE (`decide`, pure; rule B): the train pooled pass rate must gain at
    least `min(0.10, (1 - b) / 2)` with a Newcombe hybrid-score 95% lower
    bound above zero (or hold its objective with the judge up by 1.0);
    validation and holdout reject only a confident regression or a point
    drop beyond `max(0.05, 1/total checks)`; tokens follow
    propose_skill_edit's rule exactly. Any errored fixture is inconclusive.
 7. PATCH: the section replaced, its `eval-coverage.yml` `bytes` recomputed,
    a `docs/guidance-impact.md` entry, AGENTS.md regenerated, all diffed
    against the baseline copy, then VALIDATED by _agent-guidance's own CI
    checks in a throwaway git repository (`validate_patch`).
 8. Writes `<results>/guidance-improvements/<section>/<ts>.json` always,
    `<ts>.patch` whenever a candidate existed, and `<ts>.pr-body.md` only
    when accepted and the patch validated. Nothing is pushed and no pull
    request is opened.

`--dry-run` prints the plan and makes no model call and no write.

Every model call goes through a `Runner`, so the tests drive the pipeline
offline with a fake. A LOCAL exhibit under ADR 0002 decision 4, like
propose_skill_edit: the operator's login, not badge input, never in CI.

Exit codes: 0 accepted and the patch validated, 1 rejected or an invalid
proposal, 2 refused or errored (a failed patch validation included).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
EVALS_DIR = REPO_ROOT / "evals"
RUN_EVAL = REPO_ROOT / "harness" / "run_eval.py"

sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guidance  # noqa: E402
import run_eval  # noqa: E402
import local_eval  # noqa: E402
import propose_skill_edit as pse  # noqa: E402
from propose_skill_edit import (  # noqa: E402
    EXIT_ACCEPTED, EXIT_REFUSED, EXIT_REJECTED, JUDGE_TOLERANCE, Refusal,
    TOKEN_INCREASE_TOLERANCE, TOKENS_BASIS, check_launch_environment,
    guarded_environment, record_refusal, write_record)

MIN_FIXTURES = 4
DEFAULT_TRIALS = 3
#: Rule B's ceiling on the required train gain; less when headroom is short.
MAX_REQUIRED_GAIN = 0.10
#: A validation or holdout point drop at most this (or one check) is noise.
MIN_DROP_TOLERANCE = 0.05
#: A judge-only train gain: objective equal, judge mean up by at least this.
JUDGE_ONLY_GAIN = 1.0
#: Two-sided 95% normal quantile for the Wilson and Newcombe intervals.
Z95 = 1.959963984540054
#: Proposal reply limits for `hook_candidates`.
MAX_HOOK_CANDIDATES = 10
MAX_HOOK_TEXT = 300
#: The impact entry's rationale, collapsed to one line, at most this long.
MAX_MOTIVATION_CHARS = 300
#: The decision the PR body refers to (skills-evals ADR 0005); never an issue.
TRACKING_ISSUE = "skills-evals ADR 0005"
#: _agent-guidance files the patch may touch besides the section's own file.
MANIFEST_REL = "agents-md/eval-coverage.yml"
IMPACT_REL = "docs/guidance-impact.md"
AGENTS_REL = "AGENTS.md"
AGENT_CHECKS_REL = ".githooks/agent-checks"
#: The repository's own AGENTS.md recipe (its AGENTS.md, "Regenerate with").
REGENERATE_AGENTS_MD = (
    "printf '%s\\n%s\\n' \"$(./scripts/build-agents-md.sh)\" "
    "\"$(sed -n '/^## Repo-specific additions/,$p' AGENTS.md)\" "
    "> AGENTS.md.new && mv AGENTS.md.new AGENTS.md")
#: ci.yml's "Self-guidance is current": the regenerated file, to "$1".
SELF_GUIDANCE_CURRENT = (
    "printf '%s\\n%s\\n' \"$(./scripts/build-agents-md.sh)\" "
    "\"$(sed -n '/^## Repo-specific additions/,$p' AGENTS.md)\" "
    "> \"$1\" && diff -u AGENTS.md \"$1\"")
#: A clean git for the throwaway validation repository: no user config, a
#: fixed identity, never a remote.
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "propose-guidance-edit",
                "GIT_AUTHOR_EMAIL": "noreply@example.com",
                "GIT_COMMITTER_NAME": "propose-guidance-edit",
                "GIT_COMMITTER_EMAIL": "noreply@example.com",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
#: How much of a validation command's output the record keeps.
OUTPUT_TAIL_CHARS = 400


class InvalidProposal(Exception):
    """The proposal broke a constraint; it is recorded and never measured."""


# ---------------------------------------------------------------------------
# Layout adapter: the ONLY place that knows where guidance fixtures live, how
# one trial is invoked and where its summary lands. A layout change is an
# edit here and nowhere else.
# ---------------------------------------------------------------------------

#: The ablation mode a fixture must declare, and the arm run_eval names it.
ABLATION_MODE = "full"
ARM = "ablation_full"


def fixture_layout_discover(evals_dir: Path, section: str) -> list[str]:
    """The section's fixtures, as paths relative to `evals_dir`, sorted:
    `guidance/<dir>/fixture.yaml` and `guidance/<dir>/<name>/fixture.yaml`
    with `subject: guidance`, this `section:` and `full` in `ablation:`.
    A directory whose name starts with `_` is skipped at either level
    (`_delivery` measures delivery, not behavior)."""
    root = evals_dir / "guidance"
    candidates = []
    if root.is_dir():
        for top in sorted(root.iterdir()):
            if not top.is_dir() or top.name.startswith("_"):
                continue
            candidates.append(top)
            candidates += [sub for sub in sorted(top.iterdir())
                           if sub.is_dir() and not sub.name.startswith("_")]
    found = []
    for directory in candidates:
        path = directory / run_eval.FIXTURE_FILE
        if not path.is_file():
            continue
        try:
            fixture = run_eval.load_fixture(directory)
        except (OSError, yaml.YAMLError, guidance.GuidanceError) as exc:
            raise Refusal(f"fixture {directory.relative_to(evals_dir).as_posix()!r} "
                          f"is unreadable: {exc}") from exc
        ablation = fixture.get("ablation")
        if (fixture.get("subject") == "guidance" and fixture.get("section") == section
                and isinstance(ablation, list) and ABLATION_MODE in ablation):
            found.append(directory.relative_to(evals_dir).as_posix())
    return sorted(found)


def fixture_layout_argv(fixture_dir: Path, guidance_dir: Path, results_dir: Path,
                        ts: str, no_judge: bool, read_deny: Path) -> list[str]:
    """run_eval.py's argv for ONE trial of the `full` ablation arm.

    No `--timestamp`: run_eval refuses it for a guidance fixture outside
    in-place pairing, so every trial of the first live fire (2026-10-28)
    ended `missing_summary`. Each trial gets its own empty `results_dir`
    instead, and `fixture_layout_summary` finds the one summary in it."""
    argv = [str(fixture_dir), "--ablation", "--arm", ARM,
            "--guidance", str(guidance_dir), "--results-dir", str(results_dir),
            "--read-deny", str(read_deny),
            "--run-billing", "subscription", "--run-runner", "workstation",
            "--run-location", "local", "--run-id", ts]
    return argv + (["--no-judge"] if no_judge else [])


def fixture_layout_summary(results_dir: Path, section: str) -> Path:
    """That one trial's summary.json, under the run's own timestamp: the only
    `<ARM>/summary.json` below `guidance/<section>/` in its fresh results
    directory. None found, or more than one, gives a path that does not
    exist, which `read_summary` reads as a missing summary."""
    found = sorted((results_dir / "guidance" / section).glob(f"**/{ARM}/summary.json"))
    if len(found) == 1:
        return found[0]
    return results_dir / "guidance" / section / ARM / "summary.json"


# ---------------------------------------------------------------------------
# The runner: every step that can spend model budget, and nothing else.
# ---------------------------------------------------------------------------

class Runner:
    """The real thing. Tests substitute a fake with the same two methods."""

    def run_eval(self, argv: list[str]) -> int:
        """One guarded run_eval.py invocation; 2 means a runner error."""
        with guarded_environment() as env:
            try:
                return subprocess.run([sys.executable, str(RUN_EVAL), *argv],
                                      env=env, check=False).returncode
            except OSError as exc:
                raise Refusal(f"run_eval could not run: {type(exc).__name__}") from exc

    def propose(self, prompt: str, model: str) -> str:
        """propose_skill_edit's call: one headless call, no tools."""
        return pse.Runner().propose(prompt, model)


# ---------------------------------------------------------------------------
# Split, checkout, row
# ---------------------------------------------------------------------------

def split_fixtures(fixtures: list[str], rotation: int,
                   holdout: str | None = None) -> tuple[list[str], str, str]:
    """(train, validation, holdout) per thresholds design rule A1."""
    if len(fixtures) < MIN_FIXTURES:
        raise Refusal(f"needs more fixtures: {len(fixtures)} found, "
                      f"{MIN_FIXTURES} required")
    ordered = sorted(fixtures)
    if holdout is None:
        holdout = ordered[-1]
    elif holdout not in ordered:
        raise Refusal(f"--holdout {holdout!r} is not a fixture of this section")
    remaining = [f for f in ordered if f != holdout]
    validation = remaining[rotation % len(remaining)]
    return [f for f in remaining if f != validation], validation, holdout


def check_validation_tools(guidance_dir: Path) -> None:
    """The patch validator needs node and the checkout's own node_modules."""
    if shutil.which("node") is None or not (
            guidance_dir / "node_modules" / "markdown-it").is_dir():
        raise Refusal("patch validation needs `node` on PATH and the guidance "
                      "checkout's node_modules: run `npm ci` in "
                      f"{guidance_dir}")


def extent_of(text: str, heading: str) -> dict:
    spans = [s for s in guidance.h2_extents(text) if s["heading"] == heading]
    if len(spans) != 1:
        raise Refusal(f"heading {heading!r} appears {len(spans)} times; "
                      "expected exactly once")
    return spans[0]


def load_row(guidance_dir: Path, section: str) -> dict:
    try:
        row = guidance.find_row(guidance.load_manifest(guidance_dir), section,
                                guidance_dir)
    except guidance.GuidanceError as exc:
        raise Refusal(str(exc)) from exc
    if row.get("status") == "skipped":
        raise Refusal(f"section {section!r} is a skipped row: "
                      f"{row.get('reason') or 'no reason given'}")
    return row


# ---------------------------------------------------------------------------
# The proposal
# ---------------------------------------------------------------------------

PROPOSAL_CONSTRAINTS = """\
Constraints:
- Reply with the FULL replacement of this one section, starting with its
  identical `## ` heading line. No new `##` heading (`###` is allowed).
- Keep it no longer than it needs to be: every byte is delivered into every
  agent session in the fleet.
- Generalize: teach the behavior the checks below look for; do not quote a
  fixture, its file names or its answers.
- A rule already enforced by the commit-time checks in agent-checks (below)
  should be cut from the section: its refusal message documents it.
- List in hook_candidates each rule left in the section that a commit-time
  check in agent-checks could enforce instead, with a one-line description
  of that check (an empty list is fine)."""


def fixture_brief(name: str, fixture: dict) -> list[str]:
    """A train fixture's prompt, check descriptions and judge rubric."""
    checks = list(fixture.get("objective_checks") or [])
    for arm in (fixture.get("arms") or {}).values():
        if isinstance(arm, dict) and arm.get("mode") == ABLATION_MODE:
            checks += arm.get("objective_checks") or []
    lines = [f"<fixture name={json.dumps(name)}>",
             f"Prompt: {str(fixture.get('prompt', '')).strip()}"]
    for check in checks:
        if isinstance(check, dict):
            text = " ".join(str(check.get("description") or "").split())
            lines.append(f"- check {check.get('id')}: {text}")
    rubric = fixture.get("judge_rubric")
    if rubric:
        lines += ["Judge rubric:", str(rubric).strip()]
    return lines + ["</fixture>", ""]


def proposal_prompt(section: str, extent: str, corpus: str,
                    agent_checks: str | None, train: dict) -> str:
    parts = [f"You are improving the fleet guidance section `{section}`. Agents "
             "receive the guidance below in every session; fixtures measure "
             "whether they follow this section.", "", PROPOSAL_CONSTRAINTS, "",
             "<section>", extent, "</section>", "",
             "<delivered_guidance>", corpus, "</delivered_guidance>", ""]
    if agent_checks is None:
        parts += ["<agent_checks>", "(absent: this checkout has no "
                  f"{AGENT_CHECKS_REL}, so no rule is enforced at commit time)",
                  "</agent_checks>", ""]
    else:
        parts += ["<agent_checks>", agent_checks, "</agent_checks>", ""]
    for name, fixture in train.items():
        parts += fixture_brief(name, fixture)
    parts += ['Answer with ONE JSON object and nothing else: {"rationale": '
              '"<why this rewrite teaches the behavior better>", "section": '
              '"<the full replacement section>", "hook_candidates": [{"rule": '
              '"<a rule in the section>", "check": "<the commit-time check '
              'that could enforce it>"}]}']
    return "\n".join(parts)


def parse_proposal(reply: str) -> dict:
    """The first `{rationale, section, hook_candidates}` object in `reply`,
    tolerating a code fence, with `hook_candidates` validated."""
    decoder = json.JSONDecoder()
    start = reply.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(reply, start)
        except json.JSONDecodeError:
            start = reply.find("{", start + 1)
            continue
        if (isinstance(value, dict) and isinstance(value.get("rationale"), str)
                and isinstance(value.get("section"), str)
                and "hook_candidates" in value):
            value["hook_candidates"] = check_hook_candidates(value["hook_candidates"])
            return value
        start = reply.find("{", start + 1)
    raise InvalidProposal("proposal is not a {rationale, section, "
                          "hook_candidates} JSON object")


def check_hook_candidates(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_HOOK_CANDIDATES:
        raise InvalidProposal(f"hook_candidates must be a list of at most "
                              f"{MAX_HOOK_CANDIDATES} objects")
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"rule", "check"}
                or not all(isinstance(item[k], str) and item[k].strip()
                           and len(item[k]) <= MAX_HOOK_TEXT
                           for k in ("rule", "check"))):
            raise InvalidProposal(
                "each hook candidate must be exactly {rule, check}, two "
                f"non-empty strings of at most {MAX_HOOK_TEXT} characters")
    return value


def normalize_section(proposed: str, original: str, heading: str) -> str:
    """The validated replacement extent, its trailing whitespace made the
    original's (so the next heading stays separated), else InvalidProposal."""
    if not proposed.strip():
        raise InvalidProposal("the proposed section is empty")
    old_heading_line = original.splitlines()[0]
    if proposed.splitlines()[0] != old_heading_line:
        raise InvalidProposal("the proposed section must start with the "
                              f"identical heading line {old_heading_line!r}")
    tail = original[len(original.rstrip()):]
    new = proposed.rstrip() + (tail if "\n" in tail else "\n")
    spans = guidance.h2_extents(new)
    if len(spans) != 1 or spans[0]["heading"] != heading:
        raise InvalidProposal(f"the proposed section holds {len(spans)} `##` "
                              "headings; exactly its own is allowed")
    if new == original:
        raise InvalidProposal("the proposed section is unchanged")
    return new


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def slug(fixture: str) -> str:
    return fixture.replace("/", "--")


def trial_dir(results: Path, ts: str, side: str, fixture: str, k: int) -> Path:
    return results / "runs" / ts / side / slug(fixture) / f"trial-{k}"


def fixture_metrics(summaries: list[dict | None], rcs: list[int]) -> dict:
    """One fixture's pooled metrics over its trials; any errored trial makes
    the fixture errored (inconclusive), never a failure."""
    error = None
    clean = []
    for summary, rc in zip(summaries, rcs):
        if not isinstance(summary, dict):
            error = error or "missing_summary"
            summary = {"error": {"type": "missing_summary"}}
        elif rc not in (0, 1):
            error = error or "trial_exit"
        elif summary.get("inconclusive") is True:
            error = error or "inconclusive"
        elif summary.get("error"):
            err = summary["error"]
            error = error or (err.get("type") if isinstance(err, dict) else None) or "trial_error"
        if summary.get("error") and not isinstance(summary["error"], dict):
            summary = dict(summary, error={"type": "trial_error"})
        clean.append(summary)
    stats = run_eval.aggregate_trials(clean)
    objective = stats["aggregate"]["objective"] or {}
    judge_stats = stats["aggregate"]["judge"] or {}
    if not error and judge_stats.get("errors"):
        error = "judge_error"
    per_trial = [sum(1 for c in s.get("objective_checks") or [] if c.get("passed"))
                 for s in clean if not s.get("error")]
    return {"error": error, "passed": objective.get("passed"),
            "total": objective.get("total"),
            "judge_mean": (judge_stats.get("overall") or {}).get("mean"),
            "n": stats["n"], "per_trial_passed": per_trial,
            "tokens": pse.mean_total_tokens(clean), "tokens_basis": TOKENS_BASIS}


def read_summary(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Decision (pure)
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (Wilson 1927; method 3
    in Newcombe, "Interval estimation for the difference between independent
    proportions", Statistics in Medicine 17:873-890, 1998)."""
    if n <= 0:
        raise ValueError("a Wilson interval needs n > 0")
    p = successes / n
    denom = 2 * (n + z * z)
    center = 2 * successes + z * z
    half = z * math.sqrt(z * z + 4 * successes * (1 - p))
    return (center - half) / denom, (center + half) / denom


def newcombe_interval(x1: int, n1: int, x2: int, n2: int,
                      z: float = Z95) -> tuple[float, float]:
    """95% interval for p1 - p2 by Newcombe's hybrid score method (method 10
    of Newcombe 1998, cited above), from each proportion's Wilson interval.
    Table II's example 56/70 vs 48/80 gives (0.0524, 0.3339)."""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson_interval(x1, n1, z)
    l2, u2 = wilson_interval(x2, n2, z)
    d = p1 - p2
    return (d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def pooled(metrics: dict, names: list[str]) -> dict:
    rows = [metrics.get(n) or {"error": "missing fixture"} for n in names]
    passed = sum(r.get("passed") or 0 for r in rows)
    total = sum(r.get("total") or 0 for r in rows)
    judged = [r.get("judge_mean") for r in rows]
    return {"passed": passed, "total": total,
            "rate": passed / total if total else None,
            "judge_mean": (sum(judged) / len(judged)
                           if judged and all(run_eval._is_number(j) for j in judged)
                           else None)}


def required_gain(baseline_rate: float) -> float:
    """Rule B: at most ten points, at most half the remaining headroom."""
    return min(MAX_REQUIRED_GAIN, (1 - baseline_rate) / 2)


def near_threshold(baseline: dict, candidate: dict, train: list[str]) -> bool:
    """The train gain is within one check of the required gain."""
    b, c = pooled(baseline, train), pooled(candidate, train)
    if not b["total"] or not c["total"]:
        return False
    return abs((c["rate"] - b["rate"]) - required_gain(b["rate"])) <= 1 / b["total"]


def arm_varies(*sides: dict) -> bool:
    return any(len(set(m.get("per_trial_passed") or [])) > 1
               for side in sides for m in side.values())


def guard_group(b: dict, c: dict, label: str, judge_tolerance: float) -> list[str]:
    """Why a validation or holdout group rejects, or nothing."""
    reasons = []
    drop_limit = max(MIN_DROP_TOLERANCE, 1 / b["total"])
    _, upper = newcombe_interval(c["passed"], c["total"], b["passed"], b["total"])
    if upper < 0:
        reasons.append(f"{label} objective regressed confidently "
                       f"({b['passed']}/{b['total']} -> {c['passed']}/{c['total']}, "
                       f"upper bound {upper:.4f})")
    elif b["rate"] - c["rate"] > drop_limit and not math.isclose(
            b["rate"] - c["rate"], drop_limit, rel_tol=0, abs_tol=1e-12):
        reasons.append(f"{label} objective fell more than {drop_limit:.3f} "
                       f"({b['rate']:.3f} -> {c['rate']:.3f})")
    if b["judge_mean"] is not None and c["judge_mean"] is not None and \
            c["judge_mean"] < b["judge_mean"] - judge_tolerance:
        reasons.append(f"{label} judge fell more than {judge_tolerance:g}: "
                       f"{b['judge_mean']:.2f} -> {c['judge_mean']:.2f}")
    return reasons


def decide(baseline: dict, candidate: dict, train: list[str],
           validation: str, holdout: str) -> tuple[bool, list[str]]:
    """(accepted, reasons) over per-fixture metrics, thresholds rule B."""
    measured = train + [validation, holdout]
    errored = sorted({n for side in (baseline, candidate) for n in measured
                      if (side.get(n) or {}).get("error")
                      or not (side.get(n) or {}).get("total")})
    if errored:
        return False, [f"inconclusive: errored or unscored fixtures {errored}"]
    missing_judge = [n for n in measured
                     if run_eval._is_number(baseline[n].get("judge_mean"))
                     != run_eval._is_number(candidate[n].get("judge_mean"))]
    if missing_judge:
        return False, [f"inconclusive: judge scores on one side only {missing_judge}"]
    bases = sorted({pse.tokens_basis(side.get(n)) for side in (baseline, candidate)
                    for n in measured})
    if len(bases) > 1:
        return False, [f"inconclusive: token bases differ {bases}; baseline "
                       "and candidate must be measured the same way"]
    unknown = [f"{label} {n}" for label, side in (("baseline", baseline),
                                                  ("candidate", candidate))
               for n in measured if not pse.valid_tokens(side[n].get("tokens"))]
    if unknown:
        return False, [f"inconclusive: missing token data for {unknown}"]
    b_tokens = sum(baseline[n]["tokens"] for n in measured)
    c_tokens = sum(candidate[n]["tokens"] for n in measured)
    limit = b_tokens * (1 + TOKEN_INCREASE_TOLERANCE)
    costs_more = c_tokens > limit and not math.isclose(c_tokens, limit, rel_tol=1e-9)

    reasons = []
    b, c = pooled(baseline, train), pooled(candidate, train)
    gain, g_req = c["rate"] - b["rate"], required_gain(b["rate"])
    lower, _ = newcombe_interval(c["passed"], c["total"], b["passed"], b["total"])
    meets = gain >= g_req or math.isclose(gain, g_req, rel_tol=0, abs_tol=1e-12)
    improved = b["rate"] < 1.0 and meets and lower > 0
    if b["passed"] == c["passed"] and b["total"] == c["total"] \
            and b["judge_mean"] is not None and c["judge_mean"] is not None \
            and c["judge_mean"] - b["judge_mean"] >= JUDGE_ONLY_GAIN:
        improved = True
        reasons.append(f"train objective held and judge rose "
                       f"{b['judge_mean']:.2f} -> {c['judge_mean']:.2f}")
    elif b["rate"] == 1.0:
        reasons.append("train has no headroom: baseline passes every check")
    elif not improved:
        reasons.append(f"train did not gain enough: {b['passed']}/{b['total']} -> "
                       f"{c['passed']}/{c['total']} (gain {gain:.3f}, required "
                       f"{g_req:.3f}, Newcombe lower bound {lower:.4f})")
    else:
        reasons.append(f"train gained {gain:.3f} (required {g_req:.3f}, Newcombe "
                       f"lower bound {lower:.4f})")

    held_reasons = (guard_group(pooled(baseline, [validation]),
                                pooled(candidate, [validation]), "validation",
                                JUDGE_TOLERANCE)
                    + guard_group(pooled(baseline, [holdout]),
                                  pooled(candidate, [holdout]), "holdout", 0.0))
    reasons += held_reasons
    held = not held_reasons
    reasons.append(f"tokens {b_tokens:g} -> {c_tokens:g} per trial over "
                   f"{', '.join(measured)}")
    if costs_more and not improved:
        reasons.append("candidate costs more tokens without a quality gain")
    elif costs_more and held:
        reasons.append("candidate costs more tokens; accepted only because "
                       "train gained at least the minimum")
    if held and improved:
        reasons.append("validation and holdout held and train improved")
    return held and improved, reasons


# ---------------------------------------------------------------------------
# The _agent-guidance patch
# ---------------------------------------------------------------------------

def replace_extent(text: str, heading: str, new_extent: str) -> str:
    span = extent_of(text, heading)
    return text[:span["start"]] + new_extent + text[span["end"]:]


def extent_bytes(text: str, heading: str) -> int:
    """The manifest's `bytes`: the extent's UTF-8 length (h2_extents gives
    character offsets; check-guidance-coverage.js counts bytes)."""
    span = extent_of(text, heading)
    return len(text[span["start"]:span["end"]].encode("utf-8"))


def set_manifest_bytes(text: str, section: str, value: int) -> str:
    """`text` (eval-coverage.yml) with only that row's `bytes:` scalar
    replaced, located by its composed node's marks; comments survive."""
    root = yaml.compose(text)
    if not isinstance(root, yaml.SequenceNode):
        raise Refusal(f"{MANIFEST_REL} is not a YAML list")
    for row in root.value:
        if not isinstance(row, yaml.MappingNode):
            continue
        fields = {k.value: v for k, v in row.value if isinstance(k, yaml.ScalarNode)}
        ident = fields.get("id")
        if isinstance(ident, yaml.ScalarNode) and ident.value == section:
            node = fields.get("bytes")
            if not isinstance(node, yaml.ScalarNode):
                raise Refusal(f"row {section!r} has no scalar `bytes:` to update")
            return (text[:node.start_mark.index] + str(value)
                    + text[node.end_mark.index:])
    raise Refusal(f"no row {section!r} in {MANIFEST_REL}")


def one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 3].rstrip() + "..."


def ratio(m: dict) -> str:
    return f"{m['passed']}/{m['total']}"


def impact_entry(section: str, ts: str, now: datetime, rationale: str,
                 hook_count: int, old_bytes: int, new_bytes: int,
                 baseline: dict, candidate: dict, split: dict, trials: int,
                 fixtures: list[str]) -> str:
    motivation = f"automatic improve run {ts}: {one_line(rationale, MAX_MOTIVATION_CHARS)}"
    if hook_count:
        motivation += (f"; {hook_count} rule{'s' if hook_count != 1 else ''} "
                       "could move to agent-checks")
    groups = [(label, names) for label, names in (
        ("train objective", split["train"]), ("validation", [split["validation"]]),
        ("holdout", [split["holdout"]]))]
    evals = ", ".join(f"{label} {ratio(pooled(baseline, names))} -> "
                      f"{ratio(pooled(candidate, names))}" for label, names in groups)
    date = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return (f"## {date} — {section} — edit\n"
            f"- Motivation: {motivation}\n"
            f"- Change: rewrote the section ({old_bytes} -> {new_bytes} bytes); "
            "proposed by skills-evals scripts/propose_guidance_edit.py\n"
            f"- Eval: {evals}, n={trials} per side; fixtures {', '.join(fixtures)}\n"
            "- Outcome: pending\n\n")


DATED_HEADING = re.compile(r"\d{4}-\d{2}-\d{2} —")


def insert_impact_entry(text: str, entry: str) -> str:
    """`entry` before the first dated `##` entry (a real heading token; a
    `##` inside the "Entry format" fence is a fence token), else appended."""
    tokens = guidance._markdown_it().parse(text)
    lines = text.splitlines(keepends=True)
    for i, token in enumerate(tokens):
        if (token.type == "heading_open" and token.tag == "h2"
                and DATED_HEADING.match(tokens[i + 1].content)):
            offset = sum(len(line) for line in lines[:token.map[0]])
            return text[:offset] + entry + text[offset:]
    body = text if text.endswith("\n") else text + "\n"
    return body + "\n" + entry.rstrip("\n") + "\n"


def regenerate_agents_md(root: Path) -> None:
    proc = subprocess.run(["bash", "-c", REGENERATE_AGENTS_MD], cwd=root,
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Refusal(f"regenerating AGENTS.md exited {proc.returncode}")


def build_patch(base_root: Path, cand_root: Path, paths: list[str]) -> str:
    """A `git apply`-able diff of the changed paths, rooted at the checkout,
    with `a/` and `b/` prefixes."""
    out = []
    env = dict(os.environ, **GIT_IDENTITY)
    # Raw git is fine here (#343): two scratch copies with no .git, never an
    # agent-touched workspace, so harness/workspace_git.py does not apply.
    for rel in paths:
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--no-color", "--no-ext-diff",
             "--src-prefix=a/", "--dst-prefix=b/", "--", f"baseline/{rel}",
             f"candidate/{rel}"],
            cwd=base_root.parent, capture_output=True, text=True, env=env, check=False)
        if proc.returncode not in (0, 1):
            raise Refusal(f"git diff of {rel} exited {proc.returncode}")
        for line in proc.stdout.splitlines(keepends=True):
            if line.startswith(("diff --git ", "--- a/", "+++ b/")):
                line = (line.replace("a/baseline/", "a/", 1)
                        .replace("b/candidate/", "b/", 1))
            out.append(line)
    return "".join(out)


def validate_patch(baseline_dir: Path, candidate_dir: Path,
                   guidance_checkout: Path, now: datetime) -> dict:
    """_agent-guidance's own CI checks on baseline -> candidate, in a fresh
    git repository with no remote. Exit codes and short tails only."""
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    env = dict(os.environ, **GIT_IDENTITY, GIT_AUTHOR_DATE=stamp,
               GIT_COMMITTER_DATE=stamp)
    with tempfile.TemporaryDirectory(prefix="propose-skill-edit-guidance-validate-") as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(baseline_dir, repo, symlinks=True)

        # Raw git is fine here (#343): a throwaway repository this function
        # creates, never an agent-touched workspace.
        def git(*args: str) -> str:
            proc = subprocess.run(["git", *args], cwd=repo, env=env,
                                  capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise Refusal(f"validation git {args[0]} exited {proc.returncode}")
            return proc.stdout.strip()

        git("init", "-q", "-b", "main")
        git("add", "-A")
        git("commit", "-q", "--no-verify", "-m", "baseline")
        base = git("rev-parse", "HEAD")
        for child in repo.iterdir():
            if child.name != ".git":
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        shutil.copytree(candidate_dir, repo, symlinks=True, dirs_exist_ok=True)
        git("add", "-A")
        git("commit", "-q", "--no-verify", "--allow-empty", "-m", "candidate")
        head = git("rev-parse", "HEAD")
        (repo / "node_modules").symlink_to(
            (guidance_checkout / "node_modules").resolve(), target_is_directory=True)
        event = Path(tmp) / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base},
                                                      "head": {"sha": head}}}),
                         encoding="utf-8")
        run_env = dict(env, GITHUB_EVENT_PATH=str(event))
        checks = {
            "guidance-coverage": ["node", "scripts/check-guidance-coverage.js",
                                  "--check-bytes"],
            "guidance-touch": ["node", "scripts/check-guidance-touch.js"],
            "agents-md-structure": ["./scripts/check-agents-md.sh", "AGENTS.md"],
            "self-guidance-current": ["bash", "-c", SELF_GUIDANCE_CURRENT,
                                      "self-guidance", str(Path(tmp) / "expected.md")],
        }
        results = {}
        for name, cmd in checks.items():
            try:
                proc = subprocess.run(cmd, cwd=repo, env=run_env, capture_output=True,
                                      text=True, check=False, timeout=300)
                code, output = proc.returncode, proc.stdout + proc.stderr
            except (OSError, subprocess.TimeoutExpired) as exc:
                code, output = None, type(exc).__name__
            results[name] = {"exit_code": code, "tail": output[-OUTPUT_TAIL_CHARS:]}
    return {"ok": all(r["exit_code"] == 0 for r in results.values()),
            "base": base, "head": head, "checks": results}


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------

def neutralize(text: str) -> str:
    """Model text in the PR body: no issue reference a closing keyword could
    act on, no @mention."""
    return re.sub(r"#(?=\d)", "number ", text).replace("@", "(at)")


def table(baseline: dict, candidate: dict | None, split: dict) -> str:
    def cell(m):
        if not m:
            return "-", "-"
        if m.get("error") or not m.get("total"):
            return f"error ({m.get('error')})", "-"
        judge = m.get("judge_mean")
        return ratio(m), f"{judge:.2f}" if run_eval._is_number(judge) else "-"
    lines = ["| Fixture | Split | Baseline objective | Candidate objective "
             "| Baseline judge | Candidate judge |", "|---|---|---|---|---|---|"]
    for name in split["train"] + [split["validation"], split["holdout"]]:
        role = ("holdout" if name == split["holdout"] else
                "validation" if name == split["validation"] else "train")
        b_obj, b_judge = cell(baseline.get(name))
        c_obj, c_judge = cell((candidate or {}).get(name))
        lines.append(f"| {name} | {role} | {b_obj} | {c_obj} | {b_judge} | {c_judge} |")
    return "\n".join(lines)


def pr_body(record: dict, rows: str) -> str:
    lines = [f"## Proposed `{record['section']}` guidance edit (validation-gated)", "",
             f"Produced by skills-evals `scripts/propose_guidance_edit.py`. "
             f"Part of {TRACKING_ISSUE}. A local exhibit (skills-evals ADR 0002, "
             "decision 4): measured under the operator's own login, not badge "
             "input. Review before merging; nothing here merges itself.", "",
             f"- Guidance: `{record['guidance']['sha']}`, row `{record['row']['file']}` "
             f"heading `{record['row']['heading']}`",
             f"- Split: train {', '.join(record['split']['train'])}; validation "
             f"{record['split']['validation']} (rotation {record['split']['rotation']}); "
             f"fixed holdout {record['split']['holdout']}",
             f"- Trials per side per fixture: {record['trials']['total']}, arm `{ARM}`",
             "", "### Rationale", "", neutralize(record["proposal"]["rationale"]), "",
             "### Metrics", "", rows, "",
             f"Decision: {'; '.join(record['reasons'])}.", ""]
    hooks = record["proposal"].get("hook_candidates") or []
    if hooks:
        lines += ["### Could move to agent-checks", ""]
        lines += [f"- {neutralize(h['rule'])}: {neutralize(h['check'])}" for h in hooks]
        lines.append("")
    lines += ["The patch is `" + record["files"]["patch"] + "`; apply it with "
              "`git apply` at the _agent-guidance root."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("section")
    parser.add_argument("--guidance", default=None,
                        help="_agent-guidance checkout (default as run_eval.py)")
    parser.add_argument("--ref", default="HEAD",
                        help="guidance commit to archive (default HEAD)")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--rotation", type=int, default=None,
                        help="validation fixture index (default: records so far)")
    parser.add_argument("--holdout", default=None,
                        help="fixed fixture, never shown to the proposer "
                             "(default: the last in name order)")
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; no model call, no write")
    args = parser.parse_args(argv)
    if not 1 <= args.trials <= run_eval.MAX_TRIALS // 2:
        parser.error(f"--trials must be 1..{run_eval.MAX_TRIALS // 2}")
    if args.rotation is not None and args.rotation < 0:
        parser.error("--rotation must be >= 0")
    return args


def plan(args: argparse.Namespace, scratch: Path) -> dict:
    """Everything decided before any model call; writes only into `scratch`."""
    section = args.section
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", section):
        raise Refusal(f"invalid section id {section!r}")
    results = (args.results_dir or pse.default_results_dir()).expanduser().resolve()
    records_dir = results / "guidance-improvements" / section
    fixtures = fixture_layout_discover(EVALS_DIR, section)
    rotation = (args.rotation if args.rotation is not None
                else pse.default_rotation(records_dir))
    train, validation, holdout = split_fixtures(fixtures, rotation, args.holdout)
    loaded = {n: run_eval.load_fixture(EVALS_DIR / n) for n in fixtures}
    models = sorted({pse.arm_model(f, args.no_judge) for f in loaded.values()})
    checkout = guidance.resolve_guidance_dir(
        args.guidance, os.environ.get("AGENT_GUIDANCE_DIR"), REPO_ROOT)
    if not (checkout / ".git").exists():
        raise Refusal(f"no _agent-guidance git checkout at {checkout}; pass --guidance PATH")
    check_validation_tools(checkout)
    sha = pse.resolve_ref(checkout, args.ref)
    base_root = pse.archive_registry(checkout, sha, scratch / "baseline")
    row = load_row(base_root, section)
    return {"section": section, "results": results, "records_dir": records_dir,
            "fixtures": loaded, "rotation": rotation, "train": train,
            "validation": validation, "holdout": holdout, "models": models,
            "checkout": checkout, "sha": sha, "base_root": base_root, "row": row,
            "proposal_model": pse.proposal_model()}


def improve(args: argparse.Namespace, runner, now: datetime,
            validator=validate_patch) -> int:
    # Scratch prefixes (here and in validate_patch) sit under the reviewed
    # `propose-skill-edit-` deny prefix (run_eval.HARNESS_TEMP_PREFIXES), so
    # no arm can read either guidance copy; literals, so the allocation
    # inventory test can read them.
    scratch = Path(tempfile.mkdtemp(prefix="propose-skill-edit-guidance-"))
    try:
        p = plan(args, scratch)
        ts = now.astimezone(timezone.utc).strftime(run_eval.TIMESTAMP_FORMAT)
        section, results = p["section"], p["results"]
        names = p["train"] + [p["validation"], p["holdout"]]
        if args.dry_run:
            first = fixture_layout_argv(
                EVALS_DIR / names[0], p["base_root"],
                trial_dir(results, ts, "baseline", names[0], 1), ts,
                args.no_judge, results)
            print(json.dumps({
                "section": section, "fixtures": sorted(p["fixtures"]),
                "rotation": p["rotation"], "train": p["train"],
                "validation": p["validation"], "holdout": p["holdout"],
                "row": {k: p["row"].get(k) for k in ("file", "heading", "status", "bytes")},
                "guidance": {"path": str(p["checkout"]), "sha": p["sha"]},
                "models": {"arm": p["models"], "proposal": p["proposal_model"]},
                "results_dir": str(results), "first_run_argv": first,
                "model_calls": {"proposal": 1,
                                "run_eval_per_side": len(names) * args.trials,
                                "run_eval_per_side_if_extended": len(names) * args.trials * 2},
            }, indent=2))
            return EXIT_ACCEPTED
        try:
            for path in (results, p["records_dir"]):
                local_eval.check_results_dir(path, require_empty=False)
            local_eval.check_results_dir(results / "runs" / ts)
        except local_eval.Refused as exc:
            raise Refusal(str(exc)) from exc
        p["records_dir"].mkdir(parents=True, exist_ok=True)
        stem = p["records_dir"] / ts
        if stem.with_suffix(".json").exists():
            raise Refusal(f"a record for {ts} already exists")
        return run_pipeline(args, runner, now, validator, p, ts, stem, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_pipeline(args, runner, now, validator, p, ts, stem, scratch) -> int:
    section, results, row = p["section"], p["results"], p["row"]
    names = p["train"] + [p["validation"], p["holdout"]]
    base_root = p["base_root"]
    split = {"rotation": p["rotation"], "train": p["train"],
             "validation": p["validation"], "holdout": p["holdout"]}
    record = {
        "schema": 1, "section": section, "timestamp": ts,
        "local_exhibit": "skills-evals ADR 0002 decision 4: operator login, not badge input",
        "row": {"file": row["file"], "heading": row["heading"],
                "status": row.get("status")},
        "guidance": {"path": str(p["checkout"]), "sha": p["sha"]},
        "split": split, "trials": {"initial": args.trials, "total": args.trials},
        "no_judge": args.no_judge,
        "models": {"arm": p["models"], "proposal": p["proposal_model"]},
        "proposal": None, "baseline": None, "candidate": None,
        "tokens_basis": TOKENS_BASIS, "patch_validation": None,
        "runs": str(results / "runs" / ts), "files": {},
    }
    try:
        check_launch_environment()
    except Refusal as exc:
        return record_refusal(stem, record, "preflight", exc)

    original_file = (base_root / row["file"]).read_text(encoding="utf-8")
    span = extent_of(original_file, row["heading"])
    original = original_file[span["start"]:span["end"]]
    checks_path = base_root / AGENT_CHECKS_REL
    agent_checks = (checks_path.read_text(encoding="utf-8")
                    if checks_path.is_file() else None)
    prompt = proposal_prompt(section, original, guidance.corpus(base_root, row),
                             agent_checks, {n: p["fixtures"][n] for n in p["train"]})
    try:
        reply = runner.propose(prompt, p["proposal_model"])
    except Refusal as exc:
        return record_refusal(stem, record, "proposal", exc)
    try:
        proposal = parse_proposal(reply)
        record["proposal"] = {"model": p["proposal_model"],
                              "rationale": proposal["rationale"],
                              "hook_candidates": proposal["hook_candidates"]}
        new_extent = normalize_section(proposal["section"], original, row["heading"])
    except InvalidProposal as exc:
        record.update(status="invalid-proposal", exit_code=EXIT_REJECTED,
                      reasons=[str(exc)])
        write_record(stem, record)
        print(f"rejected before measurement: {exc}")
        return EXIT_REJECTED

    cand_root = pse.archive_registry(p["checkout"], p["sha"], scratch / "candidate")
    new_file = replace_extent(original_file, row["heading"], new_extent)
    (cand_root / row["file"]).write_text(new_file, encoding="utf-8")

    # Measure, interleaved: baseline trial k then candidate trial k, per fixture.
    raw = {side: {n: ([], []) for n in names} for side in ("baseline", "candidate")}
    roots = {"baseline": base_root, "candidate": cand_root}

    def measure(trials: range) -> dict:
        for k in trials:
            for name in names:
                for side in ("baseline", "candidate"):
                    out = trial_dir(results, ts, side, name, k)
                    rc = runner.run_eval(fixture_layout_argv(
                        EVALS_DIR / name, roots[side], out, ts, args.no_judge, results))
                    summaries, rcs = raw[side][name]
                    summaries.append(read_summary(fixture_layout_summary(out, section)))
                    rcs.append(rc)
        return {side: {n: fixture_metrics(*raw[side][n]) for n in names}
                for side in raw}

    try:
        metrics = measure(range(1, args.trials + 1))
        if arm_varies(metrics["baseline"], metrics["candidate"]) or near_threshold(
                metrics["baseline"], metrics["candidate"], p["train"]):
            metrics = measure(range(args.trials + 1, 2 * args.trials + 1))
            record["trials"]["total"] = 2 * args.trials
    except Refusal as exc:
        return record_refusal(stem, record, "measure", exc)
    baseline, candidate = metrics["baseline"], metrics["candidate"]
    record.update(baseline=baseline, candidate=candidate)
    accepted, reasons = decide(baseline, candidate, p["train"], p["validation"],
                               p["holdout"])
    rows = table(baseline, candidate, split)
    record.update(reasons=reasons, table=rows)

    # The rest of the patch, then the diff and its validation.
    old_bytes = len(original.encode("utf-8"))
    new_bytes = extent_bytes(new_file, row["heading"])
    manifest = cand_root / MANIFEST_REL
    manifest.write_text(set_manifest_bytes(manifest.read_text(encoding="utf-8"),
                                           section, new_bytes), encoding="utf-8")
    impact = cand_root / IMPACT_REL
    impact.write_text(insert_impact_entry(
        impact.read_text(encoding="utf-8"),
        impact_entry(section, ts, now, proposal["rationale"],
                     len(proposal["hook_candidates"]), old_bytes, new_bytes,
                     baseline, candidate, split, record["trials"]["total"], names)),
        encoding="utf-8")
    regenerate_agents_md(cand_root)
    patch = build_patch(base_root, cand_root,
                        [row["file"], MANIFEST_REL, IMPACT_REL, AGENTS_REL])
    stem.with_suffix(".patch").write_text(patch, encoding="utf-8")
    record["files"]["patch"] = str(stem.with_suffix(".patch"))
    validation = validator(base_root, cand_root, p["checkout"], now)
    record["patch_validation"] = validation

    if accepted and not validation["ok"]:
        failed = [n for n, r in validation["checks"].items() if r["exit_code"] != 0]
        record.update(status="refused", phase="patch-validation",
                      exit_code=EXIT_REFUSED,
                      reasons=reasons + [f"patch validation failed: {failed}"])
        write_record(stem, record)
        print(f"refused: the patch failed {failed}; record: {stem.with_suffix('.json')}",
              file=sys.stderr)
        return EXIT_REFUSED
    status = "accepted" if accepted else "rejected"
    code = EXIT_ACCEPTED if accepted else EXIT_REJECTED
    record.update(status=status, exit_code=code)
    if accepted:
        body_path = stem.with_name(stem.name + ".pr-body.md")
        record["files"]["pr_body"] = str(body_path)
        body_path.write_text(pr_body(record, rows), encoding="utf-8")
    write_record(stem, record)
    print(rows)
    print(f"{status}: {'; '.join(reasons)}")
    print(f"record: {stem.with_suffix('.json')}")
    return code


def main(argv: list[str] | None = None, runner=None, now: datetime | None = None,
         validator=validate_patch) -> int:
    args = parse_args(argv)
    try:
        return improve(args, runner or Runner(), now or datetime.now(timezone.utc),
                       validator)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
