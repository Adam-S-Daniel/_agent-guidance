#!/usr/bin/env python3
"""Build the board's read-only snapshot from two immutable git trees.

Results are untrusted data: never check them out, import them, or execute them.
Unknown measurements remain null. Only --out is written.
Inventory total counts named skill and subject:any fixtures; dirs counts named
skills plus distinct repository-relative directories containing any fixtures.
Guidance fixtures and the propagation/delivery probes are separate. An invalid
inventory fixture makes total and dirs unknown while retaining valid skill rows.
minFixtures is the proposal policy, not the smallest observed fixture count.
Guidance coverage comes from an optional immutable manifest tree, counting
sections without a linked fixture.
"""

from __future__ import annotations

import sys

# Importing a sibling from a normal CLI must not create __pycache__ beside it.
sys.dont_write_bytecode = True

import argparse
import ast
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

import yaml

try:
    from .ingest_routine_results import Rejected, parse_result_path
except ImportError:
    from ingest_routine_results import Rejected, parse_result_path

REPOSITORY = "https://github.com/example-adopter/skills-evals"
GUIDANCE_REPOSITORY = "https://github.com/example-adopter/_agent-guidance"
GUIDANCE_MANIFEST = "agents-md/eval-coverage.yml"
METRICS = ("obj", "total", "judge", "tokens", "turns", "cost")
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
PROBE_FIXTURES = {
    "evals/propagation/fixture.yaml": "propagation",
    "evals/guidance-bridge-canary/fixture.yaml": "guidance-bridge-canary",
}


class FeedError(Exception):
    """A sanitized failure of the input references or git plumbing."""


def git(repo, *args):
    # Raw git is fine here (#343): this repository's own checkout, never an agent-touched
    # workspace, so harness/workspace_git.py does not apply.
    process = subprocess.run(["git", "-C", str(repo), *args],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode:
        raise FeedError("cannot read git input")
    return process.stdout


def resolve(repo, ref):
    raw = git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    sha = raw.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise FeedError("invalid input commit")
    return sha


def link(kind, sha, path=""):
    return f"{REPOSITORY}/{kind}/{sha}" + (
        "/" + "/".join(quote(segment, safe="") for segment in path.split("/"))
        if path else "")


class Tree:
    def __init__(self, repo, ref):
        self.repo, self.sha = repo, resolve(repo, ref)
        self.files = {}
        for entry in git(repo, "ls-tree", "-r", "-l", "-z", self.sha).split(b"\0"):
            if not entry:
                continue
            header, path = entry.split(b"\t", 1)
            mode, kind, object_id, size = header.split()
            if kind == b"blob" and mode in (b"100644", b"100755"):
                try:
                    self.files[path.decode("utf-8")] = (object_id.decode("ascii"), int(size))
                except (UnicodeError, ValueError):
                    continue

    def text(self, path, cap=65536):
        item = self.files.get(path)
        if item is None or item[1] > cap:
            return None
        try:
            text = git(self.repo, "cat-file", "blob", item[0]).decode("utf-8")
            if any(ord(c) < 32 and c not in "\n\r\t" or ord(c) == 127 for c in text):
                return None
            return text
        except UnicodeError:
            return None


def bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ValueError("data exceeds bounds")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("non-string key")
            bounded(child, depth + 1, budget)
    elif isinstance(value, list):
        for child in value:
            bounded(child, depth + 1, budget)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite data")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("unsupported data")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class UniqueYaml(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return unique_pairs((loader.construct_object(key), loader.construct_object(value))
                        for key, value in node.value)


UniqueYaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def document(text, yaml_format=False):
    if text is None:
        return None
    try:
        value = (yaml.load(text, Loader=UniqueYaml) if yaml_format else
                 json.loads(text, object_pairs_hook=unique_pairs))
        bounded(value)
        return value
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        return None


def mapping(value):
    return value if isinstance(value, dict) else {}


def number(value):
    try:
        return value if (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value >= 0) else None
    except OverflowError:
        return None


def mean(value):
    return number(mapping(value).get("mean"))


def token_total(usage, aggregated=False):
    usage = mapping(usage)
    getter = mean if aggregated else number
    values = [getter(usage.get(key)) for key in TOKEN_KEYS[:2]]
    values += [getter(usage[key]) for key in TOKEN_KEYS[2:] if key in usage]
    if aggregated:
        blocks = [mapping(usage[key]) for key in TOKEN_KEYS if key in usage]
        counts = [block["n"] for block in blocks if "n" in block]
        if any("n_missing" in block and block["n_missing"] != 0 for block in blocks) \
                or any(number(count) is None for count in counts) \
                or len(set(counts)) > 1:
            return None
    if not all(value is not None for value in values):
        return None
    try:
        return number(sum(values))
    except OverflowError:
        return None


def metrics(summary):
    result = dict.fromkeys((*METRICS, "n"))
    if not isinstance(summary, dict):
        return result
    if "aggregate" in summary:
        aggregate = mapping(summary["aggregate"])
        objective = mapping(aggregate.get("objective"))
        efficiency = mapping(aggregate.get("efficiency"))
        result.update(obj=number(objective.get("mean_passed")),
                      total=number(objective.get("mean_total")),
                      judge=mean(mapping(aggregate.get("judge")).get("overall")),
                      tokens=token_total(efficiency, True),
                      turns=mean(efficiency.get("num_turns")),
                      cost=mean(aggregate.get("cost_usd")), n=number(summary.get("n")))
    else:
        checks = summary.get("objective_checks")
        if isinstance(checks, list) and all(isinstance(check, dict) and
                isinstance(check.get("passed"), bool) for check in checks):
            result.update(obj=sum(check["passed"] for check in checks), total=len(checks))
        agent = mapping(summary.get("agent"))
        result.update(judge=number(mapping(summary.get("judge")).get("overall")),
                      tokens=token_total(agent.get("usage")),
                      turns=number(agent.get("num_turns")), cost=number(agent.get("cost_usd")),
                      n=number(summary.get("n", 1)))
    n = result["n"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 \
            or ("n" not in summary and not any(result[metric] is not None for metric in METRICS)):
        result["n"] = None
    return result


def model_names(summary):
    if not isinstance(summary, dict):
        return []
    models = summary.get("models_used")
    if models is None:
        models = [mapping(summary.get("agent")).get("model")]
    return [model for model in models if isinstance(model, str) and model.strip()] \
        if isinstance(models, list) else []


def notes(summary):
    summary = mapping(summary)
    error_type = mapping(summary.get("error")).get("type")
    return [error_type] if isinstance(error_type, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]{0,63}", error_type) else []


def result_path(path):
    parts = path.split("/")
    if parts[0] not in ("results", "eval-results", "routine-results"):
        return None
    start = 2 if len(parts) > 1 and RUN_ID.fullmatch(parts[1]) else 1
    try:
        parsed = parse_result_path("/".join(parts[start:]))
        if parsed["kind"] not in ("summary.json", "report.md"):
            return None
        # Lexical timestamp matching alone accepts impossible calendar dates.
        datetime.strptime(parsed["timestamp"], "%Y%m%dT%H%M%SZ")
        return "/".join(parts[:start]), parsed
    except (Rejected, ValueError):
        return None


def results(tree):
    runs = {}
    for path in sorted(tree.files):
        found = result_path(path)
        if found is None:
            continue
        source, parsed = found
        identity = (source, parsed["key"], parsed["timestamp"])
        run = runs.setdefault(identity, {"report_path": None, "fixtures": {}})
        if parsed["kind"] == "report.md":
            run["report_path"] = path
        else:
            fixture = run["fixtures"].setdefault(parsed["fixture"], {})
            fixture.setdefault(parsed["arm"], []).append((parsed["trial"], path))
    rows = []
    for (source, key, stamp), run in sorted(runs.items()):
        report_path = run["report_path"]
        report = tree.text(report_path) if report_path else None
        for fixture, arms in (run["fixtures"] or {None: {}}).items():
            summaries, rendered = [], []
            for arm, paths in sorted(arms.items()):
                aggregates = [path for trial, path in paths if trial is None]
                selected = aggregates[:1] or [path for _, path in paths]
                docs = [document(tree.text(path)) for path in selected]
                summaries += docs
                measured = [metrics(doc) for doc in docs]
                if len(measured) == 1:
                    values = measured[0]
                else:
                    values = dict.fromkeys(METRICS)
                    for metric in METRICS:
                        samples = [item[metric] for item in measured]
                        if not all(sample is not None for sample in samples):
                            continue
                        try:
                            values[metric] = number(sum(samples) / len(samples))
                        except OverflowError:
                            continue
                    values["n"] = len(measured) if all(item["n"] is not None for item in measured) else None
                rendered.append({"arm": arm, **values})
            names = sorted({name for doc in summaries for name in model_names(doc)})
            note = sorted({value for doc in summaries for value in notes(doc)})
            rows.append({"fixture": key + ("/" + fixture if fixture else ""), "ts": stamp,
                         "runs": None, "model": ", ".join(names) or None, "report": report,
                         "url": link("blob", tree.sha, report_path) if report is not None else None,
                         "note": "; ".join(note) or None, "arms": rendered,
                         "_source": source})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["fixture"]].append(row)
    newest = []
    for fixture, entries in sorted(grouped.items()):
        row = dict(max(entries, key=lambda item: (item["ts"], item["_source"])))
        row["runs"] = len(entries)
        del row["_source"]
        newest.append(row)
    points = []
    for row in sorted(rows, key=lambda item: (item["ts"], item["_source"])):
        # The reference fixture's trend, across its move from flat to nested.
        if row["fixture"] in ("workflow-path-audit",
                              "workflow-path-audit/required-check-routing"):
            arms = {arm["arm"]: arm for arm in row["arms"]}
            points.append({"ts": row["ts"], **{name: (
                {metric: arms[name][metric] for metric in METRICS} if name in arms else None)
                for name in ("with_skill", "without_skill")}})
    return newest, points


def minimum_fixtures(tree):
    """Read the single literal policy assignment without executing input code."""
    source = tree.text("scripts/propose_skill_edit.py", 1024 * 1024)
    if source is None:
        return None
    try:
        parsed = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    writes = [node for node in ast.walk(parsed) if isinstance(node, ast.Name)
              and node.id == "MIN_FIXTURES" and isinstance(node.ctx, (ast.Store, ast.Del))]
    assignments = [node for node in parsed.body if
                   isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == "MIN_FIXTURES" or
                   isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                   and node.target.id == "MIN_FIXTURES"]
    other_bindings = [node for node in ast.walk(parsed) if
                      isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and node.name == "MIN_FIXTURES" or isinstance(node, ast.alias)
                      and (node.asname or node.name.split(".")[0]) == "MIN_FIXTURES"
                      or isinstance(node, ast.ExceptHandler) and node.name == "MIN_FIXTURES"]
    if len(writes) != 1 or len(assignments) != 1 or other_bindings:
        return None
    value = assignments[0].value
    return value.value if isinstance(value, ast.Constant) and type(value.value) is int \
        and value.value > 0 else None


def guidance_inventory(main, repo, ref):
    url = f"{GUIDANCE_REPOSITORY}/blob/main/{GUIDANCE_MANIFEST}"
    result = {"sections": None, "gap": None, "url": url,
              "note": "Guidance manifest repository was not supplied."}
    if repo is None:
        return result
    try:
        tree = Tree(repo, ref)
        result["url"] = f"{GUIDANCE_REPOSITORY}/blob/{tree.sha}/{GUIDANCE_MANIFEST}"
        entries = document(tree.text(GUIDANCE_MANIFEST, 1024 * 1024), True)
    except (FeedError, OSError, ValueError, UnicodeError):
        result["note"] = "Guidance manifest repository or ref is unavailable."
        return result
    result["note"] = "Guidance manifest is missing or malformed."
    if not isinstance(entries, list) or not entries:
        return result
    ids, gap = set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            return result
        section = entry.get("id")
        if not isinstance(section, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", section) \
                or section in ids or entry.get("status") not in ("gap", "covered", "skipped"):
            return result
        ids.add(section)
        fixture = entry.get("fixture")
        if fixture is None:
            gap += 1
            continue
        if not isinstance(fixture, str):
            return result
        parts = fixture.rstrip("/").split("/")
        if len(parts) < 2 or parts[0] != "evals" or any(
                not part or part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", part)
                for part in parts):
            return result
        path = "/".join(parts)
        if parts[-1] != "fixture.yaml":
            path += "/fixture.yaml"
        if path not in main.files:
            gap += 1
    result.update(sections=len(entries), gap=gap,
                  note="Gap counts manifest sections without a fixture linked in the main tree, including skipped sections.")
    return result


def inventory(tree, rows, results_sha, guidance_repo=None, guidance_ref="origin/main"):
    skills, any_dirs = defaultdict(list), defaultdict(list)
    uncertain = False
    for path in sorted(tree.files):
        parts = path.split("/")
        if parts[0] != "evals" or any(part in ("seed", "references") for part in parts):
            continue
        if parts[-1] != "fixture.yaml":
            continue
        fixture = document(tree.text(path, 1024 * 1024), True)
        if not isinstance(fixture, dict):
            uncertain = True
            continue
        # These named probes have dedicated runners, not a skill/any subject.
        # Match both identities so a name-only or conflicting fixture stays invalid.
        if path in PROBE_FIXTURES and fixture.get("name") == PROBE_FIXTURES[path] \
                and not any(key in fixture for key in ("subject", "skill", "section")):
            continue
        subject = fixture.get("subject", "skill")
        if subject == "guidance":
            continue
        if subject == "any":
            if "skill" in fixture or "section" in fixture:
                uncertain = True
                continue
            any_dirs["/".join(parts[1:-1])].append(path)
            continue
        skill = fixture.get("skill")
        if subject != "skill" or not isinstance(skill, str) or not re.fullmatch(
                r"[A-Za-z0-9._-]+", skill) or skill in (".", ".."):
            uncertain = True
            continue
        skills[skill].append(path)
    rendered = []
    for name, paths in sorted(skills.items()):
        parents = [path.split("/")[:-1] for path in paths]
        common = []
        for segments in zip(*parents):
            if len(set(segments)) != 1:
                break
            common.append(segments[0])
        subject = "/".join(common[:2] if all(len(parent) > 1 for parent in parents)
                           and len({parent[1] for parent in parents}) == 1 else common)
        measured = any(row["fixture"] == name or row["fixture"].startswith(name + "/") for row in rows)
        trigger = f"evals/{name}/trigger-eval-set.json"
        trigger_doc = document(tree.text(trigger, 1024 * 1024))
        rendered.append({"name": name, "count": len(paths), "url": link("tree", tree.sha, subject),
                         "results": link("tree", results_sha) if measured else None,
                         "trigger": link("blob", tree.sha, trigger) if query_count(trigger_doc) is not None else None})
    trigger = "evals/writing-adrs/trigger-eval-set.json"
    return {"total": None if uncertain else sum(map(len, skills.values())) + sum(map(len, any_dirs.values())),
            "dirs": None if uncertain else len(skills) + len(any_dirs),
            "minFixtures": minimum_fixtures(tree), "skills": rendered,
            "guidance": guidance_inventory(tree, guidance_repo, guidance_ref),
            "triggerSet": link("blob", tree.sha, trigger) if query_count(
                document(tree.text(trigger, 1024 * 1024))) is not None else None}


def query_count(entries):
    if not isinstance(entries, list) or not all(isinstance(entry, dict) and
            set(entry) == {"query", "should_trigger"} and
            isinstance(entry["query"], str) and bool(entry["query"].strip()) and
            isinstance(entry["should_trigger"], bool) for entry in entries):
        return None
    return len(entries)


def build_feed(repo, main_ref, results_ref, guidance_repo=None, guidance_ref="origin/main"):
    main, result = Tree(repo, main_ref), Tree(repo, results_ref)
    rows, points = results(result)
    counts = inventory(main, rows, result.sha, guidance_repo, guidance_ref)
    timestamps = [int(git(repo, "show", "-s", "--format=%ct", tree.sha)) for tree in (main, result)]
    as_of = datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat().replace("+00:00", "Z")
    return {"dash": {"results": {"rows": rows}, "trend": {"points": points},
        "inventory": counts, "meta": {"asOf": as_of,
            "mainSha": main.sha, "resultsSha": result.sha,
            "sources": [{"label": "Evaluation results", "url": link("tree", result.sha)},
                        {"label": "Main tree", "url": link("tree", main.sha)},
                        {"label": "Guidance coverage", "url": counts["guidance"]["url"]},
                        {"label": "Board script implementation", "url": f"{REPOSITORY}/blob/main/scripts/board_feed.py"}]}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--results-ref", default="origin/persistent/eval-results")
    parser.add_argument("--guidance-repo", type=Path)
    parser.add_argument("--guidance-ref", default="origin/main")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        feed = build_feed(args.repo, args.main_ref, args.results_ref, args.guidance_repo, args.guidance_ref)
        args.out.write_text(json.dumps(feed, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
        return 0
    except (FeedError, OSError, ValueError, UnicodeError):
        print("board feed: cannot read input or write output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
