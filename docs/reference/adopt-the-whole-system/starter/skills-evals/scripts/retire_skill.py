#!/usr/bin/env python3
"""Act on a `retire` verdict (#70, part 2): file or update an issue, or open
an auto-merging removal pull request in agentskills.

  retire_skill.py --gates FILE --registry agentskills=PATH
                  [--registry NAME=PATH ...] [--dry-run]
                  [--allow-mode proposal|auto ...] [--usage FILE]

`--gates` is scripts/compute_gates.py's output. A skill acts only when its
verdict there is `retire`, and this script re-checks what that verdict
means before acting: every (fixture, roster model) cell of the cells'
fixtures crossed with the gates file's roster models present exactly once,
and every one `no-benefit` (as harness/gates.py defines the verdict). Any
field of the wrong type is a refusal, never a crash or a pass.
compute_gates lets one skill retire per evaluation; two `retire` verdicts
here are refused, never a pick.

THE MODE is the gates file's `retirement_mode` (from evals/gate-policy.yml;
anything but `auto` is `proposal`), lowered to `proposal` in two cases (the
removal path's `effective_mode`, as scripts/improve_gate.py lowers an
improvement to a draft):

  * the skill's plugin runs code (improve_gate.py's `auto_refusal`: a
    `hooks/` or `bin/` directory, a settings or package file, or a manifest
    `hooks` or `settings` key). Removing one of its skills would wait on
    agentskills' plugin-runtime-review, which auto-merge cannot approve
    (agentskills ADR 0021, decision 4);
  * the caller has no write credential for agentskills this run
    (`--fallback-to-proposal` without `--allow-mode auto`): the scheduled
    workflow passes it when the GitHub App token was not minted.

The issue says why, and a human removes.

  * `proposal`: an issue in skills-evals, found by a marker comment in its
    body (`ISSUE_MARKER`) on an issue `github-actions[bot]` opened, is
    created or its body updated. One issue per skill, ever: an open one is
    updated, and a closed one means the skill is already decided, so nothing
    is filed again. A human removes. An issue anyone else wrote is ignored,
    whatever it quotes.
  * `auto` (the owner, 2026-10-25; docs/decisions/0014 and agentskills ADR
    0021): a branch `retire-skill/<skill>` is prepared in the
    agentskills checkout; it is pushed (and the push verified with
    `git merge-base --is-ancestor`), a ready pull request is opened, labeled
    `agent-authored`, and auto-merge (merge commit, `--match-head-commit`)
    is enabled and read back. No human review, so the branch must pass the
    registry's required checks on its own. Its first commit deletes
    `plugins/<plugin>/skills/<skill>/`, raises the plugin's MAJOR version in
    both manifests (agentskills ADR 0009 requires a raise; removing a
    skill breaks every consumer that calls it, which the registry has bumped
    as major before, `coding-local` 1.1.0 -> 2.0.0 for `sync-skills`),
    adds a `docs/skill-impact.md` entry, drops the skill from
    `scripts/plugin_layout.json` (the ADR 0013
    layout its tests pin) and regenerates the README skill table with the
    registry's own `scripts/generate_readme_table.py`. When `skills.lock`
    locks the plugin, a second commit re-pins it to the first with the
    registry's `scripts/generate_skills_lock.py --repin`, and
    `--check-current` must then pass (the order that script documents:
    content, commit, re-pin, commit). The two registry scripts run with a
    scrubbed environment (no token reaches them), from the checkout's
    default branch, which the registry's own CI already runs. Registry
    writes use the token in `REGISTRY_TOKEN` (the App's), never `GH_TOKEN`
    (this repository's, for the issue path).

`--allow-mode` lists the modes the caller has wired (default: `proposal`
only, the safe one; auto must be named). A gates file in a mode not allowed
exits 2 rather than doing nothing quietly, unless `--fallback-to-proposal`
lowers auto to proposal.

AUTO MODE OPENS NOTHING (exit 0, nothing written) while any loop pull
request is open in agentskills (head `eval-improve/`, `draft-skill/` or
`retire-skill/`; the slot routine-improve-gate.yml's `registry-pr` job
checks): all three write the bundle version, `docs/skill-impact.md` and
`skills.lock`, so a second would conflict once the first merged
(agentskills ADR 0021, decision 5). Nor while a closed pull request
from `retire-skill/<skill>` exists: a person closed it, so the skill is
decided, as a closed issue decides a proposal.

REFUSALS (exit 2, nothing written):

  * any run that is not `--dry-run` outside the scheduled workflow
    (`GITHUB_EVENT_NAME` must be `schedule`): never from a dispatch, a pull
    request or a terminal;
  * in auto mode, any run that is not `--dry-run` outside GitHub Actions
    (`GITHUB_ACTIONS` must be `true`) on the default branch (`GITHUB_REF`
    must be `refs/heads/main`);
  * in auto mode, a shallow agentskills checkout (the lock re-pin and
    its check read the commit the lock pins, which a shallow clone lacks), a
    skill that is its plugin's last (emptying a plugin is a registry
    decision, and the lock generator refuses it), a
    `scripts/plugin_layout.json` that does not list the skill under its
    plugin, a skill whose removal that registry's `plugin-runtime-review`
    gate would hold for the owner although its plugin runs no code (a
    `hooks/` or `bin/` directory in the skill, or another plugin's hook
    naming a path inside it), which auto-merge cannot
    approve, a skill `scripts/skills_waivers.yml` waives a finding under
    (the waiver goes stale with it and `check_skills.py` exits 1), or a
    skill defining a test that a tracked source elsewhere cites by name (the
    registry's citation test would fail on the dangling name);
  * in auto mode, a skill whose basename is not found exactly once across
    every `--registry` checkout given, or whose one copy is not a real
    `plugins/<plugin>/skills/<skill>/` directory of agentskills: none
    of `plugins/`, the plugin, its `skills/`, the skill, its manifests or
    `docs/skill-impact.md` may be a symlink or resolve outside the
    checkout. In proposal mode the issue says so instead, since a human
    removes.

Only agentskills is ever written. The private registry,
agentskills-private, is never a target (agentskills ADR 0021,
decision 7): the scheduled job does not check it out, so a skill there has
no tested digest and never reaches `retire`, and a `--registry` copy found
anywhere but agentskills is refused in auto mode.

`--dry-run` reads (the gates file, the checkouts, and open pull requests or
issues through `gh`), writes nothing, and prints the plan as JSON: the
action, the issue or pull request text, and the commands it would run.

Every GitHub call goes through one of two functions, `gh(args, stdin)` (the
`gh` CLI with `GH_TOKEN`: reads, and skills-evals issues) and
`registry_gh(args, stdin)` (with `REGISTRY_TOKEN`: agentskills
writes), and every push through `push(checkout, branch)`, all injectable,
so tests need no network. An error from `gh` is reported by its exit code
only, never its output. Deterministic: no clock; the skill-impact date is
`--date` (the workflow passes the run's UTC date), else the gates file's
`as_of`.

`--usage` is the published `usage/weekly-local.json`; the issue body names
the skill's invocations over the four weeks ending at the gates file's
`as_of` (harness/skill_usage.weight), or says `unknown` when there is no
fresh census. It informs the human; it changes no decision.

The gate guards against honest mistakes, not forged results (the owner,
2026-10-26): anyone who can write the results can also remove a skill.

GUIDANCE SECTIONS (`--target guidance`, skills-evals#124; the functions
named `guidance_*` below, kept apart from the skill path). The gates file's
`guidance` block and `guidance_retirement_mode` decide, read the same way:

  * a `retire` verdict, re-checked as a skill's is: in `proposal` mode an
    issue in skills-evals (`GUIDANCE_ISSUE_MARKER`); in `auto` mode (the owner:
    removal is automatic, 2026-10-10, and its pull requests auto-merge,
    2026-10-25) a branch `retire-guidance/<section>` in the
    `--guidance PATH` checkout of _agent-guidance that removes the
    section's extent from its file (the whole file for a sections/*.md file
    with no other `##`; for base.md, re-copied to the repository's own
    `.claude/hooks/fleet-guidance.md` payload), deletes its
    `agents-md/eval-coverage.yml` row,
    rewrites the manifest's bytes (`check-guidance-coverage.js
    --write-bytes`), adds a typed `remove` entry to
    `docs/guidance-impact.md` whose `Eval:` line names the fixtures, the
    pooled run count (`n=`) and the policy, and regenerates `AGENTS.md`
    exactly as that repository's own instructions say: managed output with
    its trailing newlines stripped, then everything from the `## Repo-
    specific additions` heading LINE (never the first occurrence of the
    substring, which the managed header quotes); pushed, opened, labeled
    `agent-authored` and armed to auto-merge (merge commit, matched to the
    removal head, read back), one at a time (`retire-guidance/` heads).
    These writes to _agent-guidance go through `registry_gh` and the push
    with `REGISTRY_TOKEN`, as the skill path's do; the issues stay on `gh`.
    With `--fallback-to-proposal`, `auto` mode without `auto` allowed (no
    token reached the run) files the issue instead of exiting 2, and the
    issue says why;
  * a `shorten-or-merge` verdict (the section helps only alone): an issue in
    skills-evals (`SHORTEN_ISSUE_MARKER`) in either mode, never a removal.

One action per evaluation: a `retire` first, else the first
`shorten-or-merge` section by name.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

import compute_gates  # noqa: E402
import delivery  # noqa: E402
import guidance  # noqa: E402
import skill_usage  # noqa: E402
import improve_gate  # noqa: E402

TARGET = "agentskills"
REGISTRY_REPO = "example-adopter/agentskills"
EVALS_REPO = "example-adopter/skills-evals"
BRANCH_PREFIX = "retire-skill/"
#: Every loop pull request's head in agentskills: improvement, new
#: skill, removal. One open at a time across all three (ADR 0021,
#: decision 5; routine-improve-gate.yml's registry-pr job checks the same).
LOOP_PREFIXES = ("eval-improve/", "draft-skill/", BRANCH_PREFIX)
#: The fleet label and marker for a contribution an agent made on the owner's
#: behalf (_agent-guidance ADR 0023), as the improvement gate writes them.
AGENT_LABEL = "agent-authored"
AGENT_MARKER = improve_gate.AGENT_MARKER
AGENT_TRAILER = "Agent: claude-code"
REGISTRY_ADR = improve_gate.REGISTRY_ADR
#: The environment variable holding the agentskills write token (the
#: skills-evals GitHub App's installation token, minted by the workflow).
REGISTRY_TOKEN_ENV = "REGISTRY_TOKEN"
PR_URL_RE = re.compile(rf"https://github\.com/{re.escape(REGISTRY_REPO)}/pull/(\d+)")
GUIDANCE_NO_CREDENTIAL = ("no write credential for _agent-guidance reached this run "
                         "(the GitHub App token was not minted)")
NO_CREDENTIAL = ("no write credential for agentskills reached this run (the "
                 "GitHub App token was not minted)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ISSUE_MARKER = "<!-- skills-evals:retire-skill:{skill} -->"
IMPACT_FILE = "docs/skill-impact.md"
LAYOUT_FILE = "scripts/plugin_layout.json"
README_FILE = "README.md"
LOCK_FILE = "skills.lock"
README_GENERATOR = "scripts/generate_readme_table.py"
LOCK_GENERATOR = "scripts/generate_skills_lock.py"
WAIVERS_FILE = "scripts/skills_waivers.yml"
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")
ADR = ("https://github.com/example-adopter/skills-evals/blob/main/docs/decisions/"
       "0014-removal-pull-requests-merge-without-review.md")
COMMIT_NAME = "github-actions[bot]"
ISSUE_AUTHOR = "github-actions[bot]"
DEFAULT_REF = "refs/heads/main"
COMMIT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# A fixture directory or a roster model id: no space, `|` or newline, so a
# value can never break the evidence table or mention anyone.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@\[\]/-]{0,127}$")


class RetireError(Exception):
    """A refusal: nothing is written."""


def default_gh(args: list, stdin: str | None = None, token: str | None = None) -> str:
    """Run `gh` and return its stdout; its output is never echoed on failure,
    since an API error body can carry data a public log must not. `token`,
    when given, replaces GH_TOKEN for this call only."""
    env = {**os.environ, "GH_TOKEN": token} if token else None
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True,
                          text=True, check=False, env=env)
    if proc.returncode != 0:
        raise RetireError(f"gh {args[0]} exited {proc.returncode}")
    return proc.stdout


def _auth_env(token: str) -> dict:
    """git's environment with `token` as an HTTP header for github.com, set
    through GIT_CONFIG_* so it never appears in an argv or .git/config."""
    header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {**os.environ, "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {header}",
            "GIT_TERMINAL_PROMPT": "0"}


def default_push(checkout: Path, branch: str, token: str | None = None) -> None:
    """Push HEAD to `branch`, then prove it landed: a refused commit can
    still leave `git push` reporting success, so the pushed ref is fetched
    back and HEAD must be its ancestor (the fleet's push rule)."""
    if not token:
        raise RetireError(f"no {REGISTRY_TOKEN_ENV} to push with")
    env = _auth_env(token)
    for args in (["push", "origin", f"HEAD:refs/heads/{branch}"],
                 ["fetch", "-q", "--no-tags", "origin",
                  f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
                 ["merge-base", "--is-ancestor", "HEAD", f"refs/remotes/origin/{branch}"]):
        proc = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True,
                              text=True, check=False, env=env)
        if proc.returncode != 0:
            raise RetireError(f"git {args[0]} exited {proc.returncode}"
                              + (": the pushed branch does not hold the removal commit"
                                 if args[0] == "merge-base" else ""))


def _git(checkout: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(checkout), *args],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RetireError(f"git {args[0]} exited {proc.returncode}")
    return proc.stdout


def check_event(env) -> None:
    if env.get("GITHUB_EVENT_NAME") != "schedule":
        raise RetireError("refusing: only the scheduled workflow acts on a retire "
                          f"verdict (GITHUB_EVENT_NAME is {env.get('GITHUB_EVENT_NAME')!r})")


def check_auto_env(env) -> None:
    if env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_REF") != DEFAULT_REF:
        raise RetireError("refusing: auto mode acts only in GitHub Actions on "
                          f"{DEFAULT_REF} (GITHUB_ACTIONS is "
                          f"{env.get('GITHUB_ACTIONS')!r}, GITHUB_REF is "
                          f"{env.get('GITHUB_REF')!r})")


def _names(value) -> bool:
    """A non-empty list of distinct NAME_RE strings."""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, str) and NAME_RE.fullmatch(v) for v in value)
            and len(set(value)) == len(value))


def load_gates(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RetireError(f"cannot read the gates file ({type(exc).__name__})") from None
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 \
            or not isinstance(doc.get("skills"), dict) \
            or not _names(doc.get("roster_models")) \
            or not isinstance(doc.get("as_of"), str) \
            or not re.fullmatch(r"\d{8}T\d{6}Z", doc["as_of"]):
        raise RetireError("the gates file is not compute_gates.py schema 1")
    return doc


def mode_of(gates: dict) -> str:
    return "auto" if gates.get("retirement_mode") == "auto" else "proposal"


def retiring(gates: dict) -> str | None:
    """The one skill whose verdict is `retire`, re-checked; None when none.
    RetireError for two, or for a `retire` the cells do not support."""
    names = sorted(n for n, v in gates["skills"].items()
                   if isinstance(v, dict) and v.get("verdict") == "retire")
    if not names:
        return None
    if len(names) > 1:
        raise RetireError(f"{len(names)} skills have a retire verdict; compute_gates "
                          "allows one per evaluation")
    skill, verdict = names[0], gates["skills"][names[0]]
    if not SKILL_RE.fullmatch(skill):
        raise RetireError("the retiring skill's name is not a skill basename")
    if not isinstance(verdict.get("reason"), str):
        raise RetireError(f"{skill}: a retire verdict whose reason is not text")
    cells = verdict.get("cells")
    roster = gates["roster_models"]
    if not isinstance(cells, list) or not cells or not _names(roster):
        raise RetireError(f"{skill}: a retire verdict with no cell")
    for c in cells:
        if not isinstance(c, dict) or c.get("class") != "no-benefit":
            raise RetireError(f"{skill}: a retire verdict with a cell that is not "
                              "no-benefit")
        fixture = c.get("fixture")
        if not (fixture is None or isinstance(fixture, str) and NAME_RE.fullmatch(fixture)) \
                or not isinstance(c.get("model"), str) \
                or not isinstance(c.get("looks"), list):
            raise RetireError(f"{skill}: a retire verdict with a malformed cell")
    pairs = [(c["fixture"], c["model"]) for c in cells]
    fixtures = {f for f, _ in pairs}
    want = {(f, m) for f in fixtures for m in roster}
    if len(set(pairs)) != len(pairs) or set(pairs) != want:
        raise RetireError(f"{skill}: the retire verdict's cells are not each fixture "
                          "crossed with each roster model exactly once (the cross product)")
    return skill


def locate(registries: dict, skill: str) -> dict:
    """{"plugin", "path"} of the one copy of `skill`, in agentskills;
    RetireError otherwise."""
    if TARGET not in registries:
        raise RetireError(f"no {TARGET} checkout was given (--registry {TARGET}=PATH)")
    try:
        matches = delivery._registry_matches(registries, None, skill)
    except delivery.DeliveryError:
        raise RetireError(f"{skill}: a copy escapes its checkout") from None
    root = Path(registries[TARGET]["path"])
    dirs = sorted(p for p in root.glob(f"plugins/*/skills/{skill}") if p.exists()
                  or p.is_symlink())
    if len(matches) != 1 or len(dirs) != 1:
        raise RetireError(f"{skill}: basename found {len(matches)} time(s) across the "
                          f"registries given, not exactly once")
    identity, plugin, directory = matches[0]
    if directory != dirs[0] or not directory.is_relative_to(root):
        raise RetireError(f"{skill}: its one copy is not in {TARGET}")
    if directory.is_symlink():
        raise RetireError(f"{skill}: its copy is a symlink")
    if not directory.is_dir():
        raise RetireError(f"{skill}: its copy is not a real directory")
    rel = directory.relative_to(root).as_posix()
    if rel != f"plugins/{plugin}/skills/{skill}":
        raise RetireError(f"{skill}: unexpected location {rel}")
    contained(root, ["plugins", f"plugins/{plugin}", f"plugins/{plugin}/skills", rel])
    return {"plugin": plugin, "path": rel}


def contained(root: Path, rels) -> None:
    """RetireError unless each of `rels` under `root` is no symlink and
    resolves inside `root`, so no write follows a link out of the checkout."""
    base = root.resolve()
    for rel in rels:
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise RetireError(f"{rel} is a symlink or resolves outside the {TARGET} "
                              "checkout")


def _json_lines(text: str) -> list:
    out = []
    for line in text.splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                out.append(item)
    return out


def open_loop_prs(gh) -> list:
    """Open agentskills pull requests whose head is any loop's."""
    text = gh(["api", "--paginate",
               f"repos/{REGISTRY_REPO}/pulls?state=open&per_page=100",
               "--jq", ".[] | {number: .number, head: .head.ref, url: .html_url}"])
    return [p for p in _json_lines(text)
            if isinstance(p.get("head"), str) and p["head"].startswith(LOOP_PREFIXES)]


def closed_removal_prs(gh, skill: str) -> list:
    """Closed agentskills pull requests from `retire-skill/<skill>`."""
    owner = REGISTRY_REPO.split("/")[0]
    text = gh(["api", "--paginate",
               f"repos/{REGISTRY_REPO}/pulls?state=closed&per_page=100"
               f"&head={owner}:{BRANCH_PREFIX}{skill}",
               "--jq", ".[] | {number: .number, head: .head.ref, url: .html_url}"])
    return [p for p in _json_lines(text) if p.get("head") == f"{BRANCH_PREFIX}{skill}"]


def runtime_refusal(checkout: Path, plugin: str) -> str | None:
    """improve_gate.py's `auto_refusal`: why `plugin` runs code, or None."""
    try:
        return improve_gate.auto_refusal(checkout, plugin)
    except improve_gate.Rejected as exc:
        raise RetireError(str(exc)) from None


def existing_issues(gh, skill: str) -> dict:
    """{"open": [...], "closed": [...]}: the numbers, ascending, of every
    issue, in any state, that github-actions[bot] opened with this skill's
    marker. An issue anyone else opened is ignored, whatever it quotes."""
    text = gh(["api", "--paginate",
               f"repos/{EVALS_REPO}/issues?state=all&per_page=100",
               "--jq", ".[] | select(.pull_request == null) | "
                       "{number: .number, body: (.body // \"\"), "
                       "author: .user.login, state: .state}"])
    marker = ISSUE_MARKER.format(skill=skill)
    found = {"open": [], "closed": []}
    for item in _json_lines(text):
        if item.get("author") != ISSUE_AUTHOR or not isinstance(item.get("body"), str) \
                or marker not in item["body"]:
            continue
        number, state = item.get("number"), item.get("state")
        if type(number) is not int or number < 1 or state not in found:
            raise RetireError("an issue record from the API is malformed")
        found[state].append(number)
    return {state: sorted(numbers) for state, numbers in found.items()}


def _figure(block, field):
    if not isinstance(block, dict) or not isinstance(block.get(field), (int, float)):
        return "n/a"
    return f"{block[field]:.3f}"


def evidence(gates: dict, skill: str) -> str:
    """A Markdown table of the verdict's cells."""
    rows = ["| Fixture | Model | Runs with | Runs without | Objective upper | "
            "Judge upper | Looks |", "|---|---|---|---|---|---|---|"]
    for cell in gates["skills"][skill]["cells"]:
        arms = [cell.get(a) if isinstance(cell.get(a), dict) else {}
                for a in ("with", "without")]
        looks = ", ".join(str(look.get("n")) for look in cell.get("looks") or []
                          if isinstance(look, dict))
        rows.append(f"| {cell.get('fixture') or '(flat)'} | {cell.get('model')} | "
                    f"{arms[0].get('runs', 'n/a')} | {arms[1].get('runs', 'n/a')} | "
                    f"{_figure(cell.get('objective'), 'retire_upper')} | "
                    f"{_figure(cell.get('judge'), 'retire_upper')} | {looks} |")
    return "\n".join(rows)


def _date(as_of: str) -> str:
    return f"{as_of[0:4]}-{as_of[4:6]}-{as_of[6:8]}"


def _runs(cells) -> int:
    return sum(c[arm]["runs"] for c in cells for arm in ("with", "without")
               if isinstance(c.get(arm), dict) and type(c[arm].get("runs")) is int)


def impact_entry(gates: dict, skill: str, plugin: str, run_url: str | None,
                 versions: tuple, date: str | None = None) -> str:
    """The registry's entry format (docs/skill-impact.md, "Entry format"):
    the date the removal is opened, one line each, links in full since the
    file lives in another repository."""
    cells = gates["skills"][skill]["cells"]
    fixtures = sorted({c.get("fixture") or "(flat)" for c in cells})
    paths = [f"evals/{skill}" if f == "(flat)" else f"evals/{skill}/{f}" for f in fixtures]
    where = f"workflow run {run_url}" if run_url else "its scheduled workflow"
    return (f"## {date or _date(gates['as_of'])} — {plugin}/{skill} — remove\n\n"
            f"- Motivation: the retirement verdict is `retire` "
            f"([skills-evals ADR 0014](https://github.com/{EVALS_REPO}/blob/main/docs/decisions/0014-removal-pull-requests-merge-without-review.md)): every cell "
            f"({len(fixtures)} fixture(s) x {len(gates['roster_models'])} roster model(s)) "
            f"shows no benefit at two fixed looks.\n"
            f"- Change: `plugins/{plugin}/skills/{skill}/` removed, the skill dropped from "
            f"`{LAYOUT_FILE}` and the README table, bundle `{plugin}` {versions[0]} -> "
            f"{versions[1]} (a removal is a breaking change), by skills-evals' "
            f"`scripts/retire_skill.py` ({where}).\n"
            f"- Eval: n={_runs(cells)} pooled arm runs (with vs without the skill) as of "
            f"{gates['as_of']}, fixtures {', '.join(paths)}, "
            f"policy evals/gate-policy.yml; per-cell bounds in the pull request.\n"
            f"- Outcome: pending merge; auto-merges once required checks pass, with no "
            f"human review ([ADR 0021]({REGISTRY_ADR})).\n")


def insert_entry(text: str, entry: str) -> str:
    """`entry` newest first: right after the header's `---` rule."""
    lines = text.split("\n")
    try:
        rule = lines.index("---")
    except ValueError:
        raise RetireError(f"{IMPACT_FILE} has no `---` rule to insert after") from None
    head = "\n".join(lines[:rule + 1]).rstrip("\n")
    rest = "\n".join(lines[rule + 1:]).lstrip("\n")
    return f"{head}\n\n{entry}\n{rest}"


def bump(version: str) -> str:
    """The next MAJOR version: a removal breaks every consumer that calls the
    skill. check_plugin_versions.py asks only that it rise; the registry
    bumped major for its last skill removal (sync-skills, 1.1.0 -> 2.0.0)."""
    match = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise RetireError("a plugin version is not MAJOR.MINOR.PATCH")
    return f"{int(match.group(1)) + 1}.0.0"


def manifest_versions(checkout: Path, plugin: str) -> dict:
    """{manifest path: (old, new)}; both manifests must agree."""
    out = {}
    contained(checkout, [f"plugins/{plugin}/.claude-plugin",
                         *(f"plugins/{plugin}/{name}" for name in MANIFESTS), "docs",
                         IMPACT_FILE])
    for name in MANIFESTS:
        rel = f"plugins/{plugin}/{name}"
        try:
            doc = json.loads((checkout / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise RetireError(f"cannot read {rel}") from None
        version = doc.get("version") if isinstance(doc, dict) else None
        out[rel] = (version, bump(version))
    if len({old for old, _ in out.values()}) != 1:
        raise RetireError(f"{plugin}'s two manifests disagree on its version")
    return out


def usage_line(usage: dict | None, skill: str, as_of: str) -> str:
    """One line on how often `skill` was invoked lately, for the human who
    decides; `unknown` when there is no fresh usage census at `as_of`."""
    now = datetime.strptime(as_of, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    count = skill_usage.weight(usage, skill, now)
    if count is None:
        return "Recent use: unknown (no fresh usage census)."
    return (f"Recent use: {count} local invocation(s) in the four weeks to {as_of} "
            "(usage/weekly-local.json).")


def layout_without(checkout: Path, plugin: str, skill: str) -> str:
    """`scripts/plugin_layout.json`'s text with `skill` dropped from
    `plugin`, in the registry's canonical form (json.dumps, indent 2, one
    trailing newline). RetireError unless the file lists the skill under the
    plugin, or when the skill is the plugin's last."""
    contained(checkout, ["scripts", LAYOUT_FILE])
    try:
        doc = json.loads((checkout / LAYOUT_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RetireError(f"cannot read {LAYOUT_FILE}") from None
    plugins = doc.get("plugins") if isinstance(doc, dict) else None
    entry = plugins.get(plugin) if isinstance(plugins, dict) else None
    skills = entry.get("skills") if isinstance(entry, dict) else None
    if not isinstance(skills, list) or skills.count(skill) != 1:
        raise RetireError(f"{LAYOUT_FILE} does not list {skill} once under {plugin}")
    if len(skills) == 1:
        raise RetireError(f"{skill} is {plugin}'s last skill; emptying a plugin is a "
                          "registry decision, not a retirement")
    entry["skills"] = [s for s in skills if s != skill]
    return json.dumps(doc, indent=2) + "\n"


def lock_relevant(checkout: Path, plugin: str) -> bool:
    """Whether `skills.lock` locks `plugin`, so removing one of its skills
    makes the lock stale (agentskills CI's `--check-current`)."""
    contained(checkout, [LOCK_FILE])
    path = checkout / LOCK_FILE
    if not path.exists():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RetireError(f"cannot read {LOCK_FILE}") from None
    bundles = doc.get("bundles") if isinstance(doc, dict) else None
    skills = doc.get("skills") if isinstance(doc, dict) else None
    if not isinstance(bundles, list) or not isinstance(skills, dict):
        raise RetireError(f"{LOCK_FILE} has no bundles list or skills map")
    return plugin in bundles or any(isinstance(k, str) and k.startswith(f"{plugin}/")
                                    for k in skills)


def check_registry(checkout: Path, location: dict, skill: str) -> None:
    """The auto-mode preconditions on the checkout itself; RetireError
    otherwise. Read-only, so a dry run refuses the same way."""
    if _git(checkout, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise RetireError(f"the {TARGET} checkout is shallow; the lock re-pin needs "
                          "full history (fetch-depth: 0)")
    plugin = location["plugin"]
    skills_dir = checkout / "plugins" / plugin / "skills"
    others = [p for p in skills_dir.iterdir() if p.name != skill
              and (p.is_dir() or p.is_symlink())]
    if not others:
        raise RetireError(f"{skill} is {plugin}'s last skill; emptying a plugin is a "
                          "registry decision, not a retirement")
    layout_without(checkout, plugin, skill)
    contained(checkout, [README_FILE, README_GENERATOR, LOCK_GENERATOR])
    for rel in (README_FILE, README_GENERATOR):
        if not (checkout / rel).is_file():
            raise RetireError(f"{TARGET} has no {rel}")
    if lock_relevant(checkout, plugin) and not (checkout / LOCK_GENERATOR).is_file():
        raise RetireError(f"{TARGET} has no {LOCK_GENERATOR}")
    runtime_gated(checkout, location["path"], skill)
    waived_paths(checkout, location["path"], skill)
    dangling_citations(checkout, location["path"], skill)


TEST_NAME_RE = re.compile(r"\btest_[a-z0-9_]+")


def _defined_tests(checkout: Path, rels) -> set:
    """Function names (parsed, never grepped) and module stems of `rels`."""
    names = set()
    for rel in rels:
        if not rel.endswith(".py"):
            continue
        names.add(Path(rel).stem)
        try:
            tree = ast.parse((checkout / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            raise RetireError(f"cannot parse {rel}") from None
        names |= {node.name for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {n for n in names if n.startswith("test_")}


def dangling_citations(checkout: Path, path: str, skill: str) -> None:
    """RetireError when a tracked source outside the skill cites, by name, a
    test that only the skill defines. agentskills'
    test_every_test_this_repo_cites_by_name_exists scans every tracked
    non-test `*.py` and `*.sh` for `test_...` tokens and fails on one no
    tracked module defines, so removing the skill would turn that citation
    into a failing required check. Rewording someone's comment is not a
    removal's job; a human does that."""
    inside = _git(checkout, "ls-files", "-z", "--", path).split("\0")
    gone = _defined_tests(checkout, [r for r in inside if r])
    if not gone:
        return
    outside = [r for r in _git(checkout, "ls-files", "-z", "--", "*.py", "*.sh")
               .split("\0") if r and not (r == path or r.startswith(f"{path}/"))]
    gone -= _defined_tests(checkout, outside)
    for rel in outside:
        if Path(rel).name.startswith("test_"):
            continue
        try:
            text = (checkout / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise RetireError(f"cannot read {rel}") from None
        cited = sorted(set(TEST_NAME_RE.findall(text)) & gone)
        if cited:
            raise RetireError(f"{rel} cites {cited[0]}, a test only {skill} defines; "
                              "removing the skill leaves that citation dangling, which "
                              f"{TARGET}' test_every_test_this_repo_cites_by_name_exists "
                              "fails on")


def waived_paths(checkout: Path, path: str, skill: str) -> None:
    """RetireError when `scripts/skills_waivers.yml` waives a finding inside
    the skill: that waiver matches nothing once the skill is gone, and
    check_skills.py reports a `stale-waiver` and exits 1, so the pull request
    could never merge. Removing the waiver with the skill means editing a
    commented YAML file, which this does not do; a human does that. The
    file is parsed, never grepped — its comments name skills
    and would read as waivers."""
    contained(checkout, [WAIVERS_FILE])
    file = checkout / WAIVERS_FILE
    if not file.exists():
        return
    try:
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise RetireError(f"cannot read {WAIVERS_FILE}") from None
    waivers = (doc or {}).get("waivers") if isinstance(doc, dict) else None
    if waivers is None:
        waivers = []
    if not isinstance(waivers, list):
        raise RetireError(f"{WAIVERS_FILE}: waivers is not a list")
    for waiver in waivers:
        where = waiver.get("path") if isinstance(waiver, dict) else None
        if isinstance(where, str) and (where == path or where.startswith(f"{path}/")):
            raise RetireError(f"{WAIVERS_FILE} waives a finding under {skill}; that "
                              "waiver goes stale when the skill does and check_skills.py "
                              "would exit 1, so a human retires the waiver with the skill")


def runtime_gated(checkout: Path, path: str, skill: str) -> None:
    """RetireError when deleting this skill would be a plugin RUNTIME change
    in agentskills' sense (its ADR 0016 gate): a `hooks/` or `bin/`
    directory inside the skill, or a hook naming a path inside it. Such a
    pull request waits on the `plugin-runtime-review` environment for the
    owner's approval, which auto-merge cannot supply, so the branch would sit
    open forever and hold the one removal slot. No skill ships either today;
    this refuses the day one does, rather than wedging the queue."""
    for name in ("hooks", "bin"):
        if (checkout / path / name).exists():
            raise RetireError(f"{skill} ships a {name}/ directory, so removing it is a "
                              "plugin runtime change: agentskills' "
                              "plugin-runtime-review gate holds such a pull request for "
                              "the owner, and auto-merge cannot approve it")
    needle = f"skills/{skill}/"
    for config in sorted(checkout.glob("plugins/*/hooks/*.json")) + [
            checkout / f"plugins/{p}/{m}" for p in
            sorted(d.name for d in (checkout / "plugins").iterdir() if d.is_dir())
            for m in MANIFESTS]:
        if config.is_symlink() or not config.is_file():
            continue
        try:
            text = config.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise RetireError(f"cannot read {config.relative_to(checkout).as_posix()}") \
                from None
        if needle in text:
            raise RetireError(
                f"{config.relative_to(checkout).as_posix()} names a path inside {skill}, "
                "so removing it is a plugin runtime change: agentskills' "
                "plugin-runtime-review gate holds such a pull request for the owner, and "
                "auto-merge cannot approve it")


def run_registry_script(checkout: Path, script: str, *args: str) -> None:
    """Run one of the registry's own scripts in its checkout, with a scrubbed
    environment: no token or other secret from this workflow reaches it, and
    no bytecode is written into the checkout. Reported by exit code only."""
    contained(checkout, ["scripts", script])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "HOME": os.environ.get("HOME", "/nonexistent"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    # Each target is spelled literally at its call so the suite's spawn audit
    # (run_tests.py, S-B-a-2) can prove it is not the test runner.
    if script == README_GENERATOR:
        proc = subprocess.run([sys.executable, "scripts/generate_readme_table.py", *args],
                              cwd=str(checkout), env=env, capture_output=True, text=True,
                              check=False)
    elif script == LOCK_GENERATOR:
        proc = subprocess.run([sys.executable, "scripts/generate_skills_lock.py", *args],
                              cwd=str(checkout), env=env, capture_output=True, text=True,
                              check=False)
    else:
        raise RetireError(f"{script} is not a registry script this tool runs")
    if proc.returncode != 0:
        raise RetireError(" ".join([script, *args, "exited", str(proc.returncode)]))


def _issue_body(gates, skill, location, problem, run_url, usage=None,
                mode_reason=None) -> str:
    where = (f"`{location['path']}` in {REGISTRY_REPO}" if location
             else f"not located: {problem}")
    run = f"\nWorkflow run: {run_url}\n" if run_url else ""
    why = (f"`evals/gate-policy.yml` sets `retirement_mode: auto`, but {mode_reason}, "
           "so no removal pull request is opened and a human removes it"
           if mode_reason else
           "`evals/gate-policy.yml` sets `retirement_mode: proposal`, so a human "
           "removes it")
    return (f"{ISSUE_MARKER.format(skill=skill)}\n"
            f"The retirement verdict for `{skill}` is **retire** as of "
            f"{gates['as_of']}: {gates['skills'][skill].get('reason')}.\n\n"
            f"{usage_line(usage, skill, gates['as_of'])}\n\n"
            f"{why}. Copy: {where}.\n\n{evidence(gates, skill)}\n{run}\n"
            "Filed by scripts/retire_skill.py on the scheduled workflow (#70).\n")


def _pr_body(gates, skill, location, versions, run_url, repin=False) -> str:
    bumps = "\n".join(f"- `{p}`: {old} -> {new}" for p, (old, new) in versions.items())
    lock = (f" A second commit re-pins `{LOCK_FILE}` to the removal commit."
            if repin else "")
    run = f"\nWorkflow run: {run_url}\n" if run_url else ""
    return (f"Removes `{location['path']}`: its retirement verdict in "
            f"[skills-evals ADR 0014](https://github.com/{EVALS_REPO}/blob/main/docs/decisions/0014-removal-pull-requests-merge-without-review.md) is "
            f"**retire** as of {gates['as_of']} "
            f"({gates['skills'][skill].get('reason')}).\n\n{evidence(gates, skill)}\n\n"
            f"Version bumps:\n{bumps}\n\nAdds a `{IMPACT_FILE}` entry, drops the skill "
            f"from `{LAYOUT_FILE}` and regenerates the `{README_FILE}` skill table."
            f"{lock}\n\n"
            f"This pull request auto-merges (merge commit) once required checks pass, "
            f"with no human review, by the owner's 2026-10-25 decision "
            f"([skills-evals ADR 0014]({ADR}); [ADR 0021]({REGISTRY_ADR})). To decline "
            f"the removal, close it: a closed `{BRANCH_PREFIX}{skill}` pull request means "
            f"the skill is decided, and none is opened again.\n"
            f"{run}\nOpened by skills-evals' scripts/retire_skill.py on its scheduled "
            f"workflow.\n\n{AGENT_MARKER}\n")


def _lowered(mode_reason: str, allowed) -> str:
    """`proposal`, the mode auto falls back to; RetireError when the caller
    has not allowed it either."""
    if "proposal" not in allowed:
        raise RetireError(f"retirement_mode auto falls back to proposal ({mode_reason}), "
                          f"which this caller has not wired (allowed: {', '.join(allowed)})")
    return "proposal"


def plan(gates: dict, registries: dict, gh, run_url: str | None = None,
         allowed=("proposal",), usage: dict | None = None, fallback: bool = False,
         date: str | None = None) -> dict:
    """What to do, read-only. RetireError for a refusal."""
    skill = retiring(gates)
    mode = mode_of(gates)
    if skill is None:
        return {"action": "none", "mode": mode, "reason": "no skill has a retire verdict"}
    mode_reason = None
    if mode == "auto" and "auto" not in allowed and fallback:
        mode_reason = NO_CREDENTIAL
        mode = _lowered(mode_reason, allowed)
    if mode not in allowed:
        raise RetireError(f"retirement_mode is {mode}, which this caller has not wired "
                          f"(allowed: {', '.join(allowed)})")
    if mode == "auto":
        location = locate(registries, skill)
        checkout = Path(registries[TARGET]["path"])
        mode_reason = runtime_refusal(checkout, location["plugin"])
        if mode_reason:
            mode = _lowered(mode_reason, allowed)
    if mode == "proposal":
        try:
            location, problem = locate(registries, skill), None
        except RetireError as exc:
            location, problem = None, str(exc)
        issues = existing_issues(gh, skill)
        lowered = {"mode_reason": mode_reason} if mode_reason else {}
        if not issues["open"] and issues["closed"]:
            return {"action": "none", "mode": mode, "skill": skill, **lowered,
                    "issue": issues["closed"][0],
                    "reason": f"{skill} is already decided: issue "
                              f"#{issues['closed'][0]} is closed, so none is filed again"}
        number = issues["open"][0] if issues["open"] else None
        return {"action": "update-issue" if number else "create-issue", "mode": mode,
                "skill": skill, "issue": number, **lowered,
                "title": f"Retire skill {skill}: no benefit on every roster model (#70)",
                "body": _issue_body(gates, skill, location, problem, run_url, usage, mode_reason)}
    prs = open_loop_prs(gh)
    if prs:
        return {"action": "none", "mode": mode, "skill": skill,
                "reason": "a loop pull request is already open in "
                          f"{REGISTRY_REPO}, so this removal waits for it: "
                          + ", ".join(str(p.get("url")) for p in prs)}
    closed = closed_removal_prs(gh, skill)
    if closed:
        return {"action": "none", "mode": mode, "skill": skill,
                "reason": f"{skill} is already decided: removal pull request "
                          f"{closed[0].get('url')} is closed, so none is opened again"}
    versions = manifest_versions(checkout, location["plugin"])
    check_registry(checkout, location, skill)
    repin = lock_relevant(checkout, location["plugin"])
    entry = impact_entry(gates, skill, location["plugin"], run_url,
                         next(iter(versions.values())), date)
    return {"action": "removal", "mode": mode, "skill": skill, **location,
            "branch": f"{BRANCH_PREFIX}{skill}", "versions": versions,
            "impact_entry": entry, "repin": repin,
            "title": f"Remove {location['plugin']}/{skill}: no benefit (skills-evals#70)",
            "body": _pr_body(gates, skill, location, versions, run_url, repin)}


def prepare_branch(checkout: Path, action: dict) -> None:
    """The removal commit, and the lock re-pin when the plugin is locked, on
    a new branch of `checkout`, which must be clean."""
    if _git(checkout, "status", "--porcelain"):
        raise RetireError(f"the {TARGET} checkout has uncommitted changes")
    contained(checkout, ["plugins", f"plugins/{action['plugin']}",
                         f"plugins/{action['plugin']}/skills", action["path"],
                         f"plugins/{action['plugin']}/.claude-plugin", *action["versions"],
                         "docs", IMPACT_FILE])
    check_registry(checkout, action, action["skill"])
    layout = layout_without(checkout, action["plugin"], action["skill"])
    _git(checkout, "checkout", "-b", action["branch"])
    _git(checkout, "rm", "-r", "-q", "--", action["path"])
    for rel, (old, new) in action["versions"].items():
        path = checkout / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("version") != old:
            raise RetireError(f"{rel} changed under the plan")
        text = path.read_text(encoding="utf-8")
        needle = f'"version": "{old}"'
        if text.count(needle) != 1:
            raise RetireError(f"{rel}: its version is not written once as {needle}")
        path.write_text(text.replace(needle, f'"version": "{new}"'), encoding="utf-8")
    impact = checkout / IMPACT_FILE
    try:
        text = impact.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise RetireError(f"cannot read {IMPACT_FILE}") from None
    impact.write_text(insert_entry(text, action["impact_entry"]), encoding="utf-8")
    (checkout / LAYOUT_FILE).write_text(layout, encoding="utf-8")
    run_registry_script(checkout, README_GENERATOR)
    _git(checkout, "add", "--", IMPACT_FILE, LAYOUT_FILE, README_FILE, *action["versions"])
    _commit(checkout,
            f"Remove {action['plugin']}/{action['skill']}: no benefit on every roster model\n\n"
            "skills-evals#70's scheduled retirement found no benefit in every cell at "
            "two fixed looks.")
    if action.get("repin"):
        run_registry_script(checkout, LOCK_GENERATOR, "--repin")
        run_registry_script(checkout, LOCK_GENERATOR, "--check-current")
        _git(checkout, "add", "--", LOCK_FILE)
        _commit(checkout, f"Re-pin skills.lock after removing {action['plugin']}/"
                          f"{action['skill']}\n\n"
                          "The lock pinned a commit that still has the skill, so its "
                          "--check-current would fail; it now pins the removal commit.")
    if _git(checkout, "status", "--porcelain"):
        raise RetireError(f"the {TARGET} checkout has changes the removal did not commit")


def _commit(checkout: Path, message: str) -> None:
    """A commit by the bot identity, with the fleet's agent trailer (as the
    improvement gate's commits carry it)."""
    _git(checkout, "-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
         "commit", "-q", "-m", f"{message}\n\n{AGENT_TRAILER}")


def commands(action: dict) -> list:
    """The writes `execute` makes, for a dry run."""
    kind = action["action"]
    if kind == "create-issue":
        return [["gh", "issue", "create", "-R", EVALS_REPO, "--title", action["title"],
                 "--body-file", "-"]]
    if kind == "update-issue":
        return [["gh", "issue", "edit", str(action["issue"]), "-R", EVALS_REPO,
                 "--body-file", "-"]]
    if kind == "removal":
        return [["git", "checkout", "-b", action["branch"]],
                ["git", "rm", "-r", "--", action["path"]],
                *[["edit", rel, f"version {old} -> {new}"]
                  for rel, (old, new) in action["versions"].items()],
                ["edit", IMPACT_FILE, "insert the entry"],
                ["edit", LAYOUT_FILE, f"drop {action['skill']}"],
                ["python3", README_GENERATOR],
                ["git", "commit"],
                *([["python3", LOCK_GENERATOR, "--repin"],
                   ["python3", LOCK_GENERATOR, "--check-current"],
                   ["git", "commit"]] if action.get("repin") else []),
                ["git", "push", "origin", f"HEAD:refs/heads/{action['branch']}"],
                ["git", "merge-base", "--is-ancestor", "HEAD",
                 f"refs/remotes/origin/{action['branch']}"],
                ["gh", "pr", "create", "-R", REGISTRY_REPO, "--base", "main",
                 "--head", action["branch"], "--title", action["title"], "--body-file", "-"],
                ["gh", "api", "-X", "POST",
                 f"repos/{REGISTRY_REPO}/issues/<new pull request number>/labels",
                 "-f", f"labels[]={AGENT_LABEL}"],
                ["gh", "pr", "merge", "<new pull request url>", "-R", REGISTRY_REPO,
                 "--auto", "--merge", "--match-head-commit", "<removal head sha>"],
                ["gh", "pr", "view", "<new pull request url>", "-R", REGISTRY_REPO,
                 "--json", "autoMergeRequest"]]
    return []


def execute(action: dict, registries: dict, gh, push, env, registry_gh=None) -> str:
    """Make the plan's writes: skills-evals issues through `gh`,
    agentskills writes through `registry_gh` (default `gh`)."""
    registry_gh = registry_gh or gh
    kind = action["action"]
    if kind == "none":
        return action["reason"]
    if kind == "create-issue":
        gh(commands(action)[0][1:], action["body"])
        return f"filed an issue for {action['skill']}"
    if kind == "update-issue":
        gh(commands(action)[0][1:], action["body"])
        return f"updated issue #{action['issue']} for {action['skill']}"
    check_auto_env(env)
    checkout = Path(registries[TARGET]["path"])
    prepare_branch(checkout, action)
    head = _git(checkout, "rev-parse", "HEAD").strip()
    push(checkout, action["branch"])
    url = registry_gh(["pr", "create", "-R", REGISTRY_REPO, "--base", "main",
                       "--head", action["branch"], "--title", action["title"],
                       "--body-file", "-"], action["body"]).strip()
    match = PR_URL_RE.fullmatch(url)
    if not match:
        raise RetireError("creating the pull request printed no pull request URL")
    # Labeled before auto-merge: a pull request that could not be marked as
    # the loop's stays open for a person instead of merging.
    registry_gh(["api", "-X", "POST", f"repos/{REGISTRY_REPO}/issues/{match.group(1)}/labels",
                 "-f", f"labels[]={AGENT_LABEL}"])
    registry_gh(["pr", "merge", url, "-R", REGISTRY_REPO, "--auto", "--merge",
                 "--match-head-commit", head])
    method = registry_gh(["pr", "view", url, "-R", REGISTRY_REPO, "--json",
                          "autoMergeRequest", "--jq",
                          '.autoMergeRequest.mergeMethod // ""']).strip()
    if method != "MERGE":
        raise RetireError(f"auto-merge (merge commit) is not enabled on {url}")
    return f"opened {url} with auto-merge"


# ---------------------------------------------------------------------------
# Guidance sections (skills-evals#124): `--target guidance`. Kept apart from
# the skill path above; the two share only the gh/push plumbing, the issue
# lookup's shape and `insert_entry`'s neighbor `_date`.

GUIDANCE_REPO = "example-adopter/_agent-guidance"
GUIDANCE_BRANCH_PREFIX = "retire-guidance/"
GUIDANCE_ISSUE_MARKER = "<!-- skills-evals:retire-guidance:{section} -->"
SHORTEN_ISSUE_MARKER = "<!-- skills-evals:shorten-guidance:{section} -->"
GUIDANCE_MANIFEST = "agents-md/eval-coverage.yml"
GUIDANCE_IMPACT = "docs/guidance-impact.md"
GUIDANCE_AGENTS = "AGENTS.md"
#: _agent-guidance's own fleet-memory payload: a hand-made byte copy of
#: base.md that nothing syncs (its scripts/check-fleet-guidance-mirror.js).
GUIDANCE_PAYLOAD = ".claude/hooks/fleet-guidance.md"
#: The heading LINE AGENTS.md is split on, never a substring of a line: the
#: managed block's own first line quotes it (_agent-guidance AGENTS.md).
REPO_SPECIFIC_LINE = "## Repo-specific additions"
DATED_HEADING_RE = re.compile(r"^\d{4}-\d{2}-\d{2} — ")


def guidance_mode(gates: dict) -> str:
    return "auto" if gates.get("guidance_retirement_mode") == "auto" else "proposal"


def guidance_block(gates: dict) -> dict:
    block = gates.get("guidance")
    if not isinstance(block, dict):
        raise RetireError("the gates file has no guidance block (a policy without "
                          "guidance:, or a compute_gates.py from before #124)")
    return block


def guidance_retiring(gates: dict) -> str | None:
    """The one section whose verdict is `retire`, re-checked exactly as a
    skill's is (`retiring`, over the guidance block)."""
    return retiring({**gates, "skills": guidance_block(gates)})


def guidance_shortening(gates: dict) -> list:
    """Sections whose verdict is `shorten-or-merge`, by name."""
    names = sorted(n for n, v in guidance_block(gates).items()
                   if isinstance(v, dict) and v.get("verdict") == "shorten-or-merge")
    for name in names:
        if not SKILL_RE.fullmatch(name):
            raise RetireError("a shorten-or-merge section's name is not a section id")
    return names


def guidance_open_prs(gh) -> list:
    text = gh(["api", "--paginate",
               f"repos/{GUIDANCE_REPO}/pulls?state=open&per_page=100",
               "--jq", ".[] | {number: .number, head: .head.ref, url: .html_url}"])
    return [p for p in _json_lines(text)
            if isinstance(p.get("head"), str) and p["head"].startswith(GUIDANCE_BRANCH_PREFIX)]


def guidance_issues(gh, marker: str) -> dict:
    """As `existing_issues`, for any marker."""
    text = gh(["api", "--paginate",
               f"repos/{EVALS_REPO}/issues?state=all&per_page=100",
               "--jq", ".[] | select(.pull_request == null) | "
                       "{number: .number, body: (.body // \"\"), "
                       "author: .user.login, state: .state}"])
    found = {"open": [], "closed": []}
    for item in _json_lines(text):
        if item.get("author") != ISSUE_AUTHOR or not isinstance(item.get("body"), str) \
                or marker not in item["body"]:
            continue
        number, state = item.get("number"), item.get("state")
        if type(number) is not int or number < 1 or state not in found:
            raise RetireError("an issue record from the API is malformed")
        found[state].append(number)
    return {state: sorted(numbers) for state, numbers in found.items()}


def guidance_evidence(verdict: dict) -> str:
    """The in-situ cells, then the alone cells for contrast."""
    tables = []
    for title, cells in (("Whole guidance with vs without the section (decides)",
                          verdict.get("cells")),
                         ("The section alone vs no guidance (contrast)",
                          (verdict.get("alone") or {}).get("cells"))):
        rows = [f"{title}:", "", "| Fixture | Model | Class | Runs with | Runs without | "
                "Objective upper | Judge upper |", "|---|---|---|---|---|---|---|"]
        for cell in cells if isinstance(cells, list) else []:
            if not isinstance(cell, dict):
                continue
            arms = [cell.get(a) if isinstance(cell.get(a), dict) else {}
                    for a in ("with", "without")]
            fixture, model, cls = (cell.get(k) for k in ("fixture", "model", "class"))
            rows.append(f"| {fixture if isinstance(fixture, str) and NAME_RE.fullmatch(fixture) else 'n/a'} "
                        f"| {model if isinstance(model, str) and NAME_RE.fullmatch(model) else 'n/a'} "
                        f"| {cls if isinstance(cls, str) and NAME_RE.fullmatch(cls) else 'n/a'} "
                        f"| {arms[0].get('runs', 'n/a')} | {arms[1].get('runs', 'n/a')} | "
                        f"{_figure(cell.get('objective'), 'retire_upper')} | "
                        f"{_figure(cell.get('judge'), 'retire_upper')} |")
        tables.append("\n".join(rows))
    return "\n\n".join(tables)


def guidance_row(checkout: Path, section: str) -> dict:
    """The section's manifest row in `checkout`, every path it touches
    checked to be no symlink and inside the checkout."""
    try:
        rows = guidance.load_manifest(checkout)
        row = guidance.find_row(rows, section, checkout)
    except guidance.GuidanceError:
        raise RetireError(f"{section}: no readable manifest row in the "
                          "_agent-guidance checkout") from None
    rel = row["file"]
    if rel != "agents-md/base.md" and not re.fullmatch(r"agents-md/sections/[a-z0-9-]+\.md", rel):
        raise RetireError(f"{section}: its file {rel!r} is neither base.md nor a sections file")
    contained(checkout, ["agents-md", "agents-md/sections", rel, GUIDANCE_MANIFEST, "docs",
                         GUIDANCE_IMPACT, GUIDANCE_AGENTS, "scripts", ".claude",
                         ".claude/hooks", GUIDANCE_PAYLOAD])
    return row


def _read_exact(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write_exact(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def guidance_without_section(text: str, heading: str) -> str | None:
    """`text` with the `##` extent named `heading` removed (harness/guidance.py
    `h2_extents`, the arithmetic check-guidance-coverage.js uses), or None
    when it was the file's only `##`, so the file goes."""
    spans = guidance.h2_extents(text)
    hits = [s for s in spans if s["heading"] == heading]
    if len(hits) != 1:
        raise RetireError(f"heading {heading!r} is not in its file exactly once")
    if len(spans) == 1:
        return None
    return text[:hits[0]["start"]] + text[hits[0]["end"]:]


def manifest_without_row(text: str, section: str) -> str:
    """The manifest with `section`'s row deleted, comments and layout kept:
    the row's lines are found on the composed YAML tree (its sequence item's
    start, to the next item's start or the end), never by a line scan."""
    try:
        doc = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise RetireError(f"{GUIDANCE_MANIFEST} is not valid YAML") from None
    if not isinstance(doc, yaml.SequenceNode):
        raise RetireError(f"{GUIDANCE_MANIFEST} is not a YAML list")
    items = doc.value
    hits = [i for i, node in enumerate(items) if isinstance(node, yaml.MappingNode)
            and any(k.value == "id" and isinstance(v, yaml.ScalarNode) and v.value == section
                    for k, v in node.value)]
    if len(hits) != 1:
        raise RetireError(f"{section}: not exactly one manifest row")
    lines = text.splitlines(keepends=True)
    start = items[hits[0]].start_mark.line
    end = items[hits[0] + 1].start_mark.line if hits[0] + 1 < len(items) else len(lines)
    return "".join(lines[:start] + lines[end:])


def guidance_impact_entry(gates: dict, section: str, run_url: str | None) -> str:
    """A typed `remove` entry whose Eval: line is a real result for
    check-guidance-touch.js: the fixtures, the pooled run count as `n=` and
    the policy."""
    verdict = guidance_block(gates)[section]
    cells = verdict["cells"]
    fixtures = sorted({c.get("fixture") or "(flat)" for c in cells})
    runs = _runs(cells)
    where = f"workflow run {run_url}" if run_url else "the scheduled workflow"
    return (f"## {_date(gates['as_of'])} — {section} — remove\n"
            f"- Motivation: skills-evals#124's retirement verdict is `retire`: the whole "
            f"guidance with and without the section shows no benefit in every cell "
            f"({len(fixtures)} fixture(s) x {len(gates['roster_models'])} roster model(s)) "
            f"at two fixed looks.\n"
            f"- Change: removed the section and its eval-coverage.yml row, by "
            f"skills-evals' scheduled retirement ({where}).\n"
            f"- Eval: n={runs} pooled arm runs (full vs full-minus-section) as of "
            f"{gates['as_of']}, fixtures "
            f"{', '.join(f'evals/guidance/{section}/{f}' for f in fixtures)}, policy "
            f"evals/gate-policy.yml guidance:.\n"
            f"- Outcome: auto-merges once required checks pass, with no human review "
            f"(the owner, 2026-10-10 and 2026-10-25).\n")


def guidance_insert_entry(text: str, entry: str) -> str:
    """`entry` newest first: before the first dated `##` entry (after the
    file's header and its `## Entry format`), or at the end when none."""
    for span in guidance.h2_extents(text):
        if DATED_HEADING_RE.match(span["heading"]):
            return text[:span["start"]] + entry + "\n" + text[span["start"]:]
    return text.rstrip("\n") + "\n\n" + entry


def regenerate_agents_md(checkout: Path, managed: str) -> None:
    """AGENTS.md rebuilt as _agent-guidance's own AGENTS.md says: `managed`
    (build-agents-md.sh's stdout) with its trailing newlines stripped, a
    newline, then the file from its `## Repo-specific additions` heading
    LINE on, trailing newlines stripped, and one closing newline."""
    path = checkout / GUIDANCE_AGENTS
    lines = _read_exact(path).split("\n")
    marks = [i for i, line in enumerate(lines) if line.rstrip("\r") == REPO_SPECIFIC_LINE]
    if len(marks) != 1:
        raise RetireError(f"{GUIDANCE_AGENTS} has {len(marks)} `{REPO_SPECIFIC_LINE}` "
                          "heading lines, not exactly one")
    tail = "\n".join(lines[marks[0]:]).rstrip("\n")
    _write_exact(path, managed.rstrip("\n") + "\n" + tail + "\n")


def _run(checkout: Path, *args: str) -> str:
    proc = subprocess.run(list(args), cwd=checkout, capture_output=True, text=True,
                          check=False)
    if proc.returncode != 0:
        raise RetireError(f"{' '.join(args[:2])} exited {proc.returncode}")
    return proc.stdout


def guidance_issue_action(gates, section, kind, run_url, mode_reason=None) -> dict:
    """Create or update the section's issue (`kind` retire or shorten), or
    nothing when a closed one already decided it."""
    verdict = guidance_block(gates)[section]
    marker = (GUIDANCE_ISSUE_MARKER if kind == "retire" else SHORTEN_ISSUE_MARKER).format(
        section=section)
    return {"marker": marker, "section": section, "kind": kind,
            "title": (f"Retire guidance section {section}: no marginal benefit on every "
                      "roster model (#124)" if kind == "retire" else
                      f"Shorten or merge guidance section {section}: it helps only alone "
                      "(#124)"),
            "body": (f"{marker}\nThe guidance verdict for `{section}` is "
                     f"**{verdict.get('verdict')}** as of {gates['as_of']}: "
                     f"{verdict.get('reason')}.\n\n"
                     + ((f"`evals/gate-policy.yml` sets `guidance: retirement_mode: auto`, "
                         f"but {mode_reason}, so no removal pull request is opened and a "
                         "human removes it from _agent-guidance.\n\n" if mode_reason else
                         "`evals/gate-policy.yml` sets `guidance: retirement_mode: proposal`, "
                         "so a human removes it from _agent-guidance.\n\n")
                        if kind == "retire" else "Nothing is removed: the section teaches the behavior, and the "
                        "rest of the guidance already covers it. Shorten it or merge it into "
                        "the section that covers it.\n\n")
                     + guidance_evidence(verdict)
                     + (f"\n\nWorkflow run: {run_url}" if run_url else "")
                     + "\n\nFiled by scripts/retire_skill.py --target guidance on the "
                       "scheduled workflow (#124).\n")}


def guidance_plan(gates: dict, checkout: Path | None, gh, run_url: str | None = None,
                  allowed=("proposal",), fallback: bool = False) -> dict:
    """What to do for guidance, read-only. RetireError for a refusal."""
    mode = guidance_mode(gates)
    mode_reason = None
    section = guidance_retiring(gates)
    if section is None:
        shortening = guidance_shortening(gates)
        if not shortening:
            return {"action": "none", "mode": mode,
                    "reason": "no guidance section has a retire or shorten-or-merge verdict"}
        section, kind = shortening[0], "shorten"
    else:
        kind = "retire"
        if mode == "auto" and "auto" not in allowed and fallback:
            mode_reason = GUIDANCE_NO_CREDENTIAL
            mode = _lowered(mode_reason, allowed)
        if mode not in allowed:
            raise RetireError(f"guidance_retirement_mode is {mode}, which this caller has "
                              f"not wired (allowed: {', '.join(allowed)})")
    if kind == "shorten" or mode == "proposal":
        issue = guidance_issue_action(gates, section, kind, run_url, mode_reason)
        lowered = {"mode_reason": mode_reason} if mode_reason else {}
        found = guidance_issues(gh, issue["marker"])
        if not found["open"] and found["closed"]:
            return {"action": "none", "mode": mode, "section": section, **lowered,
                    "issue": found["closed"][0],
                    "reason": f"{section} is already decided: issue "
                              f"#{found['closed'][0]} is closed, so none is filed again"}
        number = found["open"][0] if found["open"] else None
        return {"action": "update-issue" if number else "create-issue", "mode": mode,
                "section": section, "issue": number, **lowered, "title": issue["title"],
                "body": issue["body"]}
    prs = guidance_open_prs(gh)
    if prs:
        raise RetireError("a guidance removal pull request is already open: "
                          + ", ".join(str(p.get("url")) for p in prs))
    if checkout is None:
        raise RetireError("no _agent-guidance checkout was given (--guidance PATH)")
    row = guidance_row(checkout, section)
    verdict = guidance_block(gates)[section]
    return {"action": "guidance-removal", "mode": mode, "section": section,
            "file": row["file"], "heading": row["heading"],
            "branch": f"{GUIDANCE_BRANCH_PREFIX}{section}",
            "impact_entry": guidance_impact_entry(gates, section, run_url),
            "title": f"Remove guidance section {section}: no marginal benefit "
                     "(skills-evals#124)",
            "body": (f"Removes the `## {row['heading']}` section of `{row['file']}` "
                     f"and its `{GUIDANCE_MANIFEST}` row: its verdict in "
                     f"[skills-evals ADR 0014](https://github.com/{EVALS_REPO}/blob/main/docs/decisions/0014-removal-pull-requests-merge-without-review.md) is "
                     f"**retire** as of {gates['as_of']} ({verdict.get('reason')}).\n\n"
                     f"{guidance_evidence(verdict)}\n\nAdds a `{GUIDANCE_IMPACT}` `remove` "
                     f"entry and regenerates `{GUIDANCE_AGENTS}`.\n\nThis pull request "
                     "auto-merges once required checks pass, with no human review "
                     "(the owner, 2026-10-10 and 2026-10-25)."
                     + (f"\n\nWorkflow run: {run_url}" if run_url else "")
                     + "\n\nOpened by skills-evals' scripts/retire_skill.py --target "
                       "guidance on its scheduled workflow.\n\n" + AGENT_MARKER + "\n")}


def guidance_prepare_branch(checkout: Path, action: dict) -> None:
    """The removal commit on a new branch of `checkout`, which must be
    clean: the section, its row, the bytes, the impact entry, AGENTS.md."""
    if _git(checkout, "status", "--porcelain"):
        raise RetireError("the _agent-guidance checkout has uncommitted changes")
    row = guidance_row(checkout, action["section"])
    if row["file"] != action["file"] or row["heading"] != action["heading"]:
        raise RetireError("the manifest row changed under the plan")
    _git(checkout, "checkout", "-q", "-b", action["branch"])
    source = checkout / row["file"]
    remaining = guidance_without_section(_read_exact(source), row["heading"])
    if remaining is None:
        _git(checkout, "rm", "-q", "--", row["file"])
    else:
        _write_exact(source, remaining)
        if row["file"] == "agents-md/base.md":
            (checkout / GUIDANCE_PAYLOAD).write_bytes(source.read_bytes())
    manifest = checkout / GUIDANCE_MANIFEST
    _write_exact(manifest, manifest_without_row(_read_exact(manifest), action["section"]))
    _run(checkout, "node", "scripts/check-guidance-coverage.js", "--write-bytes")
    impact = checkout / GUIDANCE_IMPACT
    _write_exact(impact, guidance_insert_entry(_read_exact(impact), action["impact_entry"]))
    regenerate_agents_md(checkout, _run(checkout, "bash", "scripts/build-agents-md.sh"))
    _git(checkout, "add", "-A", "--", "agents-md", GUIDANCE_IMPACT, GUIDANCE_AGENTS,
         GUIDANCE_PAYLOAD)
    _git(checkout, "-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
         "commit", "-q", "-m",
         f"Remove guidance section {action['section']}: no marginal benefit\n\n"
         "skills-evals#124's scheduled retirement found no benefit of the whole "
         "guidance with the section over it without, in every cell at two fixed looks."
         f"\n\n{AGENT_TRAILER}")


def guidance_commands(action: dict) -> list:
    """The writes `guidance_execute` makes, for a dry run."""
    kind = action["action"]
    if kind == "create-issue":
        return [["gh", "issue", "create", "-R", EVALS_REPO, "--title", action["title"],
                 "--body-file", "-"]]
    if kind == "update-issue":
        return [["gh", "issue", "edit", str(action["issue"]), "-R", EVALS_REPO,
                 "--body-file", "-"]]
    if kind == "guidance-removal":
        return [["git", "checkout", "-b", action["branch"]],
                ["edit", action["file"], f"remove ## {action['heading']}"],
                *([["copy", "agents-md/base.md", GUIDANCE_PAYLOAD]]
                  if action["file"] == "agents-md/base.md" else []),
                ["edit", GUIDANCE_MANIFEST, f"delete row {action['section']}"],
                ["node", "scripts/check-guidance-coverage.js", "--write-bytes"],
                ["edit", GUIDANCE_IMPACT, "insert the remove entry"],
                ["bash", "scripts/build-agents-md.sh", "> AGENTS.md (managed half)"],
                ["git", "commit"],
                ["git", "push", "origin", f"HEAD:refs/heads/{action['branch']}"],
                ["git", "merge-base", "--is-ancestor", "HEAD",
                 f"refs/remotes/origin/{action['branch']}"],
                ["gh", "pr", "create", "-R", GUIDANCE_REPO, "--base", "main",
                 "--head", action["branch"], "--title", action["title"], "--body-file", "-"],
                ["gh", "api", "-X", "POST",
                 f"repos/{GUIDANCE_REPO}/issues/<new pull request number>/labels",
                 "-f", f"labels[]={AGENT_LABEL}"],
                ["gh", "pr", "merge", "<new pull request url>", "-R", GUIDANCE_REPO,
                 "--auto", "--merge", "--match-head-commit", "<removal head sha>"],
                ["gh", "pr", "view", "<new pull request url>", "-R", GUIDANCE_REPO,
                 "--json", "autoMergeRequest"]]
    return []


GUIDANCE_PR_URL_RE = re.compile(rf"https://github\.com/{re.escape(GUIDANCE_REPO)}/pull/(\d+)")


def guidance_execute(action: dict, checkout: Path | None, gh, push, env,
                     registry_gh=None) -> str:
    """Make the plan's writes: skills-evals issues through `gh`,
    _agent-guidance writes through `registry_gh` (default `gh`)."""
    registry_gh = registry_gh or gh
    kind = action["action"]
    if kind == "none":
        return action["reason"]
    if kind in ("create-issue", "update-issue"):
        gh(guidance_commands(action)[0][1:], action["body"])
        return (f"filed an issue for {action['section']}" if kind == "create-issue"
                else f"updated issue #{action['issue']} for {action['section']}")
    check_auto_env(env)
    guidance_prepare_branch(checkout, action)
    head = _git(checkout, "rev-parse", "HEAD").strip()
    push(checkout, action["branch"])
    url = registry_gh(["pr", "create", "-R", GUIDANCE_REPO, "--base", "main",
                       "--head", action["branch"], "--title", action["title"],
                       "--body-file", "-"], action["body"]).strip()
    match = GUIDANCE_PR_URL_RE.fullmatch(url)
    if not match:
        raise RetireError("creating the pull request printed no pull request URL")
    # Labeled before auto-merge, as the skill path does: a pull request that
    # could not be marked as the loop's stays open for a person.
    registry_gh(["api", "-X", "POST", f"repos/{GUIDANCE_REPO}/issues/{match.group(1)}/labels",
                 "-f", f"labels[]={AGENT_LABEL}"])
    registry_gh(["pr", "merge", url, "-R", GUIDANCE_REPO, "--auto", "--merge",
                 "--match-head-commit", head])
    method = registry_gh(["pr", "view", url, "-R", GUIDANCE_REPO, "--json",
                          "autoMergeRequest", "--jq",
                          '.autoMergeRequest.mergeMethod // ""']).strip()
    if method != "MERGE":
        raise RetireError(f"auto-merge (merge commit) is not enabled on {url}")
    return f"opened {url} with auto-merge"


def main(argv: list | None = None, *, env=None, gh=default_gh, push=None,
         registry_gh=None, out=sys.stdout) -> int:
    env = os.environ if env is None else env
    token = env.get(REGISTRY_TOKEN_ENV) or None
    if push is None:
        def push(checkout, branch):
            default_push(checkout, branch, token)
    if registry_gh is None and gh is not default_gh:
        registry_gh = gh  # an injected gh (a test's fake) answers both
    elif registry_gh is None:
        def registry_gh(args, stdin=None):
            if not token:
                raise RetireError(f"no {REGISTRY_TOKEN_ENV} for an agentskills write")
            return default_gh(args, stdin, token)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gates", required=True, type=Path)
    parser.add_argument("--registry", action="append", metavar="NAME=PATH")
    parser.add_argument("--allow-mode", action="append", choices=("auto", "proposal"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fallback-to-proposal", action="store_true",
                        help="this caller has no write credential for the target repository: a "
                             "gates file in auto mode files the issue instead of exiting 2")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="the skill-impact entry's date (default: the gates file's "
                             "as_of date)")
    parser.add_argument("--target", choices=("skill", "guidance"), default="skill",
                        help="act on the skills' verdicts (default) or the guidance "
                             "sections' (#124)")
    parser.add_argument("--guidance", type=Path, metavar="PATH",
                        help="the _agent-guidance checkout a guidance removal edits")
    parser.add_argument("--usage", type=Path, default=None,
                        help="usage/weekly-local.json, for the issue's recent-use line")
    args = parser.parse_args(argv)
    run_url = None
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY") == EVALS_REPO \
            and env["GITHUB_RUN_ID"].isdigit():
        run_url = (f"https://github.com/{EVALS_REPO}/actions/runs/{env['GITHUB_RUN_ID']}")
    try:
        if not args.dry_run:
            check_event(env)
        if args.date is not None and not DATE_RE.fullmatch(args.date):
            raise RetireError("--date is not YYYY-MM-DD")
        gates = load_gates(args.gates)
        registries = compute_gates.flag_registries(args.registry)
        allowed = tuple(args.allow_mode or ("proposal",))
        if args.target == "guidance":
            if not args.dry_run and guidance_mode(gates) == "auto" and "auto" in allowed:
                check_auto_env(env)
            action = guidance_plan(gates, args.guidance, gh, run_url, allowed,
                                   fallback=args.fallback_to_proposal)
            if args.dry_run:
                out.write(json.dumps({"dry_run": True, **action,
                                      "would_run": guidance_commands(action)},
                                     indent=2, sort_keys=True) + "\n")
                return 0
            if action.get("mode_reason"):
                print(f"retire_skill: auto lowered to proposal: {action['mode_reason']}",
                      file=sys.stderr)
            out.write(guidance_execute(action, args.guidance, gh, push, env,
                                       registry_gh) + "\n")
            return 0
        if not args.dry_run and mode_of(gates) == "auto" and "auto" in allowed:
            check_auto_env(env)
        usage = skill_usage.load(args.usage) if args.usage is not None else None
        action = plan(gates, registries, gh, run_url, allowed, usage,
                      fallback=args.fallback_to_proposal, date=args.date)
        if args.dry_run:
            out.write(json.dumps({"dry_run": True, **action,
                                  "would_run": commands(action)},
                                 indent=2, sort_keys=True) + "\n")
            return 0
        if action.get("mode_reason"):
            print(f"retire_skill: auto lowered to proposal: {action['mode_reason']}",
                  file=sys.stderr)
        out.write(execute(action, registries, gh, push, env, registry_gh) + "\n")
    except (RetireError, ValueError) as exc:
        print(f"retire_skill: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
