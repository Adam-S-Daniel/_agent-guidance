#!/usr/bin/env python3
"""Run an eval fixture.

Usage:
    python3 harness/run_eval.py evals/<skill> --arm objective-only
    python3 harness/run_eval.py evals/<skill> --arm both [--registry NAME=PATH ...]
        [--roster PATH] [--no-judge | --judge {auto,always}]
    python3 harness/run_eval.py evals/guidance/<id> --arm both [--guidance PATH]
    python3 harness/run_eval.py evals/<skill> --arm both [--fixture NAME]
        [--trials N]

`evals/<skill>` is either one flat fixture (`evals/<skill>/fixture.yaml`) or
a skill directory of nested ones (`evals/<skill>/<name>/fixture.yaml`, each
with its own `seed/`), never both. Named as a skill directory, every nested
fixture runs, or the one `--fixture NAME` selects; a nested fixture can also
be named by its own directory. `--trials N` (default 1) runs each arm N times
and writes the aggregate beside the per-trial summaries — see "Fixture layout"
and "Trials" below (#66).

For a skill fixture, the model precedence is `--model` > the fixture's
`model:` > the committed roster > error.

`--arm objective-only` scores a workspace as-is (no agent invocation) — the
pristine seed should FAIL the fixture's checks; a correctly reworked copy
should PASS. `--arm with_skill|without_skill|both` runs the agent under test
(the Claude Code CLI, headless) on a fresh copy of the seed, scores it with
the objective checks and the LLM judge, and writes a summary + report under
`--results-dir` (default `results/`). For a with/without pair the judge is
conditional (#371): see "Conditional judging" below, and `--judge always` to
judge every trial regardless.

TWO SUBJECTS. `subject: skill` (the default) copies one skill from a registry
into `<workspace>/.claude/skills/` and runs with `--setting-sources project`.
`subject: guidance` (#97) delivers a payload assembled from an
`_agent-guidance` checkout into a FRESH per-arm config dir, through the real
fleet-memory hook, and runs with `--setting-sources user,project` — with a
magic-token guard per arm that makes a mis-delivered arm INCONCLUSIVE (exit 2)
rather than a quiet number. See `_run_guidance` below, harness/guidance.py,
and DESIGN.md "Guidance subject".
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fnmatch
import functools
import hashlib
import json
import math
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from cli_json import (bounded_tool_trace, failed_run_detail,  # noqa: E402
                      normalize_cli_result, rate_limit_events, secret_values, tool_events)
import guidance  # noqa: E402
import run_canary  # noqa: E402
from harness_repo import _git_out, harness_clone_root, harness_git_common_dir  # noqa: E402
import context  # noqa: E402
import delivery  # noqa: E402
import guidance_violations  # noqa: E402
import judge_policy  # noqa: E402
from scorers import judge, objective  # noqa: E402
import seed_prep  # noqa: E402
import answer_leak  # noqa: E402
import budget  # noqa: E402
import workspace_git  # noqa: E402


FIXTURE_FILE = "fixture.yaml"
SEED_DIR = "seed"

# The run directory's name: UTC, second resolution, sortable as text.
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_TIMESTAMP_RE = re.compile(r"\d{8}T\d{6}Z")


def _git_common_dir(root: Path) -> Path | None:
    """The trusted harness checkout's common Git directory, read as data.

    Not `_git`: the workspace Git helper refuses a linked worktree's `.git`
    file (#343), and the harness checkout is not an agent workspace.
    """
    dot_git = root / ".git"
    try:
        if dot_git.is_dir():
            return dot_git.resolve()
        text = dot_git.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        git_dir = (root / text[len("gitdir:"):].strip()).resolve()
        commondir = git_dir / "commondir"
        if commondir.is_file():
            return (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
        return git_dir
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def scorer_read_denied(args: argparse.Namespace, *, harness_root: Path | None = None,
                       fixture: dict | None = None) -> list[Path]:
    """Trusted scorer roots matching PR #345's checkout/output/profile coverage.

    Merge seam: after #345 lands, replace this collection with paths from
    arm_read_denied(run_checkouts(args), outputs=run_outputs(args)).

    Git's common directory identifies the original clone even in a worktree.
    Results and session archives hold earlier arms' evidence and stay hidden.
    """
    if fixture is not None and not any(
            check.get("type") in ("command_succeeds", "repo_tests")
            for check in fixture.get("objective_checks", [])):
        # No workspace code will run; pure checks need no sandbox or Git probe.
        return []
    root = Path(harness_root if harness_root is not None else Path(__file__).resolve().parents[1]).resolve()
    git_dir = _git_common_dir(root)
    clone = git_dir.parent if git_dir is not None and git_dir.name == ".git" else root
    roots = [root, clone.parent, Path.home(), session_archive_dir().parent]
    if git_dir is not None:
        roots.append(git_dir)
    results = getattr(args, "results_dir", None)
    if results is not None:
        roots.append(Path(results))
    roots.extend(Path(p) for p in getattr(args, "read_deny", None) or ())
    # ADR 0012 context sources are explicit checkouts too (--context-repo).
    roots.extend(Path(p) for p in (getattr(args, "context_repos", None) or {}).values())
    roots.append(Path.home() / ".claude")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser())
    try:
        registries = resolve_registries(
            getattr(args, "registry", None), os.environ.get("SKILLS_EVALS_REGISTRIES"),
            root, os.environ.get("AGENTSKILLS_DIR"))
    except ValueError:
        # Match run_checkouts: invalid overrides are rejected by main.
        registries = {}
    roots.extend(entry["path"] for entry in registries.values())
    roots.append(guidance.resolve_guidance_dir(
        getattr(args, "guidance", None), os.environ.get("AGENT_GUIDANCE_DIR"), root))
    return list(dict.fromkeys(path.resolve() for path in roots))


def load_fixture(eval_dir: Path) -> dict:
    """The fixture, or a named configuration error.

    A-N1-2. A-N1 typed the mapping-valued KEYS; the container that holds them
    was never typed at all. Measured on f9115ce: a fixture whose root is a
    LIST was `AttributeError: 'list' object has no attribute 'get'` and rc 1,
    and an EMPTY file (YAML `None`) was `TypeError: argument of type
    'NoneType' is not iterable` raised inside A-N1's own `_require_mapping` —
    both outside the rc-2 contract, and the second one inside the very
    predicate added to keep shapes out of the harness.
    """
    path = eval_dir / FIXTURE_FILE
    return context.load_fixture_yaml(path.read_bytes(), path)


def fixture_digest(eval_dir: Path) -> str | None:
    """The fixture's content digest, `fixture_digest` in every skill summary
    (#70: runs pool only when their fixtures are identical), or None, and the
    summary then carries no digest, when the directory is not in a Git
    checkout or does not match what is staged there. The sha256 of `git
    ls-files -s -z -- .` in the fixture directory: each tracked file's mode,
    blob sha and path relative to that directory. An ignored file (a
    `node_modules` that scripts/dev_setup.sh installs under a seed, say) does
    not count. A symlink (mode 120000) must point at a regular file tracked
    in the same checkout, and folds in that target's blob sha, so a change to
    the target is a change to the fixture; one pointing at anything else (a
    tracked symlink, an untracked or ignored file, a directory, a path
    outside the checkout) gives None.

    Matching what is staged is checked here, never by `git ls-files -m`,
    which runs the checkout's own clean filters (`.gitattributes` plus
    `filter.<name>.clean` is code from the checkout): each tracked working
    file, or symlink's target text, is hashed in Python as a Git blob and
    compared with its staged sha, and its executable bit with its mode. Any
    mismatch, an untracked file that is not ignored (`ls-files -o
    --exclude-standard`), an unmerged entry, a submodule, or a symlink to a
    directory gives None. A checkout that converts line endings or checks
    symlinks out as text therefore has no digest. Git's output is read as
    UTF-8 with surrogate escapes, never in the locale's encoding."""
    root = Path(eval_dir)
    out = _git_out("ls-files", "-s", "-z", "--", ".", cwd=root)
    if not out:
        return None
    if _git_out("ls-files", "-o", "--exclude-standard", "-z", "--", ".",
                cwd=root) != "":
        return None
    entries, everything = [], None
    for entry in out.split("\0"):
        if not entry:
            continue
        info, _, path = entry.partition("\t")
        mode, sha, stage = info.split(" ")
        if stage != "0" or mode not in ("100644", "100755", "120000") \
                or not _matches_blob(root / path, mode, sha):
            return None
        line = f"{mode} {sha} {path}"
        if mode == "120000":
            if everything is None:
                everything = _tracked_blobs(root)
            target = _symlink_target(root / path)
            if target is None:
                return None
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if (root / path).is_dir() or any(
                    key.startswith(resolved + os.sep) for key in everything):
                return None  # a symlink to a directory (N3)
            found = everything.get(resolved)
            if found is None:
                # Its target is a tracked symlink, an untracked or ignored
                # file, or outside the checkout (N4): not content Git pins.
                return None
            line += f" -> {found}"
        entries.append(line)
    return hashlib.sha256("\0".join(sorted(entries))
                          .encode("utf-8", "surrogateescape")).hexdigest()


def _matches_blob(path: Path, mode: str, sha: str) -> bool:
    """Whether the working copy at `path` is the staged blob `sha` of
    `mode`: a regular file's bytes (and its executable bit), or a symlink's
    target text, hashed as Git hashes a blob (SHA-1, or SHA-256 in a
    SHA-256 repository)."""
    try:
        info = path.lstat()
        if mode == "120000":
            data = (os.fsencode(os.readlink(path)) if stat.S_ISLNK(info.st_mode)
                    else path.read_bytes())
        else:
            if not stat.S_ISREG(info.st_mode) \
                    or bool(info.st_mode & 0o100) != (mode == "100755"):
                return False
            data = path.read_bytes()
    except OSError:
        return False
    algorithm = {40: hashlib.sha1, 64: hashlib.sha256}.get(len(sha))
    if algorithm is None:
        return False
    return algorithm(b"blob %d\0" % len(data) + data).hexdigest() == sha


def _tracked_blobs(root: Path) -> dict:
    """{path relative to `root`: blob sha} of every regular file tracked in
    `root`'s checkout (`ls-files -s -- :/`, whose paths are relative to it)."""
    out = _git_out("ls-files", "-s", "-z", "--", ":/", cwd=root) or ""
    blobs = {}
    for entry in out.split("\0"):
        info, _, path = entry.partition("\t")
        fields = info.split(" ")
        if len(fields) == 3 and fields[0] in ("100644", "100755"):
            blobs[os.path.normpath(path)] = fields[1]
    return blobs


def _symlink_target(path: Path) -> str | None:
    """A symlink's target text: the link's, or a checkout that wrote it as a
    plain file holds it as the file's text."""
    try:
        if path.is_symlink():
            return os.readlink(path)
        return path.read_bytes().decode("utf-8", "surrogateescape")
    except OSError:
        return None


# Every timeout knob a fixture can set, as (key, path-to-its-mapping). Each is
# read with `.get(key, <default>)`, which returns the VALUE whenever the key is
# PRESENT — so an explicit YAML null (`guard:` / `  timeout_s:`) yielded None
# and `subprocess.run(timeout=None)` waited forever: measured against a CLI
# that never returns, the only backstop in CI is the 45-minute job kill, with
# no summary and no artifact written. A string yielded a TypeError traceback
# and rc 1, outside the "configuration problem" contract (rc 2, a named
# message, no traceback).
TIMEOUT_KNOBS = (
    ("timeout_s", ()),           # the agent leg, both subjects
    ("setup_timeout_s", ()),     # the fixture's `setup:` hook
    ("timeout_s", ("guard",)),   # the guidance subject's per-arm delivery guard
    ("timeout_s", ("judge",)),   # the judge call
)

# Every fixture key the harness reads as a MAPPING. The knob parents above
# are derived rather than repeated, so a new nested knob cannot arrive without
# its parent being type-checked; `env:` is the one that is not a timeout
# parent and has the identical defect — `(env_spec or {}).items()` on a
# present non-mapping is an AttributeError traceback and rc 1, outside the
# rc-2 configuration contract, exactly as `(fixture.get("guard") or
# {}).get(...)` was.
MAPPING_FIXTURE_KEYS = tuple(dict.fromkeys(
    [parent for _key, parents in TIMEOUT_KNOBS for parent in parents] + ["env"]))


class MappingFixtureKeyError(guidance.GuidanceError):
    """`_require_mapping` refused a key. Carries WHICH key, so `main()` can
    route the `judge:` one onto `invalid_judge_block` — #81's named error,
    which writes report.md and one summary.json per arm — while every other
    key keeps the plain rc-2 configuration line. Without the key the two
    guards could only be ordered, and whichever ran first silently decided
    the error type for a fixture both of them refuse."""

    def __init__(self, key: str, message: str):
        self.key = key
        super().__init__(message)


def _require_mapping(fixture: dict, key: str, fixture_path: Path) -> None:
    """A PRESENT `key:` is a mapping, or an explicit null. Anything else is a
    named configuration error at fixture load.

    An explicit null is fine and means "absent": `(fixture.get("guard") or
    {})` and `(env_spec or {})` both fall back on it, and every committed
    fixture that omits the key is untouched by this. A truthy non-mapping is
    not fine — YAML will hand over a list, a string or a number just as
    happily, and each of them reaches `.get()`/`.items()` on the wrong type
    and dies with an AttributeError traceback instead of naming the rule.
    """
    if not isinstance(fixture, dict):
        raise guidance.GuidanceError(
            f"{fixture_path}: expected a mapping of fixture keys, got "
            f"{type(fixture).__name__} — `{key}:` cannot be looked up in it. "
            "A-N1-2: this predicate used to assume its own argument's shape, "
            "so an empty fixture file died with a TypeError inside it.")
    if key not in fixture or fixture[key] is None or isinstance(fixture[key], dict):
        return
    raise MappingFixtureKeyError(
        key,
        f"{fixture_path}: `{key}:` must be a mapping (or absent), got "
        f"{type(fixture[key]).__name__} {fixture[key]!r}. The value's TYPE is "
        "named as well as its repr because the repr alone does not say it: "
        "`True` and `3` read as a bool and an int only if you already know "
        "YAML's spelling rules. The harness reads the key with "
        "`.get()`/`.items()`, so "
        "a list, a string or a number here is not a configuration it can run "
        "— it used to reach the wrong type and die with an AttributeError "
        "traceback instead of naming the rule.")


def validate_mapping_keys(fixture: dict, fixture_path: Path) -> None:
    """Every mapping-typed fixture key, checked ONCE at load — before any
    subject branch, any path is derived and any CLI is invoked."""
    for key in MAPPING_FIXTURE_KEYS:
        _require_mapping(fixture, key, fixture_path)
    guidance.check_env_block(fixture.get("env"), f"{fixture_path}: `env:`")


# The CEILING every knob above is checked against, and the predicate that
# applies it, both live in harness/guidance.py — beside the GuidanceError they
# raise, and where EVERY source of a timeout can reach them. Round 2 put them
# here, in the fixture loader, and `--timeout` on the command line overrode the
# checked value afterwards with an unchecked one: `--timeout 2200000` was still
# rc 1 and a bare `OverflowError`. `main()` now runs the same predicate on the
# flag (see the `--timeout` check there), run_canary.py and run_propagation.py
# run it on theirs, and
# `test_every_harness_subprocess_timeout_names_its_validated_source` inventories
# every `subprocess` timeout under harness/ so a new sink cannot arrive with no
# validated source behind it.
#
# Re-exported under this module's own name because it is part of run_eval's
# published surface: `test_the_timeout_ceiling_is_the_workflow_job_budget`
# anchors it to eval.yml's `eval` job.
MAX_TIMEOUT_S = guidance.MAX_TIMEOUT_S

# The bound on this module's own local `git` calls. Named rather than inlined
# so the sink check and the `timeout=` argument are provably the same value:
# the pin compares the two expressions, not two beliefs about them.
GIT_TIMEOUT_S = 10


def validate_timeouts(fixture: dict, fixture_path: Path) -> None:
    """Coerce-and-check every timeout knob ONCE, at fixture load, before any
    subject branch or subprocess. Absent is fine — the caller's default
    applies, and a fixture with a valid or absent knob is untouched by this,
    so every committed fixture scores byte-identically.

    """
    for key, parents in TIMEOUT_KNOBS:
        node = fixture
        for parent in parents:
            # NOT `node = {}` on a non-mapping. Normalising the bad container
            # away let `guard: [1]` through validation and on into
            # `(fixture.get("guard") or {}).get("timeout_s", 300)`, which is
            # an AttributeError traceback and rc 1 — the same defect this
            # predicate closes for the leaf, one level up. Checked here as
            # well as in `validate_mapping_keys` so this function is sound for
            # a direct caller, not only for the one order main() calls them in.
            if not isinstance(node, dict):
                node = None
                break
            _require_mapping(node, parent, fixture_path)
            node = node.get(parent)
        if not isinstance(node, dict) or key not in node:
            continue
        guidance.check_timeout(node[key], ".".join(parents + (key,)),
                               guidance.FIXTURE_TIMEOUT_REMEDY,
                               prefix=f"{fixture_path}: ")


def validate_followups(fixture: dict, fixture_path: Path) -> None:
    """`followups:` is absent, null, or a non-empty list of non-blank
    strings, checked once at load like every other key the harness reads.

    Each entry is sent as one further user turn after the prompt (see
    `run_agent`). Anything else would reach `claude -p` as a stringified
    list or an empty prompt, so it is a named configuration error (rc 2)
    instead.
    """
    value = fixture.get("followups") if isinstance(fixture, dict) else None
    if value is None:
        return
    if (not isinstance(value, list) or not value
            or not all(isinstance(item, str) and item.strip() for item in value)):
        raise guidance.GuidanceError(
            f"{fixture_path}: `followups:` must be a non-empty list of "
            f"non-blank strings (or absent), got {type(value).__name__} "
            f"{value!r}. Each entry is one further user turn sent with "
            "`--resume` after the prompt, identically in both arms.")


def validate_effort(fixture: dict, fixture_path: Path) -> None:
    """`effort:` is absent, null, or one of guidance.EFFORT_LEVELS, checked
    once at load. Anything else would reach `claude --effort` and fail as the
    agent's error, so it is a named configuration error (rc 2) instead."""
    value = fixture.get("effort") if isinstance(fixture, dict) else None
    try:
        guidance.check_effort(value)
    except guidance.GuidanceError as exc:
        raise guidance.GuidanceError(f"{fixture_path}: `effort:` {exc}") from None


REGISTRIES_YML = Path(__file__).parent / "registries.yml"

_REQUIRED_REGISTRY_FIELDS = ("name", "url", "layout")


def _normalize_registry_url(url: str) -> str:
    """Case-insensitive, trailing-slash- and .git-suffix-insensitive form of
    a registry URL, so `https://github.com/Org/repo/`, `...repo.git`, and a
    differently-cased host or path all match the same registries.yml entry.
    """
    url = url.strip().rstrip("/")
    if url.lower().endswith(".git"):
        url = url[:-4]
    return url.lower()


def _layout_parts(layout: str) -> list[str]:
    """Split a registries.yml `layout` glob into path segments and check it
    ends in the skill-name placeholder immediately before `SKILL.md` — the
    one shape `_skill_md_glob` can substitute into. Shared by load-time
    validation and `_skill_md_glob` itself so the two can never drift apart:
    a layout that "passes" at load time (e.g. `skills/bundle*/SKILL.md`,
    which merely ends with the substring `*/SKILL.md`) but is rejected at
    arm time by a stricter check used to raise uncaught, deep inside a run.
    """
    parts = layout.split("/")
    if len(parts) < 2 or parts[-1] != "SKILL.md" or parts[-2] != "*":
        raise ValueError(f"layout {layout!r} must end in '*/SKILL.md'")
    if any("**" in part for part in parts):
        raise ValueError(
            f"layout {layout!r} contains a '**' segment — recursive globs "
            "are rejected: against a registry with a stale copy under, "
            "say, .git/, the sorted-first match could come from there")
    return parts


def _load_registries_config(path: Path = REGISTRIES_YML) -> list[dict]:
    """harness/registries.yml: [{name, url, layout}, ...] — this harness's own
    record of registry name/URL/layout, kept in step by hand with acsi-agentskills'
    scripts/skills_registries.yml (see test/run_tests.py::TestIssue63) rather
    than importing that file at run time: this harness must resolve using
    only its own checkout plus the registry under test.

    Shape-validated on load with one clear message per problem — a bare
    KeyError/TypeError from a malformed file is not something a contributor
    editing registries.yml by hand should have to decode.
    """
    if not path.is_file():
        raise ValueError(f"{path} not found")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict) or not doc.get("registries"):
        raise ValueError(f"{path} is empty or missing a top-level 'registries:' key")
    entries = doc["registries"]
    if not isinstance(entries, list):
        raise ValueError(f"{path}'s 'registries:' must be a list")

    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path} registries[{i}] must be a mapping")
        # Presence first (a genuinely absent or blank field), then type —
        # `not entry.get(f)` alone would misreport a wrong-typed-but-present
        # value (`name: no` parses as the bool False) as "missing" instead of
        # naming the real problem.
        missing = [f for f in _REQUIRED_REGISTRY_FIELDS
                  if entry.get(f) is None or entry.get(f) == ""]
        if missing:
            raise ValueError(
                f"{path} registries[{i}] is missing required field(s): "
                f"{', '.join(missing)}")
        bad_type = [f for f in _REQUIRED_REGISTRY_FIELDS
                   if not isinstance(entry.get(f), str)]
        if bad_type:
            raise ValueError(
                f"{path} registries[{i}] field(s) must be strings: " +
                ", ".join(f"{f!r} is {type(entry[f]).__name__}" for f in bad_type))
        name, url, layout = entry["name"], entry["url"], entry["layout"]
        if name in seen_names:
            raise ValueError(f"{path} has a duplicate registry name {name!r}")
        seen_names.add(name)
        norm_url = _normalize_registry_url(url)
        if norm_url in seen_urls:
            raise ValueError(f"{path} has a duplicate registry url {url!r}")
        seen_urls.add(norm_url)
        try:
            _layout_parts(layout)
        except ValueError as exc:
            raise ValueError(
                f"{path} entry {name!r} has layout {layout!r}: {exc}") from exc
        if not isinstance(entry.get("may_be_empty", False), bool):
            raise ValueError(
                f"{path} entry {name!r}: 'may_be_empty' must be true or false")
        if Path(layout).is_absolute():
            raise ValueError(
                f"{path} entry {name!r} has an absolute layout {layout!r} — "
                "layouts are globbed relative to the registry checkout")
        if ".." in Path(layout).parts:
            raise ValueError(
                f"{path} entry {name!r} has layout {layout!r} containing "
                "'..' — layouts must stay within the registry checkout")
    return entries


def _parse_registry_flags(values: list[str] | None) -> dict[str, str]:
    """Repeatable --registry NAME=PATH entries. A bare PATH (no "=") is the
    pre-#63 single-path form and is taken as the acsi-agentskills entry, so
    the legacy invocation (`--registry ../acsi-agentskills`) keeps working
    unchanged. An empty PATH (`--registry acsi-agentskills=`, or a bare empty
    string) is rejected here rather than silently resolving to the current
    directory.
    A NAME repeated across two flags (bare or explicit) is rejected too —
    silently taking the last one made a copy-pasted or re-ordered invocation
    "work" while quietly dropping the first flag's registry.
    """
    out: dict[str, str] = {}
    for value in values or []:
        name, sep, path = value.partition("=")
        if sep:
            if not path:
                raise ValueError(
                    f"--registry {value!r}: empty PATH after '=' for "
                    f"registry {name!r}")
            key = name
        else:
            if not value:
                raise ValueError(
                    "--registry '': empty value — expected NAME=PATH, or a "
                    "bare PATH (legacy, taken as the acsi-agentskills entry)")
            key = "acsi-agentskills"
            path = value
        if key in out:
            raise ValueError(
                f"--registry {value!r}: registry {key!r} given more than "
                f"once (already {out[key]!r}) — repeated --registry flags "
                "for the same name silently last-won; pass it once")
        out[key] = path
    return out


def _parse_registry_env(value: str | None) -> dict[str, str]:
    """$SKILLS_EVALS_REGISTRIES: the same NAME=PATH shape, comma-separated. A
    bare entry (no "=") is taken as the acsi-agentskills entry too, the same
    as the --registry flag's legacy bare-PATH form (see _parse_registry_flags)
    — previously this silently dropped a bare entry instead, which was the
    one shape the CLI flag treats as meaningful. A NAME repeated across two
    entries (bare or explicit) is rejected too, the same as
    _parse_registry_flags — silently taking the last one made a re-ordered
    or copy-pasted env value "work" while quietly dropping the first entry's
    registry.
    """
    out: dict[str, str] = {}
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, path = item.partition("=")
        if sep:
            if not path:
                raise ValueError(
                    f"$SKILLS_EVALS_REGISTRIES entry {item!r}: empty PATH "
                    f"after '=' for registry {name!r}")
            key = name
        else:
            key = "acsi-agentskills"
            path = item
        if key in out:
            raise ValueError(
                f"$SKILLS_EVALS_REGISTRIES entry {item!r}: registry {key!r} "
                f"given more than once (already {out[key]!r}) — repeated "
                "entries for the same name silently last-won; pass it once")
        out[key] = path
    return out


def resolve_registries(cli_values: list[str] | None, env_value: str | None,
                       base_dir: Path, agentskills_dir: str | None = None) -> dict[str, dict]:
    """Map every registry named in harness/registries.yml to a local checkout.

    Sources, in order: a --registry NAME=PATH flag (repeatable; a bare PATH
    means acsi-agentskills) merged BY NAME with $SKILLS_EVALS_REGISTRIES (same
    shape; a flag wins over an env entry naming the same registry, but an env
    entry for a DIFFERENT registry still applies even when a flag is also
    given), then `agentskills_dir` for the acsi-agentskills entry specifically
    (the harness's pre-#63 override — callers pass $AGENTSKILLS_DIR, whose
    name outlived the agentskills -> acsi-agentskills rename since renaming a
    public env var is out of scope), then a sibling-directory default
    `../<name>` next to `base_dir` — the same convention acsi-agentskills'
    own skills_registries.yml uses for the registries it doesn't live in.

    An override naming a registry not listed in harness/registries.yml
    (a typo'd `--registry example_site_platform=...`, say) is rejected here rather
    than silently discarded — the pre-fix behavior fell back to that
    registry's sibling default instead, which can "work" by accident and
    makes a bad override unverifiable from the exit code alone.
    """
    overrides_cli = _parse_registry_flags(cli_values)
    overrides_env = _parse_registry_env(env_value)
    config = _load_registries_config()
    known = {entry["name"] for entry in config}
    unknown = sorted((set(overrides_cli) | set(overrides_env)) - known)
    if unknown:
        names = ", ".join(repr(n) for n in unknown)
        raise ValueError(
            f"unknown registry name(s) {names} in --registry / "
            "$SKILLS_EVALS_REGISTRIES — not listed in harness/registries.yml "
            f"(known registries: {', '.join(sorted(known))})")

    resolved = {}
    for entry in config:
        name = entry["name"]
        if name in overrides_cli:
            path = Path(overrides_cli[name]).expanduser().resolve()
            source = "--registry flag"
        elif name in overrides_env:
            path = Path(overrides_env[name]).expanduser().resolve()
            source = "$SKILLS_EVALS_REGISTRIES"
        elif name == "acsi-agentskills" and agentskills_dir:
            path = Path(agentskills_dir).expanduser().resolve()
            source = "$AGENTSKILLS_DIR"
        else:
            path = (base_dir / ".." / name).resolve()
            source = "sibling default"
        resolved[name] = {"name": name, "path": path, "layout": entry["layout"],
                          "url": entry["url"], "source": source,
                          "may_be_empty": entry.get("may_be_empty", False)}
    return resolved


def _validate_registry_paths(registries: dict[str, dict]) -> None:
    """Fail fast on any EXPLICITLY overridden registry (a --registry flag,
    $SKILLS_EVALS_REGISTRIES entry, or $AGENTSKILLS_DIR) whose resolved path
    is not a directory — a typo'd override is a config mistake worth catching
    before any arm spends agent budget, not several minutes later as a
    confusing skill_not_found. Sibling-default entries are left alone here:
    most of registries.yml (e.g. acsi-agentskills-private) is never checked out
    locally and is fine to stay unresolved unless a fixture actually needs
    it — that path is checked lazily, per-arm, in _run_arm instead.
    """
    for name, entry in registries.items():
        if entry["source"] == "sibling default":
            continue
        if not entry["path"].is_dir():
            raise ValueError(
                f"registry {name!r} ({entry['source']}) does not resolve "
                f"to a directory")


def registry_for_url(registries: dict[str, dict], url: str) -> dict:
    """The registries.yml entry (path + layout) whose url matches a fixture's
    `registry:` field. Raises with a message naming harness/registries.yml —
    the file to edit — rather than failing silently or crashing deep inside
    a glob.
    """
    target = _normalize_registry_url(url)
    for entry in registries.values():
        if _normalize_registry_url(entry["url"]) == target:
            return entry
    known = ", ".join(sorted(registries)) or "(none configured)"
    raise ValueError(
        f"unknown registry {url!r} — not listed in harness/registries.yml "
        f"(known registries: {known})")


def _skill_md_glob(layout: str, skill: str) -> str:
    """Substitute `skill` for the skill-name placeholder in a registries.yml
    `layout` glob (the segment immediately before `SKILL.md`), leaving any
    earlier `*` (a bundle/plugin wildcard) untouched. Returns the FULL glob
    ending in `/SKILL.md` — callers must glob for FILES and take `.parent`,
    never glob for a directory: a skill dir with no SKILL.md (a stub left by
    a rename, a bundle mid-migration) must fail closed as skill_not_found
    rather than "installing" whatever happens to sit in that directory.
    """
    parts = _layout_parts(layout)
    parts[-2] = skill
    return "/".join(parts)


_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_skill_name(skill: str) -> None:
    """A skill name must be a single, non-empty path segment with no path or
    glob metacharacters. It flows unvalidated into both a registry glob and a
    shutil.copytree destination: `../../x` would escape the registry on read
    and the workspace on write, `*` would install whichever skill happens to
    glob-match first, and `""` would install the whole registry container.
    """
    if not _SKILL_NAME_RE.fullmatch(skill) or skill in (".", ".."):
        raise ValueError(
            f"invalid skill name {skill!r}: must be a single non-empty path "
            "segment with no path or glob metacharacters")


#: THE TRUSTED ROSTER (ADR 0001, #147). `main` is ruleset-protected and
#: pull-request-only, so an arm, the judge, the preflight model or a
#: `catalogue_seen` entry cannot appear here or vanish from here without a
#: reviewed commit — which is the one property the published
#: `roster/latest.json` on `persistent/eval-results` never had.
TRUSTED_ROSTER = Path(__file__).resolve().parent.parent / "evals" / "roster.yml"


def _resolve_roster(cli_value: Path | None) -> Path:
    """Model roster: --roster, else $EVAL_ROSTER, else the COMMITTED file.

    THE DEFAULT USED TO BE `roster/latest.json`, materialised by CI from the
    `persistent/eval-results` branch and pointed at by `$EVAL_ROSTER` — a branch other
    jobs on other machines write to, which the design treats as untrusted
    input. That made one line on that branch decide which models every
    unpinned fixture ran against, and fourteen review rounds on PR #129
    could not make a local check over it safe (issue #147). The roster the
    harness RUNS ON is `evals/roster.yml` now; `roster/latest.json` is an
    exhibit for the explorer and is read by no decision.

    `--roster` and `$EVAL_ROSTER` stay as overrides for tests and local runs
    (ADR 0001, decision 1). Neither is set by `eval.yml` any more.

    Whether a missing roster is an error depends on the fixture — see
    select_models(): it is for an unpinned one, and it is not for a pinned one.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ.get("EVAL_ROSTER")
    if env:
        return Path(env).expanduser()
    return TRUSTED_ROSTER


def read_roster(roster_path: Path | None) -> tuple[dict | None, str | None]:
    """(roster, problem). Never raises, and never returns a half-shaped roster.

    YAML AND JSON, DECIDED BY CONTENT AND NOT BY EXTENSION (#147). The
    trusted roster is YAML, because a human edits it in a reviewed pull
    request and YAML is what every other hand-edited file in this repo is;
    a `--roster` pointing at a published `roster/latest.json` is still JSON.
    JSON is a subset of YAML 1.2, so `yaml.safe_load` reads both and there
    is no extension sniff to get wrong — a file named `.json` holding YAML,
    or the reverse, reads the same either way.

    The NEVER-RAISES, NEVER-HALF-SHAPED CONTRACT IS UNCHANGED, and it is
    what this function is for. Every one of these shapes was reachable and
    three of them crashed with an AttributeError three frames down: a
    top-level list, `arms` as a list of strings, `judge` as a string, a
    truncated file, an empty file. A named problem is the whole difference
    between a run that says what is wrong and a stack trace in a CI log.
    The problem names the same three cases it always did — absent/empty,
    unreadable, not a mapping — so nothing downstream has to learn a new
    vocabulary for a parser change.
    """
    if roster_path is None:
        return None, "no roster path was resolved"
    path = Path(roster_path)
    # The basename, not the full path (see select_models' own messages,
    # round 2 item 15): this problem string flows unchanged into
    # select_models' return value, which the caller writes into
    # summary.json — and eval.yml commits that file to the public
    # persistent/eval-results branch.
    if not path.is_file() or path.stat().st_size == 0:
        return None, f"no model roster at {path.name}"
    try:
        with open(path, encoding="utf-8") as f:
            document = yaml.safe_load(f)
    # `yaml.YAMLError` covers a truncated or otherwise unparseable
    # document (it is `ScannerError`/`ParserError`'s base), and the JSON
    # errors stay named beside it because `json` is still in this module's
    # vocabulary and a caller reading the message wants the class that
    # fired, not a category. `RecursionError` is here for the same reason
    # roster.read_json has it: a deeply nested document exhausts the
    # parser's stack rather than failing to parse, and used to escape as a
    # traceback carrying the runner's absolute paths.
    except (yaml.YAMLError, json.JSONDecodeError, OSError, UnicodeDecodeError,
            ValueError, RecursionError) as exc:
        return None, f"model roster at {path.name} is unreadable ({type(exc).__name__})"
    if not isinstance(document, dict):
        return None, f"model roster at {path.name} is not a mapping"
    return document, None


def roster_models(roster: dict | None) -> tuple[list[str], str | None, bool, int]:
    """(arm ids, judge id, judge-is-also-an-arm, skipped) out of a roster document.

    Anything the wrong shape is dropped here rather than trusted downstream.
    `judge.is_arm` is the roster's own flag; membership in `arms` is the fact
    behind it, and an older roster carrying no flag must not read as consent.

    `skipped` counts `arms` entries dropped for being the wrong shape (not a
    dict with a string `id`). It matters because dropping them silently once
    let the judge-is-arm check pass against an EMPTIED set: a malformed
    `arms` list (e.g. raw strings instead of `{id, reason}` objects) parsed
    to `arm_ids == []`, so `judge_id in arm_ids` was always False even when
    the roster's own (unparsed) entries plainly named that judge as an arm.
    """
    entries = roster.get("arms") if isinstance(roster, dict) else None
    arm_ids = []
    skipped = 0
    if isinstance(entries, list):
        for a in entries:
            if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]:
                arm_ids.append(a["id"])
            else:
                skipped += 1
    judge_entry = roster.get("judge") if isinstance(roster, dict) else None
    judge_id = judge_entry.get("id") if isinstance(judge_entry, dict) else None
    if not isinstance(judge_id, str) or not judge_id:
        judge_id = None
    flagged = bool(isinstance(judge_entry, dict) and judge_entry.get("is_arm"))
    return (arm_ids, judge_id,
           flagged or (judge_id is not None and judge_id in arm_ids), skipped)


def _judge_is_pairwise(fixture: dict) -> bool:
    """Whether the fixture's `judge.mode` is anything but absolute, casefolded
    as `judge.score()` and main()'s `judge_mode_unsupported` check fold it."""
    judge_cfg = fixture.get("judge")
    mode = judge_cfg.get("mode") if isinstance(judge_cfg, dict) else None
    mode = mode.strip().casefold() if isinstance(mode, str) else mode
    return mode not in (None, "", "absolute")


def select_judged_models(fixture: dict, args: argparse.Namespace) -> tuple:
    """(agent model, judge model, error, `judge_selection`) for this run.

    The first three are `select_models()`'s answer. `judge_selection` is the
    block every summary of the run records (#371, ADR 0013): the judge chosen,
    the rule that chose it and both tiers; None when no judge is selected
    (`--no-judge`, or an error).

    THE JUDGE IS CHOSEN FOR THE ARM, in this order:

    1. `fixture-pin`: the fixture's `judge.model:` wins, unchecked, as before.
    2. `roster-judge`, for a fixture whose `judge.mode` is not absolute
       (pairwise): the roster's `judge`, whatever the arm, and refused when
       the roster says that judge is also an arm, when it is this run's arm
       model, or when the ladder puts it in that model's tier.
    3. `tier-above-arm`: the newest roster arm in the lowest tier above the
       arm model's, refused when the ids in that tier cannot be ordered
       (`judge_policy.newest_of`). With no roster arm above it,
       `roster-judge`: the roster's `judge`, which must sit in a tier above
       the arm's (`judge_policy.judge_for_arm`).

    A run is one arm model on both of its arms, so it still has one judge.
    Rule 3 needs the tier ladder, and so does a pairwise fixture, which is
    refused without it: the roster judge cannot be checked against the arm's
    tier. A pin records null tiers when the ladder cannot be read.
    """
    pinned_agent = args.model or fixture.get("model")
    judge_cfg = fixture.get("judge")
    pinned_judge = judge_cfg.get("model") if isinstance(judge_cfg, dict) else None
    no_judge = getattr(args, "no_judge", False)
    needs_agent = not pinned_agent
    needs_judge = not pinned_judge and not no_judge
    if not needs_agent and not needs_judge:
        if no_judge:
            return pinned_agent, pinned_judge, None, None
        return pinned_agent, pinned_judge, None, judge_policy.selection(
            pinned_judge, judge_policy.RULE_FIXTURE_PIN, pinned_agent,
            judge_policy.read_rungs()[0])

    def refuse(error: str) -> tuple:
        return None, None, error, None

    path = _resolve_roster(getattr(args, "roster", None))
    roster, problem = read_roster(path)
    if problem:
        missing = [w for need, w in ((needs_agent, "model"),
                                     (needs_judge, "judge model")) if need]
        return refuse(f"{problem}, and this fixture pins no {' or '.join(missing)}")
    arm_ids, judge_id, judge_is_arm, skipped = roster_models(roster)
    raw_arms = roster.get("arms") if isinstance(roster, dict) else None
    if isinstance(raw_arms, list) and raw_arms and not arm_ids:
        # Non-empty `arms` that parses to zero usable ids is a broken
        # roster, not "no arms configured" — and it is unsafe to trust for
        # ANYTHING this roster names (including the judge), regardless of
        # whether this particular run even needed an arm from it.
        return refuse(f"the model roster at {path.name} lists "
                      f"{len(raw_arms)} arm entry/entries but none has "
                      f"a usable string `id` (skipped {skipped}); this "
                      f"roster cannot be trusted for a model pick")
    if needs_agent and not arm_ids:
        return refuse(f"the model roster at {path.name} names no usable "
                      f"arm, and this fixture pins no model")
    agent = pinned_agent or arm_ids[0]
    if no_judge:
        return agent, pinned_judge, None, None
    rungs, ladder_problem = judge_policy.read_rungs()
    if pinned_judge:
        return agent, pinned_judge, None, judge_policy.selection(
            pinned_judge, judge_policy.RULE_FIXTURE_PIN, agent, rungs)
    if _judge_is_pairwise(fixture):
        if not judge_id:
            return refuse(f"the model roster at {path.name} names no usable "
                          f"judge, and this fixture pins no judge model")
        if judge_is_arm:
            return refuse(f"the model roster at {path.name} names a judge "
                          f"that is also an arm; a model must not grade its "
                          f"own run. Pin `judge.model:` in the fixture to "
                          f"override")
        # The arm need not be a roster arm (`--model`, the fixture's
        # `model:`), so the roster's own flag does not cover these two.
        if judge_id == agent:
            return refuse(f"the model roster at {path.name} names the judge "
                          f"`{judge_id}`, which is this run's arm model; a "
                          f"model must not grade its own run. Pin "
                          f"`judge.model:` in the fixture to override")
        if ladder_problem:
            return refuse(f"{ladder_problem}, so the roster judge cannot be "
                          f"checked against the arm's tier, and this fixture "
                          f"pins no judge model")
        chosen = judge_policy.selection(
            judge_id, judge_policy.RULE_ROSTER_JUDGE, agent, rungs)
        if chosen["arm_tier"] is not None \
                and chosen["arm_tier"] == chosen["judge_tier"]:
            return refuse(f"the model roster at {path.name} names the judge "
                          f"`{judge_id}`, which is in the "
                          f"{chosen['judge_tier']} tier of the arm model "
                          f"`{agent}`; a judge must not share its arm's "
                          f"tier. Pin `judge.model:` in the fixture to "
                          f"override")
        return agent, judge_id, None, chosen
    if ladder_problem:
        return refuse(f"{ladder_problem}, so no judge can be chosen for the "
                      f"arm, and this fixture pins no judge model")
    chosen, why = judge_policy.judge_for_arm(agent, arm_ids, judge_id, rungs)
    if chosen is None:
        return refuse(f"the model roster at {path.name} gives this arm no "
                      f"judge: {why}. Pin `judge.model:` in the fixture to "
                      f"override")
    return agent, chosen["model"], None, chosen


def select_models(fixture: dict, args: argparse.Namespace) -> tuple:
    """(agent model, judge model, error) for this run.

    Precedence: `--model` > the fixture's pin > the roster > nothing runs.

    FAIL CLOSED. The runner used to fall through to the CLI's own default
    model whenever the fixture pinned nothing and the roster was absent,
    empty, truncated or the wrong shape — which publishes a badge for a model
    nobody chose and makes every week-over-week comparison a comparison
    against a different model. An unpinned fixture with no usable roster is a
    runner-level error naming the path it looked for, and it leaves through
    the normal exit-2 path. A fixture that pins BOTH `model:` and
    `judge.model:` never needs the roster and is unaffected: it still runs
    with no roster at all. Pinning only one still reads the roster for the
    other.

    The roster's arms are ordered cheapest tier first, so `arms[0]` is the
    WEAKEST model in the set. That is deliberate for a single-arm run — a
    floor effect is as signal-free as a ceiling effect, and the matrix runner
    that will run every arm is where the per-fixture calibration belongs. A
    fixture that needs a specific one keeps its pin.

    The judge is chosen for that arm model (`select_judged_models`, which
    also returns why): a tier above it, never the same model.
    """
    return select_judged_models(fixture, args)[:3]


_VAR_RE = re.compile(r"\$(\w+)|\$\{([^}]*)\}")


def expand(value: str, env: dict) -> str:
    """`$VAR` / `${VAR}` resolved against `env`, in ONE pass.

    Not `os.path.expandvars`: that reads `os.environ`, so a `WORKSPACE`
    already set in the harness's own environment won every time and
    `$WORKSPACE/bin` resolved to the OUTER path — which silently removed the
    fixture's fake from PATH and left whatever real tool was next on it, under
    bypassPermissions. One pass also means the substituted text is never
    re-scanned, so a workspace path holding a `$` cannot expand again.
    """
    return _VAR_RE.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), value)


# The ONLY inherited variables an arm receives, for every fixture. An
# ALLOWLIST, deliberately: a denylist forwards everything nobody thought to
# name, and what reaches the arm then depends on the operator's shell.
# Measured under the denylist this replaces, through `run_eval.py --arm
# without_skill` with a stand-in `claude` that dumps its own environment:
# `GH_HOST`, `GH_ENTERPRISE_TOKEN` and `GITHUB_ENTERPRISE_TOKEN` — the other
# half of `gh`'s own credential resolution — arrived verbatim, and so did
# `AWS_*`, `NPM_TOKEN`, `GITLAB_TOKEN`, `OPENAI_API_KEY`, `HF_TOKEN`,
# `SSH_AUTH_SOCK`, `KUBECONFIG`, `DOCKER_CONFIG`, `GIT_ASKPASS`,
# `PYTHONPATH`, `LD_PRELOAD`, and variables whose VALUES name the operator's
# own checkout. The arm's workspace is its cwd under bypassPermissions, `env`
# is one of the first things a shell reaches for, and `_write_summary` writes
# the arm's transcript to `results/<skill>/<ts>/<arm>/transcripts/raw.json`,
# which `.github/workflows/eval.yml` pushes to the public `persistent/eval-results`
# branch — so a variable that reaches the arm is a variable an arm can
# publish.
#
# Each entry carries the reason the CLI or the operating system needs it. A
# name is added here only because something in `run_agent`'s invocation,
# `_run_arm`, or eval.yml demonstrably needs it — never because a test
# wanted it.
_ALLOWED_ENV = (
    "PATH",                # find the CLI, and the fixture's own stand-ins
    "HOME",                # the CLI's config, cache and credential store
    "USER",                # some tools shell out and read it; cheap to keep
    "LOGNAME",             # the POSIX spelling of the same thing
    "SHELL",               # what the CLI spawns for its own tool calls
    "TERM",                # terminal capabilities; absent, some tools hang
    "LANG",                # locale: decides the CLI's default text encoding
    "LANGUAGE",            # locale fallback list, same reason
    "TZ",                  # local time in anything the agent formats
    "TMPDIR",              # where the CLI and its children write temp files
    "TMP",                 # the same, spelled the other way
    "TEMP",                # and the third spelling
    "HTTP_PROXY",          # a runner may only reach the API through a proxy
    "HTTPS_PROXY",         # the API is HTTPS, so this is the load-bearing one
    "NO_PROXY",            # hosts that must bypass it
    "ALL_PROXY",           # the catch-all spelling some clients read
    "http_proxy",          # not an alias: clients read one case or the other
    "https_proxy",         # the lower-case spelling curl and requests prefer
    "no_proxy",            # the lower-case bypass list, read by the same clients
    "all_proxy",           # the lower-case catch-all, for completeness of the pair
    "SSL_CERT_FILE",       # a corporate CA bundle, or TLS fails outright
    "SSL_CERT_DIR",        # the directory spelling of the same bundle
    "NODE_EXTRA_CA_CERTS", # the CLI is a Node program; this is its CA hook
    "REQUESTS_CA_BUNDLE",  # anything Python the CLI shells out to
    "CURL_CA_BUNDLE",      # anything curl-based it shells out to
)

# Prefixes, for families whose members are not knowable in advance.
_ALLOWED_ENV_PREFIXES = (
    "ANTHROPIC_",  # the API credential: eval.yml exports ANTHROPIC_AUTH_TOKEN
                   # step-locally, local runs use ANTHROPIC_API_KEY. Forwarded
                   # by design — the CLI cannot authenticate otherwise — which
                   # also makes it one of the variables the arm's own
                   # published transcript could leak (see "a variable that
                   # reaches the arm is a variable an arm can publish" above);
                   # nothing here redacts it before raw.json is written, so
                   # eval.yml leaves raw.json out of its artifacts (#289).
    "CLAUDE_",     # the CLI's own knobs, including CLAUDE_CODE_OAUTH_TOKEN.
                   # Also carries CLAUDE_BIN, which only the harness itself
                   # reads (run_agent/judge.score/run_canary via os.environ,
                   # never the CLI) — a residue of the prefix, not something
                   # under test needing it — and CLAUDE_CODE_USE_BEDROCK/
                   # _VERTEX with none of the AWS_*/GOOGLE_APPLICATION_
                   # CREDENTIALS that would authenticate them: this allowlist
                   # assumes the first-party API, which is what eval.yml
                   # uses. Adding those credential families to reach
                   # Bedrock/Vertex would undo B1's own point.
    "LC_",         # the per-category locale settings LANG does not cover
    "XDG_",        # config/cache/data/state/runtime dirs the CLI writes under
)

# Emptied rather than dropped. `GH_TOKEN`/`GITHUB_TOKEN` are not on the
# allowlist, so they no longer arrive on their own — but an arm under
# bypassPermissions can call a real `gh` by absolute path, past the stand-in
# on PATH, and an ABSENT token sends `gh` looking in its config and the
# keyring for another one. Empty stops that search; `GH_CONFIG_DIR` points it
# inside the workspace, where there is no host and no credential to find.
_BLANKED_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
_WORKSPACE_GH_CONFIG = ".gh/config"

# The agent arm's network boundary (ADR 0011). A real-work seed is a public
# fleet repository's pre-fix tree, so the merged fix is one `git clone`, `curl`
# or WebFetch away, and an empty token (above) stops nothing a public
# repository serves; the deployed sites serve the built fix too. Claude Code's
# own sandbox (https://code.claude.com/docs/en/sandboxing) runs every Bash
# command and its children with no route out except a proxy that refuses
# these hosts in every permission mode, and with `strictAllowlist` and no
# allowed domains it refuses every other host as well. It covers shell
# commands only, so the two built-in web tools are removed outright.
# Delivered with `--settings`: `allowUnsandboxedCommands: false` there makes
# the sandbox admin-required, so a `.claude/settings.json` the agent writes
# into its workspace cannot loosen it, and nothing lands in the workspace for
# a scoring check to see. The CLI's own API traffic does not go through the
# sandbox proxy (measured on CLI 2.1.292).
ARM_DENIED_DOMAINS = (
    "github.com", "*.github.com", "codeload.github.com",
    "githubusercontent.com", "*.githubusercontent.com",
    "example-site-a", "*.example-site-a", "example-site-b", "*.example-site-b",
    "cdn.jsdelivr.net",
)
ARM_DISALLOWED_TOOLS = ("WebFetch", "WebSearch")
# Claude in Chrome is a browser outside the sandbox, and `--strict-mcp-config`
# does not remove it: `CLAUDE_CODE_ENABLE_CFC` or the account's default can
# turn it on (CLI 2.1.292). `--no-chrome` turns it off, and the variable never
# reaches an arm.
ARM_DROPPED_ENV = ("CLAUDE_CODE_ENABLE_CFC",)
# The CLI's own stderr lines for a sandbox that is not running, matched as the
# whole start of a line (CLI 2.1.292's bundle): the `failIfUnavailable`
# refusal, an initialization failure (`\u274c Sandbox Error: <reason>`, exit
# 1) and the warning it prints when it runs commands unsandboxed. A line that
# merely quotes one of them elsewhere does not match.
_SANDBOX_DOWN_LINE = re.compile(
    r"^(?:Error: sandbox required but unavailable: "
    r"|(?:\u274c\s*)?Sandbox Error: "
    r"|(?:\u26a0\ufe0f?\s*)?Sandbox disabled: )")


# The agent arm's READ boundary (ADR 0011's addendum). The answer key sits on
# this machine as well as on GitHub: this checkout's
# `evals/real-work/*/checker/` and `solution.patch`, the sibling clones beside
# it (example-site-platform `main` holds the merged fixes) and, on a workstation, the
# transcripts of the original work under `~/.claude/projects/`. The sandbox
# reads most of the machine by default, so `filesystem.denyRead` closes these
# to Bash and its children, and the same paths as `Read(//<abs>/**)` deny
# rules close them to the Read, Grep and Glob tools, which the sandbox does
# not cover (https://code.claude.com/docs/en/sandboxing,
# https://code.claude.com/docs/en/permissions). Paths are absolute: a guidance
# arm runs with a scratch HOME, so `~/` would name the wrong directory.
#
# Re-opened inside the real HOME, for sandboxed commands only (`allowRead`;
# a Read deny rule cannot be carved, and the file tools need none of them):
# git's global configuration, so `git` keeps the identity and settings an
# arm's `git commit` uses, the same files the CLI itself re-opens under
# `blockReadsOutsideWorkingDirectories`; and every PATH directory under HOME,
# with a `bin` directory's sibling `lib`, so a toolchain installed there
# (`~/.local/bin`, nvm's `.../bin` and `.../lib`) still runs. A carve-out at
# or around a denied checkout is never emitted.
ARM_HOME_READ_CARVE_OUTS = (".gitconfig", ".config/git/config",
                            ".config/git/ignore", ".config/git/attributes")
HARNESS_ROOT = Path(__file__).resolve().parent.parent


class ArmReadIsolationError(ValueError):
    """The arm's read fence cannot be built safely; `code` is the arm's error
    name (`workspace_read_denied`, `read_rules_unsafe`, `settings_too_large`,
    `toolchain_under_alias`)."""

    def __init__(self, message: str, code: str = "workspace_read_denied"):
        super().__init__(message)
        self.code = code


# Resolved once, at import: before any test stands in for `subprocess.run`,
# and the checkout this module runs from does not move during a run.
HARNESS_CLONE_ROOT = harness_clone_root()
HARNESS_GIT_COMMON_DIR = harness_git_common_dir()


def harness_commit(root: Path = HARNESS_ROOT,
                   common: Path | None = HARNESS_GIT_COMMON_DIR) -> str | None:
    """The commit this harness checkout is at, or None: its `HEAD`, followed
    through one loose or packed ref. Read from the git metadata files, with
    no git command (harness_repo's reads are a fixed inventory), so a ref
    store this cannot read (reftable) records null rather than a guess."""
    try:
        dot = root / ".git"
        gitdir = dot if dot.is_dir() else root / dot.read_text(
            encoding="utf-8").removeprefix("gitdir:").strip()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: refs/") and ".." not in head:
            ref, refs = head[5:], common or gitdir
            if (refs / ref).is_file():
                head = (refs / ref).read_text(encoding="utf-8").strip()
            else:
                head = next(line.split()[0] for line in (refs / "packed-refs")
                            .read_text(encoding="utf-8").splitlines()
                            if line.endswith(" " + ref))
    except (OSError, ValueError, StopIteration):
        return None
    return head if re.fullmatch(r"[0-9a-f]{40}", head) else None


HARNESS_COMMIT = harness_commit()


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


PROFILE_LABEL = "a Claude Code profile"


def profile_dirs(home: Path, environ=None) -> list[Path]:
    """The Claude Code profiles on this machine an arm may not read: the real
    `~/.claude` and any `CLAUDE_CONFIG_DIR` this process inherited (an
    external profile's `projects/` holds other sessions' transcripts)."""
    environ = os.environ if environ is None else environ
    out = [Path(home) / ".claude"]
    if environ.get("CLAUDE_CONFIG_DIR"):
        out.append(Path(environ["CLAUDE_CONFIG_DIR"]).expanduser())
    return out


MOUNT_ALIAS_LABEL = "an alias of the host filesystem"
# The test seam for the mount table: a file in mountinfo's format that stands
# in for `/proc/self/mountinfo` (an empty one names no alias). Read once, at
# the outermost call (`host_mountinfo`); everything below takes the text as a
# parameter. Only the harness's own environment carries it: an arm never runs
# the harness.
MOUNTINFO_ENV = "SKILLS_EVALS_MOUNTINFO"


def host_mountinfo(environ=None) -> str:
    """The mount table an arm's read fence is built from: the file
    `MOUNTINFO_ENV` names, else this namespace's `/proc/self/mountinfo`.
    Empty off Linux, which has no mountinfo to read. Raises
    ArmReadIsolationError (`read_rules_unsafe`) when the table cannot be read,
    or the real one is empty: without it a Linux arm cannot prove that its
    path-based read fences cover the aliases."""
    environ = os.environ if environ is None else environ
    if sys.platform != "linux":
        return ""
    fixture = environ.get(MOUNTINFO_ENV)
    try:
        text = Path(fixture or "/proc/self/mountinfo").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise ArmReadIsolationError(
            "the Linux mount metadata cannot be read", code="read_rules_unsafe") from exc
    if not fixture and not text.strip():
        raise ArmReadIsolationError(
            "the Linux mount metadata is empty", code="read_rules_unsafe")
    return text


def _linux_mounts(text: str) -> list[tuple[str, str, Path, Path, str, str]]:
    """Mountinfo text, parsed: device, source, filesystem root, mountpoint,
    type, options per mount. Mount paths escape spaces, tabs, newlines and
    backslashes as octal. Empty text is no mounts; any other text must name
    the root mount."""
    if not text.strip():
        return []
    try:
        mounts = []
        for line in text.splitlines():
            before, separator, after = line.partition(" - ")
            fields, tail = before.split(), after.split()
            if not separator or len(fields) < 6 or len(tail) < 3:
                raise ValueError("malformed mountinfo")
            unescape = lambda value: re.sub(  # noqa: E731
                r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)
            root, mountpoint = Path(unescape(fields[3])), Path(unescape(fields[4]))
            if not root.is_absolute() or not mountpoint.is_absolute():
                raise ValueError("relative mount path")
            mounts.append((fields[2], unescape(tail[1]), root, mountpoint,
                           tail[0], tail[2]))
        if not any(m[3] == Path("/") for m in mounts):
            raise ValueError("missing root mount")
        return mounts
    except ValueError as exc:
        raise ArmReadIsolationError(
            "the Linux mount metadata cannot establish the host filesystem's "
            "read aliases", code="read_rules_unsafe") from exc


def _mount_at(path: Path, mounts):
    """The active filesystem containing a path; later stacked mounts win."""
    matches = [(len(m[3].parts), index, m) for index, m in enumerate(mounts)
               if _within(path, m[3])]
    return max(matches, key=lambda item: item[:2])[2]


def _host_filesystem_aliases(home: Path, mounts) -> list[Path]:
    """Alternate projections of the root/HOME filesystems, plus WSL /mnt.

    Same-source self-binds (/tmp on /tmp, /snap on /snap) are canonical and
    stay available. A separately mounted HOME uses its longest mount prefix.
    WSL drives and WSLg aliases make all of /mnt a host-data boundary.
    """
    if not mounts:
        return []
    canonical = [_mount_at(Path("/"), mounts), _mount_at(home, mounts)]
    aliases = []
    for mount in mounts:
        device, source, root, mountpoint, filesystem, options = mount
        origins = [origin for origin in canonical
                   if (device, filesystem) == (origin[0], origin[4])]
        self_bind = any(_within(root, origin[2])
                        and mountpoint == origin[3] / root.relative_to(origin[2])
                        for origin in origins)
        if (not self_bind and any(_within(root, origin[2])
                                 or _within(origin[2], root) for origin in origins)
                and mountpoint not in aliases):
            aliases.append(mountpoint)
        if (filesystem == "drvfs" or
                filesystem == "9p" and "aname=drvfs" in options):
            aliases.append(mountpoint)
    if any(_within(alias, Path("/mnt")) for alias in aliases):
        aliases = [p for p in aliases if not _within(p, Path("/mnt"))]
        aliases.append(Path("/mnt"))
    return list(dict.fromkeys(aliases))


def path_without_aliases(path_env: str, aliases) -> str:
    """PATH without the entries an alias's whole deny would leave unreadable.

    Each alias is denied WHOLE (no carve-out: on WSL, one complement rule set
    per Windows PATH directory came to some 49 KB of `--settings`), so an
    entry under one, spelled there or reached through a symlink, holds
    nothing the arm's commands could run. Dropped, in order; every other
    entry (relative ones included) is kept as given. The arm needs no
    Windows-side executable; `arm_path_or_error` refuses an arm whose
    required tools live only under an alias."""
    def under_alias(entry: str) -> bool:
        if not os.path.isabs(entry):
            return False
        lexical = Path(os.path.normpath(entry))
        if any(_within(lexical, a) for a in aliases):
            return True
        return any(_within(Path(entry).resolve(), a) for a in aliases)
    if not aliases:
        return path_env
    return os.pathsep.join(e for e in path_env.split(os.pathsep)
                           if not under_alias(e))


# What every arm's sandboxed commands run: the shell the CLI's Bash tool
# spawns, and git (the agent's own commits; the harness's diff of the
# workspace). An arm whose PATH finds one of these only under a denied alias
# fails with `toolchain_under_alias` rather than reopen the alias.
ARM_REQUIRED_TOOLS = ("bash", "git")


def arm_path_or_error(path_env: str, aliases) -> str:
    """`path_without_aliases`, or ArmReadIsolationError
    (`toolchain_under_alias`) naming the first required tool (never its
    path: the detail is published) that the full PATH finds and the
    stripped one does not."""
    stripped = path_without_aliases(path_env, aliases)
    for tool in ARM_REQUIRED_TOOLS:
        if (shutil.which(tool, path=stripped) is None
                and shutil.which(tool, path=path_env) is not None):
            raise ArmReadIsolationError(
                f"the arm's `{tool}` is found only under {MOUNT_ALIAS_LABEL}, "
                "which the arm's read sandbox denies whole; install it on the "
                "Linux side", code="toolchain_under_alias")
    return stripped


def arm_read_denied(checkouts=(), *, home: Path | None = None,
                    harness_root: Path = HARNESS_ROOT,
                    profiles=None, outputs=(),
                    mountinfo: str | None = None) -> list[tuple[str, Path]]:
    """Every path an agent arm may not read, each with a label for errors.

    The harness checkout, the clone it belongs to, the directory holding that
    clone and its siblings (on a workstation, `~/repos`), every registry and
    guidance checkout the run was given, the run's output directories
    (`outputs`: the results directory, which holds earlier arms' transcripts
    and tool traces), the harness's session archive (earlier multi-turn arms'
    sessions, wherever `XDG_STATE_HOME` puts it), the real HOME and the
    Claude Code profiles (`profile_dirs`). Resolved, in a fixed order, without
    duplicates; a path that does not exist holds nothing to read and is left
    out. Then every alias of the root or HOME filesystem the mount table
    (`mountinfo`, default `host_mountinfo()`) names, WSL's /mnt among them.
    """
    if harness_root == HARNESS_ROOT:
        clone, common = HARNESS_CLONE_ROOT, HARNESS_GIT_COMMON_DIR
    else:
        clone, common = (harness_clone_root(harness_root),
                         harness_git_common_dir(harness_root))
    if clone is None:
        raise ArmReadIsolationError(
            "the harness clone's main checkout cannot be determined (its git "
            "directory is elsewhere, with no core.worktree, and this checkout "
            "is not inside it), so its siblings cannot be denied",
            code="harness_clone_unknown")
    home = Path(home if home is not None else Path.home())
    if profiles is None:
        profiles = profile_dirs(home)
    labeled = [("the harness checkout", harness_root),
               ("the harness clone", clone),
               ("the directory holding the harness clone", clone.parent),
               # Its history, wherever `--separate-git-dir` put it.
               *((("the harness clone's git directory", common),)
                 if common and not _within(common, clone) else ()),
               *(("a registry or guidance checkout", Path(c)) for c in checkouts),
               *(("a harness output directory", Path(o)) for o in outputs),
               ("the harness's session archive", session_archive_dir().parent),
               ("HOME", home),
               *((PROFILE_LABEL, Path(p)) for p in profiles)]
    out: list[tuple[str, Path]] = []
    for label, path in labeled:
        path = path.resolve()
        if not path.exists():
            continue
        index = next((i for i, (_, seen) in enumerate(out) if seen == path), None)
        if index is None:
            out.append((label, path))
        elif label in ("HOME", PROFILE_LABEL):
            # HOME (or a profile) is also the directory holding the clone when
            # it is cloned straight into HOME: it keeps the label that gives it
            # the profile carve-out (`_profile_read_rules`).
            out[index] = (label, path)
    # Mountinfo establishes that these paths exist. Keep the alias spelling:
    # resolving a filesystem bind does not recover its canonical source.
    if mountinfo is None:
        mountinfo = host_mountinfo()
    for path in _host_filesystem_aliases(home.resolve(), _linux_mounts(mountinfo)):
        index = next((i for i, (_, seen) in enumerate(out) if seen == path), None)
        if index is None:
            out.append((MOUNT_ALIAS_LABEL, path))
        else:
            # An alias stays denied whole even when HOME or an inherited
            # profile names that exact mountpoint. Its classification must
            # survive deduplication before profile exceptions are considered.
            out[index] = (MOUNT_ALIAS_LABEL, path)
    return out


def _arm_read_carve_outs(home: Path, guarded: list[Path],
                         path_env: str) -> list[Path]:
    """The `allowRead` entries inside HOME (see ARM_HOME_READ_CARVE_OUTS).

    `guarded` is every denied path but HOME itself: the checkouts and the
    directory holding the clone, the results and the archive, every profile
    and every host-filesystem alias. A candidate that is one of them, holds
    one or lies inside one is never emitted, so a carve-out cannot reopen an
    answer key, another session's transcripts or a deny root."""
    candidates = [home / rel for rel in ARM_HOME_READ_CARVE_OUTS]
    for entry in path_env.split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        entry = Path(entry).resolve()
        candidates.append(entry)
        # `~/bin`'s parent is HOME itself, not a toolchain prefix.
        if entry.name == "bin" and entry.parent != home:
            candidates.append(entry.parent / "lib")
    out = []
    for path in candidates:
        if not path.exists():
            continue
        path = path.resolve()
        # Strictly inside HOME, and neither inside nor around a guarded path.
        if path == home or home not in path.parents or path in out:
            continue
        if any(_within(path, d) or _within(d, path) for d in guarded):
            continue
        out.append(path)
    return out


def run_checkouts(args: argparse.Namespace, registries: dict | None = None,
                  guidance_dir: Path | None = None) -> list[Path]:
    """Every registry and guidance checkout this run was given or defaults to.

    A skill run hands over its resolved `registries`, a guidance run its
    `guidance_dir`; the other kind is resolved here the way `main` would, and
    a registry override `main` would reject adds nothing (the sibling
    defaults sit under the clone's parent, which is denied anyway).
    """
    # Callers that predate #63 hand `_run_arm` a bare registry path, which
    # names no registry this run resolved: resolve them as main would.
    if not isinstance(registries, dict):
        try:
            registries = resolve_registries(
                getattr(args, "registry", None),
                os.environ.get("SKILLS_EVALS_REGISTRIES"), HARNESS_ROOT,
                os.environ.get("AGENTSKILLS_DIR"))
        except ValueError:
            registries = {}
    if guidance_dir is None:
        guidance_dir = guidance.resolve_guidance_dir(
            getattr(args, "guidance", None), os.environ.get("AGENT_GUIDANCE_DIR"),
            HARNESS_ROOT)
    return [*(entry["path"] for entry in registries.values()), guidance_dir]


def run_outputs(args: argparse.Namespace) -> list[Path]:
    """Where this run writes results, transcripts and tool traces, and every
    `--read-deny` directory a wrapper named: its own output tree, which holds
    its earlier runs beside this one's `--results-dir` (local_eval's earlier
    trials, propose_skill_edit's baseline run and proposed patch)."""
    results = getattr(args, "results_dir", None)
    return [*((Path(results).resolve(),) if results is not None else ()),
            *(Path(p).resolve() for p in getattr(args, "read_deny", None) or ())]


def check_workspace_readable(workspace: Path,
                             denied: list[tuple[str, Path]]) -> None:
    """Refuse a workspace the arm could not read: a denied path would cover it.

    Raises ArmReadIsolationError naming the kind of path, not the path (the
    message reaches summary.json, which is published).
    """
    real = Path(workspace).resolve()
    for label, path in denied:
        if _within(real, path):
            raise ArmReadIsolationError(
                f"the arm workspace lies under {label}, which the arm's read "
                "sandbox denies; put TMPDIR outside HOME and the checkouts")


def _glob_escape(text: str) -> str:
    """Read rules use gitignore syntax: escape its pattern characters."""
    return re.sub(r"([*?\[\]\\])", r"\\\1", text)


def _read_rules(pattern: str) -> list[str]:
    """`Read` deny rules for an escaped absolute path and everything under it.
    `//` anchors at the filesystem root; the bare path also covers a Grep or
    Glob rooted at the directory itself."""
    return [f"Read(/{pattern})", f"Read(/{pattern}/**)"]


# A name the complement patterns below can spell: the CLI's project
# directory names (`-` for every other character), `.claude`, `projects`.
_COMPLEMENT_NAME = re.compile(r"[A-Za-z0-9._+~@#%,=-]+\Z")
# What a wildcard rule must never reach through a symlink: the CLI merges
# Read deny rules into the sandbox and, on Linux, follows a symlink a
# wildcard matches to its target (see `_profile_read_rules`).
_SYSTEM_ROOTS = tuple(Path(p) for p in (
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc",
    "/opt", "/proc", "/sys", "/dev", "/run", "/nix", "/snap"))


# The characters a complement pattern spells out in a bracket class. CLI
# 2.1.292's Read-rule matcher does NOT negate a class (`[!c]` and `[^c]`
# both match `c`) and matches case-insensitively (`[A-Z]` matched `t`), both
# measured; so "any character but c" is written as the class of every
# character here but c in either case. Every CLI project directory name is
# drawn from it (`[A-Za-z0-9-]`); a name with a character outside it at the
# point it leaves `keep` is named literally (`_profile_read_rules`). A name
# that equals `keep` but for case is not denied (the matcher cannot tell
# them apart).
_CLASS_CHARS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "0123456789._+~@#%,=-")


def _class_without(char: str) -> str:
    """A bracket class of `_CLASS_CHARS` minus `char` in either case, `-`
    last (literal)."""
    return _class_without_all(char)


def _class_without_all(chars: str, alphabet: str = _CLASS_CHARS,
                       compact_classes: bool = False) -> str:
    """A bracket class of `_CLASS_CHARS` minus every one of `chars` in
    either case, `-` last (literal) unless it is one of them."""
    left = {c.lower() for c in chars}
    kept = [c for c in alphabet if c.lower() not in left and c != "-"]
    if compact_classes:
        # Equivalent ASCII ranges keep large scratch-profile rule sets below
        # the single-argument size limit.
        runs, index = [], 0
        while index < len(kept):
            end = index
            while (end + 1 < len(kept) and kept[end].isalnum()
                   and kept[end + 1].isalnum()
                   and ord(kept[end + 1]) == ord(kept[end]) + 1):
                end += 1
            runs.append(kept[index] + "-" + kept[end] if end - index >= 2
                        else "".join(kept[index:end + 1]))
            index = end + 1
        kept = runs
    if "-" not in left:
        kept.append("-")
    return "[" + "".join(kept) + "]"


def _complement_patterns(keep: str, spare=(),
                         alphabet: str = _CLASS_CHARS,
                         compact_classes: bool = False) -> list[str]:
    """gitignore patterns that match every name in a directory but `keep`
    and the `spare` names, including names created later: at each prefix the
    kept names share, one leaving them there (`<prefix>[<every class char
    but theirs>]*`) and the prefix by name (but `.`, and a kept name), and
    every longer name than a kept one no other extends (`<name>?*`). With
    one kept name: one leaving `keep` at each character, each strict prefix
    by name and `<keep>?*`. Every name is spelled from `_CLASS_CHARS`
    (`_COMPLEMENT_NAME`). Wildcards come without `/**`: a pattern that
    matches a directory covers what is in it (measured with the Read tool)."""
    kept = [keep, *(name for name in spare
                    if name.lower() != keep.lower())]
    folded = {name.lower() for name in kept}
    out, seen = [], set()
    # Prefixes in the order the names spell them: depth first along `keep`.
    for name in kept:
        for index in range(len(name) + 1):
            prefix = name[:index]
            if prefix.lower() in seen:
                continue
            seen.add(prefix.lower())
            nexts = "".join(sorted({other[index] for other in kept
                                    if len(other) > index
                                    and other[:index].lower() == prefix.lower()}))
            if nexts:
                out.append(f"{prefix}{_class_without_all(nexts, alphabet, compact_classes)}*")
            else:
                out.append(f"{prefix}?*")
            if (prefix and prefix not in (".", "..")
                    and prefix.lower() not in folded):
                out.append(prefix)
    return out


def _outside_class(directory: Path, keep: str, spare=(),
                   alphabet: str = _CLASS_CHARS) -> list[str]:
    """The entries of `directory` the complement patterns cannot reach: the
    character where they leave every kept name is not in `_CLASS_CHARS`."""
    if not directory.is_dir():
        return []
    kept = [keep.lower(), *(name.lower() for name in spare)]
    out = []
    for name in sorted(os.listdir(directory)):
        # The matcher ignores case, so the patterns leave a kept name where
        # the case-folded names part.
        if name.lower() in kept:
            continue
        common = max(len(os.path.commonprefix([name.lower(), k])) for k in kept)
        if common < len(name) and name[common] not in alphabet:
            out.append(name)
    return out


def _check_symlinks(directory: Path, root: Path, keep: str,
                    protected: list[Path], needed: list[Path],
                    guarded=(), tmp_root: Path | None = None) -> list[str]:
    """The symlinks in `directory` the wildcard rules must leave alone.

    A wildcard rule that matches a symlink out of `root` denies its target
    to every command (one HOME entry linking into /usr/bin made `bwrap:
    execvp /bin/bash: Permission denied`, measured). So a link that leaves
    `root` for a path the arm needs (`protected`, system and PATH
    directories, reached at all; `needed`, the workspace and TMPDIR, covered
    whole) is spared: no rule names it, and what it leads to is no secret (a
    GitHub runner's and a container's HOME hold such links). A link to any
    other directory outside `root` is denied as usual. Refused
    (`read_rules_unsafe`): a link that would have to be spared but leads
    into or around a path that must stay denied (`guarded`: a checkout, an
    output directory, the archive, another profile, a harness directory
    under TMPDIR, or `tmp_root` itself, where later ones appear), since the
    Read tool would reach it through the spared name; and one whose name the
    patterns cannot spell. The matcher ignores case, so another existing
    entry at the same level with the same name ignoring case is refused
    too: sparing the link would spare that entry, and a literal deny for
    the entry would follow the link into its needed target."""
    if not directory.is_dir():
        return []
    spare = []
    entries = sorted(directory.iterdir())
    for entry in entries:
        if entry.name == keep:
            continue
        try:
            if not entry.is_symlink():
                continue
            target = entry.resolve()
        except PermissionError:
            # An unreadable entry cannot provide a readable escape. Leave
            # its name covered by the deny patterns.
            continue
        if _within(target, root):
            continue
        if not (any(_within(target, p) or _within(p, target) for p in protected)
                or any(_within(n, target) for n in needed)):
            continue
        if (any(_within(target, g) or _within(g, target) for g in guarded)
                or (tmp_root is not None and _within(tmp_root, target))
                or _within(root, target)
                or not _COMPLEMENT_NAME.match(entry.name)):
            raise ArmReadIsolationError(
                f"a symlink in {PROFILE_LABEL} or HOME leads both to a path "
                "the arm needs and to one it may not read",
                code="read_rules_unsafe")
        if any(other.name != entry.name
               and other.name.lower() == entry.name.lower()
               for other in entries):
            raise ArmReadIsolationError(
                f"a symlink in {PROFILE_LABEL} or HOME has another entry "
                "with the same name ignoring case",
                code="read_rules_unsafe")
        spare.append(entry.name)
    return spare


def _profile_read_rules(root: Path, session_dir: Path | None,
                        protected: list[Path], needed: list[Path],
                        guarded=(), tmp_root: Path | None = None,
                        compact_classes=False,
                        readable_plugins=()) -> list[str]:
    """The Read deny rules for HOME or a profile, which leave `session_dir`.

    `session_dir` is the arm's own session directory when it lies under
    `root` (a skill arm's `~/.claude/projects/<workspace>`): the CLI saves a
    large tool output there and the agent reads it back with the Read tool,
    and a Read allow rule cannot carve a hole in a deny. So at each level on
    the way down, `_complement_patterns` deny every other name, present or
    future: another session's project directory created after these
    settings were built is denied too. A symlink out of `root` to a path
    the arm needs is spared at its level (`_check_symlinks`). The sandbox
    still denies all of `root` to commands. A name the patterns cannot spell
    denies `root` whole.
    """
    if readable_plugins:
        kept = [p for p in readable_plugins if _within(p, root)]
        if session_dir is not None and root in session_dir.parents:
            kept.append(session_dir)
        if kept:
            return _profile_branch_read_rules(root, kept, protected, needed,
                                              guarded, tmp_root)
    whole = _read_rules(_glob_escape(str(root)))
    if session_dir is None or root not in session_dir.parents:
        return whole
    parts = session_dir.relative_to(root).parts
    if not all(_COMPLEMENT_NAME.match(part) for part in parts):
        return whole
    rules, current = [], root
    for part in parts:
        spare = _check_symlinks(current, root, part, protected, needed,
                                guarded, tmp_root)
        base = _glob_escape(str(current))
        for pattern in _complement_patterns(part, spare, compact_classes=compact_classes):
            if "*" in pattern:
                rules.append(f"Read(/{base}/{pattern})")
            else:
                rules += _read_rules(f"{base}/{_glob_escape(pattern)}")
        for name in _outside_class(current, part, spare):
            rules += _read_rules(f"{base}/{_glob_escape(name)}")
        current = current / part
    return rules


def _profile_branch_read_rules(root: Path, kept, protected, needed,
                               guarded, tmp_root) -> list[str]:
    """Deny a scratch profile except its verified plugin trees and session.

    Each kept path gets a branch through the profile, so newly created
    siblings remain denied. Host filesystem aliases never use this exception.
    """
    paths = [p for p in kept if _within(p, root)]
    if not paths:
        return _read_rules(_glob_escape(str(root)))
    alphabet = _CLASS_CHARS + " "
    if any(any(c not in alphabet for part in p.relative_to(root).parts for c in part)
           for p in paths):
        raise ArmReadIsolationError(
            "a scratch profile branch cannot be spared safely by the Read rules",
            code="read_rules_unsafe")

    def visit(directory, branches):
        if directory in branches:
            return []
        names = list(dict.fromkeys(p.relative_to(directory).parts[0] for p in branches))
        spare = _check_symlinks(directory, root, names[0], protected, needed,
                                guarded, tmp_root)
        others = list(dict.fromkeys([*names[1:], *spare]))
        base = _glob_escape(str(directory))
        rules = []
        for pattern in _complement_patterns(names[0], others, alphabet, compact_classes=True):
            if "*" in pattern:
                rules.append(f"Read(/{base}/{pattern})")
            else:
                rules += _read_rules(f"{base}/{_glob_escape(pattern)}")
        for name in _outside_class(directory, names[0], others, alphabet):
            rules += _read_rules(f"{base}/{_glob_escape(name)}")
        for name in names:
            child = directory / name
            rules += visit(child, [p for p in branches if _within(p, child)])
        return rules

    return visit(root, paths)


def _agent_config_dirs(workspace: Path, config_dir: Path | None = None) -> list[Path]:
    """The directories whose contents configure the arm: the workspace's
    `.claude/` and the arm's own profile (`config_dir`: a guidance arm's
    scratch `CLAUDE_CONFIG_DIR`, whose `settings.json` is the session's user
    settings), each as given and resolved.

    What the CLI loads from there (settings and their hooks, skills, agents,
    commands) runs as the arm's own configuration, and a hook runs outside
    the sandbox; a settings reload mid-session can fire one in the same turn.
    So the agent may not write them: `Edit(...)` deny rules cover the Edit,
    Write and NotebookEdit tools (https://code.claude.com/docs/en/permissions,
    "Edit rules apply to all built-in tools that edit files"; deny rules hold
    in every mode, `bypassPermissions` included), and `denyWrite` covers Bash
    and its children. The CLI's own writes there are not tool calls and are
    not affected. Trusted hooks (a plugin's, a skill's frontmatter) are left
    running: turning hooks off would cripple the subject under test.
    """
    out = []
    candidates = [Path(workspace) / ".claude", Path(workspace).resolve() / ".claude"]
    if config_dir is not None:
        candidates += [Path(config_dir), Path(config_dir).resolve()]
    for path in candidates:
        if path not in out:
            out.append(path)
    return out


# The prefixes of every named temporary store the harness and scripts make
# under TMPDIR (including explicit parents that may be TMPDIR): other
# arms' workspaces and scratch profiles (transcripts), canary and
# propagation legs, scoring copies, `deps:` caches, objective-command
# scratch and `workspace_git`'s private metadata copies (`trusted-git-`,
# every workspace's own included: the arm may neither read nor alter the
# Git state the harness trusts), and the session miner's clone of the
# lesson records and its classify calls (ADR 0015). Leftovers from a crashed
# run, or a concurrent one, are denied.
HARNESS_TEMP_PREFIXES = (
    "workspace-", "skills-evals-", "guidance-bridge-canary-", "propagation-",
    "scoring-seed-", "deps-python-", "deps-bundler-", "deps-cache-", "objective-repo-tests-",
    "objective-command-", "local-eval-guard-", "sink-mutation-", "trusted-git-",
    "scaffold-", "scaffold-context-", "claude-probe-home-", "skill-edit-guard-",
    "propose-skill-edit-", "scoring-guidance-", "scoring-skill-", "mine-real-work-",
    ".scaffold-", ".gh-label-", ".gh-timeline-", "usage-census-",
    "session-miner-", "lesson-classify-")


def _harness_temp_rules(tmp_root: Path, workspace: Path | None,
                        protected: list[Path], needed: list[Path], profiles=()):
    """The sandbox `denyRead` paths and Read deny rules for the harness's
    directories under `tmp_root`, but the arm's own (the top directory its
    workspace sits in). Read rules are structural, so a directory created
    later is denied too: `<prefix>*` for a prefix the arm's own directory
    does not carry, else the complement patterns of its name that keep the
    prefix (`_complement_patterns`). Existing ones are listed for commands
    as well; a symlink among them that reaches a path the arm needs is
    refused, as in `_check_symlinks`."""
    own = (workspace.relative_to(tmp_root).parts[0]
           if workspace is not None and tmp_root in workspace.parents else None)
    own_names = list(dict.fromkeys([
        *([own] if own is not None else []),
        *(path.relative_to(tmp_root).parts[0] for path in profiles
          if tmp_root in path.parents)]))
    base = _glob_escape(str(tmp_root))
    rules = []
    for prefix in HARNESS_TEMP_PREFIXES:
        kept = [name for name in own_names if name.lower().startswith(prefix.lower())]
        if kept and all(_COMPLEMENT_NAME.match(name) for name in kept):
            for index, pattern in enumerate(_complement_patterns(kept[0], kept[1:])):
                if pattern.lower().startswith(prefix.lower()):
                    if "*" in pattern:
                        rules.append(f"Read(/{base}/{pattern})")
                    else:
                        rules += _read_rules(f"{base}/{_glob_escape(pattern)}")
        else:
            rules.append(f"Read(/{base}/{_glob_escape(prefix)}*)")
    denied = []
    try:
        entries = sorted(os.listdir(tmp_root))
    except OSError:
        entries = []
    for name in entries:
        if name in own_names or not name.lower().startswith(
                tuple(p.lower() for p in HARNESS_TEMP_PREFIXES)):
            continue
        path = tmp_root / name
        if path.is_symlink():
            target = path.resolve()
            if (any(_within(target, p) or _within(p, target) for p in protected)
                    or any(_within(n, target) for n in needed)):
                raise ArmReadIsolationError(
                    "a harness directory under TMPDIR is a symlink to a path "
                    "the arm needs", code="read_rules_unsafe")
        denied.append(path)
    return denied, rules


# The `--settings` value is one argv element; Linux caps one at 128 KiB
# (E2BIG). Well under it, and a settings object this large means the rules
# went wrong somewhere.
MAX_SETTINGS_BYTES = 64 * 1024


def arm_sandbox_settings(checkouts=(), *, home: Path | None = None,
                         path_env: str | None = None,
                         harness_root: Path = HARNESS_ROOT,
                         session_dir: Path | None = None,
                         workspace: Path | None = None,
                         config_dir: Path | None = None,
                         profiles=None, outputs=(), additional_profiles=(),
                         tmp_root: Path | None = None,
                         mountinfo: str | None = None,
                         readable_plugins=()) -> dict:
    """The settings every agent arm runs under, as one JSON object.

    `checkouts` are the run's registry and guidance checkouts; `session_dir`
    is where the arm's CLI will save its session (see `_profile_read_rules`);
    `workspace` and `config_dir` are the arm's own, which the agent may not
    configure (see `_agent_config_dirs`). `home`, `path_env`, `harness_root`
    and `profiles` (`profile_dirs`) default to this process's own, and
    `mountinfo` to `host_mountinfo()`. A host-filesystem alias is denied
    whole, and PATH entries under one are dropped (`path_without_aliases`)
    before any carve-out is considered.
    Raises ArmReadIsolationError when the read rules cannot be built safely.
    """
    home = Path(home if home is not None else Path.home()).resolve()
    if profiles is None:
        profiles = profile_dirs(home)
    compact_profiles = {Path(path).resolve() for path in additional_profiles}
    readable_plugins = [Path(path).resolve() for path in readable_plugins]
    if any(not any(_within(path, root) and path != root for root in compact_profiles)
           for path in readable_plugins):
        raise ArmReadIsolationError("plugin reads must stay inside the scratch profile",
                                    code="read_rules_unsafe")
    profiles = [*profiles, *additional_profiles]
    if mountinfo is None:
        mountinfo = host_mountinfo()
    denied = arm_read_denied(checkouts, home=home, harness_root=harness_root,
                             profiles=profiles, outputs=outputs,
                             mountinfo=mountinfo)
    aliases = [path for label, path in denied if label == MOUNT_ALIAS_LABEL]
    roots = [path for label, path in denied
             if label != MOUNT_ALIAS_LABEL
             and (label in ("HOME", PROFILE_LABEL) or path == home)]
    # HOME may also be the directory holding the clone (a cloud routine
    # clones into it): the carve-outs still keep clear of every checkout.
    hard = [path for _, path in denied if path not in roots]
    if any(_within(path, denied_root) for path in readable_plugins for denied_root in hard):
        raise ArmReadIsolationError("plugin exception overlaps a denied source",
                                    code="read_rules_unsafe")
    if path_env is None:
        path_env = os.environ.get("PATH", "")
    path_env = path_without_aliases(path_env, aliases)
    # Carve-outs keep clear of every denied path but HOME itself, which may
    # also be the directory holding the clone (a cloud routine clones into
    # it): the checkouts, outputs, archive, profiles and aliases.
    guarded_carve = [path for label, path in denied
                     if path != home or label == MOUNT_ALIAS_LABEL]
    carve = _arm_read_carve_outs(home, guarded_carve, path_env) if home.is_dir() else []
    keep = Path(session_dir).resolve() if session_dir else None
    config_dirs = (_agent_config_dirs(workspace, config_dir)
                   if workspace is not None else [])
    protected = [*_SYSTEM_ROOTS,
                 *(Path(e).resolve() for e in path_env.split(os.pathsep)
                   if e and os.path.isabs(e))]
    needed = [Path(tempfile.gettempdir()).resolve()]
    if workspace is not None:
        needed.append(Path(workspace).resolve())
    tmp_root = Path(tmp_root or tempfile.gettempdir()).resolve()
    tmp_denied, tmp_rules = _harness_temp_rules(
        tmp_root, Path(workspace).resolve() if workspace is not None else None,
        protected, needed, compact_profiles)
    read_rules = []
    for _, path in denied:
        # What a spared symlink in HOME or a profile must not lead to: every
        # other denied path and the harness's directories under TMPDIR.
        guarded = [p for _, p in denied if p != path] + tmp_denied
        # An alias, like every other path but HOME and the profiles, is
        # denied whole: no rule and no carve-out inside it.
        for rule in (_profile_read_rules(path, keep, protected, needed,
                                         guarded, tmp_root,
                                         compact_classes=path in compact_profiles,
                                         readable_plugins=readable_plugins) if path in roots
                     else _read_rules(_glob_escape(str(path)))):
            if rule not in read_rules:
                read_rules.append(rule)
    return {
        "sandbox": {
            "enabled": True,
            # Refuse to start rather than run commands unsandboxed.
            "failIfUnavailable": True,
            # No `dangerouslyDisableSandbox` retry.
            "allowUnsandboxedCommands": False,
            # Explicit, so no user or project value fills them in; a managed
            # value would still win, so `managed_sandbox_refusal` refuses one.
            "excludedCommands": [],
            "network": {"strictAllowlist": True, "allowedDomains": [],
                        "deniedDomains": list(ARM_DENIED_DOMAINS),
                        "allowAllUnixSockets": False, "allowUnixSockets": [],
                        "allowLocalBinding": False},
            "filesystem": {"denyRead": [*(str(p) for _, p in denied),
                                        *(str(p) for p in tmp_denied)],
                           "allowRead": [str(p) for p in [*carve, *readable_plugins]],
                           "denyWrite": list(dict.fromkeys(str(p) for p in [
                               *config_dirs, *compact_profiles,
                               *(Path(p).resolve() for p in checkouts)]))},
        },
        "permissions": {"deny": [
            *read_rules,
            *tmp_rules,
            *(rule for path in config_dirs
              for rule in (f"Edit(/{_glob_escape(str(path))})",
                           f"Edit(/{_glob_escape(str(path))}/**)")),
            *(rule for path in [*compact_profiles, *(Path(p).resolve() for p in checkouts)]
              for rule in (f"Edit(/{_glob_escape(str(path))})",
                           f"Edit(/{_glob_escape(str(path))}/**)"))]},
    }


def arm_isolation_flags(checkouts=(), session_dir: Path | None = None,
                        workspace: Path | None = None,
                        config_dir: Path | None = None, outputs=(), *,
                        path_env: str | None = None,
                        mountinfo: str | None = None,
                        additional_profiles=(), readable_plugins=()) -> list[str]:
    """The CLI flags that put an agent arm behind `arm_sandbox_settings`
    (`path_env`, the arm's own PATH, and `mountinfo` default as there).
    Raises ArmReadIsolationError (`settings_too_large`) past
    MAX_SETTINGS_BYTES rather than handing the OS an argument it refuses."""
    settings = json.dumps(arm_sandbox_settings(
        checkouts, session_dir=session_dir, workspace=workspace,
        config_dir=config_dir, outputs=outputs, additional_profiles=additional_profiles,
        readable_plugins=readable_plugins, path_env=path_env,
        mountinfo=mountinfo), sort_keys=True, separators=(",", ":"))
    if len(settings.encode()) > MAX_SETTINGS_BYTES:
        raise ArmReadIsolationError(
            f"the arm's --settings is {len(settings.encode())} bytes, over "
            f"the {MAX_SETTINGS_BYTES}-byte cap", code="settings_too_large")
    return ["--settings", settings,
            "--disallowedTools", ",".join(ARM_DISALLOWED_TOOLS), "--no-chrome"]


def _sandbox_unavailable_detail(stderr: str) -> str | None:
    """The CLI's own line saying the sandbox is not running, else None."""
    for line in (stderr or "").splitlines():
        if _SANDBOX_DOWN_LINE.match(line.strip()):
            return line.strip()[:400]
    return None


# The managed sandbox and permission settings an arm's run accepts, each with
# the values it may take; ANY other key under `sandbox` or `permissions`, or
# an accepted key with another value, refuses the run, so a key a later CLI
# adds fails closed. A managed value outranks `--settings` and managed arrays
# merge in (https://code.claude.com/docs/en/sandboxing, "Keep developers from
# widening the policy"), so these are the keys that can only keep the arm's
# boundary as tight as `--settings` sets it or tighter; a widening key is
# accepted only at the arm's own restrictive value (`[]`, `false`). Among the
# refused, measured or read in CLI 2.1.292: `enabledPlatforms` without this
# platform and `filesystem.disabled: true` switch confinement off with no
# sandbox-down line; `enableWeakerNestedSandbox: true` exposes the host's
# /proc; `bwrapPath`/`socatPath` choose the binaries that enforce it;
# non-empty `excludedCommands`, proxy ports, Unix sockets,
# `allowLocalBinding: true`, `allowedDomains`, `allowRead`, `allowWrite` and
# `permissions.additionalDirectories` widen it; and top-level
# `allowManagedPermissionRulesOnly: true` drops the `--settings` Read and
# Edit denies.
_ANY_BOOL = (True, False)
# Value kinds: a tuple of the exact values allowed (`True`/`False` compared by
# identity, so `1` and `{}` do not pass), "strings" (a list of short strings),
# "empty" (exactly `[]`, the arm's own baseline), "platforms" (strings,
# including this platform), "str" (a short string), "credential_files" and
# "credential_env" (lists of entries `_credential_entry_ok` accepts).
_MANAGED_ACCEPTED = {
    ("sandbox", "enabled"): (True,),
    ("sandbox", "failIfUnavailable"): (True,),
    ("sandbox", "allowUnsandboxedCommands"): (False,),
    ("sandbox", "autoAllowBashIfSandboxed"): _ANY_BOOL,
    ("sandbox", "enabledPlatforms"): "platforms",
    # The arm's own baseline values, and the CLI defaults that match it.
    ("sandbox", "excludedCommands"): "empty",
    ("sandbox", "enableWeakerNestedSandbox"): (False,),
    ("sandbox", "enableWeakerNetworkIsolation"): (False,),
    ("sandbox", "filesystem", "disabled"): (False,),
    ("sandbox", "filesystem", "allowRead"): "empty",
    ("sandbox", "filesystem", "allowWrite"): "empty",
    ("sandbox", "filesystem", "denyRead"): "strings",
    ("sandbox", "filesystem", "denyWrite"): "strings",
    ("sandbox", "filesystem", "allowManagedReadPathsOnly"): _ANY_BOOL,
    ("sandbox", "network", "deniedDomains"): "strings",
    ("sandbox", "network", "allowedDomains"): "empty",
    ("sandbox", "network", "strictAllowlist"): (True,),
    ("sandbox", "network", "allowManagedDomainsOnly"): _ANY_BOOL,
    ("sandbox", "network", "allowAllUnixSockets"): (False,),
    ("sandbox", "network", "allowUnixSockets"): "empty",
    ("sandbox", "network", "allowLocalBinding"): (False,),
    ("sandbox", "credentials", "files"): "credential_files",
    ("sandbox", "credentials", "envVars"): "credential_env",
    ("permissions", "deny"): "strings",
    ("permissions", "ask"): "strings",
    # An allow rule never outranks a deny rule (the arm's Read and Edit
    # denies) and the sandbox still confines what it approves.
    ("permissions", "allow"): "strings",
    ("permissions", "additionalDirectories"): "empty",
    ("permissions", "defaultMode"): "str",
    ("permissions", "disableBypassPermissionsMode"): "str",
    ("permissions", "disableAutoMode"): "str",
    ("permissions", "blockReadsOutsideWorkingDirectories"): _ANY_BOOL,
}
# The objects the accepted keys sit in; only these may be (empty) objects.
_MANAGED_STRUCTURE = {("sandbox",), ("sandbox", "filesystem"),
                      ("sandbox", "network"), ("sandbox", "credentials"),
                      ("permissions",)}
# Top-level managed keys that change what the arm's `--settings` can do.
_MANAGED_TOP_LEVEL = {"allowManagedPermissionRulesOnly": (False,)}
_MANAGED_STRING_MAX = 4096


def _short_string(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MANAGED_STRING_MAX


# A credential entry's fields as CLI 2.1.292's schema declares them (read from
# its bundle), each with a check of its value. Every one narrows access or,
# in `deny` mode, is ignored; `injectHosts` would let the host proxy send the
# real value to those hosts, so only an empty list is accepted.
_CREDENTIAL_FIELDS = {
    "mode": lambda v: v in ("deny", "mask"),
    "extract": lambda v: isinstance(v, str) and len(v) <= _MANAGED_STRING_MAX,
    "onExtractNoMatch": lambda v: v in ("warn", "deny", "error"),
    "decode": lambda v: v == "jwt",
    "maskClaims": lambda v: isinstance(v, list) and all(map(_short_string, v)),
    "maskDuplicates": lambda v: isinstance(v, bool),
    "injectHosts": lambda v: v == [] and isinstance(v, list),
}
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _credential_entry_ok(entry, kind: str) -> bool:
    """One `sandbox.credentials` entry: a `path` (files) or a `name` (envVars),
    a `mode`, and only the schema's other fields, each of the right type."""
    if not isinstance(entry, dict) or "mode" not in entry:
        return False
    if kind == "credential_files":
        key, fields = "path", set(_CREDENTIAL_FIELDS)
        key_ok = _short_string(entry.get("path"))
    else:
        key, fields = "name", set(_CREDENTIAL_FIELDS) - {"maskDuplicates"}
        key_ok = isinstance(entry.get("name"), str) and bool(_ENV_NAME.match(entry["name"]))
    return (key_ok and set(entry) <= fields | {key}
            and all(_CREDENTIAL_FIELDS[name](value)
                    for name, value in entry.items() if name != key))


def _managed_value_ok(kind, value, platform: str) -> bool:
    if kind == "platforms":
        return (isinstance(value, list) and all(map(_short_string, value))
                and platform in value)
    if kind == "strings":
        return isinstance(value, list) and all(map(_short_string, value))
    if kind == "empty":
        return value == [] and isinstance(value, list)
    if kind == "str":
        return _short_string(value)
    if kind in ("credential_files", "credential_env"):
        return isinstance(value, list) and all(
            _credential_entry_ok(entry, kind) for entry in value)
    return any(value is allowed for allowed in kind)


def _managed_violation(value, keys: tuple, platform: str) -> str | None:
    """The first key path under `keys` the arm's sandbox does not accept,
    with why, or None: an object only at a `_MANAGED_STRUCTURE` path, every
    other key on `_MANAGED_ACCEPTED` with a value of its kind."""
    dotted = ".".join(keys)
    if keys in _MANAGED_STRUCTURE:
        if not isinstance(value, dict):
            return f"{dotted} to something other than an object"
        for key, child in value.items():
            found = _managed_violation(child, (*keys, str(key)), platform)
            if found:
                return found
        return None
    kind = _MANAGED_ACCEPTED.get(keys)
    if kind is None:
        return (f"{dotted}, which is not on the list of managed keys an "
                "arm's sandbox accepts")
    if not _managed_value_ok(kind, value, platform):
        if kind == "platforms":
            return f"{dotted} without {platform!r}"
        return f"{dotted} to a value the arm's sandbox does not accept"
    return None


# Where a managed policy lives (the CLI's documented locations). The one
# copy: scripts/local_eval.py hands the same paths to its guard launcher.
MANAGED_SETTINGS_FILES = (
    Path("/etc/claude-code/managed-settings.json"),
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("C:/Program Files/ClaudeCode/managed-settings.json"))
MANAGED_SETTINGS_DROPINS = (Path("/etc/claude-code/managed-settings.d"),)


def managed_settings_paths(files=None, dropins=None) -> list[Path]:
    """The managed policy files, each drop-in directory's `*.json` included,
    defaulting to the documented locations. A missing file is listed; a
    reader treats it as no policy."""
    paths = list(map(Path, MANAGED_SETTINGS_FILES if files is None else files))
    for directory in map(Path, MANAGED_SETTINGS_DROPINS if dropins is None else dropins):
        if directory.is_dir():
            paths += sorted(directory.glob("*.json"))
    return paths


def cli_platform() -> str:
    """The CLI's own name for this platform, as `enabledPlatforms` lists it."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    try:
        version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        version = ""
    return "wsl" if "microsoft" in version else "linux"


def managed_sandbox_refusal(files=None, dropins=None,
                            platform: str | None = None) -> str | None:
    """Why the managed settings make an arm's sandbox untrustworthy, or None.

    Reads the managed policy files local_eval's settings guard checks
    (`MANAGED_SETTINGS_FILES` and `_DROPINS`) and accepts only
    `_MANAGED_ACCEPTED` under `sandbox` and `permissions`, plus
    `_MANAGED_TOP_LEVEL`. Names the file and key, never a value; an
    unreadable file is a refusal too.
    """
    platform = platform or cli_platform()
    for path in managed_settings_paths(files, dropins):
        try:
            settings = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            return (f"managed settings file {path} cannot be read "
                    f"({type(exc).__name__})")
        if not isinstance(settings, dict):
            return f"managed settings file {path} is not a JSON object"
        for key, allowed in _MANAGED_TOP_LEVEL.items():
            if key in settings and not _managed_value_ok(allowed, settings[key], platform):
                return (f"managed settings file {path} sets {key}, which the "
                        "arm's --settings cannot outrank")
        for section in ("sandbox", "permissions"):
            if section in settings:
                found = _managed_violation(settings[section], (section,), platform)
                if found:
                    return f"managed settings file {path} sets {found}"
    return None


# What the CLI and trusted delivery hook write, during a turn, under the directories
# `_agent_config_dirs` names, as relative paths (fnmatch, `*` crossing `/`).
# Measured with CLI 2.1.292 (strace of a two-turn arm with a Bash call, a
# saved large output, a Write and a resume): under the workspace's `.claude/`
# an empty `.cc-writes/`; under the profile the rest. Anything else that
# changes during a turn is the agent's, apart from the hook receipt below.
#
# Trees: the whole subtree is the CLI's, whatever it holds; the CLI loads no
# configuration from any of them.
_CLI_WORKSPACE_TREES = (".cc-writes",)
_CLI_PROFILE_TREES = (".cc-writes", "backups", "bridge-spawn", "projects",
                      "seed-admin", "session-env", "sessions", "shell-snapshots")
# Markers: exempt only as what the CLI writes, a regular file (not a symlink
# or a directory, which could hold a plugin's `hooks/hooks.json`) of at most
# the given size in bytes; anything else at that path is a change. The lock
# is a directory the CLI creates and removes, exempt only while empty.
_BUILTIN_PLUGIN_STORE = "plugins/store/cc-plugin-*_builtin-" + "[0-9a-f]" * 12 + ".json"
_CLI_PROFILE_MARKERS = {
    ".claude.json": None, ".claude.json.tmp.*": None,
    "plugins/cache/*/.orphaned_at": 4096,
    "skills/synced/*/.last-complete-round": 4096,
    # The trusted fleet-memory hook appends this detached delivery receipt,
    # possibly after delivery returned. It loads no CLI configuration. The
    # writer checks 1 MiB before its last append of at most 4096 bytes.
    "fleet-delivery.jsonl": 1024 * 1024 + 4096,
    # CLI 2.1.304 writes these at startup in a fresh profile. The policy
    # limits are server-signed and can only restrict; the stamp, synced-at,
    # cleanup and sweep files are timestamps.
    ".last-cleanup": 4096, "plugins/.last_inuse_sweep": 4096,
    "policy-limits.json": 65536, "policy-limits.json.signature.json": 65536,
    "policy-limits.json.stamp.json": 4096, "remote-settings.json.synced-at": 4096,
    # Server-managed settings, which the CLI loads: exempt only while they
    # are an empty JSON object, as written at startup; anything else could
    # configure the agent.
    "remote-settings.json": (4096, "{}"),
    # A newer CLI (seen 2026-10-27 in the routine, which installs the latest)
    # writes the policy limits' signature issue time beside the signature
    # above. Like it, it can only restrict and loads no configuration.
    "policy-limits.json.signature-iat.json": 65536,
    # CLI 2.1.310 caches the plugin directory's catalog here (it reads back at
    # most 16 MiB). It is a list of plugins one could install, not an install:
    # plugins load from installed_plugins.json and plugins/cache/, still watched.
    "plugins/plugin-directory-cache-v2.json": 16 * 1024 * 1024,
    # CLI 2.1.311 writes a built-in plugin's key-value store here on every
    # session (the agents-md plugin's usage-row bookkeeping). Plugins load from
    # installed_plugins.json and plugins/cache/, not from a store; a
    # third-party plugin's store stays watched. The suffix is spelled out
    # because `*` crosses `/`: a bare `*.json` would also match a
    # `.json/hooks/hooks.json` planted beneath a directory of that name.
    _BUILTIN_PLUGIN_STORE: 65536,
}
_CLI_PROFILE_EMPTY_DIRS = (".claude.json.lock",)
# A shared profile (the operator's real `~/.claude`, or an inherited
# `CLAUDE_CONFIG_DIR`) changes under every other session, so only the paths
# the CLI loads configuration from are watched there; the CLI's own synced
# account skills are its bookkeeping.
_SHARED_PROFILE_CONFIG = ("settings.json", "settings.local.json", "CLAUDE.md",
                          "agents", "commands", "hooks", "rules",
                          "output-styles", "skills", "plugins")
_SHARED_PROFILE_TREES = ("skills/synced",)
# `plugins/` (installed and known lists, cached plugins and marketplaces, all
# plugin-loading surfaces) is watched whole; the only writes measured there
# during a two-turn arm (strace, CLI 2.1.292) were `.orphaned_at` markers.
_SHARED_PROFILE_MARKERS = {"plugins/cache/*/.orphaned_at": 4096,
                           # A CLI 2.1.304 startup timestamp (see above).
                           "plugins/.last_inuse_sweep": 4096,
                           # The CLI 2.1.310 catalog cache (see above).
                           "plugins/plugin-directory-cache-v2.json": 16 * 1024 * 1024,
                           # The CLI 2.1.311 built-in plugin store (see above).
                           _BUILTIN_PLUGIN_STORE: 65536}


def _is_empty_json_object(path: Path, limit: int) -> bool:
    """Whether `path` (not followed if a symlink) holds `{}` as JSON."""
    try:
        # Non-blocking, so a FIFO swapped in after the lstat cannot hang it.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return False
        data = stream.read(limit + 1)
    if len(data) > limit:
        return False
    try:
        return json.loads(data) == {}
    except ValueError:
        return False


def _exempt(path: Path, rel: str, trees, markers, empty_dirs) -> bool:
    """Whether `rel` is CLI or trusted delivery bookkeeping (see above)."""
    if any(rel == t or rel.startswith(t + "/") for t in trees):
        return True
    st = os.lstat(path)
    for pattern, limit in markers.items():
        if fnmatch.fnmatchcase(rel, pattern):
            # The receipt writer also refuses foreign-owned or hard-linked
            # files; such a path is not its trusted bookkeeping.
            if rel == "fleet-delivery.jsonl" and (
                    not hasattr(os, "geteuid") or st.st_nlink != 1
                    or st.st_uid != os.geteuid()):
                return False
            if isinstance(limit, tuple):
                # (size cap, "{}"): also content-checked as an empty object.
                limit = limit[0]
                return (stat.S_ISREG(st.st_mode) and st.st_size <= limit
                        and _is_empty_json_object(path, limit))
            return stat.S_ISREG(st.st_mode) and (limit is None or st.st_size <= limit)
    if rel in empty_dirs:
        return stat.S_ISDIR(st.st_mode) and not os.listdir(path)
    return False


def _config_snapshot(root: Path, label: str, trees=(), markers=None,
                     empty_dirs=(), only=None) -> dict[str, bytes | None]:
    """Every file and symlink under `root` by `label`-prefixed relative path
    (a file's SHA-256, a symlink's target), minus the CLI's bookkeeping.
    `only` limits the walk to those top-level names."""
    root = Path(root)
    markers = markers or {}
    out: dict[str, bytes | None] = {}
    if root.is_symlink() or root.is_file():
        return {label: b"symlink:" + os.readlink(root).encode()
                if root.is_symlink() else hashlib.sha256(root.read_bytes()).digest()}
    if not root.is_dir():
        return out
    for current, dirs, files in os.walk(root):
        rel_dir = Path(current).relative_to(root)
        if not rel_dir.parts and only is not None:
            dirs[:] = [d for d in dirs if d in only]
            files = [f for f in files if f in only]
        kept = []
        for name in sorted(dirs + files):
            rel = (rel_dir / name).as_posix()
            path = Path(current) / name
            key = f"{label}/{rel}"
            try:
                if _exempt(path, rel, trees, markers, empty_dirs):
                    continue
            except FileNotFoundError:
                # Listed, then gone (another session, or the agent): a
                # change, never a crash. An exempt tree is never looked at.
                out[key] = b"<vanished>"
                continue
            if path.is_symlink():
                out[key] = b"symlink:" + os.readlink(path).encode()
            elif path.is_dir():
                # Not recorded itself: an empty directory configures
                # nothing, and what it comes to hold is.
                kept.append(name)
            else:
                try:
                    out[key] = hashlib.sha256(path.read_bytes()).digest()
                except OSError:
                    out[key] = b"<unreadable>"
        dirs[:] = [d for d in dirs if d in kept]
    return out


def _agent_config_snapshot(workspace: Path, config_dir: Path | None = None,
                           shared: bool = False) -> dict[str, bytes | None]:
    """The workspace's `.claude/` and the arm's profile, by relative path (see
    `_config_snapshot`): a scratch profile whole, a shared one (`shared`)
    only where the CLI loads configuration from."""
    out = _config_snapshot(Path(workspace) / ".claude", ".claude",
                           trees=_CLI_WORKSPACE_TREES)
    if config_dir is not None:
        if shared:
            out.update(_config_snapshot(Path(config_dir), "$CLAUDE_CONFIG_DIR",
                                        trees=_SHARED_PROFILE_TREES,
                                        markers=_SHARED_PROFILE_MARKERS,
                                        only=_SHARED_PROFILE_CONFIG))
        else:
            out.update(_config_snapshot(
                Path(config_dir), "$CLAUDE_CONFIG_DIR", trees=_CLI_PROFILE_TREES,
                markers=_CLI_PROFILE_MARKERS, empty_dirs=_CLI_PROFILE_EMPTY_DIRS))
    return out


def _agent_config_written(workspace: Path, before: dict,
                          config_dir: Path | None = None,
                          shared: bool = False) -> str | None:
    """What the agent created, changed or removed in its configuration since
    `before`, named relative to the workspace or profile (at most five)."""
    after = _agent_config_snapshot(workspace, config_dir, shared)
    changed = sorted(name for name in set(before) | set(after)
                     if name not in before or name not in after
                     or before[name] != after[name])
    if not changed:
        return None
    more = f" and {len(changed) - 5} more" if len(changed) > 5 else ""
    return ", ".join(changed[:5]) + more


def agent_env(workspace: Path, env_spec: dict | None,
              source: dict | None = None) -> dict:
    """The environment the agent under test runs in.

    A fixture's `env:` mapping is applied over an allowlisted slice of the
    harness's own environment, with `$WORKSPACE` (and any other `$VAR`)
    expanded against the workspace the arm actually got — a temp dir the
    fixture cannot know in advance. That is what lets a seed put a fake
    binary on the agent's PATH (`PATH: "$WORKSPACE/bin:$PATH"`), the Class B
    "fake `gh` on the seed workspace's PATH" move DESIGN.md prescribes,
    without the seed carrying an absolute path. Values are strings; a
    non-string is stringified rather than rejected, since YAML will happily
    hand over an int.

    What the arm receives, and nothing else:

      * the names in `_ALLOWED_ENV` and the prefixes in
        `_ALLOWED_ENV_PREFIXES` that are present in `source`, forwarded
        verbatim — every other inherited variable is dropped;
      * the harness's own `WORKSPACE`, `GH_CONFIG_DIR` (inside the
        workspace) and `GH_TOKEN`/`GITHUB_TOKEN` (empty strings);
      * the fixture's own `env:` block, applied last, so a fixture that
        wants a name back can say so.

    `gh`'s token variables do not reach it from the harness's environment:
    `GH_TOKEN` and `GITHUB_TOKEN` arrive empty, and `GH_ENTERPRISE_TOKEN`,
    `GITHUB_ENTERPRISE_TOKEN` and `GH_HOST` are not on the list. A fixture's
    own `env:` block can still name anything it likes — it is applied last,
    and no fixture here names one of those.

    What IS refused, here at the function that builds the child's environment
    and by the same predicate `guidance.agent_env` uses, is a name or value
    the OS itself will not take: a name with an `=` in it reached
    `subprocess.run(env=...)` and came back as `ValueError: illegal
    environment variable name` — rc 1 and a traceback, after the arm had
    started. The allowlist decides what is INHERITED; this predicate decides
    what is spellable at all, so neither widens nor narrows the other.

    `source` is the parent environment to filter, defaulting to `os.environ`
    — a test can hand over a mapping it built rather than mutating the
    process's own environment, which is what makes "the arm received exactly
    these names" decidable without depending on the operator's shell.
    """
    guidance.check_env_block(env_spec, "the fixture's `env:`")
    parent = os.environ if source is None else source
    env = {key: value for key, value in parent.items()
           if key in _ALLOWED_ENV or key.startswith(_ALLOWED_ENV_PREFIXES)}
    env["WORKSPACE"] = str(workspace)
    for key in _BLANKED_ENV:
        env[key] = ""
    env["GH_CONFIG_DIR"] = str(workspace / _WORKSPACE_GH_CONFIG)
    for key, value in (env_spec or {}).items():
        env[str(key)] = expand(str(value), env)
    return env


# Both spellings `expand()` honours, so a fixture cannot opt out of the
# guard below by writing the braced one. `${WORKSPACE}` failed a
# `startswith("$WORKSPACE")` test, which returned as if the fixture had put
# nothing of its own on PATH.
_WORKSPACE_SPELLINGS = ("$WORKSPACE", "${WORKSPACE}")


def assert_stand_ins_on_path(workspace: Path, env: dict, env_spec: dict | None) -> None:
    """A fixture that prepends `$WORKSPACE/<dir>` to PATH must actually get it.

    The failure this catches is silent and total: the arm runs the REAL tool
    the fixture meant to fake, under bypassPermissions, and scores whatever
    that tool happened to say. Raising is the right end for it — a harness
    that cannot honour a fixture's `env:` block has no result worth writing.

    `$WORKSPACE/bin` and `${WORKSPACE}/bin` are the same fixture: `expand()`
    resolves both, so both are guarded here.
    """
    spec = str((env_spec or {}).get("PATH", ""))
    if not spec.startswith(_WORKSPACE_SPELLINGS):
        return
    wanted = Path(expand(spec.split(os.pathsep, 1)[0], dict(env)))
    got = Path(env["PATH"].split(os.pathsep)[0])
    if got != wanted:
        raise RuntimeError(f"fixture PATH resolved to {got}, expected {wanted}")
    stand_ins = ([p for p in sorted(wanted.iterdir())
                  if p.is_file() and os.access(p, os.X_OK)] if wanted.is_dir() else [])
    if not stand_ins:
        raise RuntimeError(
            f"no executable stand-in in {wanted}, which the fixture puts first "
            "on PATH: the arm would run the real tool instead")


def run_setup(workspace: Path, fixture: dict) -> dict | None:
    """Run the fixture's `setup:` command, if any, in the workspace before
    anything else touches it — before the agent, and before objective-only
    scoring of a freshly copied seed.

    Some fixtures need to build state a checked-in seed can't hold cleanly
    (nested git repositories, for instance: a bare repo and a clone of it
    committed as literal files would embed one git checkout inside another,
    which `git add -A` on the harness's own bookkeeping commit treats as a
    submodule boundary rather than plain files). `setup:` names a shell
    command, run with `cwd=workspace` and `$WORKSPACE` (plus any other
    `$VAR`) expanded the same way `env:` values are (see `agent_env`) — one
    pass, against the ALLOWLISTED environment, never `os.environ` directly
    (see `expand`'s own docstring for why that distinction matters: a
    `WORKSPACE` already set in the harness's own environment would otherwise
    win over the one this run actually got), so a fixture can write `bash
    $WORKSPACE/setup.sh` or a bare `bash setup.sh` interchangeably.

    Returns `None` when the fixture has no `setup:` (every existing fixture)
    or the command exits 0. Otherwise returns a `{"error": "setup_failed",
    "detail": ...}` dict — the same error-dict convention `run_agent` uses —
    so a failing setup script fails the arm/run with a named error and a
    captured stderr/stdout tail, never a bare traceback out of a check that
    assumed setup had already put its files in place.
    """
    timeout = fixture.get("setup_timeout_s", 60)
    guidance.check_timeout(timeout, "run_eval.run_setup(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    # `deps:` first, so a `setup:` command can use what it installed.
    deps_error = seed_prep.install_deps(
        workspace, fixture, agent_env(workspace, fixture.get("env")), timeout)
    if deps_error is not None:
        return deps_error
    setup_cmd = fixture.get("setup")
    if not setup_cmd:
        return None
    env = agent_env(workspace, fixture.get("env"))
    cmd = expand(str(setup_cmd), env)
    try:
        result = subprocess.run(["bash", "-c", cmd], cwd=workspace,
                                capture_output=True, text=True, timeout=timeout,
                                env=env)
    except subprocess.TimeoutExpired:
        return {"error": "setup_failed", "detail": f"setup timed out after {timeout}s"}
    if result.returncode != 0:
        return {"error": "setup_failed",
                "detail": result.stderr.strip() or result.stdout.strip()}
    return None


def install_skill(workspace: Path, arm: dict) -> dict | None:
    """Copy an isolation `with_skill` arm's skill into the seed workspace.

    `arm` carries `skill`, `registry` (a checkout Path), and optionally
    `layout` and `registry_name`. Returns None once installed, else an error
    dict ("invalid_skill_name", "skill_not_found", "ambiguous_skill",
    "skill_install_failed") in `run_agent`'s shape. Called by `_run_arm`
    before the agent, never from `run_agent`: an in-place arm's context is
    delivered outside the agent call (ADR 0012), and a second, name-triggered
    install there is what collided with it (`FileExistsError`).
    """
    skill = arm["skill"]
    try:
        _validate_skill_name(skill)
    except ValueError as exc:
        return {"error": "invalid_skill_name", "detail": str(exc)}
    registry = arm["registry"]
    # Registry layouts vary (acsi-agentskills' plugins/<bundle>/skills/<skill>/,
    # example-site-platform's flat skills/<skill>/, example-site-a's
    # .claude/skills/<skill>/ — see harness/registries.yml). `layout`
    # carries the glob for the registry under test, defaulting to
    # acsi-agentskills' shape for callers that predate #63. Globs for the
    # SKILL.md FILE (not the containing directory) and takes its parent,
    # so a skill directory with no SKILL.md — a stub left by a rename, a
    # bundle mid-migration — fails closed as skill_not_found instead of
    # "installing" whatever's actually in there.
    layout = arm.get("layout", "plugins/*/skills/*/SKILL.md")
    skill_md_glob = _skill_md_glob(layout, skill)
    matches = sorted(p.parent for p in registry.glob(skill_md_glob) if p.is_file())
    # The registry's NAME (arm_config's own, when _run_arm resolved it —
    # falling back to the checkout dir's basename otherwise) plus the
    # RELATIVE glob, never the resolved absolute path: this detail reaches
    # summary.json, which eval.yml commits to the public
    # persistent/eval-results branch (item 6, #129 review round 4 — the
    # same treatment select_models' own roster-path messages use).
    registry_label = arm.get("registry_name") or registry.name
    if not matches:
        return {"error": "skill_not_found",
                "detail": f"no SKILL.md matched {registry_label}/{skill_md_glob}"}
    if len(matches) > 1:
        # Two copies of one name: which one is "the skill" is not a sort
        # order's call (ADR 0012 removed "first sorted match wins").
        return {"error": "ambiguous_skill",
                "detail": f"{len(matches)} SKILL.md files matched "
                          f"{registry_label}/{skill_md_glob}; a skill name must "
                          "name one directory"}
    skill_dest = workspace / ".claude" / "skills" / skill
    try:
        shutil.copytree(matches[0], skill_dest)
    except OSError as exc:
        # FileExistsError (the destination dir already exists) and
        # NotADirectoryError (a seed shipping .claude/skills itself as a
        # regular FILE, so os.makedirs can't create skill_dest under it)
        # both land here — both are a seed/workspace layout problem, not
        # something to raise, but "already exists" is only true of the first
        # one (N1, #129 review round 6) — a generic wording that names the
        # exception type covers both honestly. The skill name, not
        # `skill_dest`'s absolute workspace path — this detail reaches
        # summary.json, which eval.yml commits to the public
        # persistent/eval-results branch.
        return {"error": "skill_install_failed",
                "detail": f"could not install {skill}/ into the seed "
                          f"workspace ({type(exc).__name__})"}
    return None


def run_agent(workspace: Path, prompt: str, arm: dict) -> dict:
    """Run the agent under test (the Claude Code CLI, headless) on the workspace.

    `arm` carries: name ("with_skill"/"without_skill"), optional model,
    optional timeout (default 600s),
    optional env (the fixture's `env:` mapping, see agent_env), optional
    followups (the fixture's `followups:` list, see ADR 0009), optional
    permission_mode (guidance.PERMISSION_MODES; default
    guidance.DEFAULT_PERMISSION_MODE, `auto`), optional effort
    (guidance.EFFORT_LEVELS; None passes no `--effort`, the CLI's default).

    This replaces the old `-> str` transcript stub with a richer dict. Success
    dicts have no "error" key and carry transcript/usage/cost_usd/num_turns/
    duration_ms/raw. Error dicts always have an "error" key — one of
    "timeout",
    "sandbox_unavailable", "workspace_read_denied", "read_rules_unsafe",
    "harness_clone_unknown", "toolchain_under_alias",
    "settings_too_large", "managed_sandbox_policy", "spawn_failed",
    "nonzero_exit", "invalid_json", "agent_error", "agent_wrote_agent_config"
    — plus a "detail". Every call runs behind
    `arm_isolation_flags(arm["read_denied"])` (ADR 0011), the run's registry
    and guidance checkouts denied to reads along with HOME and this clone. Callers
    MUST check `"error" in result` rather than relying on exceptions; only
    process invocation failures are turned into error dicts here, nothing is
    raised. Nothing is installed here: a skill or a deployed context reaches
    the workspace or profile before this call (`install_skill`,
    `_prepare_in_place`).
    """
    # S1-a-2. The predicate sits HERE, at the function that hands the value
    # to the OS, and not only at the sources a table can name. Measured on
    # f9115ce: rebinding this call site's `timeout` to an unvalidated fixture
    # key (`fixture.get("agent_timeout_s", 600)`) left the source inventory,
    # the flag pin and all 120 TestIssue97 tests green while a fixture
    # reproduced `OverflowError: timeout is too large`, rc 1.
    timeout = arm.get("timeout", 600)
    guidance.check_timeout(timeout, "run_eval.run_agent(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    # Checked at the sink too, before anything is installed or spawned: the
    # value becomes an argv element, and an unknown one is a configuration
    # error (GuidanceError, rc 2 through main), never a CLI failure scored as
    # the agent's.
    permission_mode = guidance.check_permission_mode(
        arm.get("permission_mode", guidance.DEFAULT_PERMISSION_MODE))
    effort = guidance.check_effort(arm.get("effort"))
    # The arm must be able to read its own workspace: one under a denied path
    # (TMPDIR inside HOME, say) is refused, never silently re-opened.
    checkouts = tuple(arm.get("read_denied") or ())
    outputs = tuple(arm.get("read_denied_outputs") or ())
    # The mount table is read once, here, and handed to everything below.
    try:
        mountinfo = host_mountinfo()
        denied = arm_read_denied(checkouts, outputs=outputs, mountinfo=mountinfo)
        check_workspace_readable(workspace, denied)
    except ArmReadIsolationError as exc:
        return {"error": exc.code, "detail": str(exc)}
    refusal = managed_sandbox_refusal()
    if refusal is not None:
        return {"error": "managed_sandbox_policy", "detail": refusal}
    # `setting_sources` and `env_override` are the guidance subject's two
    # seams (#97): guidance is delivered into USER memory by the fleet hook,
    # so a guidance arm is invoked with `user,project` and with the scrubbed
    # allowlist environment its arm built. Both default to exactly what skill
    # arms have always had — `project`, and this harness's own environment
    # with the fixture's `env:` applied — so skill fixtures are byte-identical
    # across this change.
    # Auto-memory off, applied last (guidance.CLI_FORCED_ENV): a skill arm
    # runs under the real HOME, so its memory would land in the operator's
    # profile.
    env = guidance.cli_child_env(arm.get("env_override")
                                 or agent_env(workspace, arm.get("env")))
    for name in ARM_DROPPED_ENV:
        env.pop(name, None)
    # Every host-filesystem alias is denied whole, so a PATH entry under one
    # (WSL appends the Windows PATH under /mnt/c) is dropped from the arm's
    # environment, a skill arm's and a guidance arm's `env_override` alike;
    # a required tool found only there fails the arm (`arm_path_or_error`).
    aliases = [path for label, path in denied if label == MOUNT_ALIAS_LABEL]
    if "PATH" in env:
        try:
            env["PATH"] = arm_path_or_error(env["PATH"], aliases)
        except ArmReadIsolationError as exc:
            return {"error": exc.code, "detail": str(exc)}
    # Where this arm's CLI saves its session, which the read rules leave open
    # when it lies under the real HOME or a profile (`_profile_read_rules`).
    projects = _session_projects_dir(env)
    session_dir = projects / _munged_project_name(workspace) if projects else None
    # The profile the session loads as user settings: the arm's own
    # `CLAUDE_CONFIG_DIR`, else `$HOME/.claude` of the child.
    config_dir = (Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR")
                  else Path(env["HOME"]) / ".claude" if env.get("HOME") else None)
    # Watched after every turn: a scratch profile (a guidance arm's) whole; a
    # shared one (the operator's `~/.claude`, or an inherited
    # `CLAUDE_CONFIG_DIR`) only where the CLI loads configuration from, since
    # other sessions write the rest (`_SHARED_PROFILE_CONFIG`).
    shared_profile = not (env.get("CLAUDE_CONFIG_DIR") and env.get("CLAUDE_CONFIG_DIR")
                          != os.environ.get("CLAUDE_CONFIG_DIR"))
    try:
        # Capture ambient roots through the settings builder's defaults, and
        # add the child scratch roots rather than replacing that protection.
        scratch_profiles = ([Path(env["HOME"])] if env.get("HOME") else []) if arm.get("env_override") else []
        if arm.get("env_override") and config_dir is not None:
            scratch_profiles.append(config_dir)
        if arm.get("pair_parent"):
            parent = Path(arm["pair_parent"]).resolve()
            if (config_dir is None or not _within(config_dir.resolve(), parent)
                    or _within(workspace.resolve(), parent)):
                raise ArmReadIsolationError("pair profile reservation overlaps the workspace",
                                            code="read_rules_unsafe")
            scratch_profiles.append(parent)
        readable_plugins = []
        for supplied in arm.get("verified_plugin_roots", ()):
            path = Path(supplied)
            # Only the frozen skill trees and the two-field plugin manifest
            # prepared by delivery are exposed, never profile registrations,
            # credentials, settings, hooks or MCP configuration. Revalidate
            # the directory boundary before emitting the read exception.
            canonical = delivery.plugins_root(config_dir) if config_dir is not None else None
            if (not arm.get("env_override") or canonical is None
                    or path.parent != canonical or path.resolve() != path
                    or not path.is_dir()):
                raise ArmReadIsolationError("unverified plugin read exception",
                                            code="read_rules_unsafe")
            for current, dirs, names in os.walk(path, followlinks=False):
                for name in dirs + names:
                    mode = os.lstat(Path(current) / name).st_mode
                    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                        raise ArmReadIsolationError("plugin tree is not regular content",
                                                    code="read_rules_unsafe")
            manifest = path / ".claude-plugin" / "plugin.json"
            if (json.loads(manifest.read_bytes()) != {"name": path.name,
                                                      "version": delivery.PLUGIN_VERSION}
                    or set(p.name for p in path.iterdir()) != {"skills", ".claude-plugin"}
                    or set(p.name for p in manifest.parent.iterdir()) != {"plugin.json"}):
                raise ArmReadIsolationError("unverified plugin configuration",
                                            code="read_rules_unsafe")
            for skill in (path / "skills").iterdir():
                delivery._tree_files(skill)
            readable_plugins.append(path)
        isolation = arm_isolation_flags(checkouts, session_dir, workspace, config_dir,
                                        outputs, additional_profiles=scratch_profiles,
                                        readable_plugins=readable_plugins,
                                        path_env=env.get("PATH", ""), mountinfo=mountinfo)
    except (ArmReadIsolationError, delivery.DeliveryError, OSError, ValueError) as exc:
        if not isinstance(exc, ArmReadIsolationError):
            return {"error": "read_rules_unsafe", "detail": "plugin read boundary could not be verified"}
        return {"error": exc.code, "detail": str(exc)}

    # `--verbose` makes `--output-format json` print the whole message array
    # (every turn's `type: result`), not only the LAST result. An agent that
    # starts a background subagent answers, then answers again when the
    # subagent reports; without the flag the first answer is dropped and the
    # scorer sees only the follow-up text (CLI 2.1.289, probed). The flag is
    # also the CLI's own config override, so the output shape no longer
    # depends on the machine's verbose setting. `normalize_cli_result`
    # accepts both shapes.
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", prompt,
           "--output-format", "json", "--verbose",
           # One `--permission-mode` flag, never alongside
           # `--dangerously-skip-permissions` (guidance.PERMISSION_MODES).
           "--permission-mode", permission_mode,
           "--setting-sources", arm.get("setting_sources", "project"),
           # The network and read sandbox and no web tools, for every arm and
           # every follow-up turn (`turn_cmd` keeps everything after the prompt).
           *isolation,
           # The account's claude.ai MCP connectors (mail, drive, GitHub)
           # load even under `--setting-sources project` (CLI 2.1.289,
           # measured); strict means only `--mcp-config` servers, of which
           # the harness passes none.
           "--strict-mcp-config"]
    # No transcript under the real HOME for a one-turn arm. A follow-up turn
    # `--resume`s the session, and a non-persisted session cannot be resumed
    # ("No conversation found", measured), so a multi-turn arm persists and
    # `_archive_session_dir` moves what it wrote out of the profile.
    if not arm.get("followups"):
        cmd.append("--no-session-persistence")
    if arm.get("model"):
        cmd += ["--model", arm["model"]]
    # After the prompt like every flag, so a follow-up turn, which reuses
    # this command with only the prompt replaced, keeps it.
    if effort is not None:
        cmd += ["--effort", effort]

    # `followups:` (ADR 0009): each entry is one more user turn in the SAME
    # session and workspace — the first call's flags plus `--resume
    # <session_id>`, the text in the prompt's place. Every turn gets the
    # whole `timeout`. The first turn's error details carry no label, so a
    # fixture without `followups:` behaves exactly as before.
    texts = [prompt, *(arm.get("followups") or [])]
    preexisting = session_dir is not None and os.path.lexists(session_dir)
    # Every call's tool events (#89), for `transcripts/tool_trace.json`. The
    # scorers never see them: they are attached to the returned dict under
    # `tool_trace` only, beside `raw`. Every attempted CLI call gets a slot,
    # even when its evidence is unavailable or it observed no tool calls.
    secrets = secret_values(env, dict(os.environ))
    calls: list[list[dict]] = []
    trace_complete = True
    # Each call's times and meter events (#370), for the summary's `usage`.
    clocked: list[dict] = []

    def traced(answer: dict) -> dict:
        for call in clocked:
            call.setdefault("ended", _now())
            call["outcome"] = "ok"
        if clocked:
            clocked[-1]["outcome"] = answer.get("error", "ok")
            _TELEMETRY.record("agent", clocked, [answer.get("raw")])
        if calls:
            answer["tool_trace"] = bounded_tool_trace(calls, complete=trace_complete)
        return answer

    # The finally covers every return below: a multi-turn arm's transcript
    # leaves the profile whether the arm succeeded or not.
    try:
        turns: list[dict] = []
        # What the harness installed (a with_skill arm's skill), before the
        # agent's first turn.
        config_before = _agent_config_snapshot(workspace, config_dir, shared_profile)
        for index, text in enumerate(texts):
            label = f"follow-up {index} of {len(texts) - 1}: " if index else ""
            turn_cmd = cmd
            if index:
                session_id = turns[-1].get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    return traced({"error": "invalid_json",
                                   "detail": f"{label}the previous result has no "
                                             "session_id to resume"})
                turn_cmd = [*cmd[:2], text, *cmd[3:], "--resume", session_id]
            calls.append([])
            clocked.append({"started": _now()})
            try:
                result = subprocess.run(turn_cmd, cwd=workspace, capture_output=True,
                                        text=True, timeout=timeout, env=env)
                clocked[-1]["ended"] = _now()
            except OSError as exc:
                # E2BIG and the like: the CLI never started.
                trace_complete = False
                return traced({"error": "spawn_failed",
                               "detail": f"{label}the CLI could not be started "
                                         f"({type(exc).__name__}, errno {exc.errno})"})
            except subprocess.TimeoutExpired:
                trace_complete = False
                return traced({"error": "timeout",
                               "detail": f"{label}agent timed out after {timeout}s"})

            if result.returncode != 0:
                unavailable = _sandbox_unavailable_detail(result.stderr)
                if unavailable is not None:
                    # Fail closed and say why: the arm never ran, with or
                    # without a network.
                    trace_complete = False
                    return traced({"error": "sandbox_unavailable",
                                   "detail": label + unavailable,
                                   "returncode": result.returncode})
                try:
                    decoded = json.loads(result.stdout)
                    calls[-1] = tool_events(decoded, secrets)
                    clocked[-1]["events"] = rate_limit_events(decoded)
                    normalize_cli_result(decoded)
                    trace_complete = trace_complete and isinstance(decoded, list)
                except ValueError:
                    trace_complete = False
                return traced({"error": "nonzero_exit",
                               "detail": label + failed_run_detail(result.stdout,
                                                                   result.stderr),
                               "returncode": result.returncode})

            unavailable = _sandbox_unavailable_detail(result.stderr)
            if unavailable is not None:
                # Exit 0 with the CLI's own "Sandbox disabled" warning: the
                # commands ran unconfined, so nothing they did can score.
                trace_complete = False
                return traced({"error": "sandbox_unavailable",
                               "detail": label + unavailable,
                               "returncode": result.returncode})
            try:
                decoded = json.loads(result.stdout)
                calls[-1] = tool_events(decoded, secrets)
                clocked[-1]["events"] = rate_limit_events(decoded)
                data = normalize_cli_result(decoded)
                trace_complete = trace_complete and isinstance(decoded, list)
            except json.JSONDecodeError as e:
                trace_complete = False
                # Shape only. The array form starts with the `system/init`
                # message (cwd, tool and connector names), and this detail
                # reaches summary.json, so no slice of stdout is echoed.
                return traced({"error": "invalid_json",
                               "detail": f"{label}stdout is not valid JSON ({e.msg} at "
                                         f"character {e.pos} of {len(result.stdout)})"})
            except ValueError as e:
                trace_complete = False
                return traced({"error": "invalid_json", "detail": f"{label}{e}"})

            if data.get("is_error"):
                detail = data.get("result", "")
                return traced({"error": "agent_error",
                               "detail": f"{label}{detail}" if label else detail,
                               "raw": data})
            turns.append(data)
            # After EVERY turn: the agent's own configuration under `.claude/`
            # (blocked by the settings above) is not the arm the fixture
            # describes, and project settings reload mid-session.
            written = _agent_config_written(workspace, config_before, config_dir,
                                            shared_profile)
            if written:
                return traced({"error": "agent_wrote_agent_config",
                               "detail": f"{label}the agent wrote {written}",
                               "raw": data})

        if len(turns) > 1:
            return traced(_combine_turns(turns, texts[1:]))
        data = turns[0]
        return traced({
            "transcript": data.get("result"),
            "usage": data.get("usage"),
            "cost_usd": data.get("total_cost_usd"),
            "num_turns": data.get("num_turns"),
            "duration_ms": data.get("duration_ms"),
            "raw": data,
        })
    finally:
        if session_dir is not None and not preexisting:
            _archive_session_dir(session_dir, arm.get("session_scratch"))


# The CLI's own project-directory name (2.1.289, read from its bundle and
# matched by the `memory_paths` its init event reports): every character
# outside [A-Za-z0-9] becomes "-". Past 200 characters the CLI truncates and
# appends a hash of its own, which this harness does not reproduce, so a name
# that long is never touched.
_PROJECT_NAME_MAX = 200


def _munged_project_name(workspace: Path) -> str:
    """The `~/.claude/projects/<name>` the CLI keys a session started in
    `workspace` by. The real path: the child's cwd resolves symlinks."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(workspace))


def _session_projects_dir(env: dict) -> Path | None:
    """Where the child's CLI writes session transcripts: `$CLAUDE_CONFIG_DIR/
    projects`, else `$HOME/.claude/projects`, read from the CHILD's env."""
    config = env.get("CLAUDE_CONFIG_DIR")
    if config:
        return Path(config) / "projects"
    home = env.get("HOME")
    return Path(home) / ".claude" / "projects" if home else None


def session_archive_dir() -> Path:
    """The harness-owned archive for transcripts a multi-turn arm had to
    persist: `$XDG_STATE_HOME/skills-evals/sessions`, else
    `~/.local/state/skills-evals/sessions`. Outside every checkout, so a
    transcript (host paths, fixture data) never rides into a results tree."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "skills-evals" / "sessions"


def _archive_session_dir(session_dir: Path, scratch=None) -> Path | None:
    """Move the project directory THIS arm's CLI created out of the profile.

    The caller only asks for a directory that did not exist before the arm's
    first turn, so nothing another session wrote is touched. Left alone: a
    name past the CLI's truncation length (the CLI would have written a
    different, hashed name), a symlink or non-directory, and a directory
    inside the arm's own `scratch` (the guidance arm's scratch config dir,
    which the arm removes with the rest of its scratch). Returns where it
    went, else None; an OSError is swallowed — housekeeping never fails an
    arm."""
    if len(session_dir.name) > _PROJECT_NAME_MAX:
        return None
    if session_dir.is_symlink() or not session_dir.is_dir():
        return None
    if scratch and Path(os.path.realpath(session_dir)).is_relative_to(
            os.path.realpath(scratch)):
        return None
    try:
        archive = session_archive_dir()
        archive.mkdir(parents=True, exist_ok=True)
        slot = Path(tempfile.mkdtemp(prefix=f"{session_dir.name}.", dir=archive))
        dest = slot / session_dir.name
        shutil.move(str(session_dir), str(dest))
        return dest
    except OSError:
        return None


def _sum_usage(total, part):
    """Add one turn's usage-shaped value into the running total: numbers
    add, mappings merge key by key, anything else takes the latest value."""
    def is_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if is_number(total) and is_number(part):
        return total + part
    if isinstance(total, dict) and isinstance(part, dict):
        merged = dict(total)
        for key, value in part.items():
            merged[key] = _sum_usage(merged[key], value) if key in merged else value
        return merged
    return part if part is not None else total


# What separates one turn's reply from the next in a multi-turn transcript.
# The judge reads the whole conversation, so it sees the follow-up the agent
# was answering, not two replies run together.
FOLLOWUP_MARKER = "\n\n--- user follow-up ---\n{text}\n--- agent reply ---\n"


def _combine_turns(turns: list[dict], followups: list[str]) -> dict:
    """`run_agent`'s success dict for a session of several CLI calls.

    The transcript is every reply in order with each follow-up between them.
    A resumed call's `total_cost_usd` and `modelUsage` are already CUMULATIVE
    for the session (measured on Claude Code 2.1.289: the resumed result's
    cost and per-model tokens equal the sums over both calls), so those two
    come from the LAST call; summing them would count the first call twice.
    Its `usage`, `num_turns` and `duration_ms` cover that call alone, so
    those are summed. `raw` is the last call's result object (so its
    `result` is the final reply) with those totals, and every call's own
    result object under `turns`.
    """
    transcript = turns[0].get("result") or ""
    for text, data in zip(followups, turns[1:]):
        transcript += FOLLOWUP_MARKER.format(text=text) + (data.get("result") or "")
    totals = {key: turns[-1].get(key)
              for key in ("total_cost_usd", "modelUsage")}
    for key in ("usage", "num_turns", "duration_ms"):
        value = None
        for turn in turns:
            if turn.get(key) is not None:
                value = turn[key] if value is None else _sum_usage(value, turn[key])
        totals[key] = value
    return {
        "transcript": transcript,
        "usage": totals["usage"],
        "cost_usd": totals["total_cost_usd"],
        "num_turns": totals["num_turns"],
        "duration_ms": totals["duration_ms"],
        "raw": {**turns[-1], **totals, "turns": turns},
    }


# The arm's workspace is the agent's cwd, and it can read every byte of it:
# `pwd`, `git log`, `ls -a`, `cat bin/gh`, `env`. So neither the directory
# name nor the baseline commit's identity may name this repository, this
# harness or the arm — `/tmp/skills-evals-with_skill-XXXX` and a commit
# authored by "skills-evals harness" told the agent what it was being
# measured with before it had read a single line of the seed.
WORKSPACE_PREFIX = "workspace-"
SEED_COMMIT_IDENTITY = ("ci@example.com", "ci")
SEED_COMMIT_MESSAGE = "initial commit"

# Where `materialize_workspace` records the workspace's own absolute path,
# and where a stand-in binary in `<workspace>/bin/` reads it from.
#
# Under `.git/`, deliberately, and it is the whole of the mechanism:
#   * `git status` never shows it, and no objective check in this repository
#     globs into `.git/` — both measured — so it changes no check's verdict;
#   * `cp -a` of the WHOLE workspace carries it — and it still names the
#     ORIGINAL, so a copy of the workspace records where the original does;
#   * a bare copy, or a hard link, of the binary alone never has it, so a
#     copy in some other `bin/` has nothing to read and refuses.
# The rule it replaces deduced the location from a directory NAME — a copy
# of the binary in any `.../bin/` recorded into that directory's parent,
# which put the record somewhere no check looks.
WORKSPACE_ANCHOR = ".git/workspace-root"


class SetupFailedError(RuntimeError):
    """Raised by `materialize_workspace` when the fixture's `setup:` command
    (see `run_setup`) fails. Carries the same `{"error": "setup_failed",
    "detail": ...}` dict `run_setup` returns, plus the half-built workspace
    it was raised over — `materialize_workspace` cannot simply swallow the
    error (a fixture that needed setup and didn't get it must not silently
    score a workspace that never had it), and the caller still owns cleanup
    of the temp dir, since the exception fires before `materialize_workspace`
    returns one.
    """

    def __init__(self, workspace: Path, detail: dict):
        self.workspace = workspace
        self.detail = detail
        super().__init__(detail.get("detail", ""))


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Harness Git always crosses the private-metadata boundary.

    Bookkeeping (seed commit, post-arm add/diff) had no timeout before #343
    and its cost grows with the workspace, so a fixed small bound would fail
    honest large trees. It gets the sink ceiling instead: a hang guard, not a
    speed bound. Expiry is the named `workspace_git_collection_failed`.
    """
    return workspace_git.run(*args, cwd=cwd, check=True,
                             timeout=guidance.MAX_TIMEOUT_S)


def materialize_workspace(seed: Path, fixture: dict | None = None,
                          workspace: Path | None = None) -> Path:
    """A fresh arm workspace: the seed copied in, under a baseline git commit.

    Extracted from `_run_arm` so a test can build the workspace the arm
    actually gets rather than a hand-rolled lookalike — what an agent can
    read in here is a property of THIS function, and a copy in a test would
    drift away from it silently.

    `fixture` is optional and defaults to `None`: every existing caller that
    only needs the plain committed-and-anchored workspace (no `setup:`) can
    keep calling `materialize_workspace(seed)` unchanged. When a fixture IS
    given and carries a `setup:` command, `run_setup` runs it BEFORE the
    baseline commit — a setup script (e.g. disarm-inherited-reach's) builds
    real git repositories in the workspace and deletes its own machinery as
    its last step, and committing first would let that machinery survive
    into the "seed" commit even though it no longer exists on disk (see
    `run_setup`'s own docstring). A failing setup raises `SetupFailedError`
    rather than returning it, since this function's only other return shape
    is a ready-to-use `Path` with nothing to attach an error to.

    `workspace`, when given, is a directory that does not exist yet inside an
    in-place arm's scratch (ADR 0012), next to its scratch HOME and profile;
    a missing seed leaves it empty, as a guidance arm's always was.
    """
    given = workspace is not None
    if given:
        workspace = Path(workspace)
        workspace.mkdir()
    else:
        workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX))
    try:
        if not given or seed.is_dir():
            shutil.copytree(seed, workspace, dirs_exist_ok=True)
        if fixture is not None:
            # `strip_agent_context:` before setup; its guard after, on the
            # workspace exactly as the agent gets it (harness/seed_prep.py).
            seed_prep.prepare_seed(workspace, fixture)
            setup_result = (run_setup(workspace, fixture)
                            or seed_prep.seed_guard(workspace, fixture))
            if setup_result is not None:
                raise SetupFailedError(workspace, setup_result)
        _git("init", "-q", cwd=workspace)
        _git("add", "-A", cwd=workspace)
        _git("commit", "-q", "-m", SEED_COMMIT_MESSAGE, cwd=workspace)
        # After the baseline commit, so the anchor is never part of it. A
        # stand-in in `<workspace>/bin/` reads this to find where its
        # invocation log goes; without it, it refuses to serve or record
        # anything at all.
        anchor = workspace / WORKSPACE_ANCHOR
        anchor.parent.mkdir(parents=True, exist_ok=True)
        anchor.write_text(f"{workspace}\n", encoding="utf-8")
        workspace_git.seal(workspace)
    except SetupFailedError:
        # The caller still owns cleanup here (see the class docstring) --
        # `_run_arm`'s handler needs the half-built workspace to inspect.
        raise
    except Exception:
        # Anything else (a failed `_git` call, a `copytree` I/O error, ...)
        # means this function is not handing the workspace back to anyone,
        # so it must not leak the `mkdtemp` directory it created.
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    return workspace


def _nested_repo_dirs(workspace: Path) -> list[Path]:
    """Every directory anywhere under `workspace` (any depth, not just the
    top level — a copy at `$WORKSPACE/scratch/throwaway` is just as
    gitlink-collapsed as one directly under the workspace) that is itself a
    git repository (standalone, or a linked worktree) — the workspace's own
    bookkeeping repo (see `_run_arm`) collapses each to a single gitlink
    line in `git diff`, hiding exactly what changed inside from the judge.
    `.git` and `.claude` directories are pruned from the walk wherever they
    appear (not just at the top), and a nested repo's own working tree is
    not walked into any further once found — its last commit is what
    `_nested_repo_diff` shows, not a search for repos nested inside it. A
    bare repository (e.g. a fixture's `prod.git`) has no nested `.git`
    marker of its own — the directory IS the git dir — so it is never
    picked up here; its raw internals (objects, hooks/*.sample) are
    excluded from the bookkeeping repo entirely instead (see
    `evals/disarm-inherited-reach/seed/setup.sh`'s `.git/info/exclude`
    entry) since they are not a working tree to show a patch for.

    A directory that is ITSELF a bare repository (or any other git-dir
    shape `objective._looks_like_a_git_dir` recognizes) is pruned from the
    walk too, round 3 N4: its internals (`objects/`, `refs/`, `hooks/`) can
    never legitimately contain a nested working tree's own `.git` marker,
    so descending into them is pure waste — and, for a real object store,
    a walk of thousands of loose-object subdirectories for nothing.
    """
    out = []
    for root, dirs, _files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in (".git", ".claude")]
        root_path = Path(root)
        if root_path == workspace:
            continue
        if (root_path / ".git").exists():
            out.append(root_path)
            dirs[:] = []
        elif objective._looks_like_a_git_dir(root):
            dirs[:] = []
    return sorted(out)


def _nested_repo_diff(workspace: Path, dirs: list[Path]) -> str:
    """The last-commit patch for each nested repo dir in `dirs`, labelled by
    its path relative to `workspace` — what a script running inside a
    gitlink-collapsed copy actually did, which the workspace's own `git
    diff` cannot show (a gitlink is a single line: the commit SHA it now
    points at, not a patch).
    """
    guidance.check_timeout(GIT_TIMEOUT_S, "run_eval._nested_repo_diff(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    sections = []
    for d in dirs:
        rel = d.relative_to(workspace)
        # A linked worktree is reported structurally, never followed into its
        # parent repository's metadata. Standalone nested repos use the same
        # trusted Git boundary as the workspace root.
        if (d / ".git").is_file() and not (d / ".git").is_symlink():
            sections.append(f"=== {rel} (linked Git metadata; patch unavailable) ===")
            continue
        log = workspace_git.run("log", "--stat", "-p", "-1", "--format=%H %s",
                                cwd=d, timeout=GIT_TIMEOUT_S)
        if log.returncode != 0 or not log.stdout.strip():
            sections.append(f"=== {rel} (no commits) ===")
        else:
            sections.append(f"=== {rel}: last commit ===\n{log.stdout}")
    return "\n\n".join(sections)


_DIFF_HEADER_RE = re.compile(r"^diff --git a/.* b/(.*)$")


def _summarize_binary_blobs(workspace: Path, diff: str) -> str:
    """Replace each changed file's diff body with a short `Binary file
    <path> (<n> bytes)` summary when the file's actual on-disk bytes are
    not valid UTF-8 (round 3 N6) — decided directly against the real file,
    not trusted to git's own per-diff binary-vs-text decision.

    `git diff` prints "Binary files ... differ" for most binary content,
    but its own detection (a NUL byte within a sampled prefix) can miss a
    genuinely binary file too small to reliably contain one by chance — a
    git loose object is a zlib-compressed stream of a few dozen bytes for
    a small commit, and it is exactly as likely to be free of 0x00 as any
    other byte value. Left classified as "text", its raw non-UTF-8 bytes
    get embedded straight into the diff; decoded with `errors="replace"`
    (see `_git`, called with `text=True`), those become a wall of U+FFFD
    replacement characters — unreadable, and mostly useless, judge input.
    Deciding by UTF-8-decodability instead targets the actual failure mode
    (the lossy text decode) rather than approximating git's own heuristic.
    The scenario this closes: an agent leaves a bare clone in the
    workspace (`git clone --bare checkout mirror`), and its loose objects
    (a small repo has no packs at all) are added to the bookkeeping diff
    as plain new files.
    """
    if "diff --git " not in diff:
        return diff
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    out = []
    for chunk in chunks:
        header, _, _ = chunk.partition("\n")
        m = _DIFF_HEADER_RE.match(header)
        if not m:
            out.append(chunk)
            continue
        path = m.group(1)
        try:
            data = (workspace / path).read_bytes()
        except OSError:
            out.append(chunk)
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            out.append(f"{header}\nBinary file {path} ({len(data)} bytes)\n")
        else:
            out.append(chunk)
    return "".join(out)


def _build_judge_diff(workspace: Path) -> str:
    """The text handed to the judge as "what changed": the workspace's own
    bookkeeping diff, plus — for any top-level directory that is itself a
    git repository and therefore collapsed to a single gitlink line in that
    diff — its own last commit, so the judge can see what actually happened
    inside a scratch copy the agent made rather than just a gitlink SHA
    changing.
    """
    _git("add", "-A", cwd=workspace)
    diff = _git("diff", "--cached", "--", ".", ":!.claude", cwd=workspace).stdout
    diff = _summarize_binary_blobs(workspace, diff)
    nested = _nested_repo_diff(workspace, _nested_repo_dirs(workspace))
    if nested:
        diff = f"{diff}\n\n# Nested repository contents (collapsed to gitlinks above)\n{nested}"
    return diff


# ---------------------------------------------------------------------------
# What ran (#202). The owner's decision of 2026-09-27 unpinned the harness —
# CI now always installs the npm latest (#203, 2026-09-28) — so every run
# records the version it actually got, and the model(s) that actually served
# each arm, rather than leaving either to a pin or a flag.
# ---------------------------------------------------------------------------

HARNESS_NAME = "claude-code"


def _permission_mode(args: argparse.Namespace) -> str:
    """The run's `--permission-mode`. A caller that built its own Namespace
    without one gets the default — the mode run_agent and the judge then
    launch with, so the summary records what actually ran."""
    return getattr(args, "permission_mode", None) or guidance.DEFAULT_PERMISSION_MODE


def _effort(args: argparse.Namespace, fixture: dict) -> str | None:
    """The agent arms' `--effort`: the run's `--effort` > the fixture's
    `effort:` > None, the CLI's own default (no flag passed). The same
    precedence as `--model` over `model:`."""
    return getattr(args, "effort", None) or (
        fixture.get("effort") if isinstance(fixture, dict) else None)

# The bound on the `--version` probe. Named rather than inlined so the sink
# check and the `timeout=` argument are provably the same value.
VERSION_TIMEOUT_S = 30
#: A version string longer than this is cut: it is the CLI's own output,
#: recorded into a summary.json the public persistent/eval-results branch carries.
VERSION_MAX_CHARS = 64
#: Every character a version string needs — the same set the workflows'
#: install step keeps (`${version//[^A-Za-z0-9._() -]/}`). Anything else is
#: dropped: the version lands in a markdown code span in report.md, where a
#: backtick or a link would be rendered, not recorded.
_VERSION_JUNK = re.compile(r"[^A-Za-z0-9._() -]")
#: `models_used` validation: model ids are short strings, and a result is
#: served by a handful of them. Anything else is junk and is ignored.
MODEL_ID_MAX_CHARS = 128
MODELS_USED_MAX = 16


def claude_version() -> str | None:
    """The first line of `<CLAUDE_BIN or claude> --version`, reduced to
    version characters, stripped, capped at VERSION_MAX_CHARS — or None, with
    a one-line warning on stderr naming only an exit status or an exception
    class, never the CLI's output.

    Never raises for the CLI's sake: a run whose version could not be read
    still runs and records `null`. (The timeout predicate raises before the
    spawn, like every sink's, and that is a configuration error.)
    """
    guidance.check_timeout(VERSION_TIMEOUT_S, "run_eval.claude_version(timeout=)",
                           guidance.SINK_TIMEOUT_REMEDY)
    why = None
    try:
        # `subprocess.run(timeout=)` already does what the workflows' `timeout
        # -k 10 60` does (#203 round 1): on expiry it SIGKILLs the child
        # (`process.kill()` in its TimeoutExpired handler) and reaps it, so a
        # CLI that ignores SIGTERM cannot outlive the probe.
        result = subprocess.run(
            [os.environ.get("CLAUDE_BIN", "claude"), "--version"],
            capture_output=True, text=True, timeout=VERSION_TIMEOUT_S)
        if result.returncode != 0:
            why = f"exit {result.returncode}"
        else:
            # The whole stdout, first line taken here: no shell pipe, so no
            # `head` closing the pipe under the CLI.
            lines = (result.stdout or "").splitlines()
            first = _VERSION_JUNK.sub("", lines[0]) if lines else ""
            version = first.strip()[:VERSION_MAX_CHARS].strip()
            if version:
                return version
            why = "no version in its output"
    except Exception as exc:  # noqa: BLE001 — record null, never crash the run
        why = type(exc).__name__
    print(f"warning: harness: could not read the Claude Code version ({why}); "
          "recording null", file=sys.stderr)
    return None


def models_used(*results) -> list[str]:
    """The sorted model ids that served one or more CLI results.

    Each result is the CLI's `--output-format json` object; its `modelUsage`
    is keyed by the model id that served the turns. Only string keys of
    1..MODEL_ID_MAX_CHARS printable characters count — no whitespace, and no
    backtick or pipe, which would break report.md's line — and at most
    MODELS_USED_MAX are kept; an absent or malformed `modelUsage`
    contributes nothing.
    """
    found: set[str] = set()
    for result in results:
        usage = result.get("modelUsage") if isinstance(result, dict) else None
        if not isinstance(usage, dict):
            continue
        found.update(key for key in usage if _is_model_id(key))
    return sorted(found)[:MODELS_USED_MAX]


#: The per-model counts `model_tokens` keeps from the CLI's `modelUsage`, as
#: (summary name, `modelUsage` key). The names are `usage`'s own, so a
#: per-model figure reads like the arm's total. Its other keys (thinking
#: tokens, web searches, cost, context window) are not kept; its
#: `canonicalModel` is, as `canonical_model`.
MODEL_TOKEN_FIELDS = (
    ("input_tokens", "inputTokens"),
    ("output_tokens", "outputTokens"),
    ("cache_read_input_tokens", "cacheReadInputTokens"),
    ("cache_creation_input_tokens", "cacheCreationInputTokens"),
)
#: The largest token count kept, per count and per total. A larger figure
#: is junk, not a measurement (a trial's whole budget is far below it), and
#: a sum of figures near the float range would overflow to Infinity, which
#: is not JSON. Past it a count or total is null.
MAX_TOKEN_COUNT = 10 ** 12
#: `cross_model.flagged` is set when more than this fraction of a trial's
#: tokens (the four MODEL_TOKEN_FIELDS, summed over every model) were spent
#: on models other than the arm's own `--model`: the trial's work was done
#: MOSTLY by another model (a subagent's), so its scores describe that model
#: at least as much as the arm's. A half leaves room for the CLI's own small
#: calls on other models (compaction, helpers) without flagging. A flag
#: only: no score, gate or threshold reads it.
CROSS_MODEL_SHARE_THRESHOLD = 0.5
#: A canonical model id, after `canonical_model_id` has removed the
#: spellings around it.
_CANONICAL_ID = re.compile(r"claude-[a-z0-9]+(?:[.-][a-z0-9]+)*")
#: The spellings `canonical_model_id` removes, in this order: a bracketed
#: variant suffix (`[1m]`), a Bedrock `<region>.anthropic.` prefix and
#: `-v<n>:<n>` suffix, a Vertex `@<date>` suffix, and a `-YYYYMMDD` date
#: suffix (a dated snapshot is the same model).
_ID_SUFFIX_VARIANT = re.compile(r"\[[^\]]*\]$")
_ID_PREFIX_BEDROCK = re.compile(r"^(?:[a-z]{2,4}\.)?anthropic\.")
_ID_SUFFIX_BEDROCK = re.compile(r"-v\d+(?::\d+)?$")
_ID_SUFFIX_DATE = re.compile(r"(?:-|@)\d{8}$")


def canonical_model_id(model) -> str | None:
    """The canonical id `model` names, or None when it is not a full model
    id: an alias such as `sonnet` cannot be resolved here, because the
    alias table lives in the CLI and the harness keeps no copy of it (the
    roster's probe, scripts/probe_model_defaults.py, resolves aliases per
    CI run and commits none). Unknown, never a guess."""
    if not isinstance(model, str):
        return None
    text = _ID_SUFFIX_VARIANT.sub("", model.strip())
    text = _ID_PREFIX_BEDROCK.sub("", text)
    text = _ID_SUFFIX_BEDROCK.sub("", text)
    text = _ID_SUFFIX_DATE.sub("", text)
    return text if _CANONICAL_ID.fullmatch(text) else None


def _token_count(value):
    return value if (_is_number(value) and 0 <= value <= MAX_TOKEN_COUNT) else None


def model_usage(result) -> dict:
    """The per-model token accounting of one CLI result, from `modelUsage`:
    `{"tokens", "dropped", "complete"}`.

    `tokens` is `{model id: {name: count, ..., "canonical_model": id}}` for
    every MODEL_TOKEN_FIELDS name, model ids sorted and validated as
    `models_used()` validates them, at most MODELS_USED_MAX. A count the CLI
    did not report, or reported as anything but a finite number from 0 to
    MAX_TOKEN_COUNT, is null: missing, never zero. `canonical_model` is the
    entry's own `canonicalModel` when it is a model id, else null.

    `dropped` counts the entries left out: an invalid model id, an entry
    that is not an object, or one past MODELS_USED_MAX. `complete` is true
    only for a `modelUsage` object with at least one entry and none dropped;
    an incomplete accounting is never read as a whole.

    The CLI's `modelUsage` covers "every model call made through the query
    pipeline ... main loop, Task subagents, sidechains, and internal calls
    such as compaction" (the SDK schema text bundled in Claude Code 2.1.292),
    so a subagent on another model appears under its own key. It is
    cumulative per session, so a multi-turn arm's last result already holds
    every turn (`_combine_turns`).
    """
    usage = result.get("modelUsage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return {"tokens": {}, "dropped": 0, "complete": False}
    out, dropped = {}, 0
    for key in sorted(usage, key=str):
        entry = usage[key]
        if not _is_model_id(key) or not isinstance(entry, dict) \
                or len(out) == MODELS_USED_MAX:
            dropped += 1
            continue
        out[key] = {name: _token_count(entry.get(source))
                    for name, source in MODEL_TOKEN_FIELDS}
        canonical = entry.get("canonicalModel")
        out[key]["canonical_model"] = canonical if _is_model_id(canonical) else None
    return {"tokens": out, "dropped": dropped,
            "complete": bool(out) and dropped == 0}


def model_tokens(result) -> dict:
    """`model_usage(result)["tokens"]`."""
    return model_usage(result)["tokens"]


def _entry_total(counts) -> float | None:
    """The four counts of one `model_tokens` entry summed, or None when any
    is missing or invalid or the sum is past MAX_TOKEN_COUNT."""
    if not isinstance(counts, dict):
        return None
    values = [_token_count(counts.get(name)) for name, _ in MODEL_TOKEN_FIELDS]
    if any(v is None for v in values):
        return None
    return _token_count(sum(values))


def model_usage_total(tokens: dict, complete: bool) -> float | None:
    """Every model's four counts summed: the improvement loop's tokens
    (ADR 0005, 2026-10-07 addendum). None when the accounting is incomplete,
    empty, any count is missing, or the total is past MAX_TOKEN_COUNT."""
    if not complete or not isinstance(tokens, dict) or not tokens:
        return None
    totals = [_entry_total(counts) for counts in tokens.values()]
    if any(t is None for t in totals):
        return None
    return _token_count(sum(totals))


def _cross_model_parts(tokens: dict, own: str | None,
                       complete: bool = True) -> tuple | None:
    """(tokens on other models, all tokens) of one `model_tokens` block, or
    None when it cannot be told: an incomplete accounting, an own model that
    is not a full model id, an entry whose model cannot be resolved, no
    tokens, or any count missing (a partial sum would misstate the share).
    Models are compared by `canonical_model_id`, an entry's
    `canonical_model` first."""
    own_id = canonical_model_id(own)
    if not complete or own_id is None or not isinstance(tokens, dict) \
            or not tokens:
        return None
    other = total = 0
    for model, counts in tokens.items():
        entry_total = _entry_total(counts)
        entry_id = canonical_model_id(counts.get("canonical_model") or model) \
            if isinstance(counts, dict) else None
        if entry_total is None or entry_id is None:
            return None
        total += entry_total
        if entry_id != own_id:
            other += entry_total
    if _token_count(total) is None or total <= 0:
        return None
    return other, total


def cross_model(tokens: dict, own: str | None, *, complete: bool = True,
                dropped: int = 0) -> dict:
    """A trial summary's `cross_model` block: the arm's own `model` (null when
    the arm passed no `--model`) and its `canonical_model`
    (`canonical_model_id`, null when it cannot be resolved), whether the
    accounting is `complete` and how many entries were `dropped`,
    `other_share` (the fraction of the tokens spent on other models), the
    `threshold`, and `flagged` (`other_share` above it). `other_share` and
    `flagged` are null, unknown, whenever the share cannot be told."""
    parts = _cross_model_parts(tokens, own, complete)
    share = parts[0] / parts[1] if parts else None
    return {"model": own, "canonical_model": canonical_model_id(own),
            "complete": bool(complete), "dropped": dropped,
            "other_share": share, "threshold": CROSS_MODEL_SHARE_THRESHOLD,
            "flagged": None if parts is None else _over_threshold(*parts)}


def _over_threshold(other, total) -> bool:
    """`other / total` above CROSS_MODEL_SHARE_THRESHOLD, compared exactly
    (rationals, not floats): a float share can round onto the threshold."""
    return Fraction(other) > Fraction(CROSS_MODEL_SHARE_THRESHOLD) * Fraction(total)


def _is_model_id(key) -> bool:
    return (isinstance(key, str) and 0 < len(key) <= MODEL_ID_MAX_CHARS
            and key.isprintable() and not any(c.isspace() for c in key)
            and "`" not in key and "|" not in key)


# ---------------------------------------------------------------------------
# Run metadata and usage (#370 item 1). Every summary.json carries a `run`
# block (how the run was billed, what ran it, where, and its identity) and a
# `usage` block (times, tokens and cost per role, and the account meter as
# the CLI reported it during calls the harness made anyway: no call is ever
# added to read it). A value that could not be read is null with an entry in
# its block's `reasons`, never omitted and never guessed.
# ---------------------------------------------------------------------------

RUN_SCHEMA_VERSION = USAGE_SCHEMA_VERSION = 1
#: The `run` fields a flag can set, with the values each takes.
RUN_CHOICES = {"billing": ("api", "subscription"),
               "runner": ("actions", "routine", "workstation"),
               "location": ("cloud", "local")}
#: An identifier a public summary may carry: a run id, a session id, the
#: name of whoever asserted a quiet window.
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
#: What the environment OBSERVES about a run, per field, as (variable, the
#: value it must hold or None for any identifier, the field's value or None
#: for the variable's own). Billing has no such observation, and a hosted
#: session does not say a routine started it.
RUN_ENV = {"billing": (),
           "runner": (("GITHUB_ACTIONS", "true", "actions"),),
           "location": (("CLAUDE_CODE_REMOTE_SESSION_ID", None, "cloud"),),
           "id": (("GITHUB_RUN_ID", None, None),),
           "session_id": (("CLAUDE_CODE_REMOTE_SESSION_ID", None, None),)}
USAGE_ROLES = ("agent", "judge", "guard")
USAGE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CACHE_SPLIT = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
#: Meter snapshots one trial's summary keeps: the first and the last half
#: of this many.
METER_MAX_SNAPSHOTS = 12
#: The most CLI calls one trial's summary lists; past it only the role
#: totals are kept.
USAGE_MAX_CALLS = 32
#: The most bytes one summary's `usage.measured` may take AS WRITTEN (indented,
#: at its depth in the file). The counts above bound how many entries there
#: are, not how large a snapshot's verbatim `info` is once indented, so this
#: is the bound that keeps a summary inside the ingester's 64 KiB cap: past
#: it snapshots are dropped, then the call list.
USAGE_MAX_BYTES = 32 * 1024
#: The most windows a `meter_delta` names.
METER_MAX_WINDOWS = 16
#: A CLI call's recorded outcome: `ok`, or the harness's own error name.
_OUTCOME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
#: A meter window's name, or a cost basis: a short label the CLI printed.
_CLI_LABEL = re.compile(r"[A-Za-z0-9_.-]{1,32}")
#: Why a role's figures are null even when its call succeeded.
_API_BILLED = "API-billed run: the subscription's account meter does not apply"
#: When the meter is read. Measured once (2026-10-10, CLI 2.1.296, one agent
#: call of two API requests): one event, printed as the call began. Every
#: call the harness makes (agent, judge and guard) is made with `--verbose`
#: and so is read as it begins, so a run's last snapshot is the start of its
#: last call.
_NO_RUN_END = ("no call the harness makes prints a meter event after its last "
               "call (agent, judge or guard) has started (one real call "
               "observed: one event, as the call began); reading the meter at "
               "run end would need an added call")


def _now() -> datetime:
    """The current UTC time: the one clock every `usage` time is read from."""
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else moment.strftime(USAGE_TIME_FORMAT)


def _run_name(value: str) -> str:
    """argparse `type=` for a flag whose value is recorded as an identifier."""
    if not RUN_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "expected 1 to 128 letters, digits, dots, underscores or hyphens, "
            "starting with a letter or digit")
    return value


def run_block(flags, environ, *, harness_commit: str | None,
              cli_version: str | None) -> dict:
    """A summary's `run` block. Each of RUN_ENV's fields is the run's flag
    (`--run-<field>`) when one was passed, else what the environment
    observes, else null; `source` says which (`flag`, `env:<NAME>`, null)."""
    block, source = {"schema_version": RUN_SCHEMA_VERSION}, {}
    reasons = {}
    for field, observations in RUN_ENV.items():
        value = getattr(flags, f"run_{field}", None)
        origin = None if value is None else "flag"
        for name, wanted, meaning in () if value is not None else observations:
            seen = environ.get(name) or ""
            if (seen == wanted) if wanted else RUN_NAME.fullmatch(seen):
                value, origin = meaning or seen, f"env:{name}"
                break
        block[field], source[field] = value, origin
        if value is None:
            reasons[field] = ("no flag named it and nothing in the environment "
                              "identifies it")
    if harness_commit is None:
        reasons["harness_commit"] = ("the harness checkout's HEAD could not be "
                                     "read from its git metadata")
    if cli_version is None:
        reasons["cli_version"] = ("the CLI version was not read: no CLI call "
                                  "was made, or `--version` failed")
    return {**block, "harness_commit": harness_commit,
            "cli_version": cli_version, "source": source, "reasons": reasons}


def _sum_or_null(values: list):
    """Numbers add and mappings merge key by key; a null among them makes
    the sum null, because a partial sum would read as the whole. Anything
    else is kept only when every value agrees."""
    if not values or any(v is None for v in values):
        return None
    if all(_is_number(v) for v in values):
        return _token_count(sum(values))
    if all(isinstance(v, dict) for v in values):
        return {key: _sum_or_null([v[key] for v in values if key in v])
                for key in dict.fromkeys(k for v in values for k in v)}
    return values[0] if all(v == values[0] for v in values) else None


def _role_usage(role: str, calls: list, results: list) -> dict:
    """`usage.measured.<role>` for one trial: the role's CLI calls and what
    their results say they spent. `models` is per model id, from
    `modelUsage` (cumulative per session, so an agent's follow-up calls are
    one result). `cost_usd` is the results' own total when each has one,
    else the per-model costs summed. `cache_creation` is the 5-minute and
    1-hour cache-write split, which the CLI reports for the main loop only.
    A role that made no call records zeros; one that made a call records
    null, with a reason, for whatever its results do not say."""
    block = {"calls": len(calls), "duration_ms": sum(c["duration_ms"] for c in calls),
             "num_turns": 0, "cost_usd": 0, "cost_basis": None, "models": {},
             "cache_creation": dict.fromkeys(CACHE_SPLIT, 0)}
    if not calls:
        return {**block, "reasons": {"cost_basis": f"no {role} call was made"}}
    models, basis = {}, set()
    for result in results:
        for model, counts in model_usage(result)["tokens"].items():
            entry = result["modelUsage"][model]
            label = entry.get("costBasis")
            basis.add(label if isinstance(label, str)
                      and _CLI_LABEL.fullmatch(label) else None)
            mine = {**{name: counts[name] for name, _ in MODEL_TOKEN_FIELDS},
                    "cost_usd": _token_count(entry.get("costUSD"))}
            models[model] = (_sum_or_null([models[model], mine])
                             if model in models else mine)
    whole = bool(results) and all(model_usage(r)["complete"] for r in results)
    costs = ([r.get("total_cost_usd") for r in results]
             if all("total_cost_usd" in r for r in results)
             else [m["cost_usd"] for m in models.values()] if whole else [None])
    found = [r["usage"].get("cache_creation") if isinstance(r.get("usage"), dict)
             else None for r in results]
    split = _sum_or_null([
        {name: _token_count(part.get(name)) for name in CACHE_SPLIT}
        if isinstance(part, dict) else None for part in found])
    # Whole or unknown: a result whose `modelUsage` is absent, empty or has
    # an entry that could not be read leaves `models` null (and any cost summed
    # from it), never the models that happened to be readable.
    block.update({
        "num_turns": _sum_or_null([
            _token_count(r.get("num_turns")) if isinstance(r.get("num_turns"), int)
            else None for r in results]),
        "cost_usd": _sum_or_null([_token_count(cost) for cost in costs]),
        "cost_basis": basis.pop() if len(basis) == 1 else None,
        "models": models if whole else None,
        "cache_creation": split if split and None not in split.values() else None})
    why = (f"the {role}'s CLI result does not carry it" if results
           else f"the {role} call ended without a result")
    return {**block, "reasons": {name: why for name, value in block.items()
                                 if value is None}}


def _sum_measured(trials: list) -> dict:
    """An arm aggregate's `usage.measured`: its trials' figures summed role
    by role (`_sum_or_null`), over the span from the first trial's start to
    the last one's end. The calls and the meter snapshots themselves are
    listed in the trial summaries."""
    blocks = [t["usage"]["measured"] for t in trials
              if isinstance((t.get("usage") or {}).get("measured"), dict)]
    out = {"scope": "arm", "calls": None, "meter": None,
           "started_at": min((b["started_at"] for b in blocks if b["started_at"]),
                             default=None),
           "ended_at": max((b["ended_at"] for b in blocks if b["ended_at"]),
                           default=None)}
    for role in USAGE_ROLES:
        active = [b[role] for b in blocks if b[role]["calls"]]
        if not active:
            out[role] = _role_usage(role, [], [])
            continue
        total = {name: _sum_or_null([a[name] for a in active])
                 for name in active[0] if name != "reasons"}
        out[role] = {**total, "reasons": {
            name: "not every trial recorded it; see the trial summaries"
            for name, value in total.items() if value is None}}
    listed = "listed in each trial's own summary"
    unmetered = {b["reasons"].get("meter") for b in blocks} if blocks else {None}
    out["reasons"] = {"calls": listed,
                      "meter": unmetered.pop() if unmetered == {_API_BILLED} else listed,
                      **{name: "no trial of this arm made a call"
                         for name in ("started_at", "ended_at") if out[name] is None}}
    return out


def _split_events(entries) -> tuple[list, list]:
    """(the entries without their meter events, the events) of a sink's
    entries (`judge.collecting_models`, `run_canary.collecting_results`).
    The events of every entry are concatenated in order, as one call's; the
    entries are left as they were, the first returned list holding copies
    that are exactly what the usage figures are read from."""
    events, results = [], []
    for entry in entries:
        if isinstance(entry, dict):
            events += entry.get("events") or []
            entry = {key: value for key, value in entry.items() if key != "events"}
        results.append(entry)
    return results, events


def _cap_snapshots(snapshots: list) -> list:
    """The first and the last half of `METER_MAX_SNAPSHOTS`, when there are
    more than that."""
    half = METER_MAX_SNAPSHOTS // 2
    return (snapshots if len(snapshots) <= METER_MAX_SNAPSHOTS
            else snapshots[:half] + snapshots[-half:])


def _written_bytes(measured: dict) -> int:
    """What a trial's `usage.measured` takes as written (USAGE_MAX_BYTES)."""
    return len(json.dumps({"usage": {"measured": measured}},
                          indent=2).encode("utf-8"))


def _fit_meter(measured: dict, known: list, total: int) -> None:
    """Hold `measured` to USAGE_MAX_BYTES by dropping snapshots: down to the
    first and last of the `known` ones, then to none. `total` is how many
    the trial printed, so `omitted` stays true."""
    for ends in (known[:1] + known[1:][-1:], []):
        if measured["meter"] is None or _written_bytes(measured) <= USAGE_MAX_BYTES:
            break
        measured["meter"] = {"snapshots": ends, "omitted": total - len(ends)}


def _meter_delta(snapshots: list, uncovered: dict) -> dict | None:
    """How far each window moved between the first and the last snapshot,
    for the windows both name. Account-wide, so it is this run's cost only
    if nothing else ran: labeled, never corrected. A window whose reset time
    changed in between has no delta.

    It is not the whole run either. A call (agent, judge or guard) is read as
    it begins (_NO_RUN_END), so the call that printed the last snapshot and
    every call after it are outside the delta, as is any call before the
    first snapshot. `uncovered` counts both and is carried in the block."""
    if len(snapshots) < 2:
        return None
    first, last = (s["info"].get("unifiedWindows") for s in (snapshots[0], snapshots[-1]))
    windows = {}
    for name in first if isinstance(first, dict) and isinstance(last, dict) else ():
        before, after = first[name], last.get(name)
        if len(windows) == METER_MAX_WINDOWS or not (
                _CLI_LABEL.fullmatch(name) and isinstance(before, dict)
                and isinstance(after, dict)
                and _token_count(before.get("utilization")) is not None
                and _token_count(after.get("utilization")) is not None):
            continue
        reset = before.get("resetsAt") != after.get("resetsAt")
        moved = round(after["utilization"] - before["utilization"], 6)
        windows[name] = {"from": before["utilization"], "to": after["utilization"],
                         "delta": None if reset else moved, "window_reset": reset}
    if not windows:
        return None
    return {"confounded": True, "scope": "account-wide",
            "from_at": snapshots[0]["at"], "to_at": snapshots[-1]["at"],
            "uncovered": uncovered, "windows": windows}


#: Why a trial, a judge call or a guidance arm was not started (#68): the
#: run's budget stop had been reached. `usage.budget_stop` says which limit.
BUDGET_STOP_ERROR = "budget_stop"


def _seven_day(info) -> tuple | None:
    """(utilization, resetsAt) of one meter snapshot's weekly window, or None
    when the snapshot does not carry a readable one."""
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    week = windows.get("seven_day") if isinstance(windows, dict) else None
    if not isinstance(week, dict) or _token_count(week.get("utilization")) is None:
        return None
    return week["utilization"], week.get("resetsAt")


def budget_stop_reading(readings: list, limits: dict) -> dict | None:
    """The budget stop (#68) a run's weekly-window readings call for, oldest
    first, or None. `limits` is `budget.load_budget()`'s.

    `moved` is the latest reading less the first, rounded as `_meter_delta`
    rounds a window's delta so the two agree; when the window's reset time
    changed in between, the latest reading alone, which is what was spent
    since the reset and never less than this run spent in it. The account
    limit wins when both are reached: it is the one that protects the owner's own
    work."""
    if not readings:
        return None
    (start, start_reset), (latest, latest_reset) = readings[0], readings[-1]
    moved = round(latest - start, 6) if latest_reset == start_reset else latest
    if latest >= limits["stop_when_account_above"]:
        reason, limit = "account_above", limits["stop_when_account_above"]
    elif moved >= limits["per_run_cap"]:
        reason, limit = "per_run_cap", limits["per_run_cap"]
    else:
        return None
    return {"reason": reason, "utilization": latest, "moved": moved, "limit": limit}


def _budget_detail(stop: dict, what: str) -> str:
    """A skipped piece of work's error detail: no meter value, only which
    limit, so the line reads the same whatever the account was doing."""
    return (f"{what} not started: the run reached its budget stop "
            f"({stop['reason']}); see usage.budget_stop")


class BudgetStopped(Exception):
    """A guidance arm was not started because the run reached its budget
    stop (#68). `_run_guidance` ends the run with exit 2 and no report."""

    def __init__(self, stop: dict):
        super().__init__(_budget_detail(stop, "guidance arm"))
        self.stop = stop


class _RunTelemetry:
    """One run's usage record, filled as the run goes.

    `record()` queues a role's CLI calls; the next summary written takes
    them as its own (`usage.measured`), which is exact because trials run
    one after another and each ends by writing its summary. The one
    exception is `amend()`: a judge call held back for its pair's objective
    result (#371) is made after every trial of the pair and added to the
    summary of the trial it judges, so that trial's `started_at`..`ended_at`
    spans the trials that ran in between. Every call (agent, judge and guard)
    prints a meter event as it starts (#370), and `amend()` turns a deferred
    judge call's events into snapshots as `_measure` does. A meter
    snapshot is stored once, with the trial whose call printed it. The
    run-level half (times, how far the meter moved, what else was running)
    is written into every summary as it stood at that moment, and `finish()`
    rewrites it in all of them once the run has ended, so each summary of a
    run carries the same one.
    """

    def __init__(self):
        self.begin()

    def begin(self, flags=None) -> None:
        self.flags = flags
        # The budget limits (#68): set by main() once `--budget` is read.
        # None, as for an in-process caller that never sets them, stops
        # nothing. `stop` is sticky: once reached, no later work starts.
        self.budget: dict | None = None
        self.stop: dict | None = None
        self.started, self.ended = _now(), None
        self.trial_started = None
        self.pending: list[dict] = []
        self.snapshots: list[dict] = []
        # The run's CLI calls so far, and for each snapshot how many of them
        # had started before the call that printed it.
        self.calls_seen = 0
        self.snapshot_calls: list[int] = []
        self.unreadable = 0
        self.written: list[Path] = []

    def begin_trial(self) -> None:
        """A trial starts. Calls still queued belong to a trial that wrote
        no summary, and are dropped rather than given to this one."""
        self.trial_started = _now()
        self.pending = []

    def record(self, role: str, calls: list, results=()) -> None:
        """Queue one role's CLI calls, each `{"started", "ended", "outcome",
        "events"}`, and the CLI results they produced."""
        self.pending.append({"role": role, "calls": calls, "results": [
            r for r in results if isinstance(r, dict)]})

    def timed(self, role: str, started: datetime, results=(),
              outcome: str = "ok", events=()) -> None:
        """Queue one call that began at `started` and has just returned, with
        the meter events it printed."""
        self.record(role, [{"started": started, "ended": _now(),
                            "outcome": outcome, "events": list(events)}], results)

    def run(self, cli_version: str | None) -> dict:
        return run_block(self.flags, os.environ, harness_commit=HARNESS_COMMIT,
                         cli_version=cli_version)

    def _api_billed(self) -> bool:
        return getattr(self.flags, "run_billing", None) == "api"

    def budget_stop(self) -> dict | None:
        """The run's budget stop (#68), or None while it may start more work.

        Read from every weekly-window reading the run has so far, including
        those of calls still queued for the summary being built (an agent
        call whose judge is about to be made). A run with no reading yet goes
        on: the meter is only read as a call begins. An API-billed run
        is never stopped: it has no account meter (`_API_BILLED`)."""
        if self.stop is not None or self.budget is None or self._api_billed():
            return self.stop
        infos = [s["info"] for s in self.snapshots] + [
            event["info"] for group in self.pending for call in group["calls"]
            for event in call.get("events") or ()]
        readings = [r for r in map(_seven_day, infos) if r is not None]
        self.stop = budget_stop_reading(readings, self.budget)
        return self.stop

    def _call_snapshots(self, events: list, role: str, index: int,
                        started: datetime) -> list:
        """The snapshots one call's meter events make: the first and the last
        readable one. An unreadable event is counted, not kept. The call is
        the run's latest (`calls_seen` has counted it)."""
        self.unreadable += sum(e["info"] is None for e in events)
        events = [e for e in events if e["info"] is not None]
        shots = []
        for event in events[:1] + events[1:][-1:]:
            # Only an event before the call's first timestamped message has
            # no time of its own; the call's start is the latest time known
            # to precede it.
            shots.append({
                "at": event["at"] or _stamp(started),
                "at_source": "cli_message" if event["at"] else "call_start",
                "call": {"role": role, "index": index},
                "info": event["info"]})
            self.snapshot_calls.append(self.calls_seen - 1)
        return shots

    def _measure(self) -> dict:
        """This summary's own calls and the meter snapshots they printed."""
        groups, self.pending = self.pending, []
        started, self.trial_started = self.trial_started, None
        ended = _now() if groups or started else None
        listed, shots, counts = [], [], dict.fromkeys(USAGE_ROLES, 0)
        for group in groups:
            for call in group["calls"]:
                index = counts[group["role"]]
                counts[group["role"]] += 1
                self.calls_seen += 1
                call["duration_ms"] = int((call["ended"] - call["started"])
                                          .total_seconds() * 1000)
                listed.append({"role": group["role"], "index": index,
                               "started_at": _stamp(call["started"]),
                               "ended_at": _stamp(call["ended"]),
                               "duration_ms": call["duration_ms"],
                               "outcome": call["outcome"] if _OUTCOME.fullmatch(
                                   str(call["outcome"])) else "error"})
                shots += self._call_snapshots(call.get("events") or [],
                                              group["role"], index,
                                              call["started"])
        self.snapshots += shots
        kept = _cap_snapshots(shots)
        first = started or min((c["started"] for g in groups for c in g["calls"]),
                               default=None)
        measured = {"scope": "trial", "started_at": _stamp(first),
                    "ended_at": _stamp(ended),
                    "calls": listed if len(listed) <= USAGE_MAX_CALLS else None,
                    "meter": None if self._api_billed() else {
                        "snapshots": kept, "omitted": len(shots) - len(kept)}}
        for role in USAGE_ROLES:
            own = [g for g in groups if g["role"] == role]
            measured[role] = _role_usage(role, [c for g in own for c in g["calls"]],
                                         [r for g in own for r in g["results"]])
        measured["reasons"] = {name: "no trial was attempted"
                               for name in ("started_at", "ended_at")
                               if measured[name] is None}
        if measured["meter"] is None:
            measured["reasons"]["meter"] = _API_BILLED
        # Bound what is written, not what is counted: fewer snapshots (the
        # trial's first and last, then none), then no call list.
        _fit_meter(measured, shots, len(shots))
        if _written_bytes(measured) > USAGE_MAX_BYTES:
            measured["calls"] = None
        if measured["calls"] is None:
            measured["reasons"]["calls"] = (
                f"more than {USAGE_MAX_CALLS} calls, or too large to list: "
                "only the role totals are kept")
        return measured

    def amend(self, summary: dict, role: str, started: datetime,
              results=(), outcome: str = "ok", events=()) -> None:
        """Add one call made AFTER `summary` was written to its
        `usage.measured`: a judge call held back until its pair's objective
        result was known (#371). The role made no call before it, so its
        block is that one call's. The call's meter events become snapshots
        as in `_measure` and join the summary's own (unless the run is
        API-billed and the summary has none), within the same bounds; `calls`
        keeps its bound too."""
        measured = summary["usage"]["measured"]
        ended = _now()
        self.calls_seen += 1
        call = {"started": started, "ended": ended,
                "duration_ms": int((ended - started).total_seconds() * 1000)}
        measured["ended_at"] = _stamp(ended)
        measured[role] = _role_usage(role, [call], [
            r for r in results if isinstance(r, dict)])
        new = self._call_snapshots(list(events), role, 0, started)
        self.snapshots += new
        if new and measured["meter"] is not None:
            old = measured["meter"]
            combined = old["snapshots"] + new
            total = len(old["snapshots"]) + old["omitted"] + len(new)
            kept = _cap_snapshots(combined)
            measured["meter"] = {"snapshots": kept, "omitted": total - len(kept)}
            _fit_meter(measured, combined, total)
        if measured["calls"] is not None:
            measured["calls"].append({
                "role": role, "index": 0, "started_at": _stamp(started),
                "ended_at": _stamp(ended), "duration_ms": call["duration_ms"],
                "outcome": outcome if _OUTCOME.fullmatch(str(outcome))
                else "error"})
            if (len(measured["calls"]) > USAGE_MAX_CALLS
                    or _written_bytes(measured) > USAGE_MAX_BYTES):
                measured["calls"] = None
                measured["reasons"]["calls"] = (
                    f"more than {USAGE_MAX_CALLS} calls, or too large to list: "
                    "only the role totals are kept")

    def _run_level(self) -> dict:
        """The half of `usage` every summary of the run shares."""
        reasons = {
            "concurrent_runs": "not knowable inside one run; computed at ingest",
            "limit_promotion": "not observable from the harness",
            "price_table_version": "costs are the CLI's own figures; it names "
                                   "a cost basis, not a price table version"}
        if self.ended is None:
            reasons["ended_at"] = "the run had not ended when this summary was written"
        meter = delta = None
        if self._api_billed():
            reasons["meter"] = reasons["meter_delta"] = _API_BILLED
        else:
            # The snapshots themselves are in the trial summaries, once each.
            meter = {"snapshot_count": len(self.snapshots),
                     "unreadable_events": self.unreadable,
                     "not_sampled": {"run_end": _NO_RUN_END}}
            delta = _meter_delta(self.snapshots, {
                "calls_before_first_snapshot": self.snapshot_calls[0],
                "calls_from_last_snapshot":
                    self.calls_seen - self.snapshot_calls[-1]}
                if self.snapshots else {})
            if delta is None:
                reasons["meter_delta"] = (
                    "fewer than two meter snapshots that share a window were "
                    "recorded; the meter is read once per call (agent, judge or "
                    "guard), as the call starts, so a run of one agent call and no "
                    "other has none")
        quiet = getattr(self.flags, "asserted_quiet", None)
        session = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or ""
        # Present only once the run has stopped for its budget (#68), so a
        # run that never did writes the record it always wrote; `finish()`
        # puts it in every summary of the run, written before the stop or not.
        stopped = {} if self.stop is None else {"budget_stop": dict(self.stop)}
        return {"started_at": _stamp(self.started), "ended_at": _stamp(self.ended),
                "meter": meter, "meter_delta": delta, "concurrent_runs": None,
                "other_account_activity": "asserted_quiet" if quiet else "unknown",
                "asserted_quiet_by": quiet,
                "outer_session": {
                    "id": session if RUN_NAME.fullmatch(session) else None,
                    "usage": None, "note": "not measured"},
                "limit_promotion": None, "price_table_version": None,
                "reasons": reasons, **stopped}

    def usage(self, trials: list | None) -> dict:
        """A summary's `usage` block. `trials` are an arm aggregate's trial
        summaries; None means the summary is one trial's own."""
        measured = self._measure() if trials is None else _sum_measured(trials)
        return {"schema_version": USAGE_SCHEMA_VERSION, **self._run_level(),
                "measured": measured}

    def finish(self) -> None:
        """End the run: every summary it wrote gets the run-level record as
        it finally stood. A summary that cannot be read back is left alone."""
        self.ended = self.ended or _now()
        level = self._run_level()
        for path in self.written:
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
                summary["usage"].update(level)
            except (OSError, ValueError, KeyError, AttributeError):
                continue
            path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        self.written = []

    def report_lines(self, cli_version: str | None) -> list[str]:
        """report.md's lines naming the run and what the account meter did.
        Rendering the report is the end of the run."""
        self.ended = self.ended or _now()
        run, level = self.run(cli_version), self._run_level()
        named = ", ".join(f"{field} {run[field] or 'not recorded'}"
                          for field in ("billing", "runner", "location", "id"))
        lines = [f"- Run: {named}; started {level['started_at']}, "
                 f"ended {level['ended_at']}"]
        if self.stop is not None:
            lines.append(f"- Budget stop: {self.stop['reason']} (limit "
                         f"{self.stop['limit']}, weekly meter "
                         f"{self.stop['utilization']}, moved {self.stop['moved']}); "
                         "no trial or judge call started after it")
        delta = level["meter_delta"]
        if delta is None:
            why = level["reasons"]["meter_delta"]
            return [*lines, f"- Account meter: no change recorded ({why})"]
        moved = ", ".join(f"{name} {window['from']} to {window['to']}"
                          for name, window in delta["windows"].items())
        late = delta["uncovered"]["calls_from_last_snapshot"]
        return [*lines, "- Account meter (account-wide, so anything else the "
                        "account ran is in it; read as each agent, judge or guard "
                        "call starts, "
                        f"so the {late} call(s) from the last reading on are "
                        f"not in it): {moved}, from {len(self.snapshots)} snapshots"]


_TELEMETRY = _RunTelemetry()


def _ending_the_run(run):
    """`main`, with the run's usage record begun before it and finished
    however it returns."""
    @functools.wraps(run)
    def wrapper():
        _TELEMETRY.begin()
        try:
            return run()
        finally:
            _TELEMETRY.finish()
    return wrapper


def _harness_line(harness_version: str | None, arm_summaries: list[dict]) -> str:
    """report.md's one line naming the harness and each arm's models."""
    models = "; ".join(
        f"{s['arm']}={', '.join(s.get('models_used') or []) or 'none recorded'}"
        for s in arm_summaries)
    version = f"`{harness_version}`" if harness_version else "version unknown"
    return f"- Harness: Claude Code {version}; models used: {models}"


def _write_summary(results_dir: Path, skill: str | None, arm_name: str,
                   timestamp: str, error: dict | None, agent: dict | None,
                   objective_checks: list | None, judge_result: dict | None,
                   raw: dict | None, key: str | None = None,
                   extra: dict | None = None, *,
                   harness_version: str | None = None,
                   permission_mode: str | None = None,
                   models: list | None = None,
                   tool_trace: dict | None = None,
                   judge_models: list | None = None,
                   arm_dir: Path | None = None,
                   effort: str | None = None,
                   agent_usage: dict | None = None,
                   agent_model: str | None = None,
                   usage_trials: list | None = None) -> None:
    """One arm's summary.json (+ raw transcript).

    `key` is the results-tree path for this subject — a skill's own name, or
    `guidance/<section id>` — and defaults to `skill`, which is what every
    skill arm has always written. `extra` carries the guidance subject's own
    fields (subject/section/mode/bytes/delivery/guard).

    Every summary, error paths included, records `harness` (the CLI version
    read once per run, or null, plus `permission_mode`, the mode every agent
    and judge call of the run was launched with) and `models_used` /
    `judge_models_used`
    (`models_used()` of the agent's and the judge's results; empty when that
    call never ran) — #202. The `harness` block also records `effort`, the
    `--effort` level every agent call of the arm was launched with, or null
    when none was passed (the CLI's default).

    `model_tokens` (`model_usage()` of the agent's result, passed as
    `agent_usage`; `{}` when that call never produced one) and `cross_model`
    (`cross_model()` of it and `agent_model`, the arm's own `--model`) are in
    every summary too. An aggregate summary passes `sum_model_usage()` of
    its trials.

    `run` and `usage` (#370) are in every summary too: how the run was
    billed, what ran it and where, and what this summary's own CLI calls and
    the run as a whole cost (`_RunTelemetry`). An arm aggregate passes its
    trial summaries as `usage_trials`.

    `arm_dir` (#66) names the directory to write into when it is not the
    default `<results>/<key or skill>/<timestamp>/<arm>/`: a nested fixture's
    arm directory, or one trial's `trial-<k>/` inside an arm directory. Left
    as None, the path is the one every caller before #66 got.
    """
    if arm_dir is None:
        arm_dir = results_dir / (key or skill) / timestamp / arm_name
    arm_dir.mkdir(parents=True, exist_ok=True)
    usage = agent_usage or {"tokens": {}, "dropped": 0, "complete": False}
    summary = {}
    if skill is not None:
        summary["skill"] = skill
    summary.update({
        "arm": arm_name,
        "timestamp": timestamp,
        "error": error,
        "agent": agent,
        "objective_checks": objective_checks,
        "judge": judge_result,
        "harness": {"name": HARNESS_NAME, "version": harness_version,
                    "permission_mode": permission_mode, "effort": effort},
        "models_used": list(models or []),
        "judge_models_used": list(judge_models or []),
        "model_tokens": dict(usage["tokens"]),
        "cross_model": cross_model(usage["tokens"], agent_model,
                                   complete=usage["complete"],
                                   dropped=usage["dropped"]),
    })
    if extra:
        summary.update(extra)
    if summary.get("subject") == "guidance" and summary.get("pairing") == "in_place":
        summary.setdefault("n", 1)
        if key and len(key.split("/")) == 3:
            summary.setdefault("fixture", key.split("/")[2])
    summary["run"] = _TELEMETRY.run(harness_version)
    summary["usage"] = _TELEMETRY.usage(usage_trials)
    with open(arm_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    if arm_dir / "summary.json" not in _TELEMETRY.written:
        _TELEMETRY.written.append(arm_dir / "summary.json")
    if raw is not None:
        transcripts_dir = arm_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        with open(transcripts_dir / "raw.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
    if tool_trace is not None:
        transcripts_dir = arm_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        with open(transcripts_dir / TOOL_TRACE_NAME, "w", encoding="utf-8") as f:
            json.dump(tool_trace, f, indent=2)


# How much of an error detail one report table cell carries. The full
# detail is always in summary.json; this is the reader's version.
_REPORT_CELL_CHARS = 200


def _render_report(skill: str, prompt: str, timestamp: str, arm_summaries: list[dict],
                   harness_version: str | None = None, run_lines=()) -> str:
    lines = [
        f"# Eval report: {skill}",
        "",
        f"- Prompt: {prompt.strip()}",
        f"- Timestamp: {timestamp}",
        *run_lines,
        _harness_line(harness_version, arm_summaries),
        "",
        "| Arm | Objective | Judge overall | Cost (USD) | Turns | Duration (ms) | Error |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in arm_summaries:
        checks = s.get("objective_checks")
        objective_str = f"{sum(1 for c in checks if c['passed'])}/{len(checks)}" if checks else "-"

        judge_str = _judge_cell(s.get("judge"))

        agent = s.get("agent") or {}
        cost = agent.get("cost_usd")
        cost_str = f"{cost:.4f}" if isinstance(cost, (int, float)) else "-"
        turns_str = str(agent.get("num_turns")) if agent.get("num_turns") is not None else "-"
        duration_str = str(agent.get("duration_ms")) if agent.get("duration_ms") is not None else "-"

        err_str = _error_cell(s.get("error"))

        lines.append(f"| {s['arm']} | {objective_str} | {judge_str} | {cost_str} | "
                     f"{turns_str} | {duration_str} | {err_str} |")
    lines += guidance_violations.render([
        {"arm": s["arm"], "stats": {"aggregate": {
            "guidance_violations": guidance_violations.aggregate([s])}}}
        for s in arm_summaries])
    return "\n".join(lines) + "\n"


def _error_cell(err: dict | None) -> str:
    """One error, as report text that cannot break a table or a list.

    Error details can carry multiline stderr or `|`s — keep the table
    intact. The cut is marked: an error cut off mid-sentence at exactly 200
    characters reads as the whole error, and the reader has no way to tell
    there is more of it in summary.json, which keeps the detail in full.
    """
    if not err:
        return ""
    err_str = " ".join(
        f"{err['type']}: {err['detail']}".split()).replace("|", "\\|")
    if len(err_str) > _REPORT_CELL_CHARS:
        err_str = err_str[:_REPORT_CELL_CHARS - 1] + "…"
    return err_str


# ---------------------------------------------------------------------------
# Trials (#66). One trial is one `_run_arm` call: a fresh workspace, a fresh
# agent call, the judge on that trial alone. The CLI has no temperature flag,
# so repeating the arm is the only way to see run-to-run spread (DESIGN.md,
# "Harness-wide rules").
#
# ON DISK, by the number of trials asked for:
#
#   --trials 1 (the default)   <arm>/summary.json            that trial, plus `n: 1`
#                              <arm>/transcripts/raw.json
#   --trials N, N > 1          <arm>/trial-<k>/summary.json  each trial, plus `trial: k`
#                              <arm>/trial-<k>/transcripts/raw.json
#                              <arm>/summary.json            the aggregate
#
# At N = 1 the arm summary IS the trial, field for field what this harness
# wrote before trials existed, with `n: 1` appended; no `trial-1/` copy of it
# is written. At N > 1 the arm summary describes no single trial, so the four
# single-trial fields carry what stays true of the whole arm: `error` is null
# only when NO trial errored, and `agent`, `objective_checks` and `judge` are
# null — the numbers are under `aggregate`. A reader of the single-trial
# shape therefore finds no check list and no judge result there, rather than
# one trial's standing in for the arm.
# ---------------------------------------------------------------------------

TRIAL_DIR_PREFIX = "trial-"

# A typo guard, not a statistical claim: every trial is a paid agent call and
# a paid judge call, per arm, per fixture. DESIGN.md asks for N >= 3.
MAX_TRIALS = 20


def _is_number(value) -> bool:
    """A finite real number, and not a bool. A judge's `overall`, a
    dimension's score and a cost are all read off JSON a model or a CLI
    produced; anything else there is left out of the statistics rather than
    averaged. `math.isfinite` raises on an int too large for a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _stats(values: list) -> dict | None:
    """`n`, `mean`, `min`, `max` and `sum` of a list of numbers, or None for
    an empty one — there is no mean of nothing, and a 0.0 there would read as
    a score."""
    if not values:
        return None
    total = sum(values)
    return {"n": len(values), "mean": total / len(values),
            "min": min(values), "max": max(values), "sum": total}


# The per-trial efficiency figures the harness records, as (name, path into
# the trial summary's `agent` block). `usage` is the CLI's own token block; a
# CLI that omits a key leaves that trial missing from that metric.
EFFICIENCY_METRICS = (
    ("cost_usd", ("cost_usd",)),
    ("num_turns", ("num_turns",)),
    ("duration_ms", ("duration_ms",)),
    ("input_tokens", ("usage", "input_tokens")),
    ("output_tokens", ("usage", "output_tokens")),
    ("cache_creation_input_tokens", ("usage", "cache_creation_input_tokens")),
    ("cache_read_input_tokens", ("usage", "cache_read_input_tokens")),
)
# `tool_errors` is the one metric not in a summary: it is counted from the
# trial's `transcripts/tool_trace.json` (see `_trial_tool_errors`), so adding
# it to the summary's `agent` block would change every summary this harness
# writes, which the one-trial default must not do (#66).
EFFICIENCY_NAMES = (*(name for name, _ in EFFICIENCY_METRICS), "tool_errors")


def _tool_error_count(tool_trace: dict | None) -> int | None:
    """How many tool calls of one trial ended in an error, from its tool trace.

    Counted by DISTINCT tool-use id: a result repeated under one id (a replayed
    message) is one failed call. A result with no usable id is counted as it
    stands, since there is nothing to deduplicate on.

    New traces explicitly record whether every CLI call supplied complete
    evidence. Lost evidence or a size cap makes the count None, which the
    aggregate reports as missing rather than as a silent undercount. Legacy
    trials without a trace or without the completeness field retain their
    previous interpretation.
    """
    if tool_trace is None:
        return 0
    if tool_trace.get("complete") is False or tool_trace.get("omitted_events"):
        return None
    ids, unidentified = set(), 0
    for event in tool_trace.get("events") or []:
        if (isinstance(event, dict) and event.get("kind") == "tool_result"
                and event.get("is_error") is True):
            if isinstance(event.get("id"), str):
                ids.add(event["id"])
            else:
                unidentified += 1
    return len(ids) + unidentified


def _trial_tool_errors(trial_dir: Path) -> int | None:
    """`_tool_error_count` of the trace one trial wrote under `trial_dir`.
    No trace file is a trial that made no tool call; a file that cannot be
    read as a trace is unknown."""
    path = trial_dir / "transcripts" / TOOL_TRACE_NAME
    if not path.exists():
        return 0
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _tool_error_count(trace) if isinstance(trace, dict) else None


def _metric_stats(values: list, n_trials: int) -> dict:
    """`n`, `n_missing`, `mean`, `median`, `min`, `max` and `sum` of one
    efficiency metric. `n_missing` is the trials that did not report it.
    Unlike `_stats`, an empty list still returns a block (every figure null),
    so a reader can tell "no trial reported it" from "the key is absent"."""
    if not values:
        return {"n": 0, "n_missing": n_trials, "mean": None, "median": None,
                "min": None, "max": None, "sum": None}
    total = sum(values)
    return {"n": len(values), "n_missing": n_trials - len(values),
            "mean": total / len(values), "median": statistics.median(values),
            "min": min(values), "max": max(values), "sum": total}


def efficiency_stats(trials: list[dict],
                     tool_errors: list | None = None) -> dict:
    """`_metric_stats` for every `EFFICIENCY_NAMES` entry over `trials`
    (per-trial summaries). A trial without an `agent` block, or whose value
    is absent or not a finite number, counts in `n_missing`.

    `tool_errors` is one count (or None) per trial, in trial order, from
    `_trial_tool_errors`; the summaries do not carry it. Without it every
    trial is missing for that metric. Only a trial with an `agent` block (an
    agent call that returned) can have one."""
    out = {}
    for name, path in EFFICIENCY_METRICS:
        values = []
        for trial in trials:
            value = trial.get("agent")
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if _is_number(value):
                values.append(value)
        out[name] = _metric_stats(values, len(trials))
    counted = [
        count for index, trial in enumerate(trials)
        if isinstance(trial.get("agent"), dict) and tool_errors is not None
        and index < len(tool_errors)
        and _is_number(count := tool_errors[index])]
    out["tool_errors"] = _metric_stats(counted, len(trials))
    return out


def efficiency_delta(with_stats: dict, without_stats: dict) -> dict:
    """Per metric, `with` minus `without` of the mean and of the median, from
    two `efficiency_stats` blocks. Negative means the `with` arm used less.
    A delta is null when either side has no value for the metric. This
    reports a difference; it does not say which metric matters or whether the
    difference is enough."""
    out = {}
    for name in EFFICIENCY_NAMES:
        a, b = with_stats.get(name) or {}, without_stats.get(name) or {}
        entry = {"with_n": a.get("n"), "without_n": b.get("n")}
        for figure in ("mean", "median"):
            x, y = a.get(figure), b.get(figure)
            entry[f"delta_{figure}"] = (
                x - y if _is_number(x) and _is_number(y) else None)
        out[name] = entry
    return out


def _trial_usage(trial: dict) -> tuple:
    """(model_tokens, complete) of one trial summary: (None, False) when it
    has none (its agent call produced no result, or the summary predates the
    field). `complete` is its `cross_model.complete`, false when absent."""
    tokens = trial.get("model_tokens") if isinstance(trial, dict) else None
    if not isinstance(tokens, dict) or not tokens:
        return None, False
    block = trial.get("cross_model")
    return tokens, isinstance(block, dict) and block.get("complete") is True


def _trial_count(tokens: dict, complete: bool, model: str, name: str):
    """One trial's `name` count for `model`: the recorded value; zero for a
    model a COMPLETE trial did not use; None (missing) for a model an
    incomplete trial does not show, since it may be the one dropped."""
    if model in tokens:
        counts = tokens[model]
        return _token_count(counts.get(name)) if isinstance(counts, dict) else None
    return 0 if complete else None


def _arm_model(trials: list[dict]) -> str | None:
    """The arm's own model as its trial summaries recorded it."""
    for trial in trials:
        block = trial.get("cross_model") if isinstance(trial, dict) else None
        if isinstance(block, dict) and isinstance(block.get("model"), str):
            return block["model"]
    return None


def _token_models(trials: list[dict]) -> tuple:
    """(the sorted model ids across the trials, at most MODELS_USED_MAX, and
    how many past that cap were left out)."""
    found = sorted({model for trial in trials
                    for model in (_trial_usage(trial)[0] or {})})
    return found[:MODELS_USED_MAX], max(0, len(found) - MODELS_USED_MAX)


def sum_model_usage(trials: list[dict]) -> dict:
    """`model_usage()`'s shape for an arm: per model and count, the sum over
    the trials that recorded `model_tokens`. A model a complete trial did not
    use adds zero; one an incomplete trial does not show makes that sum null,
    as does any missing count. `complete` only when EVERY trial recorded a
    complete accounting (one whose entries were all dropped, or that produced
    no result, did not) and no model was left out. An aggregate summary's
    `model_tokens` and `cross_model`."""
    measured = [(tokens, complete) for tokens, complete in map(_trial_usage, trials)
                if tokens is not None]
    models, cut = _token_models(trials)
    out = {}
    for model in models:
        out[model] = {}
        for name, _ in MODEL_TOKEN_FIELDS:
            values = [_trial_count(t, c, model, name) for t, c in measured]
            out[model][name] = (_token_count(sum(values))
                                if all(v is not None for v in values) else None)
        names = {t[model].get("canonical_model") for t, _ in measured
                 if isinstance(t.get(model), dict)}
        out[model]["canonical_model"] = names.pop() if len(names) == 1 else None
    dropped = cut + sum(
        trial["cross_model"]["dropped"] for trial in trials
        if isinstance(trial, dict) and isinstance(trial.get("cross_model"), dict)
        and isinstance(trial["cross_model"].get("dropped"), int)
        and not isinstance(trial["cross_model"]["dropped"], bool))
    # Every trial, not only those with tokens: a trial whose entries were all
    # dropped, or that produced no result, leaves the arm's total unknown.
    return {"tokens": out, "dropped": dropped,
            "complete": bool(trials) and not cut
            and all(_trial_usage(trial)[1] for trial in trials)}


def model_token_stats(trials: list[dict]) -> dict:
    """`_metric_stats` per model and per MODEL_TOKEN_FIELDS count, over every
    trial: a trial with no `model_tokens` is missing; one that recorded other
    models but not this one used zero of it when it was complete, and is
    missing when it was not."""
    out = {}
    for model in _token_models(trials)[0]:
        out[model] = {}
        for name, _ in MODEL_TOKEN_FIELDS:
            values = []
            for trial in trials:
                tokens, complete = _trial_usage(trial)
                if tokens is None:
                    continue
                value = _trial_count(tokens, complete, model, name)
                if value is not None:
                    values.append(value)
            out[model][name] = _metric_stats(values, len(trials))
    return out


def cross_model_stats(trials: list[dict]) -> dict:
    """An arm's `cross_model` aggregate: its own `model`, the `threshold`,
    `n` (trials whose share could be told), `n_unknown` (every other trial:
    an incomplete accounting, entries all dropped, no result, an unresolved
    model), `flagged_trials`, and `other_share` and `flagged` pooled over the
    `n` trials' tokens. Those two are null, unknown, when `n` is 0 or
    `n_unknown` is not."""
    own = _arm_model(trials)
    other = total = n = n_unknown = flagged = 0
    for trial in trials:
        tokens, complete = _trial_usage(trial)
        parts = _cross_model_parts(tokens or {}, own, complete)
        if parts is None:
            n_unknown += 1
            continue
        n += 1
        other, total = other + parts[0], total + parts[1]
        flagged += _over_threshold(*parts)
    known = n and not n_unknown
    return {"model": own, "threshold": CROSS_MODEL_SHARE_THRESHOLD, "n": n,
            "n_unknown": n_unknown, "flagged_trials": flagged,
            "other_share": other / total if known else None,
            "flagged": _over_threshold(other, total) if known else None}


def aggregate_trials(trials: list[dict],
                     tool_errors: list | None = None) -> dict:
    """One arm's trial summaries, reduced to the fields an aggregate carries.

    `trials` is the list of per-trial summaries in trial order (trial 1
    first), each in the shape `_write_summary` writes. `tool_errors` is the
    optional per-trial tool-error count `efficiency_stats` takes.

    EVERY TRIAL COUNTS IN `n`. A trial whose `error` is set is counted in
    `errors` and listed in `trial_errors`; it is in no score mean or pass
    rate. `scored` is `n - errors`, the number of trials the objective
    figures are over. Each statistic also carries its own `n`, because the
    three are not always over the same trials: a scored trial whose judge
    call failed is in the objective and cost figures and not in the judge's.

    The `aggregate` block:

      * `objective` — null when no trial was scored. Otherwise its own `n`
        (the scored trials), `passed` and `total` (integer counts summed
        over them), `mean_passed` and `mean_total` (those counts divided by
        that `n`), and `checks`: one entry per check id in first-seen order,
        with how many times it was scored (`n` — once per scored trial,
        unless the fixture uses one id for several checks), how many of
        those passed, and `pass_rate`.
      * `judge` — null when no scored trial has a judge result (`--no-judge`,
        or nothing was scored). When every scored trial's judge was skipped
        (`JUDGE_SKIPPED`, #371) it is that block plus `n` 0, `errors` 0,
        `overall` null and no dimensions: a skip is not a judge error.
        Otherwise `n` (results with a numeric
        `overall`), `errors` (results without one: the judge call failed or
        answered something unusable), `overall` (`_stats`, or null when
        `n` is 0) and `dimensions`: `_stats` per dimension name, matched
        trimmed and casefolded, over the results counted in `n`.
      * `cost_usd` — `_stats` over every trial's finite numeric agent cost,
        including errored trials, or null when none reported a usable cost.
      * `cost_unknown_trials` — trials without a usable agent cost. A zero
        cost is known; an absent or invalid cost is unknown, not free.
      * `efficiency` — `efficiency_stats` over the same trials as
        `cost_usd` (errored trials with an agent block included): one block
        per `EFFICIENCY_METRICS` name, each with `n`, `n_missing`, `mean`,
        `median`, `min`, `max` and `sum`. Added alongside the figures above,
        which are unchanged.
      * `model_tokens` — `model_token_stats`: the same block per model id and
        per `MODEL_TOKEN_FIELDS` count, from each trial's `model_tokens`.
      * `cross_model` — `cross_model_stats`: how many trials spent more than
        `CROSS_MODEL_SHARE_THRESHOLD` of their tokens on models other than
        the arm's own, and that share pooled over the arm.
    """
    scored = [t for t in trials if not t.get("error")]
    trial_errors = [
        {"trial": index, "type": t["error"].get("type"),
         "detail": t["error"].get("detail", "")}
        for index, t in enumerate(trials, start=1) if t.get("error")]

    objective_stats = None
    if scored:
        by_id: dict = {}
        passed = total = 0
        for trial in scored:
            for check in trial.get("objective_checks") or []:
                # Keyed by the id's repr: a fixture may write any YAML value
                # as an id, a list included, and a list is not a dict key.
                entry = by_id.setdefault(
                    repr(check["id"]), {"id": check["id"], "n": 0, "passed": 0})
                entry["n"] += 1
                total += 1
                if check.get("passed"):
                    entry["passed"] += 1
                    passed += 1
        for entry in by_id.values():
            entry["pass_rate"] = entry["passed"] / entry["n"]
        objective_stats = {
            "n": len(scored), "passed": passed, "total": total,
            "mean_passed": passed / len(scored),
            "mean_total": total / len(scored),
            "checks": list(by_id.values())}

    judge_stats = None
    judged = [t["judge"] for t in scored
              if isinstance(t.get("judge"), dict) and not _is_skipped(t["judge"])]
    if not judged and any(_is_skipped(t.get("judge")) for t in scored):
        judge_stats = {**JUDGE_SKIPPED, "n": 0, "errors": 0, "overall": None,
                       "dimensions": []}
    elif judged:
        usable = [j for j in judged
                  if "error" not in j and _is_number(j.get("overall"))]
        by_name: dict = {}
        for result in usable:
            for dim in result.get("dimensions") or []:
                if not isinstance(dim, dict) or not _is_number(dim.get("score")):
                    continue
                name = str(dim.get("name", "")).strip()
                by_name.setdefault(name.casefold(),
                                   {"name": name, "scores": []})["scores"].append(
                                       dim["score"])
        judge_stats = {
            "n": len(usable), "errors": len(judged) - len(usable),
            "overall": _stats([j["overall"] for j in usable]),
            "dimensions": [{"name": d["name"], **_stats(d["scores"])}
                           for d in by_name.values()]}

    costs = [t["agent"]["cost_usd"] for t in trials
             if isinstance(t.get("agent"), dict)
             and _is_number(t["agent"].get("cost_usd"))]

    violations = guidance_violations.aggregate(trials)
    return {"n": len(trials), "errors": len(trial_errors),
            "scored": len(scored), "trial_errors": trial_errors,
            "aggregate": {"objective": objective_stats, "judge": judge_stats,
                          "cost_usd": _stats(costs),
                          "cost_unknown_trials": len(trials) - len(costs),
                          "efficiency": efficiency_stats(trials, tool_errors),
                          "model_tokens": model_token_stats(trials),
                          "cross_model": cross_model_stats(trials),
                          **({"guidance_violations": violations} if violations is not None else {})}}


def _trials_error(stats: dict) -> dict | None:
    """The aggregate summary's `error`: null only when no trial errored.

    Deliberately not "null while some trial was scored". `error` is the field
    every reader of the single-trial shape already treats as "this arm has no
    clean score", and an arm with an errored trial has a mean over the
    survivors, which is not the same measurement as its sibling arm's.
    """
    if not stats["errors"]:
        return None
    kinds: dict = {}
    for entry in stats["trial_errors"]:
        kinds[entry["type"]] = kinds.get(entry["type"], 0) + 1
    listed = ", ".join(f"{kind} x{count}" for kind, count in kinds.items())
    tail = (f"score means are over the {stats['scored']} that did not"
            if stats["scored"] else "no trial was scored")
    return {"type": "trial_errors",
            "detail": f"{stats['errors']} of {stats['n']} trials errored "
                      f"({listed}); {tail}"}


def _fmt_mean(value: float) -> str:
    """One decimal, but an integral mean prints as an integer (`7`, not
    `7.0`) — the same rule scripts/make_badge.py prints its means by."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"


def _fmt_figure(name: str, value: float) -> str:
    """Cost to four decimals, as the cost column prints it; everything else
    (turns, milliseconds, token and error counts) to one."""
    return f"{value:.4f}" if name == "cost_usd" else _fmt_mean(value)


def _fmt_metric(name: str, block: dict | None) -> str:
    """One efficiency cell: `mean / median` of the trials that reported it,
    plus `(n of N)` when some did not. `-` when none did."""
    if not block or not block.get("n"):
        return "-"
    cell = (f"{_fmt_figure(name, block['mean'])} / "
            f"{_fmt_figure(name, block['median'])}")
    if block.get("n_missing"):
        cell += f" ({block['n']} of {block['n'] + block['n_missing']})"
    return cell


def _fmt_delta(name: str, value) -> str:
    if not _is_number(value):
        return "-"
    text = _fmt_figure(name, abs(value))
    if float(text) == 0:
        return "0"
    return ("+" if value > 0 else "-") + text


def _render_efficiency_table(arms: list[dict]) -> list[str]:
    """The efficiency table of one fixture: a row per metric, a column per
    arm (`mean / median`), and, when the arms are `with_skill` and
    `without_skill`, the with-minus-without delta of the mean and the
    median. Rows no arm reported are left out."""
    blocks = {arm["arm"]: arm["stats"]["aggregate"].get("efficiency") or {}
              for arm in arms}
    names = [name for name in EFFICIENCY_NAMES
             if any((blocks[a].get(name) or {}).get("n") for a in blocks)]
    if not names:
        return []
    paired = "with_skill" in blocks and "without_skill" in blocks
    delta = (efficiency_delta(blocks["with_skill"], blocks["without_skill"])
             if paired else {})
    header = "| Metric (mean / median) | " + " | ".join(blocks) + " |"
    rule = "| --- |" + " --- |" * len(blocks)
    if paired:
        header += " Delta mean | Delta median |"
        rule += " --- | --- |"
    lines = ["", "Efficiency per trial (a trial that did not report a metric "
             "is left out and the cell says how many did; delta is "
             "with_skill minus without_skill):" if paired else
             "Efficiency per trial (a trial that did not report a metric is "
             "left out and the cell says how many did):", "", header, rule]
    for name in names:
        row = f"| {name} | " + " | ".join(
            _fmt_metric(name, blocks[a].get(name)) for a in blocks) + " |"
        if paired:
            row += (f" {_fmt_delta(name, delta[name]['delta_mean'])} |"
                    f" {_fmt_delta(name, delta[name]['delta_median'])} |")
        lines.append(row)
    return lines


def _render_trials_report(skill: str, timestamp: str, trials: int,
                          sections: list[dict],
                          harness_version: str | None = None, run_lines=()) -> str:
    """report.md for a run with more than one trial, or with nested fixtures.

    One section per fixture, its header carrying `n`. `sections` is a list of
    `{"label", "prompt", "arms"}`, each arm `{"arm", "models_used", "stats"}`
    with `stats` as `aggregate_trials` returns it. The single-fixture,
    single-trial run of a flat fixture does not come through here: it keeps
    `_render_report`'s page, byte for byte.
    """
    lines = [f"# Eval report: {skill}", "",
             f"- Timestamp: {timestamp}",
             f"- Trials per arm: {trials}", *run_lines]
    for section in sections:
        arms = section["arms"]
        lines += ["",
                  f"## Fixture: {section['label']} (n={trials})", "",
                  f"- Prompt: {section['prompt'].strip()}",
                  _harness_line(harness_version, arms), "",
                  "| Arm | n | Errors | Objective (mean) | Judge overall "
                  "(mean, min to max) | Cost USD (mean / sum) |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for arm in arms:
            stats = arm["stats"]
            block = stats["aggregate"]
            objective_stats = block["objective"]
            if objective_stats and objective_stats["total"]:
                objective_str = (f"{_fmt_mean(objective_stats['mean_passed'])}/"
                                 f"{_fmt_mean(objective_stats['mean_total'])}")
            else:
                objective_str = "-"
            judge_stats = block["judge"]
            if not judge_stats:
                judge_str = "-"
            elif _is_skipped(judge_stats):
                judge_str = SKIPPED_CELL
            elif not judge_stats["overall"]:
                judge_str = "error"
            else:
                overall = judge_stats["overall"]
                judge_str = (f"{overall['mean']:.1f} ({overall['min']:.1f} to "
                             f"{overall['max']:.1f}, {overall['n']} judged)")
                if judge_stats["errors"]:
                    judge_str += f"; {judge_stats['errors']} judge error(s)"
            cost = block["cost_usd"]
            cost_str = (f"{cost['mean']:.4f} / {cost['sum']:.4f}"
                        if cost else "-")
            if block["cost_unknown_trials"]:
                cost_str += f"; {block['cost_unknown_trials']} unknown"
            lines.append(f"| {arm['arm']} | {stats['n']} | {stats['errors']} | "
                         f"{objective_str} | {judge_str} | {cost_str} |")

        lines += _render_efficiency_table(arms)
        lines += guidance_violations.render(arms)

        errored = [(arm["arm"], entry) for arm in arms
                   for entry in arm["stats"]["trial_errors"]]
        if errored:
            lines += ["",
                      "Errored trials are counted in n and excluded from "
                      "score means; known costs from all trials are counted:",
                      ""]
            lines += [f"- {arm_name} trial {entry['trial']}: "
                      f"{_error_cell(entry)}" for arm_name, entry in errored]

        check_ids: list = []
        for arm in arms:
            for check in (arm["stats"]["aggregate"]["objective"] or {}).get(
                    "checks", []):
                if check["id"] not in check_ids:
                    check_ids.append(check["id"])
        if check_ids:
            lines += ["",
                      "| Check (passed / scored) | "
                      + " | ".join(arm["arm"] for arm in arms) + " |",
                      "| --- |" + " --- |" * len(arms)]
            for check_id in check_ids:
                cells = []
                for arm in arms:
                    checks = (arm["stats"]["aggregate"]["objective"]
                              or {}).get("checks", [])
                    match = next((c for c in checks if c["id"] == check_id), None)
                    cells.append(f"{match['passed']}/{match['n']}"
                                 if match else "-")
                lines.append(f"| {str(check_id).replace('|', chr(92) + '|')} | "
                             + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# In-place pairs (ADR 0012 part 2)
#
# Both arms carry the deployed context `context.resolve_context` froze; the
# `with` arm holds the subject and the `without` arm does not. The context
# goes into a scratch profile through the guidance path's arm shape, so the
# ADR 0011 flags, read denies, profile write denies and post-turn snapshot
# all hold as they do for a guidance arm. Cloud-only for now.
# ---------------------------------------------------------------------------

# Semantic roles of the familiar arm names; the pairing says what they mean.
_ROLES = {"with_skill": "with", "without_skill": "without",
          "with_guidance": "with", "without_guidance": "without"}
IN_PLACE_SETTING_SOURCES = guidance.SETTING_SOURCES["user"]
IN_PLACE_GUIDANCE_ARMS = {"with_guidance": {"mode": "full"},
                          "without_guidance": {"mode": "full-minus-section"}}


class ContextRepositories(dict):
    """`OWNER/REPO` -> checkout for the resolver: the explicit
    `--context-repo` mappings, else the sibling clone of that repository's
    name beside this one (the routine's side-by-side clones). Every checkout
    handed out is remembered, so the arms can be denied reads of it."""

    def __init__(self, explicit: dict, parent: Path | None):
        super().__init__(explicit)
        self.parent = parent
        self.handed_out: list[Path] = []

    def get(self, identity, default=None):
        path = super().get(identity)
        if path is None and self.parent is not None and isinstance(identity, str) \
                and identity.count("/") == 1:
            candidate = self.parent / identity.split("/", 1)[1]
            if (candidate / ".git").exists():
                path = candidate
        if path is None:
            return default
        if Path(path) not in self.handed_out:
            self.handed_out.append(Path(path))
        return path

    def used_paths(self) -> list[Path]:
        sources = list(dict.fromkeys([*(Path(p).resolve() for p in self.values()),
                                      *(p.resolve() for p in self.handed_out)]))
        paths = list(sources)
        for source in sources:
            # This is trusted source discovery, never workspace collection:
            # linked and separate metadata must remain readable to the
            # resolver, then be fenced from the arm. Keep workspace Git's
            # rejection of those layouts intact. Use the reviewed metadata
            # reader shared with harness checkout discovery.
            try:
                out = _git_out("rev-parse", "--path-format=absolute", "--git-dir",
                               "--git-common-dir", cwd=source)
                dirs = out.splitlines() if out else []
                if len(dirs) != 2 or any(not Path(p).is_absolute() or not Path(p).is_dir() for p in dirs):
                    raise ValueError("invalid Git directory discovery")
                for directory in dirs:
                    path = Path(directory).resolve()
                    if path not in paths:
                        paths.append(path)
            except (OSError, ValueError) as exc:
                raise ArmReadIsolationError(
                    "a context checkout's Git metadata cannot be determined",
                    code="context_git_metadata_unknown") from exc
        return paths


def _check_with_budget(frozen, subject, budget: dict) -> None:
    """A subject added to `with` makes that arm the larger one; it must fit
    the fixture's budget too, which `resolve_context` checked without it."""
    sizes = dict(frozen.manifest["measurements"])
    if isinstance(subject, delivery.SkillSubject):
        deployed = next((tree for tree in frozen.skills if (tree.registry, tree.bundle, tree.skill)
                         == (subject.registry, subject.bundle, subject.skill)), None)
        sizes["skill_catalog_bytes"] += len(context._catalog(
            subject.bundle, subject.skill, subject.files["SKILL.md"]))
        sizes["skill_payload_bytes"] += sum(len(raw) for raw in subject.files.values())
        if deployed is not None:
            sizes["skill_catalog_bytes"] -= len(context._catalog(
                deployed.bundle, deployed.skill, deployed.files["SKILL.md"]))
            sizes["skill_payload_bytes"] -= sum(len(raw) for raw in deployed.files.values())
    if isinstance(subject, delivery.SectionSubject):
        sizes["guidance_bytes"] = len(delivery.guidance_payload(frozen, subject, "with"))
    for key, measured in sizes.items():
        if measured > budget[key]:
            raise context.ContextError(
                "budget_exceeded", f"the with arm's {key} measured {measured} "
                f"bytes exceeds limit {budget[key]}")


def _in_place_plan(fixture: dict, args: argparse.Namespace,
                   registries: dict, kind: str) -> dict:
    """Resolve the fixture's frozen context and its subject once, before any
    arm. Every refusal is a named configuration error: exit 2, no paid call."""
    if not isinstance(fixture.get("context"), dict):
        raise delivery.DeliveryError(
            "context_required", "an in-place pair needs the fixture's `context:` "
            "(its context repository); without one only --pairing isolation runs")
    repositories = ContextRepositories(
        getattr(args, "context_repos", None) or {},
        HARNESS_CLONE_ROOT.parent if HARNESS_CLONE_ROOT is not None else None)
    frozen = context.resolve_context(fixture["context"], repositories)
    if frozen.hook is None:
        raise delivery.DeliveryError(
            "missing_object", "the pinned guidance revision has no fleet-memory "
            "hook, and in-place guidance is delivered only through the real hook")
    candidate = getattr(args, "subject_candidate", None)
    if kind == "skill":
        bundle = getattr(args, "skill_bundle", None)
        registry = getattr(args, "skill_registry", None)
        # A fixture registry qualifies absent subjects; adopted defaults remain
        # the immutable deployed version unless the run explicitly qualifies it.
        if registry is None and fixture.get("registry") and not any(
                tree.skill == fixture["skill"] and (bundle is None or tree.bundle == bundle)
                for tree in frozen.skills):
            registry = delivery._identity({"url": fixture["registry"]})
        subject = delivery.resolve_skill_subject(
            frozen, f"{bundle}:{fixture['skill']}" if bundle else fixture["skill"],
            registries, registry=registry, candidate=candidate)
    else:
        subject = delivery.resolve_section_subject(
            frozen, fixture["section"], repositories, candidate=candidate)
    _check_with_budget(frozen, subject, fixture["context"]["budget"])
    plan = {"frozen": frozen, "subject": subject, "repositories": repositories,
            "candidate_paths": ([Path(candidate).resolve() if kind == "skill" else
                                 Path(candidate).resolve().parent] if candidate else [])}
    _check_context_summary(plan)
    # Prove every context metadata fence before any CLI/version call.
    try:
        repositories.used_paths()
    except ArmReadIsolationError as exc:
        raise delivery.DeliveryError("context_git_metadata_unknown", str(exc)) from exc
    return plan


def _check_context_summary(plan: dict) -> None:
    """Do not create evidence the trusted ingester cannot accept. Reserve half
    the 64 KiB summary cap for scores, errors and aggregate measurements."""
    record = _in_place_record(plan, "with")
    sources = record["context"]["sources"]
    subject = record["context_subject"]
    names = [bundle["name"] for source in sources for bundle in source["bundles"]]
    names += record["context"]["guidance"]["sections"]
    repositories = [record["context"]["repository"], *(source["registry"] for source in sources)]
    if subject["kind"] == "skill":
        names += [subject["bundle"], subject["skill"]]
        repositories.append(subject["registry"])
        long_extent = False
    else:
        names.append(subject["section"])
        long_extent = any(len(subject[key]) > 256 for key in ("file", "heading"))
    if (any(len(name) > 64 for name in names)
            or any(any(len(part) > 100 for part in repository.split("/")) for repository in repositories)
            or long_extent or record["arm_context"]["skills"] > 4096
            or len(sources) > 64 or any(len(source["bundles"]) > 64 for source in sources)
            or len(record["context"]["guidance"]["sections"]) > 64
            or len(json.dumps(record, indent=2).encode("utf-8")) > 32768):
        raise delivery.DeliveryError("context_summary_too_large",
                                     "frozen context exceeds bounded summary manifest limits")


def _plan_subjects(plan: dict):
    subject = plan["subject"]
    return (subject if isinstance(subject, delivery.SkillSubject) else None,
            subject if isinstance(subject, delivery.SectionSubject) else None)


def _in_place_record(plan: dict, role: str) -> dict:
    """What an in-place arm's summary records: the pairing and role, the
    context's digests (never its bytes), the subject's identity, action and
    both digests, and this arm's own delivered digests."""
    frozen = plan["frozen"]
    skill_subject, section_subject = _plan_subjects(plan)
    manifest = frozen.manifest
    proof = manifest["guidance"]
    expected = delivery.expected_skills(frozen, skill_subject, role)
    payload = delivery.guidance_payload(frozen, section_subject, role)
    return {
        "pairing": "in_place",
        "role": role,
        "context": {
            "repository": manifest["repository"], "revision": manifest["revision"],
            "digest": manifest["digest"], "lock_digest": manifest["lock"]["digest"],
            "sources": [{"registry": source["registry"], "revision": source["revision"],
                         "digest": source["digest"],
                         "bundles": [{"name": b["name"], "digest": b["digest"]}
                                     for b in source["bundles"]]}
                        for source in manifest["sources"]],
            "guidance": {"revision": proof["revision"], "digest": proof["digest"],
                         "bytes": proof["bytes"], "sections": list(proof["sections"]),
                         "hook_digest": proof["hook_digest"]},
            "measurements": dict(manifest["measurements"])},
        "context_subject": plan["subject"].record(),
        "arm_context": {"skills": len(expected),
                        "skills_digest": delivery.skills_digest(expected),
                        # #70, round 5: the skills other than the subject,
                        # the same set in both arms of one comparison.
                        "others_digest": delivery.skills_digest(
                            {name: digest for name, digest in expected.items()
                             if skill_subject is None
                             or name != f"{skill_subject.bundle}:{skill_subject.skill}"}),
                        "guidance_bytes": len(payload),
                        "guidance_digest": hashlib.sha256(payload).hexdigest(),
                        "subject_present": role == "with"},
    }


@contextmanager
def _in_place_pair(plan: dict | None):
    """Reserve one absolute profile location for a serial pair.

    Each arm gets a fresh workspace, HOME and temporary directory outside
    this protected parent. Its profile is deleted before its sibling is
    built, so equal registration paths never share delivered arm content.
    """
    if plan is None or "_pair_parent" in plan:
        yield
        return
    parent = Path(tempfile.mkdtemp(prefix=f"{ARM_WORKSPACE_PREFIX}pair-"))
    plan["_pair_parent"] = parent
    plan["_pair_config"] = parent / "config"
    try:
        yield
    finally:
        plan.pop("_pair_parent", None)
        plan.pop("_pair_config", None)
        shutil.rmtree(parent, ignore_errors=True)


def _in_place_scratch(plan: dict) -> Path:
    # The reserved profile parent is write-denied; keep writable workspaces
    # and tool temporary files outside it.
    if os.path.lexists(plan["_pair_config"]):
        raise delivery.DeliveryError("install_collision", "a previous arm's profile was not cleaned")
    return Path(tempfile.mkdtemp(prefix=f"{ARM_WORKSPACE_PREFIX}arm-"))


def _clean_in_place_config(plan: dict | None) -> None:
    if plan is not None and "_pair_config" in plan:
        shutil.rmtree(plan["_pair_config"], ignore_errors=True)


def _prepare_in_place(role: str, fixture: dict, seed: Path, plan: dict,
                      scratch: Path) -> tuple[Path, dict, dict]:
    """One in-place arm, in ADR 0012's order: copy, strip, deps/setup,
    seed_guard and the baseline commit (`materialize_workspace`), then the
    frozen context, then the subject added or removed, then the proofs.
    Returns the workspace, the arm's environment and the guidance delivery."""
    home, tmpdir = scratch / "home", scratch / "tmp"
    config = plan.get("_pair_config", scratch / "config")
    for path in (home, config, tmpdir):
        path.mkdir()
    workspace = materialize_workspace(seed, fixture, workspace=scratch / "ws")
    frozen = plan["frozen"]
    skill_subject, section_subject = _plan_subjects(plan)
    bundles = {tree.bundle for tree in frozen.skills}
    if skill_subject is not None:
        # Both arms register the subject's bundle, so the registrations are
        # the same and only the subject's directory differs.
        bundles.add(skill_subject.bundle)
    delivery.install_plugins(config, frozen.skills, bundles)
    delivered = delivery.deliver_guidance(frozen.hook, frozen.guidance, scratch=scratch,
                                          home=home, config=config)
    if skill_subject is not None:
        if skill_subject.action == "removed" and role == "without":
            delivery.remove_skill(config, skill_subject)
        elif role == "with":
            deployed = next((tree for tree in frozen.skills
                             if (tree.registry, tree.bundle, tree.skill) ==
                             (skill_subject.registry, skill_subject.bundle, skill_subject.skill)), None)
            if deployed is not None and (skill_subject.files != deployed.files
                                         or skill_subject.modes != deployed.modes):
                delivery.remove_skill(config, skill_subject)
                delivery.add_skill(config, skill_subject)
            elif skill_subject.action == "added":
                delivery.add_skill(config, skill_subject)
    payload = delivery.guidance_payload(frozen, section_subject, role)
    if payload != frozen.guidance:
        delivered = delivery.deliver_guidance(frozen.hook, payload, scratch=scratch,
                                              home=home, config=config)
    delivery.check_skills(config, workspace,
                          delivery.expected_skills(frozen, skill_subject, role),
                          skill_subject, role, home=home)
    env = guidance.agent_env(workspace=workspace, home=home, tmpdir=tmpdir,
                             config_dir=config, env_spec=fixture.get("env"))
    assert_stand_ins_on_path(workspace, env, fixture.get("env"))
    return workspace, env, delivered


# ---------------------------------------------------------------------------
# Conditional judging (#371)
#
# The judge is the expensive call, and a with/without pair whose objective
# result already decides it gains nothing from it. For a pair (`--arm both`,
# or each mode pair of a guidance run, below) no trial
# calls the judge while trials run (`_judge_hold`); each leaves the inputs of
# its call in the pair's `hold` list. Once every trial of both arms is scored,
# the arms' objective pass rates are compared exactly (`_objective_decided`):
# `with` strictly ahead records JUDGE_SKIPPED in every scored trial's summary
# and calls nothing; a tie, a loss or an undefined rate judges every scored
# trial of both arms. Never some trials of a pair and not others.
# A guidance run pairs its arms by MODE, not by name (`_guidance_judge_holds`):
# each complete pair of `GUIDANCE_JUDGE_PAIRS` (the same pairs as
# gates.GUIDANCE_PAIRS) is held and settled on its own, so a fixture that
# declares `section`/`none` and `full`/`full-minus-section` arms under any
# names skips the judge per pair; an arm in no complete pair is judged inside
# its trial.
# `--judge always`, a single-arm run and a fixture whose judge mode is not
# absolute judge each trial inside the trial, as before; `--no-judge` judges
# nothing and records null.
# ---------------------------------------------------------------------------

JUDGE_SKIPPED = {"status": "skipped", "reason": "objective-decided"}
SKIPPED_CELL = f"skipped ({JUDGE_SKIPPED['reason']})"


def _is_skipped(block) -> bool:
    return isinstance(block, dict) and block.get("status") == "skipped"


def _judge_cell(block: dict | None) -> str:
    """The judge column of one arm's row in a single-trial report."""
    jd = block or {}
    if _is_skipped(jd):
        return SKIPPED_CELL
    if "overall" in jd:
        return f"{jd['overall']:.1f}"
    return "error" if "error" in jd else "-"


def _judge_hold(args: argparse.Namespace, fixture: dict,
                arm_names: list[str]) -> list | None:
    """`[]` when this run's judging waits for its pair's objective result, else
    None: the judge runs inside each trial. A fixture whose `judge.mode` is
    anything but absolute (judged pairwise, for one) is never held."""
    if (args.no_judge or getattr(args, "judge", "auto") == "always"
            or _judge_is_pairwise(fixture)
            or sorted(str(_ROLES.get(name)) for name in arm_names)
            != ["with", "without"]):
        return None
    return []


#: The with/without pairs of a guidance run, by arm mode: gates.GUIDANCE_PAIRS,
#: which a test holds this equal to (gates is not imported by the harness).
GUIDANCE_JUDGE_PAIRS = {"in_situ": {"full": "with", "full-minus-section": "without"},
                        "alone": {"section": "with", "none": "without"}}


def _guidance_judge_holds(args: argparse.Namespace, fixture: dict,
                          arms: list[dict]) -> list[dict]:
    """One `{"with": name, "without": name, "hold": []}` per complete pair of
    `arms` (`GUIDANCE_JUDGE_PAIRS`, exactly one arm of each mode), whose judge
    calls wait on that pair's objective result; `[]` when nothing is held
    (`--no-judge`, `--judge always`, a judge mode that is not absolute, or no
    complete pair)."""
    if (args.no_judge or getattr(args, "judge", "auto") == "always"
            or _judge_is_pairwise(fixture)):
        return []
    pairs = []
    for roles in GUIDANCE_JUDGE_PAIRS.values():
        named = {role: [a["name"] for a in arms if a["mode"] == mode]
                 for mode, role in roles.items()}
        if all(len(names) == 1 for names in named.values()):
            pairs.append({"with": named["with"][0],
                          "without": named["without"][0], "hold": []})
    return pairs


def _objective_decided(with_trials: list[dict], without_trials: list[dict]) -> bool:
    """Whether the `with` arm's objective pass rate is strictly above the
    `without` arm's: checks passed over checks scored, over the trials whose
    `error` is null (so an errored trial cannot move it). Compared exactly.
    An arm with no scored check has no rate, and nothing is decided."""
    rates = []
    for trials in (with_trials, without_trials):
        checks = [check for trial in trials if not trial.get("error")
                  for check in trial.get("objective_checks") or []]
        rates.append(Fraction(sum(1 for c in checks if c.get("passed")),
                              len(checks)) if checks else None)
    return None not in rates and rates[0] > rates[1]


def _call_judge(fixture: dict, transcript: str, diff: str, model: str | None,
                permission_mode: str | None) -> tuple:
    """One judge call: (the result, the models that served it, when it began,
    the CLI results it produced, the outcome to record, the meter events it
    printed). A call that fails is `{"error": ...}`, a result and not a
    crash."""
    judge_cfg = fixture.get("judge", {})
    # Bound before the `with`, and read AFTER the try: a judge CLI call that
    # completed is recorded even when `score` then raises on its answer (#203
    # round 1).
    judge_results: list = []
    started = _now()
    try:
        with judge.collecting_models() as judge_results:
            judge_result = judge.score(
                fixture["judge_rubric"], transcript, diff, model=model,
                timeout=judge_cfg.get("timeout_s", 120),
                weights=judge_cfg.get("weights"),
                permission_mode=permission_mode)
    except guidance.GuidanceError:
        # S1-a-2. A sink's own timeout refusal is a CONFIGURATION error, not a
        # judge result: recorded as `{"error": ...}` it would score the arm
        # and exit 0/1 with the rule never named. Re-raised so main()'s rc-2
        # contract holds.
        raise
    except Exception as exc:  # noqa: BLE001 — record, never crash the run
        judge_result = {"error": str(exc)}
    judge_results, events = _split_events(judge_results)
    return (judge_result, models_used(*judge_results), started, judge_results,
            "error" if "error" in (judge_result or ()) else "ok", events)


def _judge_unless_stopped(judge_call) -> tuple:
    """(the judge's result, the models that served it) for a judge call made
    inside its trial, its usage queued for that trial's summary; or, once the
    run has reached its budget stop (#68), an error result and no call."""
    stop = _TELEMETRY.budget_stop()
    if stop is not None:
        return {"error": _budget_detail(stop, "judge call")}, []
    judge_result, judge_models, started, results, outcome, events = judge_call()
    _TELEMETRY.timed("judge", started, results, outcome, events)
    return judge_result, judge_models


def _settle_judging(hold: list, decided: bool) -> None:
    """Finish the trials whose judge call waited for their pair: each held
    summary gets JUDGE_SKIPPED when the pair is `decided`, else the call is
    made now and its usage added to that summary (`_RunTelemetry.amend`). The
    in-memory result the report is rendered from is updated alongside."""
    for entry in hold:
        summary = json.loads(entry["path"].read_text(encoding="utf-8"))
        stop = None if decided else _TELEMETRY.budget_stop()
        if decided:
            block, models = dict(JUDGE_SKIPPED), []
        elif stop is not None:
            # No call made, so nothing is added to the summary's usage.
            block, models = {"error": _budget_detail(stop, "judge call")}, []
        else:
            block, models, started, results, outcome, events = entry["call"]()
            _TELEMETRY.amend(summary, "judge", started, results, outcome, events)
        summary["judge"], summary["judge_models_used"] = block, models
        entry["path"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
        entry["result"]["judge"] = block


def _run_arm(arm_name: str, fixture: dict, seed: Path, registries: dict[str, dict],
            args: argparse.Namespace, timestamp: str,
            selection: tuple | None = None, *,
            out_dir: Path | None = None, extra: dict | None = None,
            plan: dict | None = None, hold: list | None = None) -> dict:
    """Materialize a workspace, invoke the agent, score it, write results, clean up.

    `hold` (#371) is None when the judge runs inside the trial. A list defers
    the judge call: the summary is written with `judge: null` and the list
    gets one entry (the summary's path, the call as a function of the inputs
    captured while the workspace still exists, and this result) for
    `_settle_judging`, which fills it in once the pair is scored.

    `plan` (`_in_place_plan`) makes this an in-place arm (ADR 0012): the
    workspace sits in a scratch beside its own HOME and profile, the frozen
    context is delivered there, and the subject is added or removed, all
    before the agent (`_prepare_in_place`).

    ONE TRIAL (#66). A fresh workspace, one agent call, one judge call (unless
    skipped or deferred, #371), one summary.json. `out_dir` and `extra` are how `_run_arm_trials` points that
    summary at a trial directory and labels it (`trial`, `fixture`, `n`); a
    caller that passes neither gets the path and the fields this function
    wrote before trials existed.

    `selection` is `select_judged_models()`'s answer, resolved ONCE by main() and
    passed in: the roster is one file describing one run, and re-reading it per
    arm let two arms of the same run disagree if it changed underneath them.
    A selection error is checked and recorded FIRST, before any workspace is
    materialized (mkdtemp/copytree/git init) — a run this arm will never make
    is not worth building one only to shutil.rmtree it straight back out.
    """
    if plan is not None and "_pair_parent" not in plan:
        with _in_place_pair(plan):
            return _run_arm(arm_name, fixture, seed, registries, args, timestamp,
                            selection, out_dir=out_dir, extra=extra, plan=plan,
                            hold=hold)
    _TELEMETRY.begin_trial()
    # A caller's own three-value selection (`select_models()`'s shape)
    # records no `judge_selection`.
    agent_model, judge_model, selection_error, judge_selection = (
        (*selection, None)[:4] if selection is not None
        else select_judged_models(fixture, args))
    # Read once per run by main() (#202); a caller that built its own
    # Namespace without it records null rather than probing the CLI here.
    harness_version = getattr(args, "harness_version", None)
    permission_mode = _permission_mode(args)
    effort = _effort(args, fixture)
    extra = dict(extra or {})
    # Which model judges this arm and why (#371): on every summary of the
    # trial, a skipped or errored one included.
    extra["judge_selection"] = judge_selection
    if "_fixture_digest" in fixture:
        extra["fixture_digest"] = fixture["_fixture_digest"]
    # Whether this trial's judge call waited on its pair's objective result
    # (`_judge_hold`), so it may be skipped: #70 reads judge figures only
    # from runs judged unconditionally.
    extra["judge_conditional"] = hold is not None
    if plan is not None:
        extra.update(_in_place_record(plan, _ROLES[arm_name]))
    if fixture.get("_real_work"):
        extra["guidance_violations"] = guidance_violations.measure(None, None, None)
    stop = _TELEMETRY.budget_stop()
    if stop is not None:
        # The run reached its budget stop (#68): this trial is not started,
        # and is recorded as an error so the arm's aggregate, the report and
        # the exit code all say it is missing rather than scoring around it.
        error = {"type": BUDGET_STOP_ERROR, "detail": _budget_detail(stop, "trial")}
        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, None, None, None, None, extra=extra,
                       harness_version=harness_version,
                       permission_mode=permission_mode, arm_dir=out_dir,
                       effort=effort, agent_model=agent_model)
        return {"arm": arm_name, "error": error, "agent": None,
                "objective_checks": None, "judge": None, "models_used": [], **extra}
    if selection_error:
        # A runner-level error, recorded on the arm exactly like an agent
        # failure, so it leaves through main()'s existing exit-2 path instead
        # of running the agent on a model nobody chose. Checked BEFORE
        # materializing anything: a workspace this run will never use (no
        # agent is ever invoked) is not worth an mkdtemp + copytree + a git
        # init/add/commit only to shutil.rmtree it two lines later.
        error = {"type": "model-selection", "detail": selection_error}
        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, None, None, None, None, extra=extra,
                       harness_version=harness_version,
                       permission_mode=permission_mode, arm_dir=out_dir,
                       effort=effort, agent_model=agent_model)
        return {"arm": arm_name, "error": error, "agent": None,
                "objective_checks": None, "judge": None, "models_used": [], **extra}

    # run_setup (inside materialize_workspace, before the bookkeeping commit)
    # can fail — a fixture's `setup:` script (e.g. disarm-inherited-reach's)
    # builds real git repositories in the workspace and then deletes its own
    # machinery (setup.sh, any template dir) as its last step, and committing
    # first would let that machinery survive into the "seed" commit even
    # though it no longer exists on disk (see `materialize_workspace`'s and
    # `run_setup`'s own docstrings). `materialize_workspace` raises rather
    # than returning an error dict here, so the failure is caught outside its
    # own try/finally and the half-built workspace it still made is cleaned
    # up via the exception's own `workspace` attribute.
    scratch = env_override = None
    try:
        if plan is None:
            workspace = materialize_workspace(seed, fixture)
        else:
            scratch = _in_place_scratch(plan)
            workspace, env_override, _ = _prepare_in_place(
                _ROLES[arm_name], fixture, seed, plan, scratch)
    except BaseException as exc:
        if isinstance(exc, SetupFailedError):
            error = {"type": exc.detail["error"], "detail": exc.detail.get("detail", "")}
        elif isinstance(exc, (delivery.DeliveryError, context.ContextError)):
            # A named delivery refusal (exit 2 through the arm's error).
            error = {"type": exc.code, "detail": str(exc)}
        else:
            if scratch is not None:
                workspace_git.release(scratch / "ws")
                shutil.rmtree(scratch, ignore_errors=True)
                _clean_in_place_config(plan)
            raise
        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, None, None, None, None, extra=extra,
                       harness_version=harness_version,
                       permission_mode=permission_mode, arm_dir=out_dir,
                       effort=effort, agent_model=agent_model)
        if scratch is not None:
            workspace_git.release(scratch / "ws")
            shutil.rmtree(scratch, ignore_errors=True)
        elif isinstance(exc, SetupFailedError):
            shutil.rmtree(exc.workspace, ignore_errors=True)
        _clean_in_place_config(plan)
        return {"arm": arm_name, "error": error, "agent": None,
                "objective_checks": None, "judge": None, "models_used": [], **extra}
    try:
        if plan is None:
            assert_stand_ins_on_path(workspace, agent_env(workspace, fixture.get("env")),
                                     fixture.get("env"))

        arm_config = {
            "name": arm_name,
            "model": agent_model,
            "timeout": args.timeout or fixture.get("timeout_s", 600),
            "env": fixture.get("env"),
            "followups": fixture.get("followups"),
            "permission_mode": permission_mode,
            "read_denied": run_checkouts(args, registries=registries),
            "read_denied_outputs": run_outputs(args),
            "effort": effort,
        }
        if plan is not None:
            # The guidance path's arm shape: scratch HOME and profile, user
            # settings loaded from that profile, and the context checkouts
            # denied to reads like every registry checkout.
            arm_config.update({
                "pairing": "in_place",
                "pair_parent": str(plan["_pair_parent"]),
                "verified_plugin_roots": [str(p) for p in delivery.plugins_root(
                    Path(env_override["CLAUDE_CONFIG_DIR"])).iterdir() if p.is_dir()],
                "setting_sources": IN_PLACE_SETTING_SOURCES,
                "env_override": env_override,
                "session_scratch": str(scratch),
                "read_denied": [*arm_config["read_denied"],
                                *plan["repositories"].used_paths(),
                                *plan.get("candidate_paths", [])]})
        # A bad `registry:` (missing field, wrong type, unknown URL, or a
        # resolved path that doesn't exist) becomes an error dict here — the
        # same shape run_agent returns for skill_not_found — rather than an
        # uncaught KeyError/ValueError. _run_arm's only exception handling is
        # the `finally:` below, so anything raised here used to kill the
        # WHOLE run (including --arm both's other arm) with a bare
        # traceback: no report.md, no summary.json, and main() never reached
        # its documented `return 2`. A TRUTHY non-string `registry:` (a
        # list, an int, a mapping, a bool — YAML will happily hand over any
        # of these) used to reach _normalize_registry_url's `.strip()` with
        # the raw value and raise an uncaught AttributeError/TypeError;
        # `invalid_registry_field` closes that alongside the missing/blank
        # case above.
        registry_error = None
        if arm_name == "with_skill" and plan is None:
            registry_value = fixture.get("registry")
            if not registry_value:
                registry_error = {
                    "error": "missing_registry_field",
                    "detail": f"fixture for skill {fixture.get('skill')!r} has "
                              "no (or a blank) 'registry:' field"}
            elif not isinstance(registry_value, str):
                registry_error = {
                    "error": "invalid_registry_field",
                    "detail": f"fixture for skill {fixture.get('skill')!r} has "
                              "a 'registry:' field that must be a string, "
                              f"not {type(registry_value).__name__}"}
            else:
                try:
                    entry = registry_for_url(registries, registry_value)
                except ValueError as exc:
                    registry_error = {"error": "unknown_registry", "detail": str(exc)}
                else:
                    if not entry["path"].is_dir():
                        # The registry's NAME and layout, not its resolved
                        # absolute path — this detail reaches summary.json,
                        # which eval.yml commits to the public persistent/eval-results
                        # branch (item 6, #129 review round 4).
                        registry_error = {
                            "error": "registry_not_found",
                            "detail": f"registry {entry['name']!r} has no "
                                      f"local checkout for layout "
                                      f"{entry['layout']!r}"}
                    else:
                        arm_config["skill"] = fixture["skill"]
                        arm_config["registry"] = entry["path"]
                        arm_config["layout"] = entry["layout"]
                        arm_config["registry_name"] = entry["name"]

        baseline = guidance_violations.snapshot(workspace) if fixture.get("_real_work") else None
        # The skill reaches the workspace before the agent, never from inside
        # `run_agent` (ADR 0012).
        result = registry_error if registry_error is not None else (
            install_skill(workspace, arm_config) if "skill" in arm_config else None)
        if result is None:
            result = run_agent(workspace, fixture["prompt"], arm_config)
        if fixture.get("_real_work"):
            extra["guidance_violations"] = guidance_violations.measure(
                baseline, workspace, result.get("tool_trace"))

        error = None
        agent_summary = None
        objective_checks = None
        judge_result = judge_call = None
        raw = result.get("raw")
        tool_trace = result.get("tool_trace")
        # Only a run that produced a result can say what served it; an
        # errored agent call records none (an `agent_error` result's raw
        # object is not trusted to be a complete one).
        agent_models = [] if "error" in result else models_used(raw)
        agent_usage = None if "error" in result else model_usage(raw)
        judge_models: list = []

        if "error" in result:
            error = {"type": result["error"], "detail": result.get("detail", "")}
        else:
            agent_summary = {
                "cost_usd": result.get("cost_usd"),
                "num_turns": result.get("num_turns"),
                "duration_ms": result.get("duration_ms"),
                "usage": result.get("usage"),
            }
            # The SAME fixture errors the objective-only path names, at the
            # one call site that has a transcript and so is the only place
            # `SeedTooLarge` can actually fire: an uncaught one here came
            # out of the list comprehension in `main` as a traceback and
            # exit 1, losing both arms' artifacts with it. Recorded as an
            # arm error, in the shape `run_setup` already uses, which
            # `main` turns into exit 2.
            try:
                workspace_git.validate(workspace)
                objective_checks = objective.run_checks(
                    fixture, str(workspace), str(seed),
                    transcript=result.get("transcript"),
                    read_denied=scorer_read_denied(args, fixture=fixture))
            except objective.ScorerUnavailableError as exc:
                # A scoring dependency is missing on THIS machine: not the
                # agent's failure, so the trial is an error (excluded from
                # every check's denominator, listed in `trial_errors`), never
                # a failed check. The judge is skipped with the objective.
                error = {"type": "scorer_unavailable", "detail": str(exc)}
                _write_summary(args.results_dir, fixture["skill"], arm_name,
                               timestamp, error, agent_summary, None, None, raw,
                               extra=extra, harness_version=harness_version,
                               permission_mode=permission_mode,
                               models=agent_models, arm_dir=out_dir,
                               tool_trace=tool_trace, effort=effort,
                               agent_usage=agent_usage,
                               agent_model=agent_model)
                return {"arm": arm_name, "error": error,
                        "agent": agent_summary, "objective_checks": None,
                        "judge": None, "models_used": agent_models, **extra}
            except objective.FixtureError as exc:
                error = {"type": "invalid_fixture", "detail": str(exc)}
                _write_summary(args.results_dir, fixture["skill"], arm_name,
                               timestamp, error, agent_summary, None, None, raw,
                               extra=extra, harness_version=harness_version,
                               permission_mode=permission_mode,
                               models=agent_models, arm_dir=out_dir,
                               tool_trace=tool_trace, effort=effort,
                               agent_usage=agent_usage,
                               agent_model=agent_model)
                return {"arm": arm_name, "error": error,
                        "agent": agent_summary, "objective_checks": None,
                        "judge": None, "models_used": agent_models, **extra}

            if not args.no_judge:
                judge_call = functools.partial(
                    _call_judge, fixture, result.get("transcript") or "",
                    _build_judge_diff(workspace), judge_model,
                    permission_mode)
                if hold is None:
                    judge_result, judge_models = _judge_unless_stopped(judge_call)
                    judge_call = None

        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, agent_summary, objective_checks, judge_result, raw,
                       extra=extra, harness_version=harness_version,
                       permission_mode=permission_mode,
                       models=agent_models, judge_models=judge_models,
                       arm_dir=out_dir, tool_trace=tool_trace, effort=effort,
                       agent_usage=agent_usage, agent_model=agent_model)

        done = {"arm": arm_name, "error": error, "agent": agent_summary,
                "objective_checks": objective_checks, "judge": judge_result,
                "models_used": agent_models, **extra}
        if judge_call is not None:
            hold.append({"path": (out_dir or args.results_dir / fixture["skill"]
                                  / timestamp / arm_name) / "summary.json",
                         "call": judge_call, "result": done})
        return done
    except workspace_git.WorkspaceGitError as exc:
        error = {"type": exc.name, "detail": str(exc)}
        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, agent_summary, None, None, raw, extra=extra,
                       harness_version=harness_version, permission_mode=permission_mode,
                       models=agent_models, arm_dir=out_dir, tool_trace=tool_trace,
                       effort=effort, agent_usage=agent_usage, agent_model=agent_model)
        return {"arm": arm_name, "error": error, "agent": agent_summary,
                "objective_checks": None, "judge": None,
                "models_used": agent_models, **extra}
    finally:
        workspace_git.release(workspace)
        shutil.rmtree(scratch if scratch is not None else workspace, ignore_errors=True)
        _clean_in_place_config(plan)


def _unit_dir(results_dir: Path, skill: str, timestamp: str,
              fixture_name: str | None) -> Path:
    """Where one fixture's arm directories go: the run directory itself for a
    flat fixture (the path every run before #66 wrote), `<run>/<fixture>/`
    for a nested one — so two fixtures of one skill never share an arm
    directory."""
    run_dir = results_dir / skill / timestamp
    return run_dir / fixture_name if fixture_name else run_dir


def _read_summary(arm_dir: Path) -> dict:
    with open(arm_dir / "summary.json", encoding="utf-8") as f:
        return json.load(f)


def _union(summaries: list[dict], key: str) -> list[str]:
    """The sorted, capped union of one model-list field across trials."""
    found = {model for summary in summaries for model in summary.get(key) or []}
    return sorted(found)[:MODELS_USED_MAX]


def _aggregate_arm(arm_name: str, item: dict, args: argparse.Namespace,
                   timestamp: str) -> tuple[list[dict], dict]:
    """(the trial summaries, `aggregate_trials` over them) for one arm, read
    back from disk. Above one trial it also writes the arm's aggregate
    summary, so it can run again once a deferred judge has settled (#371)."""
    fixture = item["fixture"]
    plan = item.get("in_place")
    arm_dir = _unit_dir(args.results_dir, fixture["skill"], timestamp,
                        item["name"]) / arm_name
    label = {"fixture": item["name"]} if item["name"] else {}
    if "_fixture_digest" in fixture:
        label["fixture_digest"] = fixture["_fixture_digest"]
    if args.trials == 1:
        written = [_read_summary(arm_dir)]
        return written, aggregate_trials(written, [_trial_tool_errors(arm_dir)])
    trial_dirs = [arm_dir / f"{TRIAL_DIR_PREFIX}{index}"
                  for index in range(1, args.trials + 1)]
    written = [_read_summary(trial_dir) for trial_dir in trial_dirs]
    stats = aggregate_trials(written, [_trial_tool_errors(d) for d in trial_dirs])
    _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                   _trials_error(stats), None, None,
                   dict(JUDGE_SKIPPED) if _is_skipped(stats["aggregate"]["judge"])
                   else None, None,
                   extra={**label, **stats,
                          "judge_selection": written[0].get("judge_selection"),
                          **({"judge_conditional": written[0]["judge_conditional"]}
                             if "judge_conditional" in written[0] else {}),
                          **(_in_place_record(plan, _ROLES[arm_name])
                             if plan else {})},
                   harness_version=getattr(args, "harness_version", None),
                   permission_mode=_permission_mode(args),
                   models=_union(written, "models_used"),
                   judge_models=_union(written, "judge_models_used"),
                   arm_dir=arm_dir, effort=_effort(args, fixture),
                   agent_usage=sum_model_usage(written),
                   agent_model=_arm_model(written), usage_trials=written)
    return written, stats


def _run_arm_trials(arm_name: str, item: dict, registries: dict[str, dict],
                    args: argparse.Namespace, timestamp: str,
                    selection: tuple | None, *,
                    hold: list | None = None) -> dict:
    """Run `args.trials` trials of one arm of one fixture, and aggregate.

    `item` is one prepared fixture: `{"fixture", "seed", "name"}`, `name`
    being the nested fixture's name or None for a flat one. `hold` is
    `_run_arm`'s: a list defers every trial's judge call to
    `_settle_judging`, after which `_settle_skill_pair` aggregates again.

    Returns `{"arm", "models_used", "stats", "results"}`: `stats` is
    `aggregate_trials` over the trial summaries AS WRITTEN (read back from
    disk, so the aggregate cannot describe anything a reader of the trial
    files would not find there), and `results` the in-memory `_run_arm`
    returns, which the single-trial report is rendered from.
    """
    fixture = item["fixture"]
    plan = item.get("in_place")
    if plan is not None and "_pair_parent" not in plan:
        with _in_place_pair(plan):
            return _run_arm_trials(arm_name, item, registries, args, timestamp,
                                   selection, hold=hold)
    arm_dir = _unit_dir(args.results_dir, fixture["skill"], timestamp,
                        item["name"]) / arm_name
    label = {"fixture": item["name"]} if item["name"] else {}
    if args.trials == 1:
        results = [_run_arm(arm_name, fixture, item["seed"], registries,
                            args, timestamp, selection, out_dir=arm_dir,
                            extra={**label, "n": 1}, plan=plan, hold=hold)]
    else:
        results = [_run_arm(arm_name, fixture, item["seed"], registries,
                            args, timestamp, selection,
                            out_dir=arm_dir / f"{TRIAL_DIR_PREFIX}{index}",
                            extra={**label, "trial": index}, plan=plan,
                            hold=hold)
                   for index in range(1, args.trials + 1)]
    written, stats = _aggregate_arm(arm_name, item, args, timestamp)
    return {"arm": arm_name, "models_used": _union(written, "models_used"),
            "stats": stats, "results": results}


def _settle_skill_pair(hold: list, arms: list[dict], item: dict,
                       args: argparse.Namespace, timestamp: str) -> None:
    """`_settle_judging` for one fixture's `with_skill` and `without_skill`
    arms, then each arm aggregated again so its `stats` and its aggregate
    summary carry the judged or skipped trials."""
    if not hold:
        return
    _settle_judging(hold, _objective_decided(arms[0]["results"],
                                             arms[1]["results"]))
    for arm in arms:
        arm["stats"] = _aggregate_arm(arm["arm"], item, args, timestamp)[1]


def _write_pre_run_error(args: argparse.Namespace, fixture: dict,
                         error_type: str, detail: str,
                         fixture_name: str | None = None) -> str:
    """Record a fixture-level error as the artifacts a run would have left.

    A pre-run refusal that only printed to stdout left `results/` with
    nothing in it, so the reason the run produced no numbers was visible
    only to whoever watched it happen. The report and one summary.json per
    arm carry the named error instead, in the shape `_render_report` and
    `_write_summary` already use for an arm that failed.

    The harness version is recorded as null here: a pre-run refusal never
    invokes the CLI, so it does not spawn one just to ask its version.

    No trial is attempted, so the summaries carry no `n` (#66): they are the
    ones this function always wrote. A nested fixture's go under its own
    `<run>/<fixture>/` directory and the report names it.
    """
    timestamp = args.run_timestamp
    error = {"type": error_type, "detail": detail}
    arm_names = (["with_skill", "without_skill"] if args.arm == "both"
                 else [args.arm])
    unit_dir = _unit_dir(args.results_dir, fixture["skill"], timestamp,
                         fixture_name)
    for arm_name in arm_names:
        _write_summary(args.results_dir, fixture["skill"], arm_name, timestamp,
                       error, None, None, None, None,
                       extra={"fixture": fixture_name} if fixture_name else None,
                       arm_dir=unit_dir / arm_name)
    title = (f"{fixture['skill']}/{fixture_name}" if fixture_name
             else fixture["skill"])
    report = _render_report(title, fixture.get("prompt", ""),
                            timestamp,
                            [{"arm": name, "error": error} for name in arm_names],
                            run_lines=_TELEMETRY.report_lines(None))
    report_path = args.results_dir / fixture["skill"] / timestamp / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    return timestamp


# ---------------------------------------------------------------------------
# The `guidance` subject (#97)
#
# A skill arm installs a skill and runs with `--setting-sources project`. A
# guidance arm delivers a payload the way the fleet does — the real
# fleet-memory.sh hook, into a FRESH scratch config dir — and runs with
# `--setting-sources user,project`. Every arm then PROVES its delivery with a
# magic-token probe before it is allowed to score anything: on a machine
# carrying the fleet hook the real ~/.claude/CLAUDE.md already IS the
# guidance, so an unisolated `without` arm reports a null delta that reads as
# "the guidance does nothing".
# ---------------------------------------------------------------------------

SKILL_ARMS = ("objective-only", "with_skill", "without_skill", "both")

DEFAULT_GUIDANCE_ARMS = {"with_guidance": {"mode": "section"},
                         "without_guidance": {"mode": "none"}}

_ARM_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# A-N2-2. An arm name becomes more paths than the arm directory, and the two
# it also becomes were unmodelled. Measured on f9115ce, both rc 1 and both a
# traceback:
#   * an arm named `report.md` collided with the run's OWN report — the arm's
#     summary.json and transcripts/raw.json were written first, then
#     `_render_report` opened the arm DIRECTORY for writing:
#     `IsADirectoryError: [Errno 21]`;
#   * `a` * 255 (and 256, and 4096) was `OSError: [Errno 36] File name too
#     long`, out of the per-arm workspace's mkdtemp rather than out of the
#     arm directory, because that path carries a prefix as well as the name.
#
# The files a run writes INTO the run directory, beside the per-arm dirs, and
# the ones it writes INSIDE an arm dir. Only the first class can collide with
# an arm name; the second is why an arm called `summary.json` is harmless.
# `test_the_arm_name_refusal_covers_every_file_the_run_writes` parses this
# module and refuses any write-open whose filename is in neither tuple, so a
# new run-directory file cannot arrive without being classified.
REPORT_NAME = "report.md"
RUN_DIR_FILES = (REPORT_NAME,)
TOOL_TRACE_NAME = "tool_trace.json"
ARM_DIR_FILES = ("summary.json", "raw.json", TOOL_TRACE_NAME)

# The prefix every per-arm workspace's mkdtemp carries, named once so the
# length cap below and the call sites cannot drift apart.
ARM_WORKSPACE_PREFIX = "skills-evals-"
# mkdtemp appends 8 random characters to the prefix it is given, and the
# longest single filesystem component is 255 bytes on every filesystem this
# runs on. The workspace is the tightest consumer of an arm name, so it is
# what the cap is derived from rather than a number someone picked.
_NAME_MAX = 255
_MKDTEMP_RANDOM_LEN = 8
MAX_ARM_NAME_LEN = (_NAME_MAX - len(ARM_WORKSPACE_PREFIX) - 1
                    - _MKDTEMP_RANDOM_LEN)

# The anchor `_names_a_new_directory` measures against. Any absolute path that
# is not the filesystem root works; it never exists and is never written.
_ARM_NAME_ANCHOR = Path("/arm-name-check")


def _names_a_new_directory(name: str) -> bool:
    """Does `name` name a NEW directory directly under a run directory?

    A2-N2. The character class above accepts `.` and `..`, which are the two
    names that do NOT. Measured through main(), one arm per run: an arm named
    `..` wrote `summary.json` and `transcripts/raw.json` one level ABOVE the
    timestamped run directory — into the per-key directory that accumulates
    run history on the public `persistent/eval-results` branch — and `.` wrote into the
    run directory itself, on top of whatever was there.

    Stated as the property rather than as a blocklist of the two names that
    break it today: join the name to an anchor, normalise it the way the
    filesystem will, and require the result to be a direct child of the
    anchor still called what it was called. `...` and `.hidden` pass — they
    really are new directories — and only `.` and `..` do not.
    """
    joined = Path(os.path.normpath(_ARM_NAME_ANCHOR / name))
    return joined.parent == _ARM_NAME_ANCHOR and joined.name == name

# The placeholder a guidance fixture writes where the run's magic token goes.
# The token is fresh per run, so a fixture cannot name it; `transcript_matches`
# patterns (and any other check string) get it substituted in at score time.
TOKEN_PLACEHOLDER = "$MAGIC_TOKEN"
# The CONTROL arm's own token, delivered to it and to nothing else.
DECOY_PLACEHOLDER = "$DECOY_TOKEN"


def _validate_arm_entry(name: str, entry: dict, pairing: str = "isolation") -> dict:
    if isinstance(name, str) and name in RUN_DIR_FILES:
        raise guidance.GuidanceError(
            f"invalid arm name {name!r}: the run writes "
            f"{', '.join(RUN_DIR_FILES)} into the run directory itself, "
            "beside the per-arm directories, so an arm of that name is a "
            "directory where a file has to go — the arm's own summary.json "
            "and transcripts/raw.json are written first and the report then "
            "fails with IsADirectoryError, after the run has spent every arm")
    if isinstance(name, str) and len(name) > MAX_ARM_NAME_LEN:
        raise guidance.GuidanceError(
            f"invalid arm name of {len(name)} characters: an arm name may be "
            f"at most {MAX_ARM_NAME_LEN}. It becomes a directory name under "
            "results/ AND the per-arm workspace "
            f"`{ARM_WORKSPACE_PREFIX}<name>-XXXXXXXX`, which is the longer of "
            f"the two; past {_NAME_MAX} bytes the filesystem refuses it with "
            "`File name too long` part-way into the run instead of naming a "
            "rule here")
    if (not isinstance(name, str) or not _ARM_NAME_RE.fullmatch(name)
            or not _names_a_new_directory(name)):
        raise guidance.GuidanceError(
            f"invalid arm name {name!r}: arm names become directory names "
            "under results/, so they must be a single path segment that "
            "names a NEW directory — `.` and `..` are neither, and an arm "
            "named `..` writes its summary one level above the run "
            "directory, into the history the public results branch carries")
    if not isinstance(entry, dict) or "mode" not in entry:
        raise guidance.GuidanceError(f"arm {name!r} must be a mapping with a `mode:`")
    unknown = sorted(set(entry) - {"mode", "objective_checks"})
    if unknown:
        # A typo'd `objective_check:` would drop the arm's whole check list
        # and still report green, which is worse than failing at load time.
        raise guidance.GuidanceError(
            f"arm {name!r} has unknown key(s) {unknown} — an arm takes `mode:` "
            "and an optional `objective_checks:`")
    mode = entry["mode"]
    if mode not in guidance.MODES:
        raise guidance.GuidanceError(
            f"arm {name!r} has unknown mode {mode!r} — expected one of "
            f"{', '.join(guidance.MODES)}")
    # Name and expectation must agree. The guard derives its expectation from
    # the MODE, so a `with_*` arm carrying `mode: none` would be a control arm
    # wearing a treatment arm's name — the summary would read as a delivered
    # arm that saw nothing, which is precisely the shape of a real failure.
    if name.startswith("with_") and mode == "none":
        raise guidance.GuidanceError(
            f"arm {name!r} is named as a treatment arm but carries "
            "`mode: none` — rename it or give it a mode that delivers")
    # Isolation only: an in-place baseline carries the rest of the context
    # (`full-minus-section`), so its `without_*` arm delivers (ADR 0012).
    if (pairing == "isolation" and not name.startswith("with_") and mode != "none"
            and name.startswith("without_")):
        raise guidance.GuidanceError(
            f"arm {name!r} is named as a control arm but carries "
            f"`mode: {mode}` — rename it or give it `mode: none`")
    return {"name": name, "mode": mode,
            "objective_checks": entry.get("objective_checks")}


def guidance_arms(fixture: dict, arm_flag: str, ablation: bool = False,
                  pairing: str = "isolation") -> list[dict]:
    """The arms to run, in declaration order.

    Default pair `section` / `none` — "does this section teach the behavior".
    `ablation: [full, full-minus-section]` is the second pair, `--ablation`,
    which the matrix runner schedules monthly: the marginal value of the
    section IN SITU inside a 56 KB always-on file, which is the question that
    decides whether it keeps paying for its bytes.
    """
    if pairing == "in_place":
        # One fixed pair: the deployed guidance with the section, and without
        # it. `arms:` and `ablation:` describe isolation-era pairs.
        if ablation or "arms" in fixture:
            raise guidance.GuidanceError(
                "--pairing in_place runs the fixed with_guidance/without_guidance "
                "pair over the deployed context; it takes neither --ablation nor "
                "a fixture's `arms:`")
        declared = IN_PLACE_GUIDANCE_ARMS
    elif ablation:
        modes = fixture.get("ablation")
        if not isinstance(modes, list) or len(modes) != 2:
            raise guidance.GuidanceError(
                "--ablation needs the fixture to declare `ablation:` as a list "
                "of exactly two modes (e.g. [full, full-minus-section])")
        declared = {f"ablation_{str(m).replace('-', '_')}": {"mode": m} for m in modes}
        if len(declared) != 2:
            raise guidance.GuidanceError(
                f"`ablation: {modes}` names the same mode twice")
    else:
        # ABSENT means "take the default pair". PRESENT means "these are my
        # arms", and an empty, null or non-mapping value is a fixture error —
        # `fixture.get("arms") or DEFAULT_GUIDANCE_ARMS` made the check below
        # dead code, so `arms: {}`, `arms:` and `arms: []` all silently ran
        # somebody else's `section`/`none` pair under this fixture's name.
        declared = (DEFAULT_GUIDANCE_ARMS if "arms" not in fixture
                    else fixture["arms"])
        if not isinstance(declared, dict) or not declared:
            raise guidance.GuidanceError(
                "`arms:` must be a mapping of arm name -> {mode: ...}, and a "
                f"non-empty one; got {declared!r}. Omit the key entirely to "
                "take the default "
                f"{'/'.join(a['mode'] for a in DEFAULT_GUIDANCE_ARMS.values())}"
                " pair.")
    arms = [_validate_arm_entry(name, entry, pairing) for name, entry in declared.items()]
    if arm_flag in ("both", "all"):
        return arms
    for arm in arms:
        if arm["name"] == arm_flag:
            return [arm]
    raise guidance.GuidanceError(
        f"--arm {arm_flag!r} names no arm in this fixture (declared: "
        f"{', '.join(a['name'] for a in arms)}; or `both` for all of them)")


def substitute_token(value, token: str, decoy: str | None = None):
    """Replace the fixture's `$MAGIC_TOKEN` placeholder with this run's token,
    recursively, in a copy — the fixture dict itself is never mutated.

    `$DECOY_TOKEN` is the control arm's own token, and is substituted only for
    an arm that HAS one (`mode: none`). Left alone elsewhere it stays a
    literal, and a check looking for it fails loudly rather than passing on a
    placeholder nobody filled in.
    """
    if isinstance(value, str):
        out = value.replace(TOKEN_PLACEHOLDER, token)
        return out if decoy is None else out.replace(DECOY_PLACEHOLDER, decoy)
    if isinstance(value, list):
        return [substitute_token(item, token, decoy) for item in value]
    if isinstance(value, dict):
        return {key: substitute_token(item, token, decoy)
                for key, item in value.items()}
    return value


def _guard_error(guard: dict) -> dict:
    """The error block for an arm whose delivery could not be proved.

    Two distinguishable shapes, both INCONCLUSIVE and both exit 2, neither
    ever PASS or FAIL: `guard_error` (the probe could not run at all — no
    credential, CLI missing; a guard that cannot run is never a skipped
    guard) and `guard_miss` (it ran and disagreed with this arm's mode).
    """
    if guard["error"]:
        return {"type": "guard_error",
                "detail": f"delivery guard could not run: {guard['error']['type']}: "
                          f"{guard['error']['detail']}"}
    if guard.get("contaminated"):
        return {"type": "guard_contaminated",
                "detail": "the delivery guard's probe reported a token this "
                          "arm was NOT delivered — a control arm reporting the "
                          "TREATMENT token, or a treatment arm reporting the "
                          "control's DECOY. Either way this arm read memory "
                          "the harness never delivered to it, so the per-arm "
                          "isolation did not hold and every number in this run "
                          "is suspect; no score is written for it"}
    expectation = "the magic word" if guard["expected"] else "no magic word"
    observed = "saw it" if guard["observed"] else "did not see it"
    return {"type": "guard_miss",
            "detail": f"delivery guard expected {expectation}, the probe "
                      f"{observed} — this arm did not read the token it was "
                      "delivered (a treatment arm its payload's, a control arm "
                      "its decoy), so it never read its own scratch user "
                      "memory; no score is written for it"}


def _guidance_agent_model(fixture: dict, args: argparse.Namespace) -> str | None:
    """A guidance run's arm model, resolved once per run as the skill path's
    `select_models` does: the `--model` flag, the fixture's `model:` pin,
    else the roster's first arm. Before 2026-10-28 an unpinned guidance run
    left it to the CLI default and recorded `cross_model.model` null, so
    compute_gates dropped every ingested guidance run ("no requested arm
    model") and no section ever had evidence to retire or improve on. With
    no readable roster (a harness copy without one) it stays None, the CLI
    default, as before."""
    pinned = getattr(args, "model", None) or fixture.get("model")
    if pinned:
        return pinned
    roster, problem = read_roster(_resolve_roster(getattr(args, "roster", None)))
    arm_ids = [] if problem else roster_models(roster)[0]
    return arm_ids[0] if arm_ids else None


def _run_guidance_arm(arm: dict, fixture: dict, seed: Path, ctx: dict,
                      args: argparse.Namespace, timestamp: str) -> dict:
    """Materialize a scratch dir, deliver, guard, invoke, score, clean up.

    `ctx["judge_holds"][arm["name"]]`, its pair's list, defers the judge
    call as `_run_arm`'s `hold` does (#371); an arm absent from it judges
    inside the trial."""
    plan = ctx.get("plan")
    hold = (ctx.get("judge_holds") or {}).get(arm["name"])
    if plan is not None and "_pair_parent" not in plan:
        with _in_place_pair(plan):
            return _run_guidance_arm(arm, fixture, seed, ctx, args, timestamp)
    _TELEMETRY.begin_trial()
    if _TELEMETRY.budget_stop() is not None:
        # A guidance run is one pair: an arm left out leaves nothing to
        # compare, so the run ends here (#68) instead of writing a summary
        # shaped like a delivery result that never happened.
        raise BudgetStopped(_TELEMETRY.budget_stop())
    harness_version = getattr(args, "harness_version", None)
    permission_mode = _permission_mode(args)
    effort = _effort(args, fixture)
    agent_model = (getattr(args, "model", None) or fixture.get("model")
                   or ctx.get("agent_model"))
    extra = {}
    agent_summary = raw = tool_trace = None
    agent_models = []
    agent_usage = None
    scratch = (_in_place_scratch(plan) if plan is not None else
               Path(tempfile.mkdtemp(prefix=f"{ARM_WORKSPACE_PREFIX}{arm['name']}-")))
    try:
        plan = ctx.get("plan")
        if plan is not None:
            # An in-place arm (ADR 0012): the deployed guidance with or
            # without the section, plus the deployed skills, delivered into
            # a scratch profile after deps/setup and the seed guard. Its
            # delivery is proved offline here; the model-call guard for
            # in-place arms is part 3's.
            role = _ROLES[arm["name"]]
            channel, decoy, guard = "user", None, None
            setting_sources = IN_PLACE_SETTING_SOURCES
            extra = {"subject": "guidance", "section": ctx["section"],
                     "mode": arm["mode"], "bytes": None, "delivery": channel,
                     "hook_verdict": None, "installed": None, "decoy": None,
                     "hook_returncode": None, "guard": None,
                     **_in_place_record(plan, role)}
            if fixture.get("_real_work"):
                extra["guidance_violations"] = guidance_violations.measure(None, None, None)
            try:
                workspace, env, delivered = _prepare_in_place(
                    role, fixture, seed, plan, scratch)
            except (SetupFailedError, delivery.DeliveryError, context.ContextError) as exc:
                error = ({"type": exc.detail["error"], "detail": exc.detail.get("detail", "")}
                         if isinstance(exc, SetupFailedError) else
                         {"type": exc.code, "detail": str(exc)})
                _write_summary(args.results_dir, None, arm["name"], timestamp,
                               error, None, None, None, None, key=ctx["key"],
                               extra=extra, harness_version=harness_version,
                               permission_mode=permission_mode, effort=effort,
                               agent_model=agent_model)
                return {"arm": arm["name"], "mode": arm["mode"], "error": error,
                        "agent": None, "objective_checks": None, "judge": None,
                        "guard": None, "inconclusive": False, "models_used": [],
                        "pairing": "in_place",
                        **({"guidance_violations": extra["guidance_violations"]}
                           if "guidance_violations" in extra else {})}
            extra.update({"bytes": delivered["bytes"], "installed": True,
                          "hook_verdict": delivered["hook_verdict"],
                          "hook_returncode": 0})
        else:
            workspace, home = scratch / "ws", scratch / "home"
            config, tmpdir = scratch / "config", scratch / "tmp"
            for path in (workspace, home, config, tmpdir):
                path.mkdir(parents=True)
            if seed.is_dir():
                shutil.copytree(seed, workspace, dirs_exist_ok=True)
            seed_prep.prepare_seed(workspace, fixture)
            seed_error = seed_prep.seed_guard(workspace, fixture)
            if seed_error is not None:
                raise guidance.GuidanceError(seed_error["detail"])
            _git("init", "-q", cwd=workspace)
            _git("add", "-A", cwd=workspace)
            _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=workspace)
            workspace_git.seal(workspace)

            channel = ctx["delivery"]
            # The token THIS arm is delivered. A treatment arm gets the run's
            # magic token; the control gets a DECOY of its own, so that its guard
            # can ask a question with a wrong answer — "does this arm read its own
            # scratch user memory?" — instead of the vacuous "no magic word?", the
            # one answer a `none` arm gave whether it was clean or contaminated.
            decoy = ctx["decoys"].get(arm["name"])
            arm_token = decoy if decoy is not None else ctx["token"]
            # Every token this run minted that was NOT delivered to this arm.
            # Symmetric by construction: the treatment token for a control arm,
            # the control's decoy for a treatment arm, and any other control's
            # decoy for a control arm. Reporting one of these means the arm read
            # memory nobody delivered to it.
            forbidden = tuple(other for other in (ctx["token"], *ctx["decoys"].values())
                              if other != arm_token)
            payload = guidance.assemble(ctx["guidance_dir"], ctx["row"], arm["mode"],
                                        token=arm_token)
            info = guidance.deliver(
                ctx["guidance_dir"], scratch=scratch, home=home, payload=payload,
                dest_dir=config if channel == "user" else workspace)
            env = guidance.agent_env(workspace=workspace, home=home, tmpdir=tmpdir,
                                     config_dir=config, env_spec=fixture.get("env"))
            # A delivery that provably did not happen is not a guard question.
            # `installed` and the hook's returncode are offline and free; the
            # guard costs a real model call and can only answer the AMBIGUOUS
            # "the probe did not see the token" — which is what a sabotaged hook
            # (prints `fleet-guidance: current`, writes nothing, exits 0) used to
            # get reported as. Both facts land in the arm's `extra` either way.
            extra = {"subject": "guidance", "section": ctx["section"],
                     "mode": arm["mode"], "bytes": info["bytes"],
                     "delivery": channel, "hook_verdict": info["verdict"],
                     "installed": info["installed"], "decoy": decoy,
                     "hook_returncode": info["returncode"], "guard": None,
                     **ctx.get("record", {}),
                     # Per arm: an arm in no complete pair judges unconditionally.
                     "judge_conditional": hold is not None}
            if fixture.get("_real_work"):
                extra["guidance_violations"] = guidance_violations.measure(None, None, None)
            if info["returncode"] is not None and (
                    not info["installed"] or info["returncode"] != 0):
                error = {"type": "delivery_failed",
                         "detail": (
                             f"the hook exited {info['returncode']} and the marked "
                             f"block is {'present' if info['installed'] else 'ABSENT'} "
                             f"in {info['dest']} — this arm was never delivered "
                             "its payload, so nothing about it is measurable; no "
                             "guard call was made and no score is written")}
                _write_summary(args.results_dir, None, arm["name"], timestamp,
                               error, None, None, None, None,
                               key=ctx["key"], extra=extra,
                               harness_version=harness_version,
                               permission_mode=permission_mode, effort=effort,
                               agent_model=agent_model)
                return {"arm": arm["name"], "mode": arm["mode"], "error": error,
                        "agent": None, "objective_checks": None, "judge": None,
                        "guard": None, "inconclusive": True, "models_used": [],
                        **({"guidance_violations": extra["guidance_violations"]}
                           if "guidance_violations" in extra else {})}

            setting_sources = guidance.SETTING_SOURCES[channel]
            # The guard's preflight model: the fixture's own `model:` pin when it
            # has one, else the CLI's default. When the model roster (#67) lands,
            # its `preflight` entry — the cheapest model that can answer a
            # tool-free probe — is what this line consults instead.
            preflight_model = args.model or fixture.get("model")
            guard_started = _now()
            with run_canary.collecting_results() as guard_results:
                guard = guidance.run_guard(
                    workspace=workspace, token=arm_token,
                    expected=guidance.guard_expectation(arm["mode"]), env=env,
                    # The other side, for EVERY arm: it must not report a token
                    # it was not delivered.
                    forbidden_tokens=forbidden,
                    setting_sources=setting_sources, model=preflight_model,
                    timeout=(fixture.get("guard") or {}).get("timeout_s", 300))

            extra["guard"] = guard
            guard_results, guard_events = _split_events(guard_results)
            _TELEMETRY.timed("guard", guard_started, guard_results, outcome=(
                guard.get("error") or {}).get("type") or "ok",
                events=guard_events)

            if not guard["ok"]:
                error = _guard_error(guard)
                _write_summary(args.results_dir, None, arm["name"], timestamp,
                               error, None, None, None, None,
                               key=ctx["key"], extra=extra,
                               harness_version=harness_version,
                               permission_mode=permission_mode, effort=effort,
                               agent_model=agent_model)
                return {"arm": arm["name"], "mode": arm["mode"], "error": error,
                        "agent": None, "objective_checks": None, "judge": None,
                        "guard": guard, "inconclusive": True, "models_used": [],
                        **({"guidance_violations": extra["guidance_violations"]}
                           if "guidance_violations" in extra else {})}

        arm_config = {
            "name": arm["name"],
            "model": agent_model,
            "timeout": args.timeout or fixture.get("timeout_s", 600),
            "setting_sources": setting_sources,
            "env_override": env,
            "followups": fixture.get("followups"),
            "permission_mode": permission_mode,
            "effort": effort,
            # A multi-turn arm's transcript lands under `config`, inside this
            # scratch, and goes when the scratch does: never archived.
            "session_scratch": str(scratch),
            "read_denied": run_checkouts(args, guidance_dir=ctx["guidance_dir"]),
            "read_denied_outputs": run_outputs(args),
        }
        if plan is not None:
            arm_config.update({"pairing": "in_place", "pair_parent": str(plan["_pair_parent"]),
                               "verified_plugin_roots": [str(p) for p in delivery.plugins_root(
                                   Path(env["CLAUDE_CONFIG_DIR"])).iterdir() if p.is_dir()]})
            arm_config["read_denied"] = [*arm_config["read_denied"],
                                         *plan["repositories"].used_paths(),
                                *plan.get("candidate_paths", [])]
        baseline = guidance_violations.snapshot(workspace) if fixture.get("_real_work") else None
        result = run_agent(workspace, fixture["prompt"], arm_config)
        if fixture.get("_real_work"):
            extra["guidance_violations"] = guidance_violations.measure(
                baseline, workspace, result.get("tool_trace"))

        error = None
        agent_summary = None
        objective_checks = None
        judge_result = judge_call = None
        raw = result.get("raw")
        tool_trace = result.get("tool_trace")
        agent_models = [] if "error" in result else models_used(raw)
        agent_usage = None if "error" in result else model_usage(raw)
        judge_models: list = []

        if "error" in result:
            error = {"type": result["error"], "detail": result.get("detail", "")}
        else:
            agent_summary = {
                "cost_usd": result.get("cost_usd"),
                "num_turns": result.get("num_turns"),
                "duration_ms": result.get("duration_ms"),
                "usage": result.get("usage"),
            }
            checks = arm["objective_checks"] or fixture.get("objective_checks", [])
            scored = dict(fixture)
            scored["objective_checks"] = substitute_token(
                checks, ctx["token"], decoy)
            try:
                workspace_git.validate(workspace)
                objective_checks = objective.run_checks(
                    scored, str(workspace), str(seed),
                    transcript=result.get("transcript"),
                    read_denied=scorer_read_denied(args, fixture=scored))
            except objective.ScorerUnavailableError as exc:
                # As in `_run_arm`: a missing scoring dependency on THIS
                # machine is a trial error, not a failed check, and the
                # judge is skipped with the objective.
                error = {"type": "scorer_unavailable", "detail": str(exc)}

            if error is None and not args.no_judge and fixture.get("judge_rubric"):
                _git("add", "-A", cwd=workspace)
                # `:!CLAUDE.md` only under the project-delivery fallback, where
                # the hook wrote the payload INTO the workspace: without it the
                # judge would be handed the whole delivered corpus as if the
                # agent had written it, which under `mode: full` is 56 KB of
                # diff that says nothing about the agent's work.
                excludes = [":!.claude"]
                if channel == "project":
                    excludes.append(":!CLAUDE.md")
                diff = _git("diff", "--cached", "--", ".", *excludes,
                            cwd=workspace).stdout
                judge_call = functools.partial(
                    _call_judge, fixture, result.get("transcript") or "", diff,
                    (fixture.get("judge") or {}).get("model"), permission_mode)
                if hold is None:
                    judge_result, judge_models = _judge_unless_stopped(judge_call)
                    judge_call = None

        _write_summary(args.results_dir, None, arm["name"], timestamp, error,
                       agent_summary, objective_checks, judge_result, raw,
                       key=ctx["key"], extra=extra,
                       harness_version=harness_version,
                       permission_mode=permission_mode, models=agent_models,
                       judge_models=judge_models, tool_trace=tool_trace,
                       effort=effort, agent_usage=agent_usage,
                       agent_model=agent_model)
        done = {"arm": arm["name"], "mode": arm["mode"], "error": error,
                "agent": agent_summary, "objective_checks": objective_checks,
                "judge": judge_result, "guard": guard, "inconclusive": False,
                "models_used": agent_models,
                **({"pairing": "in_place"} if plan is not None else {}),
                **({"guidance_violations": extra["guidance_violations"]}
                   if "guidance_violations" in extra else {})}
        if judge_call is not None:
            hold.append({
                "path": args.results_dir / ctx["key"] / timestamp / arm["name"]
                / "summary.json", "call": judge_call, "result": done})
        return done
    except workspace_git.WorkspaceGitError as exc:
        error = {"type": exc.name, "detail": str(exc)}
        _write_summary(args.results_dir, None, arm["name"], timestamp,
                       error, agent_summary, None, None, raw, key=ctx["key"],
                       extra=extra, harness_version=harness_version,
                       permission_mode=permission_mode, models=agent_models,
                       tool_trace=tool_trace, effort=effort, agent_usage=agent_usage,
                       agent_model=agent_model)
        return {"arm": arm["name"], "mode": arm["mode"], "error": error,
                "agent": agent_summary, "objective_checks": None, "judge": None,
                "guard": None, "inconclusive": True, "models_used": agent_models,
                **({"guidance_violations": extra["guidance_violations"]}
                   if "guidance_violations" in extra else {})}
    except guidance.GuidanceError:
        if not fixture.get("_real_work"):
            raise
        error = {"type": "guidance_configuration", "detail": "guidance trial setup failed"}
        counts = extra.get("guidance_violations") or guidance_violations.measure(None, None, None)
        _write_summary(args.results_dir, None, arm["name"], timestamp,
                       error, agent_summary, None, None, raw, key=ctx["key"],
                       extra={"subject": "guidance", "section": ctx["section"],
                              "mode": arm["mode"], "bytes": None,
                              "guidance_violations": counts},
                       harness_version=harness_version, permission_mode=permission_mode,
                       models=agent_models, tool_trace=tool_trace,
                       effort=effort, agent_usage=agent_usage,
                       agent_model=agent_model)
        return {"arm": arm["name"], "mode": arm["mode"], "error": error,
                "agent": agent_summary, "objective_checks": None, "judge": None,
                "guard": None, "inconclusive": True, "models_used": [],
                "guidance_violations": counts}
    finally:
        workspace_git.release(scratch / "ws")
        shutil.rmtree(scratch, ignore_errors=True)
        _clean_in_place_config(plan)


def _render_guidance_report(section: str, prompt: str, timestamp: str,
                            delivery: str, arm_bytes: dict,
                            arm_summaries: list[dict],
                            harness_version: str | None = None, run_lines=()) -> str:
    """The guidance report. Its header names the MODE PAIR, because "with vs
    without" is meaningless here without it — `section` vs `none` and `full`
    vs `full-minus-section` are different questions about the same section.
    """
    modes = ", ".join(f"{s['arm']}={s['mode']}" for s in arm_summaries)
    lines = [
        f"# Eval report: guidance/{section}",
        "",
        f"- Modes: {modes}",
        f"- Delivery: {delivery}",
        f"- Prompt: {prompt.strip()}",
        f"- Timestamp: {timestamp}",
        *run_lines,
        _harness_line(harness_version, arm_summaries),
        "",
        "| Arm | Mode | Bytes | Guard | Objective | Judge overall | Cost (USD) | Error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in arm_summaries:
        checks = s.get("objective_checks")
        objective_str = (f"{sum(1 for c in checks if c['passed'])}/{len(checks)}"
                         if checks else "-")
        judge_str = _judge_cell(s.get("judge"))
        agent = s.get("agent") or {}
        cost = agent.get("cost_usd")
        cost_str = f"{cost:.4f}" if isinstance(cost, (int, float)) else "-"
        guard = s.get("guard") or {}
        if s.get("pairing") == "in_place":
            guard_str = "offline proof only (in place)"
        elif guard.get("ok"):
            guard_str = "ok (saw it)" if guard.get("expected") else "ok (clean)"
        elif guard.get("observed") is None:
            guard_str = "INCONCLUSIVE (probe failed)"
        else:
            guard_str = (f"INCONCLUSIVE (expected {guard.get('expected')}, "
                         f"observed {guard.get('observed')})")
        err = s.get("error")
        err_str = (" ".join(f"{err['type']}: {err['detail']}".split())
                   .replace("|", "\\|")[:200] if err else "")
        lines.append(f"| {s['arm']} | {s['mode']} | {arm_bytes.get(s['arm'], '-')} | "
                     f"{guard_str} | {objective_str} | {judge_str} | {cost_str} | "
                     f"{err_str} |")
    lines += guidance_violations.render([
        {"arm": s["arm"], "stats": {"aggregate": {
            "guidance_violations": guidance_violations.aggregate([s])}}}
        for s in arm_summaries])
    return "\n".join(lines) + "\n"


def guidance_results_key(section: str, fixture_name: str | None) -> str:
    """The results/ subtree of a guidance run: `guidance/<section>`, plus the
    fixture's directory name for a `subject: any` fixture run with --section."""
    return f"guidance/{section}" + (f"/{fixture_name}" if fixture_name else "")


def _run_guidance(args: argparse.Namespace, fixture: dict,
                  fixture_name: str | None = None) -> int:
    """`subject: guidance` — the whole run, from section id to exit code.

    `fixture_name` is set for a `subject: any` fixture run with --section: its
    results go under `guidance/<section>/<fixture name>/`, so two real-work
    fixtures run under one section never share a results directory.
    """
    section = fixture.get("section")
    if not isinstance(section, str) or not section:
        print(f"{args.eval_dir / 'fixture.yaml'} has `subject: guidance` but no "
              "(or a non-string) `section:` — the id of a row in "
              "_agent-guidance's agents-md/eval-coverage.yml")
        return 2
    if "/" in section or section in (".", ".."):
        print(f"invalid section id {section!r}: it becomes a results/ path segment")
        return 2

    key = guidance_results_key(section, fixture_name)
    seed = args.eval_dir / "seed"

    if args.arm == "objective-only":
        # N-f. A guidance fixture's checks are PER ARM, so a top-level
        # `objective_checks:` is usually absent — and `run_checks` over zero
        # checks printed `{"checks": []}` and exited 0, which reads as "every
        # check passed". Say so instead. This is the convention
        # check-guidance-coverage.js states in its own header: an empty
        # measurement is not a passing one.
        if not fixture.get("objective_checks"):
            print(f"{args.eval_dir / 'fixture.yaml'} declares no top-level "
                  "`objective_checks:` — objective-only has nothing to score. "
                  "A guidance fixture's checks are per arm; run it with "
                  "`--arm both` (or a named arm) instead.")
            return 2
        with tempfile.TemporaryDirectory(prefix="scoring-guidance-") as tmp:
            workspace = Path(tmp) / "ws"
            if seed.is_dir():
                shutil.copytree(seed, workspace)
            else:
                workspace.mkdir(parents=True)
            # Every guidance arm strips and guards its copy of the seed; the
            # objective-only path scores the workspace the same way.
            seed_prep.prepare_seed(workspace, fixture)
            seed_error = seed_prep.seed_guard(workspace, fixture)
            if seed_error is not None:
                print(f"setup failed: {seed_error['detail']}")
                return 2
            try:
                results = objective.run_checks(fixture, str(workspace), str(seed),
                                               read_denied=scorer_read_denied(args, fixture=fixture))
            except objective.ScorerUnavailableError as exc:
                # As the skill path's objective-only: a missing scoring
                # dependency is not a failed check (exit 1), so name it and
                # exit 2 instead of a traceback.
                print(f"scorer_unavailable: {exc}")
                return 2
            except objective.FixtureError as exc:
                # As the skill path's objective-only: a bad fixture value
                # (`strip_seed: "no"`, an oversized seed file) is a named
                # `invalid_fixture` and exit 2, not a traceback and exit 1,
                # the code a failing check returns. `SeedTooLarge` is a
                # `FixtureError`, so one clause covers both.
                print(f"invalid_fixture: {exc}")
                return 2
        print(json.dumps({"subject": "guidance", "section": section,
                          "arm": args.arm, "checks": results}, indent=2))
        return 0 if all(r["passed"] for r in results) else 1

    if not isinstance(fixture.get("prompt"), str) or not fixture["prompt"]:
        print(f"{args.eval_dir / 'fixture.yaml'} is missing a string `prompt:`")
        return 2

    if getattr(args, "skill_registry", None) is not None or getattr(args, "skill_bundle", None) is not None:
        print("configuration error: skill registry/bundle qualifiers require a skill subject")
        return 2
    pairing = getattr(args, "pairing", delivery.DEFAULT_PAIRING)
    plan = None
    try:
        arms = guidance_arms(fixture, args.arm, args.ablation, pairing)
        if pairing == "in_place":
            if args.delivery != "user":
                raise delivery.DeliveryError(
                    "invalid_pairing", "an in-place pair delivers guidance as user "
                    "memory, as the fleet does; --delivery project is isolation-only")
            plan = _in_place_plan(fixture, args, {}, "guidance")
        guidance_dir = (guidance.require_guidance_dir(guidance.resolve_guidance_dir(
            args.guidance, os.environ.get("AGENT_GUIDANCE_DIR"),
            Path(__file__).resolve().parent.parent)) if plan is None else
            plan["repositories"].get(context.GUIDANCE_REPOSITORY))
        row = (guidance.find_row(guidance.load_manifest(guidance_dir), section,
                                 guidance_dir) if plan is None else None)
        # What the retirement gate pools an isolation run by (#124): the
        # fixture's content, its directory name (two fixtures of one section
        # share `results/guidance/<section>/`), the section and the rest of
        # the guidance as digests, and whether the judge waited on the pair.
        judge_pairs = _guidance_judge_holds(args, fixture, arms)
        record = {} if plan is not None else {
            "fixture_name": args.eval_dir.name,
            "guidance_digests": guidance.content_digests(guidance_dir, row),
            **({"fixture_digest": digest}
               if (digest := fixture_digest(args.eval_dir)) is not None else {})}
    except guidance.GuidanceError as exc:
        print(f"guidance configuration error: {exc}")
        return 2

    ctx = {"guidance_dir": guidance_dir, "row": row, "section": section,
           "key": key, "delivery": args.delivery, "plan": plan, "record": record,
           "judge_holds": {pair[role]: pair["hold"] for pair in judge_pairs
                           for role in ("with", "without")},
           # One fresh token per RUN, shared by every arm: the control arm
           # looks for the SAME token the treatment arm was given, which is
           # what turns "the control saw it" into proof of contamination.
           "token": guidance.new_token(),
           # Every `none` arm's decoy, minted HERE rather than inside the arm
           # that gets it. A TREATMENT arm's guard needs them too — a
           # treatment probe reporting a control's decoy is the same per-arm
           # isolation failure seen from the other side — and an arm cannot
           # be handed a token that does not exist until its own turn comes.
           "decoys": {arm["name"]: guidance.new_decoy_token()
                      for arm in arms if arm["mode"] == "none"}}

    ctx["agent_model"] = _guidance_agent_model(fixture, args)

    timestamp = getattr(args, "run_timestamp", None) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if getattr(args, "timestamp", None) is not None and (args.results_dir / key / timestamp).exists():
        print("configuration error: --timestamp names existing guidance results; choose another timestamp")
        return 2
    try:
        # Once per run, before any arm (#202), as the skill path does.
        args.harness_version = claude_version()
        with _in_place_pair(plan):
            arm_summaries = [_run_guidance_arm(arm, fixture, seed, ctx, args, timestamp)
                             for arm in arms]
            by_name = {s["arm"]: [s] for s in arm_summaries}
            for pair in judge_pairs:
                if pair["hold"]:
                    _settle_judging(pair["hold"], _objective_decided(
                        by_name[pair["with"]], by_name[pair["without"]]))
    except guidance.GuidanceError as exc:
        # A checkout missing the hook, a heading that has drifted from its
        # manifest row, a payload that cannot be read: all configuration, all
        # exit 2, none of them a traceback out of the middle of a run.
        print(f"guidance configuration error: {exc}")
        return 2
    except BudgetStopped as exc:
        # The arms already run keep their summaries, which `finish()` stamps
        # with `usage.budget_stop`; there is no pair to report on.
        print(f"Budget stop: {exc}")
        return 2

    report_dir = args.results_dir / key / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)
    arm_bytes = {}
    for arm in arm_summaries:
        summary_path = report_dir / arm["arm"] / "summary.json"
        with open(summary_path, encoding="utf-8") as f:
            arm_bytes[arm["arm"]] = json.load(f)["bytes"]
    report = _render_guidance_report(section, fixture["prompt"], timestamp,
                                     args.delivery, arm_bytes, arm_summaries,
                                     args.harness_version,
                                     _TELEMETRY.report_lines(args.harness_version))
    with open(report_dir / REPORT_NAME, "w", encoding="utf-8") as f:
        f.write(report)

    inconclusive = [s for s in arm_summaries if s["inconclusive"]]
    for arm_summary in inconclusive:
        guard = arm_summary["guard"]
        # A `delivery_failed` arm never reached the guard, so there is no
        # expected/observed pair to report — only the delivery's own detail.
        preamble = (f"guard expected {guard['expected']}, observed "
                    f"{guard['observed']} — ") if guard else "delivery — "
        print(f"INCONCLUSIVE {arm_summary['arm']} (mode "
              f"{arm_summary['mode']}): {preamble}"
              f"{arm_summary['error']['detail']}")
    if inconclusive:
        print("INCONCLUSIVE: at least one arm could not prove its delivery; "
              "no score was written for it. This is never a PASS and never a "
              "FAIL.")
        return 2

    errored_arms = [s["arm"] for s in arm_summaries if s["error"]]
    if errored_arms:
        print(f"Runner-level error in arm(s): {', '.join(errored_arms)}")
        return 2
    return 0


# ---------------------------------------------------------------------------
# Fixture layout (#66). A skill's fixtures live in ONE of two shapes:
#
#   flat     <skill>/fixture.yaml               one fixture; results under
#            <skill>/seed/                      results/<skill>/<ts>/<arm>/
#
#   nested   <skill>/<fixture-name>/fixture.yaml   several; results under
#            <skill>/<fixture-name>/seed/          results/<skill>/<ts>/<fixture-name>/<arm>/
#
# THE RULE IS POSITIONAL. A fixture directory whose PARENT directory is named
# exactly the fixture's own `skill:` is a nested fixture, named after its own
# directory; any other fixture directory is a flat one and writes the paths
# every run before #66 wrote. `run_eval.py evals/<skill>/<name>` and
# `run_eval.py evals/<skill> --fixture <name>` name the same fixture and write
# the same paths, which is what stops two fixtures of one skill sharing
# `results/<skill>/<ts>/<arm>/`.
#
# The position is read off the path AS NAMED — made absolute and normalized,
# symlinks not followed (`fixture_position`). A fixture is the directory entry
# an operator points at, and two entries of one skill directory are two
# fixtures whatever they link to; following links let two of them resolve to
# one name and one results directory.
#
# What the rule cannot see is intent: a flat fixture kept in a directory whose
# parent happens to carry the skill's own name (`<skill>/<skill>/fixture.yaml`)
# reads as a nested fixture called `<skill>`. `evals/<skill>/fixture.yaml` is
# never that — its parent is `evals`.
#
# NEVER BOTH. A directory holding a `fixture.yaml` is a fixture and may not
# also hold fixture subdirectories (its own `seed/` excepted, whatever the
# seed contains), nor sit directly inside another fixture. The two shapes
# write different trees under the same `results/<skill>/`, so a skill that
# had both would publish runs a reader cannot tell apart by path. Every
# fixture directory an invocation selects is checked both ways before it is
# loaded (`check_fixture_dir`).
# ---------------------------------------------------------------------------

# A nested fixture's name becomes a directory BESIDE report.md, and a flat
# fixture's arm directories sit at that same level — so a fixture may not be
# called what a run-directory file or a skill arm's directory is called.
# `seed` is refused because a flat fixture's own seed directory is skipped by
# name.
RESERVED_FIXTURE_NAMES = (SEED_DIR, "with_skill", "without_skill",
                          "objective-only", *RUN_DIR_FILES)


class FixtureLayoutError(guidance.GuidanceError):
    """The directory named on the command line is not a layout this runner
    can run: no fixture, a flat fixture and nested ones together, a fixture
    name that cannot be a results directory, a `--fixture` naming nothing."""


def _validate_fixture_name(name: str) -> None:
    """`name` is an existing directory's own name, so it is already one path
    segment of a length the filesystem takes; what is left to refuse is the
    characters results/ paths are held to, and the names already taken."""
    if not _ARM_NAME_RE.fullmatch(name):
        raise FixtureLayoutError(
            f"invalid fixture name {name!r}: a nested fixture is named after "
            "its directory and that name becomes a directory under results/, "
            "so it may only use letters, digits, `.`, `_` and `-`")
    # Casefolded: on a filesystem that folds case, `Report.md/` and
    # `report.md` are one name.
    if name.casefold() in RESERVED_FIXTURE_NAMES:
        raise FixtureLayoutError(
            f"invalid fixture name {name!r}: reserved "
            f"({', '.join(RESERVED_FIXTURE_NAMES)}). A nested fixture's "
            "results directory sits beside report.md, where a flat fixture's "
            "arm directories also go, and `seed` is a flat fixture's own seed")


def nested_fixture_names(directory: Path) -> list[str]:
    """The sorted names of `directory`'s immediate subdirectories that hold a
    fixture.yaml. Immediate only: a fixture two levels down is not this
    directory's."""
    if not directory.is_dir():
        return []
    return sorted(child.name for child in directory.iterdir()
                  if child.is_dir() and (child / FIXTURE_FILE).is_file())


def _mixed_layout_error(directory: Path, nested: list[str]) -> FixtureLayoutError:
    return FixtureLayoutError(
        f"{directory} holds a {FIXTURE_FILE} AND fixture subdirectories "
        f"({', '.join(nested)}). A skill directory holds one flat fixture or "
        "nested ones, never both: the two write different trees under the "
        "same results/<skill>/. Move the flat fixture into a subdirectory of "
        "its own, or remove the nested ones")


SUBJECT_ANY = "any"


def apply_runtime_subject(fixture: dict, skill: str | None, section: str | None,
                          arm: str, fixture_path: Path) -> dict:
    """The fixture with its subject named for this run.

    `subject: any` is a real-work fixture that names no treatment of its own
    (the owner, 2026-10-06, Q4: "Subject-agnostic"): `--skill NAME` runs it as a
    skill fixture and `--section ID` as a guidance fixture, so one fixture
    serves every subject instead of being copied once per subject. With
    neither, only `--arm objective-only` can run it: scoring a workspace
    needs no treatment. A fixture that fixes its own subject refuses both
    flags, so a flag never silently changes what a committed fixture means.
    The input is never mutated.
    """
    for flag, value in (("--skill", skill), ("--section", section)):
        if value is not None and not value.strip():
            raise guidance.GuidanceError(f"{flag} must be a nonblank name")
    subject = fixture.get("subject", "skill")
    if subject != SUBJECT_ANY:
        if skill is not None or section is not None:
            raise guidance.GuidanceError(
                f"{fixture_path} fixes its subject ({subject!r}); --skill and "
                f"--section name the subject only for a `subject: "
                f"{SUBJECT_ANY}` fixture")
        return fixture
    if "skill" in fixture or "section" in fixture:
        raise guidance.GuidanceError(
            f"{fixture_path} is `subject: {SUBJECT_ANY}`, so it names no "
            "`skill:` or `section:`; the run names one with --skill or --section")
    if skill is not None and section is not None:
        raise guidance.GuidanceError(
            "pass one of --skill and --section, not both --skill and --section")
    if skill is not None:
        return {**fixture, "subject": "skill", "skill": skill, "_real_work": True}
    if section is not None:
        return {**fixture, "subject": "guidance", "section": section, "_real_work": True}
    if arm != "objective-only":
        raise guidance.GuidanceError(
            f"{fixture_path} is `subject: {SUBJECT_ANY}` and this run names no "
            "subject: pass --skill NAME or --section ID, or score it with "
            "--arm objective-only")
    return fixture


def check_draft(fixture: dict, arm: str, allow_draft: bool, fixture_path: Path) -> None:
    """`draft: true` (#65's key) marks a fixture no human has reviewed yet.

    objective-only still runs it, which is how a draft proves its checks
    execute; an agent arm needs `--allow-draft`, which no workflow passes, so
    a draft is never paid for or published until a person removes the key.
    """
    if "draft" not in fixture:
        return
    if not isinstance(fixture["draft"], bool):
        raise guidance.GuidanceError(
            f"{fixture_path}: `draft:` must be true or false, got {fixture['draft']!r}")
    if fixture["draft"] and arm != "objective-only" and not allow_draft:
        raise guidance.GuidanceError(
            f"{fixture_path} is `draft: true`: a person removes the key after "
            "reviewing that its task text is the real ask and its checks encode "
            "it. Score it with --arm objective-only, or pass --allow-draft for a "
            "local run")


def fixture_position(eval_dir: Path) -> Path:
    """`eval_dir` as named: absolute and normalized (`.`, `..`, a trailing
    slash), symlinks NOT followed. The layout rule reads a fixture's name and
    its parent's off this path."""
    return Path(os.path.abspath(eval_dir))


def check_fixture_dir(eval_dir: Path) -> None:
    """Refuse a fixture directory that is half of a mixed layout: one that
    holds fixture subdirectories of its own (`seed/` excepted), or one that
    sits directly inside another fixture."""
    inner = [name for name in nested_fixture_names(eval_dir) if name != SEED_DIR]
    if inner:
        raise _mixed_layout_error(eval_dir, inner)
    position = fixture_position(eval_dir)
    if (position.parent / FIXTURE_FILE).is_file():
        raise _mixed_layout_error(position.parent, [position.name])


def resolve_fixture_dirs(eval_dir: Path, selected: str | None) -> list[Path]:
    """The fixture directories one invocation runs, in name order, each one
    already checked by `check_fixture_dir`.

    `eval_dir` holding a fixture.yaml is that one fixture — today's shape,
    and the only one `--fixture` does not apply to. Otherwise it is a skill
    directory and every `<name>/fixture.yaml` beneath it is run, or the one
    `--fixture NAME` selects.
    """
    if (eval_dir / FIXTURE_FILE).is_file():
        check_fixture_dir(eval_dir)
        if selected is not None:
            raise FixtureLayoutError(
                f"--fixture {selected!r} selects one of a skill directory's "
                f"nested fixtures, and {eval_dir} is itself a fixture. Drop "
                "the flag, or name the skill directory")
        return [eval_dir]
    nested = nested_fixture_names(eval_dir)
    if not nested:
        raise FixtureLayoutError(
            f"{eval_dir} holds no {FIXTURE_FILE}, and no "
            f"<name>/{FIXTURE_FILE} beneath it")
    for name in nested:
        _validate_fixture_name(name)
        check_fixture_dir(eval_dir / name)
    if selected is None:
        return [eval_dir / name for name in nested]
    if selected not in nested:
        raise FixtureLayoutError(
            f"--fixture {selected!r} names no fixture under {eval_dir} "
            f"(found: {', '.join(nested)})")
    return [eval_dir / selected]


def nested_fixture_name(eval_dir: Path, skill: str) -> str | None:
    """The nested fixture's name, or None for a flat fixture — the positional
    rule above, applied to one fixture directory whose `skill:` is known."""
    position = fixture_position(eval_dir)
    if position.parent.name != skill:
        return None
    _validate_fixture_name(position.name)
    return position.name


def _valid_timestamp(value: str) -> bool:
    """`--timestamp` is a results/ path segment, so it is held to the exact
    shape the wall-clock default produces and must be a real date and time."""
    if not _TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


@_ending_the_run
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("eval_dir", type=Path)
    # Deliberately NOT an argparse `choices=` list any more: a guidance
    # fixture declares its own arm names (the delivery canary declares five,
    # one per mode), which argparse cannot know at parse time. Validated per
    # subject below instead, with the same exit code (2) and a message that
    # names the arms this fixture actually has.
    parser.add_argument("--arm", default="objective-only",
                        help="objective-only | with_skill | without_skill | both "
                             "for a skill fixture; both, or any arm name the "
                             "fixture declares, for a guidance fixture")
    parser.add_argument("--guidance", default=None,
                        help="path to an _agent-guidance checkout (guidance "
                             "fixtures only); else $AGENT_GUIDANCE_DIR, else the "
                             "sibling ../_agent-guidance")
    parser.add_argument("--delivery", default="user", choices=list(guidance.DELIVERIES),
                        help="how the guidance reaches the agent: `user` (the "
                             "production path — the fleet hook writes "
                             "$CLAUDE_CONFIG_DIR/CLAUDE.md, the CLI reads it as "
                             "user memory) or `project` (the fallback for a CLI "
                             "that does not honour CLAUDE_CONFIG_DIR for memory)")
    parser.add_argument("--ablation", action="store_true",
                        help="run the fixture's `ablation:` mode pair (full vs "
                             "full-minus-section) instead of its arms")
    parser.add_argument("--workspace", type=Path, default=None,
                        help="objective-only: score this workspace instead of the pristine seed")
    parser.add_argument("--registry", action="append", default=None,
                        help="registry checkout, repeatable: NAME=PATH (name from "
                             "harness/registries.yml), or a bare PATH (legacy) taken "
                             "as the acsi-agentskills entry; unknown names and empty "
                             "paths are rejected. Merges by name with "
                             "$SKILLS_EVALS_REGISTRIES (same NAME=PATH,NAME=PATH "
                             "shape; a bare entry there is also taken as "
                             "acsi-agentskills), then $AGENTSKILLS_DIR (name kept "
                             "unchanged) for acsi-agentskills specifically, then a "
                             "sibling checkout ../<name> next to this repo for any "
                             "name still unresolved")
    parser.add_argument("--context-repo", action="append", default=None,
                        metavar="OWNER/REPO=PATH",
                        help="explicit context checkout mapping, repeatable "
                             "(ADR 0012); an unmapped repository falls back to "
                             "the sibling clone of its name beside this one")
    parser.add_argument("--pairing", default=delivery.DEFAULT_PAIRING,
                        choices=list(delivery.PAIRINGS),
                        help="in_place: both arms carry the fixture's deployed "
                             "context and only the subject differs (ADR 0012; "
                             "the hosted routine only); isolation (default): "
                             "the subject alone against nothing")
    parser.add_argument("--skill-registry", default=None, metavar="OWNER/REPO",
                        help="in_place only: qualify the skill's registry identity")
    parser.add_argument("--subject-candidate", type=Path, default=None, metavar="PATH",
                        help="in_place only: explicitly test this skill directory or guidance file")
    parser.add_argument("--skill-bundle", default=None, metavar="BUNDLE",
                        help="in_place only: qualify the skill subject as "
                             "BUNDLE:SKILL when its bare name is ambiguous")
    parser.add_argument("--model", default=None,
                        help="override the fixture's model for the agent")
    parser.add_argument("--roster", type=Path, default=None,
                        help="override the committed evals/roster.yml model "
                             "roster (tests and deliberate local runs only)")
    parser.add_argument("--permission-mode", default=guidance.DEFAULT_PERMISSION_MODE,
                        choices=list(guidance.PERMISSION_MODES),
                        help="the CLI permission mode every agent arm and "
                             "the judge are launched with (default "
                             f"{guidance.DEFAULT_PERMISSION_MODE}). Recorded "
                             "in every summary.json's `harness` block; "
                             "`bypassPermissions` reproduces runs recorded "
                             "before this option existed, and is refused by "
                             "the CLI when it runs as root")
    parser.add_argument("--effort", default=None,
                        choices=list(guidance.EFFORT_LEVELS),
                        help="the CLI effort level every agent arm is "
                             "launched with, first turn and follow-ups alike; "
                             "overrides the fixture's `effort:`. Without "
                             "either, no `--effort` is passed (the CLI's "
                             "default). Recorded in every summary.json's "
                             "`harness` block; the judge does not take it")
    parser.add_argument("--no-judge", action="store_true",
                        help="skip judge scoring (wins over --judge)")
    parser.add_argument("--judge", default="auto", choices=("auto", "always"),
                        help="auto (default): a with/without pair is judged "
                             "only when the objective result does not already "
                             "decide it (the `with` arm's pass rate strictly "
                             "above the `without` arm's skips the judge for "
                             "the pair); always: judge every trial")
    parser.add_argument("--timeout", type=int, default=None,
                        help="override the fixture's agent timeout (seconds); "
                             f"1..{guidance.MAX_TIMEOUT_S}, the same ceiling "
                             "the fixture's own `timeout_s:` is held to")
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="root directory for run outputs (summaries + reports)")
    parser.add_argument("--read-deny", type=Path, action="append", default=[],
                        metavar="DIR",
                        help="another directory no agent arm may read "
                             "(repeatable): a wrapper's own output tree, "
                             "holding its earlier runs beside --results-dir")
    parser.add_argument("--fixture", default=None, metavar="NAME",
                        help="when eval_dir is a skill directory holding "
                             "nested fixtures (<name>/fixture.yaml), run only "
                             "this one; without it, every nested fixture runs")
    parser.add_argument("--skill", default=None, metavar="NAME",
                        help="the skill a `subject: any` fixture runs under "
                             "(a subject-agnostic real-work fixture)")
    parser.add_argument("--section", default=None, metavar="ID",
                        help="the guidance section a `subject: any` fixture "
                             "runs under")
    parser.add_argument("--allow-draft", action="store_true",
                        help="run an agent arm of a `draft: true` fixture "
                             "(local review only; no workflow passes it)")
    parser.add_argument("--trials", type=int, default=1,
                        help="trials per arm, each a fresh workspace, a fresh "
                             "agent call and (unless --judge auto skips the "
                             "pair) its own judge call "
                             f"(default 1; 1..{MAX_TRIALS}). Above 1, each "
                             "trial is written under <arm>/trial-<k>/ and "
                             "<arm>/summary.json carries the aggregate")
    parser.add_argument("--timestamp", default=None,
                        help="the run directory's name, as YYYYMMDDTHHMMSSZ, "
                             "instead of the current UTC time — for tests and "
                             "wrappers that need a deterministic path. Refused "
                             "when that run directory already holds one of "
                             "the arms this invocation would write")
    for field, choices in RUN_CHOICES.items():
        parser.add_argument(f"--run-{field}", default=None, choices=list(choices),
                            help=f"the run's {field}, recorded in every "
                                 "summary.json's `run` block. Without it the "
                                 "field is what the environment observes, "
                                 "else null with a reason")
    parser.add_argument("--run-id", default=None, type=_run_name,
                        help="the run's identifier on its path: a routine or "
                             "workstation run id (default: $GITHUB_RUN_ID "
                             "when that is set)")
    parser.add_argument("--budget", type=Path, default=budget.DEFAULT_BUDGET,
                        metavar="PATH",
                        help="the weekly allowance budget (#68): a run stops "
                             "starting trials and judge calls once the weekly "
                             "meter has moved per_run_cap since its first "
                             "reading, or reads stop_when_account_above "
                             "(default: evals/budget.yml)")
    parser.add_argument("--asserted-quiet", default=None, type=_run_name,
                        metavar="WHO",
                        help="record that WHO asserts nothing else used the "
                             "account during this run (`usage."
                             "other_account_activity`); otherwise `unknown`")
    args = parser.parse_args()
    _TELEMETRY.flags = args
    try:
        _TELEMETRY.budget = budget.load_budget(args.budget)
    except budget.BudgetError as exc:
        print(f"configuration error: {exc}")
        return 2
    try:
        args.context_repos = context.parse_context_repos(args.context_repo)
    except guidance.GuidanceError as exc:
        print(f"configuration error: {exc}")
        return 2

    # S1-a. The FLAG is checked before anything else — before the fixture is
    # loaded, before either subject branch, before any CLI call — because it
    # is the OTHER source of the value that reaches
    # `subprocess.run(timeout=...)`, and it OVERRIDES the fixture knob
    # `validate_timeouts` has just bounded (`args.timeout or
    # fixture.get("timeout_s", 600)`, twice below). Round 2 bounded the knob
    # and left the override unchecked, so the defect the ceiling was added to
    # close came straight back through the flag beside it: measured on
    # a6d165d, `--timeout 2200000` was rc 1 and a bare `OverflowError`,
    # `--timeout 2701` was accepted above the job budget, and `--timeout 3000`
    # against a scored leg that never returns did not come back at all.
    # argparse's `type=int` bounds nothing: it rejects `abc` and accepts every
    # integer there is, negative and absurd alike.
    #
    # `is not None` and not a truthiness test: `--timeout 0` is falsy, so
    # `args.timeout or ...` would silently fall back to the fixture's value
    # rather than honour a nonsense flag — a zero must be REFUSED by name, not
    # quietly ignored.
    if args.timeout is not None:
        try:
            guidance.check_timeout(args.timeout, "--timeout",
                                   guidance.CLI_TIMEOUT_REMEDY)
        except guidance.GuidanceError as exc:
            print(f"configuration error: {exc}")
            return 2

    # #66. Checked before any fixture is read, like the flag above: both are
    # operator input that decides what gets spent or where it gets written.
    # `type=int` bounds nothing, so `--trials 0`, a negative and an absurd
    # count are refused by name rather than run as zero or as a bill.
    if not 1 <= args.trials <= MAX_TRIALS:
        print(f"configuration error: --trials must be between 1 and "
              f"{MAX_TRIALS}, got {args.trials}. Every trial is a paid agent "
              "call per arm, and a paid judge call too unless the judge is "
              "skipped")
        return 2
    if args.trials > 1 and args.arm == "objective-only":
        print("configuration error: --trials applies to the agent arms. "
              "objective-only scores one workspace with no agent call, so N "
              "trials of it would be N copies of one answer")
        return 2
    if args.timestamp is not None and not _valid_timestamp(args.timestamp):
        print(f"configuration error: --timestamp {args.timestamp!r} is not a "
              "UTC time written as YYYYMMDDTHHMMSSZ (for example "
              "20260716T070000Z). It becomes the run directory's name under "
              "results/")
        return 2
    # Before any fixture or arm: a managed policy that turns the arms' sandbox
    # off without the CLI saying so, or widens it past what `--settings` can
    # take back (`managed_sandbox_refusal`). objective-only runs no agent.
    if args.arm != "objective-only":
        refusal = managed_sandbox_refusal()
        if refusal is not None:
            print(f"managed_sandbox_policy: {refusal}")
            return 2
    if (args.skill_bundle is not None or args.skill_registry is not None or
            args.subject_candidate is not None) and args.pairing != "in_place":
        print("configuration error: subject qualifiers/candidates require an in-place pair; "
              "pass --pairing in_place")
        return 2
    # Cloud-only (ADR 0012, "Decisions on the build"): an in-place arm's
    # scratch profile holds no login, and no local credential adapter exists.
    if args.pairing == "in_place":
        try:
            delivery.require_hosted(os.environ)
        except delivery.DeliveryError as exc:
            print(f"configuration error: {exc}")
            return 2
    # ONE timestamp per invocation: every fixture, arm and trial of this run
    # shares one run directory.
    args.run_timestamp = (args.timestamp
                          or datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT))

    try:
        eval_dirs = resolve_fixture_dirs(args.eval_dir, args.fixture)
    except FixtureLayoutError as exc:
        print(f"fixture configuration error: {exc}")
        return 2
    # A fixture found beneath the directory named on the command line, rather
    # than named by it.
    discovered = eval_dirs != [args.eval_dir]

    # EVERY selected fixture is loaded and checked before ANY arm of any of
    # them starts: the first refusal ends the invocation with nothing spent.
    prepared = []
    for eval_dir in eval_dirs:
        fixture = None
        try:
            fixture = load_fixture(eval_dir)
            agnostic = fixture.get("subject") == SUBJECT_ANY
            fixture = apply_runtime_subject(fixture, args.skill, args.section,
                                            args.arm, eval_dir / FIXTURE_FILE)
            check_draft(fixture, args.arm, args.allow_draft,
                        eval_dir / FIXTURE_FILE)
            answer_leak.validate_fixture(fixture, eval_dir / FIXTURE_FILE)
            validate_mapping_keys(fixture, eval_dir / FIXTURE_FILE)
            validate_timeouts(fixture, eval_dir / FIXTURE_FILE)
            validate_followups(fixture, eval_dir / FIXTURE_FILE)
            validate_effort(fixture, eval_dir / FIXTURE_FILE)
            seed_prep.validate_fixture(fixture, eval_dir)
        except MappingFixtureKeyError as exc:
            # A malformed `judge:` is #81's `invalid_judge_block` — named in
            # stdout AND recorded as report.md plus one summary.json per arm, so
            # a run that produced no numbers says why in `results/` rather than
            # only to whoever watched it. The load-time guard here refuses it
            # before the later `isinstance(judge_cfg, dict)` check can, so that
            # check would otherwise never be reached for a non-mapping and the
            # artifacts would silently stop being written. Only when the fixture
            # is well-formed enough to have a usable `skill:` — `_write_pre_run_
            # error` derives every path it writes from that name.
            skill_name = fixture.get("skill") if isinstance(fixture, dict) else None
            usable_skill = isinstance(skill_name, str)
            if usable_skill:
                try:
                    _validate_skill_name(skill_name)
                except ValueError:
                    usable_skill = False
            if exc.key == "judge" and usable_skill:
                try:
                    name = nested_fixture_name(eval_dir, skill_name)
                except FixtureLayoutError as layout_exc:
                    print(f"fixture configuration error: {layout_exc}")
                    return 2
                print(f"invalid_judge_block: {exc}")
                _write_pre_run_error(args, fixture, "invalid_judge_block", str(exc),
                                     fixture_name=name)
                return 2
            print(f"fixture configuration error: {exc}")
            return 2
        except guidance.GuidanceError as exc:
            print(f"fixture configuration error: {exc}")
            return 2

        # Two subjects: a skill copied into the workspace (the original, and
        # untouched by #97), and the fleet guidance delivered into user memory by
        # the real fleet-memory.sh hook. Everything below this branch is the skill
        # path exactly as it was.
        subject = fixture.get("subject", "skill")
        if subject == "guidance":
            # #66 is the skill subject's: a guidance fixture is run by its own
            # directory, one trial per arm. In-place pairs accept a fixed
            # timestamp for reproducible evidence; isolation keeps its contract. Refused by name rather than run with a flag that
            # asked for something else quietly ignored.
            if discovered or args.trials > 1 or (args.timestamp is not None and args.pairing != "in_place"):
                print(f"configuration error: {eval_dir / FIXTURE_FILE} is a "
                      "guidance fixture. Nested-fixture discovery, --trials "
                      "above 1 and --timestamp apply to skill fixtures only; "
                      "run it by its own directory without them")
                return 2
            try:
                if agnostic:
                    name = fixture_position(eval_dir).name
                    _validate_fixture_name(name)
                return _run_guidance(args, fixture, name if agnostic else None)
            except FixtureLayoutError as exc:
                print(f"fixture configuration error: {exc}")
                return 2
            except guidance.GuidanceError as exc:
                # The sink checks (S1-a-2) raise from inside whichever function
                # was about to spawn. Caught HERE so every one of them lands on
                # the rc-2 configuration contract instead of a traceback.
                print(f"configuration error: {exc}")
                return 2
        if subject == SUBJECT_ANY:
            # Only objective-only reaches here (apply_runtime_subject refused
            # every other arm): no skill, no results path, no judge. A
            # subject-agnostic fixture is named after its own directory.
            try:
                name = fixture_position(eval_dir).name
                _validate_fixture_name(name)
            except FixtureLayoutError as exc:
                print(f"fixture configuration error: {exc}")
                return 2
            prepared.append({"fixture": fixture, "seed": eval_dir / SEED_DIR,
                             "name": name})
            continue
        if subject != "skill":
            print(f"{eval_dir / FIXTURE_FILE} has unknown subject "
                  f"{subject!r} — expected 'skill', 'guidance' or "
                  f"'{SUBJECT_ANY}'")
            return 2
        if args.arm not in SKILL_ARMS:
            print(f"--arm {args.arm!r} is not valid for a skill fixture "
                  f"(expected one of {', '.join(SKILL_ARMS)})")
            return 2

        # Validated ONCE, here, before any path is derived from the fixture:
        # `_write_summary` and `report_path` below both build a filesystem path
        # out of `fixture["skill"]` unconditionally, for every arm — a fixture
        # missing "skill" or "prompt" used to die with a bare KeyError deep
        # inside _run_arm/_render_report, and a `skill:` containing `../` was
        # never rejected before those paths were built (run_agent's own check
        # only fires for the with_skill arm, by which point _write_summary has
        # already used the raw name for with_skill AND without_skill). Presence
        # first (a genuinely absent or blank field, same as
        # _load_registries_config's own missing check), then type — a TRUTHY
        # non-string `skill:`/`prompt:` (a list, an int) used to sail past a
        # bare `not fixture.get(f)` check and die later with an uncaught
        # TypeError from re.fullmatch or subprocess.run.
        required = ["skill"] if args.arm == "objective-only" else ["skill", "prompt"]
        missing = [f for f in required
                  if fixture.get(f) is None or fixture.get(f) == ""]
        if missing:
            print(f"{eval_dir / FIXTURE_FILE} is missing required "
                  f"field(s): {', '.join(missing)}")
            return 2
        bad_type = [f for f in required if not isinstance(fixture.get(f), str)]
        if bad_type:
            print(f"{eval_dir / FIXTURE_FILE} field(s) must be strings: " +
                  ", ".join(f"{f!r} is {type(fixture[f]).__name__}" for f in bad_type))
            return 2

        # Validated HERE, before anything derives a path from it. It used to
        # run after the judge-mode guard below, and only for a non-objective-only
        # arm — so a fixture carrying both `skill: ../../ESCAPED` and a judge
        # mode this runner refuses had `_write_pre_run_error` build
        # `<results-dir>/../../ESCAPED/<timestamp>/report.md` and write it,
        # two directories above where the operator pointed the run. Every path
        # this function builds comes off this name, so the check comes first
        # and applies to every arm.
        try:
            _validate_skill_name(fixture["skill"])
        except ValueError as exc:
            print(f"invalid fixture: {exc}")
            return 2

        # Flat or nested (#66), decided by where the fixture sits and before any
        # results path is built: every path below hangs off this name.
        try:
            if agnostic:
                # Named after its own directory whatever --skill says, so two
                # real-work fixtures run under one skill never share results.
                name = fixture_position(eval_dir).name
                _validate_fixture_name(name)
            else:
                name = nested_fixture_name(eval_dir, fixture["skill"])
        except FixtureLayoutError as exc:
            print(f"fixture configuration error: {exc}")
            return 2
        if discovered and name is None:
            print(f"fixture configuration error: {eval_dir / FIXTURE_FILE} "
                  f"declares `skill: {fixture['skill']}`, but it was found as a "
                  f"nested fixture of {fixture_position(eval_dir).parent.name!r}. A "
                  "skill directory's nested fixtures all carry the directory's "
                  "own name as their `skill:`")
            return 2

        # `judge:` written as anything but a mapping — a list, a string, a
        # number, a bare `true`; YAML hands over all of them — used to reach
        # `.get("mode")` and raise an uncaught AttributeError: exit 1, a
        # traceback, and none of the artifacts a fixture-level refusal is
        # supposed to leave. Named and recorded like every other pre-run
        # refusal, and for every arm: a malformed block is malformed whether or
        # not this run would have reached the judge.
        judge_cfg = fixture.get("judge")
        if judge_cfg is None or judge_cfg == "":
            judge_cfg = {}
        if not isinstance(judge_cfg, dict):
            detail = (f"fixture's `judge:` block is a {type(judge_cfg).__name__}, "
                      "not a mapping: it must carry keys like `mode:`, `model:` "
                      "and `references:`, or be left out entirely")
            print(f"invalid_judge_block: {detail}")
            _write_pre_run_error(args, fixture, "invalid_judge_block", detail,
                                 fixture_name=name)
            return 2

        # A fixture whose `judge:` block asks for an instrument this runner
        # cannot drive is refused before any arm starts, rather than scored with
        # the wrong one. `_run_arm` still calls `judge.score()` with the three
        # keywords it knew before #81 — no mode, no references — so a
        # `judge.mode: pairwise` fixture used to be scored by the ABSOLUTE judge
        # against a ranking rubric: measured on recruiter-reply, exit 0 and a
        # report reading "Judge overall | 7.5", which is not a rank and means
        # nothing there. Wiring `_run_arm` onto `judge.score_fixture` belongs to
        # #97 (https://github.com/Adam-S-Daniel/skills-evals/issues/97); until
        # then the run either passes --no-judge or does not happen.
        #
        # The mode is casefolded, exactly as `judge.score()` casefolds it, so
        # `mode: Absolute` is absolute rather than "a mode this runner cannot
        # drive yet" — which said nothing true about a spelling of the mode the
        # runner does drive.
        #
        # objective-only is exempt because it runs no judge at all: these
        # fixtures are meant to exit 1 there with "no transcript", which is the
        # documented asymmetry rather than a runner error.
        judge_mode = judge_cfg.get("mode", "absolute")
        normalised_mode = (judge_mode.strip().casefold()
                           if isinstance(judge_mode, str) else judge_mode)
        if (args.arm != "objective-only" and not args.no_judge
                and normalised_mode not in (None, "", "absolute")):
            # Front-loaded: `_render_report` truncates this cell to 200
            # characters, and the three sentences of provenance that used to
            # open it pushed the issue, its URL and the flag that makes the run
            # work off the end of the report a reader actually sees.
            detail = (f"cannot drive judge mode {judge_mode!r} yet: re-run with "
                      "--no-judge and read the objective column. The call site "
                      "is not wired onto judge.score_fixture() yet; until "
                      "then _run_arm still calls judge.score() with the "
                      "arguments it knew before #81, so scoring this fixture "
                      "here would rank it with the absolute judge.")
            print(f"judge_mode_unsupported: {detail}")
            _write_pre_run_error(args, fixture, "judge_mode_unsupported", detail,
                                 fixture_name=name)
            return 2

        digest = fixture_digest(eval_dir)
        prepared.append({"fixture": ({**fixture, "_fixture_digest": digest}
                                     if digest is not None else fixture),
                         "seed": eval_dir / SEED_DIR, "name": name})

    # Resolved and validated before ANY arm starts, including objective-only:
    # a bad --registry/$SKILLS_EVALS_REGISTRIES override used to be silently
    # ignored for objective-only (it never reaches resolve_registries at
    # all), so a typo'd override "worked" there while failing everywhere else.
    try:
        registries = resolve_registries(
            args.registry, os.environ.get("SKILLS_EVALS_REGISTRIES"),
            Path(__file__).resolve().parent.parent, os.environ.get("AGENTSKILLS_DIR"))
        _validate_registry_paths(registries)
    except ValueError as exc:
        print(f"registry configuration error: {exc}")
        return 2

    if args.arm == "objective-only":
        # One JSON document about one workspace: a skill directory holding
        # several nested fixtures has no single answer to print.
        if len(prepared) != 1:
            print("configuration error: --arm objective-only scores one "
                  f"fixture, and {args.eval_dir} holds "
                  f"{len(prepared)} ({', '.join(i['name'] for i in prepared)}). "
                  "Pass --fixture NAME")
            return 2
        fixture, seed = prepared[0]["fixture"], prepared[0]["seed"]
        # `objective.FixtureError` — a `strip_seed:` written as anything but
        # a boolean, a seed file over the provenance read cap — is a fixture
        # error, and every other fixture error here is a named line and exit
        # 2. These two came out as an uncaught traceback and exit 1, which
        # is the code a legitimately FAILING eval returns: a fixture that
        # could not be scored at all was indistinguishable, to a CI job
        # reading the exit code, from one whose agent wrote a bad reply.
        try:
            if args.workspace:
                # An explicitly given workspace is scored as-is — the
                # caller's own responsibility to have already run any
                # `setup:` themselves (or to be scoring a hand-built
                # workspace that never needed it).
                workspace = args.workspace
                results = objective.run_checks(fixture, str(workspace),
                                               str(seed), read_denied=scorer_read_denied(args, fixture=fixture))
            else:
                with tempfile.TemporaryDirectory(prefix="scoring-skill-") as tmp:
                    workspace = Path(tmp) / "ws"
                    shutil.copytree(seed, workspace)
                    seed_prep.prepare_seed(workspace, fixture)
                    setup_error = (run_setup(workspace, fixture)
                                   or seed_prep.seed_guard(workspace, fixture))
                    if setup_error is not None:
                        print(f"setup failed: {setup_error['detail']}")
                        return 2
                    results = objective.run_checks(fixture, str(workspace),
                                                   str(seed), read_denied=scorer_read_denied(args, fixture=fixture))
        except guidance.GuidanceError as exc:
            # run_setup's and the objective git checks' sink checks land here.
            print(f"configuration error: {exc}")
            return 2
        except objective.ScorerUnavailableError as exc:
            # Not a fixture error and not a failed check: exit 2, named.
            print(f"scorer_unavailable: {exc}")
            if "skill" in fixture:  # a `subject: any` run has no results path
                _write_pre_run_error(args, fixture, "scorer_unavailable", str(exc),
                                     fixture_name=prepared[0]["name"])
            return 2
        except objective.FixtureError as exc:
            # `SeedTooLarge` is a `FixtureError`, so one clause covers both.
            print(f"invalid_fixture: {exc}")
            if "skill" in fixture:  # a `subject: any` run has no results path
                _write_pre_run_error(args, fixture, "invalid_fixture", str(exc),
                                     fixture_name=prepared[0]["name"])
            return 2

        head = ({"skill": fixture["skill"]} if "skill" in fixture
                else {"subject": SUBJECT_ANY, "fixture": prepared[0]["name"]})
        print(json.dumps({**head, "arm": args.arm, "checks": results}, indent=2))
        return 0 if all(r["passed"] for r in results) else 1

    # The frozen context and its subject, once per fixture, before any arm:
    # an unresolvable context or subject is exit 2 with nothing spent.
    if args.pairing == "in_place":
        for item in prepared:
            try:
                item["in_place"] = _in_place_plan(item["fixture"], args, registries, "skill")
            except guidance.GuidanceError as exc:
                print(f"context configuration error: {exc}")
                return 2

    timestamp = args.run_timestamp
    arm_names = ["with_skill", "without_skill"] if args.arm == "both" else [args.arm]
    # The wall clock never names a run directory twice in practice; a
    # `--timestamp` can. Written into again, an arm directory would keep the
    # earlier run's `trial-<k>/` beside this run's aggregate, so the reuse is
    # refused before anything is spent — for the flag only, which leaves the
    # default path as it was.
    if args.timestamp is not None:
        taken = [str(arm_dir) for item in prepared for arm_dir in (
                     _unit_dir(args.results_dir, item["fixture"]["skill"],
                               timestamp, item["name"]) / name
                     for name in arm_names) if arm_dir.exists()]
        if taken:
            print(f"configuration error: --timestamp {timestamp} names a run "
                  f"that already holds {', '.join(taken)}. A run never writes "
                  "over another; pick another timestamp or results directory")
            return 2
    # Every prepared fixture carries the same `skill:` — a leaf is one
    # fixture, and a skill directory's nested ones are held to its name.
    skill = prepared[0]["fixture"]["skill"]
    outcomes = []
    try:
        # Read once per run, before any arm (#202): every arm's summary.json
        # records the same version, and a run whose version could not be
        # read still runs and records null.
        args.harness_version = claude_version()
        for item in prepared:
            # Resolved once per fixture: one trusted-roster read, one model
            # choice, every arm and every trial of that fixture.
            selection = select_judged_models(item["fixture"], args)
            hold = _judge_hold(args, item["fixture"], arm_names)
            with _in_place_pair(item.get("in_place")):
                arms = [_run_arm_trials(name, item, registries, args, timestamp,
                                        selection, hold=hold)
                        for name in arm_names]
                if hold is not None:
                    _settle_skill_pair(hold, arms, item, args, timestamp)
                outcomes.append((item, arms))
    except guidance.GuidanceError as exc:
        # Every subprocess sink `_run_arm` can reach — run_setup, run_agent,
        # _nested_repo_diff, judge.score, the objective git checks — checks
        # its timeout on entry and raises this. Named rc 2, never a
        # traceback and never `Runner-level error in arm(s)`.
        print(f"configuration error: {exc}")
        return 2

    # The page a run has always left, byte for byte, for the run it has
    # always been: one flat fixture, one trial per arm. Anything else — more
    # trials, or a nested fixture — gets one section per fixture with `n` in
    # its header.
    single_trial_flat = (len(outcomes) == 1 and outcomes[0][0]["name"] is None
                         and args.trials == 1)
    run_lines = _TELEMETRY.report_lines(args.harness_version)
    if single_trial_flat:
        item, arms = outcomes[0]
        report = _render_report(skill, item["fixture"]["prompt"], timestamp,
                                [arm["results"][0] for arm in arms],
                                args.harness_version, run_lines)
    else:
        report = _render_trials_report(
            skill, timestamp, args.trials,
            [{"label": item["name"] or skill,
              "prompt": item["fixture"]["prompt"], "arms": arms}
             for item, arms in outcomes],
            args.harness_version, run_lines)
    report_path = args.results_dir / skill / timestamp / REPORT_NAME
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # ANY errored trial is a runner-level error and exit 2, as an errored arm
    # always was: an arm whose mean is over the survivors is not the
    # measurement that was asked for.
    errored_arms = []
    for item, arms in outcomes:
        for arm in arms:
            stats = arm["stats"]
            if not stats["errors"]:
                continue
            label = (f"{item['name']}/{arm['arm']}" if item["name"]
                     else arm["arm"])
            if args.trials > 1:
                label += f" ({stats['errors']} of {stats['n']} trials)"
            errored_arms.append(label)
    if _TELEMETRY.stop is not None:
        print(f"Budget stop: {_TELEMETRY.stop['reason']}; trials and judge "
              "calls after it were not started (see usage.budget_stop)")
    if errored_arms:
        print(f"Runner-level error in arm(s): {', '.join(errored_arms)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
