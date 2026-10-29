#!/usr/bin/env python3
"""Validate a routine's `claude/eval-guidance-improve-<run id>` branch and
open its pull request in example-adopter/_agent-guidance.

The guidance analog of scripts/improve_gate.py (ADR 0005, "Routine guidance
improve mode addendum"). In `guidance-improve` mode the eval routine runs
`scripts/propose_guidance_edit.py` on one section and, when its candidate is
ACCEPTED (exit 0: accepted and the patch passed _agent-guidance's own checks),
pushes one branch holding exactly

    eval-guidance-improve/<run id>/summary.json    the record (`<ts>.json`)
    eval-guidance-improve/<run id>/report.md       its PR body (`<ts>.pr-body.md`)
    eval-guidance-improve/<run id>/guidance.patch  its patch (`<ts>.patch`)

and nothing else. It never opens a pull request. The branch is UNTRUSTED
input (a model run wrote it): `.github/workflows/routine-guidance-improve-gate.yml`
runs this script from the default branch's checkout, and it reads the branch
only through git plumbing, reusing ingest_routine_results.py's and
improve_gate.py's helpers. Nothing from the branch is executed, imported or
checked out; `prepare` hands the validated patch to `git apply` in an
_agent-guidance checkout, as data.

`eval-guidance-improve/` is its own root: `claude/eval-guidance-improve-*`
also matches the results signal's `claude/eval-*` branch filter, and only the
path filters (`eval-results/**`, `eval-improve/**`, this root) keep each push
to its own gate.

What a branch must look like to pass `validate`:

  * its name is `claude/eval-guidance-improve-<YYYYMMDDTHHMMSSZ>-<6 hex>`;
  * against its merge-base with the default branch it ADDS exactly the three
    files above, regular and non-executable, under its own run id;
  * summary.json is an `accepted` record (schema 1, exit code 0, patch
    validation ok) for one section id (`SECTION_RE`), at a full guidance
    commit sha, whose row file is `agents-md/base.md` or
    `agents-md/sections/<name>.md` and not a skipped row, and whose split
    names exactly the section's fixtures on the default branch
    (propose_guidance_edit's own listing);
  * guidance.patch is a `git diff` of the row's file and, optionally,
    `agents-md/eval-coverage.yml`, `docs/guidance-impact.md` and `AGENTS.md`
    (what the proposer's `build_patch` writes), each at most once, every
    hunk's line counts agreeing with its header, and the row's file among
    them;
  * report.md opens with the proposer's own title for that section and holds
    no closing keyword or @mention;
  * every file is under its size cap and valid UTF-8 with no control
    characters.

`prepare` builds the _agent-guidance commit on a clean checkout of its default
branch tip, holding no credential: it refuses unless the measured sha is an
ancestor of the tip and `agents-md/` and `AGENTS.md` are unchanged between the
two (what merges is what was measured), and the tip's manifest row is the
record's; it applies the patch, re-copies `agents-md/base.md` to
`.claude/hooks/fleet-guidance.md` when base.md changed (that repository's
byte-identical fleet-memory payload), checks WHAT changed (`check_content`:
nothing of the section's file outside the section's extent, no new `##`
heading, only the row's `bytes` in the manifest, one inserted impact entry
for the section; AGENTS.md is left to that repository's own staleness
check, which regenerates it), and refuses any changed path outside
repo-settings ADR 0014's allowlist for _agent-guidance: `agents-md/**`,
`AGENTS.md`, `docs/guidance-impact.md`, and `.claude/hooks/fleet-guidance.md`
only as a byte-identical copy of `agents-md/base.md`. The merge mode is
`evals/improve-policy.yml`'s `improve_merge_mode` (improve_gate.merge_mode):
there is no plugin runtime in _agent-guidance to lower it.

`open-pr` writes, with the eval-improve App's token minted for
_agent-guidance alone (`GUIDANCE_TOKEN`; repo-settings ADR 0014). With no
token it opens nothing and says why, exit 0: a missing secret or a failed
mint is not a gate failure. Otherwise, unless the target branch
`eval-guidance-improve/<section>` already exists or any guidance-loop pull
request (`eval-guidance-improve/*`, `retire-guidance/*`) is open there (they
all write `AGENTS.md` and `docs/guidance-impact.md`, so a second one would
conflict), it pushes the commit (verified with `git merge-base
--is-ancestor`), opens the pull request (a draft unless the mode is `auto`),
labels it `agent-authored`, and in `auto` mode enables auto-merge by merge
commit with `--match-head-commit` and reads it back. Every GitHub call goes
through an injectable `gh(args, stdin)` and the push through `push(tree,
branch)`, so the tests need no network.

`fire-check` is routine-eval-fire.yml's offline check before the bearer
exists: the section id's shape, at least propose_guidance_edit's
`MIN_FIXTURES` fixtures declaring the `full` arm (its `fixture_layout_discover`),
a holdout empty or one of them, and, with `--guidance DIR`, a manifest row that
is not skipped (its `load_row`).

Subcommands (output is `key=value` lines of validated values, for
$GITHUB_OUTPUT):

  resolve    --event-name NAME --event-path PATH
  validate   --repo DIR --base REF --source REF --branch NAME [--expect-sha SHA] --out DIR
  prepare    --staged DIR --tree DIR --section ID --guidance-sha SHA --run-id ID [--policy FILE]
  pr-body    --staged DIR --run-id ID --sha SHA --branch NAME [--mode draft|auto]
  open-pr    --staged DIR --tree DIR --section ID --run-id ID --sha SHA --branch NAME
             --base BRANCH --commit SHA --mode draft|auto
  fire-check SECTION [--holdout=FIXTURE] [--guidance DIR]   (no output; exit 0 or 1)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO_ROOT / "harness"))

import improve_gate  # noqa: E402
import improve_outcomes  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
from ingest_routine_results import Rejected  # noqa: E402

BRANCH_RE = re.compile(rf"claude/eval-guidance-improve-(?P<run_id>{ingest.RUN_ID})")
BRANCH_SHAPE = "claude/eval-guidance-improve-<run id>"
SOURCE_ROOT = improve_outcomes.KINDS["guidance-improve"]["root"]
SIZE_CAPS = {"summary.json": 512 * 1024, "report.md": 64 * 1024,
             "guidance.patch": 256 * 1024}
SECTION_RE = improve_outcomes.SECTION_RE
GUIDANCE_REPO = "example-adopter/_agent-guidance"
#: One branch per section in _agent-guidance, as `eval-improve/<skill>` is
#: one per skill in agentskills.
TARGET_PREFIX = "eval-guidance-improve/"
TARGET_BRANCH = TARGET_PREFIX + "{section}"
#: Every guidance-loop head in _agent-guidance: improvements and removals
#: (scripts/retire_skill.py's `retire-guidance/`). One open at a time.
LOOP_PREFIXES = (TARGET_PREFIX, "retire-guidance/")
AGENT_LABEL = "agent-authored"
AGENT_MARKER = improve_gate.AGENT_MARKER
AGENT_TRAILER = "Agent: claude-code"
#: The App token routine-guidance-improve-gate.yml mints for _agent-guidance.
TOKEN_ENV = "GUIDANCE_TOKEN"
NO_TOKEN = ("no write credential for _agent-guidance reached this run (the "
            "eval-improve App secrets are unset, or the mint failed)")

BASE_MD = "agents-md/base.md"
SECTIONS_DIR = "agents-md/sections/"
MANIFEST = "agents-md/eval-coverage.yml"
IMPACT = "docs/guidance-impact.md"
AGENTS = "AGENTS.md"
#: _agent-guidance's fleet-memory payload: a byte-identical copy of base.md.
PAYLOAD = ".claude/hooks/fleet-guidance.md"
#: A section file: base.md, or one file directly under agents-md/sections/.
SECTION_FILE_RE = re.compile(r"agents-md/(?:base\.md|sections/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md)")
#: A fixture key, relative to evals/ (propose_guidance_edit's listing).
FIXTURE_RE = re.compile(r"guidance/[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
                        r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,127})?")
HEADING_MAX = 300

#: propose_guidance_edit.run_pipeline's record keys (the suite reads them
#: from its AST and checks they stay inside these sets).
RECORD_REQUIRED = ("schema", "section", "timestamp", "row", "guidance", "split",
                   "trials", "proposal", "baseline", "candidate", "patch_validation",
                   "files", "status", "exit_code", "reasons")
RECORD_ALLOWED = RECORD_REQUIRED + ("local_exhibit", "no_judge", "models", "tokens_basis",
                                    "runs", "table", "phase")
REPORT_TITLE = "## Proposed `{section}` guidance edit (validation-gated)"
DIFF_HEADER_RE = re.compile(r"diff --git a/(?P<a>\S+) b/(?P<b>\S+)")
INDEX_RE = re.compile(r"index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: 100644)?")
POLICY = improve_gate.POLICY


# ---------------------------------------------------------------------------
# Content.

def check_record(doc, where: str, fixtures: list[str]) -> dict:
    """The accepted record's validated values. `fixtures` is the section's
    fixture listing on the default branch (propose_guidance_edit's
    `fixture_layout_discover`); the split must name exactly those."""
    ingest._object(doc, where, RECORD_ALLOWED, RECORD_REQUIRED)
    improve_gate.bounded(doc, where)
    if doc["schema"] != 1 or isinstance(doc["schema"], bool):
        raise Rejected(f"{where}: unknown schema")
    if doc["status"] != "accepted" or doc["exit_code"] != 0 \
            or isinstance(doc["exit_code"], bool):
        raise Rejected(f"{where}: status is not accepted; the routine pushes "
                       "only an accepted candidate")
    section = doc["section"]
    if not isinstance(section, str) or not SECTION_RE.fullmatch(section):
        raise Rejected(f"{where}: bad section id")
    if not isinstance(doc["timestamp"], str) \
            or not ingest.TIMESTAMP_RE.fullmatch(doc["timestamp"]):
        raise Rejected(f"{where}: bad timestamp")
    reasons = doc["reasons"]
    if not isinstance(reasons, list) or not reasons \
            or not all(isinstance(r, str) for r in reasons):
        raise Rejected(f"{where}: reasons must be a list of strings")
    trials = doc["trials"]
    ingest._object(trials, f"{where}: trials", ("initial", "total"), ("initial", "total"))
    for key in ("initial", "total"):
        ingest._integer(trials[key], f"{where}: trials", 1, ingest.MAX_TRIALS)
    validation = doc["patch_validation"]
    if not isinstance(validation, dict) or validation.get("ok") is not True:
        raise Rejected(f"{where}: the patch did not pass _agent-guidance's checks")

    row = doc["row"]
    ingest._object(row, f"{where}: row", ("file", "heading", "status"), ("file", "heading"))
    if not isinstance(row["file"], str) or not SECTION_FILE_RE.fullmatch(row["file"]):
        raise Rejected(f"{where}: row file is not agents-md/base.md or a "
                       "file under agents-md/sections/")
    heading = row["heading"]
    if not isinstance(heading, str) or not heading.strip() or len(heading) > HEADING_MAX \
            or any(not c.isprintable() for c in heading):
        raise Rejected(f"{where}: bad row heading")
    if row.get("status") == "skipped":
        raise Rejected(f"{where}: the section's row is skipped")
    found = doc["guidance"]
    ingest._object(found, f"{where}: guidance", ("path", "sha"), ("sha",))
    if not isinstance(found["sha"], str) or not ingest.SHA_RE.fullmatch(found["sha"]):
        raise Rejected(f"{where}: guidance sha is not a 40-hex commit id")

    split = doc["split"]
    keys = ("rotation", "train", "validation", "holdout")
    ingest._object(split, f"{where}: split", keys, keys)
    train = split["train"] if isinstance(split["train"], list) else [None]
    names = train + [split["validation"], split["holdout"]]
    if not all(isinstance(n, str) and FIXTURE_RE.fullmatch(n) for n in names) \
            or len(set(names)) != len(names):
        raise Rejected(f"{where}: bad fixture name in split")
    if sorted(names) != sorted(fixtures):
        raise Rejected(f"{where}: the split is not the section's fixtures on the "
                       "default branch")
    return {"section": section, "guidance_sha": found["sha"], "section_file": row["file"],
            "heading": heading}


def check_patch(text: str, where: str, section_file: str) -> list[str]:
    """The paths of a multi-file `git diff` (propose_guidance_edit's
    `build_patch`): the row's file and only the proposer's three bookkeeping
    files, each once, every header exact and every hunk's counts agreeing."""
    if not text.endswith("\n"):
        raise Rejected(f"{where}: does not end with a newline")
    allowed = {section_file, MANIFEST, IMPACT, AGENTS}
    lines = text[:-1].split("\n")
    i, paths = 0, []
    while i < len(lines):
        header = DIFF_HEADER_RE.fullmatch(lines[i])
        if not header or header["a"] != header["b"]:
            raise Rejected(f"{where}: line {i + 1} is not a `diff --git a/P b/P` header")
        path = header["a"]
        if path not in allowed or path in paths:
            raise Rejected(f"{where}: changes a path outside the section's file "
                           "and its bookkeeping files, or one path twice")
        i += 1
        if i < len(lines) and INDEX_RE.fullmatch(lines[i]):
            i += 1
        if lines[i:i + 2] != [f"--- a/{path}", f"+++ b/{path}"]:
            raise Rejected(f"{where}: {path!r} has no plain ---/+++ header pair "
                           "(a mode change, rename, binary or new file)")
        i, hunks, changed = improve_gate.check_hunks(lines, i + 2, where)
        if not hunks or not changed:
            raise Rejected(f"{where}: {path!r} changes nothing")
        paths.append(path)
    if section_file not in paths:
        raise Rejected(f"{where}: does not change the section's file")
    return paths


def check_report(text: str, where: str, section: str) -> None:
    if text.split("\n", 1)[0] != REPORT_TITLE.format(section=section):
        raise Rejected(f"{where}: does not open with the proposer's title for "
                       "this section")
    improve_gate.check_body_text(text, where)


def section_fixtures(evals_dir: Path, section: str) -> list[str]:
    """propose_guidance_edit's own listing of the section's fixtures that
    declare the `full` arm, as paths relative to `evals_dir`."""
    import propose_guidance_edit as proposer  # noqa: PLC0415 - heavy, only here
    try:
        return proposer.fixture_layout_discover(evals_dir, section)
    except proposer.Refusal:
        raise Rejected("a guidance fixture on the default branch is unreadable") from None


# ---------------------------------------------------------------------------
# Subcommands.

def validate(repo: str, base: str, source: str, branch: str, expect_sha: str | None,
             out: Path, evals_dir: Path | None = None) -> dict:
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
            raise Rejected(f"{name!r}: not a file a guidance improve branch carries")
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
    doc = ingest.load_json(ingest.decode_text(data, where), where)
    section = doc.get("section") if isinstance(doc, dict) else None
    if not isinstance(section, str) or not SECTION_RE.fullmatch(section):
        raise Rejected(f"{where}: bad section id")
    found = check_record(doc, where, section_fixtures(
        evals_dir or Path(repo) / "evals", section))
    where, data = files["guidance.patch"]
    check_patch(ingest.decode_text(data, where), where, found["section_file"])
    where, data = files["report.md"]
    check_report(ingest.decode_text(data, where), where, found["section"])

    if out.exists() and any(out.iterdir()):
        raise Rejected("the staging directory is not empty")
    out.mkdir(parents=True, exist_ok=True)
    for kind, (_, data) in files.items():
        (out / kind).write_bytes(data)
    return {"run_id": run_id, "sha": tip, "section": found["section"],
            "guidance_sha": found["guidance_sha"], "section_file": found["section_file"],
            "target_branch": TARGET_BRANCH.format(section=found["section"])}


def _no_symlink(tree: Path, rel: str) -> Path:
    try:
        return improve_gate._no_symlink(tree, rel)
    except Rejected:
        raise Rejected(f"{rel!r}: the _agent-guidance path has a symlink") from None


def allowed_change(tree: Path, path: str) -> bool:
    """Whether a changed path is inside repo-settings ADR 0014's allowlist
    for _agent-guidance."""
    if path.startswith("agents-md/") or path in (AGENTS, IMPACT):
        return True
    if path == PAYLOAD:
        payload, base = _no_symlink(tree, PAYLOAD), _no_symlink(tree, BASE_MD)
        return payload.is_file() and base.is_file() \
            and payload.read_bytes() == base.read_bytes()
    return False


CONTENT_UNREADABLE = "a changed _agent-guidance file is missing or not UTF-8 text"
CONTENT_OUTSIDE = ("the patch changes the section's file outside the section's "
                   "own extent, or its heading line")
CONTENT_HEADINGS = ("the patch adds, removes or retitles a `##` heading in the "
                    "section's file")
CONTENT_MANIFEST = ("the patch changes agents-md/eval-coverage.yml beyond the "
                    "section row's `bytes`, or that value is not the new extent's size")
CONTENT_IMPACT = ("the patch does more to docs/guidance-impact.md than insert one "
                  "dated entry naming the section")
CONTENT_PAYLOAD = ".claude/hooks/fleet-guidance.md is not a byte-identical copy of base.md"


def _read_text(tree: Path, rel: str) -> str:
    path = _no_symlink(tree, rel)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Rejected(CONTENT_UNREADABLE) from None


def _inserted(old: str, new: str) -> bool:
    """Whether `new` is `old` with one run of text inserted at one point
    (nothing removed or rewritten)."""
    if len(new) < len(old):
        return False
    k = 0
    while k < len(old) and old[k] == new[k]:
        k += 1
    return new.endswith(old[k:])


def check_content(before: dict, after: dict, section: str, section_file: str,
                  heading: str) -> None:
    """What the applied patch changed, not only where (the branch is
    untrusted, and base.md reaches every repository in the fleet), checked
    with propose_guidance_edit's own extent finder (harness/guidance.py's
    `h2_extents`, a markdown parse, never a regex):

      * the section's file keeps its `##` headings exactly (so the new
        extent holds no heading but its own: the proposer's
        `normalize_section` rule), is byte-identical outside the section's
        extent, and keeps the extent's heading line;
      * agents-md/eval-coverage.yml, parsed with yaml, differs at most in
        the section row's `bytes`, which must equal the new extent's UTF-8
        size (`extent_bytes`, check-guidance-coverage.js's count);
      * docs/guidance-impact.md is the old text with one insertion (the
        proposer files its entry ahead of the newest dated one), and the
        dated `##` headings are the old ones plus one naming the section.

    AGENTS.md is left to _agent-guidance's own required checks: its
    staleness check regenerates it from agents-md/ and fails on any drift.
    Every refusal is fixed text."""
    import guidance  # noqa: PLC0415 - the harness's markdown reader, only here
    import propose_guidance_edit as proposer  # noqa: PLC0415 - heavy, only here
    old, new = before[section_file], after[section_file]
    # The file's `##` headings, in order, are unchanged: no heading added,
    # removed or retitled, so the extent below is the section's whole span.
    if [h["heading"] for h in guidance.h2_extents(old)] \
            != [h["heading"] for h in guidance.h2_extents(new)]:
        raise Rejected(CONTENT_HEADINGS)
    try:
        old_span, new_span = proposer.extent_of(old, heading), proposer.extent_of(new, heading)
        new_bytes = proposer.extent_bytes(new, heading)
    except proposer.Refusal:
        raise Rejected(CONTENT_OUTSIDE) from None
    old_extent = old[old_span["start"]:old_span["end"]]
    new_extent = new[new_span["start"]:new_span["end"]]
    if old[:old_span["start"]] != new[:new_span["start"]] \
            or old[old_span["end"]:] != new[new_span["end"]:] \
            or old_extent.splitlines()[:1] != new_extent.splitlines()[:1]:
        raise Rejected(CONTENT_OUTSIDE)

    try:
        rows_old = yaml.safe_load(before[MANIFEST])
        rows_new = yaml.safe_load(after[MANIFEST])
    except yaml.YAMLError:
        raise Rejected(CONTENT_MANIFEST) from None
    if not isinstance(rows_old, list) or not isinstance(rows_new, list) \
            or len(rows_old) != len(rows_new):
        raise Rejected(CONTENT_MANIFEST)
    target = 0
    for row_old, row_new in zip(rows_old, rows_new):
        if isinstance(row_old, dict) and row_old.get("id") == section:
            target += 1
            if not isinstance(row_new, dict) or row_new.get("id") != section \
                    or {k: v for k, v in row_old.items() if k != "bytes"} \
                    != {k: v for k, v in row_new.items() if k != "bytes"} \
                    or isinstance(row_new.get("bytes"), bool) \
                    or row_new.get("bytes") != new_bytes:
                raise Rejected(CONTENT_MANIFEST)
        elif row_old != row_new:
            raise Rejected(CONTENT_MANIFEST)
    if target != 1:
        raise Rejected(CONTENT_MANIFEST)

    old, new = before[IMPACT], after[IMPACT]
    if old != new:
        dated = [s["heading"] for s in guidance.h2_extents(new)
                 if proposer.DATED_HEADING.match(s["heading"])]
        was = [s["heading"] for s in guidance.h2_extents(old)
               if proposer.DATED_HEADING.match(s["heading"])]
        added = list(dated)
        for heading_text in was:
            if heading_text not in added:
                raise Rejected(CONTENT_IMPACT)
            added.remove(heading_text)
        if not _inserted(old, new) or len(added) != 1 \
                or f" — {section} — " not in added[0]:
            raise Rejected(CONTENT_IMPACT)


def prepare(staged: Path, tree: Path, section: str, guidance_sha: str, run_id: str,
            policy: Path = POLICY) -> dict:
    """Build the _agent-guidance commit on `tree`, a clean checkout of its
    default branch tip with `guidance_sha` fetched; see the module
    docstring. Returns the merge mode, the base and the commit."""
    if not SECTION_RE.fullmatch(section) or not ingest.SHA_RE.fullmatch(guidance_sha) \
            or not re.fullmatch(ingest.RUN_ID, run_id):
        raise Rejected("prepare takes only validated values")
    repo = str(tree)
    if ingest.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise Rejected("the _agent-guidance checkout is not clean")
    base = ingest.resolve_commit(repo, "HEAD")
    if subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", guidance_sha, base],
                      capture_output=True, check=False).returncode:
        raise Rejected("the measured guidance sha is not on _agent-guidance's "
                       "default branch")
    # Every section's text and AGENTS.md (generated from all of it) must be
    # what was measured: the proposer's corpus is the whole guidance.
    for rel in ("agents-md", AGENTS):
        if ingest.git(repo, "rev-parse", f"{guidance_sha}:{rel}") \
                != ingest.git(repo, "rev-parse", f"HEAD:{rel}"):
            raise Rejected("the guidance changed on _agent-guidance's default "
                           "branch since it was measured; measure it again")
    record = json.loads((staged / "summary.json").read_text(encoding="utf-8"))
    import guidance  # noqa: PLC0415 - the harness's manifest reader, only here
    try:
        row = guidance.find_row(guidance.load_manifest(tree), section, tree)
    except guidance.GuidanceError:
        raise Rejected("the section has no row in _agent-guidance's manifest") from None
    if row.get("status") == "skipped" or row.get("file") != record["row"]["file"] \
            or row.get("heading") != record["row"]["heading"]:
        raise Rejected("the manifest row is not the one the record measured")
    for rel in ("agents-md", row["file"], MANIFEST, IMPACT, AGENTS, ".claude", PAYLOAD):
        _no_symlink(tree, rel)

    before = {rel: _read_text(tree, rel) for rel in (row["file"], MANIFEST, IMPACT)}
    patch = str((staged / "guidance.patch").resolve())
    for args in (("apply", "--check", patch), ("apply", patch)):
        # Raw git is fine here (#343): the gate's own _agent-guidance
        # checkout at its default branch tip, never an agent-touched
        # workspace, so harness/workspace_git.py does not apply.
        if subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          check=False).returncode:
            raise Rejected("the patch does not apply to _agent-guidance's "
                           "default branch")
    check_content(before, {rel: _read_text(tree, rel) for rel in before}, section,
                  row["file"], row["heading"])
    payload = tree / PAYLOAD
    if row["file"] == BASE_MD and payload.is_file():
        payload.write_bytes((tree / BASE_MD).read_bytes())
        if payload.read_bytes() != (tree / BASE_MD).read_bytes():
            raise Rejected(CONTENT_PAYLOAD)
    changed = [entry[3:] for entry in ingest.git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").decode().split("\0")
        if entry]
    if not changed or any(not allowed_change(tree, p) for p in changed):
        raise Rejected("the change reaches past repo-settings ADR 0014's "
                       "allowlist for _agent-guidance")
    ingest.git(repo, "add", "-A", "--", *changed)
    ingest.git(repo, *improve_gate.COMMIT_IDENTITY, "commit", "-q", "-m",
               f"Edit guidance section {section} from improvement run {run_id}\n\n"
               "skills-evals' guidance improvement loop measured the edit with "
               "scripts/propose_guidance_edit.py and accepted it; the patch "
               f"passed this repository's own checks.\n\n{AGENT_TRAILER}")
    return {"mode": improve_gate.merge_mode(policy), "base_sha": base,
            "commit": ingest.resolve_commit(repo, "HEAD")}


def pr_body(staged: Path, run_id: str, sha: str, branch: str, mode: str = "draft") -> str:
    report = (staged / "report.md").read_text(encoding="utf-8")
    if mode == "auto":
        review = ("This pull request merges itself (merge commit) once this "
                  "repository's required checks pass, with no human review: the owner "
                  "decided on 2026-10-26 that accepted loop changes merge with no "
                  "human in the loop (repo-settings ADRs 0012 and 0014). Where the "
                  "report below says to review before merging, this paragraph "
                  "supersedes it.")
    else:
        review = ("A person reviews and merges this, or declines it; nothing "
                  "merges itself (skills-evals evals/improve-policy.yml is `draft`).")
    return "\n".join([
        f"From the skills-evals guidance improvement loop, run `{run_id}`: the eval "
        f"routine pushed [`{branch}`](https://github.com/example-adopter/skills-evals/"
        f"tree/{sha}) at `{sha}`, and `routine-guidance-improve-gate.yml` validated it "
        "without running it and applied its `guidance.patch` here (and re-copied "
        f"`{BASE_MD}` to `{PAYLOAD}` when the section is in base.md).",
        "", review, "", "---", "", report.rstrip("\n"), "", AGENT_MARKER, ""])


def default_gh(args: list, stdin: str | None = None, token: str | None = None) -> str:
    """retire_skill.default_gh, its failure as a Rejected naming only the
    exit code (never gh's output, which can carry an API body)."""
    import retire_skill  # noqa: PLC0415 - shared gh/push plumbing
    try:
        return retire_skill.default_gh(args, stdin, token)
    except retire_skill.RetireError as exc:
        raise Rejected(str(exc)) from None


def default_push(tree: Path, branch: str, token: str | None) -> None:
    """retire_skill.default_push: push, fetch back, `merge-base --is-ancestor`."""
    import retire_skill  # noqa: PLC0415 - shared gh/push plumbing
    try:
        retire_skill.default_push(tree, branch, token)
    except retire_skill.RetireError as exc:
        raise Rejected(str(exc)) from None


PR_URL_RE = re.compile(rf"https://github\.com/{re.escape(GUIDANCE_REPO)}/pull/(\d+)")


def open_pr(token: str | None, gh, push, *, section: str, run_id: str, base: str,
            commit: str, mode: str, body: str) -> dict:
    """Open the pull request (module docstring). {opened: url or "",
    reason: why nothing was opened, or ""}."""
    target = TARGET_BRANCH.format(section=section)
    if not token:
        return {"opened": "", "reason": NO_TOKEN}
    heads = gh(["api", "--paginate", f"repos/{GUIDANCE_REPO}/pulls?state=open&per_page=100",
                "--jq", ".[].head.ref"])
    if any(h.startswith(LOOP_PREFIXES) for h in heads.splitlines()):
        return {"opened": "", "reason": "a guidance-loop pull request is already open "
                                        "in _agent-guidance; this candidate waits for it"}
    existing = gh(["api", f"repos/{GUIDANCE_REPO}/git/matching-refs/heads/{target}",
                   "--jq", ".[].ref"])
    if f"refs/heads/{target}" in existing.splitlines():
        return {"opened": "", "reason": f"{target} already exists in _agent-guidance; "
                                        "a candidate for this section is already waiting"}
    push(target)
    draft = [] if mode == "auto" else ["--draft"]
    url = gh(["pr", "create", "-R", GUIDANCE_REPO, *draft, "--base", base, "--head", target,
              "--title", f"Proposed {section} guidance edit (improvement run {run_id})",
              "--body-file", "-"], body).strip()
    match = PR_URL_RE.fullmatch(url)
    if not match:
        raise Rejected("creating the pull request printed no pull request URL")
    # Labeled before auto-merge: a pull request that could not be marked as
    # the loop's stays open for a person.
    gh(["api", "-X", "POST", f"repos/{GUIDANCE_REPO}/issues/{match.group(1)}/labels",
        "-f", f"labels[]={AGENT_LABEL}"])
    if mode == "auto":
        gh(["pr", "merge", url, "-R", GUIDANCE_REPO, "--auto", "--merge",
            "--match-head-commit", commit])
        method = gh(["pr", "view", url, "-R", GUIDANCE_REPO, "--json", "autoMergeRequest",
                     "--jq", '.autoMergeRequest.mergeMethod // ""']).strip()
        if method != "MERGE":
            raise Rejected("auto-merge (merge commit) is not enabled on the pull request")
    return {"opened": url, "reason": ""}


FIRE_BAD_SECTION = "input 'section' is not a guidance section id"
FIRE_TOO_FEW = "input 'section' has fewer than {n} fixtures declaring the full arm"
FIRE_BAD_HOLDOUT = "input 'holdout' names no fixture of that section"
FIRE_BAD_ROW = "input 'section' has no measurable row in _agent-guidance's manifest"


def fire_check(section: str, holdout: str, guidance_dir: Path | None = None,
               evals_dir: Path = REPO_ROOT / "evals") -> None:
    """Refuse a section or holdout propose_guidance_edit would refuse, as far
    as the committed fixtures (and, with `guidance_dir`, the manifest) can
    tell. Every refusal is fixed text."""
    import propose_guidance_edit as proposer  # noqa: PLC0415 - heavy, only here
    if not SECTION_RE.fullmatch(section):
        raise Rejected(FIRE_BAD_SECTION)
    try:
        found = proposer.fixture_layout_discover(evals_dir, section)
    except proposer.Refusal:
        raise Rejected(FIRE_BAD_SECTION) from None
    if len(found) < proposer.MIN_FIXTURES:
        raise Rejected(FIRE_TOO_FEW.format(n=proposer.MIN_FIXTURES))
    if holdout and holdout not in found:
        raise Rejected(FIRE_BAD_HOLDOUT)
    if guidance_dir is not None:
        try:
            proposer.load_row(guidance_dir, section)
        except proposer.Refusal:
            raise Rejected(FIRE_BAD_ROW) from None


def resolve(event_name: str, event_path: str) -> dict:
    return ingest.resolve(event_name, event_path, BRANCH_RE, BRANCH_SHAPE)


def main(argv: list[str] | None = None, *, env=None, gh=None, push=None,
         out=sys.stdout) -> int:
    env = os.environ if env is None else env
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
    p = sub.add_parser("prepare")
    p.add_argument("--staged", required=True, type=Path)
    p.add_argument("--tree", required=True, type=Path)
    p.add_argument("--section", required=True)
    p.add_argument("--guidance-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--policy", type=Path, default=POLICY)
    for name in ("pr-body", "open-pr"):
        p = sub.add_parser(name)
        p.add_argument("--staged", required=True, type=Path)
        p.add_argument("--run-id", required=True)
        p.add_argument("--sha", required=True)
        p.add_argument("--branch", required=True)
        p.add_argument("--mode", choices=improve_gate.MERGE_MODES, default="draft")
        if name == "open-pr":
            p.add_argument("--tree", required=True, type=Path)
            p.add_argument("--section", required=True)
            p.add_argument("--base", required=True)
            p.add_argument("--commit", required=True)
    p = sub.add_parser("fire-check")
    p.add_argument("section")
    p.add_argument("--holdout", default="")
    p.add_argument("--guidance", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "fire-check":
            fire_check(args.section, args.holdout, args.guidance)
            return 0
        if args.command == "resolve":
            result = resolve(args.event_name, args.event_path)
        elif args.command == "validate":
            result = validate(args.repo, args.base, args.source, args.branch,
                              args.expect_sha or None, args.out)
        elif args.command == "prepare":
            result = prepare(args.staged, args.tree, args.section, args.guidance_sha,
                             args.run_id, args.policy)
        else:
            match = BRANCH_RE.fullmatch(args.branch)
            if not match or match.group("run_id") != args.run_id \
                    or not ingest.SHA_RE.fullmatch(args.sha):
                raise Rejected(f"{args.command} takes only validated values")
            body = pr_body(args.staged, args.run_id, args.sha, args.branch, args.mode)
            if args.command == "pr-body":
                out.write(body)
                return 0
            if not SECTION_RE.fullmatch(args.section) \
                    or not ingest.SHA_RE.fullmatch(args.commit) \
                    or not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", args.base):
                raise Rejected("open-pr takes only validated values")
            token = env.get(TOKEN_ENV) or None
            result = open_pr(
                token, gh or (lambda a, stdin=None: default_gh(a, stdin, token)),
                push or (lambda branch: default_push(args.tree, branch, token)),
                section=args.section, run_id=args.run_id, base=args.base,
                commit=args.commit, mode=args.mode, body=body)
    except Rejected as exc:
        message = "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(exc))
        print(f"rejected: {message}", file=sys.stderr)
        return 1
    for key, value in result.items():
        out.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
