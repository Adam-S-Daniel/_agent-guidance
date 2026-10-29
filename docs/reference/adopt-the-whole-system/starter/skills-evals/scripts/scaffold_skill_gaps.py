#!/usr/bin/env python3
"""Publish a redacted skill census and propose public GAP fixtures offline.

The census reads trusted local checkouts. The writer reads only its redacted
artifact, creates draft TODO fixtures, and uses GitHub's data API; it never
executes a registry file or launches an eval arm.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

import eval_coverage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import context  # noqa: E402

REPO = "example-adopter/skills-evals"
PARENT = 62
SHA = re.compile(r"[0-9a-f]{40}\Z")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\Z")
RUN_ID = re.compile(r"[0-9]+\Z")
REGISTRY_POLICY = {entry["name"]: entry for entry in yaml.safe_load(
    (ROOT / "harness" / "registries.yml").read_text(encoding="utf-8"))["registries"]}


class GapError(Exception):
    """A safe refusal; the message contains no registry content."""


def _revision(path: Path) -> str:
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    value = result.stdout.strip()
    if result.returncode or not SHA.fullmatch(value):
        raise GapError("a required checkout has no full Git revision")
    return value


def census(registry: list[str], guidance_dir: Path, out: Path) -> None:
    args = argparse.Namespace(registry=registry, evals_dir=eval_coverage.DEFAULT_EVALS,
                              non_coverage=eval_coverage.DEFAULT_NON_COVERAGE,
                              private_non_coverage=None, include_private_names=False,
                              json=True, check=False)
    try:
        _, raw = eval_coverage.run(args)
        resolved = eval_coverage.resolve(registry)
    except eval_coverage.CensusRefusal as exc:
        raise GapError("the complete registry census was refused") from exc
    doc = json.loads(raw)
    if doc["problems"]:
        raise GapError("the census has stale or orphaned entries")
    for reg in doc["registries"]:
        if not reg["private"]:
            reg["url"] = resolved[reg["name"]]["url"]
    sources = {
        "repository_revision": _revision(ROOT),
        "guidance_revision": _revision(guidance_dir),
        "registries": {name: _revision(entry["path"])
                       for name, entry in resolved.items()
                       if name not in eval_coverage.PRIVATE_REGISTRIES},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage.json").write_text(eval_coverage.render_json(doc), encoding="utf-8")
    (out / "coverage.md").write_text(eval_coverage.render_markdown(doc), encoding="utf-8")
    (out / "sources.json").write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")


def marker(registry: str, skill: str) -> str:
    if not NAME.fullmatch(registry):
        raise GapError("a public GAP name is unsafe")
    try:
        eval_coverage.run_eval._validate_skill_name(skill)
    except ValueError as exc:
        raise GapError("a public GAP name is unsafe") from exc
    return f"<!-- skill-gap:{registry}/{skill} -->"


def branch_name(registry: str, skill: str) -> str:
    marker(registry, skill)
    # Hex every name: conditional encoding would collide with an already
    # hex-shaped legal skill name, and Git forbids some harness-legal dots.
    return f"scaffold/skill-{registry}-x{skill.encode('ascii').hex()}"


def public_gaps(doc: dict) -> list[tuple[dict, dict]]:
    if doc.get("schema") != 1 or doc.get("include_private_names") is not False:
        raise GapError("coverage artifact is not a redacted schema-1 census")
    if not isinstance(doc.get("registries"), list) or doc.get("problems") != []:
        raise GapError("coverage artifact is incomplete or has problems")
    if (not all(isinstance(reg, dict) and isinstance(reg.get("name"), str)
                for reg in doc["registries"])
            or len(doc["registries"]) != len(REGISTRY_POLICY)
            or {reg.get("name") for reg in doc["registries"]} != set(REGISTRY_POLICY)):
        raise GapError("coverage artifact is missing a registry")
    gaps = []
    totals = {key: 0 for key in ("total", *eval_coverage.STATUSES)}
    for reg in doc["registries"]:
        policy = REGISTRY_POLICY[reg["name"]]
        if reg.get("private") is not (reg["name"] in eval_coverage.PRIVATE_REGISTRIES):
            raise GapError("coverage artifact has an invalid privacy flag")
        if reg.get("layout") != policy["layout"]:
            raise GapError("coverage artifact has an invalid registry layout")
        counts = reg.get("counts")
        if (not isinstance(counts, dict) or set(counts) != set(totals)
                or any(type(value) is not int or value < 0 for value in counts.values())
                or sum(counts[status] for status in eval_coverage.STATUSES) != counts["total"]
                or counts["total"] == 0):
            raise GapError("coverage artifact has invalid counts")
        for key in totals:
            totals[key] += counts[key]
        if reg.get("private"):
            if reg.get("skills") != [] or reg.get("names_withheld") is not True:
                raise GapError("private registry names were not withheld")
            continue
        if (reg.get("url") != policy["url"] or reg.get("names_withheld") is not False
                or not isinstance(reg.get("skills"), list)
                or len(reg["skills"]) != counts["total"]):
            raise GapError("public registry rows are missing")
        actual = {status: 0 for status in eval_coverage.STATUSES}
        seen = set()
        for row in reg["skills"]:
            if not isinstance(row, dict) or not isinstance(row.get("skill"), str):
                raise GapError("public registry row is malformed")
            skill = row["skill"]
            try:
                eval_coverage.run_eval._validate_skill_name(skill)
            except ValueError as exc:
                raise GapError("public registry has an unsafe skill") from exc
            if skill in seen:
                raise GapError("public registry has an unsafe or duplicate skill")
            seen.add(skill)
            status = row.get("status")
            fixtures = row.get("fixtures")
            if (status not in eval_coverage.STATUSES or type(fixtures) is not int
                    or fixtures < 0 or (status == "covered") != (fixtures > 0)):
                raise GapError("public registry row has inconsistent coverage")
            actual[status] += 1
            if row.get("status") == "gap":
                marker(reg["name"], row["skill"])
                gaps.append((reg, row))
        if any(actual[status] != counts[status] for status in actual):
            raise GapError("public registry row counts disagree")
    if doc.get("totals") != totals:
        raise GapError("coverage total disagrees with registry counts")
    return gaps


def validate_sources(sources: dict, main_sha: str) -> None:
    public = set(REGISTRY_POLICY) - eval_coverage.PRIVATE_REGISTRIES
    if (not isinstance(sources, dict)
            or set(sources) != {"repository_revision", "guidance_revision", "registries"}
            or not isinstance(sources["registries"], dict)
            or set(sources["registries"]) != public
            or sources["repository_revision"] != main_sha
            or any(not isinstance(value, str) or not SHA.fullmatch(value)
                   for value in (sources["repository_revision"],
                                 sources["guidance_revision"],
                                 *sources["registries"].values()))):
        raise GapError("census source revisions are incomplete or unsafe")


def draft_files(reg: dict, row: dict, sources: dict) -> dict[str, str]:
    name, skill = reg["name"], row["skill"]
    tag = marker(name, skill)
    revisions = sources.get("registries", {})
    for value in (sources.get("repository_revision"),
                  sources.get("guidance_revision"), revisions.get(name)):
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise GapError("source revisions are missing or invalid")
    url = reg.get("url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        raise GapError("public source URL is missing")
    context_doc = {
        "repository": REPO,
        "revision": sources["repository_revision"],
        "guidance_revision": sources["guidance_revision"],
        "budget": {"guidance_bytes": 1, "skill_catalog_bytes": 1,
                   "skill_payload_bytes": 1},
    }
    fixture = {"skill": skill, "registry": url, "draft": True,
               "context": context_doc,
               "prompt": "TODO: Replace with a real incident and task before review."}
    # The harness validates context metadata without resolving or running it.
    rendered = "# " + tag + "\n" + yaml.safe_dump(fixture, sort_keys=False)
    context.load_fixture_yaml(rendered, "fixture.yaml")
    base = f"evals/skill-gaps/{name}/{skill}"
    readme = (f"{tag}\n# Draft eval for {name}/{skill}\n\n"
              f"Source: {url}/tree/{revisions[name]}\n\n"
              "TODO: Pick a real incident from the named repository; record the "
              "pre-fix task, seed, and objective checks. Measure and replace "
              "the context budget placeholders. Keep `draft: true` until "
              "review proves the seed red and the fix green.\n")
    return {f"{base}/fixture.yaml": rendered, f"{base}/README.md": readme}


def has_label(item: dict, name: str) -> bool:
    return any(label.get("name") == name for label in item.get("labels", []))


def held(item: dict) -> bool:
    return has_label(item, "on-hold")


def _canonical_pr(item: dict, tag: str, branch: str) -> bool:
    """Only the writer's same-repository branch can reserve a GAP."""
    body = item.get("body")
    number = item.get("number")
    head, base = item.get("head"), item.get("base")
    if (not isinstance(body, str) or tag not in body.splitlines()
            or item.get("state") not in ("open", "closed")
            or type(number) is not int or number <= 0
            or item.get("html_url") != f"https://github.com/{REPO}/pull/{number}"
            or not isinstance(head, dict) or not isinstance(base, dict)):
        return False
    head_repo, base_repo = head.get("repo"), base.get("repo")
    return (head.get("ref") == branch and base.get("ref") == "main"
            and isinstance(head_repo, dict) and head_repo.get("full_name") == REPO
            and isinstance(base_repo, dict) and base_repo.get("full_name") == REPO)


def _canonical_issue(item: dict, tag: str, name: str, pr_urls: set[str]) -> bool:
    """Authenticate tracking records before honoring holds or review outcomes."""
    body, user = item.get("body"), item.get("user")
    if (not isinstance(body, str) or not isinstance(user, dict)
            or item.get("state") not in ("open", "closed")
            or user.get("login") != "github-actions[bot]" or user.get("type") != "Bot"
            or item.get("title") != f"Author eval for {name}"
            or type(item.get("id")) is not int or item["id"] <= 0):
        return False
    lines = body.splitlines()
    return (tag in lines
            and f"Tracking parent: https://github.com/{REPO}/issues/{PARENT}" in lines
            and any(f"Draft PR: {url}" in lines for url in pr_urls))


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise GapError("GitHub write credential is absent")
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None,
                *, missing_ok: bool = False):
        raw = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.github.com" + path, data=raw, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read(16 * 1024 * 1024 + 1)
                if len(body) > 16 * 1024 * 1024:
                    raise GapError("GitHub response exceeds the checked size limit")
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise GapError(f"GitHub {method} request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GapError(f"GitHub {method} request failed") from exc
        try:
            return json.loads(body) if body else {}
        except (ValueError, UnicodeError) as exc:
            raise GapError("GitHub response was not JSON") from exc

    def pages(self, path: str) -> list[dict]:
        rows = []
        for page in range(1, 21):
            separator = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise GapError("GitHub list response was malformed")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise GapError("GitHub list exceeded the checked pagination limit")


def _put_coverage(api: GitHub, path: str, raw: bytes) -> None:
    url = f"/repos/{REPO}/contents/{path}"
    existing = api.request("GET", url + "?ref=persistent/eval-results", missing_ok=True)
    if existing is not None:
        old = _decode_content(existing)
        if old == raw:
            return
    payload = {"message": "Publish complete skill coverage census",
               "content": base64.b64encode(raw).decode("ascii"),
               "branch": "persistent/eval-results"}
    if existing is not None:
        payload["sha"] = existing["sha"]
    api.request("PUT", url, payload)


def _decode_content(item: dict) -> bytes:
    """GitHub wraps base64 content across lines; reject all other bytes."""
    value = item.get("content")
    if not isinstance(value, str):
        raise GapError("GitHub content response is malformed")
    return base64.b64decode("".join(value.split()), validate=True)


def _latest_run(api: GitHub) -> int:
    path = f"/repos/{REPO}/contents/coverage/latest.json?ref=persistent/eval-results"
    existing = api.request("GET", path, missing_ok=True)
    if existing is None:
        return 0
    try:
        data = json.loads(_decode_content(existing))
        value = data.get("publication", {}).get("run_id", "0")
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error) as exc:
        raise GapError("published coverage metadata is malformed") from exc
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise GapError("published coverage run ID is malformed")
    return int(value)


def _ensure_branch(api: GitHub, branch: str, files: dict[str, str], base: str) -> None:
    ref_path = f"/repos/{REPO}/git/ref/heads/{branch}"
    existing_ref = api.request("GET", ref_path, missing_ok=True)
    if existing_ref is not None:
        existing_sha = existing_ref["object"]["sha"]
        if not isinstance(existing_sha, str) or not SHA.fullmatch(existing_sha):
            raise GapError("existing scaffold branch has no commit revision")
        existing_commit = api.request("GET", f"/repos/{REPO}/git/commits/{existing_sha}")
        parents = existing_commit.get("parents")
        if not isinstance(parents, list) or len(parents) != 1:
            raise GapError("existing scaffold branch has unexpected history")
        parent_sha = parents[0].get("sha")
        if not isinstance(parent_sha, str) or not SHA.fullmatch(parent_sha):
            raise GapError("existing scaffold branch has an invalid parent")
        comparison = api.request("GET", f"/repos/{REPO}/compare/{parent_sha}...{branch}")
        changed = comparison.get("files")
        if (comparison.get("total_commits") != 1 or not isinstance(changed, list)
                or {item.get("filename") for item in changed} != set(files)
                or any(item.get("status") != "added" for item in changed)):
            raise GapError("existing scaffold branch has unrelated changes")
        fixture_path = next(path for path in files if path.endswith("/fixture.yaml"))
        existing = api.request("GET", f"/repos/{REPO}/contents/{fixture_path}?ref={branch}",
                               missing_ok=True)
        if existing is None:
            raise GapError("existing scaffold branch is missing its fixture")
        raw = _decode_content(existing).decode("utf-8")
        if raw.splitlines()[0] != files[fixture_path].splitlines()[0]:
            raise GapError("existing scaffold branch has a different marker")
        doc = context.load_fixture_yaml(raw, "fixture.yaml")
        expected = context.load_fixture_yaml(files[fixture_path], "fixture.yaml")
        if (doc.get("draft") is not True or doc.get("skill") != expected["skill"]
                or doc.get("registry") != expected["registry"]):
            raise GapError("existing scaffold branch is not the expected draft")
        return
    commit = api.request("GET", f"/repos/{REPO}/git/commits/{base}")
    tree = []
    for path, content in files.items():
        blob = api.request("POST", f"/repos/{REPO}/git/blobs",
                           {"content": base64.b64encode(content.encode()).decode(),
                            "encoding": "base64"})
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    result = api.request("POST", f"/repos/{REPO}/git/trees",
                         {"base_tree": commit["tree"]["sha"], "tree": tree})
    created = api.request("POST", f"/repos/{REPO}/git/commits",
                          {"message": "Propose a reviewable skill eval draft",
                           "tree": result["sha"], "parents": [base],
                           "author": {"name": "github-actions[bot]",
                                      "email": "41898282+github-actions[bot]@users.noreply.github.com"},
                           "committer": {"name": "github-actions[bot]",
                                         "email": "41898282+github-actions[bot]@users.noreply.github.com"}})
    api.request("POST", f"/repos/{REPO}/git/refs",
                {"ref": "refs/heads/" + branch, "sha": created["sha"]})


def automate(api: GitHub, coverage: dict, sources: dict, run_id: str,
             main_sha: str, *, write: bool, as_of: str | None = None) -> list[str]:
    if not RUN_ID.fullmatch(run_id) or not SHA.fullmatch(main_sha):
        raise GapError("run ID or default branch revision is invalid")
    gaps = public_gaps(coverage)
    validate_sources(sources, main_sha)
    if not write:
        return [marker(reg["name"], row["skill"]) for reg, row in gaps[:3]]
    if as_of is None:
        as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", as_of):
        raise GapError("coverage timestamp is invalid")
    newest_run = _latest_run(api)
    published = {**coverage, "publication": {
        "run_id": run_id,
        "as_of": as_of,
        "repository_revision": main_sha,
        "guidance_revision": sources["guidance_revision"],
        "public_registry_revisions": sources["registries"]}}
    raw = eval_coverage.render_json(published).encode()
    _put_coverage(api, f"coverage/{run_id}.json", raw)
    if int(run_id) < newest_run:
        return []  # a late rerun cannot propose GAPs from an older census
    if int(run_id) >= newest_run:
        _put_coverage(api, "coverage/latest.json", raw)
        totals = coverage["totals"]
        badge = {"schemaVersion": 1, "label": "skill eval coverage",
                 "message": (f"{totals['covered']} covered, {totals['skipped']} skipped, "
                             f"{totals['gap']} GAP · {as_of[:10]} (run {run_id})"),
                 "color": "green" if totals["gap"] == 0 else "orange"}
        _put_coverage(api, "badges/coverage.json",
                      (json.dumps(badge, sort_keys=True) + "\n").encode())
    parent = api.request("GET", f"/repos/{REPO}/issues/{PARENT}")
    if parent.get("state") != "open" or held(parent):
        return []
    prs = api.pages(f"/repos/{REPO}/pulls?state=all")
    issues = [item for item in api.pages(f"/repos/{REPO}/issues?state=all")
              if "pull_request" not in item]
    children = api.pages(f"/repos/{REPO}/issues/{PARENT}/sub_issues")
    child_ids = {item["id"] for item in children}
    selected = []
    for reg, row in gaps:
        tag = marker(reg["name"], row["skill"])
        branch = branch_name(reg["name"], row["skill"])
        matching_prs = [item for item in prs if _canonical_pr(item, tag, branch)]
        pr_urls = {item["html_url"] for item in matching_prs}
        matching_issues = [item for item in issues if _canonical_issue(
            item, tag, f"{reg['name']}/{row['skill']}", pr_urls)]
        if any(held(item) for item in (*matching_prs, *matching_issues)):
            continue
        # A closed canonical record is an explicit review outcome. Prefer an
        # active record when one exists; otherwise leave the GAP for a person.
        pr = next((item for item in matching_prs if item.get("state", "open") == "open"), None)
        issue = next((item for item in matching_issues if item.get("state", "open") == "open"), None)
        if (pr is None and matching_prs) or (issue is None and matching_issues):
            continue
        if pr and issue and issue["id"] in child_ids and has_label(pr, "eval-scaffold"):
            continue
        selected.append((reg, row, tag, pr, issue))
        if len(selected) == 3:
            break
    for reg, row, tag, pr, issue in selected:
        branch = branch_name(reg["name"], row["skill"])
        source = f"{reg['url']}/tree/{sources['registries'][reg['name']]}"
        if pr is None:
            _ensure_branch(api, branch, draft_files(reg, row, sources), main_sha)
            pr = api.request("POST", f"/repos/{REPO}/pulls",
                             {"title": f"Draft eval for {reg['name']}/{row['skill']}",
                              "head": branch, "base": "main", "draft": True,
                              "body": f"{tag}\nPart of https://github.com/{REPO}/issues/60\n"
                                      f"Public GAP from {source}. TODO: select a real incident, "
                                      "measure context limits, and write checks before review.\n"})
            prs.append(pr)
        if not has_label(pr, "eval-scaffold"):
            api.request("POST", f"/repos/{REPO}/issues/{pr['number']}/labels",
                        {"labels": ["eval-scaffold"]})
        if issue is None:
            issue = api.request("POST", f"/repos/{REPO}/issues",
                                {"title": f"Author eval for {reg['name']}/{row['skill']}",
                                 "body": f"{tag}\nPart of https://github.com/{REPO}/issues/60\n"
                                         f"Tracking parent: https://github.com/{REPO}/issues/{PARENT}\n"
                                         f"Source: {source}\nDraft PR: {pr['html_url']}\n"
                                         "TODO: mine an incident and prove objective checks red/green.\n"})
            issues.append(issue)
        if issue["id"] not in child_ids:
            api.request("POST", f"/repos/{REPO}/issues/{PARENT}/sub_issues",
                        {"sub_issue_id": issue["id"]})
            child_ids.add(issue["id"])
    return [tag for _, _, tag, _, _ in selected]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("census")
    capture.add_argument("--registry", action="append", required=True)
    capture.add_argument("--guidance-dir", type=Path, required=True)
    capture.add_argument("--out", type=Path, required=True)
    publish = commands.add_parser("automate")
    publish.add_argument("--artifact", type=Path, required=True)
    publish.add_argument("--run-id", required=True)
    publish.add_argument("--main-sha", required=True)
    publish.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "census":
            census(args.registry, args.guidance_dir, args.out)
        else:
            coverage = json.loads((args.artifact / "coverage.json").read_text())
            sources = json.loads((args.artifact / "sources.json").read_text())
            tags = automate(GitHub(os.environ.get("GH_TOKEN", "")) if args.write else None,
                            coverage, sources, args.run_id, args.main_sha, write=args.write)
            print(f"Public GAP proposals requiring work: {len(tags)}")
    except (GapError, OSError, ValueError, KeyError, TypeError, UnicodeError,
            binascii.Error, context.ContextError) as exc:
        # The exception text of malformed artifacts or API bodies can contain
        # private data; workflow logs receive a fixed refusal only.
        print("skill-gap automation refused incomplete or unsafe input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
