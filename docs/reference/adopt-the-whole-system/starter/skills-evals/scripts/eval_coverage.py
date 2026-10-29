#!/usr/bin/env python3
"""Coverage census: every skill in every registry, and whether it has an eval.

Part of https://github.com/Adam-S-Daniel/skills-evals/issues/64. Local mode
reads registry checkouts and prints a table. Publication mode writes
a public-safe report and badge for the workflow; it never runs an eval arm.

    python3 scripts/eval_coverage.py \\
        --registry acsi-agentskills=PATH --registry example-site-platform=PATH \\
        --registry example-site-a=PATH --registry acsi-agentskills-private=PATH \\
        [--json] [--check] [--include-private-names]

    python3 scripts/eval_coverage.py --registry NAME=PATH ... \\
        --publish-dir OUTPUT --timestamp YYYY-MM-DDTHH:MM:SSZ

WHAT A ROW IS. Every `SKILL.md` matched by a registry's `layout` glob in
`harness/registries.yml` is one row, resolved by the harness's own
`resolve_registries` (so `--registry NAME=PATH`, `$SKILLS_EVALS_REGISTRIES`
and the layouts are the ones `run_eval.py` uses, not a second copy). Its
status is mechanical:

  covered  at least one non-draft `fixture.yaml` anywhere under `evals/` (the set
           `eval.yml` discovers with `find evals -mindepth 1 -name
           fixture.yaml`, at any depth) names this skill in its `skill:` field
           and this registry in its `registry:` field. `fixtures` counts them,
           so one flat fixture and several nested ones both register.
  skipped  no fixture, but `evals/non-coverage.yml` has a row for the pair,
           and that row carries the reason.
  gap      neither.

A fixture with no `skill:` key at all (the `guidance` and `propagation`
subjects) is not a skill fixture and is ignored. A `skill:` key that is present
but null or blank is a malformed skill fixture and is refused, never ignored.

THE DENOMINATOR IS THE PART THAT LIES, so local mode refuses (exit 2, nothing
printed to stdout) rather than report a partial census: a registry in
`harness/registries.yml` with no `--registry` path, a path that is not a
directory, a layout that matches no skill, one skill name in two bundles of one
registry (`(registry, skill)` would be ambiguous and every count inflated), an
`--evals-dir` with no `fixture.yaml` in it, a fixture whose `skill:` is null or
blank or whose `registry:` is missing or unknown, a file that is not valid
UTF-8 or YAML, a malformed or unknown-field `non-coverage.yml`. Nothing falls
back to a sibling directory: the operator names every checkout. A refusal
message never carries a skill name from a fixture or a non-coverage row, and
never one from a private registry (see PRIVATE REGISTRY). Publication mode
writes an unresolved report with no totals or badge and still exits 2.

PROBLEMS, reported beside the rows and failing `--check`:

  stale_skip         a non-coverage row names a skill its registry no longer
                     has (a rename must not hide behind a skip)
  skip_but_covered   a non-coverage row for a skill that also has a fixture
  orphan_fixture     a fixture names a skill its registry does not have

EXIT CODES. 0: census printed (and, under `--check`, no gap and no problem;
without `--check` a gap is reported and the exit is still 0).
1: `--check` and a gap or a problem. 2: refused, or a usage error.

PRIVATE REGISTRY. This repo is public and so is CI output. For a registry in
PRIVATE_REGISTRIES the table carries counts only: no skill name appears in
markdown or JSON unless `--include-private-names` is passed, and
`evals/non-coverage.yml` may not hold rows for it (the loader refuses). A
private skip lives in an operator-held file passed as
`--private-non-coverage PATH`, same schema, rows for private registries only.

Output is deterministic (registry order from registries.yml, then bundle and
skill name; no clock, no absolute paths). Stdlib plus PyYAML.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import os
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
import run_eval  # noqa: E402
import context as fixture_context  # noqa: E402

# An explicit allowlist, not a name-pattern guess. test_issue_64 asserts every
# registries.yml entry whose name says "private" is listed here, so a new
# private registry cannot arrive with its skill names printable.
PRIVATE_REGISTRIES = frozenset({"acsi-agentskills-private"})

DEFAULT_NON_COVERAGE = REPO_ROOT / "evals" / "non-coverage.yml"
DEFAULT_EVALS = REPO_ROOT / "evals"

# Field names are deliberately plain: a name such as "key" or "token" next to
# a high-entropy value trips the fleet's gitleaks rule.
SKIP_FIELDS = ("registry", "skill", "decision", "reason")

STATUSES = ("covered", "skipped", "gap")


class CensusRefusal(Exception):
    """The census cannot be trusted; refuse (exit 2) instead of printing."""


# --- inputs ---------------------------------------------------------------

def resolve(registry_flags: list[str] | None) -> dict[str, dict]:
    """One local checkout per registry in harness/registries.yml, via the
    harness's own resolver; no registry may fall back to a sibling default."""
    try:
        resolved = run_eval.resolve_registries(
            registry_flags, os.environ.get("SKILLS_EVALS_REGISTRIES"), REPO_ROOT)
    except ValueError as exc:
        raise CensusRefusal(str(exc)) from exc
    for name, entry in resolved.items():
        if entry["source"] == "sibling default":
            raise CensusRefusal(
                f"registry {name!r} has no --registry {name}=PATH: the census "
                "never guesses a checkout, because a registry it cannot see "
                "makes the denominator short")
        if not entry["path"].is_dir():
            raise CensusRefusal(
                f"registry {name!r} ({entry['source']}) does not resolve to a "
                "directory")
    return resolved


def enumerate_skills(entry: dict) -> list[dict]:
    """Every SKILL.md the registry's layout matches, as unclassified rows."""
    layout = entry["layout"]
    try:
        parts = run_eval._layout_parts(layout)
    except ValueError as exc:
        raise CensusRefusal(f"registry {entry['name']!r}: {exc}") from exc
    # Wildcards before the skill-name segment are the bundle/plugin name.
    bundle_idx = [i for i, part in enumerate(parts[:-2]) if "*" in part]
    # Check each segment before traversing it: globbing the whole layout can
    # read another registry through a directory symlink, or silently omit a
    # broken/cyclic link while still reporting a complete denominator.
    try:
        registry_root = entry["path"].resolve(strict=True)
        candidates = [entry["path"]]
        for index, part in enumerate(parts):
            matches = []
            for parent in candidates:
                if any(char in part for char in "*?["):
                    children = parent.glob(part)
                else:
                    child = parent / part
                    children = [child] if child.exists() or child.is_symlink() else []
                for match in children:
                    canonical = match.resolve(strict=True)
                    if not canonical.is_relative_to(registry_root):
                        raise CensusRefusal(
                            f"registry {entry['name']!r} has a path outside its root")
                    if (canonical.is_file() if index == len(parts) - 1
                            else canonical.is_dir()):
                        matches.append(match)
            candidates = matches
    except (OSError, RuntimeError):
        raise CensusRefusal(
            f"registry {entry['name']!r} has an unresolvable layout path") from None
    rows = []
    for skill_md in sorted(candidates):
        rel = skill_md.relative_to(entry["path"]).parts
        rows.append({
            "registry": entry["name"],
            "bundle": "/".join(rel[i] for i in bundle_idx) or None,
            "skill": skill_md.parent.name,
        })
    rows.sort(key=lambda r: (r["bundle"] or "", r["skill"]))
    seen: set[str] = set()
    for row in rows:
        try:
            run_eval._validate_skill_name(row["skill"])
        except ValueError:
            raise CensusRefusal(
                f"registry {entry['name']!r} has an invalid skill directory name") from None
        if row["skill"] in seen:
            # (registry, skill) keys every fixture and skip lookup, so a name
            # in two bundles would double-count; a private name stays unsaid.
            who = ("" if entry["name"] in PRIVATE_REGISTRIES
                   else f" {row['skill']!r}")
            raise CensusRefusal(
                f"registry {entry['name']!r} has one skill{who} in more than "
                "one bundle: (registry, skill) would be ambiguous and every "
                "count inflated")
        seen.add(row["skill"])
    if not rows:
        raise CensusRefusal(
            f"registry {entry['name']!r} yields no skills for layout "
            f"{layout!r}: a registry with nothing in it is a wrong path or a "
            "wrong layout, not a zero")
    return rows


def _load_yaml(path: Path, what: str):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except OSError as exc:
        raise CensusRefusal(f"cannot read {what} {path}: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise CensusRefusal(f"{what} {path} is not valid UTF-8") from exc
    except yaml.YAMLError as exc:
        # Not str(exc): PyYAML can quote file content in it (an unknown tag,
        # `!zz-name`, comes back verbatim), and that can be a private skill
        # name. The line number is enough to find the problem.
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise CensusRefusal(f"{what} {path} is not valid YAML{where}") from exc


def count_fixtures(evals_dir: Path, resolved: dict[str, dict]) -> dict:
    """{(registry name, skill): fixture count} from `skill:` / `registry:`."""
    if not evals_dir.is_dir():
        raise CensusRefusal(f"evals directory {evals_dir} is not a directory")
    # Exactly the set eval.yml's dispatch check discovers:
    # `find evals -mindepth 1 -name fixture.yaml`, at any depth, no exclusions.
    paths = sorted(evals_dir.rglob("fixture.yaml"))
    if not paths:
        raise CensusRefusal(
            f"no fixture.yaml under {evals_dir}: an empty or wrong evals "
            "directory would report every skill as a gap or a skip")
    counts: dict[tuple[str, str], int] = {}
    for path in paths:
        doc = _load_yaml(path, "fixture")
        if not isinstance(doc, dict):
            raise CensusRefusal(f"fixture {path.relative_to(evals_dir)} is not a mapping")
        if "skill" not in doc:
            continue  # a guidance / propagation subject, not a skill fixture
        skill = doc["skill"]
        # The messages below never carry `skill`: while the registry is
        # unknown it could be a private registry's name, and the fixture path
        # in `label` already says where to look.
        label = path.relative_to(evals_dir).as_posix()
        if not isinstance(skill, str) or not skill.strip():
            raise CensusRefusal(
                f"fixture {label}: 'skill:' is present but not a non-blank "
                "string (omit the key for a non-skill subject)")
        url = doc.get("registry")
        if not isinstance(url, str) or not url.strip():
            raise CensusRefusal(
                f"fixture {label} names a skill but has no 'registry:'")
        try:
            run_eval._validate_skill_name(skill)
        except ValueError:
            raise CensusRefusal(
                f"fixture {label}: 'skill:' is not a valid skill name (one "
                "path segment, no path or glob characters)") from None
        try:
            name = run_eval.registry_for_url(resolved, url)["name"]
        except ValueError as exc:
            raise CensusRefusal(f"fixture {label}: {exc}") from exc
        # Generated TODO fixtures are review requests, not eval coverage.
        # Refuse malformed values so a quoted "false" cannot silently count.
        draft = doc.get("draft", False)
        if type(draft) is not bool:
            raise CensusRefusal(f"fixture {label}: 'draft:' must be a boolean")
        if not draft:
            counts[(name, skill)] = counts.get((name, skill), 0) + 1
    return counts


def load_skips(path: Path, resolved: dict[str, dict], *, private: bool,
               into: dict) -> None:
    """Merge one non-coverage file into `into`: {(registry, skill): row}."""
    doc = _load_yaml(path, "non-coverage file")
    if not isinstance(doc, dict) or set(doc) != {"skips"} or not isinstance(
            doc["skips"], list):
        raise CensusRefusal(
            f"{path.name}: expected a mapping with exactly one key, 'skips:', "
            "a list of rows")
    for i, row in enumerate(doc["skips"]):
        where = f"{path.name} skips[{i}]"
        if not isinstance(row, dict) or set(row) != set(SKIP_FIELDS):
            raise CensusRefusal(
                f"{where}: a row has exactly the fields "
                f"{', '.join(SKIP_FIELDS)}")
        for field in SKIP_FIELDS:
            if not isinstance(row[field], str) or not row[field].strip():
                raise CensusRefusal(f"{where}: '{field}' must be a non-blank string")
        registry, skill = row["registry"], row["skill"]
        if registry not in resolved:
            raise CensusRefusal(
                f"{where}: registry {registry!r} is not in harness/registries.yml")
        if (registry in PRIVATE_REGISTRIES) != private:
            raise CensusRefusal(
                f"{where}: " + (
                    "the operator-held private file takes rows for private "
                    "registries only" if private else
                    "rows for a private registry may not be committed (the "
                    "skill name would be public); use --private-non-coverage"))
        try:
            run_eval._validate_skill_name(skill)
        except ValueError:
            raise CensusRefusal(
                f"{where}: 'skill' is not a valid skill name (one path "
                "segment, no path or glob characters)") from None
        if (registry, skill) in into:
            raise CensusRefusal(f"{where}: a second row for the same registry and skill")
        into[(registry, skill)] = row


# --- the census -----------------------------------------------------------

def build_census(resolved: dict[str, dict], fixtures: dict, skips: dict) -> dict:
    registries = []
    problems = []
    seen_skills = set()
    for name, entry in resolved.items():
        rows = enumerate_skills(entry)
        for row in rows:
            seen_skills.add((name, row["skill"]))
            key = (name, row["skill"])
            row["fixtures"] = fixtures.get(key, 0)
            row["reason"] = None
            if row["fixtures"]:
                row["status"] = "covered"
            elif key in skips:
                row["status"] = "skipped"
                row["reason"] = skips[key]["reason"]
            else:
                row["status"] = "gap"
        counts = {"total": len(rows)}
        for status in STATUSES:
            counts[status] = sum(1 for r in rows if r["status"] == status)
        registries.append({
            "name": name, "layout": entry["layout"],
            "private": name in PRIVATE_REGISTRIES,
            "counts": counts, "skills": rows})
    for (name, skill), row in sorted(skips.items()):
        if (name, skill) not in seen_skills:
            problems.append({"kind": "stale_skip", "registry": name, "skill": skill})
        elif fixtures.get((name, skill)):
            problems.append({"kind": "skip_but_covered", "registry": name,
                             "skill": skill})
    for (name, skill), _n in sorted(fixtures.items()):
        if (name, skill) not in seen_skills:
            problems.append({"kind": "orphan_fixture", "registry": name,
                             "skill": skill})
    problems.sort(key=lambda p: (p["registry"], p["kind"], p["skill"]))
    totals = {"total": sum(r["counts"]["total"] for r in registries)}
    for status in STATUSES:
        totals[status] = sum(r["counts"][status] for r in registries)
    return {"schema": 1, "registries": registries, "totals": totals,
            "problems": problems}


def redact(census: dict, include_private_names: bool) -> dict:
    """The publishable form: counts only for a private registry (rows and the
    skill of any problem in it are dropped) unless names were asked for."""
    out = {**census, "include_private_names": include_private_names,
           "registries": [], "problems": []}
    for reg in census["registries"]:
        withheld = reg["private"] and not include_private_names
        reg = {**reg, "names_withheld": withheld}
        if withheld:
            reg["skills"] = []
        out["registries"].append(reg)
    private_names = {r["name"] for r in census["registries"] if r["private"]}
    for problem in census["problems"]:
        if problem["registry"] in private_names and not include_private_names:
            problem = {k: v for k, v in problem.items() if k != "skill"}
        out["problems"].append(problem)
    return out


# --- rendering ------------------------------------------------------------

def _cell(text) -> str:
    safe = html.escape(" ".join(str(text).split()), quote=True)
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", safe)


def render_json(census: dict) -> str:
    return json.dumps(census, indent=2, sort_keys=True) + "\n"


def render_markdown(census: dict) -> str:
    lines = ["# Skill eval coverage census", "",
             "| Registry | Skills | Covered | Skipped | GAP |",
             "|---|---:|---:|---:|---:|"]
    for reg in census["registries"]:
        c = reg["counts"]
        lines.append(f"| {_cell(reg['name'])} | {c['total']} | {c['covered']} | "
                     f"{c['skipped']} | {c['gap']} |")
    t = census["totals"]
    lines.append(f"| **Total** | {t['total']} | {t['covered']} | {t['skipped']} "
                 f"| {t['gap']} |")
    for reg in census["registries"]:
        lines += ["", f"## {reg['name']}", ""]
        if reg["names_withheld"]:
            lines.append("Skill names withheld for this private registry; "
                         "counts above. `--include-private-names` lists them.")
            continue
        lines += ["| Bundle | Skill | Status | Fixtures | Reason |",
                  "|---|---|---|---:|---|"]
        for row in reg["skills"]:
            status = "GAP" if row["status"] == "gap" else row["status"]
            lines.append(
                f"| {_cell(row['bundle'] or '')} | {_cell(row['skill'])} | "
                f"{status} | {row['fixtures']} | {_cell(row['reason'] or '')} |")
    lines += ["", "## Problems", ""]
    if not census["problems"]:
        lines.append("None.")
    for problem in census["problems"]:
        who = f" `{problem['skill']}`" if "skill" in problem else ""
        lines.append(f"- {problem['kind']}: {problem['registry']}{who}")
    return "\n".join(lines) + "\n"


def fixture_readiness(evals_dir: Path) -> dict:
    """Count validated fixture metadata, without claiming context delivery."""
    counts = {"total": 0, "draft": 0, "context_declared": 0,
              "context_missing": 0, "guidance_unproven": 0,
              "paired_context_delivery_verified": False}
    for path in sorted(evals_dir.rglob("fixture.yaml")):
        try:
            fixture = fixture_context.load_fixture_yaml(path.read_bytes(), "fixture")
        except (OSError, fixture_context.ContextError) as exc:
            raise CensusRefusal("fixture_metadata_invalid") from exc
        draft = fixture.get("draft", False)
        if type(draft) is not bool:
            raise CensusRefusal("fixture_metadata_invalid")
        counts["total"] += 1
        counts["draft"] += int(draft)
        if "context" in fixture:
            counts["context_declared"] += 1
            counts["guidance_unproven"] += int(
                fixture["context"]["guidance_revision"] is None)
        else:
            counts["context_missing"] += 1
    return counts


def _publication_markdown(report: dict) -> str:
    if report["status"] == "unresolved":
        lines = ["# Skill eval coverage census", "", "Census unresolved; no fleet totals or badge published.",
                 "", "| Registry | State |", "|---|---|"]
        for registry in report["registries"]:
            lines.append(f"| {_cell(registry['name'])} | {registry['status']} |")
        lines += ["", "Fixture and paired-context readiness unverified."]
        return "\n".join(lines) + "\n"
    readiness = report["fixture_readiness"]
    return (render_markdown(report) + "\n## Fixture metadata readiness\n\n"
            f"- Fixtures: {readiness['total']}; draft: {readiness['draft']}.\n"
            f"- Context declared: {readiness['context_declared']}; missing: "
            f"{readiness['context_missing']}; guidance unproven: "
            f"{readiness['guidance_unproven']}.\n"
            "- Paired context delivery verified: no. These counts validate "
            "metadata only; ADR 0012 currently resolves context without "
            "delivering it to either arm.\n")


def _unresolved_report(timestamp: str, names: list[str],
                       unresolved: set[str]) -> dict:
    return {"schema": 2, "status": "unresolved", "generated_at": timestamp,
            "registries": [{"name": name,
                            "status": "unresolved" if name in unresolved else "resolved"}
                           for name in names],
            "unresolved": sorted(unresolved), "totals": None,
            "fixture_readiness": None}


def build_publication(args: argparse.Namespace) -> tuple[int, dict]:
    """Fail closed per registry, including when a checkout is missing or empty."""
    try:
        resolved = run_eval.resolve_registries(
            args.registry, os.environ.get("SKILLS_EVALS_REGISTRIES"), REPO_ROOT)
    except ValueError as exc:
        raise CensusRefusal("registry_configuration_invalid") from exc
    names = list(resolved)
    unresolved = set()
    for name, entry in resolved.items():
        if entry["source"] == "sibling default" or not entry["path"].is_dir():
            unresolved.add(name)
            continue
        try:
            enumerate_skills(entry)
        except CensusRefusal:
            unresolved.add(name)
    if unresolved:
        return 2, _unresolved_report(args.timestamp, names, unresolved)
    try:
        fixtures = count_fixtures(args.evals_dir, resolved)
        skips: dict = {}
        load_skips(args.non_coverage, resolved, private=False, into=skips)
        if args.private_non_coverage:
            load_skips(args.private_non_coverage, resolved, private=True, into=skips)
        census = redact(build_census(resolved, fixtures, skips), False)
        readiness = fixture_readiness(args.evals_dir)
    except CensusRefusal:
        return 2, _unresolved_report(args.timestamp, names, set(names))
    report = {**census, "schema": 2, "status": "complete",
              "generated_at": args.timestamp, "unresolved": [],
              "fixture_readiness": readiness}
    failed = report["totals"]["gap"] or report["problems"]
    return (1 if args.check and failed else 0), report


def write_publication(directory: Path, report: dict) -> None:
    """Overwrite fixed outputs only; an unresolved run cannot retain a badge."""
    directory.mkdir(parents=True, exist_ok=True)
    badge = directory / "badge.json"
    badge.unlink(missing_ok=True)
    (directory / "latest.json").write_text(render_json(report), encoding="utf-8")
    (directory / "latest.md").write_text(_publication_markdown(report), encoding="utf-8")
    if report["status"] == "complete":
        totals = report["totals"]
        endpoint = {"schemaVersion": 1, "label": "Coverage",
                    "message": (f"{totals['covered']} covered · {totals['skipped']} skipped · "
                                f"{totals['gap']} gap · {report['generated_at'][:10]}"),
                    "color": "green" if not totals["gap"] and not report["problems"]
                    else "orange"}
        badge.write_text(render_json(endpoint), encoding="utf-8")


# --- entry point ----------------------------------------------------------

def run(args: argparse.Namespace) -> tuple[int, str]:
    resolved = resolve(args.registry)
    fixtures = count_fixtures(args.evals_dir, resolved)
    skips: dict = {}
    load_skips(args.non_coverage, resolved, private=False, into=skips)
    if args.private_non_coverage:
        load_skips(args.private_non_coverage, resolved, private=True, into=skips)
    census = redact(build_census(resolved, fixtures, skips),
                    args.include_private_names)
    text = render_json(census) if args.json else render_markdown(census)
    failed = census["totals"]["gap"] or census["problems"]
    return (1 if args.check and failed else 0), text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Census of every skill in every registry: covered / "
                    "skipped / GAP. Read-only.")
    ap.add_argument("--registry", action="append", metavar="NAME=PATH",
                    help="local checkout of a registry in harness/registries.yml "
                         "(repeatable; every registry there needs one)")
    ap.add_argument("--evals-dir", type=Path, default=DEFAULT_EVALS)
    ap.add_argument("--non-coverage", type=Path, default=DEFAULT_NON_COVERAGE)
    ap.add_argument("--private-non-coverage", type=Path,
                    help="operator-held skip file for private registries")
    ap.add_argument("--json", action="store_true",
                    help="JSON instead of markdown")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on a GAP or a problem")
    ap.add_argument("--include-private-names", action="store_true",
                    help="list private-registry skill names (never in CI)")
    ap.add_argument("--publish-dir", type=Path,
                    help="write public-safe JSON, markdown, and a complete-only badge")
    ap.add_argument("--timestamp", help="explicit UTC time for publication, YYYY-MM-DDTHH:MM:SSZ")
    args = ap.parse_args(argv)
    if args.publish_dir is not None:
        if args.include_private_names:
            print("eval_coverage: refusing: private names cannot be published", file=sys.stderr)
            return 2
        if not args.timestamp or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                args.timestamp):
            print("eval_coverage: refusing: publication needs a UTC timestamp", file=sys.stderr)
            return 2
        try:
            parsed = datetime.strptime(args.timestamp, "%Y-%m-%dT%H:%M:%SZ")
            if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != args.timestamp:
                raise ValueError("noncanonical UTC time")
            code, report = build_publication(args)
            write_publication(args.publish_dir, report)
        except (ValueError, CensusRefusal, OSError):
            print("eval_coverage: refusing: publication input or output invalid",
                  file=sys.stderr)
            return 2
        if code == 2:
            print("eval_coverage: census unresolved; see the safe report", file=sys.stderr)
        return code
    if args.timestamp is not None:
        print("eval_coverage: refusing: --timestamp requires --publish-dir", file=sys.stderr)
        return 2
    try:
        code, text = run(args)
    except CensusRefusal as exc:
        print(f"eval_coverage: refusing: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
