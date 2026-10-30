#!/usr/bin/env python3
"""Validate a routine's `claude/eval-improve-<run id>` branch and apply it.

The improvement loop's routine mode (ADR 0005 amendment 1; ADR 0010). The
eval routine runs `scripts/propose_skill_edit.py` and, when its candidate is
ACCEPTED, pushes one branch holding exactly

    eval-improve/<run id>/summary.json   the loop's record (`<ts>.json`)
    eval-improve/<run id>/report.md      its pull-request body (`<ts>.pr-body.md`)
    eval-improve/<run id>/skill.patch    its registry patch (`<ts>.patch`)

and nothing else. It never opens a pull request. The branch is UNTRUSTED
input (a model run wrote it): `.github/workflows/routine-improve-gate.yml`
runs this script from the default branch's checkout, and it reads the branch
only through git plumbing (`git diff --raw`, `git cat-file`), reusing
`ingest_routine_results.py`'s helpers. Nothing from the branch is executed,
imported or checked out; `apply` hands the validated patch to `git apply` in
a registry checkout, as data.

`eval-improve/` is not `eval-results/`: `claude/eval-improve-*` also matches
the results signal's `claude/eval-*` branch filter, and only the two path
filters keep an improve push from starting a results ingest (and a results
push from starting this gate).

What a branch must look like to pass:

  * its name is `claude/eval-improve-<YYYYMMDDTHHMMSSZ>-<6 hex>`;
  * against its merge-base with the default branch it ADDS exactly the three
    files above, regular and non-executable, under its own run id;
  * summary.json is an `accepted` record (schema 1) for the acsi-agentskills
    registry at a full commit sha, whose `skill_md` is that registry's
    `plugins/<plugin>/skills/<skill>/SKILL.md` for the record's own skill,
    and the skill has fixtures under `evals/<skill>/` on the default branch;
  * skill.patch is a unified diff of that one path and nothing else, every
    hunk's line counts agreeing with its header;
  * report.md opens with the loop's own title and holds no closing keyword
    or @mention (it becomes a pull-request body in another repository);
  * every file is under its size cap and valid UTF-8 with no control
    characters.

Subcommands (output is `key=value` lines of validated values, for
$GITHUB_OUTPUT):

  resolve  --event-name NAME --event-path PATH
  validate --repo DIR --base REF --source REF --branch NAME
           [--expect-sha SHA] --out DIR
  apply    --staged DIR --tree DIR --skill-md PATH
  prepare  --staged DIR --tree DIR --skill-md PATH --registry-sha SHA
           --run-id ID [--policy FILE]
  pr-body  --staged DIR --run-id ID --sha SHA --branch NAME
           [--mode draft|auto]                          (body on stdout)
  merge-mode --tree DIR --plugin NAME [--policy FILE]   (mode=, mode_reason=)
  fire-check SKILL [--holdout NAME]                     (no output; exit 0 or 1)

`prepare` builds the whole registry change on a clean checkout of
acsi-agentskills' default branch tip, holding no credential: the patch
(`apply`), a `PURPOSE.md` stub when the skill has none, the plugin's patch
version raised in both manifests (acsi-agentskills ADR 0009), a
`docs/skill-impact.md` entry, the README table regenerated, then a second
commit re-pinning `skills.lock` to the first when the lock covers the
skill (the registry's own `scripts/` do the last two). Those are what the
registry's required checks and AGENTS.md ask of a skill edit. It prints the
merge mode: `evals/improve-policy.yml`'s `improve_merge_mode`, or `draft`
when the skill's plugin runs code (`auto_refusal`), so an auto-merged pull
request never touches what acsi-agentskills' plugin-runtime-review gates.
`merge-mode` prints the same decision (`effective_mode`) for
routine-draft-skill-gate.yml, so one policy file switches every loop gate
that writes acsi-agentskills.

`fire-check` is the other end: `routine-eval-fire.yml` runs it before the
fire bearer exists, so a skill the loop would refuse never starts a routine
session. It reuses the loop's own resolution (`skill_fixtures`,
`load_fixtures`, `MIN_FIXTURES`) and harness/registries.yml, read-only and
offline. The loop's last step, finding `<skill>/SKILL.md` in the registry
checkout, needs that checkout, which the fire job does not have, so that
step stays the routine's.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_routine_results as ingest  # noqa: E402
from ingest_routine_results import Rejected  # noqa: E402

BRANCH_RE = re.compile(rf"claude/eval-improve-(?P<run_id>{ingest.RUN_ID})")
BRANCH_SHAPE = "claude/eval-improve-<run id>"
#: The one top-level directory an improve branch adds to.
SOURCE_ROOT = "eval-improve"
#: Every file the branch must add, with its byte cap.
SIZE_CAPS = {"summary.json": 256 * 1024, "report.md": 64 * 1024,
             "skill.patch": 128 * 1024}

#: The only registry a pull request is opened in. ADR 0010 decision 1
#: lets routine arms run only the registries' default branches; this gate
#: further limits the loop's output to the one registry it targets.
REGISTRY_NAME = "acsi-agentskills"
REGISTRY_URL = "https://github.com/example-adopter/acsi-agentskills"
NAME = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
#: acsi-agentskills' layout (harness/registries.yml: plugins/*/skills/*/SKILL.md).
SKILL_MD_RE = re.compile(rf"plugins/(?P<plugin>{NAME})/skills/(?P<skill>{NAME})/SKILL\.md")
NAME_RE = re.compile(NAME)
#: The branch the pull request comes from in acsi-agentskills: one per skill,
#: so a second accepted candidate waits until the first has merged (and its
#: branch was deleted) or was closed (ADR 0005, "the idempotency check on an
#: open eval-improve/<skill> branch").
TARGET_BRANCH = "eval-improve/{skill}"

#: The improvement gate's policy: `improve_merge_mode`.
POLICY = Path(__file__).resolve().parent.parent / "evals" / "improve-policy.yml"
MERGE_MODES = ("auto", "draft")
#: A policy that names no mode (the owner, 2026-10-26: accepted improvements merge
#: with no human in the loop).
DEFAULT_MERGE_MODE = "auto"
#: What makes a plugin one that runs code, as acsi-agentskills'
#: scripts/plugin_runtime_gate.py sees it: these paths under its root, or a
#: `hooks` or `settings` key in a manifest. A skill in such a plugin is never
#: auto-merged, because a hook can name any file under the root.
RUNTIME_PATHS = ("hooks", "bin", "settings.json", "package.json",
                 "bun.lock", "bun.lockb", "npm-shrinkwrap.json",
                 "package-lock.json")
RUNTIME_KEYS = ("hooks", "settings")
#: The registry files `prepare` writes besides the skill's own directory.
IMPACT_FILE = "docs/skill-impact.md"
README = "README.md"
LOCK = "skills.lock"
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
#: The registry ADR that lets an accepted improvement merge itself.
REGISTRY_ADR = (f"{REGISTRY_URL}/blob/main/docs/decisions/"
                "0021-automated-skill-changes-merge-without-review.md")
#: Who commits in the registry checkout. The App's token pushes the commits;
#: it does not author them.
COMMIT_IDENTITY = ("-c", "user.name=skills-evals improve-gate bot",
                   "-c", "user.email=skills-evals@users.noreply.github.com")
#: The fleet's marker for a GitHub contribution an agent wrote on the owner's
#: behalf (_agent-guidance ADR 0023); the candidate is a Claude Code run's.
AGENT_MARKER = ("<sub>🤖 Written by Claude Code on behalf of "
                "@example-adopter</sub>")

#: The repository secrets the pull-request job mints its GitHub App token from
#: (owner decision, the owner: "GitHub App (Recommended)"): the App's client id
#: and its private key in PEM form. Neither name holds one of gitleaks'
#: generic-api-key keywords (ADR 0010 decision 5).
APP_CLIENT_ID_SECRET = "EVAL_IMPROVE_APP_CLIENT_ID"
APP_PEM_SECRET = "EVAL_IMPROVE_APP_PEM"

#: propose_skill_edit.improve()'s record keys (the suite reads them from its
#: AST and checks they stay inside these sets).
RECORD_REQUIRED = ("schema", "skill", "timestamp", "registry", "split",
                   "trials", "status", "reasons", "baseline", "candidate",
                   "description_half", "body_half", "table", "files")
RECORD_ALLOWED = RECORD_REQUIRED + (
    "local_exhibit", "no_judge", "min_gain", "models", "runs", "trigger_set",
    "phase", "exit_code", "decision", "tokens_basis")
#: The values a record's `tokens_basis` may take (propose_skill_edit's
#: TOKENS_BASIS and TOKENS_BASIS_LEGACY); a record without one predates it.
TOKENS_BASES = ("model_usage_total", "usage_main_loop")
MAX_RECORD_STRING = 32 * 1024
MAX_RECORD_DEPTH = 10
MAX_RECORD_ITEMS = 512

REPORT_TITLE = "## Proposed `{skill}` SKILL.md edit (validation-gated)"
#: GitHub closes an issue or pull request named after one of these keywords
#: in a merged pull request's body, in any repository; a model-written
#: report must not be able to do that. Matched against the text with its
#: Markdown emphasis, code and link syntax stripped (`closing_text`), so
#: `**Fixes** #1` or `Fixes [#1](...)` cannot hide one; a link's target URL
#: is kept, so `Closes [x](https://github.com/o/r/issues/7)` is caught too.
_KEYWORD = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[\s:<]*"
CLOSING_RE = re.compile(
    _KEYWORD + r"(?:(?:[\w.-]+/[\w.-]+)?#\d+"
    r"|https?://github\.com/[^\s/]+/[^\s/]+/(?:issues|pull)/\d+)", re.I)
#: A keyword with an issue or pull request URL anywhere later on its line
#: (a link label can sit between the two). Over-rejects; that is fine.
CLOSING_URL_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[^\n]*?"
    r"https?://github\.com/[^\s/]+/[^\s/]+/(?:issues|pull)/\d+", re.I)
#: Markdown that can sit between a keyword and its reference.
_MARKDOWN_NOISE = re.compile(r"[*_`~\[\]]")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
#: An @mention notifies a person. Only a letter or digit right before the @
#: (an email address, an `action@sha` pin) makes it something else;
#: punctuation does not, so this over-rejects rather than miss one.
MENTION_RE = re.compile(r"(?<![A-Za-z0-9])@[A-Za-z0-9][A-Za-z0-9-]*")
HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?")


# ---------------------------------------------------------------------------
# Content.

def bounded(value, where: str, depth: int = 0) -> None:
    """Any JSON value, within the record's depth, string and size limits
    (larger than a result summary's: a record carries the proposal's diff
    and rationale)."""
    if depth > MAX_RECORD_DEPTH:
        raise Rejected(f"{where}: nested too deep")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not ingest.is_number(value):
            raise Rejected(f"{where}: not a finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_RECORD_STRING:
            raise Rejected(f"{where}: string too long")
        return
    if isinstance(value, (list, dict)):
        if len(value) > MAX_RECORD_ITEMS:
            raise Rejected(f"{where}: too many items")
        items = value.items() if isinstance(value, dict) else enumerate(value)
        for key, item in items:
            if isinstance(key, str) and len(key) > 128:
                raise Rejected(f"{where}: key too long")
            bounded(item, where, depth + 1)
        return
    raise Rejected(f"{where}: unexpected JSON type")


def check_record(doc, where: str, repo: str) -> dict:
    ingest._object(doc, where, RECORD_ALLOWED, RECORD_REQUIRED)
    bounded(doc, where)
    if doc["schema"] != 1 or isinstance(doc["schema"], bool):
        raise Rejected(f"{where}: unknown schema")
    if doc["status"] != "accepted":
        raise Rejected(f"{where}: status is not accepted; the routine pushes "
                       "only an accepted candidate")
    if "decision" in doc and doc["decision"] != "accepted":
        raise Rejected(f"{where}: decision is not accepted")
    if "tokens_basis" in doc and doc["tokens_basis"] not in TOKENS_BASES:
        raise Rejected(f"{where}: unknown tokens_basis")
    skill = doc["skill"]
    if not isinstance(skill, str) or not NAME_RE.fullmatch(skill):
        raise Rejected(f"{where}: bad skill name")
    if not isinstance(doc["timestamp"], str) \
            or not ingest.TIMESTAMP_RE.fullmatch(doc["timestamp"]):
        raise Rejected(f"{where}: bad timestamp")
    ingest._integer(doc["trials"], f"{where}: trials", 1, ingest.MAX_TRIALS)
    reasons = doc["reasons"]
    if not isinstance(reasons, list) or not reasons \
            or not all(isinstance(r, str) for r in reasons):
        raise Rejected(f"{where}: reasons must be a list of strings")

    registry = doc["registry"]
    keys = ("name", "url", "sha", "skill_md")
    ingest._object(registry, f"{where}: registry", keys, keys)
    if registry["name"] != REGISTRY_NAME or registry["url"] != REGISTRY_URL:
        raise Rejected(f"{where}: registry is not {REGISTRY_NAME}")
    if not isinstance(registry["sha"], str) \
            or not ingest.SHA_RE.fullmatch(registry["sha"]):
        raise Rejected(f"{where}: registry sha is not a 40-hex commit id")
    match = SKILL_MD_RE.fullmatch(registry["skill_md"]) \
        if isinstance(registry["skill_md"], str) else None
    if not match or match.group("skill") != skill \
            or ".." in registry["skill_md"].split("/"):
        raise Rejected(f"{where}: registry skill_md is not this skill's "
                       "SKILL.md")

    split = doc["split"]
    ingest._object(split, f"{where}: split",
                   ("rotation", "train", "validation", "holdout"),
                   ("rotation", "train", "validation"))
    names = split["train"] if isinstance(split["train"], list) else [None]
    names = names + [split["validation"]] + (
        [split["holdout"]] if "holdout" in split else [])
    if not all(isinstance(n, str) and NAME_RE.fullmatch(n) for n in names):
        raise Rejected(f"{where}: bad fixture name in split")

    if not (Path(repo) / "evals" / skill).is_dir():
        raise Rejected(f"{where}: skill has no evals/ directory on the "
                       "default branch")
    return {"skill": skill, "registry_sha": registry["sha"],
            "skill_md": registry["skill_md"]}


def check_patch(text: str, where: str, skill_md: str) -> None:
    """A unified diff of `skill_md` only, as difflib writes it."""
    if not text.endswith("\n"):
        raise Rejected(f"{where}: does not end with a newline")
    lines = text[:-1].split("\n")
    if lines[:2] != [f"--- a/{skill_md}", f"+++ b/{skill_md}"]:
        raise Rejected(f"{where}: must change {skill_md!r} and nothing else")
    i, hunks, changed = check_hunks(lines, 2, where)
    if i < len(lines):
        raise Rejected(f"{where}: line {i + 1} is not a hunk header")
    if not hunks or not changed:
        raise Rejected(f"{where}: changes nothing")


def check_hunks(lines: list, i: int, where: str) -> tuple[int, int, int]:
    """(index of the first line after them, hunks, changed lines) of the
    unified-diff hunks starting at `lines[i]`, every hunk's line counts
    agreeing with its header. Stops at the first line that is not a hunk
    header; scripts/guidance_improve_gate.py reads a multi-file patch with
    it."""
    changed = hunks = 0
    while i < len(lines):
        header = HUNK_RE.fullmatch(lines[i])
        if not header:
            break
        old = int(header.group(2) if header.group(2) is not None else 1)
        new = int(header.group(4) if header.group(4) is not None else 1)
        hunks += 1
        i += 1
        while old or new:
            if i >= len(lines):
                raise Rejected(f"{where}: hunk {hunks} is shorter than its "
                               "header")
            mark = lines[i][:1]
            if mark == " " and old and new:
                old, new = old - 1, new - 1
            elif mark == "-" and old:
                old, changed = old - 1, changed + 1
            elif mark == "+" and new:
                new, changed = new - 1, changed + 1
            else:
                raise Rejected(f"{where}: line {i + 1} does not fit hunk "
                               f"{hunks}")
            i += 1
    return i, hunks, changed


def closing_text(text: str) -> str:
    """`text` with each Markdown link reduced to its label and target, and
    emphasis, code and bracket characters removed."""
    return _MARKDOWN_NOISE.sub("", _LINK.sub(r"\1 \2", text))


def check_report(text: str, where: str, skill: str) -> None:
    if text.split("\n", 1)[0] != REPORT_TITLE.format(skill=skill):
        raise Rejected(f"{where}: does not open with the loop's title for "
                       "this skill")
    check_body_text(text, where)


def check_body_text(text: str, where: str) -> None:
    """Refuse text that would act on GitHub as a pull-request body in
    another repository: a closing keyword before an issue or pull request
    reference, or an @mention."""
    stripped = closing_text(text)
    if CLOSING_RE.search(text) or CLOSING_RE.search(stripped) \
            or CLOSING_URL_RE.search(stripped):
        raise Rejected(f"{where}: has a closing keyword before an issue or "
                       "pull request reference")
    if MENTION_RE.search(text):
        raise Rejected(f"{where}: has an @mention")


# ---------------------------------------------------------------------------
# Subcommands.

def validate(repo: str, base: str, source: str, branch: str,
             expect_sha: str | None, out: Path) -> dict:
    match = BRANCH_RE.fullmatch(branch)
    if not match:
        raise Rejected(f"branch name is not {BRANCH_SHAPE}")
    run_id = match.group("run_id")
    tip = ingest.resolve_commit(repo, source)
    if expect_sha is not None:
        if not ingest.SHA_RE.fullmatch(expect_sha):
            raise Rejected("expected sha is not a 40-hex commit id")
        if tip != expect_sha:
            raise Rejected("the branch moved since the push that triggered "
                           "this run; that newer push triggers its own gate")
    base_sha = ingest.resolve_commit(repo, base)
    merge_base = ingest.git(repo, "merge-base", base_sha, tip).decode().strip()
    if not ingest.SHA_RE.fullmatch(merge_base):
        raise Rejected("the branch shares no history with the default branch")

    prefix = f"{SOURCE_ROOT}/{run_id}/"
    files = {}
    for name, _, oid in ingest.added_files(repo, merge_base, tip):
        if not name.startswith(prefix):
            raise Rejected(f"{name!r}: not under {prefix!r}")
        kind = name[len(prefix):]
        if kind not in SIZE_CAPS:
            raise Rejected(f"{name!r}: not a file an improve branch carries")
        size = int(ingest.git(repo, "cat-file", "-s", oid).decode().strip())
        if size > SIZE_CAPS[kind]:
            raise Rejected(f"{name!r}: {size} bytes is over the {kind} cap")
        data = ingest.git(repo, "cat-file", "blob", oid)
        if len(data) != size:
            raise Rejected(f"{name!r}: size changed while reading")
        files[kind] = (repr(name), data)
    missing = sorted(set(SIZE_CAPS) - set(files))
    if missing:
        raise Rejected(f"{missing}: missing under {prefix!r}")

    where, data = files["summary.json"]
    found = check_record(
        ingest.load_json(ingest.decode_text(data, where), where), where, repo)
    where, data = files["skill.patch"]
    check_patch(ingest.decode_text(data, where), where, found["skill_md"])
    where, data = files["report.md"]
    check_report(ingest.decode_text(data, where), where, found["skill"])

    if out.exists() and any(out.iterdir()):
        raise Rejected("the staging directory is not empty")
    out.mkdir(parents=True, exist_ok=True)
    for kind, (_, data) in files.items():
        (out / kind).write_bytes(data)
    return {"run_id": run_id, "sha": tip, **found,
            "target_branch": TARGET_BRANCH.format(skill=found["skill"])}


def _frontmatter(text: str, where: str) -> dict:
    if not text.startswith("---\n") or "\n---\n" not in text[3:]:
        raise Rejected(f"{where}: has no frontmatter")
    try:
        data = yaml.safe_load(text[4:text.index("\n---\n", 3)])
    except yaml.YAMLError:
        raise Rejected(f"{where}: frontmatter is not YAML") from None
    if not isinstance(data, dict):
        raise Rejected(f"{where}: frontmatter is not a mapping")
    return data


def apply(staged: Path, tree: Path, skill_md: str) -> dict:
    """Apply the staged patch to a registry checkout at `tree`. Only
    `skill_md` may change, and only its frontmatter's `description` may
    change there (propose_skill_edit.set_description's rule: the name is
    fixed, the description is the trigger half's, and nothing else moves)."""
    if not SKILL_MD_RE.fullmatch(skill_md) or ".." in skill_md.split("/"):
        raise Rejected("skill_md is not a registry SKILL.md path")
    cursor = tree
    for part in Path(skill_md).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise Rejected(f"{skill_md!r}: the registry path has a symlink")
    target = tree / skill_md
    if not target.is_file():
        raise Rejected(f"{skill_md!r}: not in the registry checkout")
    before = _frontmatter(target.read_text(encoding="utf-8"), skill_md)
    patch = str((staged / "skill.patch").resolve())
    for args in (("apply", "--check", patch), ("apply", patch)):
        # Raw git is fine here (#343): the gate's own registry checkout at a recorded sha, never an agent-touched
        # workspace, so harness/workspace_git.py does not apply.
        result = subprocess.run(["git", "-C", str(tree), *args],
                                capture_output=True, check=False)
        if result.returncode != 0:
            raise Rejected("the patch does not apply to the registry at the "
                           "recorded sha")
    after = _frontmatter(target.read_text(encoding="utf-8"), skill_md)
    if {k: v for k, v in after.items() if k != "description"} \
            != {k: v for k, v in before.items() if k != "description"} \
            or ("description" in after) != ("description" in before):
        raise Rejected(f"{skill_md!r}: the patch changes the frontmatter "
                       "beyond its description")
    changed = ingest.git(str(tree), "status", "--porcelain=v1", "-z").decode()
    if changed.split("\0")[:-1] != [f" M {skill_md}"]:
        raise Rejected("the patch changed more than the one SKILL.md")
    return {"changed": skill_md}


def merge_mode(path: Path = POLICY) -> str:
    """`improve_merge_mode` from the policy file. A missing key is
    DEFAULT_MERGE_MODE; anything unreadable, unknown or malformed is
    `draft`, the mode that merges nothing on its own."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
        return "draft"
    if doc is None:
        doc = {}
    if not isinstance(doc, dict) or set(doc) - {"improve_merge_mode"}:
        return "draft"
    mode = doc.get("improve_merge_mode", DEFAULT_MERGE_MODE)
    return mode if mode in MERGE_MODES else "draft"


def _no_symlink(tree: Path, rel: str) -> Path:
    """`tree / rel`, refusing a symlink anywhere on the path (the file
    itself may be absent)."""
    cursor = tree
    for part in Path(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise Rejected(f"{rel!r}: the registry path has a symlink")
    return cursor


def _manifest(tree: Path, rel: str) -> tuple[str, dict]:
    path = _no_symlink(tree, rel)
    try:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError):
        raise Rejected(f"cannot read {rel}") from None
    if not isinstance(doc, dict):
        raise Rejected(f"{rel} is not a JSON object")
    return text, doc


def auto_refusal(tree: Path, plugin: str) -> str | None:
    """Why the skill's plugin may not be auto-merged into, or None. A plugin
    that runs code (acsi-agentskills ADR 0016's runtime surfaces) always
    gets a draft: a hook can name any file under its root, skills included,
    and its runtime review must have an owner's approval."""
    root = f"plugins/{plugin}"
    for name in RUNTIME_PATHS:
        path = tree / root / name
        if path.exists() or path.is_symlink():
            return "the skill's plugin runs code (it has a runtime path)"
    for name in MANIFESTS:
        _, doc = _manifest(tree, f"{root}/{name}")
        if any(key in doc for key in RUNTIME_KEYS):
            return "the skill's plugin runs code (a manifest names hooks or settings)"
    return None


def effective_mode(tree: Path, plugin: str, policy: Path = POLICY) -> dict:
    """The merge mode for a loop pull request that changes a skill of
    `plugin` in the registry checkout `tree`: the policy's, lowered to
    `draft` (with the reason) when the plugin runs code. The one switch for
    every loop gate that writes acsi-agentskills: this gate's `prepare` and
    routine-draft-skill-gate.yml's `merge-mode` step."""
    if not NAME_RE.fullmatch(plugin):
        raise Rejected("plugin is not a plugin name")
    mode = merge_mode(policy)
    reason = auto_refusal(tree, plugin) if mode == "auto" else None
    return {"mode": "draft", "mode_reason": reason} if reason \
        else {"mode": mode}


def _bump_versions(tree: Path, plugin: str) -> tuple[str, str]:
    """Raise the plugin's patch version in both manifests (acsi-agentskills
    ADR 0009: `claude plugin update` fires on the version string alone, and
    its check_plugin_versions.py fails a content change without one).
    The text is edited in place so the file keeps its formatting."""
    found = {}
    for name in MANIFESTS:
        rel = f"plugins/{plugin}/{name}"
        text, doc = _manifest(tree, rel)
        version = doc.get("version")
        match = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
        if not match:
            raise Rejected(f"{rel}: version is not MAJOR.MINOR.PATCH")
        found[rel] = (text, version, match)
    if len({version for _, version, _ in found.values()}) != 1:
        raise Rejected(f"{plugin}'s two manifests disagree on its version")
    old = next(iter(found.values()))[1]
    major, minor, patch = map(int, next(iter(found.values()))[2].groups())
    new = f"{major}.{minor}.{patch + 1}"
    for rel, (text, _, _) in found.items():
        needle = f'"version": "{old}"'
        if text.count(needle) != 1:
            raise Rejected(f"{rel}: its version is not written once as {needle}")
        (tree / rel).write_text(text.replace(needle, f'"version": "{new}"'),
                                encoding="utf-8")
    return old, new


def _score(metrics) -> str:
    """`passed/total` of one fixture's metrics, or `n/a`."""
    if not isinstance(metrics, dict):
        return "n/a"
    passed, total = metrics.get("passed"), metrics.get("total")
    if type(passed) is not int or type(total) is not int \
            or not 0 <= passed <= total or total < 1:
        return "n/a"
    return f"{passed}/{total}"


def eval_numbers(record: dict) -> str:
    """One line of the record's objective scores, baseline -> candidate, per
    fixture of the split. Every value is a validated fixture name or an
    integer, so nothing the model wrote reaches the registry's text."""
    split = record["split"]
    roles = [("train", n) for n in split["train"]]
    roles.append(("validation", split["validation"]))
    if "holdout" in split:
        roles.append(("holdout", split["holdout"]))
    parts = [f"{role} {name} {_score(record['baseline'].get(name))} -> "
             f"{_score(record['candidate'].get(name))}"
             for role, name in roles]
    return "; ".join(parts)


def impact_entry(record: dict, plugin: str, run_id: str, versions: tuple,
                 purpose_added: bool, mode: str) -> str:
    stamp = record["timestamp"]
    date = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
    skill = record["skill"]
    added = " and a `PURPOSE.md` stub added" if purpose_added else ""
    outcome = ("auto-merges once required checks pass, with no human review "
               "(ADR 0021, the owner's 2026-10-26 decision)." if mode == "auto"
               else "draft pull request; a person merges or closes it.")
    return (f"## {date} — {plugin}/{skill} — edit\n\n"
            f"- Motivation: skills-evals improvement run `{run_id}`: train "
            "checks failed, and `scripts/propose_skill_edit.py`'s acceptance "
            "rule accepted its proposed edit "
            "([skills-evals ADR 0005](https://github.com/example-adopter/skills-evals/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md)).\n"
            f"- Change: SKILL.md edited by the improvement loop{added}; bundle "
            f"`{plugin}` {versions[0]} -> {versions[1]}, by skills-evals' "
            "`routine-improve-gate.yml`.\n"
            f"- Eval: objective checks passed, baseline -> candidate, "
            f"{record['trials']} trial(s) per fixture in the eval routine (a "
            f"local exhibit, not badge input): {eval_numbers(record)}.\n"
            f"- Outcome: {outcome}\n")


def insert_entry(text: str, entry: str) -> str:
    """`entry` newest first, right after the header's `---` rule
    (scripts/retire_skill.py writes its removal entries the same way)."""
    lines = text.split("\n")
    try:
        rule = lines.index("---")
    except ValueError:
        raise Rejected(f"{IMPACT_FILE} has no `---` rule to insert after") from None
    head = "\n".join(lines[:rule + 1]).rstrip("\n")
    rest = "\n".join(lines[rule + 1:]).lstrip("\n")
    return f"{head}\n\n{entry}\n{rest}"


PURPOSE_STUB = """# Purpose — {skill}

Maintenance context only; never loaded at inference.

## What it is for

Not written yet. skills-evals' improvement gate added this file when
improvement run `{run_id}` edited `SKILL.md`, because acsi-agentskills asks a
touched skill to carry one (AGENTS.md, "Skill changes get recorded"). Replace
it with the incidents and patterns that motivated the skill.

## Eval status

Fixtures live in skills-evals under `evals/{skill}/`; the edit that added
this file is recorded in `docs/skill-impact.md`.
"""


def _registry_script(tree: Path, rel: str) -> None:
    """Refuse unless `rel` is a regular file of the registry checkout. The
    callers then run it there: it is the default branch's copy (the tree is
    its tip), the same code the registry's CI runs, and the step holds no
    credential. Each call spells its argv in full, so the suite's
    fork scan can tell it is not this repository's test runner."""
    script = _no_symlink(tree, rel)
    if not script.is_file():
        raise Rejected(f"{rel}: not in the registry checkout")


def prepare(staged: Path, tree: Path, skill_md: str, registry_sha: str,
            run_id: str, policy: Path = POLICY) -> dict:
    """Build the registry change on `tree`, a clean checkout of the
    registry's default branch tip, as one or two commits; see the module
    docstring. Returns the merge mode, and `mode_reason` when the policy's
    mode was lowered to `draft`."""
    match = SKILL_MD_RE.fullmatch(skill_md)
    if not match or ".." in skill_md.split("/"):
        raise Rejected("skill_md is not a registry SKILL.md path")
    if not ingest.SHA_RE.fullmatch(registry_sha) \
            or not re.fullmatch(ingest.RUN_ID, run_id):
        raise Rejected("prepare takes only validated values")
    plugin, skill = match.group("plugin"), match.group("skill")
    skill_dir = f"plugins/{plugin}/skills/{skill}"
    repo = str(tree)
    if ingest.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise Rejected("the registry checkout is not clean")
    base = ingest.resolve_commit(repo, "HEAD")
    # The loop measured the skill at registry_sha; the pull request is based
    # on the tip. The skill's whole directory must be unchanged between the
    # two, or what merges is not what was measured.
    if ingest.git(repo, "rev-parse", f"{registry_sha}:{skill_dir}") \
            != ingest.git(repo, "rev-parse", f"HEAD:{skill_dir}"):
        raise Rejected("the skill changed on the registry's default branch "
                       "since it was measured; measure it again")

    decided = effective_mode(tree, plugin, policy)
    mode, reason = decided["mode"], decided.get("mode_reason")

    apply(staged, tree, skill_md)
    record = json.loads((staged / "summary.json").read_text(encoding="utf-8"))
    purpose = _no_symlink(tree, f"{skill_dir}/PURPOSE.md")
    purpose_added = not purpose.exists()
    if purpose_added:
        purpose.write_text(PURPOSE_STUB.format(skill=skill, run_id=run_id),
                           encoding="utf-8")
    versions = _bump_versions(tree, plugin)
    impact = _no_symlink(tree, IMPACT_FILE)
    try:
        text = impact.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Rejected(f"cannot read {IMPACT_FILE}") from None
    impact.write_text(insert_entry(text, impact_entry(
        record, plugin, run_id, versions, purpose_added, mode)), encoding="utf-8")
    _no_symlink(tree, README)
    _registry_script(tree, "scripts/generate_readme_table.py")
    if subprocess.run([sys.executable, "scripts/generate_readme_table.py"],
                      cwd=tree, capture_output=True, check=False).returncode:
        raise Rejected("the registry's README table did not regenerate")
    ingest.git(repo, "add", "-A", "--", skill_dir, IMPACT_FILE, README,
               *(f"plugins/{plugin}/{name}" for name in MANIFESTS))
    ingest.git(repo, *COMMIT_IDENTITY, "commit", "-q", "-m",
               f"Edit {plugin}/{skill} from improvement run {run_id}\n\n"
               "skills-evals' improvement loop measured the edit and accepted "
               f"it; the bundle version moves {versions[0]} -> {versions[1]} "
               "so `claude plugin update` delivers it.\n\nAgent: claude-code")
    content = ingest.resolve_commit(repo, "HEAD")

    # The lock pins a commit, so it can only name the content commit after
    # that commit exists (acsi-agentskills ci.yml, "Check skills.lock still
    # describes the current bundle").
    if (tree / LOCK).exists():
        _no_symlink(tree, LOCK)
        _registry_script(tree, "scripts/generate_skills_lock.py")
        current = subprocess.run(
            [sys.executable, "scripts/generate_skills_lock.py", "--check-current"],
            cwd=tree, capture_output=True, check=False).returncode
        if current == 1:
            if subprocess.run(
                    [sys.executable, "scripts/generate_skills_lock.py", "--repin",
                     "--repo", "."],
                    cwd=tree, capture_output=True, check=False).returncode:
                raise Rejected("the registry's skills.lock did not re-pin")
            ingest.git(repo, "add", "--", LOCK)
            ingest.git(repo, *COMMIT_IDENTITY, "commit", "-q", "-m",
                       "Re-pin skills.lock to the content commit\n\n"
                       f"The edit changed {skill}, so the lock must pin "
                       "the commit that carries it for ephemeral surfaces "
                       "to receive it.\n\nAgent: claude-code")
        elif current != 0:
            raise Rejected("the registry's skills.lock check could not run")

    if ingest.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise Rejected("the registry's scripts changed files they were not "
                       "asked to")
    allowed = {IMPACT_FILE, README, LOCK,
               *(f"plugins/{plugin}/{name}" for name in MANIFESTS)}
    changed = ingest.git(repo, "diff", "--no-renames", "--name-only", "-z",
                         base, "HEAD").decode().split("\0")[:-1]
    stray = [p for p in changed
             if p not in allowed and not p.startswith(f"{skill_dir}/")]
    if stray:
        raise Rejected("the registry change reaches past the skill's "
                       "directory and its bookkeeping files")
    out = {"mode": mode, "base_sha": base, "content_sha": content,
           "commit": ingest.resolve_commit(repo, "HEAD"),
           "version": versions[1]}
    if reason:
        out["mode_reason"] = reason
    return out


def pr_body(staged: Path, run_id: str, sha: str, branch: str,
            mode: str = "draft") -> str:
    report = (staged / "report.md").read_text(encoding="utf-8")
    if mode == "auto":
        review = [
            "This pull request merges itself (merge commit) once "
            "acsi-agentskills' required checks pass, with no human review: "
            "the owner decided on 2026-10-26 that accepted improvements merge "
            f"with no human in the loop ([ADR 0021]({REGISTRY_ADR})). It does "
            "not count toward the three human-reviewed loop pull requests of "
            "[skills-evals ADR 0005](https://github.com/example-adopter/skills-evals/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md). "
            "Where the report below says to review before merging, this "
            "paragraph supersedes it."]
    else:
        review = [
            "A person reviews and merges this, or declines it; nothing merges "
            "itself. Merging it counts toward the three human-reviewed loop "
            "pull requests that must merge before any scheduled loop "
            "([skills-evals ADR 0005](https://github.com/example-adopter/skills-evals/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md))."]
    return "\n".join([
        f"From the skills-evals improvement loop, run `{run_id}`: the "
        "eval routine pushed "
        f"[`{branch}`](https://github.com/example-adopter/skills-evals/tree/{sha}) "
        f"at `{sha}`, and `routine-improve-gate.yml` validated it without "
        "running it and applied its `skill.patch` here. The gate also added "
        "what the registry asks of a skill edit: the bundle's patch version "
        "in both manifests, a `docs/skill-impact.md` entry, a `PURPOSE.md` "
        "stub if the skill had none, the README table, and a `skills.lock` "
        "re-pin when the lock covers the skill.",
        "",
        *review,
        "",
        "The numbers below are a local exhibit (skills-evals ADR 0010 "
        "decision 3), not badge input.",
        "",
        "Part of the improvement loop (https://github.com/example-adopter/skills-evals/blob/main/docs/decisions/0005-improvement-loop-reuses-skill-creator.md).",
        "",
        "---",
        "",
        report.rstrip("\n"),
        "",
        AGENT_MARKER,
        ""])


#: fire-check's refusals: fixed text, so no input value reaches the log.
FIRE_NOT_A_SKILL = ("input 'skill' is not one skill's fixture set: the loop "
                    "finds no nested fixture under evals/<skill>/, or one "
                    "naming another skill or none, or more than one registry")
FIRE_NOT_ROUTINE_REGISTRY = (f"input 'skill' is not an {REGISTRY_NAME} skill "
                             "(harness/registries.yml), the one registry the "
                             "routine checks out")
FIRE_BAD_HOLDOUT = "input 'holdout' names no nested fixture of that skill"


def _fire_too_few() -> str:
    import propose_skill_edit as loop  # noqa: PLC0415 — only fire-check needs it
    return (f"input 'skill' has fewer than {loop.MIN_FIXTURES} nested "
            "fixtures, the loop's minimum")


def fire_check(skill: str, holdout: str) -> None:
    """Refuse a skill or holdout the improvement loop would refuse, as far as
    the committed fixtures and harness/registries.yml can tell."""
    import propose_skill_edit as loop  # noqa: PLC0415 — only fire-check needs it
    run_eval = loop.run_eval
    try:
        names = loop.skill_fixtures(skill)
        fixtures = loop.load_fixtures(skill, names)
    except (ValueError, loop.Refusal):
        raise Rejected(FIRE_NOT_A_SKILL) from None
    url = next(iter(fixtures.values())).get("registry")
    if not isinstance(url, str):
        raise Rejected(FIRE_NOT_ROUTINE_REGISTRY)
    try:
        entry = run_eval.registry_for_url(
            run_eval.resolve_registries(None, None, loop.REPO_ROOT), url)
    except ValueError:
        raise Rejected(FIRE_NOT_ROUTINE_REGISTRY) from None
    if entry["name"] != REGISTRY_NAME or entry["url"] != REGISTRY_URL:
        raise Rejected(FIRE_NOT_ROUTINE_REGISTRY)
    if len(names) < loop.MIN_FIXTURES:
        raise Rejected(_fire_too_few())
    if holdout and holdout not in names:
        raise Rejected(FIRE_BAD_HOLDOUT)


def resolve(event_name: str, event_path: str) -> dict:
    return ingest.resolve(event_name, event_path, BRANCH_RE, BRANCH_SHAPE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--event-name", required=True)
    p.add_argument("--event-path", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--expect-sha", default="")
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("apply")
    p.add_argument("--staged", required=True, type=Path)
    p.add_argument("--tree", required=True, type=Path)
    p.add_argument("--skill-md", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--staged", required=True, type=Path)
    p.add_argument("--tree", required=True, type=Path)
    p.add_argument("--skill-md", required=True)
    p.add_argument("--registry-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--policy", type=Path, default=POLICY)
    p = sub.add_parser("merge-mode")
    p.add_argument("--tree", required=True, type=Path)
    p.add_argument("--plugin", required=True)
    p.add_argument("--policy", type=Path, default=POLICY)
    p = sub.add_parser("pr-body")
    p.add_argument("--staged", required=True, type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--mode", choices=MERGE_MODES, default="draft")
    p = sub.add_parser("fire-check")
    p.add_argument("skill")
    p.add_argument("--holdout", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "fire-check":
            fire_check(args.skill, args.holdout)
            return 0
        if args.command == "resolve":
            result = resolve(args.event_name, args.event_path)
        elif args.command == "validate":
            result = validate(args.repo, args.base, args.source, args.branch,
                              args.expect_sha or None, args.out)
        elif args.command == "apply":
            result = apply(args.staged, args.tree, args.skill_md)
        elif args.command == "merge-mode":
            result = effective_mode(args.tree, args.plugin, args.policy)
        elif args.command == "prepare":
            result = prepare(args.staged, args.tree, args.skill_md,
                             args.registry_sha, args.run_id, args.policy)
        else:
            if not BRANCH_RE.fullmatch(args.branch) \
                    or not ingest.SHA_RE.fullmatch(args.sha) \
                    or BRANCH_RE.fullmatch(args.branch).group("run_id") != args.run_id:
                raise Rejected("pr-body takes only validated values")
            sys.stdout.write(pr_body(args.staged, args.run_id, args.sha,
                                     args.branch, args.mode))
            return 0
    except Rejected as exc:
        message = "".join(c if c.isprintable() else repr(c)[1:-1]
                          for c in str(exc))
        print(f"rejected: {message}", file=sys.stderr)
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
