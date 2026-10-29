#!/usr/bin/env python3
"""Propose one SKILL.md edit, measure it against held-out fixtures, keep the record.

Part of https://github.com/Adam-S-Daniel/skills-evals/issues/71 (the smallest
slice) and https://github.com/Adam-S-Daniel/skills-evals/issues/232. The
decision record is docs/decisions/0005-improvement-loop-reuses-skill-creator.md.

    python3 scripts/propose_skill_edit.py <skill> \\
        --registry agentskills=PATH [--ref REF] [--trials 3] \\
        [--rotation K] [--min-gain .10] [--holdout FIXTURE] \\
        [--trigger-eval-set FILE] [--skill-creator DIR] [--num-workers 1] \\
        [--results-dir DIR] [--no-judge] [--dry-run]

WHAT IT DOES, in order:

 1. Refuses (exit 2, "needs more fixtures") a skill with fewer than three
    nested fixtures under `evals/<skill>/`: one held-out fixture beside one
    train fixture is a coin toss, not a split.
 2. Splits them deterministically: the fixtures in name order, validation is
    the one at index `rotation % n`, train is the rest. `rotation` defaults to
    the number of records already written for this skill, so consecutive
    runs hold out a different fixture. `--holdout` removes one fixed fixture
    from both train and the rotating validation population on every run.
 3. Builds two scratch registries from `git archive <ref>` of the local
    registry checkout: no `.git`, so nothing in them has a push path.
 4. BASELINE: `scripts/local_eval.py evals/<skill> --arm with_skill --trials N`
    against the first scratch copy.
 4b. EARLY STOP: a baseline train objective that leaves less than `--min-gain`
    to gain, with a judge mean that cannot make it up (missing, or within
    `--min-gain` of 10), is rejected here with a "no train headroom" record,
    before any trigger, proposal or candidate call; add harder fixtures.
 5. TRIGGER HALF, from Anthropic's skill-creator plugin: its
    `scripts/run_loop.py` description-optimization loop (stratified 60/40
    train/held-out split of a should/should-not-trigger query set, 3 runs per
    query, best description picked by HELD-OUT score). The validation
    fixture's prompt and the fixed holdout's prompt are never in that set,
    and a query appears in it once. A fixture-derived set whose train or
    held-out split (by skill-creator's own split arithmetic) lacks a
    should-trigger or should-not-trigger query is refused before any model
    call, with a `trigger-set-unusable` record; a reviewed
    `--trigger-eval-set` is run anyway and its split problems recorded.
    A reviewed set lives beside the skill's fixtures as
    `evals/<skill>/trigger-eval-set.json` (writing-adrs ships one) and is
    passed explicitly; it must be a JSON list of exactly
    `{query: string, should_trigger: boolean}` objects.
    `--num-workers` must be 1 (the default): skill-creator's parallel
    workers share one project directory and undercount triggers.
    Scratch project settings disable the archived registry plugins that
    provide the skill, leaving the operator's login available.
 6. BODY HALF: one proposal call (roster judge model, no tools) returning
    `{rationale, unified_diff}` for SKILL.md's body, fed the train fixtures'
    failed checks and transcripts. A diff naming any other file, or touching
    the frontmatter, is rejected before anything else runs.
 7. CANDIDATE: both halves applied to the second scratch copy and measured
    through the same local_eval invocation.
 8. ACCEPT only if validation held (objective pass rate not lower; judge mean
    not lower by more than 0.5) AND train improved (objective pass rate
    higher by at least `--min-gain` (default .10), or equal with judge mean
    higher by at least ten times that gain). A fixed holdout must not regress
    on either score. This is a conservative heuristic, not a significance test.
    TOKENS (ADR 0005 amendment 2) sit beside that quality rule: each fixture's
    tokens are the mean per trial of the four token counts summed over every
    model in the CLI's `modelUsage` (subagents, sidechains and compaction
    included; ADR 0005's 2026-10-07 addendum), summed over the measured
    fixtures. Baseline and candidate must be measured on the same basis
    (`tokens_basis`). A fixture without token data on either side makes the
    run inconclusive (rejected), never a pass. A candidate that costs more
    tokens than the baseline (`TOKEN_INCREASE_TOLERANCE`, 0: any rise)
    without a quality gain is not accepted; a gain at a higher cost is
    accepted and the rise recorded in the reasons.
 9. Writes `<results>/improvements/<skill>/<ts>.json` always, `<ts>.patch`
    whenever a candidate existed, and `<ts>.pr-body.md` on accept. Nothing is
    pushed and no pull request is opened: a human reads the record first.

`--dry-run` prints the plan and makes no model call and no write.

EVERY MODEL CALL GOES THROUGH A `Runner` (the three methods below), so the
tests drive the whole pipeline offline with a fake. This is a LOCAL exhibit
under ADR 0002 decision 4: it runs under the operator's own login, its numbers
are not badge input, and it never runs in CI.

Exit codes: 0 accepted, 1 rejected (or nothing to measure), 2 refused or
errored before a decision could be taken.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
EVALS_DIR = REPO_ROOT / "evals"
LOCAL_EVAL = REPO_ROOT / "scripts" / "local_eval.py"

sys.path.insert(0, str(HARNESS_DIR))
from cli_json import normalize_cli_result  # noqa: E402
import run_eval  # noqa: E402
import local_eval  # noqa: E402

MIN_FIXTURES = 3
DEFAULT_TRIALS = 3
DEFAULT_MIN_GAIN = 0.10
#: The judge may drop by at most this much on validation (#71 step 3).
JUDGE_TOLERANCE = 0.5
#: How much more total tokens a candidate may cost than the baseline, as a
#: fraction of the baseline, before it "costs more tokens". ADR 0005's
#: Token-aware acceptance addendum (2026-10-06) records "Keep as built":
#: quality first, tokens veto a candidate with no quality gain; any
#: increase counts.
TOKEN_INCREASE_TOLERANCE = 0.0
#: What a fixture's `tokens` measure, recorded as `tokens_basis` in every
#: record and every fixture's metrics. Since ADR 0005's 2026-10-07 addendum
#: ("Switch to total (Recommended)"): the four counts summed over every
#: model in `modelUsage`, so subagent, sidechain and compaction tokens
#: count. Metrics without a basis predate it and measured
#: `TOKENS_BASIS_LEGACY`: the main loop's `usage` alone.
TOKENS_BASIS = "model_usage_total"
TOKENS_BASIS_LEGACY = "usage_main_loop"
#: The four `usage` counts that make up the main loop's tokens (all of them,
#: cache reads and writes included; a trial missing any one is unknown).
#: Recorded as `main_loop_tokens`; no rule reads them.
TOKEN_USAGE_METRICS = ("input_tokens", "output_tokens",
                       "cache_creation_input_tokens", "cache_read_input_tokens")
#: skill-creator's own hard limit on a description (improve_description.py).
DESCRIPTION_MAX_CHARS = 1024
#: How much of one failing trial's final reply the proposal prompt carries.
TRANSCRIPT_EXCERPT_CHARS = 4000
#: The arm whose numbers a SKILL.md edit can move.
ARM = "with_skill"
#: skill-creator's held-out fraction, passed as run_loop's --holdout.
TRIGGER_HOLDOUT = 0.4
#: Parallel `claude -p` calls in skill-creator's trigger eval: exactly one.
#: run_eval.py plants every worker's command file (`<skill>-skill-<uuid>`) in
#: the ONE project directory run_loop.py finds from its cwd, and counts a hit
#: only when the model picks that worker's own copy, so with N > 1 the model
#: sees N identical skills and a real trigger often scores as a miss. A
#: 2026-10-06 A/B on three writing-adrs positives: 7/9 triggers serial, 0/9
#: with 4 workers, 7/9 serial again. run_loop.py passes no per-worker
#: directory, so the only fix that leaves skill-creator unmodified is serial.
DEFAULT_NUM_WORKERS = 1
NUM_WORKERS_REFUSAL = (
    "--num-workers must be 1: skill-creator's parallel workers share one "
    "project directory, each sees every other worker's identical command file, "
    "and a trigger counts only when the model picks its own copy, so N > 1 "
    "undercounts triggers (ADR 0005, serial trigger eval addendum)")
#: What a trigger-loop usage record says about money: nothing is reported.
TRIGGER_COST_NOTE = ("skill-creator reports no tokens or cost: run_eval.py "
                     "stops reading each stream-json CLI call once it has a "
                     "verdict, and improve_description.py asks for text output")

EXIT_ACCEPTED, EXIT_REJECTED, EXIT_REFUSED = 0, 1, 2

#: Where skill-creator lives when nothing names it: the installed bundle that
#: `claude plugin install skill-creator@claude-plugins-official` records.
SKILL_CREATOR_PLUGIN_KEY = "skill-creator@claude-plugins-official"
SKILL_CREATOR_SUBDIR = Path("skills") / "skill-creator"


class Refusal(Exception):
    """Configuration or measurement problem: exit 2, no decision taken."""


class InvalidProposal(Exception):
    """The proposal broke a constraint; it is recorded and never measured."""


def check_launch_environment() -> None:
    """Refuse credential and provider settings before any model subprocess starts."""
    refused = local_eval.refused_env_names(os.environ)
    if refused:
        raise Refusal("refusing to run with " + ", ".join(refused) +
                      " set; unset them and use the interactive login")
    proxies = local_eval.proxy_userinfo_names(os.environ)
    if proxies:
        raise Refusal("refusing proxy URLs that embed credentials: " +
                      ", ".join(proxies))


@contextmanager
def guarded_environment():
    """A private launcher guards explicit and PATH-based CLI calls alike."""
    check_launch_environment()
    env = local_eval.child_environment(os.environ)
    with tempfile.TemporaryDirectory(prefix="skill-edit-guard-") as directory:
        guard_dir = Path(directory)
        try:
            local_eval.install_guard_launcher(guard_dir, environ=env)
        except local_eval.Refused as exc:
            raise Refusal(str(exc)) from exc
        try:
            yield env
        finally:
            # skill-creator can swallow an unsuccessful CLI launch and emit
            # valid JSON. The launcher's record takes precedence over it.
            refusals = local_eval.guard_refusals(guard_dir)
            if refusals:
                raise Refusal("launch-time settings guard refused: " +
                              str(refusals[0].get("message")))


# ---------------------------------------------------------------------------
# The runner: every step that can spend model budget, and nothing else.
# ---------------------------------------------------------------------------

class Runner:
    """The real thing. Tests substitute a fake with the same three methods."""

    def run_eval(self, argv: list[str]) -> int:
        """Run the guarded local exhibit wrapper; 2 means refusal."""
        return subprocess.run([sys.executable, str(LOCAL_EVAL), *argv],
                              check=False).returncode

    def run_description_loop(self, argv: list[str], *, cwd: Path,
                             skill_creator: Path) -> dict:
        """skill-creator's `scripts/run_loop.py` with `argv`, run from
        `cwd` (its `find_project_root` writes a command file under the
        nearest `.claude/`, so `cwd` must be a scratch project), and its JSON
        stdout parsed. A `skill-creator` link to the plugin is planted in `cwd`."""
        # Run through a link to the plugin planted in the scratch project, so
        # the script path is a literal: the test suite's fork scan
        # (test_every_suite_forking_test_in_this_repo_stands_down_in_a_child)
        # can then prove this spawn is skill-creator and not the suite, which
        # it cannot for `-m` or a computed path. PYTHONPATH carries
        # skill-creator's `scripts` package, so its imports resolve as under
        # its documented `python -m scripts.run_loop`.
        with guarded_environment() as env:
            # Only this trusted plugin's import directory is carried.
            env["PYTHONPATH"] = str(skill_creator)
            link = Path(cwd) / "skill-creator"
            if not link.exists():
                link.symlink_to(skill_creator, target_is_directory=True)
            try:
                proc = subprocess.run([sys.executable, "skill-creator/scripts/run_loop.py", *argv],
                                      cwd=cwd, env=env, capture_output=True, text=True,
                                      check=False)
            except OSError as exc:
                raise Refusal(f"skill-creator run_loop could not run: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            raise Refusal(f"skill-creator run_loop exited {proc.returncode}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise Refusal(f"skill-creator run_loop printed no JSON ({exc})") from exc

    def propose(self, prompt: str, model: str) -> str:
        """One headless call, no tools, prompt on stdin; the reply text."""
        try:
            with guarded_environment() as env:
                cmd = [env["CLAUDE_BIN"], "-p", "--output-format", "json",
                       "--permission-mode", "default", "--tools", "", "--model", model,
                       # The judge's isolation: no settings source, no MCP
                       # connector, no transcript (auto-memory is off in env).
                       *run_eval.judge.JUDGE_ISOLATION_FLAGS]
                proc = subprocess.run(cmd, input=prompt, capture_output=True,
                                      text=True, env=env, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refusal(f"proposal call could not run: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            raise Refusal(f"proposal call exited {proc.returncode}")
        try:
            return normalize_cli_result(json.loads(proc.stdout)).get("result", "")
        except (ValueError, AttributeError) as exc:
            raise Refusal("proposal call printed no JSON result") from exc


# ---------------------------------------------------------------------------
# Fixtures, split, registry
# ---------------------------------------------------------------------------

def skill_fixtures(skill: str) -> list[str]:
    """The nested fixture names under `evals/<skill>/`, sorted."""
    run_eval._validate_skill_name(skill)
    return run_eval.nested_fixture_names(EVALS_DIR / skill)


def split_fixtures(fixtures: list[str], rotation: int,
                   holdout: str | None = None) -> tuple[list[str], str]:
    """(train, validation): validation is `sorted(fixtures)[rotation % n]`."""
    if len(fixtures) < MIN_FIXTURES:
        raise Refusal(f"needs more fixtures: {len(fixtures)} found, "
                      f"{MIN_FIXTURES} required (one held out, the rest trained on)")
    if holdout is not None and holdout not in fixtures:
        raise Refusal(f"--holdout {holdout!r} is not a nested fixture of this skill")
    ordered = sorted(f for f in fixtures if f != holdout)
    validation = ordered[rotation % len(ordered)]
    return [f for f in ordered if f != validation], validation


def default_rotation(records_dir: Path) -> int:
    """How many records this skill already has: each run holds out the next
    fixture, and the same results directory always gives the same answer."""
    return len(list(records_dir.glob("*.json"))) if records_dir.is_dir() else 0


def load_fixtures(skill: str, names: list[str]) -> dict:
    out = {}
    for name in names:
        try:
            fixture = run_eval.load_fixture(EVALS_DIR / skill / name)
        except (OSError, yaml.YAMLError, run_eval.guidance.GuidanceError) as exc:
            raise Refusal(f"fixture {name!r} is unreadable: {exc}") from exc
        if fixture.get("skill") != skill:
            raise Refusal(f"fixture {name!r} names skill {fixture.get('skill')!r}, "
                          f"not {skill!r}")
        out[name] = fixture
    registries = {f.get("registry") for f in out.values()}
    if len(registries) != 1:
        raise Refusal("the fixtures name more than one registry; one scratch "
                      "registry cannot serve them all")
    return out


def resolve_registry(url: str, flags: list[str] | None) -> dict:
    """The registries.yml entry for `url`, with its local checkout."""
    resolved = run_eval.resolve_registries(
        flags, os.environ.get("SKILLS_EVALS_REGISTRIES"), REPO_ROOT,
        os.environ.get("AGENTSKILLS_DIR"))
    entry = run_eval.registry_for_url(resolved, url)
    if not (entry["path"] / ".git").exists():
        raise Refusal(f"registry {entry['name']!r} has no git checkout at "
                      f"{entry['source']}; pass --registry {entry['name']}=PATH")
    return entry


def resolve_ref(checkout: Path, ref: str) -> str:
    # Raw git is fine here (#343): the operator's registry checkout, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    proc = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify",
                           "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Refusal(f"ref {ref!r} does not resolve to a commit in the registry checkout")
    return proc.stdout.strip()


def archive_registry(checkout: Path, sha: str, dest: Path) -> Path:
    """`git archive <sha>` of the checkout, extracted into `dest`. The copy
    carries no `.git`, so there is no remote and no push path in it."""
    # Raw git is fine here (#343): the operator's registry checkout, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    proc = subprocess.run(["git", "-C", str(checkout), "archive", "--format=tar", sha],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise Refusal("git archive of the registry failed")
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def find_skill_dir(registry_root: Path, layout: str, skill: str) -> Path:
    matches = sorted(registry_root.glob(run_eval._skill_md_glob(layout, skill)))
    if len(matches) != 1:
        raise Refusal(f"expected exactly one {skill}/SKILL.md in the registry, "
                      f"found {len(matches)}")
    return matches[0].parent


def skill_provider_plugins(registry_root: Path, skill: str) -> list[str]:
    """Local marketplace plugins providing `skill`, using archived manifests.

    Default skills/ and declared additional skill paths are additive. Remote
    marketplace sources cannot be inspected in this archive and are skipped.
    Every local path is confined to the archive; no installed plugin runs.
    """
    def read_manifest(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refusal(f"unreadable plugin manifest {path.name}") from exc
        if not isinstance(value, dict):
            raise Refusal(f"plugin manifest {path.name} must be an object")
        return value

    def local_path(base: Path, value: str) -> Path:
        if not isinstance(value, str) or (value != "." and not value.startswith("./")):
            raise Refusal("local plugin paths must be . or start with ./")
        path = (base / value).resolve()
        if not path.is_relative_to(registry_root.resolve()):
            raise Refusal("local plugin path escapes the archived registry")
        return path

    marketplace_path = registry_root / ".claude-plugin" / "marketplace.json"
    if not marketplace_path.exists():
        return []  # No marketplace means no fully-qualified installed key.
    marketplace = read_manifest(marketplace_path)
    market_name, plugins = marketplace.get("name"), marketplace.get("plugins")
    if not isinstance(market_name, str) or not market_name or not isinstance(plugins, list):
        raise Refusal("marketplace manifest needs a name and plugins list")
    providers = set()
    for entry in plugins:
        if not isinstance(entry, dict):
            raise Refusal("marketplace plugin entry must be an object")
        source = entry.get("source")
        if isinstance(source, dict):
            continue  # Federated sources are never fetched by this loop.
        root = local_path(registry_root, source)
        manifest_path = root / ".claude-plugin" / "plugin.json"
        manifest = read_manifest(manifest_path) if manifest_path.exists() else {}
        name = entry.get("name")  # Installed keys use the marketplace entry.
        if not isinstance(name, str) or not name:
            raise Refusal("marketplace plugin entry needs a name")
        paths = []
        for declaration in (manifest.get("skills", []), entry.get("skills", [])):
            if isinstance(declaration, str):
                declaration = [declaration]
            if not isinstance(declaration, list):
                raise Refusal("plugin skills must be a path or list of paths")
            for declared in declaration:
                path = local_path(root, declared)
                if not path.is_relative_to(root):
                    raise Refusal("skill path escapes its plugin root")
                paths.append(path)
        defaults = ([] if root == registry_root.resolve() and entry.get("skills")
                    else [root / "skills"])
        for path in defaults + paths:
            candidates = ([path / "SKILL.md"] if (path / "SKILL.md").is_file()
                          else sorted(path.glob("*/SKILL.md")))
            for candidate in candidates:
                if not candidate.resolve().is_relative_to(root):
                    raise Refusal("skill path escapes its plugin root")
                try:
                    provided_name = frontmatter_data(candidate.read_text(encoding="utf-8")).get(
                        "name", candidate.parent.name)
                except (OSError, yaml.YAMLError, InvalidProposal) as exc:
                    raise Refusal("unreadable plugin skill frontmatter") from exc
                if provided_name == skill:
                    providers.add(f"{name}@{market_name}")
    return sorted(providers)


def find_skill_creator(cli_value: Path | None) -> tuple[Path, str | None]:
    """(skill-creator's skill directory, its installed commit or None).

    `--skill-creator`, else `$SKILL_CREATOR_DIR`, else the installed bundle
    recorded in `~/.claude/plugins/installed_plugins.json`."""
    candidates: list[tuple[Path, str | None]] = []
    explicit = cli_value or (Path(os.environ["SKILL_CREATOR_DIR"])
                             if os.environ.get("SKILL_CREATOR_DIR") else None)
    if explicit:
        candidates.append((Path(explicit).expanduser(), None))
    else:
        record = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        try:
            installs = json.loads(record.read_text(encoding="utf-8"))
            for entry in installs.get("plugins", {}).get(SKILL_CREATOR_PLUGIN_KEY, []):
                candidates.append((Path(entry["installPath"]) / SKILL_CREATOR_SUBDIR,
                                   entry.get("gitCommitSha")))
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            pass
    for path, sha in candidates:
        if (path / "scripts" / "run_loop.py").is_file():
            return path.resolve(), sha
    raise Refusal("skill-creator not found: install the plugin "
                  f"({SKILL_CREATOR_PLUGIN_KEY}) or pass --skill-creator DIR "
                  "(the directory holding scripts/run_loop.py)")


def arm_model(fixture: dict, no_judge: bool = True) -> str:
    """The model run_eval itself will pick for this fixture's agent."""
    ns = argparse.Namespace(model=None, roster=None, no_judge=no_judge)
    model, _, problem = run_eval.select_models(fixture, ns)
    if problem:
        raise Refusal(f"{problem}; configure a judge or pass --no-judge")
    mode = (fixture.get("judge") or {}).get("mode", "absolute")
    normalized = mode.strip().casefold() if isinstance(mode, str) else mode
    if not no_judge and normalized not in (None, "", "absolute"):
        raise Refusal(f"cannot drive judge mode {mode!r}; pass --no-judge")
    return model


def proposal_model() -> str:
    """The committed roster's judge: a model that is not an arm."""
    roster, problem = run_eval.read_roster(run_eval._resolve_roster(None))
    if problem:
        raise Refusal(problem)
    _, judge_id, _, _ = run_eval.roster_models(roster)
    if not judge_id:
        raise Refusal("the roster names no judge model for the proposal call")
    return judge_id


# ---------------------------------------------------------------------------
# Trigger half: skill-creator's description loop
# ---------------------------------------------------------------------------

def load_trigger_eval_set(path: Path) -> list[dict]:
    """A reviewed `--trigger-eval-set` file, refused unless it is exactly
    skill-creator's shape: a JSON list of `{query, should_trigger}` objects
    with a non-empty string query and a JSON boolean label. The label is
    never coerced: `"false"` would otherwise read as a should-trigger query."""
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(f"--trigger-eval-set {path.name}: unreadable: "
                      f"{type(exc).__name__}") from exc
    if not isinstance(items, list):
        raise Refusal(f"--trigger-eval-set {path.name}: must be a JSON list")
    for index, item in enumerate(items):
        if (not isinstance(item, dict)
                or set(item) != {"query", "should_trigger"}
                or not isinstance(item["query"], str) or not item["query"].strip()
                or not isinstance(item["should_trigger"], bool)):
            raise Refusal(f"--trigger-eval-set {path.name}: item {index} must be "
                          "exactly {query: non-empty string, should_trigger: boolean}")
    return items


def trigger_eval_set(skill: str, fixtures: dict, train: list[str],
                     validation: str, override: Path | None,
                     holdout: str | None = None) -> tuple[list[dict], str]:
    """skill-creator's `[{query, should_trigger}]` and where it came from.

    Default: the train fixtures' prompts should trigger; every OTHER skill's
    fixture prompts should not. `--trigger-eval-set` replaces it. Either way
    the validation fixture's prompt is dropped, so the description loop never
    trains on the fixture that decides acceptance. A query appears once (the
    first label wins): skill-creator separates train from held-out results by
    query text, so a duplicate would put one query on both sides."""
    held_out = {" ".join(str(fixtures[n].get("prompt", "")).split())
                for n in [validation] + ([holdout] if holdout else [])}
    if override:
        items = load_trigger_eval_set(override)
        source = f"file:{override.name}"
    else:
        items = [{"query": fixtures[name]["prompt"], "should_trigger": True}
                 for name in train]
        for path in sorted(EVALS_DIR.rglob(run_eval.FIXTURE_FILE)):
            other = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if (isinstance(other, dict) and other.get("skill")
                    and other.get("skill") != skill
                    and other.get("subject", "skill") == "skill"
                    and isinstance(other.get("prompt"), str)):
                items.append({"query": other["prompt"], "should_trigger": False})
        source = "fixtures"
    clean, seen = [], set()
    for item in items:
        query = " ".join(str(item.get("query", "")).split())
        if query and query not in held_out and query not in seen:
            seen.add(query)
            clean.append({"query": query, "should_trigger": bool(item.get("should_trigger"))})
    if override and (not any(i["should_trigger"] for i in clean)
                     or all(i["should_trigger"] for i in clean)):
        raise Refusal("the trigger eval set needs at least one should-trigger "
                      "and one should-not-trigger query")
    return clean, source


def trigger_split_counts(eval_set: list[dict],
                         holdout: float = TRIGGER_HOLDOUT) -> dict:
    """How many queries of each class skill-creator's `split_eval_set` puts
    in train and in held-out. Its split is stratified: per class it holds out
    `max(1, int(n * holdout))` of the shuffled queries, so the counts (not
    which queries) follow from the class sizes alone."""
    counts = {"train": {}, "held_out": {}}
    for label, wanted in (("should_trigger", True), ("should_not_trigger", False)):
        n = sum(1 for item in eval_set if item["should_trigger"] is wanted)
        held = min(n, max(1, int(n * holdout)))
        counts["train"][label] = n - held
        counts["held_out"][label] = held
    return counts


def trigger_set_problems(counts: dict) -> list[str]:
    """Each split that lacks a class. A train split without a should-trigger
    query lets the loop "pass" by never triggering, and a held-out split
    without one cannot notice a description that stopped triggering."""
    problems = []
    for split, name in (("train", "train"), ("held_out", "held-out")):
        for label in ("should_trigger", "should_not_trigger"):
            if not counts[split][label]:
                problems.append(f"{name} split has no {label.replace('_', '-')} query")
    return problems


def description_loop_argv(eval_set_path: Path, skill_dir: Path, model: str,
                          out_dir: Path,
                          num_workers: int = DEFAULT_NUM_WORKERS) -> list[str]:
    """skill-creator's documented invocation (SKILL.md "Description
    Optimization", step 3), with its defaults spelled out so the record says
    what ran, its browser report switched off, and one worker instead of its
    default 10 (see DEFAULT_NUM_WORKERS)."""
    return ["--eval-set", str(eval_set_path), "--skill-path", str(skill_dir),
            "--model", model, "--max-iterations", "5", "--runs-per-query", "3",
            "--holdout", str(TRIGGER_HOLDOUT), "--num-workers", str(num_workers),
            "--report", "none", "--results-dir", str(out_dir)]


def trigger_loop_usage(loop: dict, out_dir: Path) -> dict:
    """The trigger loop's model calls, counted from what skill-creator left:
    each history entry's per-query `runs` (one `claude -p` each), one
    proposer call between consecutive iterations, and one more per improve
    log that records a length rewrite. Tokens and cost are not reported."""
    history = loop.get("history")
    eval_calls = None
    if isinstance(history, list) and history:
        eval_calls = 0
        for entry in history:
            entry = entry if isinstance(entry, dict) else {}
            for result in (entry.get("train_results") or []) + (entry.get("test_results") or []):
                runs = result.get("runs") if isinstance(result, dict) else None
                if isinstance(runs, int) and not isinstance(runs, bool):
                    eval_calls += runs
    iterations = loop.get("iterations_run")
    description_calls = (iterations - 1 if isinstance(iterations, int)
                         and not isinstance(iterations, bool) and iterations > 0 else None)
    rewrite_calls = 0
    for log in sorted(out_dir.glob("*/logs/improve_iter_*.json")):
        try:
            entry = json.loads(log.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and "rewrite_prompt" in entry:
            rewrite_calls += 1
    total = (eval_calls + description_calls + rewrite_calls
             if eval_calls is not None and description_calls is not None else None)
    return {"counted_from": "skill-creator loop history and improve logs",
            "eval_calls": eval_calls, "description_calls": description_calls,
            "rewrite_calls": rewrite_calls, "total_calls": total,
            "tokens": None, "cost_usd": None, "cost_note": TRIGGER_COST_NOTE}


# ---------------------------------------------------------------------------
# SKILL.md editing
# ---------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter block including both `---` lines, body)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise InvalidProposal("SKILL.md has no frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[:index + 1]), "".join(lines[index + 1:])
    raise InvalidProposal("SKILL.md frontmatter is not closed")


def frontmatter_data(text: str) -> dict:
    block, _ = split_frontmatter(text)
    inner = block.split("\n", 1)[1].rsplit("---", 1)[0]
    data = yaml.safe_load(inner)
    if not isinstance(data, dict):
        raise InvalidProposal("SKILL.md frontmatter is not a mapping")
    return data


def set_description(text: str, description: str) -> str:
    """`text` with only the frontmatter's `description:` value replaced.
    Written plain when YAML reads it back unchanged, else double-quoted."""
    if not description or len(description) > DESCRIPTION_MAX_CHARS:
        raise InvalidProposal(f"description must be 1..{DESCRIPTION_MAX_CHARS} characters")
    if any(unicodedata.category(char) == "Cc" and char not in "\t\n\r"
           for char in description):
        raise InvalidProposal("description contains a control character")
    block, body = split_frontmatter(text)
    lines = block.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("description:")), None)
    if start is None:
        raise InvalidProposal("SKILL.md frontmatter has no description")
    end = start + 1
    while end < len(lines) - 1 and lines[end][:1] in (" ", "\t"):
        end += 1
    flat = " ".join(description.split())
    rendered = None
    for candidate in (f"description: {flat}\n",
                      f"description: {json.dumps(flat, ensure_ascii=False)}\n"):
        try:
            if yaml.safe_load(candidate) == {"description": flat}:
                rendered = candidate
                break
        except yaml.YAMLError:
            continue
    if rendered is None:
        raise InvalidProposal("description cannot be rendered as YAML")
    new = "".join(lines[:start]) + rendered + "".join(lines[end:]) + body
    before, after = frontmatter_data(text), frontmatter_data(new)
    if after.get("description") != flat or {k: v for k, v in after.items() if k != "description"} \
            != {k: v for k, v in before.items() if k != "description"}:
        raise InvalidProposal("rewriting the description changed another frontmatter key")
    return new


def check_diff_targets(diff: str) -> None:
    """Every file header in `diff` must be `a/SKILL.md` / `b/SKILL.md`."""
    headers = 0
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].split("\t", 1)[0].strip()
            if path not in ("a/SKILL.md", "b/SKILL.md"):
                raise InvalidProposal(f"diff touches {path!r}; only SKILL.md may change")
            headers += 1
        elif line.startswith("diff --git ") and line.split()[2:] != ["a/SKILL.md", "b/SKILL.md"]:
            raise InvalidProposal(f"diff touches {line[11:]!r}; only SKILL.md may change")
        elif line.startswith(("rename ", "copy ", "new file", "deleted file",
                              "Binary files", "GIT binary patch", "old mode", "new mode")):
            raise InvalidProposal(f"diff carries a {line.split()[0]!r} header; "
                                  "only an in-place SKILL.md text edit is allowed")
    if headers == 0:
        raise InvalidProposal("diff has no file headers")


def apply_body_diff(text: str, diff: str, scratch: Path) -> str:
    """`text` (a SKILL.md) with `diff` applied, refusing any change to the
    frontmatter. The diff is applied by `git apply` in an empty scratch
    directory that git cannot mistake for part of a repository."""
    if not diff.strip():
        return text
    check_diff_targets(diff)
    work = scratch / "apply"
    work.mkdir(parents=True)
    (work / "SKILL.md").write_text(text, encoding="utf-8")
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(scratch))
    # Raw git is fine here (#343): a fresh scratch directory with no .git, behind a ceiling, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    proc = subprocess.run(["git", "apply", "-p1", "--recount", "--whitespace=nowarn", "-"],
                          cwd=work, input=diff if diff.endswith("\n") else diff + "\n",
                          capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        # Git's first stderr line says why (e.g. "error: patch failed: SKILL.md:12"), which the record keeps.
        reason = (proc.stderr.strip().splitlines() or [""])[0].strip()[:200]
        raise InvalidProposal("diff does not apply to SKILL.md" + (f": {reason}" if reason else ""))
    unexpected = sorted(p.name for p in work.iterdir() if p.name != "SKILL.md")
    if unexpected:
        raise InvalidProposal(f"diff created {unexpected}; only SKILL.md may change")
    new = (work / "SKILL.md").read_text(encoding="utf-8")
    if split_frontmatter(new)[0] != split_frontmatter(text)[0]:
        raise InvalidProposal("diff touches the frontmatter: the name stays, and "
                              "the description is the trigger half's to change")
    return new


def registry_patch(old: str, new: str, rel_path: str) -> str:
    """A `git apply`-able patch against the registry, rooted at its top."""
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}"))


# ---------------------------------------------------------------------------
# Body half: the proposal call
# ---------------------------------------------------------------------------

PROPOSAL_CONSTRAINTS = """\
Constraints (skills-evals#71):
- Change SKILL.md only. No other file, no new payload file, no rename.
- Leave the frontmatter (the --- block) byte for byte: the name is fixed, and
  the description is the trigger and is tuned separately.
- Fix the failures below by generalizing the instruction that would have
  prevented them; do not quote the fixture, its file names or its answers.
- Prefer the smallest edit that would plausibly fix the failures."""


def run_paths(results: Path, label: str, skill: str, ts: str) -> Path:
    """One empty local_eval destination for this skill, phase and timestamp."""
    return results / "runs" / label / skill / ts


def trial_arm_dir(run_dir: Path, trial: int, skill: str, ts: str,
                  fixture: str) -> Path:
    return run_dir / f"t{trial}" / skill / ts / fixture / ARM


def failure_evidence(run_dir: Path, skill: str, ts: str,
                     fixture: str, trials: int) -> list[dict]:
    """Each trial's failed checks and the tail of its final reply."""
    evidence = []
    for k in range(1, trials + 1):
        trial = trial_arm_dir(run_dir, k, skill, ts, fixture)
        try:
            summary = json.loads((trial / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        failed = [{"id": c.get("id"), "detail": c.get("detail")}
                  for c in summary.get("objective_checks") or [] if not c.get("passed")]
        if not failed:
            continue
        reply = ""
        try:
            raw = json.loads((trial / "transcripts" / "raw.json").read_text(encoding="utf-8"))
            reply = str(raw.get("result", "")) if isinstance(raw, dict) else ""
        except (OSError, ValueError):
            pass
        evidence.append({"trial": k, "failed_checks": failed,
                         "reply_tail": reply[-TRANSCRIPT_EXCERPT_CHARS:]})
    return evidence


def proposal_prompt(skill: str, skill_md: str, purpose: str | None,
                    evidence: dict[str, list[dict]], fixtures: dict) -> str:
    parts = [f"You are improving the agent skill `{skill}`. Its SKILL.md is "
             "measured by fixtures; with the skill installed, an agent still "
             "fails the checks below on the TRAINING fixtures.", "",
             PROPOSAL_CONSTRAINTS, ""]
    if purpose:
        parts += ["<purpose>", purpose.strip(), "</purpose>", ""]
    parts += ["<skill_md>", skill_md, "</skill_md>", ""]
    for name, items in evidence.items():
        parts += [f"<fixture name={json.dumps(name)}>",
                  f"Prompt: {fixtures[name].get('prompt', '').strip()}",
                  json.dumps(items, indent=2), "</fixture>", ""]
    parts += ['Answer with ONE JSON object and nothing else: {"rationale": '
              '"<why this edit fixes the failures>", "unified_diff": "<a unified '
              'diff with headers --- a/SKILL.md and +++ b/SKILL.md, or an empty '
              'string if no body edit is warranted>"}']
    return "\n".join(parts)


def parse_proposal(reply: str) -> dict:
    """`{rationale, unified_diff}` out of the reply: the first JSON object in
    it, tolerating a code fence around it."""
    start = reply.find("{")
    decoder = json.JSONDecoder()
    while start != -1:
        try:
            value, _ = decoder.raw_decode(reply, start)
        except json.JSONDecodeError:
            start = reply.find("{", start + 1)
            continue
        if (isinstance(value, dict) and isinstance(value.get("rationale"), str)
                and isinstance(value.get("unified_diff"), str)):
            return value
        start = reply.find("{", start + 1)
    raise InvalidProposal("proposal is not a {rationale, unified_diff} JSON object")


# ---------------------------------------------------------------------------
# Measurement and decision
# ---------------------------------------------------------------------------

def run_eval_argv(skill: str, registry_name: str, registry_root: Path,
                  results: Path, ts: str, trials: int, no_judge: bool,
                  read_deny: Path | None = None) -> list[str]:
    """local_eval's argv. `read_deny` is this pipeline's results root: the
    candidate's arms must not read the baseline run or the proposed patch."""
    argv = [str(EVALS_DIR / skill), "--arm", ARM, "--trials", str(trials),
            "--registry", f"{registry_name}={registry_root}",
            "--results-dir", str(results), "--timestamp", ts]
    if read_deny is not None:
        argv += ["--read-deny", str(read_deny)]
    return argv + (["--no-judge"] if no_judge else [])


def fixture_metrics(run_dir: Path, skill: str, ts: str,
                    fixture: str, trials: int) -> dict:
    """Reduce every local_eval trial; a missing trial stays inconclusive."""
    summaries = []
    for k in range(1, trials + 1):
        path = trial_arm_dir(run_dir, k, skill, ts, fixture) / "summary.json"
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise ValueError("summary is not an object")
        except (OSError, ValueError):
            summary = {"error": {"type": "missing_summary"}}
        if summary.get("error") and not isinstance(summary["error"], dict):
            summary["error"] = {"type": "trial_error"}
        summaries.append(summary)
    stats = run_eval.aggregate_trials(summaries)
    objective = stats["aggregate"]["objective"] or {}
    judge_stats = stats["aggregate"]["judge"] or {}
    judge = judge_stats.get("overall") or {}
    error = next((s["error"].get("type") or "trial_error"
                  for s in summaries if s.get("error")), None)
    if not error and judge_stats.get("errors"):
        error = "judge_error"
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None  # Injected offline runners may only write trial summaries.
    trial_entries = manifest.get("trials") if isinstance(manifest, dict) else None
    if not error and isinstance(trial_entries, list) and any(
            not isinstance(item, dict) or item.get("exit_code") != 0
            for item in trial_entries):
        error = "trial_exit"
    return {"error": error,
            "passed": objective.get("passed"), "total": objective.get("total"),
            "judge_mean": judge.get("mean"), "n": stats.get("n"),
            "tokens": mean_total_tokens(summaries),
            "tokens_basis": TOKENS_BASIS,
            "main_loop_tokens": mean_tokens(stats["aggregate"]["efficiency"]),
            "model_tokens": model_mean_tokens(stats["aggregate"]["model_tokens"]),
            "cross_model": stats["aggregate"]["cross_model"]}


def model_mean_tokens(per_model: dict) -> dict:
    """Per model id, the mean per trial of each of its four token counts
    (`run_eval.MODEL_TOKEN_FIELDS`), from an `aggregate.model_tokens` block.
    A count is None when any trial lacks it, as in `mean_tokens`. Recorded
    beside `tokens`; no acceptance rule reads it."""
    out = {}
    for model, counts in per_model.items():
        out[model] = {}
        for name, _ in run_eval.MODEL_TOKEN_FIELDS:
            block = counts.get(name) or {}
            out[model][name] = (block["mean"] if block.get("n")
                                and not block.get("n_missing")
                                and valid_tokens(block.get("mean")) else None)
    return out


def mean_total_tokens(summaries: list[dict]) -> float | None:
    """The mean per trial of `run_eval.model_usage_total`: every model's four
    counts from the trial's `model_tokens`. None when any trial's accounting
    is missing or incomplete (an entry dropped, the models truncated, a count
    missing or invalid): the same "missing token data" `decide` already
    treats as inconclusive, never a partial sum."""
    totals = []
    for summary in summaries:
        block = summary.get("cross_model")
        complete = isinstance(block, dict) and block.get("complete") is True
        total = run_eval.model_usage_total(summary.get("model_tokens"), complete)
        if total is None:
            return None
        totals.append(total)
    return sum(totals) / len(totals) if totals else None


def tokens_basis(metrics) -> str:
    """One fixture's `tokens_basis`; metrics written before the field
    measured the main loop's `usage` alone."""
    basis = metrics.get("tokens_basis") if isinstance(metrics, dict) else None
    return basis if isinstance(basis, str) else TOKENS_BASIS_LEGACY


def valid_tokens(value) -> bool:
    """A usable token figure: a finite number, never negative."""
    return run_eval._is_number(value) and value >= 0


def mean_tokens(efficiency: dict) -> float | None:
    """Mean total tokens of one trial (the four `TOKEN_USAGE_METRICS`
    summed), from an `aggregate.efficiency` block. None when any trial lacks
    any of the four: a partial sum is a silent undercount, and a missing
    figure is never zero."""
    total = 0
    for name in TOKEN_USAGE_METRICS:
        block = efficiency.get(name) or {}
        mean = block.get("mean")
        if (not block.get("n") or block.get("n_missing")
                or not valid_tokens(mean)):
            return None
        total += block["mean"]
    return total


def group_metrics(per_fixture: dict, names: list[str]) -> dict:
    rows = [per_fixture.get(n, {"error": "missing fixture"}) for n in names]
    errors = [n for n, r in zip(names, rows) if r.get("error") or not r.get("total")]
    passed = sum(r.get("passed") or 0 for r in rows)
    total = sum(r.get("total") or 0 for r in rows)
    judged = [r["judge_mean"] for r in rows if run_eval._is_number(r.get("judge_mean"))]
    return {"errors": errors, "passed": passed, "total": total,
            "pass_rate": passed / total if total else None,
            "judge_mean": (sum(judged) / len(judged)
                           if judged and len(judged) == len(rows) else None)}


def no_headroom(b_train: dict, min_gain: float = DEFAULT_MIN_GAIN) -> str | None:
    """Why no candidate could be accepted on this baseline, or None. Pure.

    `decide` needs a train objective gain of `min_gain`, or at an equal
    objective a judge gain of ten times it. A baseline whose objective leaves
    less than `min_gain` to gain, and whose judge mean leaves less than that
    on its ten-point scale, cannot be beaten, so measuring a proposal is
    wasted. Errored or unscored train fixtures are left to `decide`'s
    inconclusive path."""
    rate = b_train["pass_rate"]
    if b_train["errors"] or rate is None:
        return None

    def below(gap: float) -> bool:
        return gap < min_gain and not math.isclose(
            gap, min_gain, rel_tol=0, abs_tol=1e-12)

    judge = b_train["judge_mean"]
    if below(1 - rate) and (judge is None or below((10 - judge) / 10)):
        return (f"no train headroom: baseline objective {rate:.3f} leaves less "
                f"than the minimum gain {min_gain:g}, and the judge cannot "
                "make it up; add harder fixtures instead")
    return None


def decide(baseline: dict, candidate: dict, train: list[str],
           validation: str, min_gain: float = DEFAULT_MIN_GAIN,
           holdout: str | None = None,
           judge_required: bool | set[str] = False) -> tuple[bool, list[str]]:
    """(accepted, reasons). Pure: reads the two per-fixture metric maps.

    ADR 0005 amendment 2: quality (objective and judge, below) is judged
    beside tokens. Token data missing for any measured fixture on either side
    is inconclusive, never a pass. A candidate that costs more tokens than the
    baseline (`TOKEN_INCREASE_TOLERANCE`) without a quality gain is not
    accepted; quality gain here is the train gain of `min_gain`, which
    acceptance has always required, so a rise in tokens is accepted only
    alongside one and is recorded in the reasons. Tokens measured on
    different bases (`tokens_basis`) on the two sides are inconclusive."""
    reasons = []
    b_train, c_train = group_metrics(baseline, train), group_metrics(candidate, train)
    b_val, c_val = group_metrics(baseline, [validation]), group_metrics(candidate, [validation])
    errored = sorted(set(b_train["errors"] + c_train["errors"]
                         + b_val["errors"] + c_val["errors"]))
    if errored:
        return False, [f"inconclusive: errored or unscored fixtures {errored}"]
    measured = train + [validation] + ([holdout] if holdout else [])
    required = (set(measured) if judge_required is True
                else set(judge_required) if judge_required else set())
    required.update(n for n in measured
                    if run_eval._is_number(baseline.get(n, {}).get("judge_mean")))
    missing_judge = [n for n in measured if n in required and any(
        not run_eval._is_number(metrics.get(n, {}).get("judge_mean"))
        for metrics in (baseline, candidate))]
    if missing_judge:
        return False, [f"inconclusive: missing expected judge scores {missing_judge}"]

    bases = sorted({tokens_basis(metrics.get(n)) for metrics in (baseline, candidate)
                    for n in measured})
    if len(bases) > 1:
        return False, [f"inconclusive: token bases differ {bases}; baseline "
                       "and candidate must be measured the same way"]
    unknown = [f"{side} {n}" for side, metrics in (("baseline", baseline),
                                                   ("candidate", candidate))
               for n in measured
               if not valid_tokens(metrics.get(n, {}).get("tokens"))]
    if unknown:
        return False, [f"inconclusive: missing token data for {unknown}"]
    b_tokens = sum(baseline[n]["tokens"] for n in measured)
    c_tokens = sum(candidate[n]["tokens"] for n in measured)
    limit = b_tokens * (1 + TOKEN_INCREASE_TOLERANCE)
    costs_more = c_tokens > limit and not math.isclose(c_tokens, limit, rel_tol=1e-9)

    held = c_val["pass_rate"] >= b_val["pass_rate"]
    if not held:
        reasons.append(f"validation objective fell {b_val['pass_rate']:.3f} -> "
                       f"{c_val['pass_rate']:.3f}")
    if b_val["judge_mean"] is not None and c_val["judge_mean"] is not None \
            and c_val["judge_mean"] < b_val["judge_mean"] - JUDGE_TOLERANCE:
        held = False
        reasons.append(f"validation judge fell more than {JUDGE_TOLERANCE}: "
                       f"{b_val['judge_mean']:.2f} -> {c_val['judge_mean']:.2f}")

    if holdout:
        b_fixed = group_metrics(baseline, [holdout])
        c_fixed = group_metrics(candidate, [holdout])
        if b_fixed["errors"] or c_fixed["errors"]:
            return False, [f"inconclusive: errored or unscored holdout {holdout!r}"]
        if c_fixed["pass_rate"] < b_fixed["pass_rate"]:
            held = False
            reasons.append("holdout objective regressed")
        if b_fixed["judge_mean"] is not None and c_fixed["judge_mean"] is not None \
                and c_fixed["judge_mean"] < b_fixed["judge_mean"]:
            held = False
            reasons.append("holdout judge regressed (zero tolerance)")

    def meets_gain(gain: float) -> bool:
        return gain > 0 and (gain >= min_gain or math.isclose(
            gain, min_gain, rel_tol=0, abs_tol=1e-12))

    improved = meets_gain(c_train["pass_rate"] - b_train["pass_rate"])
    if c_train["pass_rate"] == b_train["pass_rate"] \
            and b_train["judge_mean"] is not None and c_train["judge_mean"] is not None:
        improved = meets_gain((c_train["judge_mean"] - b_train["judge_mean"]) / 10)
    if not improved:
        reasons.append(f"train did not meet minimum gain {min_gain:g}: "
                       f"objective {b_train['pass_rate']:.3f} -> "
                       f"{c_train['pass_rate']:.3f}")
    reasons.append(f"tokens {b_tokens:g} -> {c_tokens:g} per trial over "
                   f"{', '.join(measured)}")
    if costs_more and not improved:
        reasons.append("candidate costs more tokens without a quality gain")
    elif costs_more and held:
        reasons.append("candidate costs more tokens; accepted only because "
                       "train gained at least the minimum")
    if held and improved:
        reasons.append("validation held and train improved")
    return held and improved, reasons


def table(baseline: dict, candidate: dict | None, train: list[str],
          validation: str, holdout: str | None = None) -> str:
    def cell(m: dict | None) -> tuple[str, str]:
        if not m:
            return "-", "-"
        if m.get("error") or not m.get("total"):
            return f"error ({m.get('error')})", "-"
        judge = m.get("judge_mean")
        return (f"{m['passed']}/{m['total']}",
                f"{judge:.2f}" if run_eval._is_number(judge) else "-")
    lines = ["| Fixture | Split | Baseline objective | Candidate objective "
             "| Baseline judge | Candidate judge |", "|---|---|---|---|---|---|"]
    for name in train + [validation] + ([holdout] if holdout else []):
        b_obj, b_judge = cell(baseline.get(name))
        c_obj, c_judge = cell((candidate or {}).get(name))
        split = "holdout" if name == holdout else "validation" if name == validation else "train"
        lines.append(f"| {name} | {split} | {b_obj} | {c_obj} | {b_judge} | {c_judge} |")
    return "\n".join(lines)


def pr_body(record: dict, rows: str) -> str:
    d, b = record["description_half"], record["body_half"]
    lines = [f"## Proposed `{record['skill']}` SKILL.md edit (validation-gated)", "",
             "Produced by skills-evals `scripts/propose_skill_edit.py` "
             "(skills-evals ADR 0005). "
             "A local exhibit (skills-evals ADR 0002, decision 4): measured under "
             "the operator's own login, not badge input. Review before merging; "
             "nothing here merges itself.", "",
             f"- Registry: `{record['registry']['name']}` at `{record['registry']['sha']}`",
             f"- Split: train {', '.join(record['split']['train'])}; "
             f"validation {record['split']['validation']} "
             f"(rotation {record['split']['rotation']})",
             f"- Fixed holdout: {record['split'].get('holdout') or 'none'}; "
             f"minimum train gain: {record['min_gain']:g} (normalized 0..1)",
             f"- Trials per fixture: {record['trials']}, arm `{ARM}`, "
             f"agent model `{record['models']['arm']}`", "",
             "### Train / validation", "", rows, "",
             f"Decision: {'; '.join(record['reasons'])}.", ""]
    if d.get("changed"):
        lines += ["### Description (skill-creator `scripts/run_loop.py`)", "",
                  f"Held-out trigger score {d.get('best_test_score')}, train "
                  f"{d.get('best_train_score')}, query set from {d.get('eval_set_source')}.", "",
                  f"Before: {d['original']}", "", f"After: {d['best']}", ""]
    if b.get("unified_diff"):
        lines += ["### Body edit", "", b.get("rationale", ""), ""]
    lines += ["The patch is `" + record["files"]["patch"] + "`; apply it with "
              "`git apply` at the registry root."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def default_results_dir() -> Path:
    """Outside the repository: `$XDG_STATE_HOME/skills-evals`, else
    `~/.local/state/skills-evals`."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "skills-evals"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("skill")
    parser.add_argument("--registry", action="append", default=None,
                        help="registry checkout, NAME=PATH (as run_eval.py)")
    parser.add_argument("--ref", default="HEAD",
                        help="registry commit to archive (default HEAD)")
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--rotation", type=int, default=None,
                        help="validation fixture index (default: records so far)")
    parser.add_argument("--min-gain", type=float, default=DEFAULT_MIN_GAIN,
                        help="minimum train gain on 0..1 scale (default .10; "
                             "objective rate or judge mean / 10 when objective is flat)")
    parser.add_argument("--holdout", default=None,
                        help="fixed nested fixture, never trained on; must not regress")
    parser.add_argument("--trigger-eval-set", type=Path, default=None,
                        help="skill-creator query set JSON (default: from fixtures)")
    parser.add_argument("--skill-creator", type=Path, default=None,
                        help="skill-creator's skill directory (holds scripts/run_loop.py)")
    parser.add_argument("--num-workers", type=int, default=DEFAULT_NUM_WORKERS,
                        help="parallel CLI calls in skill-creator's trigger eval; "
                             "only 1 is accepted (parallel workers undercount triggers)")
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan; no model call, no write")
    args = parser.parse_args(argv)
    if not 1 <= args.trials <= run_eval.MAX_TRIALS:
        parser.error(f"--trials must be 1..{run_eval.MAX_TRIALS}")
    if args.rotation is not None and args.rotation < 0:
        parser.error("--rotation must be >= 0")
    if args.num_workers != 1:
        parser.error(NUM_WORKERS_REFUSAL)
    if not math.isfinite(args.min_gain) or not 0 < args.min_gain <= 1:
        parser.error("--min-gain must be finite and greater than 0, at most 1")
    return args


def plan(args: argparse.Namespace) -> dict:
    """Everything decided before any model call. Reads, never writes."""
    skill = args.skill
    try:
        names = skill_fixtures(skill)
    except ValueError as exc:
        raise Refusal(str(exc)) from exc
    results = (args.results_dir or default_results_dir()).expanduser().resolve()
    records_dir = results / "improvements" / skill
    rotation = args.rotation if args.rotation is not None else default_rotation(records_dir)
    train, validation = split_fixtures(names, rotation, args.holdout)
    fixtures = load_fixtures(skill, names)
    if args.holdout:
        fixed_prompt = " ".join(str(fixtures[args.holdout].get("prompt", "")).split())
        if any(" ".join(str(fixtures[n].get("prompt", "")).split()) == fixed_prompt
               for n in train):
            raise Refusal("fixed --holdout needs a prompt distinct from every train fixture")
    models = {name: arm_model(fixture, args.no_judge) for name, fixture in fixtures.items()}
    url = next(iter(fixtures.values())).get("registry")
    try:
        registry = resolve_registry(url, args.registry)
    except ValueError as exc:
        raise Refusal(str(exc)) from exc
    sha = resolve_ref(registry["path"], args.ref)
    skill_creator, sc_sha = find_skill_creator(args.skill_creator)
    eval_set, source = trigger_eval_set(skill, fixtures, train, validation,
                                        args.trigger_eval_set, args.holdout)
    split_counts = trigger_split_counts(eval_set)
    return {"skill": skill, "results": results, "records_dir": records_dir,
            "rotation": rotation, "train": train, "validation": validation,
            "holdout": args.holdout, "min_gain": args.min_gain,
            "fixtures": fixtures, "registry": registry, "sha": sha,
            "skill_creator": skill_creator, "skill_creator_sha": sc_sha,
            "eval_set": eval_set, "eval_set_source": source,
            "trigger_split": split_counts,
            "trigger_problems": trigger_set_problems(split_counts),
            "arm_model": models[train[0]],
            "proposal_model": proposal_model()}


def print_plan(p: dict, args: argparse.Namespace) -> None:
    n = len(p["fixtures"])
    per_run = n * args.trials * (1 if args.no_judge else 2)
    print(json.dumps({
        "skill": p["skill"], "rotation": p["rotation"], "train": p["train"],
        "validation": p["validation"],
        "holdout": p["holdout"], "min_gain": p["min_gain"],
        "registry": {"name": p["registry"]["name"], "sha": p["sha"]},
        "skill_creator": str(p["skill_creator"]),
        "trigger_eval_set": {"source": p["eval_set_source"], "size": len(p["eval_set"]),
                             "split": p["trigger_split"],
                             "problems": p["trigger_problems"]},
        "num_workers": args.num_workers,
        "models": {"arm": p["arm_model"], "proposal": p["proposal_model"]},
        "results_dir": str(p["results"]),
        "model_calls": {
            "run_eval_baseline": per_run, "run_eval_candidate": per_run,
            # 5 iterations x 3 runs per query, plus up to two proposer calls
            # (one more when a description overruns 1024 characters) for
            # each of the 4 improvements between them.
            "skill_creator_loop_at_most": 5 * 3 * len(p["eval_set"]) + 2 * 4,
            "proposal": 1},
    }, indent=2))


def improve(args: argparse.Namespace, runner: Runner, now: datetime) -> int:
    p = plan(args)
    unusable = trigger_set_unusable(p)
    if args.dry_run:
        print_plan(p, args)
        if unusable:
            print(f"trigger-set-unusable: {unusable}", file=sys.stderr)
            return EXIT_REFUSED
        return EXIT_ACCEPTED
    skill, results, registry = p["skill"], p["results"], p["registry"]
    ts = now.astimezone(timezone.utc).strftime(run_eval.TIMESTAMP_FORMAT)
    # Improvement records persist here; each local_eval call receives its
    # own absent or empty directory beneath runs/.
    try:
        for path in (results, p["records_dir"],
                     results / "trigger" / skill):
            local_eval.check_results_dir(path, require_empty=False)
        local_eval.check_results_dir(results / "trigger" / skill / ts)
    except local_eval.Refused as exc:
        raise Refusal(str(exc)) from exc
    names = p["train"] + [p["validation"]] + ([p["holdout"]] if p["holdout"] else [])
    p["records_dir"].mkdir(parents=True, exist_ok=True)
    stem = p["records_dir"] / ts
    if stem.with_suffix(".json").exists():
        raise Refusal(f"a record for {ts} already exists")

    scratch = Path(tempfile.mkdtemp(prefix="propose-skill-edit-"))
    try:
        base_root = archive_registry(registry["path"], p["sha"], scratch / "baseline")
        cand_root = archive_registry(registry["path"], p["sha"], scratch / "candidate")
        base_skill = find_skill_dir(base_root, registry["layout"], skill)
        cand_skill = find_skill_dir(cand_root, registry["layout"], skill)
        rel_path = base_skill.relative_to(base_root).as_posix() + "/SKILL.md"
        original = (base_skill / "SKILL.md").read_text(encoding="utf-8")
        disabled_plugins = skill_provider_plugins(base_root, skill)

        record = {
            "schema": 1, "skill": skill, "timestamp": ts,
            "local_exhibit": "skills-evals ADR 0002 decision 4: operator login, not badge input",
            "registry": {"name": registry["name"], "url": registry["url"],
                         "sha": p["sha"], "skill_md": rel_path},
            "split": {"rotation": p["rotation"], "train": p["train"],
                      "validation": p["validation"]},
            "trials": args.trials, "no_judge": args.no_judge,
            "min_gain": args.min_gain,
            "models": {"arm": p["arm_model"], "proposal": p["proposal_model"]},
            "baseline": None, "candidate": None, "tokens_basis": TOKENS_BASIS,
            "runs": {"baseline": str(run_paths(results, "baseline", skill, ts))},
            "files": {},
        }
        if p["holdout"]:
            record["split"]["holdout"] = p["holdout"]
        record["trigger_set"] = {"source": p["eval_set_source"],
                                 "size": len(p["eval_set"]),
                                 "split": p["trigger_split"],
                                 "problems": p["trigger_problems"]}
        if unusable:
            record.update(status="trigger-set-unusable", phase="trigger-set",
                          exit_code=EXIT_REFUSED, reasons=[unusable])
            write_record(stem, record)
            print(f"refused: trigger-set-unusable: {unusable}; record: "
                  f"{stem.with_suffix('.json')}", file=sys.stderr)
            return EXIT_REFUSED
        if p["trigger_problems"]:
            print("warning: reviewed trigger eval set: " + "; ".join(p["trigger_problems"]),
                  file=sys.stderr)

        try:
            check_launch_environment()
        except Refusal as exc:
            return record_refusal(stem, record, "preflight", exc)

        rc = runner.run_eval(run_eval_argv(skill, registry["name"], base_root,
                                           run_paths(results, "baseline", skill, ts),
                                           ts, args.trials, args.no_judge,
                                           read_deny=results))
        baseline = {n: fixture_metrics(run_paths(results, "baseline", skill, ts),
                                       skill, ts, n, args.trials) for n in names}
        record["baseline"] = baseline
        if rc not in (EXIT_ACCEPTED, EXIT_REJECTED):
            record.update(status="refused", phase="baseline", exit_code=rc,
                          reasons=[f"local_eval exited {rc} during baseline"])
            write_record(stem, record)
            print(f"refused: local_eval baseline exited {rc}; record: "
                  f"{stem.with_suffix('.json')}", file=sys.stderr)
            return EXIT_REFUSED

        # Stop before any trigger, proposal or candidate call when the
        # baseline leaves no room for the gain `decide` requires.
        stuck = no_headroom(group_metrics(baseline, p["train"]), args.min_gain)
        if stuck:
            record.update(status="rejected", phase="baseline", reasons=[stuck])
            write_record(stem, record)
            print(f"rejected before proposal: {stuck}")
            return EXIT_REJECTED

        # Trigger half: skill-creator's loop, untouched.
        trigger_dir = results / "trigger" / skill / ts
        trigger_dir.mkdir(parents=True, exist_ok=True)
        eval_set_path = trigger_dir / "eval-set.json"
        eval_set_path.write_text(json.dumps(p["eval_set"], indent=2), encoding="utf-8")
        project = scratch / "trigger-project"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "settings.json").write_text(json.dumps({
            "enabledPlugins": {name: False for name in disabled_plugins}}, indent=2),
            encoding="utf-8")
        try:
            loop = runner.run_description_loop(
                description_loop_argv(eval_set_path, base_skill, p["arm_model"],
                                      trigger_dir, args.num_workers),
                cwd=project, skill_creator=p["skill_creator"])
        except Refusal as exc:
            return record_refusal(stem, record, "trigger", exc)
        (trigger_dir / "loop.json").write_text(json.dumps(loop, indent=2), encoding="utf-8")
        original_description = frontmatter_data(original).get("description", "")
        raw_best = str(loop.get("best_description") or "")
        best = " ".join(raw_best.split())
        record["description_half"] = {
            "from": "skill-creator scripts/run_loop.py",
            "skill_creator": {"path": str(p["skill_creator"]),
                              "commit": p["skill_creator_sha"]},
            "eval_set_source": p["eval_set_source"], "eval_set_size": len(p["eval_set"]),
            "disabled_plugins": disabled_plugins,
            "original": original_description, "best": best,
            "best_test_score": loop.get("best_test_score"),
            "best_train_score": loop.get("best_train_score"),
            "iterations": loop.get("iterations_run"),
            "num_workers": args.num_workers,
            "usage": trigger_loop_usage(loop, trigger_dir),
            "changed": bool(best) and best != " ".join(str(original_description).split()),
            "loop_output": str(trigger_dir / "loop.json")}

        # Body half: one proposal call, validated before anything is measured.
        evidence = {n: failure_evidence(run_paths(results, "baseline", skill, ts),
                                        skill, ts, n, args.trials)
                    for n in p["train"]}
        evidence = {n: e for n, e in evidence.items() if e}
        record["body_half"] = {"model": p["proposal_model"], "rationale": None,
                               "unified_diff": "", "error": None}
        candidate_text = original
        try:
            if evidence:
                purpose_path = base_skill / "PURPOSE.md"
                purpose = (purpose_path.read_text(encoding="utf-8")
                           if purpose_path.is_file() else None)
                proposal = parse_proposal(runner.propose(
                    proposal_prompt(skill, original, purpose, evidence, p["fixtures"]),
                    p["proposal_model"]))
                record["body_half"].update(rationale=proposal["rationale"],
                                           unified_diff=proposal["unified_diff"])
                candidate_text = apply_body_diff(original, proposal["unified_diff"], scratch)
            else:
                record["body_half"]["error"] = "no failed train check to learn from"
            if record["description_half"]["changed"]:
                candidate_text = set_description(candidate_text, raw_best)
        except InvalidProposal as exc:
            record.update(status="invalid-proposal", reasons=[str(exc)])
            write_record(stem, record)
            print(f"rejected before measurement: {exc}")
            return EXIT_REJECTED
        except Refusal as exc:
            return record_refusal(stem, record, "proposal", exc)

        if candidate_text == original:
            record.update(status="no-candidate",
                          reasons=["neither half produced a change"])
            write_record(stem, record)
            print("no candidate: neither half produced a change")
            return EXIT_REJECTED

        patch = registry_patch(original, candidate_text, rel_path)
        stem.with_suffix(".patch").write_text(patch, encoding="utf-8")
        record["files"]["patch"] = str(stem.with_suffix(".patch"))
        (cand_skill / "SKILL.md").write_text(candidate_text, encoding="utf-8")

        record["runs"]["candidate"] = str(run_paths(results, "candidate", skill, ts))
        rc = runner.run_eval(run_eval_argv(skill, registry["name"], cand_root,
                                           run_paths(results, "candidate", skill, ts),
                                           ts, args.trials, args.no_judge,
                                           read_deny=results))
        if rc not in (EXIT_ACCEPTED, EXIT_REJECTED):
            record.update(status="refused", phase="candidate", exit_code=rc,
                          reasons=[f"local_eval exited {rc} during candidate"])
            write_record(stem, record)
            print(f"refused: local_eval candidate exited {rc}; record: "
                  f"{stem.with_suffix('.json')}", file=sys.stderr)
            return EXIT_REFUSED
        candidate = {n: fixture_metrics(run_paths(results, "candidate", skill, ts),
                                        skill, ts, n, args.trials) for n in names}
        record["candidate"] = candidate

        accepted, reasons = decide(baseline, candidate, p["train"], p["validation"],
                                    args.min_gain, p["holdout"],
                                    {n for n in names if not args.no_judge
                                     and p["fixtures"][n].get("judge_rubric")})
        record.update(status="accepted" if accepted else "rejected", reasons=reasons)
        rows = table(baseline, candidate, p["train"], p["validation"], p["holdout"])
        record["table"] = rows
        if accepted:
            body_path = stem.with_name(stem.name + ".pr-body.md")
            record["files"]["pr_body"] = str(body_path)
            body_path.write_text(pr_body(record, rows), encoding="utf-8")
        write_record(stem, record)
        print(rows)
        print(f"{record['status']}: {'; '.join(reasons)}")
        print(f"record: {stem.with_suffix('.json')}")
        return EXIT_ACCEPTED if accepted else EXIT_REJECTED
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def trigger_set_unusable(p: dict) -> str | None:
    """Why a fixture-derived trigger set must not run, or None. A reviewed
    `--trigger-eval-set` is the operator's call and only warns."""
    if p["eval_set_source"] != "fixtures" or not p["trigger_problems"]:
        return None
    return ("; ".join(p["trigger_problems"]) + f" (skill-creator --holdout "
            f"{TRIGGER_HOLDOUT} of {len(p['eval_set'])} fixture-derived "
            "queries); the trigger loop would pass without "
            "measuring triggering. Pass a reviewed --trigger-eval-set with at "
            "least two distinct should-trigger and two should-not-trigger queries")


def record_refusal(stem: Path, record: dict, phase: str, exc: Refusal) -> int:
    record.update(status="refused", phase=phase, exit_code=EXIT_REFUSED,
                  reasons=[str(exc)])
    write_record(stem, record)
    print(f"refused during {phase}: {exc}; record: {stem.with_suffix('.json')}",
          file=sys.stderr)
    return EXIT_REFUSED


def write_record(stem: Path, record: dict) -> None:
    record["decision"] = {"invalid-proposal": "rejected",
                          "trigger-set-unusable": "refused"}.get(
                              record["status"], record["status"])
    record["files"]["record"] = str(stem.with_suffix(".json"))
    stem.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n",
                                         encoding="utf-8")


def main(argv: list[str] | None = None, runner: Runner | None = None,
         now: datetime | None = None) -> int:
    args = parse_args(argv)
    try:
        return improve(args, runner or Runner(), now or datetime.now(timezone.utc))
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
