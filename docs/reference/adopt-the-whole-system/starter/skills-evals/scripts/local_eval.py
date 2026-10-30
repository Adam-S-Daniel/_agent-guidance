#!/usr/bin/env python3
"""Run one skill fixture N times on this workstation: a LOCAL EXHIBIT.

ADR 0002 (docs/decisions/0002-runs-bill-the-api-org-not-the-subscription.md)
decision 4 keeps ad-hoc runs available "locally by the account holder under
their own `/login`", with the results "a local exhibit, not badge input". This
script is the safe way to make one. It drives the unmodified single-run
interface, `harness/run_eval.py <fixture> --arm <arm>`, once per trial; it does
not change how a run is scored.

Usage:

    python3 scripts/local_eval.py evals/<skill> --results-dir ~/evals-local/<name>
        [--trials 3] [--arm both|with_skill|without_skill] [--no-judge]
        [--fixture NAME] [--registry NAME=PATH ...] [--read-deny DIR ...]

`evals/<skill>` is a flat fixture (`fixture.yaml` inside), a nested fixture
named by its own directory (`evals/<skill>/<name>`), or a skill directory of
nested fixtures (all of them run, or the one `--fixture NAME` selects).

What it does, in order, and what it refuses (exit 2, nothing run):

1. Refuses when the environment carries ANY provider-selection or credential
   variable, by this rule (case-insensitive, `refused_env_names`): a name that
   begins `ANTHROPIC_`, `CLAUDE_CODE_USE_`, `AWS_`, `GOOGLE_`, `GCLOUD_`,
   `CLOUDSDK_` or `AZURE_`; is `CLAUDE_CODE_OAUTH_TOKEN` or
   `CLAUDE_CONFIG_DIR`; or contains `API_KEY`, `AUTH_TOKEN`, `ACCESS_KEY`,
   `SECRET` or `BEARER` (the one tool toggle in that family,
   `CLAUDE_CODE_USE_POWERSHELL_TOOL`, is not a provider and passes). Empty
   counts. The message names the variable, never
   its value; `env -u NAME` clears it. A run under `/login` needs none of them,
   and each one either bills a dollar or cloud account, re-routes the CLI to
   another provider, or puts a credential in reach of an arm.
   A proxy variable (`HTTP_PROXY`, `HTTPS_PROXY`, either case) whose URL
   embeds userinfo (`scheme://user:pass@host`) is refused too, by name.
   Whatever passes is then NOT inherited wholesale: main() replaces the
   process environment with an allow-list (`child_environment`) before
   anything is launched, so the version call, the probe, every arm and the
   judge (which `harness/scorers/judge.py` starts with no `env=`) see only
   that. The allow-list, in full: PATH, HOME, LANG, LANGUAGE, LC_*, TERM,
   TMPDIR, TZ, USER, LOGNAME, SHELL, HTTP_PROXY, HTTPS_PROXY, NO_PROXY (and
   their lowercase forms), NODE_EXTRA_CA_CERTS, SSL_CERT_FILE, SSL_CERT_DIR,
   CLAUDE_BIN, SKILLS_EVALS_REGISTRIES, AGENTSKILLS_DIR. No XDG_* variable
   passes: `$XDG_CONFIG_HOME/claude/settings.json` could carry a credential
   source this wrapper does not read, so children use the defaults under HOME.
   One variable is SET rather than allow-listed:
   `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (`guidance.CLI_FORCED_ENV`), so no
   trial or judge writes auto-memory into the real HOME.
1b. Reads the user, checkout and managed settings files: `~/.claude/settings
   .json` and `settings.local.json`, this checkout's `.claude/settings.json`
   and `settings.local.json`, and the managed settings (`/etc/claude-code/
   managed-settings.json` and `managed-settings.d/*.json`, plus the macOS and
   Windows locations). Refuses when one carries `apiKeyHelper`,
   `awsAuthRefresh` or `awsCredentialExport`, or an `env` object naming a
   variable rule 1 refuses, or when one cannot be read or parsed as a JSON
   object. It names the file and the key, never a value. The same check runs
   on every `**/.claude/settings*.json` inside each selected fixture's `seed/`
   (the arms load those as project settings), following symlinks as the
   workspace copy does, and on each registry checkout's root
   `.claude/settings*.json`. This early check reads SOURCE files only.
   The judge itself now runs with `--setting-sources ""` (plus
   `--strict-mcp-config` and `--no-session-persistence`), so it loads none
   of these. The check still runs because other children do load them: the
   arms load project settings, skill-creator's own `claude -p` calls
   (propose_skill_edit) get `--setting-sources project` from the guard
   launcher only when they name none, and managed settings apply to every
   launch whatever its flags. A credential source in any of them is refused
   before anything starts.
1c. The launch-time guard, which checks what the CLI will actually read. A
   source check cannot see a symlink resolved later or a fixture `setup:`
   command that writes `.claude/settings.json` into the workspace. So main()
   writes a small launcher (`scripts/local_eval_guard.py`, `launcher_source`)
   as `claude` in a private 0700 temp directory, points CLAUDE_BIN at it for
   every child and puts that directory first on PATH. Every child kind starts
   the CLI through it: the version call (run_eval.claude_version), the probe
   (init_probe), each arm (run_eval.run_agent) and the judge (judge.py reads
   CLAUDE_BIN too). In the instant before the real CLI starts it runs the
   COMPLETE settings pre-flight, through the same function the early check
   uses (`local_eval_guard.check_all_settings`, so the two cannot drift): the
   user-level, harness-checkout and managed files of 1b, then its own cwd:
   `.claude/settings*.json` there, `settings.json` and `settings.local.json`
   in each parent up to the filesystem root, and `.claude/settings*.json`
   beneath the cwd, following symlinks with loop protection. The harness
   checkout's own tree (the judge's cwd) is not walked beneath, only its
   `.claude/` is read; its source was checked in 1b. On a refusal it prints
   the file and key to stderr, records it, exits 87 and does NOT start the
   CLI; otherwise it execs the real CLI with the environment unchanged and,
   for a print-mode session (`-p`/`--print`), `--strict-mcp-config` and (when
   argv names none) `--setting-sources project` prepended, each only when
   absent (`local_eval_guard.session_isolation_args`). That reaches
   skill-creator's own `claude -p` calls, which no harness sink builds. It
   never adds `--no-session-persistence`: a caller may `--resume`.
   A bare `--version` is not checked (it loads no settings). local_eval turns
   any refusal into exit 2, names the trial and fixture(s), and runs no
   further trial.
2. Refuses a `--results-dir` that resolves (symlinks followed) inside this
   checkout, or inside any other git work tree or `.git` directory, or that
   already holds files; a git that cannot answer (dubious ownership,
   `safe.bareRepository`) is a refusal, not "no repository", and so is a
   `.git` entry in any parent directory:
   `results/` is not gitignored, so summaries written there would ride into a
   pull request.
2b. Refuses (naming the pinned versions and the pip command) when a selected
   fixture has a check that parses Bash (`objective.PARSER_BACKED_CHECKS`,
   `shell_staged_tool_guard` and `shell_capture_safe`) and this python cannot import
   `tree_sitter`/`tree_sitter_bash`. Install `tree-sitter==0.26.0
   tree-sitter-bash==0.25.1`, the CI pins, in the python that runs this
   wrapper (a venv works). run_eval, which this wrapper starts per trial,
   also never scores a missing parser as a failed check: the trial is an
   error (`scorer_unavailable`), counted in `errors`, excluded from every
   check's pass rate.
3. Refuses a fixture that is not a skill fixture, whose `env:` block names a
   variable rule 1 refuses (run_eval applies that block last, so it would hand
   the variable back to every arm), whose models cannot be selected, or whose
   registry checkout is missing.
4. Records the harness identity in `<out>/manifest.json`: `claude --version`,
   the `--permission-mode` every trial's arms and judge run under,
   the fixture's pinned models and the models `run_eval.select_models` picks,
   the UTC start time, and the git SHA (plus a dirty flag) of this checkout
   and of the registry checkout the fixture's `registry:` resolves to.
5. Contamination probe. Reads the CLI's `system/init` event
   (`harness/propagation/init_probe.py`; no model turn, the API endpoint is
   black-holed) in an EMPTY git repository, created under the same temp
   parent and name prefix `run_eval.materialize_workspace` uses for an arm,
   with the arms' own environment (`run_eval.agent_env`, the API endpoint
   overridden) and `--setting-sources project`. If the skill under
   test is already visible there — bare (`<skill>`) or namespaced
   (`<bundle>:<skill>`) — the run FAILS (exit 2) before any arm: a user-level
   copy would contaminate the without_skill arm. The visible skill list is
   recorded in the manifest either way.
6. Runs `--trials` trials (default 3). Trial k is
   `run_eval.py <fixture> --arm <arm> --results-dir <out>/t<k>
   --read-deny <out>` with every `--registry` and `--read-deny` passed
   through (relative paths resolved from YOUR cwd) and `--no-judge` when
   given, so no trial's arms read an earlier trial's transcripts; its output
   is kept in `<out>/t<k>/run_eval.log`.
   A trial that exits non-zero (2 is run_eval's runner error) is recorded in
   the manifest and counted in the aggregate's `errors`; it is never dropped.
   Right after each trial every `summary.json` under `<out>/t<k>/` is stamped
   `"local_exhibit": true`, and `scripts/make_badge.py` refuses a summary so
   stamped. Because a kill could land between a summary's write and its stamp,
   a `LOCAL_EXHIBIT` marker file is written at the results dir's root and in
   each `t<k>` BEFORE the trial launches, and `make_badge.py` refuses any
   `--results-dir` holding that marker in itself or any parent: a trial tree
   is never badge input.
7. Writes `<out>/aggregate.json`: per fixture (`fixtures.<name>.arms`, and
   `arms` at top level when one fixture ran; a flat fixture is named
   `(flat)`), per arm: `n` (trials), `errors`, per-check pass counts and
   `pass_rate`, the objective total's mean/min/max, the judge's `overall` and
   per-dimension mean/min/max when judged, and the agent cost's mean and sum.
   Each arm also has `efficiency`: for the agent cost, turns, wall time and
   the four token counts, `n`, `n_missing`, mean, median, min, max and sum
   over the scored trials (`run_eval.efficiency_stats`; `tool_errors` is
   always missing here, because a trial's tool trace is not read). A fixture
   run with both `with_skill` and `without_skill` also has `efficiency_delta`,
   with minus without, of each metric's mean and median.
   Errored arm-trials are excluded from every statistic and counted in
   `errors`.

Exit codes: 0 every trial ran clean; 1 at least one trial or arm errored (the
aggregate and manifest are still written); 2 refused before any trial ran.

What it never does: write inside the repository, touch the
`persistent/eval-results` branch, push, or copy a transcript. Each trial's
`transcripts/raw.json` stays where run_eval wrote it, under `<out>/t<k>/`; it
carries host paths and is LOCAL-ONLY, and the manifest lists those files and
says so.

Limits a reader must know:

- The judge runs under the real HOME (its login lives there) and is
  isolated by flags: `--setting-sources ""` (no user or project settings,
  CLAUDE.md, hooks or settings-enabled plugins), `--strict-mcp-config` (none
  of the account's claude.ai MCP connectors), `--no-session-persistence` and
  auto-memory off (`judge.JUDGE_ISOLATION_FLAGS`, measured on CLI 2.1.289).
  What still reaches it: managed settings, the CLI's bundled skills, and
  writes to `~/.claude.json`.
- The probe measures which SKILLS an empty workspace sees. It does not
  measure user memory, hooks or MCP servers, and it does not include the
  fixture's seed.
- On a workstation an arm (auto mode by default, `bypassPermissions` with
  `--permission-mode bypassPermissions`) inherits the real `HOME`, where
  the account's own credentials live (ADR 0002, decision 4), and it can read
  the credential file there. A scratch HOME, a scratch CLAUDE_CONFIG_DIR and
  `--bare` each lose the `/login` (measured), so arms are isolated by flags:
  `--setting-sources project`, `--strict-mcp-config`, auto-memory off, and
  `--no-session-persistence` for a one-turn arm. A multi-turn arm must
  persist to `--resume`; when it ends, the `~/.claude/projects/<workspace>`
  directory it created is moved to `$XDG_STATE_HOME/skills-evals/sessions/`
  (default `~/.local/state/...`), and one that existed before is not touched.
  Managed settings, bundled skills and `~/.claude.json` writes still apply.
- Nothing here was verified against a real CLI: no real run was made. What
  the refusals and the settings pre-flight cannot see, and so stays
  unverified: `~/.claude.json`, the system keychain, and
  `~/.claude/.credentials.json` (the interactive login itself, which is the
  intended credential), any settings key or variable a future CLI release
  adds, a settings file created AFTER the CLI has started (by its own child
  processes), one written between the launcher's check and the CLI's read (a
  race), and anything the agent itself runs (it can edit files, PATH or
  CLAUDE_BIN for the commands it spawns). A fixture `env:` block can likewise
  set CLAUDE_BIN or PATH for what the agent spawns afterwards; the harness's
  own launches still go through the guard. The interactive login is the only credential the judge is left able
  to use, if those checks and the CLI's documented precedence hold.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "harness"
RUN_EVAL = HARNESS_DIR / "run_eval.py"

sys.path.insert(0, str(HARNESS_DIR))
import guidance  # noqa: E402
import run_eval  # noqa: E402
from scorers import bash_ast, objective  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_eval_guard  # noqa: E402
from local_eval_guard import (GUARD_EXIT, Refused, check_settings_file,  # noqa: E402,F401
                              proxy_userinfo_names, refused_env_names,
                              settings_in_tree)
from propagation import init_probe  # noqa: E402

EXHIBIT = "local — not badge input"

#: What every child may inherit; nothing else survives `child_environment`.
CHILD_ENV_NAMES = ("PATH", "HOME", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
                   "USER", "LOGNAME", "SHELL",
                   "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "no_proxy",
                   "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
                   "CLAUDE_BIN", "SKILLS_EVALS_REGISTRIES", "AGENTSKILLS_DIR")
CHILD_ENV_PREFIXES = ("LC_",)

#: Written at the results dir's root and in each trial dir before the trial
#: launches; scripts/make_badge.py refuses a results dir at or below one.
EXHIBIT_MARKER = "LOCAL_EXHIBIT"

#: The guard launcher's refusal log, in its private directory.
GUARD_RECORD = "refusals.jsonl"

#: Where a managed policy lives (the CLI's documented locations), shared with
#: run_eval's managed sandbox check.
MANAGED_SETTINGS_FILES = run_eval.MANAGED_SETTINGS_FILES
MANAGED_SETTINGS_DROPINS = run_eval.MANAGED_SETTINGS_DROPINS

#: aggregate.json's name for a flat fixture.
FLAT_NAME = "(flat)"

ARMS = ("both", "with_skill", "without_skill")
MAX_TRIALS = 20

EXIT_OK = 0
EXIT_TRIAL_ERROR = 1
EXIT_REFUSED = 2

TRANSCRIPTS_NOTE = (
    "LOCAL-ONLY. Each raw.json is the CLI's full JSON result for one arm and "
    "carries host paths (workspace, HOME). This wrapper never copies them; do "
    "not commit, publish or attach them.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def child_environment(environ) -> dict:
    """The allow-listed environment every child runs under, plus
    `guidance.CLI_FORCED_ENV` (auto-memory off) SET, not inherited: the
    children run under the real HOME, and skill-creator's own `claude`
    calls (propose_skill_edit) reach no harness sink that would set it."""
    return guidance.cli_child_env(
        {name: value for name, value in environ.items()
         if name in CHILD_ENV_NAMES or name.startswith(CHILD_ENV_PREFIXES)})


def check_user_settings(home: Path) -> None:
    """The early settings pre-flight: user, this checkout's and managed files.
    The launch-time guard runs the same `check_all_settings`."""
    local_eval_guard.check_all_settings(
        home, REPO_ROOT, MANAGED_SETTINGS_FILES, MANAGED_SETTINGS_DROPINS)


def check_fixture_settings(seeds: list[Path], registries: list[Path]) -> None:
    """Refuse a credential source in settings an ARM loads as project
    settings: any depth under each fixture's seed, and the root `.claude/` of
    each registry checkout."""
    for seed in seeds:
        for path in settings_in_tree(seed):
            check_settings_file(path)
    for registry in registries:
        for path in sorted((registry / ".claude").glob("settings*.json")):
            check_settings_file(path)


def check_scorer_dependencies(fixtures: list[dict]) -> None:
    """Refuse when a selected fixture's checks need a parser this python lacks.

    Without it the scorer cannot score `shell_staged_tool_guard` at all, and a
    run would spend its trials only to record every one as a scorer error.
    """
    needing = sorted({item["fixture"]["skill"] + (f"/{item['name']}" if item.get("name") else "")
                      for item in fixtures
                      for check in item["fixture"].get("objective_checks") or []
                      if isinstance(check, dict)
                      and check.get("type") in objective.PARSER_BACKED_CHECKS})
    if needing and not bash_ast.parser_importable():
        raise Refused(
            f"{', '.join(needing)} use a check that parses Bash with Tree-sitter "
            f"({', '.join(bash_ast.PARSER_REQUIREMENTS)}), which this python "
            f"({sys.executable}) cannot import, and a missing parser would "
            "otherwise score as the agent's failure. Install the CI pins "
            f"(`{bash_ast.install_command(sys.executable)}`, ideally in a "
            "venv) and re-run. Nothing run.")


def _git_env() -> dict:
    """The caller's environment without GIT_* overrides, so `git -C <dir>`
    answers about <dir> and not about a GIT_DIR the caller exported, and with
    messages in a fixed language (check_results_dir reads one)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return env


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    # Raw git is fine here (#343): operator checkouts and the results directory, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                          text=True, env=_git_env(), timeout=60)


def git_identity(path: Path) -> dict:
    """{"sha", "dirty"} of the checkout at `path`; both null when it is not a
    git checkout (an extracted archive, say)."""
    head = _git(path, "rev-parse", "HEAD")
    if head.returncode != 0:
        return {"sha": None, "dirty": None}
    status = _git(path, "status", "--porcelain")
    return {"sha": head.stdout.strip(),
            "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None}


def check_results_dir(out: Path, *, require_empty: bool = True) -> Path:
    """The resolved results dir, or Refused.

    A persistent record root may use require_empty=False; repository and
    directory-type checks still apply. CLI runs keep the strict default.
    """
    resolved = out.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise Refused(
            f"--results-dir resolves inside this checkout ({REPO_ROOT}); "
            "results/ is not gitignored, so summaries would ride into a pull "
            "request. Pick a directory outside every repository.")
    existing = resolved
    while not existing.exists():
        existing = existing.parent
    if existing.is_dir():
        # --absolute-git-dir answers inside a work tree, inside a .git
        # directory and in a bare repository alike; --show-toplevel does not.
        try:
            repo = _git(existing, "rev-parse", "--absolute-git-dir")
        except (OSError, subprocess.SubprocessError) as exc:
            raise Refused(f"could not ask git whether {existing} is inside a "
                          f"repository ({type(exc).__name__}); refusing") from exc
        if repo.returncode == 0:
            raise Refused(
                f"--results-dir resolves inside the git work tree or git "
                f"directory {repo.stdout.strip()}; pick a directory outside "
                "every repository.")
        if "not a git repository" not in repo.stderr:
            # Any other failure (dubious ownership, safe.bareRepository, a
            # corrupt repository) is a repository git will not open, not
            # proof that there is none.
            detail = (repo.stderr.strip().splitlines() or ["no message"])[0]
            raise Refused(f"git refused to say whether {existing} is inside a "
                          f"repository ({detail}); refusing, since that is "
                          "not the same as there being none.")
        # A second check that does not ask git: a `.git` entry in this
        # directory or any parent.
        for parent in (existing, *existing.parents):
            if (parent / ".git").exists():
                raise Refused(f"--results-dir resolves inside {parent}, which "
                              "holds a git repository (a .git entry); pick a "
                              "directory outside every repository.")
    if resolved.exists() and (not resolved.is_dir()
                              or (require_empty and any(resolved.iterdir()))):
        raise Refused(
            f"--results-dir {resolved} already exists and is not an empty "
            "directory; trials from two runs must not mix.")
    return resolved


def discover_fixtures(eval_dir: Path, selected: str | None) -> list[dict]:
    """The fixtures one invocation runs, each `{"dir", "fixture", "name"}`
    (`name` is the nested fixture's name, None for a flat one), selected the
    way run_eval selects them. All must be of one skill."""
    try:
        dirs = run_eval.resolve_fixture_dirs(eval_dir, selected)
    except guidance.GuidanceError as exc:
        raise Refused(f"fixture layout: {exc}") from exc
    found = []
    for directory in dirs:
        fixture = load_skill_fixture(directory)
        try:
            name = run_eval.nested_fixture_name(directory, fixture["skill"])
        except guidance.GuidanceError as exc:
            raise Refused(f"fixture layout: {exc}") from exc
        found.append({"dir": directory, "fixture": fixture, "name": name})
    skills = sorted({f["fixture"]["skill"] for f in found})
    if len(skills) != 1:
        raise Refused(f"the fixtures name more than one skill ({', '.join(skills)})")
    return found


def _absolute_registry_flags(values: list[str] | None) -> list[str]:
    """--registry NAME=PATH flags with PATH made absolute from the CALLER's
    cwd: run_eval runs from the repo root, where a relative path would name a
    different directory."""
    try:
        parsed = run_eval._parse_registry_flags(values)
    except ValueError as exc:
        raise Refused(f"registry configuration error: {exc}") from exc
    return [f"{name}={Path(path).expanduser().resolve()}"
            for name, path in parsed.items()]


def load_skill_fixture(eval_dir: Path) -> dict:
    try:
        fixture = run_eval.load_fixture(eval_dir)
        run_eval.validate_mapping_keys(fixture, eval_dir / "fixture.yaml")
        run_eval.validate_timeouts(fixture, eval_dir / "fixture.yaml")
        run_eval.validate_followups(fixture, eval_dir / "fixture.yaml")
        run_eval.validate_effort(fixture, eval_dir / "fixture.yaml")
    except (OSError, guidance.GuidanceError) as exc:
        raise Refused(f"fixture configuration error: {exc}") from exc
    subject = fixture.get("subject", "skill")
    if subject != "skill":
        raise Refused(f"{eval_dir} is a {subject!r} fixture; this wrapper runs "
                      "skill fixtures only")
    for key in ("skill", "prompt"):
        if not isinstance(fixture.get(key), str) or not fixture[key]:
            raise Refused(f"{eval_dir / 'fixture.yaml'}: `{key}:` must be a "
                          "non-empty string")
    try:
        run_eval._validate_skill_name(fixture["skill"])
    except ValueError as exc:
        raise Refused(f"invalid fixture: {exc}") from exc
    # run_eval.agent_env applies the fixture's `env:` block LAST, so a fixture
    # naming one of the refused variables would hand it back to every arm.
    env_block = fixture.get("env")
    named = refused_env_names([str(k) for k in env_block]
                              if isinstance(env_block, dict) else [])
    if named:
        raise Refused(f"{eval_dir / 'fixture.yaml'}: `env:` names "
                      f"{', '.join(named)}, which this wrapper refuses to put "
                      "in an arm's environment")
    return fixture


def resolve_fixture_registry(fixture: dict, registry_flags: list[str],
                             needed: bool) -> dict | None:
    """The registries.yml entry the fixture's `registry:` names, resolved the
    way run_eval resolves it, or None when no arm installs the skill."""
    try:
        registries = run_eval.resolve_registries(
            registry_flags, os.environ.get("SKILLS_EVALS_REGISTRIES"),
            REPO_ROOT, os.environ.get("AGENTSKILLS_DIR"))
        run_eval._validate_registry_paths(registries)
    except ValueError as exc:
        raise Refused(f"registry configuration error: {exc}") from exc
    if not needed:
        return None
    url = fixture.get("registry")
    if not isinstance(url, str) or not url:
        raise Refused("the fixture has no `registry:` string")
    try:
        entry = run_eval.registry_for_url(registries, url)
    except ValueError as exc:
        raise Refused(f"registry configuration error: {exc}") from exc
    if not entry["path"].is_dir():
        raise Refused(f"registry {entry['name']!r} ({entry['source']}) has no "
                      "checkout; pass --registry "
                      f"{entry['name']}=PATH")
    return entry


def select_models(fixture: dict, no_judge: bool) -> dict:
    agent, judge_model, problem = run_eval.select_models(
        fixture, argparse.Namespace(model=None, roster=None, no_judge=no_judge))
    if problem:
        raise Refused(f"model selection: {problem}")
    return {"agent": agent, "judge": None if no_judge else judge_model}


def visible_copies(skills: list, skill: str) -> list[str]:
    """Entries of the init event's `skills` that ARE `skill`: the bare name,
    or any `<namespace>:<skill>`."""
    return [s for s in skills
            if isinstance(s, str) and s.rsplit(":", 1)[-1] == skill]


def contamination_probe(fixture: dict) -> dict:
    """What an arm's session sees before the seed: the skill list of an empty
    workspace under the arms' own environment and `--setting-sources
    project`. Raises init_probe.ProbeError when the CLI cannot be observed."""
    workspace = Path(tempfile.mkdtemp(prefix=run_eval.WORKSPACE_PREFIX))
    try:
        run_eval._git("init", "-q", cwd=workspace)
        env = run_eval.agent_env(workspace, fixture.get("env"))
        env["ANTHROPIC_BASE_URL"] = init_probe.BLACKHOLE_BASE_URL
        facts = init_probe.probe(
            cwd=workspace, home=Path(env.get("HOME") or Path.home()),
            tmpdir=Path(env.get("TMPDIR") or tempfile.gettempdir()),
            env_extra=env, extra_argv=("--setting-sources", "project"),
            allow_scope_flags=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    copies = visible_copies(facts.skills, fixture["skill"])
    return {
        "setting_sources": "project",
        "workspace": "empty git repository, same temp parent and prefix as an arm's",
        "claude_code_version": facts.version,
        "api_key_source": facts.init.get("apiKeySource"),
        "visible_skills": facts.skills,
        "plugins": [p.get("name") for p in facts.plugins if isinstance(p, dict)],
        "skill_under_test_copies": copies,
        "skill_under_test_visible": bool(copies),
    }


# ---------------------------------------------------------------------------
# aggregation


def _stats(values: list) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "mean": sum(values) / len(values),
            "min": min(values), "max": max(values)}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_arm_summary(trial_dir: Path, skill: str, arm: str,
                     name: str | None = None):
    """(summary, None) or (None, problem) for one arm of one fixture of one
    trial. A flat fixture's arm is `<skill>/<ts>/<arm>/`, a nested one's
    `<skill>/<ts>/<name>/<arm>/` (run_eval's #66 layout)."""
    middle = f"*/{name}" if name else "*"
    paths = sorted(trial_dir.glob(f"{skill}/{middle}/{arm}/summary.json"))
    if len(paths) != 1:
        return None, f"expected one {arm}/summary.json, found {len(paths)}"
    try:
        summary = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable summary.json ({type(exc).__name__})"
    if not isinstance(summary, dict):
        return None, "summary.json is not a mapping"
    return summary, None


def aggregate_arm(records: list) -> dict:
    """`records`: [(trial k, summary or None, problem or None)] for one arm."""
    error_trials, scored = [], []
    for k, summary, problem in records:
        if summary is None:
            error_trials.append({"trial": k, "error": problem})
        elif summary.get("error"):
            err = summary["error"]
            error_trials.append({"trial": k, "error": err.get("type")
                                 if isinstance(err, dict) else str(err)})
        else:
            scored.append(summary)

    checks: dict = {}
    totals, check_counts = [], set()
    overall, dims, costs = [], {}, []
    judge_errors = judged = 0
    models, judge_models = set(), set()
    for summary in scored:
        results = summary.get("objective_checks")
        if isinstance(results, list):
            check_counts.add(len(results))
            totals.append(sum(1 for c in results
                              if isinstance(c, dict) and c.get("passed") is True))
            for c in results:
                if not isinstance(c, dict):
                    continue
                entry = checks.setdefault(str(c.get("id")), {"passed": 0, "n": 0})
                entry["n"] += 1
                entry["passed"] += 1 if c.get("passed") is True else 0
        jd = summary.get("judge")
        if isinstance(jd, dict):
            if _number(jd.get("overall")):
                judged += 1
                overall.append(jd["overall"])
                for d in jd.get("dimensions") or []:
                    if isinstance(d, dict) and _number(d.get("score")):
                        dims.setdefault(str(d.get("name")), []).append(d["score"])
            elif "error" in jd:
                judge_errors += 1
        cost = (summary.get("agent") or {}).get("cost_usd")
        if _number(cost):
            costs.append(cost)
        models.update(m for m in summary.get("models_used") or []
                      if isinstance(m, str))
        judge_models.update(m for m in summary.get("judge_models_used") or []
                            if isinstance(m, str))
    for entry in checks.values():
        entry["pass_rate"] = entry["passed"] / entry["n"]

    return {
        "n": len(records),
        "errors": len(error_trials),
        "error_trials": error_trials,
        "scored": len(scored),
        "objective": {
            "checks": dict(sorted(checks.items())),
            "total": _stats(totals),
            "checks_per_trial": sorted(check_counts),
        },
        "judge": None if not (judged or judge_errors) else {
            "overall": _stats(overall),
            "dimensions": {name: _stats(v) for name, v in sorted(dims.items())},
            "errors": judge_errors,
        },
        "cost_usd": None if not costs else {
            "n": len(costs), "mean": sum(costs) / len(costs), "sum": sum(costs)},
        "efficiency": run_eval.efficiency_stats(scored),
        "models_used": sorted(models),
        "judge_models_used": sorted(judge_models),
    }


def arm_names(arm: str) -> list[str]:
    return ["with_skill", "without_skill"] if arm == "both" else [arm]


def build_aggregate(out: Path, skill: str, names: list, arms: list[str],
                    trials: int) -> dict:
    """`names`: the fixtures' nested names (None for a flat one)."""
    fixtures = {}
    for name in names:
        per_arm = {}
        for arm in arms:
            records = []
            for k in range(1, trials + 1):
                summary, problem = read_arm_summary(out / f"t{k}", skill, arm, name)
                records.append((k, summary, problem))
            per_arm[arm] = aggregate_arm(records)
        entry = {"arms": per_arm}
        if "with_skill" in per_arm and "without_skill" in per_arm:
            entry["efficiency_delta"] = run_eval.efficiency_delta(
                per_arm["with_skill"]["efficiency"],
                per_arm["without_skill"]["efficiency"])
        fixtures[name or FLAT_NAME] = entry
    aggregate = {"exhibit": EXHIBIT, "skill": skill, "trials": trials,
                 "fixtures": fixtures}
    if len(fixtures) == 1:
        aggregate["arms"] = next(iter(fixtures.values()))["arms"]
    return aggregate


def stamp_local_exhibit(trial_dir: Path) -> None:
    """Mark every summary.json run_eval wrote under `trial_dir` as a local
    exhibit; scripts/make_badge.py refuses a summary so marked."""
    for path in sorted(trial_dir.rglob("summary.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(summary, dict):
            summary["local_exhibit"] = True
            _write_json(path, summary)


def transcript_files(out: Path) -> list[str]:
    return sorted(p.relative_to(out).as_posix()
                  for p in out.glob("t*/**/transcripts/raw.json"))


# ---------------------------------------------------------------------------


def _write_marker(directory: Path) -> None:
    (directory / EXHIBIT_MARKER).write_text(
        f"{EXHIBIT}\nscripts/make_badge.py refuses a results dir at or below "
        "a directory holding this file.\n", encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    staged = path.with_name(path.name + ".partial")
    staged.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    staged.replace(path)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts/local_eval.py",
        description=__doc__.split("\n\n", 1)[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eval_dir", type=Path,
                        help="evals/<skill> (flat fixture, or a skill directory "
                             "of nested ones) or evals/<skill>/<name>")
    parser.add_argument("--results-dir", type=Path, required=True,
                        help="where trials, aggregate.json and manifest.json "
                             "go; outside every repository, empty or absent")
    parser.add_argument("--trials", type=int, default=3,
                        help=f"number of trials, 1..{MAX_TRIALS} (default 3)")
    parser.add_argument("--arm", default="both", choices=ARMS,
                        help="passed to run_eval.py (default both)")
    parser.add_argument("--no-judge", action="store_true",
                        help="passed to run_eval.py")
    parser.add_argument("--fixture", default=None, metavar="NAME",
                        help="passed to run_eval.py: with a skill directory of "
                             "nested fixtures, run only this one")
    parser.add_argument("--registry", action="append", default=None,
                        help="passed to run_eval.py, repeatable: NAME=PATH")
    parser.add_argument("--read-deny", type=Path, action="append", default=[],
                        metavar="DIR",
                        help="passed to run_eval.py, repeatable, beside "
                             "--results-dir itself: another directory no "
                             "agent arm may read")
    parser.add_argument("--timestamp", default=None,
                        help="validated timestamp passed to each run_eval.py trial")
    parser.add_argument("--permission-mode",
                        default=run_eval.guidance.DEFAULT_PERMISSION_MODE,
                        choices=list(run_eval.guidance.PERMISSION_MODES),
                        help="passed to run_eval.py and recorded in "
                             "manifest.json (default "
                             f"{run_eval.guidance.DEFAULT_PERMISSION_MODE})")
    args = parser.parse_args(argv)
    if not 1 <= args.trials <= MAX_TRIALS:
        parser.error(f"--trials must be 1..{MAX_TRIALS}, got {args.trials}")
    if args.timestamp is not None and not run_eval._valid_timestamp(args.timestamp):
        parser.error("--timestamp must have the YYYYMMDDTHHMMSSZ format")
    return args


def _manifest_fixture(item: dict) -> dict:
    fixture, registry = item["fixture"], item["registry"]
    judge_cfg = fixture.get("judge") if isinstance(fixture.get("judge"), dict) else {}
    return {
        "name": item["name"] or FLAT_NAME,
        "models": {"fixture_pins": {"model": fixture.get("model"),
                                    "judge_model": (judge_cfg or {}).get("model")},
                   "selected": item["models"]},
        "registry": None if registry is None else {
            "name": registry["name"], "source": registry["source"],
            **git_identity(registry["path"])},
    }


def install_guard_launcher(guard_dir: Path, environ=None) -> str:
    """Write the launch-time guard launcher as `<guard_dir>/claude` (mode
    0700) and put it in front of every child: CLAUDE_BIN names it, and
    `guard_dir` heads PATH, so a child that looks `claude` up reaches it too.
    Returns the real CLI's absolute path, which the launcher execs. With
    `environ`, only that child mapping is changed; the default preserves the
    local evaluator's process-wide launcher installation."""
    env = os.environ if environ is None else environ
    wanted = env.get("CLAUDE_BIN") or "claude"
    real = shutil.which(wanted, path=env.get("PATH", os.defpath))
    if real is None:
        raise Refused(f"cannot find the claude CLI ({wanted!r} is not "
                      "executable or on PATH); set CLAUDE_BIN")
    real = os.path.abspath(real)
    launcher = guard_dir / "claude"
    launcher.write_text(local_eval_guard.launcher_source(
        python=sys.executable, scripts_dir=str(Path(__file__).resolve().parent),
        real_cli=real, repo_root=str(REPO_ROOT),
        record=str(guard_dir / GUARD_RECORD),
        managed_files=MANAGED_SETTINGS_FILES,
        managed_dropins=MANAGED_SETTINGS_DROPINS), encoding="utf-8")
    launcher.chmod(0o700)
    env["CLAUDE_BIN"] = str(launcher)
    env["PATH"] = str(guard_dir) + os.pathsep + env.get("PATH", "")
    return real


def guard_refusals(guard_dir: Path) -> list[dict]:
    """What the launcher recorded when it refused to start the CLI."""
    try:
        lines = (guard_dir / GUARD_RECORD).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            found.append(json.loads(line))
        except ValueError:
            found.append({"message": "(unreadable guard record)"})
    return found


def _guard_refused(manifest: dict, manifest_path: Path, guard_dir: Path,
                   fixtures: list, where: str) -> int | None:
    """EXIT_REFUSED (after saying so) when the launcher refused any launch."""
    refusals = guard_refusals(guard_dir)
    if not refusals:
        return None
    names = ", ".join(f["name"] or FLAT_NAME for f in fixtures)
    manifest["status"] = f"refused: the launch-time settings guard, {where}"
    manifest["guard_refusal"] = {"where": where, "fixtures": names,
                                 "message": refusals[0].get("message")}
    _write_json(manifest_path, manifest)
    print(f"local_eval: the launch-time settings guard refused to start the "
          f"CLI during {where} (fixture(s): {names}): "
          f"{refusals[0].get('message')} Nothing further run.", file=sys.stderr)
    return EXIT_REFUSED


def main(argv=None) -> int:
    args = parse_args(argv)
    guard_dir = Path(tempfile.mkdtemp(prefix="local-eval-guard-"))
    try:
        return _run(args, guard_dir)
    finally:
        shutil.rmtree(guard_dir, ignore_errors=True)


def _run(args: argparse.Namespace, guard_dir: Path) -> int:
    try:
        refused = refused_env_names(os.environ)
        if refused:
            raise Refused(
                "refusing to run with " + ", ".join(refused) + " set: a local "
                "exhibit runs under your own /login, and each of these can "
                "bill a dollar or cloud account, re-route the CLI to another "
                "provider, or put a credential in reach of the arms. Unset "
                "them (e.g. `env -u NAME ...`) and re-run.")
        proxies = proxy_userinfo_names(os.environ)
        if proxies:
            raise Refused(
                "refusing to run with " + ", ".join(proxies) + " set to a URL "
                "that embeds credentials (scheme://user:pass@host); put the "
                "credentials elsewhere or use a proxy without them.")
        check_user_settings(Path(os.environ.get("HOME") or Path.home()))
        # From here every child (the version call, the probe, run_eval and
        # through it the arms, and the judge) inherits only the allow-list.
        allowed = child_environment(os.environ)
        os.environ.clear()
        os.environ.update(allowed)
        out = check_results_dir(args.results_dir)
        eval_dir = Path(os.path.abspath(args.eval_dir.expanduser()))
        fixtures = discover_fixtures(eval_dir, args.fixture)
        registry_flags = _absolute_registry_flags(args.registry)
        arms = arm_names(args.arm)
        for item in fixtures:
            item["registry"] = resolve_fixture_registry(
                item["fixture"], registry_flags, needed="with_skill" in arms)
            item["models"] = select_models(item["fixture"], args.no_judge)
        check_scorer_dependencies(fixtures)
        check_fixture_settings(
            [item["dir"] / run_eval.SEED_DIR for item in fixtures],
            [Path(f.split("=", 1)[1]) for f in registry_flags]
            + [item["registry"]["path"] for item in fixtures if item["registry"]])
        real_cli = install_guard_launcher(guard_dir)
    except Refused as exc:
        print(f"local_eval: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    fixture = fixtures[0]["fixture"]
    skill = fixture["skill"]
    # One id for the whole invocation, in the routine's run id shape; every
    # trial's summaries carry it in their `run` block (#370).
    run_id = (datetime.now(timezone.utc).strftime(run_eval.TIMESTAMP_FORMAT)
              + "-" + os.urandom(3).hex())
    manifest = {
        "exhibit": EXHIBIT,
        "status": "running",
        "run_id": run_id,
        "started_at": _utc_now(),
        "fixture": (eval_dir.relative_to(REPO_ROOT).as_posix()
                    if REPO_ROOT in eval_dir.parents else str(eval_dir)),
        "skill": skill,
        "invocation": {"trials": args.trials, "arm": args.arm,
                       "timestamp": args.timestamp,
                       "no_judge": args.no_judge, "fixture": args.fixture,
                       "registries": [f.split("=", 1)[0] for f in registry_flags]},
        "harness": {"claude_version": run_eval.claude_version(),
                    "permission_mode": args.permission_mode,
                    "claude_path": real_cli,
                    "claude_guard": "a temporary launcher checks the settings "
                                    "in each CLI launch's directory, then "
                                    "execs claude_path; removed at exit"},
        "fixtures": [_manifest_fixture(item) for item in fixtures],
        "skills_evals": git_identity(REPO_ROOT),
        "contamination_probe": None,
        "trials": [],
        "transcripts": {"local_only": True, "note": TRANSCRIPTS_NOTE, "files": []},
        "publishing": "none: this wrapper writes only under the results dir, "
                      "never inside a repository, never to "
                      "persistent/eval-results, and never pushes",
    }
    out.mkdir(parents=True, exist_ok=True)
    _write_marker(out)
    manifest_path = out / "manifest.json"
    _write_json(manifest_path, manifest)

    try:
        probe = contamination_probe(fixture)
    except (init_probe.ProbeError, guidance.GuidanceError, OSError,
            subprocess.SubprocessError) as exc:
        guarded = _guard_refused(manifest, manifest_path, guard_dir, fixtures,
                                 "the contamination probe")
        if guarded is not None:
            return guarded
        manifest["status"] = "refused: the contamination probe could not observe the CLI"
        manifest["contamination_probe"] = {"error": str(exc)}
        _write_json(manifest_path, manifest)
        print(f"local_eval: contamination probe failed, nothing run: {exc}",
              file=sys.stderr)
        return EXIT_REFUSED
    manifest["contamination_probe"] = probe
    if probe["skill_under_test_visible"]:
        manifest["status"] = "refused: the skill under test is already visible"
        _write_json(manifest_path, manifest)
        print(f"local_eval: {skill!r} is already visible to an empty workspace "
              f"under --setting-sources project, as "
              f"{', '.join(probe['skill_under_test_copies'])}: the without_skill "
              "arm would be contaminated. Nothing run; see "
              f"{manifest_path}.", file=sys.stderr)
        return EXIT_REFUSED
    _write_json(manifest_path, manifest)

    for k in range(1, args.trials + 1):
        trial_dir = out / f"t{k}"
        trial_dir.mkdir()
        _write_marker(trial_dir)
        cmd = [sys.executable, str(RUN_EVAL), str(eval_dir), "--arm", args.arm,
               "--results-dir", str(trial_dir),
               "--permission-mode", args.permission_mode,
               # What kind of run this is: a workstation, under the
               # operator's own login (rule 1 refuses an API credential).
               "--run-billing", "subscription", "--run-runner", "workstation",
               "--run-location", "local", "--run-id", run_id]
        # The whole results dir, not only this trial's: trial k's arms must
        # not read trials 1..k-1 (ADR 0011's reads addendum).
        for path in (out, *args.read_deny):
            cmd += ["--read-deny", os.path.abspath(path.expanduser())]
        if args.fixture is not None:
            cmd += ["--fixture", args.fixture]
        for flag in registry_flags:
            cmd += ["--registry", flag]
        if args.no_judge:
            cmd.append("--no-judge")
        if args.timestamp is not None:
            cmd += ["--timestamp", args.timestamp]
        print(f"local_eval: trial {k}/{args.trials}", flush=True)
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                              text=True)
        stamp_local_exhibit(trial_dir)
        (trial_dir / "run_eval.log").write_text(
            (proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        manifest["trials"].append({
            "trial": k, "results_dir": f"t{k}", "exit_code": proc.returncode,
            "error": None if proc.returncode == 0
            else f"run_eval.py exited {proc.returncode}; see t{k}/run_eval.log"})
        _write_json(manifest_path, manifest)
        guarded = _guard_refused(manifest, manifest_path, guard_dir, fixtures,
                                 f"trial {k}")
        if guarded is not None:
            return guarded

    aggregate = build_aggregate(out, skill, [f["name"] for f in fixtures], arms,
                                args.trials)
    _write_json(out / "aggregate.json", aggregate)

    errored = (any(t["exit_code"] != 0 for t in manifest["trials"])
               or any(a["errors"] for f in aggregate["fixtures"].values()
                      for a in f["arms"].values()))
    manifest["status"] = "completed with errors" if errored else "completed"
    manifest["finished_at"] = _utc_now()
    manifest["transcripts"]["files"] = transcript_files(out)
    _write_json(manifest_path, manifest)
    for name, entry in aggregate["fixtures"].items():
        for arm, stats in entry["arms"].items():
            total = stats["objective"]["total"]
            judge_overall = (stats["judge"] or {}).get("overall")
            print(f"local_eval: {name} {arm}: n={stats['n']} "
                  f"errors={stats['errors']} "
                  f"objective mean={total['mean'] if total else '-'} "
                  f"judge mean={judge_overall['mean'] if judge_overall else '-'}")
    print(f"local_eval: wrote {out / 'aggregate.json'} and {manifest_path} "
          f"({EXHIBIT})")
    return EXIT_TRIAL_ERROR if errored else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
