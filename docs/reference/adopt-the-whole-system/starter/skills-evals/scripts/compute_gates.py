#!/usr/bin/env python3
"""Write `gates/latest.json`: the retirement verdict of every skill (#70).

  compute_gates.py --results-tree DIR --roster evals/roster.yml
                   --policy evals/gate-policy.yml --as-of TIME --out FILE
                   [--evals-dir DIR] [--registry NAME=PATH ...]

The results tree is laid out like `persistent/eval-results`:

  results/<skill>/<timestamp>/[<fixture>/]<arm>[/trial-<k>]/summary.json
  routine-results/<run id>/<skill>/<timestamp>/[<fixture>/]<arm>[/trial-<k>]/summary.json

with `report.md` beside each `<timestamp>`'s arms. Paths below the prefix are
parsed by `ingest_routine_results.parse_result_path`, the parser the ingester
validates routine branches with.

GUIDANCE SECTIONS (#124). Runs under `guidance/<section>/` are read too
(`guidance/<section>/<fixture>/`, a real-work fixture run with --section,
is not). An isolation guidance run writes one summary per arm, no trial
files and no aggregate: it is validated by `_validate_guidance`, as one
trial, and each arm gets its role in one of the section's two pairs by its
`mode` (harness/gates.py `GUIDANCE_PAIRS`). The fixtures on main are
`evals/guidance/<section>/<name>/fixture.yaml` with that `section:` (a
directory starting with `_`, such as the delivery canary, is not one), the
summary's `fixture_name` names which, and the section's expected extent
digest comes from the `--guidance` checkout (harness/guidance.py
`content_digests`); without it every guidance cell is insufficient. With a
`guidance:` block in the policy, the document gains `guidance` (a verdict
per section seen, harness/gates.py `guidance_verdict`) and
`guidance_retirement_mode`, and one section at most retires per
evaluation, apart from the one skill.

THE CONTENT ON MAIN. Each skill's fixtures are enumerated from `--evals-dir`
(`run_eval.resolve_fixture_dirs`: `<skill>/fixture.yaml`, or every
`<skill>/<fixture>/fixture.yaml`), and each fixture's expected digest is
`run_eval.fixture_digest`, the function run_eval records it with. The
expected `tested_digest` is the skill's copy in a `--registry` checkout (the
fixture's `registry:` names which; else the one copy across them), digested
as harness/delivery.py digests a registry skill. Only `--registry` flags
count, never an environment or sibling default, so the expected content is
what the caller checked out. No registry, or no unique copy, leaves it
unknown, and every cell of the skill is then insufficient.

A VALIDATED ARM RUN is one arm of one `<timestamp>[/<fixture>]` whose summary
parses as a JSON object naming the skill and carrying an `aggregate` and a
`run` block, whose role is `with` or `without` (the summary's `role`, else
`run_eval._ROLES` of the arm name), whose requested model is recorded
(`run_eval._arm_model`), whose trial summaries parse and have the shape
run_eval writes (`gates.trial_record`), are numbered 1 to n and agree with
the arm summary's `n` and `errors` (one trial and no trial files is `n` 1),
and whose `report.md` exists.
Everything else is counted under `skipped` by reason. The arms are
independent samples (harness/gates.py), so an arm run needs no partner. Runs
under `results/` and `routine-results/` count alike: the gate guards against
honest mistakes, not forged results, since anyone who can write the results
can also remove a skill. The tree is data: it is read, never executed; NaN
and Infinity are refused on read and on write.

AS OF. `--as-of` is required: the time the evaluation stands at (the
workflow passes the results commit's committer time), as
`YYYYMMDDTHHMMSSZ` or an ISO 8601 time with its offset. A run stamped later
is not read (`skipped`: "after as-of"), so an evaluation can be repeated as
of a time.

READS ARE BOUNDED. Every file is read with lstat and O_NOFOLLOW, only when
it is a regular file, and at most the ingester's SIZE_CAPS plus one byte; a
symlink is unreadable and a larger file is skipped with a size reason. An
arm run's JSON and content digest come from one read of its files.

DUPLICATES. Within one cell key (skill, fixture, requested model, role), a
copy of an arm run, matched by its arm summary bytes, its set of trial
summary bytes, or its timestamp, is read once (`skipped`: "duplicate arm
run"), the first by path: a run ingested under both `results/` and
`routine-results/` counts once. The model is in the key because one routine
run may stamp every model's arms with one `--timestamp`.

BARE RUNS ARE REPORTED APART (ADR 0012 decision 3 and its 2026-10-27
addendum). A bare run is the one isolation `without` arm the weekly plan
fires once per (in-place fixture, roster model): a skill arm run whose
summary does not say `pairing: in_place`, whose role is `without`, whose
`<timestamp>[/<fixture>]` holds no `with` arm, and whose fixture on main
runs in place (its fixture.yaml has a top-level `context` mapping,
plan_scheduled_evals.declares_context). It is never pooled: build_index
lists it under `bare_runs`, not `runs`, so no cell, verdict, improve
evidence or cost ratio reads it, and the document lists it under
`bare_runs` for the comparison with the same cell's in-place `with` arm.
An isolation pair (both arms in one run) is left as it was.

ONE SKILL'S FAILURE IS ITS OWN: an exception while classifying a skill
(MemoryError included) makes that skill `insufficient`, naming the
exception's type (never its message), and the others are still decided.

AFTER harness/gates.py's verdict, ONE RETIREMENT PER EVALUATION: of several
skills whose verdict is `retire`, only the one with the most arm runs (then
the first by name) keeps it; the rest are `retire-pending`. Nothing is read
from an earlier gates file: confirmation is harness/gates.py's, at two looks,
and a standing retirement belongs to part 2, which reads the open pull
requests from GitHub.

Deterministic: sorted keys, sorted
walks, no clock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import context  # noqa: E402
import delivery  # noqa: E402
import gates  # noqa: E402
import guidance  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import plan_scheduled_evals  # noqa: E402
import run_eval  # noqa: E402

PREFIXES = ("results", "routine-results")
#: The most bytes read of each file kind: the ingester's own caps.
READ_CAPS = dict(ingest.SIZE_CAPS)


def _refuse_constant(name):
    raise ValueError(f"{name} is not JSON")


def _read_bytes(path: Path, cap: int) -> tuple:
    """(bytes, None) of `path`, a regular file read without following a
    symlink and of at most `cap` bytes; else (None, "unreadable" or
    "oversize"). At most `cap` + 1 bytes are ever read (round 5, B5)."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None, "unreadable"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None, "unreadable"
            chunks, left = [], cap + 1
            while left > 0:
                chunk = os.read(fd, min(left, 1 << 16))
                if not chunk:
                    break
                chunks.append(chunk)
                left -= len(chunk)
        finally:
            os.close(fd)
    except OSError:
        return None, "unreadable"
    data = b"".join(chunks)
    return (None, "oversize") if len(data) > cap else (data, None)


def _parse(data):
    """A JSON object from `data`, or None."""
    if data is None:
        return None
    try:
        doc = json.loads(data.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def _read(path: Path, cap: int = READ_CAPS["summary.json"]):
    """The JSON object in `path` (`_read_bytes`), or None."""
    return _parse(_read_bytes(path, cap)[0])


def _summaries(tree: Path) -> dict:
    """{(run dir, skill, timestamp, fixture, arm): {"summary", "trials"}}."""
    arms: dict = {}
    for prefix in PREFIXES:
        root = tree / prefix
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("summary.json")):
            parts = path.relative_to(root).parts
            head, parts = (parts[:1], parts[1:]) if prefix == "routine-results" else ((), parts)
            try:
                found = ingest.parse_result_path("/".join(parts))
            except ingest.Rejected:
                continue
            if found["key"].split("/")[0] == "guidance" and found["fixture"] is not None:
                continue  # a real-work fixture run with --section
            run_dir = "/".join((prefix, *head, found["key"], found["timestamp"]))
            entry = arms.setdefault((run_dir, found["key"], found["timestamp"],
                                     found["fixture"], found["arm"]),
                                    {"summary": None, "trials": []})
            if found["trial"] is None:
                entry["summary"] = path
            else:
                entry["trials"].append((found["trial"], path))
    return arms


def _fixture_class(evals_dir: Path, skill: str, fixture, trials: list) -> tuple:
    """(judge-carried, has objective checks or None when unknown). A fixture
    moved under `gates.FIXTURE_ALIASES` is read at its new name, which it
    pools with only while the content is the same."""
    path = evals_dir / skill / (fixture or "") / run_eval.FIXTURE_FILE
    alias = gates.FIXTURE_ALIASES.get((skill, fixture or ""))
    if alias is not None and not path.is_file():
        path = evals_dir / skill / alias / run_eval.FIXTURE_FILE
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        doc = None
    if isinstance(doc, dict):
        return (bool(run_eval._judge_is_pairwise(doc) or not doc.get("objective_checks")),
                bool(doc.get("objective_checks")))
    return any(t.get("pairwise") for t in trials), None


def _runs_in_place(evals_dir: Path, skill: str, fixture) -> bool:
    """Whether the skill fixture on main runs in place (ADR 0012), read at
    its `gates.FIXTURE_ALIASES` target as `_fixture_class` reads it."""
    directory = evals_dir / skill / (fixture or "")
    alias = gates.FIXTURE_ALIASES.get((skill, fixture or ""))
    if alias is not None and not (directory / run_eval.FIXTURE_FILE).is_file():
        directory = evals_dir / skill / alias
    return plan_scheduled_evals.declares_context(directory)


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value):
    return value if isinstance(value, str) and value else None


def _validate(tree: Path, key: tuple, entry: dict, evals_dir: Path):
    """(arm run record, None) or (None, the reason it is skipped)."""
    run_dir, skill, timestamp, fixture, arm = key
    if skill.startswith("guidance/"):
        return _validate_guidance(tree, key, entry, evals_dir)
    cap = READ_CAPS["summary.json"]
    raw, problem = _read_bytes(entry["summary"], cap) if entry["summary"] else (None, None)
    if problem == "oversize":
        return None, "arm summary over its size cap"
    summary = _parse(raw)
    if summary is None:
        return None, "no readable arm summary"
    if not isinstance(summary.get("aggregate"), dict):
        return None, "no aggregate block"
    if not isinstance(summary.get("run"), dict):
        return None, "no run block"
    if summary.get("skill") != skill:
        return None, "summary names another skill"
    subject, context_block = summary.get("context_subject"), summary.get("arm_context")
    if any(field in summary and not isinstance(summary[field], str)
           for field in ("role", "fixture_digest")) \
            or ("fixture_digest" in summary
                and not ingest.DIGEST_RE.fullmatch(summary["fixture_digest"])) \
            or any(block is not None and not isinstance(block, dict)
                   for block in (subject, context_block, summary.get("harness"))):
        return None, "malformed arm summary"
    role = summary.get("role") or run_eval._ROLES.get(arm)
    if role not in ("with", "without"):
        return None, "not a with or without arm"
    numbers = sorted(k for k, _ in entry["trials"])
    if numbers != list(range(1, len(numbers) + 1)):
        return None, "trial files are not numbered 1 to n"
    trial_paths = [p for _, p in sorted(entry["trials"])]
    trial_reads = [_read_bytes(p, cap) for p in trial_paths]
    if any(problem == "oversize" for _, problem in trial_reads):
        return None, "trial summary over its size cap"
    trials = [_parse(data) for data, _ in trial_reads] or [summary]
    if any(t is None for t in trials):
        return None, "unreadable trial summary"
    errors = sum(1 for t in trials if t.get("error"))
    if summary.get("n") != len(trials) or not _count(summary.get("n")) or (
            trial_paths and (not _count(summary.get("errors")) or summary["errors"] != errors
                             or ("scored" in summary
                                 and summary["scored"] != len(trials) - errors))):
        return None, "arm summary's n or errors disagree with its trial files"
    model = run_eval._arm_model([summary, *trials])
    if not isinstance(model, str) or not model:
        return None, "no requested arm model"
    report = f"{run_dir}/report.md"
    report_bytes, problem = _read_bytes(tree / report, READ_CAPS["report.md"])
    if problem == "oversize":
        return None, "report over its size cap"
    if report_bytes is None:
        return None, "no report"
    try:
        records = [gates.trial_record(t) for t in trials]
    except gates.MalformedSummary:
        return None, "malformed trial summary"
    context_digest = None
    if isinstance(context_block, dict) and _string(context_block.get("skills_digest")) \
            and _string(context_block.get("guidance_digest")):
        context_digest = (f"{context_block['skills_digest']}:"
                          f"{context_block['guidance_digest']}")
    # Round 5, B6: what both arms of one comparison share, the non-subject
    # skills and the frozen guidance.
    frozen = summary.get("context")
    guidance_base = (frozen.get("guidance") or {}).get("digest") \
        if isinstance(frozen, dict) and isinstance(frozen.get("guidance"), dict) else None
    others = context_block.get("others_digest") if isinstance(context_block, dict) else None
    pair_key = (f"{others}:{guidance_base}"
                if _string(others) and _string(guidance_base) else None)
    carried, has_checks = _fixture_class(evals_dir, skill, fixture, records)
    clis = sorted({doc["harness"]["version"] for doc in (summary, *trials)
                   if isinstance(doc.get("harness"), dict)
                   and _string(doc["harness"].get("version"))})
    return {"skill": skill, "fixture": fixture, "model": model, "role": role,
            "timestamp": timestamp, "path": run_dir, "report": report,
            "in_place": summary.get("pairing") == "in_place",
            "tested_digest": (_string(subject.get("tested_digest"))
                              if isinstance(subject, dict) else None),
            "fixture_digest": summary.get("fixture_digest"),
            "arm_context_digest": context_digest, "pair_key": pair_key, "cli_versions": clis,
            "run": summary["run"],
            # Judged unconditionally only when every trial says so (#70).
            "judge_conditional": not all(t.get("judge_conditional") is False
                                         for t in trials),
            "judge_carried": carried, "fixture_checks": has_checks,
            "trials": records,
            "content": _content(raw, [data for data, _ in trial_reads])}, None


GUIDANCE_MODES = {mode: (pair, role) for pair, roles in gates.GUIDANCE_PAIRS.items()
                  for mode, role in roles.items()}


def _validate_guidance(tree: Path, key: tuple, entry: dict, evals_dir: Path):
    """`_validate` for one arm of an isolation guidance run (module
    docstring): (arm run record, None) or (None, the reason it is skipped).
    The record's `skill` is the section id."""
    run_dir, subject, timestamp, _fixture, _arm = key
    section = subject.split("/", 1)[1]
    raw, problem = (_read_bytes(entry["summary"], READ_CAPS["summary.json"])
                    if entry["summary"] else (None, None))
    if problem == "oversize":
        return None, "arm summary over its size cap"
    summary = _parse(raw)
    if summary is None:
        return None, "no readable arm summary"
    if entry["trials"]:
        return None, "a guidance arm with trial files"
    if summary.get("subject") != "guidance" or summary.get("section") != section:
        return None, "summary names another section"
    if summary.get("mode") not in GUIDANCE_MODES:
        return None, "not a mode of a guidance pair"
    if not isinstance(summary.get("run"), dict):
        return None, "no run block"
    name, digests = summary.get("fixture_name"), summary.get("guidance_digests")
    if not (_string(name) and ingest.NAME_RE.fullmatch(name)) \
            or not (_string(summary.get("fixture_digest"))
                    and ingest.DIGEST_RE.fullmatch(summary["fixture_digest"])) \
            or not isinstance(digests, dict) \
            or not all(_string(digests.get(k)) and ingest.DIGEST_RE.fullmatch(digests[k])
                       for k in ("section", "rest")):
        return None, "no guidance pooling key"
    model = run_eval._arm_model([summary])
    if not isinstance(model, str) or not model:
        return None, "no requested arm model"
    report = f"{run_dir}/report.md"
    report_bytes, problem = _read_bytes(tree / report, READ_CAPS["report.md"])
    if problem == "oversize":
        return None, "report over its size cap"
    if report_bytes is None:
        return None, "no report"
    try:
        records = [gates.trial_record(summary)]
    except gates.MalformedSummary:
        return None, "malformed trial summary"
    pair, role = GUIDANCE_MODES[summary["mode"]]
    carried, has_checks = _fixture_class(evals_dir, subject, name, records)
    harness = summary.get("harness")
    clis = ([harness["version"]] if isinstance(harness, dict)
            and _string(harness.get("version")) else [])
    return {"skill": section, "fixture": name, "model": model, "role": role,
            "pair": pair, "timestamp": timestamp, "path": run_dir, "report": report,
            "tested_digest": digests["section"],
            "fixture_digest": summary["fixture_digest"],
            "arm_context_digest": digests["rest"], "pair_key": digests["rest"],
            "cli_versions": clis, "run": summary["run"],
            "judge_conditional": summary.get("judge_conditional") is not False,
            "judge_carried": carried, "fixture_checks": has_checks,
            "trials": records, "content": _content(raw, [])}, None


def _content(summary: bytes, trials: list) -> tuple:
    """(sha256 of the arm summary's bytes, sha256 over the sorted sha256s of
    its trial summaries' bytes, or None without trial files), from the bytes
    `_validate` read."""
    def digest(data):
        return hashlib.sha256(data).hexdigest()
    trial_set = (hashlib.sha256("\n".join(sorted(map(digest, trials))).encode()).hexdigest()
                 if trials else None)
    return digest(summary), trial_set


def build_index(tree: Path, evals_dir: Path, as_of: str) -> dict:
    """{"runs": [validated arm runs], "bare_runs": [bare runs, never
    pooled], "skipped": {reason: count}, "skills": [every skill key seen]}:
    none stamped after `as_of`, and each arm run once per cell key, by the
    module docstring's DUPLICATES rules. A bare run (module docstring) is
    set apart before those rules, so it never stands in for a pooled run."""
    runs, skipped, skills, sections = [], {}, set(), set()

    def count(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    validated, bare = [], []
    summaries = _summaries(tree)
    # A unit, (run dir, fixture), with a `with` arm by name, valid or not, is
    # a pair: its `without` arm is never bare.
    paired = {(key[0], key[3]) for key in summaries
              if run_eval._ROLES.get(key[4]) == "with"}
    for key, entry in sorted(summaries.items(),
                             key=lambda item: tuple(p or "" for p in item[0])):
        if key[1].startswith("guidance/"):
            sections.add(key[1].split("/", 1)[1])
        else:
            skills.add(key[1])
        record, reason = _validate(tree, key, entry, evals_dir)
        if record is None:
            count(reason)
        else:
            validated.append(record)
    paired |= {(r["path"], r["fixture"]) for r in validated if r["role"] == "with"}
    for record in list(validated):
        if "pair" not in record and record["role"] == "without" and not record["in_place"] \
                and (record["path"], record["fixture"]) not in paired \
                and _runs_in_place(evals_dir, record["skill"], record["fixture"]):
            validated.remove(record)
            if record["timestamp"] > as_of:
                count("after as-of")
            else:
                bare.append(record)
    seen = set()
    for record in sorted(validated, key=lambda r: (r["path"], r["fixture"] or "", r["role"])):
        if record["timestamp"] > as_of:
            count("after as-of")
            continue
        here = (record["skill"], record["fixture"] or "", record["model"], record["role"],
                record.get("pair"))
        marks = {(here, "summary", record["content"][0]), (here, "run", record["timestamp"])}
        if record["content"][1] is not None:
            marks.add((here, "trials", record["content"][1]))
        if marks & seen:
            count("duplicate arm run")
            continue
        seen |= marks
        runs.append(record)
    return {"runs": [r for r in runs if "pair" not in r],
            "bare_runs": bare,
            "guidance_runs": [r for r in runs if "pair" in r],
            "skipped": skipped, "skills": sorted(skills), "sections": sorted(sections)}


def parse_as_of(value: str) -> str:
    """`value` as a results timestamp (`YYYYMMDDTHHMMSSZ`, UTC), or
    ValueError. An ISO 8601 time must carry its offset."""
    if ingest.TIMESTAMP_RE.fullmatch(value):
        return value
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("--as-of needs a time zone offset")
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def flag_registries(values: list[str] | None) -> dict:
    """The registries the caller checked out (`--registry NAME=PATH`), by
    run_eval's own resolution; no environment or sibling default."""
    resolved = run_eval.resolve_registries(values or [], None, REPO_ROOT, None)
    return {name: entry for name, entry in resolved.items()
            if entry["source"] == "--registry flag" and entry["path"].is_dir()}


def tested_digest(registries: dict, skill, url) -> str | None:
    """The skill's `tested_digest` as on main: its one copy in `registries`
    (the fixture's `registry:` one, when it names one), digested as
    harness/delivery.py digests a registry skill; None when it is not
    unique or not readable."""
    if not isinstance(skill, str) or not run_eval._SKILL_NAME_RE.fullmatch(skill):
        return None
    try:
        if url is not None:
            entry = run_eval.registry_for_url(registries, str(url))
            registries = {entry["name"]: entry}
        matches = delivery._registry_matches(registries, None, skill)
        if len(matches) != 1:
            return None
        files, _modes = delivery._tree_files(matches[0][2])
    except (ValueError, OSError, delivery.DeliveryError, context.ContextError):
        return None
    return context._file_digest(files)


def expected_content(evals_dir: Path, skill: str, registries: dict) -> dict:
    """{fixture name, "" for a flat one: {"fixture_digest", "tested_digest"}}
    for every fixture of `skill` on main; {} when it has none."""
    eval_dir = evals_dir / skill
    try:
        directories = run_eval.resolve_fixture_dirs(eval_dir, None) if eval_dir.is_dir() else []
    except (run_eval.FixtureLayoutError, OSError):
        return {}
    out = {}
    for directory in directories:
        try:
            doc = yaml.safe_load((directory / run_eval.FIXTURE_FILE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            doc = None
        doc = doc if isinstance(doc, dict) else {}
        out["" if directory == eval_dir else directory.name] = {
            "fixture_digest": run_eval.fixture_digest(directory),
            "tested_digest": tested_digest(registries, doc.get("skill"), doc.get("registry"))}
    return out


def expected_guidance(evals_dir: Path, section: str, guidance_dir: Path | None) -> dict:
    """{fixture name: {"fixture_digest", "tested_digest"}} for every fixture
    of guidance `section` on main (`evals/guidance/<section>/<name>/`); the
    tested digest is the section's extent in `guidance_dir` (None without
    one, or when the manifest has no such row)."""
    root = evals_dir / "guidance" / section
    if section.startswith("_") or not root.is_dir() or root.is_symlink():
        return {}
    tested = None
    if guidance_dir is not None:
        try:
            row = guidance.find_row(guidance.load_manifest(guidance_dir), section,
                                    guidance_dir)
            tested = guidance.content_digests(guidance_dir, row)["section"]
        except (guidance.GuidanceError, OSError, UnicodeDecodeError):
            tested = None
    out = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        try:
            doc = yaml.safe_load((directory / run_eval.FIXTURE_FILE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(doc, dict) and doc.get("subject") == "guidance" \
                and doc.get("section") == section:
            out[directory.name] = {"fixture_digest": run_eval.fixture_digest(directory),
                                   "tested_digest": tested}
    return out


def finalize(skills: dict) -> None:
    """One retirement per evaluation, in place: of the skills whose verdict
    is `retire`, the one with the most arm runs (then the first by name)
    keeps it."""
    for verdict in skills.values():
        verdict["raw_verdict"] = verdict["verdict"]
    retiring = [name for name, v in sorted(skills.items()) if v["verdict"] == "retire"]
    if not retiring:
        return
    chosen = min(retiring, key=lambda n: (-len(skills[n]["runs_used"]), n))
    for name in retiring:
        if name != chosen:
            skills[name]["verdict"] = "retire-pending"
            skills[name]["reason"] = (f"no benefit is confirmed, but only one skill "
                                      f"retires per evaluation and {chosen} has more runs")


def compute(tree: Path, roster_path: Path, policy_path: Path, evals_dir: Path,
            as_of: str, registries: dict | None = None,
            guidance_dir: Path | None = None) -> dict:
    """The gates document. ValueError for a roster with no arm or a
    malformed one: an empty roster must never mean no cell to fail."""
    policies = gates.load_gate_policy(policy_path)
    policy = policies["skills"]
    roster, problem = run_eval.read_roster(roster_path)
    if problem:
        raise ValueError(problem)
    arm_ids, _judge, _is_arm, malformed = run_eval.roster_models(roster)
    if not arm_ids or malformed:
        raise ValueError("the roster has no arm, or an arm that is not an id")
    models = sorted(arm_ids)
    index = build_index(tree, evals_dir, as_of)
    skills = {}
    for skill in index["skills"]:
        try:
            skills[skill] = gates.retirement(index, models, skill, policy,
                                             expected_content(evals_dir, skill,
                                                              registries or {}))
        except Exception as exc:  # noqa: BLE001 - one skill's failure is its own
            skills[skill] = {"verdict": "insufficient", "cells": [], "runs_used": [],
                             "reason": f"the verdict raised {type(exc).__name__}"}
    finalize(skills)
    doc = {"schema_version": 1, "as_of": as_of,
            "newest_run": max((r["timestamp"] for r in index["runs"]), default=None),
            "policy": policy, "retirement_mode": policy["retirement_mode"],
            "roster_models": models, "registries": sorted((registries or {}).keys()),
            "skipped": index["skipped"], "skills": skills,
            # Reported apart, never in a cell (module docstring, BARE RUNS).
            "bare_runs": [{"skill": r["skill"], "fixture": r["fixture"], "model": r["model"],
                           "timestamp": r["timestamp"], "path": r["path"],
                           "report": gates._link(r["report"])}
                          for r in sorted(index["bare_runs"], key=lambda r: (
                              r["skill"], r["fixture"] or "", r["model"], r["timestamp"],
                              r["path"]))]}
    if policies["guidance"] is not None:
        doc.update(guidance_section_verdicts(index, models, policies["guidance"],
                                             evals_dir, guidance_dir))
    return doc


def guidance_section_verdicts(index: dict, models: list, policy: dict, evals_dir: Path,
                              guidance_dir: Path | None) -> dict:
    """The document's guidance half (#124): a verdict per section seen, one
    retirement at most among them, and the guidance policy and mode."""
    guidance_index = {"runs": index["guidance_runs"]}
    sections = {}
    for section in index["sections"]:
        try:
            sections[section] = gates.guidance_verdict(
                guidance_index, models, section, policy,
                expected_guidance(evals_dir, section, guidance_dir))
        except Exception as exc:  # noqa: BLE001 - one section's failure is its own
            sections[section] = {"verdict": "insufficient", "cells": [], "runs_used": [],
                                 "reason": f"the verdict raised {type(exc).__name__}"}
    finalize(sections)
    return {"guidance": sections, "guidance_policy": policy,
            "guidance_retirement_mode": policy["retirement_mode"],
            "guidance_checkout": guidance_dir is not None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-tree", required=True, type=Path)
    parser.add_argument("--roster", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--evals-dir", type=Path, default=REPO_ROOT / "evals")
    parser.add_argument("--registry", action="append", metavar="NAME=PATH")
    parser.add_argument("--guidance", type=Path, metavar="PATH",
                        help="an _agent-guidance checkout: the guidance sections' "
                             "content on main (#124)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        doc = compute(args.results_tree, args.roster, args.policy, args.evals_dir,
                      parse_as_of(args.as_of), flag_registries(args.registry),
                      args.guidance)
        text = json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (gates.GatePolicyError, ValueError, OSError, RecursionError) as exc:
        print(f"compute_gates: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
