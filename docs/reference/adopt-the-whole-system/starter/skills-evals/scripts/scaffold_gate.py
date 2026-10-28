#!/usr/bin/env python3
"""scaffold_gate.py: validate and prepare a routine's scaffold branch.

    scaffold_gate.py validate --repo DIR --sha SHA --head-branch NAME [--base origin/main]
    scaffold_gate.py prepare  (same flags) --out DIR [--policy evals/improve-policy.yml]

DIR is this repo's default-branch checkout with the branch's commit fetched.
validate reads the branch only through `git diff`, `git cat-file` and `git
show` on SHA; it never checks the branch out or runs anything from it. It
accepts a branch claude/eval-scaffold-<run id> whose changes, against BASE,
only ADD plain files, and those files are exactly:

  eval-scaffold/<run id>/source.json   {"repo": "<owner>/<repo>", "number": N,
                                        "fixture": "evals/<skill>/<fixture>"}
  evals/<skill>/<fixture>/...          one new fixture directory (it must not
                                       exist on BASE), with fixture.yaml and
                                       seed/, that harness/fixture.py's
                                       load_fixture accepts and whose skill is
                                       <skill>

within size limits, with no line the privacy denylist matches, and from a
pull request not already in evals/scaffold-sources.json. It prints JSON
{"ok": bool, "reasons": [...]}; exit 0 ok, 1 refused, 2 bad usage.

prepare validates again, then (no credential) makes branch
eval-scaffold/<run id> from BASE in DIR with the fixture directory, a
`status: draft` row for it in evals/scheduled.yml (a scaffolded fixture is a
draft until two clean runs, guide 10.4) and the source appended to
evals/scaffold-sources.json, commits, and writes OUT/result.json and
OUT/pr-body.md. "draft" is true unless improve_merge_mode is auto.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import improve_gate as IG  # noqa: E402
from improve_gate import Refusal  # noqa: E402

RUN_ID = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}"
BRANCH_RE = re.compile(rf"\Aclaude/eval-scaffold-({RUN_ID})\Z")
SEG = r"[a-z0-9][a-z0-9-]{0,62}"
FIXTURE_FILE = re.compile(rf"\Aevals/({SEG})/({SEG})/((?:[A-Za-z0-9._-]{{1,100}}/){{0,9}}[A-Za-z0-9._-]{{1,100}})\Z")
BAD_SEGMENTS = {".", "..", ".git"}
REPO = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}\Z")
MAX_FILES, MAX_FILE, MAX_TOTAL, MAX_SOURCE = 300, 256 * 1024, 2 * 1024 * 1024, 4096
SOURCES = "evals/scaffold-sources.json"
SCHEDULED = "evals/scheduled.yml"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def exists_at(repo, ref, path):
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"],
                          capture_output=True).returncode == 0


def used_sources(repo, ref):
    if not exists_at(repo, ref, SOURCES):
        return []
    try:
        doc = json.loads(_git(repo, "show", f"{ref}:{SOURCES}"))
    except ValueError:
        raise Refusal("scaffold-sources.json on the base is not JSON") from None
    if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
        raise Refusal("scaffold-sources.json on the base is not a list of strings")
    return doc


def check_source(raw, run_id, fixture):
    if len(raw) > MAX_SOURCE:
        raise Refusal("source.json too large")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Refusal("source.json is not JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"repo", "number", "fixture"}:
        raise Refusal("source.json needs exactly repo, number and fixture")
    if not isinstance(doc["repo"], str) or not REPO.match(doc["repo"]):
        raise Refusal("source.json repo is not owner/repo")
    if type(doc["number"]) is not int or not 0 < doc["number"] < 10**10:
        raise Refusal("source.json number is not a pull request number")
    if doc["fixture"] != fixture:
        raise Refusal("source.json fixture is not the added fixture")
    return f"{doc['repo']}#{doc['number']}"


def validate(repo, sha, head_branch, base="origin/main", privacy_hook=IG.default_privacy_hook):
    """Returns (info, reasons)."""
    try:
        m = BRANCH_RE.match(head_branch)
        if not m:
            raise Refusal("branch-name")
        run_id = m.group(1)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Refusal("bad-sha")
        entries = IG.changed_entries(repo, base, sha)
        if not entries or len(entries) > MAX_FILES + 1:
            raise Refusal("wrong number of changed files")
        source_path = f"eval-scaffold/{run_id}/source.json"
        fixture, files = None, []
        for status, mode, path in entries:
            if status != "A" or mode not in ("100644", "100755"):
                raise Refusal(f"not-a-plain-added-file {path[:80]!r}")
            if path == source_path:
                continue
            fm = FIXTURE_FILE.match(path)
            if not fm or BAD_SEGMENTS & set(path.split("/")):
                raise Refusal(f"path-not-allowed {path[:80]!r}")
            this = f"evals/{fm.group(1)}/{fm.group(2)}"
            if fixture not in (None, this):
                raise Refusal("more than one fixture directory")
            fixture = this
            files.append(path)
        if fixture is None:
            raise Refusal("no fixture directory added")
        if source_path not in {p for _, _, p in entries}:
            raise Refusal("no eval-scaffold/<run id>/source.json")
        if exists_at(repo, base, fixture):
            raise Refusal("the fixture directory already exists on the base")
        total = 0
        for path in files + [source_path]:
            size = int(_git(repo, "cat-file", "-s", f"{sha}:{path}"))
            if size > MAX_FILE:
                raise Refusal(f"file too large {path[:80]!r}")
            total += size
        if total > MAX_TOTAL:
            raise Refusal("the change is too large")
        source = check_source(IG.show(repo, sha, source_path), run_id, fixture)
        if source in used_sources(repo, base):
            raise Refusal("this pull request already became a fixture")
        skill = fixture.split("/")[1]
        scan = []
        with tempfile.TemporaryDirectory() as tmp:
            for path in files:
                data = IG.show(repo, sha, path)
                dest = Path(tmp, path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                scan += [(f"{path}:{n}", t) for n, t in
                         enumerate(data.decode("utf-8", "replace").split("\n"), 1)]
            from harness.fixture import FixtureError, load_fixture
            try:
                doc = load_fixture(Path(tmp, fixture))
            except FixtureError as e:
                raise Refusal(f"fixture: {e}") from None
        if doc["skill"] != skill:
            raise Refusal("fixture.yaml skill is not the directory's skill")
        hits = privacy_hook(scan)
        if hits:
            raise Refusal("privacy: " + "; ".join(hits[:5]))
    except Refusal as e:
        return None, [str(e)]
    return {"run_id": run_id, "fixture": fixture, "skill": skill, "source": source}, []


def prepare(info, repo, sha, base, out_dir, pol):
    branch = f"eval-scaffold/{info['run_id']}"
    _git(repo, "checkout", "--quiet", "-b", branch, base)
    _git(repo, "checkout", sha, "--", info["fixture"])
    sched = Path(repo, SCHEDULED)
    text = sched.read_text(encoding="utf-8")
    import yaml
    before = yaml.safe_load(text)["fixtures"]
    text = (text if text.endswith("\n") else text + "\n") + \
        f"  - fixture: {info['fixture']}\n    status: draft\n"
    after = yaml.safe_load(text)["fixtures"]
    if after != before + [{"fixture": info["fixture"], "status": "draft"}]:
        raise Refusal("scheduled.yml does not end with its fixtures list")
    sched.write_text(text, encoding="utf-8")
    used = used_sources(repo, base) + [info["source"]]
    Path(repo, SOURCES).write_text(json.dumps(used, indent=2) + "\n", encoding="utf-8")
    _git(repo, "add", "--", info["fixture"], SCHEDULED, SOURCES)
    _git(repo, "-c", "user.name=loop-writer[bot]", "-c", "user.email=loop-writer@users.noreply.github.com",
         "commit", "--quiet", "-m", f"Add draft fixture {info['fixture']}\n\n"
         f"Scaffolded from a merged pull request by eval run {info['run_id']}.\n\nAgent: claude-code")
    draft = pol["improve_merge_mode"] != "auto"
    title = f"Add draft fixture {info['fixture']}"
    body = (f"Draft fixture `{info['fixture']}`, scaffolded by eval run {info['run_id']} from "
            f"{IG.sanitize(info['source'])} (guide 10.4).\n\nIt is `status: draft` in "
            f"`evals/scheduled.yml` until it has two clean ingested runs.\n\n"
            f"{'Opened as a draft: improve_merge_mode is not auto.' if draft else 'Auto-merges once checks pass.'}\n\n"
            "Opened by routine-scaffold-gate.yml.\n")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "pr-body.md"), "w", encoding="utf-8") as f:
        f.write(body)
    result = {"branch": branch, "fixture": info["fixture"], "title": title, "draft": draft}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main(argv=None, privacy_hook=IG.default_privacy_hook):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["validate", "prepare"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--head-branch", required=True)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--policy", default="evals/improve-policy.yml")
    ap.add_argument("--out")
    try:
        a = ap.parse_args(argv)
        pol = IG.load_policy(a.policy)
        if a.command == "prepare" and not a.out:
            raise ValueError("prepare needs --out")
    except (SystemExit, ValueError, OSError):
        print("scaffold_gate: bad usage or policy", file=sys.stderr)
        return 2
    info, reasons = validate(a.repo, a.sha, a.head_branch, a.base, privacy_hook)
    if reasons:
        print(json.dumps({"ok": False, "reasons": reasons}))
        return 1
    if a.command == "validate":
        print(json.dumps({"ok": True, "reasons": [], **info}))
        return 0
    try:
        result = prepare(info, a.repo, a.sha, a.base, a.out, pol)
    except Refusal as e:
        print(json.dumps({"ok": False, "reasons": [str(e)]}))
        return 1
    print(json.dumps({"ok": True, "reasons": [], **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
