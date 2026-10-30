#!/usr/bin/env python3
"""Validate a routine's `claude/eval-<run id>` branch and stage its results.

ADR 0010 decision 2: the eval routine pushes its results to
`claude/eval-<run id>`, and that branch is UNTRUSTED input to Actions (an AI
run wrote it). `.github/workflows/routine-eval-ingest.yml` runs this script
from the default branch's checkout, never from the pushed branch, and only
ever reads the branch's objects through git plumbing (`git diff --raw`,
`git cat-file`). Nothing from the branch is executed, imported or checked out.

What a branch must look like to be ingested:

  * its name is `claude/eval-<YYYYMMDDTHHMMSSZ>-<6 hex>` (BRANCH_RE);
  * its tip, diffed against its merge-base with the default branch, ADDS
    files and nothing else, every one a regular non-executable file (mode
    100644: no symlink, no submodule, no executable bit), all under ONE
    `eval-results/<run id>/` directory (SOURCE_ROOT), where `<run id>` is
    the branch's own;
  * every added path has one of the shapes the harness writes
    (`parse_result_path`): `<key>/<timestamp>/report.md`,
    `<key>/<timestamp>/[<fixture>/]<arm>[/trial-<k>]/summary.json` and the
    same arm directory's `transcripts/tool_trace.json`. Anything else,
    `raw.json` included, rejects the whole branch;
  * every file is under its size cap, valid UTF-8 with no control
    characters, and passes its schema (`check_summary`, `check_tool_trace`,
    `check_report`).

A rejected branch stages nothing. An accepted one is written to
`<out>/routine-results/<run id>/<path below the run id>`, a deterministic
path the `place` subcommand then copies onto a `persistent/eval-results`
checkout, refusing to overwrite a file that differs.

Subcommands (all output is `key=value` lines of already-validated values,
for $GITHUB_OUTPUT):

  resolve  --event-name NAME --event-path PATH
  validate --repo DIR --base REF --source REF --branch NAME
           [--expect-sha SHA] --out DIR
  place    --staged DIR --tree DIR
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
import re
import subprocess
from fractions import Fraction
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Names and shapes.

RUN_ID = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}"
BRANCH_RE = re.compile(rf"claude/eval-(?P<run_id>{RUN_ID})")
SHA_RE = re.compile(r"[0-9a-f]{40}")
TIMESTAMP_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TRIAL_RE = re.compile(r"trial-(?P<k>[1-9][0-9]?)")
#: The trial cap the harness itself enforces (run_eval.MAX_TRIALS).
MAX_TRIALS = 20
#: Names a fixture or arm segment may not take: each is a file or directory
#: name the harness writes inside a run, so it would make a path ambiguous.
RESERVED_NAMES = frozenset({"report.md", "summary.json", "transcripts",
                            "tool_trace.json", "raw.json"})

#: The one top-level directory the routine pushes results under: its saved
#: prompt (edited 2026-10-06, owner's answer "Pin eval-results/") writes
#: `eval-results/<run id>/` at the repo root and commits nothing else.
SOURCE_ROOT = "eval-results"
#: Branches pushed before the root was pinned, by run id and commit sha,
#: with the root each used. Only claude/eval-20261006T192432Z-93755e needs an entry
#: (`results/<run id>/`); claude/eval-20261006T194256Z-f944ea already used
#: `eval-results/`. claude/eval-20261006T195724Z-a3f2d3 wrote
#: `results/<key>/` with no run id directory and stays rejected.
LEGACY_ROOTS = {("20261006T192432Z-93755e",
                 "c3521ee53809daea4570e5fe80f3ac847959f88a"): "results"}
#: Where accepted results land on persistent/eval-results. Not `results/`:
#: that tree is eval.yml's main-pinned WIF output and the badge input, and
#: routine results are a local exhibit only (ADR 0010, decision 3), not
#: comparable to it until a paired run shows they agree.
DEST_ROOT = "routine-results"

#: Which validated result files are kept long-term on persistent/eval-results
#: (owner's answer, 2026-10-06: "summary + report only"). Every file is
#: validated either way; a file left out of this set is checked and then not
#: copied. `tool_trace.json` is dropped: harness/cli_json.py's tool-trace
#: notes say traces are never committed to persistent/eval-results.
KEEP_LONG_TERM = frozenset({"summary.json", "report.md"})

#: Byte caps, per file kind. The real files are 1.8 to 7.6 KB; the trace cap
#: leaves room for the harness's own 64 KiB event cap (TRACE_MAX_BYTES,
#: measured compact) once pretty-printed.
SIZE_CAPS = {"summary.json": 64 * 1024, "tool_trace.json": 192 * 1024,
             "report.md": 64 * 1024}
MAX_FILES = 256
MAX_TOTAL_BYTES = 4 * 1024 * 1024

#: Bounds for a free-form JSON value (agent usage, aggregates, guidance
#: extras): it may hold any JSON, but only so much of it.
MAX_DEPTH = 8
MAX_STRING = 4096
MAX_ITEMS = 512


class Rejected(Exception):
    """The branch, or one of its files, is not ingestible. The message never
    quotes file content: it names a path and a reason."""


# ---------------------------------------------------------------------------
# Paths.

def parse_result_path(rel: str) -> dict:
    """The parts of one path below `eval-results/<run id>/`, or Rejected.

    Returns {"kind", "key", "timestamp", "fixture", "arm", "trial"}; kind is
    the file's basename, which is also its schema.
    """
    parts = rel.split("/")
    if any(not p for p in parts) or len(parts) > 8:
        raise Rejected(f"{rel!r}: not a result path")
    stamps = [i for i, p in enumerate(parts) if TIMESTAMP_RE.fullmatch(p)]
    if len(stamps) != 1:
        raise Rejected(f"{rel!r}: needs exactly one timestamp segment")
    at = stamps[0]
    key_parts, below = parts[:at], parts[at + 1:]
    prefix_fixture = None
    if key_parts and key_parts[0] == "guidance" and len(key_parts) in (2, 3):
        if not NAME_RE.fullmatch(key_parts[1]):
            raise Rejected(f"{rel!r}: bad guidance section name")
        if len(key_parts) == 3:
            prefix_fixture = key_parts.pop()
            if (not NAME_RE.fullmatch(prefix_fixture) or prefix_fixture in RESERVED_NAMES
                    or TRIAL_RE.fullmatch(prefix_fixture)):
                raise Rejected(f"{rel!r}: bad guidance fixture name")
    elif len(key_parts) != 1 or not NAME_RE.fullmatch(key_parts[0]) \
            or key_parts[0] == "guidance":
        raise Rejected(f"{rel!r}: bad subject key")
    found = {"key": "/".join(key_parts), "timestamp": parts[at],
             "fixture": prefix_fixture, "arm": None, "trial": None}
    if below == ["report.md"]:
        return {"kind": "report.md", **found}
    if below[-1:] == ["summary.json"]:
        kind, arm_path = "summary.json", below[:-1]
    elif below[-2:] == ["transcripts", "tool_trace.json"]:
        kind, arm_path = "tool_trace.json", below[:-2]
    else:
        raise Rejected(f"{rel!r}: not a file the harness publishes")
    if arm_path and TRIAL_RE.fullmatch(arm_path[-1]):
        trial = int(TRIAL_RE.fullmatch(arm_path[-1]).group("k"))
        if trial > MAX_TRIALS:
            raise Rejected(f"{rel!r}: trial number out of range")
        found["trial"] = trial
        arm_path = arm_path[:-1]
    if len(arm_path) not in (1, 2):
        raise Rejected(f"{rel!r}: expected [<fixture>/]<arm>")
    for name in arm_path:
        if not NAME_RE.fullmatch(name) or name in RESERVED_NAMES \
                or TRIAL_RE.fullmatch(name) or TIMESTAMP_RE.fullmatch(name):
            raise Rejected(f"{rel!r}: bad fixture or arm name")
    if len(arm_path) == 2:
        if prefix_fixture is not None:
            raise Rejected(f"{rel!r}: fixture occurs twice")
        found["fixture"] = arm_path[0]
    found["arm"] = arm_path[-1]
    return {"kind": kind, **found}


# ---------------------------------------------------------------------------
# Content.

def decode_text(data: bytes, where: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Rejected(f"{where}: not UTF-8") from None
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text):
        raise Rejected(f"{where}: has a control character")
    return text


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Rejected("duplicate JSON key")
        out[key] = value
    return out


def _no_constant(name):
    raise Rejected(f"non-finite JSON number {name}")


def load_json(text: str, where: str):
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates,
                          parse_constant=_no_constant)
    except Rejected as exc:
        raise Rejected(f"{where}: {exc}") from None
    except (ValueError, RecursionError):
        raise Rejected(f"{where}: not valid JSON") from None


def is_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def bounded(value, where: str, depth: int = 0) -> None:
    """Any JSON value, within MAX_DEPTH / MAX_STRING / MAX_ITEMS."""
    if depth > MAX_DEPTH:
        raise Rejected(f"{where}: nested too deep")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not is_number(value):
            raise Rejected(f"{where}: not a finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise Rejected(f"{where}: string too long")
        return
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise Rejected(f"{where}: list too long")
        for item in value:
            bounded(item, where, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise Rejected(f"{where}: object too large")
        for key, item in value.items():
            if len(key) > 128:
                raise Rejected(f"{where}: key too long")
            bounded(item, where, depth + 1)
        return
    raise Rejected(f"{where}: unexpected JSON type")


def _string(value, where, limit, *, null=False):
    if value is None and null:
        return
    if not isinstance(value, str) or len(value) > limit:
        raise Rejected(f"{where}: expected a string of at most {limit} chars")


def _integer(value, where, low, high, *, null=False):
    if value is None and null:
        return
    if isinstance(value, bool) or not isinstance(value, int) \
            or not low <= value <= high:
        raise Rejected(f"{where}: expected an integer in {low}..{high}")


def _number(value, where, low, high, *, null=False):
    if value is None and null:
        return
    if not is_number(value) or not low <= value <= high:
        raise Rejected(f"{where}: expected a number in {low}..{high}")


def _object(value, where, allowed, required=()):
    if not isinstance(value, dict):
        raise Rejected(f"{where}: expected an object")
    extra = set(value) - set(allowed)
    if extra:
        raise Rejected(f"{where}: unexpected keys {sorted(extra)}")
    missing = set(required) - set(value)
    if missing:
        raise Rejected(f"{where}: missing keys {sorted(missing)}")


MODEL_ID_RE = re.compile(r"[^\s`|]{1,128}")
#: A summary's `judge` when the pair's objective result decided it (#371).
JUDGE_SKIPPED = {"status": "skipped", "reason": "objective-decided"}

SUMMARY_REQUIRED = ("arm", "timestamp", "error", "agent", "objective_checks",
                    "judge", "harness", "models_used", "judge_models_used")
#: `_write_summary`'s own keys, its `extra` keys for a skill fixture (fixture,
#: n, trial, and the trial aggregate's) and a guidance subject's.
SUMMARY_ALLOWED = SUMMARY_REQUIRED + (
    "skill", "fixture", "n", "trial",
    "errors", "scored", "trial_errors", "aggregate",
    "subject", "section", "mode", "bytes", "delivery", "hook_verdict",
    "installed", "decoy", "hook_returncode", "guard",
    "model_tokens", "cross_model", "guidance_violations") + ("pairing", "role", "context",
                                      "context_subject", "arm_context") + ("run", "usage") + (
    "judge_selection", "fixture_digest", "judge_conditional") + (
    # An isolation guidance run's pooling key (#124): its fixture directory's
    # name and the section's and the rest of the guidance's digests.
    "fixture_name", "guidance_digests")
#: A guidance summary's `guidance_digests` (harness/guidance.py content_digests).
GUIDANCE_DIGEST_KEYS = ("section", "rest")
#: A summary's `judge_selection` (#371): which model judges the arm, and the
#: rule that chose it. Null when the run selected no judge.
JUDGE_SELECTION_KEYS = ("model", "rule", "arm_tier", "judge_tier")
JUDGE_SELECTION_RULES = ("tier-above-arm", "roster-judge", "fixture-pin")
AGENT_KEYS = ("usage", "cost_usd", "num_turns", "duration_ms")
#: `harness` keys every summary has carried since #71; `effort` came later
#: and is optional, so an older summary still ingests.
HARNESS_KEYS = ("name", "version", "permission_mode")
HARNESS_ALLOWED = HARNESS_KEYS + ("effort",)
#: run_eval's agent effort levels (guidance.EFFORT_LEVELS; a test pins the
#: two together). Null is the CLI's default.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
#: run_eval's per-model counts (MODEL_TOKEN_FIELDS), its cap on one
#: (MAX_TOKEN_COUNT) and on the models kept (MODELS_USED_MAX).
MODEL_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens")
MAX_TOKEN_COUNT = 10 ** 12
MAX_MODELS = 16
CROSS_MODEL_KEYS = ("model", "canonical_model", "complete", "dropped",
                    "other_share", "threshold", "flagged")
#: run_eval.CROSS_MODEL_SHARE_THRESHOLD: a summary may not move it.
CROSS_MODEL_SHARE_THRESHOLD = 0.5
#: How far a recorded share may sit from the one its counts give.
SHARE_TOLERANCE = 1e-9
#: run_eval.canonical_model_id's rules, repeated here because this script
#: imports nothing from the harness; a test runs both over the same cases.
_CANONICAL_ID = re.compile(r"claude-[a-z0-9]+(?:[.-][a-z0-9]+)*")
_ID_SUFFIX_VARIANT = re.compile(r"\[[^\]]*\]$")
_ID_PREFIX_BEDROCK = re.compile(r"^(?:[a-z]{2,4}\.)?anthropic\.")
_ID_SUFFIX_BEDROCK = re.compile(r"-v\d+(?::\d+)?$")
_ID_SUFFIX_DATE = re.compile(r"(?:-|@)\d{8}$")


def canonical_model_id(model) -> str | None:
    """run_eval.canonical_model_id: the canonical id a model string names,
    or None when it is not a full model id."""
    if not isinstance(model, str):
        return None
    text = _ID_SUFFIX_VARIANT.sub("", model.strip())
    text = _ID_PREFIX_BEDROCK.sub("", text)
    text = _ID_SUFFIX_BEDROCK.sub("", text)
    text = _ID_SUFFIX_DATE.sub("", text)
    return text if _CANONICAL_ID.fullmatch(text) else None


def expected_parts(tokens: dict, own_id: str) -> tuple | None:
    """(tokens on other models, all tokens) that `tokens` gives for the arm
    model `own_id`, as run_eval computes them; None when it cannot be
    told."""
    other = total = 0
    for model, counts in tokens.items():
        values = [counts[name] for name in MODEL_TOKEN_KEYS]
        entry_id = canonical_model_id(counts["canonical_model"] or model)
        if any(v is None for v in values) or entry_id is None:
            return None
        entry_total = sum(values)
        if entry_total > MAX_TOKEN_COUNT:
            return None
        total += entry_total
        if entry_id != own_id:
            other += entry_total
    if not tokens or total <= 0 or total > MAX_TOKEN_COUNT:
        return None
    return other, total


def over_threshold(other, total) -> bool:
    """`other / total` above CROSS_MODEL_SHARE_THRESHOLD, compared exactly
    (rationals, not floats), as run_eval flags it."""
    return Fraction(other) > Fraction(CROSS_MODEL_SHARE_THRESHOLD) * Fraction(total)


def check_model_tokens(value, where: str) -> None:
    """`model_tokens`: at most MAX_MODELS model ids, each with exactly the
    four counts (null, or a finite number in 0..MAX_TOKEN_COUNT) and its
    `canonical_model` (null or a model id)."""
    if not isinstance(value, dict) or len(value) > MAX_MODELS:
        raise Rejected(f"{where}: model_tokens must be an object of at most "
                       f"{MAX_MODELS} models")
    for model, counts in value.items():
        if not MODEL_ID_RE.fullmatch(model):
            raise Rejected(f"{where}: model_tokens key is not a model id")
        keys = MODEL_TOKEN_KEYS + ("canonical_model",)
        _object(counts, f"{where}: model_tokens entry", keys, keys)
        for name in MODEL_TOKEN_KEYS:
            _number(counts[name], f"{where}: model_tokens count", 0,
                    MAX_TOKEN_COUNT, null=True)
        canonical = counts["canonical_model"]
        if canonical is not None and not (isinstance(canonical, str)
                                          and MODEL_ID_RE.fullmatch(canonical)):
            raise Rejected(f"{where}: model_tokens canonical_model is not a "
                           "model id")


def check_cross_model(value, where: str, tokens) -> None:
    """`cross_model`: exactly CROSS_MODEL_KEYS, each its own type, and
    consistent with itself and with `tokens`, the summary's (already
    checked) `model_tokens`: completeness agrees with `dropped`, an
    incomplete accounting has no share or flag, the threshold is the fixed
    one, the flag is the share against it, and the share is the one the
    counts give."""
    _object(value, f"{where}: cross_model", CROSS_MODEL_KEYS, CROSS_MODEL_KEYS)
    for name in ("model", "canonical_model"):
        model = value[name]
        if model is not None and not (isinstance(model, str)
                                      and MODEL_ID_RE.fullmatch(model)):
            raise Rejected(f"{where}: cross_model.{name} is not a model id")
    if not isinstance(value["complete"], bool):
        raise Rejected(f"{where}: cross_model.complete is not a bool")
    _integer(value["dropped"], f"{where}: cross_model.dropped", 0, MAX_ITEMS)
    _number(value["other_share"], f"{where}: cross_model.other_share", 0, 1,
            null=True)
    _number(value["threshold"], f"{where}: cross_model.threshold", 0, 1)
    if value["flagged"] is not None and not isinstance(value["flagged"], bool):
        raise Rejected(f"{where}: cross_model.flagged is not a bool or null")

    complete, share = value["complete"], value["other_share"]
    if complete and (value["dropped"] or not tokens):
        raise Rejected(f"{where}: cross_model is complete with entries dropped "
                       "or none recorded")
    if not complete and (share is not None or value["flagged"] is not None):
        raise Rejected(f"{where}: cross_model has a share or flag for an "
                       "incomplete accounting")
    if value["threshold"] != CROSS_MODEL_SHARE_THRESHOLD:
        raise Rejected(f"{where}: cross_model.threshold is not "
                       f"{CROSS_MODEL_SHARE_THRESHOLD}")
    own_id = canonical_model_id(value["model"])
    if value["canonical_model"] != own_id:
        raise Rejected(f"{where}: cross_model.canonical_model does not match "
                       "its model")
    # Both the share and the flag are checked against the COUNTS, never the
    # flag against the supplied share: a share within the float tolerance of
    # the true one can sit on the other side of the threshold.
    parts = expected_parts(tokens, own_id) if complete and own_id else None
    if (share is None) != (parts is None) or (
            share is not None
            and abs(share - parts[0] / parts[1]) > SHARE_TOLERANCE):
        raise Rejected(f"{where}: cross_model.other_share does not match the "
                       "model_tokens counts")
    expected_flag = None if parts is None else over_threshold(*parts)
    if value["flagged"] != expected_flag:
        raise Rejected(f"{where}: cross_model.flagged does not match the "
                       "model_tokens counts")


#: An in-place arm's record (ADR 0012, run_eval._in_place_record): all five
#: keys or none. A summary without them is an isolation run.
IN_PLACE_KEYS = ("pairing", "role", "context", "context_subject", "arm_context")
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}")
CONTEXT_KEYS = ("repository", "revision", "digest", "lock_digest", "sources",
                "guidance", "measurements")
SOURCE_KEYS = ("registry", "revision", "digest", "bundles")
CONTEXT_GUIDANCE_KEYS = ("revision", "digest", "bytes", "sections", "hook_digest")
MEASUREMENT_KEYS = ("guidance_bytes", "skill_catalog_bytes", "skill_payload_bytes")
#: run_eval's hard budget ceiling (context.BUDGET_MAX), the largest a
#: measurement can be.
MAX_CONTEXT_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_ITEMS = 64
SUBJECT_KEYS = {
    "skill": ("kind", "registry", "bundle", "skill", "action", "deployed_digest",
              "tested_digest", "bytes"),
    "guidance": ("kind", "section", "file", "heading", "action", "start_char",
                 "end_char", "start_byte", "end_byte", "bytes", "deployed_digest",
                 "tested_digest", "tested_bytes"),
}
ARM_CONTEXT_KEYS = ("skills", "skills_digest", "guidance_bytes",
                    "guidance_digest", "subject_present")
#: Optional in arm_context (#70): the digest of the arm's skills other than
#: the subject, which compute_gates pairs the two arms by. Older summaries
#: lack it; their cells cannot be paired.
ARM_CONTEXT_OPTIONAL = ("others_digest",)


def _pattern(value, where, pattern, *, null=False):
    if value is None and null:
        return
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Rejected(f"{where}: malformed")


def check_in_place(doc, where: str, parts: dict) -> None:
    """An in-place arm's context record: bounded, well-formed, and
    consistent with its arm, its key and itself."""
    present = [key for key in IN_PLACE_KEYS if key in doc]
    if not present:
        return
    if len(present) != len(IN_PLACE_KEYS):
        raise Rejected(f"{where}: in-place keys come together")
    if doc["pairing"] != "in_place":
        raise Rejected(f"{where}: pairing must be in_place")
    if doc["role"] not in ("with", "without") or \
            doc["arm"].startswith("with_") != (doc["role"] == "with"):
        raise Rejected(f"{where}: role does not match its arm")

    context = doc["context"]
    _object(context, f"{where}: context", CONTEXT_KEYS, CONTEXT_KEYS)
    _pattern(context["repository"], f"{where}: context.repository", REPOSITORY_RE)
    _pattern(context["revision"], f"{where}: context.revision", SHA_RE)
    _pattern(context["digest"], f"{where}: context.digest", DIGEST_RE)
    _pattern(context["lock_digest"], f"{where}: context.lock_digest", DIGEST_RE,
             null=True)
    sources = context["sources"]
    if not isinstance(sources, list) or len(sources) > MAX_CONTEXT_ITEMS \
            or (context["lock_digest"] is None) != (not sources):
        raise Rejected(f"{where}: context.sources must be a short list, empty "
                       "exactly when there is no lock")
    seen_sources, seen_bundles = set(), set()
    for source in sources:
        _object(source, f"{where}: context source", SOURCE_KEYS, SOURCE_KEYS)
        _pattern(source["registry"], f"{where}: source registry", REPOSITORY_RE)
        _pattern(source["revision"], f"{where}: source revision", SHA_RE)
        _pattern(source["digest"], f"{where}: source digest", DIGEST_RE)
        identity = (source["registry"], source["revision"], source["digest"])
        if identity in seen_sources:
            raise Rejected(f"{where}: duplicate context source")
        seen_sources.add(identity)
        bundles = source["bundles"]
        if not isinstance(bundles, list) or not bundles \
                or len(bundles) > MAX_CONTEXT_ITEMS:
            raise Rejected(f"{where}: source bundles must be a short list")
        for bundle in bundles:
            _object(bundle, f"{where}: bundle", ("name", "digest"), ("name", "digest"))
            _pattern(bundle["name"], f"{where}: bundle name", NAME_RE)
            if bundle["name"] in seen_bundles:
                raise Rejected(f"{where}: duplicate context bundle")
            seen_bundles.add(bundle["name"])
            _pattern(bundle["digest"], f"{where}: bundle digest", DIGEST_RE)
    proof = context["guidance"]
    _object(proof, f"{where}: context.guidance", CONTEXT_GUIDANCE_KEYS,
            CONTEXT_GUIDANCE_KEYS)
    _pattern(proof["revision"], f"{where}: guidance revision", SHA_RE)
    _pattern(proof["digest"], f"{where}: guidance digest", DIGEST_RE)
    _pattern(proof["hook_digest"], f"{where}: hook digest", DIGEST_RE, null=True)
    if proof["hook_digest"] is None:
        raise Rejected(f"{where}: in-place context requires a hook digest")
    _integer(proof["bytes"], f"{where}: guidance bytes", 1, MAX_CONTEXT_BYTES)
    if not isinstance(proof["sections"], list) or \
            len(proof["sections"]) > MAX_CONTEXT_ITEMS:
        raise Rejected(f"{where}: guidance sections must be a short list")
    for section in proof["sections"]:
        _pattern(section, f"{where}: guidance section", NAME_RE)
    if len(proof["sections"]) != len(set(proof["sections"])):
        raise Rejected(f"{where}: duplicate guidance section")
    measurements = context["measurements"]
    _object(measurements, f"{where}: measurements", MEASUREMENT_KEYS,
            MEASUREMENT_KEYS)
    for key in MEASUREMENT_KEYS:
        _integer(measurements[key], f"{where}: {key}", 0, MAX_CONTEXT_BYTES)
    if measurements["guidance_bytes"] != proof["bytes"]:
        raise Rejected(f"{where}: guidance bytes disagree")

    subject = doc["context_subject"]
    kind = "guidance" if parts["key"].startswith("guidance/") else "skill"
    if not isinstance(subject, dict) or subject.get("kind") != kind:
        raise Rejected(f"{where}: context_subject kind does not match its key")
    _object(subject, f"{where}: context_subject", SUBJECT_KEYS[kind], SUBJECT_KEYS[kind])
    if subject["action"] not in ("removed", "added"):
        raise Rejected(f"{where}: context_subject.action is not removed or added")
    _pattern(subject["tested_digest"], f"{where}: tested digest", DIGEST_RE)
    _pattern(subject["deployed_digest"], f"{where}: deployed digest", DIGEST_RE,
             null=True)
    # Removed: a candidate may replace the deployed subject in with.
    # Added: nothing deployed.
    if (subject["action"] == "removed") != (subject["deployed_digest"] is not None):
        raise Rejected(f"{where}: context_subject digests do not match its action")
    _integer(subject["bytes"], f"{where}: context_subject.bytes", 0, MAX_CONTEXT_BYTES)
    if kind == "skill":
        _pattern(subject["registry"], f"{where}: subject registry", REPOSITORY_RE)
        _pattern(subject["bundle"], f"{where}: subject bundle", NAME_RE)
        if subject["action"] == "removed" and not any(
                source["registry"] == subject["registry"] and any(
                    bundle["name"] == subject["bundle"] for bundle in source["bundles"])
                for source in sources):
            raise Rejected(f"{where}: removed subject is not in a deployed source bundle")
        if subject["skill"] != doc.get("skill"):
            raise Rejected(f"{where}: context_subject.skill does not match its key")
    else:
        if subject["section"] != doc.get("section"):
            raise Rejected(f"{where}: context_subject.section does not match its key")
        _integer(subject["tested_bytes"], f"{where}: tested extent bytes", 1, MAX_CONTEXT_BYTES)
        _string(subject["file"], f"{where}: subject file", 256)
        _string(subject["heading"], f"{where}: subject heading", 256)
        for key in ("start_char", "end_char", "start_byte", "end_byte"):
            _integer(subject[key], f"{where}: subject {key}", 0, MAX_CONTEXT_BYTES)
        if not (subject["start_byte"] <= subject["end_byte"]
                and subject["start_char"] <= subject["end_char"]
                and subject["bytes"] == subject["end_byte"] - subject["start_byte"]):
            raise Rejected(f"{where}: context_subject extent is inconsistent")

    arm = doc["arm_context"]
    _object(arm, f"{where}: arm_context", ARM_CONTEXT_KEYS + ARM_CONTEXT_OPTIONAL,
            ARM_CONTEXT_KEYS)
    if "others_digest" in arm:
        _pattern(arm["others_digest"], f"{where}: others digest", DIGEST_RE)
    _integer(arm["skills"], f"{where}: arm_context.skills", 0, 4096)
    if doc["arm"] == "with_skill" and doc["role"] == "with" \
            and arm["skills"] == 0:
        raise Rejected(f"{where}: with_skill arm has no installed skill")
    _pattern(arm["skills_digest"], f"{where}: skills digest", DIGEST_RE)
    _pattern(arm["guidance_digest"], f"{where}: arm guidance digest", DIGEST_RE)
    _integer(arm["guidance_bytes"], f"{where}: arm guidance bytes", 0,
             MAX_CONTEXT_BYTES)
    if kind == "skill" and arm["guidance_digest"] != proof["digest"]:
        raise Rejected(f"{where}: skill treatment changed guidance")
    if kind == "skill" and arm["guidance_bytes"] != proof["bytes"]:
        raise Rejected(f"{where}: skill treatment changed guidance size")
    if subject["action"] == "added" and doc["role"] == "without" \
            and arm["guidance_digest"] != proof["digest"]:
        raise Rejected(f"{where}: without arm changed guidance for an added subject")
    if kind == "guidance":
        wanted_bytes = proof["bytes"]
        if subject["action"] == "removed":
            wanted_bytes += (subject["tested_bytes"] - subject["bytes"] if doc["role"] == "with"
                             else -subject["bytes"])
        elif doc["role"] == "with":
            wanted_bytes += 1 + subject["tested_bytes"]
        if arm["guidance_bytes"] != wanted_bytes:
            raise Rejected(f"{where}: guidance size does not match its subject extent")
    if arm["subject_present"] is not (doc["role"] == "with"):
        raise Rejected(f"{where}: arm_context.subject_present does not match its role")



GUIDANCE_RULE_IDS = {
    "unpinned_actions": "pinning-github-actions",
    "event_input_in_run": "data-exposure-in-ci",
    "push_without_verification": "git-push-does-not-mean-commit-exists",
}


def check_guidance_violations(value, where: str) -> None:
    _object(value, f"{where}: guidance_violations", ("schema_version", "rules"), ("schema_version", "rules"))
    _integer(value["schema_version"], f"{where}: guidance violation version", 1, 1)
    _object(value["rules"], f"{where}: guidance rules", GUIDANCE_RULE_IDS, GUIDANCE_RULE_IDS)
    for name, section_id in GUIDANCE_RULE_IDS.items():
        entry = value["rules"][name]
        keys = ("section_id", "status", "count", "observed_count")
        _object(entry, f"{where}: guidance rule {name}", keys, keys)
        if entry["section_id"] != section_id or entry["status"] not in ("known", "unknown"):
            raise Rejected(f"{where}: guidance rule identity or status is invalid")
        _integer(entry["observed_count"], f"{where}: observed guidance violations", 0, 100_000_000)
        _integer(entry["count"], f"{where}: guidance violations", 0, 100_000_000, null=True)
        if (entry["status"] == "known" and entry["count"] != entry["observed_count"]) or (
                entry["status"] == "unknown" and entry["count"] is not None):
            raise Rejected(f"{where}: guidance violation count disagrees with its status")

# ---------------------------------------------------------------------------
# `run` and `usage` (#370 item 1): run_eval's `run_block` and `_RunTelemetry`.
# Optional, and only together, so a summary written before them still
# ingests. Every value is checked, not only every key: a field the harness
# only ever writes as null must be null here (`USAGE_ALWAYS_NULL`; a role's
# figures are null exactly when `reasons` says so). The one free-form value is a
# snapshot's `info`, the CLI's own object, which is bounded. The constants are
# run_eval's own; a test pins them equal.

RUN_CHOICES = {"billing": ("api", "subscription"),
               "runner": ("actions", "routine", "workstation"),
               "location": ("cloud", "local")}
#: The fields a `--run-*` flag can set, and per field the environment
#: sources the harness reads with the value each implies (None: the
#: variable's own).
RUN_FLAGGED = (*RUN_CHOICES, "id")
RUN_ENV = {"billing": {},
           "runner": {"env:GITHUB_ACTIONS": "actions"},
           "location": {"env:CLAUDE_CODE_REMOTE_SESSION_ID": "cloud"},
           "id": {"env:GITHUB_RUN_ID": None},
           "session_id": {"env:CLAUDE_CODE_REMOTE_SESSION_ID": None}}
RUN_NULLABLE = (*RUN_ENV, "harness_commit", "cli_version")
RUN_KEYS = ("schema_version", *RUN_NULLABLE, "source", "reasons")
RUN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
USAGE_ROLES = ("agent", "judge", "guard")
USAGE_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
METER_FRACTION_RE = re.compile(r"(?:\.[0-9]{1,9})?Z")
LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")
OUTCOME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_METER_SNAPSHOTS = 12
MAX_METER_WINDOWS = 16
MAX_USAGE_CALLS = 32
MAX_COUNT = 10 ** 6
WEEK_MS = 7 * 24 * 3600 * 1000
#: Fields the harness writes as null and nothing else, until something
#: measures them; `concurrent_runs` is computed after ingest, not before.
USAGE_ALWAYS_NULL = ("concurrent_runs", "limit_promotion", "price_table_version")
USAGE_NULLABLE = ("ended_at", "meter", "meter_delta", *USAGE_ALWAYS_NULL)
USAGE_KEYS = ("schema_version", "started_at", *USAGE_NULLABLE,
              "other_account_activity", "asserted_quiet_by", "outer_session",
              "measured", "reasons")
#: `budget_stop` (#68): present only on a run that stopped starting work for
#: its weekly allowance budget, so every summary written before it still
#: ingests. run_eval.budget_stop_reading's keys and reasons.
USAGE_OPTIONAL = ("budget_stop",)
BUDGET_STOP_KEYS = ("reason", "utilization", "moved", "limit")
BUDGET_STOP_REASONS = ("per_run_cap", "account_above")
METER_KEYS = ("snapshot_count", "unreadable_events", "not_sampled")
NOT_SAMPLED = ("judge", "guard", "run_end")
TRIAL_METER_KEYS = ("snapshots", "omitted")
SNAPSHOT_KEYS = ("at", "at_source", "call", "info")
DELTA_KEYS = ("confounded", "scope", "from_at", "to_at", "uncovered", "windows")
UNCOVERED_KEYS = ("calls_before_first_snapshot", "calls_from_last_snapshot")
WINDOW_KEYS = ("from", "to", "delta", "window_reset")
MEASURED_NULLABLE = ("started_at", "ended_at", "calls", "meter")
MEASURED_KEYS = ("scope", *MEASURED_NULLABLE, *USAGE_ROLES, "reasons")
CALL_KEYS = ("role", "index", "started_at", "ended_at", "duration_ms", "outcome")
ROLE_NULLABLE = ("num_turns", "cost_usd", "cost_basis", "cache_creation", "models")
ROLE_KEYS = ("calls", "duration_ms", *ROLE_NULLABLE, "reasons")
ROLE_MODEL_KEYS = (*MODEL_TOKEN_KEYS, "cost_usd")
CACHE_SPLIT_KEYS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


def _reasons(block: dict, where: str, nullable) -> None:
    """`reasons` names exactly the fields of `nullable` that are null."""
    reasons = block["reasons"]
    if not isinstance(reasons, dict) \
            or set(reasons) != {name for name in nullable if block[name] is None}:
        raise Rejected(f"{where}: reasons must name exactly its null fields")
    for why in reasons.values():
        if not isinstance(why, str) or not 0 < len(why) <= 512:
            raise Rejected(f"{where}: a reason must be a short string")


def _time(value, where: str, *, null=False, fraction=False):
    """A real UTC time as the harness writes it (a snapshot's may carry the
    CLI's fraction of a second), returned for comparing; None when null."""
    if value is None and null:
        return None
    if not isinstance(value, str):
        raise Rejected(f"{where}: expected a UTC time")
    head, tail = value[:19] + "Z", value[19:]
    if not (tail == "Z" or (fraction and METER_FRACTION_RE.fullmatch(tail))):
        raise Rejected(f"{where}: expected a UTC time")
    try:
        if datetime.strptime(head, USAGE_TIME_FORMAT).strftime(
                USAGE_TIME_FORMAT) != head:
            raise ValueError
    except ValueError:
        raise Rejected(f"{where}: expected a UTC time") from None
    return value


def _span(block: dict, where: str, *, start_null=False, end_null=True) -> None:
    """`started_at` and `ended_at`, in that order when both are there."""
    started = _time(block["started_at"], f"{where}.started_at", null=start_null)
    ended = _time(block["ended_at"], f"{where}.ended_at", null=end_null)
    if started and ended and ended < started:
        raise Rejected(f"{where}: ends before it starts")


def check_run(run, where: str) -> None:
    where = f"{where}: run"
    _object(run, where, RUN_KEYS, RUN_KEYS)
    _integer(run["schema_version"], f"{where}.schema_version", 1, 1)
    for field, allowed in RUN_CHOICES.items():
        if run[field] is not None and run[field] not in allowed:
            raise Rejected(f"{where}.{field} is not a known value")
    for field in ("id", "session_id"):
        _pattern(run[field], f"{where}.{field}", RUN_NAME_RE, null=True)
    _pattern(run["harness_commit"], f"{where}.harness_commit", SHA_RE, null=True)
    _string(run["cli_version"], f"{where}.cli_version", 64, null=True)
    if run["cli_version"] == "":
        raise Rejected(f"{where}.cli_version is empty")
    _object(run["source"], f"{where}.source", RUN_ENV, RUN_ENV)
    for field, observed in RUN_ENV.items():
        source, value = run["source"][field], run[field]
        # A source is the flag, where the field has one, or an environment
        # variable the harness reads for THAT field, with the value it implies.
        known = (source is None and value is None) or (
            value is not None and (
                (source == "flag" and field in RUN_FLAGGED)
                or (isinstance(source, str) and source in observed
                    and observed[source] in (None, value))))
        if not known:
            raise Rejected(f"{where}.source does not match its fields")
    _reasons(run, where, RUN_NULLABLE)


def _check_role(block, where: str) -> None:
    _object(block, where, ROLE_KEYS, ROLE_KEYS)
    _integer(block["calls"], f"{where}.calls", 0, MAX_COUNT)
    _integer(block["duration_ms"], f"{where}.duration_ms", 0, WEEK_MS)
    _integer(block["num_turns"], f"{where}.num_turns", 0, 10 ** 7, null=True)
    _number(block["cost_usd"], f"{where}.cost_usd", 0, 10 ** 6, null=True)
    _pattern(block["cost_basis"], f"{where}.cost_basis", LABEL_RE, null=True)
    models = block["models"]
    if models is not None and (not isinstance(models, dict)
                               or len(models) > MAX_MODELS):
        raise Rejected(f"{where}.models must be an object of at most {MAX_MODELS}")
    for model, counts in (models or {}).items():
        if not MODEL_ID_RE.fullmatch(model):
            raise Rejected(f"{where}.models: bad model id")
        _object(counts, f"{where}.models", ROLE_MODEL_KEYS, ROLE_MODEL_KEYS)
        for name in ROLE_MODEL_KEYS:
            _number(counts[name], f"{where}.models", 0, MAX_TOKEN_COUNT, null=True)
    if block["cache_creation"] is not None:
        _object(block["cache_creation"], f"{where}.cache_creation",
                CACHE_SPLIT_KEYS, CACHE_SPLIT_KEYS)
        for name in CACHE_SPLIT_KEYS:
            _number(block["cache_creation"][name], f"{where}.cache_creation", 0,
                    MAX_TOKEN_COUNT)
    _reasons(block, where, ROLE_NULLABLE)


def _check_meter(usage, where: str, billing) -> None:
    """The run-level `meter` and `meter_delta`, and the trial's own snapshots
    under `measured.meter` (stored once each, in the trial that recorded
    them: an arm aggregate lists none, and an API-billed run has none)."""
    meter, delta = usage["meter"], usage["meter_delta"]
    if (meter is None) != (billing == "api") or (meter is None and delta is not None):
        raise Rejected(f"{where}.meter is null exactly when the run is API-billed")
    if meter is not None:
        _object(meter, f"{where}.meter", METER_KEYS, METER_KEYS)
        _integer(meter["snapshot_count"], f"{where}.meter.snapshot_count", 0, MAX_COUNT)
        _integer(meter["unreadable_events"], f"{where}.meter", 0, MAX_COUNT)
        _object(meter["not_sampled"], f"{where}.meter.not_sampled", NOT_SAMPLED)
        for why in meter["not_sampled"].values():
            if not isinstance(why, str) or not 0 < len(why) <= 512:
                raise Rejected(f"{where}.meter.not_sampled: not a short string")
    measured = usage["measured"]
    if not isinstance(measured, dict) or "meter" not in measured:
        raise Rejected(f"{where}.measured must carry its meter record")
    mine, where_mine = measured["meter"], f"{where}.measured.meter"
    if (mine is None) != (meter is None or measured.get("scope") == "arm"):
        raise Rejected(f"{where_mine} is null exactly for an arm aggregate or "
                       "an API-billed run")
    if mine is not None:
        _object(mine, where_mine, TRIAL_METER_KEYS, TRIAL_METER_KEYS)
        _integer(mine["omitted"], f"{where_mine}.omitted", 0, MAX_COUNT)
        if not isinstance(mine["snapshots"], list) \
                or len(mine["snapshots"]) > MAX_METER_SNAPSHOTS:
            raise Rejected(f"{where_mine}.snapshots must be a short list")
        if len(mine["snapshots"]) + mine["omitted"] > meter["snapshot_count"]:
            raise Rejected(f"{where_mine} holds more snapshots than its run")
        for snapshot in mine["snapshots"]:
            _object(snapshot, f"{where_mine} snapshot", SNAPSHOT_KEYS, SNAPSHOT_KEYS)
            _time(snapshot["at"], f"{where_mine} snapshot at", fraction=True)
            call = snapshot["call"]
            _object(call, f"{where_mine} snapshot call", ("role", "index"),
                    ("role", "index"))
            _integer(call["index"], f"{where_mine} snapshot call", 0, MAX_COUNT)
            if call["role"] not in USAGE_ROLES \
                    or snapshot["at_source"] not in ("cli_message", "call_start") \
                    or not isinstance(snapshot["info"], dict):
                raise Rejected(f"{where_mine} snapshot is not one the harness writes")
            bounded(snapshot["info"], f"{where_mine} snapshot info")
    if delta is not None:
        where = f"{where}.meter_delta"
        _object(delta, where, DELTA_KEYS, DELTA_KEYS)
        if delta["confounded"] is not True or delta["scope"] != "account-wide":
            raise Rejected(f"{where} must be labeled confounded and account-wide")
        if meter["snapshot_count"] < 2:
            raise Rejected(f"{where} needs two snapshots")
        for field in ("from_at", "to_at"):
            _time(delta[field], f"{where}.{field}", fraction=True)
        _object(delta["uncovered"], f"{where}.uncovered", UNCOVERED_KEYS,
                UNCOVERED_KEYS)
        for field in UNCOVERED_KEYS:
            _integer(delta["uncovered"][field], f"{where}.uncovered", 0, MAX_COUNT)
        windows = delta["windows"]
        if not isinstance(windows, dict) or not 0 < len(windows) <= MAX_METER_WINDOWS:
            raise Rejected(f"{where}.windows must name one to "
                           f"{MAX_METER_WINDOWS} windows")
        for name, window in windows.items():
            if not LABEL_RE.fullmatch(name):
                raise Rejected(f"{where}.windows: bad window name")
            _object(window, f"{where} window", WINDOW_KEYS, WINDOW_KEYS)
            for field in ("from", "to"):
                _number(window[field], f"{where} window {field}", 0, MAX_TOKEN_COUNT)
            _number(window["delta"], f"{where} window delta", -MAX_TOKEN_COUNT,
                    MAX_TOKEN_COUNT, null=True)
            if (window["delta"] is None) is not window["window_reset"]:
                raise Rejected(f"{where}: a window has no delta exactly when it "
                               "reset")
            if window["delta"] is not None and abs(
                    window["delta"] - (window["to"] - window["from"])) > 1e-6:
                raise Rejected(f"{where}: a window's delta is not its change")


def check_budget_stop(stop, where: str, billing) -> None:
    """`usage.budget_stop`: exactly its four keys, a reason the harness
    writes, and consistent with itself: the limit it names is a budget value
    (above 0, at most 1) and the reading it gives reached it. An API-billed
    run has no meter, so it never stops for one."""
    where = f"{where}.budget_stop"
    _object(stop, where, BUDGET_STOP_KEYS, BUDGET_STOP_KEYS)
    if billing == "api":
        raise Rejected(f"{where}: an API-billed run has no meter to stop on")
    if stop["reason"] not in BUDGET_STOP_REASONS:
        raise Rejected(f"{where}.reason is not one the harness writes")
    _number(stop["utilization"], f"{where}.utilization", 0, MAX_TOKEN_COUNT)
    _number(stop["moved"], f"{where}.moved", -MAX_TOKEN_COUNT, MAX_TOKEN_COUNT)
    _number(stop["limit"], f"{where}.limit", 0, 1)
    if stop["limit"] == 0:
        raise Rejected(f"{where}.limit must be above 0")
    reached = stop["utilization"] if stop["reason"] == "account_above" else stop["moved"]
    if reached < stop["limit"]:
        raise Rejected(f"{where}: the reading does not reach its limit")


def check_usage(usage, where: str, run: dict) -> None:
    where = f"{where}: usage"
    _object(usage, where, USAGE_KEYS + USAGE_OPTIONAL, USAGE_KEYS)
    if "budget_stop" in usage:
        check_budget_stop(usage["budget_stop"], where, run["billing"])
    _integer(usage["schema_version"], f"{where}.schema_version", 1, 1)
    _span(usage, where)
    _check_meter(usage, where, run["billing"])
    for field in USAGE_ALWAYS_NULL:
        if usage[field] is not None:
            raise Rejected(f"{where}.{field} is not measured by any harness yet")
    quiet = usage["asserted_quiet_by"]
    _pattern(quiet, f"{where}.asserted_quiet_by", RUN_NAME_RE, null=True)
    if usage["other_account_activity"] != ("unknown" if quiet is None
                                           else "asserted_quiet"):
        raise Rejected(f"{where}.other_account_activity does not match who "
                       "asserted it")
    session = usage["outer_session"]
    _object(session, f"{where}.outer_session", ("id", "usage", "note"),
            ("id", "usage", "note"))
    _pattern(session["id"], f"{where}.outer_session.id", RUN_NAME_RE, null=True)
    if session["usage"] is not None or session["note"] != "not measured":
        raise Rejected(f"{where}.outer_session is not measured by any harness yet")
    _reasons(usage, where, USAGE_NULLABLE)

    measured, where = usage["measured"], f"{where}.measured"
    _object(measured, where, MEASURED_KEYS, MEASURED_KEYS)
    if measured["scope"] not in ("trial", "arm"):
        raise Rejected(f"{where}.scope is not a known value")
    _span(measured, where, start_null=True)
    calls = measured["calls"]
    if calls is not None and (not isinstance(calls, list)
                              or len(calls) > MAX_USAGE_CALLS):
        raise Rejected(f"{where}.calls must be a bounded list")
    for call in calls or ():
        _object(call, f"{where} call", CALL_KEYS, CALL_KEYS)
        _integer(call["index"], f"{where} call index", 0, MAX_USAGE_CALLS)
        _integer(call["duration_ms"], f"{where} call duration", 0, WEEK_MS)
        _pattern(call["outcome"], f"{where} call outcome", OUTCOME_RE)
        _span(call, f"{where} call", end_null=False)
        if call["role"] not in USAGE_ROLES:
            raise Rejected(f"{where}: call of an unknown role")
    for role in USAGE_ROLES:
        _check_role(measured[role], f"{where}.{role}")
        if calls is not None and measured[role]["calls"] != sum(
                call["role"] == role for call in calls):
            raise Rejected(f"{where}.{role}.calls does not match the calls listed")
    _reasons(measured, where, MEASURED_NULLABLE)


def check_summary(doc, where: str, parts: dict) -> None:
    _object(doc, where, SUMMARY_ALLOWED, SUMMARY_REQUIRED)
    if doc["arm"] != parts["arm"]:
        raise Rejected(f"{where}: arm does not match its directory")
    if doc["timestamp"] != parts["timestamp"]:
        raise Rejected(f"{where}: timestamp does not match its directory")
    if doc.get("fixture") != parts["fixture"]:
        raise Rejected(f"{where}: fixture does not match its directory")
    if parts["key"].startswith("guidance/"):
        if doc.get("subject") != "guidance" \
                or doc.get("section") != parts["key"].split("/", 1)[1] \
                or "skill" in doc:
            raise Rejected(f"{where}: guidance subject does not match its key")
    elif doc.get("skill") != parts["key"] or "subject" in doc:
        raise Rejected(f"{where}: skill does not match its key")
    if parts["trial"] is not None:
        if doc.get("trial") != parts["trial"] or "n" in doc:
            raise Rejected(f"{where}: trial does not match its directory")
    elif "guidance_digests" in doc:
        # An isolation guidance run writes one trial per arm and no `n` (#124).
        if not parts["key"].startswith("guidance/") or "n" in doc or "trial" in doc:
            raise Rejected(f"{where}: an isolation guidance arm carries n or trial")
    else:
        _integer(doc.get("n"), f"{where}: n", 1, MAX_TRIALS)
        if "trial" in doc:
            raise Rejected(f"{where}: trial outside a trial directory")

    error = doc["error"]
    if error is not None:
        _object(error, f"{where}: error", ("type", "detail"), ("type",))
        _string(error["type"], f"{where}: error.type", 64)
        _string(error.get("detail", ""), f"{where}: error.detail", MAX_STRING)

    agent = doc["agent"]
    if agent is not None:
        _object(agent, f"{where}: agent", AGENT_KEYS)
        _number(agent.get("cost_usd"), f"{where}: agent.cost_usd", 0, 1000,
                null=True)
        _integer(agent.get("num_turns"), f"{where}: agent.num_turns", 0,
                 100_000, null=True)
        _integer(agent.get("duration_ms"), f"{where}: agent.duration_ms", 0,
                 7 * 24 * 3600 * 1000, null=True)
        bounded(agent.get("usage"), f"{where}: agent.usage")

    checks = doc["objective_checks"]
    if checks is not None:
        if not isinstance(checks, list) or len(checks) > 200:
            raise Rejected(f"{where}: objective_checks must be a short list")
        for check in checks:
            _object(check, f"{where}: objective check", ("id", "passed",
                                                         "detail"),
                    ("id", "passed"))
            bounded(check["id"], f"{where}: objective check id")
            if not isinstance(check["passed"], bool):
                raise Rejected(f"{where}: objective check passed not a bool")
            _string(check.get("detail", ""), f"{where}: objective detail",
                    MAX_STRING)

    judge = doc["judge"]
    if judge is not None:
        if not isinstance(judge, dict):
            raise Rejected(f"{where}: judge must be an object")
        bounded(judge, f"{where}: judge")
        # A judge the harness skipped because the objective result decided
        # the pair (#371) is exactly this block, and nothing else with a
        # `status` is one.
        if "status" in judge and judge != JUDGE_SKIPPED:
            raise Rejected(f"{where}: judge status is not a skip the harness "
                           "writes")
        if "overall" in judge:
            _number(judge["overall"], f"{where}: judge.overall", 0, 10)
        if isinstance(judge.get("dimensions"), list):
            for dim in judge["dimensions"]:
                if not isinstance(dim, dict):
                    raise Rejected(f"{where}: judge dimension not an object")
                if "score" in dim:
                    _number(dim["score"], f"{where}: judge score", 0, 10)

    harness = doc["harness"]
    _object(harness, f"{where}: harness", HARNESS_ALLOWED, HARNESS_KEYS)
    _string(harness["name"], f"{where}: harness.name", 64)
    _string(harness["version"], f"{where}: harness.version", 64, null=True)
    _string(harness["permission_mode"], f"{where}: harness.permission_mode",
            32, null=True)
    if harness.get("effort") is not None \
            and harness["effort"] not in EFFORT_LEVELS:
        raise Rejected(f"{where}: harness.effort is not a known level")
    if ("run" in doc) != ("usage" in doc):
        raise Rejected(f"{where}: run and usage come together")
    if "run" in doc:
        check_run(doc["run"], where)
        check_usage(doc["usage"], where, doc["run"])
    if ("model_tokens" in doc) != ("cross_model" in doc):
        raise Rejected(f"{where}: model_tokens and cross_model come together")
    if "model_tokens" in doc:
        check_model_tokens(doc["model_tokens"], where)
        check_cross_model(doc["cross_model"], where, doc["model_tokens"])

    for field in ("models_used", "judge_models_used"):
        models = doc[field]
        if not isinstance(models, list) or len(models) > 16 or not all(
                isinstance(m, str) and MODEL_ID_RE.fullmatch(m)
                for m in models):
            raise Rejected(f"{where}: {field} must be a short list of model ids")
    selection = doc.get("judge_selection")
    if selection is not None:
        _object(selection, f"{where}: judge_selection", JUDGE_SELECTION_KEYS,
                JUDGE_SELECTION_KEYS)
        if not isinstance(selection["model"], str) \
                or not MODEL_ID_RE.fullmatch(selection["model"]):
            raise Rejected(f"{where}: judge_selection.model is not a model id")
        if selection["rule"] not in JUDGE_SELECTION_RULES:
            raise Rejected(f"{where}: judge_selection.rule is not a known rule")
        for field in ("arm_tier", "judge_tier"):
            _string(selection[field], f"{where}: judge_selection.{field}", 64,
                    null=True)
    # A skipped judge made no call, so nothing beside it may name one.
    if judge == JUDGE_SKIPPED and (
            doc["judge_models_used"] or "usage" in doc
            and doc["usage"]["measured"]["judge"]["calls"] != 0):
        raise Rejected(f"{where}: a skipped judge beside a judge model or a "
                       "judge call")

    if "fixture_digest" in doc:
        _pattern(doc["fixture_digest"], f"{where}: fixture_digest", DIGEST_RE)
    if "judge_conditional" in doc and not isinstance(doc["judge_conditional"], bool):
        raise Rejected(f"{where}: judge_conditional must be true or false")
    if "fixture_name" in doc:
        _pattern(doc["fixture_name"], f"{where}: fixture_name", NAME_RE)
    if "guidance_digests" in doc:
        _object(doc["guidance_digests"], f"{where}: guidance_digests",
                GUIDANCE_DIGEST_KEYS, GUIDANCE_DIGEST_KEYS)
        for field in GUIDANCE_DIGEST_KEYS:
            _pattern(doc["guidance_digests"][field],
                     f"{where}: guidance_digests.{field}", DIGEST_RE)
    for field in ("errors", "scored"):
        if field in doc:
            _integer(doc[field], f"{where}: {field}", 0, MAX_TRIALS)
    check_in_place(doc, where, parts)
    if "guidance_violations" in doc:
        check_guidance_violations(doc["guidance_violations"], where)
    for field in ("trial_errors", "aggregate", "section", "mode", "bytes",
                  "delivery", "hook_verdict", "installed", "decoy",
                  "hook_returncode", "guard", "subject"):
        if field in doc:
            bounded(doc[field], f"{where}: {field}")


TRACE_KEYS = ("schema_version", "limits", "calls", "events", "omitted_events")
#: `cli_json.bounded_tool_trace`'s `complete` came later and is optional, so
#: an older trace still ingests.
TRACE_ALLOWED = TRACE_KEYS + ("complete",)
LIMIT_KEYS = ("input_chars", "output_chars", "max_bytes")
EVENT_KEYS = {
    "tool_use": ("call", "kind", "id", "name", "input", "input_chars",
                 "subagent"),
    "tool_result": ("call", "kind", "id", "is_error", "output",
                    "output_chars", "subagent"),
}
#: `cli_json.tool_events` adds this flag, always true, only to an event whose
#: text it clipped.
EVENT_FLAGS = {"tool_use": "input_incomplete", "tool_result": "output_incomplete"}
#: `_clip` appends "..." to a cut string, and a string too large to redact
#: becomes "<N chars, too large to redact>"; both fit within this slack.
CLIP_SLACK = 48


def check_tool_trace(doc, where: str) -> None:
    _object(doc, where, TRACE_ALLOWED, TRACE_KEYS)
    if doc["schema_version"] != 1 or isinstance(doc["schema_version"], bool):
        raise Rejected(f"{where}: unknown schema_version")
    if "complete" in doc and not isinstance(doc["complete"], bool):
        raise Rejected(f"{where}: complete not a bool")
    _object(doc["limits"], f"{where}: limits", LIMIT_KEYS, LIMIT_KEYS)
    input_cap = doc["limits"]["input_chars"]
    output_cap = doc["limits"]["output_chars"]
    _integer(input_cap, f"{where}: limits.input_chars", 1, 1000)
    _integer(output_cap, f"{where}: limits.output_chars", 1, 1000)
    _integer(doc["limits"]["max_bytes"], f"{where}: limits.max_bytes", 1,
             SIZE_CAPS["tool_trace.json"])
    _integer(doc["calls"], f"{where}: calls", 0, 100)
    _integer(doc["omitted_events"], f"{where}: omitted_events", 0, 1_000_000)
    if doc.get("complete") is True and doc["omitted_events"]:
        raise Rejected(f"{where}: complete with omitted events")
    events = doc["events"]
    if not isinstance(events, list) or len(events) > 5000:
        raise Rejected(f"{where}: events must be a bounded list")
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in EVENT_KEYS:
            raise Rejected(f"{where}: event of unknown kind")
        keys = EVENT_KEYS[event["kind"]]
        flag = EVENT_FLAGS[event["kind"]]
        _object(event, f"{where}: event", keys + (flag,), keys)
        if flag in event and event[flag] is not True:
            raise Rejected(f"{where}: event.{flag} not true")
        _integer(event["call"], f"{where}: event.call", 0, 100)
        # `cli_json._tool_use_id` writes null for an id it does not recognize.
        _string(event["id"], f"{where}: event.id", 128, null=True)
        if not isinstance(event["subagent"], bool):
            raise Rejected(f"{where}: event.subagent not a bool")
        if event["kind"] == "tool_use":
            _string(event["name"], f"{where}: event.name", 100 + CLIP_SLACK)
            _string(event["input"], f"{where}: event.input",
                    input_cap + CLIP_SLACK)
            _integer(event["input_chars"], f"{where}: event.input_chars", 0,
                     10**9)
        else:
            if not isinstance(event["is_error"], bool):
                raise Rejected(f"{where}: event.is_error not a bool")
            _string(event["output"], f"{where}: event.output",
                    output_cap + CLIP_SLACK)
            _integer(event["output_chars"], f"{where}: event.output_chars", 0,
                     10**9)


def check_report(text: str, where: str, parts: dict) -> None:
    first = text.split("\n", 1)[0]
    skill = parts["key"].split("/")[-1]
    if not first.startswith("# Eval report: ") or skill not in first:
        raise Rejected(f"{where}: report does not open with its eval title")


# ---------------------------------------------------------------------------
# Git, read-only.

def git(repo: str, *args: str) -> bytes:
    # Raw git is fine here (#343): the harness's own results checkout, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                            check=False)
    if result.returncode != 0:
        # Never echo git's stderr: it can quote a ref or a path from the
        # untrusted branch.
        raise Rejected(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def resolve_commit(repo: str, ref: str) -> str:
    sha = git(repo, "rev-parse", "--verify", "--quiet",
              f"{ref}^{{commit}}").decode().strip()
    if not SHA_RE.fullmatch(sha):
        raise Rejected("could not resolve a commit")
    return sha


def added_files(repo: str, base: str, tip: str,
                modes: tuple[str, ...] = ("100644",)) -> list[tuple[str, str, str]]:
    """(path, mode, blob id) of every file `tip` changes against `base`,
    rejecting any change that is not the addition of a regular file whose
    mode is one of `modes` (results: non-executable only; a scaffolded seed
    also keeps git's executable bit, scripts/scaffold_real_work.py)."""
    raw = git(repo, "diff", "--raw", "-z", "--no-abbrev", "--no-renames",
              "--no-ext-diff", "--no-textconv", base, tip)
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise Rejected("unexpected git diff output")
    out = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        try:
            _, new_mode, _, new_oid, status = meta.decode("ascii").split(" ")
            name = path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise Rejected("a changed path is not plain UTF-8") from None
        if status != "A":
            raise Rejected(f"{name!r}: modified or deleted (status {status}); "
                           "only additions are ingestible")
        if new_mode not in modes:
            raise Rejected(f"{name!r}: mode {new_mode} is not an accepted regular file")
        if not SHA_RE.fullmatch(new_oid):
            raise Rejected(f"{name!r}: unexpected blob id")
        out.append((name, new_mode, new_oid))
    return out


# ---------------------------------------------------------------------------
# Subcommands.

def validate(repo: str, base: str, source: str, branch: str,
             expect_sha: str | None, out: Path) -> dict:
    match = BRANCH_RE.fullmatch(branch)
    if not match:
        raise Rejected("branch name is not claude/eval-<run id>")
    run_id = match.group("run_id")
    tip = resolve_commit(repo, source)
    if expect_sha is not None:
        if not SHA_RE.fullmatch(expect_sha):
            raise Rejected("expected sha is not a 40-hex commit id")
        if tip != expect_sha:
            raise Rejected("the branch moved since the push that triggered "
                           "this run; that newer push triggers its own ingest")
    base_sha = resolve_commit(repo, base)
    merge_base = git(repo, "merge-base", base_sha, tip).decode().strip()
    if not SHA_RE.fullmatch(merge_base):
        raise Rejected("the branch shares no history with the default branch")

    files = added_files(repo, merge_base, tip)
    if not files:
        raise Rejected("the branch adds no files")
    if len(files) > MAX_FILES:
        raise Rejected(f"the branch adds more than {MAX_FILES} files")
    prefix = f"{LEGACY_ROOTS.get((run_id, tip), SOURCE_ROOT)}/{run_id}/"

    staged, total = [], 0
    for name, _, oid in files:
        if not name.startswith(prefix):
            raise Rejected(f"{name!r}: not under {prefix!r}")
        rel = name[len(prefix):]
        where = repr(name)
        parts = parse_result_path(rel)
        kind = parts["kind"]
        size = int(git(repo, "cat-file", "-s", oid).decode().strip())
        if size > SIZE_CAPS[kind]:
            raise Rejected(f"{name!r}: {size} bytes is over the {kind} cap")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise Rejected("the branch's results are over the total size cap")
        data = git(repo, "cat-file", "blob", oid)
        if len(data) != size:
            raise Rejected(f"{name!r}: size changed while reading")
        text = decode_text(data, where)
        if kind == "summary.json":
            check_summary(load_json(text, where), where, parts)
        elif kind == "tool_trace.json":
            check_tool_trace(load_json(text, where), where)
        else:
            check_report(text, where, parts)
        if kind in KEEP_LONG_TERM:
            staged.append((rel, data))
    if not any(rel.endswith("summary.json") for rel, _ in staged) \
            and "summary.json" in KEEP_LONG_TERM:
        raise Rejected("the branch carries no summary.json")

    dest = out / DEST_ROOT / run_id
    if out.exists() and any(out.iterdir()):
        raise Rejected("the staging directory is not empty")
    for rel, data in staged:
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"run_id": run_id, "sha": tip, "files": str(len(staged))}


def place(staged: Path, tree: Path) -> dict:
    """Copy every staged file onto `tree`. A file already there with the
    same bytes is left alone; one with different bytes is a conflict and
    nothing is written. No path component in `tree` may be a symlink."""
    plan = []
    tree_root = tree.resolve()
    for path in sorted(p for p in staged.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise Rejected("staged file is a symlink")
        rel = path.relative_to(staged)
        target = tree / rel
        cursor = tree
        for part in rel.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise Rejected(f"{str(rel)!r}: the destination path has a symlink")
        if not target.resolve().is_relative_to(tree_root):
            raise Rejected(f"{str(rel)!r}: escapes the destination tree")
        data = path.read_bytes()
        if target.exists():
            if not target.is_file() or target.read_bytes() != data:
                raise Rejected(f"{str(rel)!r}: already ingested with different content")
            continue
        plan.append((target, data))
    for target, data in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"changed": "true" if plan else "false", "added": str(len(plan))}


def resolve(event_name: str, event_path: str, branch_re: re.Pattern = BRANCH_RE,
            shape: str = "claude/eval-<run id>") -> dict:
    """The branch (and, for a push, the sha) to ingest, from the event file
    the runner wrote, as validated values only. `branch_re`'s named groups
    are returned beside them (the run id here; the scaffold gate passes its
    own pattern, scripts/scaffold_real_work.py)."""
    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError):
        raise Rejected("could not read the event file") from None
    if not isinstance(event, dict):
        raise Rejected("event is not an object")
    if event_name == "workflow_run":
        run = event.get("workflow_run")
        repo = (event.get("repository") or {}).get("full_name")
        if not isinstance(run, dict) or run.get("event") != "push":
            raise Rejected("the triggering run was not a push")
        if (run.get("head_repository") or {}).get("full_name") != repo \
                or not isinstance(repo, str):
            raise Rejected("the triggering push is not from this repository")
        branch, sha = run.get("head_branch"), run.get("head_sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise Rejected("the triggering push has no valid head sha")
    elif event_name == "workflow_dispatch":
        branch, sha = (event.get("inputs") or {}).get("branch"), ""
    else:
        raise Rejected("unsupported event")
    if not isinstance(branch, str) or not branch_re.fullmatch(branch):
        raise Rejected(f"branch is not {shape}")
    return {"branch": branch, "sha": sha, **branch_re.fullmatch(branch).groupdict()}


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
    p = sub.add_parser("place")
    p.add_argument("--staged", required=True, type=Path)
    p.add_argument("--tree", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            result = resolve(args.event_name, args.event_path)
        elif args.command == "validate":
            result = validate(args.repo, args.base, args.source, args.branch,
                              args.expect_sha or None, args.out)
        else:
            result = place(args.staged, args.tree)
    except Rejected as exc:
        # Paths are already repr()-escaped; this is the backstop, so no
        # message can start a new log line (a `::workflow-command::`).
        message = "".join(c if c.isprintable() else repr(c)[1:-1]
                          for c in str(exc))
        print(f"rejected: {message}", file=sys.stderr)
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
