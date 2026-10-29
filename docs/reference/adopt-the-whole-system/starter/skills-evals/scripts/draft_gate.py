#!/usr/bin/env python3
"""draft_gate.py: validate and prepare a routine's draft branch.

    draft_gate.py validate --repo DIR --sha SHA --head-branch NAME
                           --registry-dir DIR [--base origin/main]
    draft_gate.py prepare  (same flags) --out DIR

DIR (--repo) is this repo's checkout with the branch's commit fetched; it is
read only through `git diff`, `git cat-file` and `git show` on SHA, never
checked out or run. A branch claude/eval-draft-<run id> may only ADD plain
files under eval-draft/<run id>/, as one of two outcomes (docs/routine-prompts.md):

  hook   eval-draft/<run id>/hook-proposal.md and nothing else
  skill  eval-draft/<run id>/skill/<name>/SKILL.md (plus other .md files in
         that directory, nothing executable) and the fixtures under
         eval-draft/<run id>/fixtures/

A skill is refused unless SKILL.md opens with YAML frontmatter whose `name`
is <name> and whose `description` is a non-empty string, no line runs a
shell command (a line starting with `!`, or `!` before a backtick), and no
plugin in the registry already has a skill <name>. Every added line passes
the privacy denylist. validate prints {"ok", "reasons", ...}; exit 0 ok,
1 refused, 2 bad usage.

prepare validates again, then, with no credential:
  hook   writes OUT/issue.md (sanitized) and OUT/result.json {"kind": "hook", "title"}
  skill  in the registry checkout, on a new branch eval-draft/<run id>, copies
         the skill to plugins/<plugin>/skills/<name>/ (<plugin> is the first
         plugin in scripts/plugin_layout.json), adds <name> to that plugin in
         plugin_layout.json (sorted, the registry's canonical form), raises the
         plugin's minor version, appends a docs/skill-impact.md entry, commits,
         and writes OUT/pr-body.md and OUT/result.json {"kind": "skill",
         "branch", "title", "skill", "plugin", "draft": true}. A new skill is
         always a draft pull request (guide 10.4).
"""
import argparse
import json
import os
import re
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import improve_gate as IG  # noqa: E402
import registry_edit as R  # noqa: E402
from improve_gate import Refusal  # noqa: E402

RUN_ID = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}"
BRANCH_RE = re.compile(rf"\Aclaude/eval-draft-({RUN_ID})\Z")
NAME = r"[a-z0-9][a-z0-9-]{0,63}"
SKILL_FILE = re.compile(rf"\Askill/({NAME})/((?:[a-z0-9][a-z0-9._-]{{0,63}}/){{0,2}}[a-z0-9][a-z0-9._-]{{0,63}}\.md|SKILL\.md)\Z")
FIXTURE_FILE = re.compile(r"\Afixtures/(?:[A-Za-z0-9._-]{1,100}/){0,10}[A-Za-z0-9._-]{1,100}\Z")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SHELL_LINE = re.compile(r"^\s*!|!`", re.M)
MAX_FILES, MAX_FILE, MAX_TOTAL = 400, 256 * 1024, 2 * 1024 * 1024
LAYOUT = "scripts/plugin_layout.json"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def check_skill_md(text, name):
    m = FRONTMATTER.match(text)
    if not m:
        raise Refusal("SKILL.md has no frontmatter")
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        raise Refusal("SKILL.md frontmatter is not YAML") from None
    if not isinstance(fm, dict) or fm.get("name") != name:
        raise Refusal("SKILL.md frontmatter name is not the directory name")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > 1024:
        raise Refusal("SKILL.md needs a description of at most 1024 characters")


def registry_skills(registry_dir):
    out = _git(registry_dir, "ls-tree", "-d", "--name-only", "HEAD", "plugins/").decode().split()
    names = set()
    for plugin in out:
        names |= {p.rsplit("/", 1)[1] for p in
                  _git(registry_dir, "ls-tree", "-d", "--name-only", "HEAD", f"{plugin}/skills/").decode().split()}
    return names


def validate(repo, sha, head_branch, registry_dir, base="origin/main", privacy_hook=IG.default_privacy_hook):
    try:
        m = BRANCH_RE.match(head_branch)
        if not m:
            raise Refusal("branch-name")
        run_id = m.group(1)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Refusal("bad-sha")
        entries = IG.changed_entries(repo, base, sha)
        if not entries or len(entries) > MAX_FILES:
            raise Refusal("wrong number of changed files")
        prefix = f"eval-draft/{run_id}/"
        rels, total = [], 0
        for status, mode, path in entries:
            if status != "A" or mode != "100644":
                raise Refusal(f"not-a-plain-added-file {path[:80]!r}")
            if not path.startswith(prefix) or {".", "..", ".git"} & set(path.split("/")):
                raise Refusal(f"path-not-allowed {path[:80]!r}")
            size = int(_git(repo, "cat-file", "-s", f"{sha}:{path}"))
            if size > MAX_FILE:
                raise Refusal(f"file too large {path[:80]!r}")
            total += size
            rels.append(path[len(prefix):])
        if total > MAX_TOTAL:
            raise Refusal("the change is too large")
        texts = {}
        for rel in rels:
            try:
                texts[rel] = IG.show(repo, sha, prefix + rel).decode("utf-8")
            except UnicodeDecodeError:
                raise Refusal(f"not UTF-8 text {rel[:80]!r}") from None
        if "hook-proposal.md" in rels:
            if rels != ["hook-proposal.md"]:
                raise Refusal("a hook proposal comes alone, with no skill")
            info = {"kind": "hook", "run_id": run_id}
        else:
            names, skill_files = set(), []
            for rel in rels:
                sm = SKILL_FILE.match(rel)
                if sm:
                    names.add(sm.group(1))
                    skill_files.append(rel)
                elif not FIXTURE_FILE.match(rel):
                    raise Refusal(f"path-not-allowed {rel[:80]!r}")
            if len(names) != 1:
                raise Refusal("exactly one skill directory is needed")
            name = names.pop()
            md = f"skill/{name}/SKILL.md"
            if md not in rels:
                raise Refusal("the skill has no SKILL.md")
            check_skill_md(texts[md], name)
            for rel in skill_files:
                if SHELL_LINE.search(texts[rel]):
                    raise Refusal(f"a shell command line in {rel[:80]!r}")
            if name in registry_skills(registry_dir):
                raise Refusal("the registry already has a skill of that name")
            info = {"kind": "skill", "run_id": run_id, "skill": name,
                    "files": sorted(r[len(f"skill/{name}/"):] for r in skill_files)}
        scan = [(f"{rel}:{n}", t) for rel, text in texts.items() for n, t in enumerate(text.split("\n"), 1)]
        hits = privacy_hook(scan)
        if hits:
            raise Refusal("privacy: " + "; ".join(hits[:5]))
    except Refusal as e:
        return None, [str(e)]
    return info, []


def canonical_layout(doc):
    plugins = {p: {**v, "skills": sorted(v["skills"])} for p, v in sorted(doc["plugins"].items())}
    return json.dumps({**doc, "plugins": plugins}, indent=2) + "\n"


def prepare(info, repo, sha, registry_dir, out_dir, today):
    os.makedirs(out_dir, exist_ok=True)
    prefix = f"eval-draft/{info['run_id']}/"
    if info["kind"] == "hook":
        text = IG.sanitize(IG.show(repo, sha, prefix + "hook-proposal.md").decode("utf-8"))[:20000]
        body = (f"A draft-mode run ({info['run_id']}) judged that a commit-time check, not a skill, "
                "should enforce this lesson (hook-first, guide 10.4). The proposal, as the routine "
                "wrote it (data, sanitized):\n\n" + "\n".join("> " + line for line in text.split("\n")) +
                "\n\nOpened by routine-draft-gate.yml.\n")
        with open(os.path.join(out_dir, "issue.md"), "w", encoding="utf-8") as f:
            f.write(body)
        result = {"kind": "hook", "title": f"Hook proposal from draft run {info['run_id']}"}
    else:
        name = info["skill"]
        layout_path = os.path.join(registry_dir, LAYOUT)
        with open(layout_path, encoding="utf-8") as f:
            layout = json.load(f)
        plugin = sorted(layout["plugins"])[0]
        branch = f"eval-draft/{info['run_id']}"
        R.git(registry_dir, "checkout", "--quiet", "-b", branch)
        dest = os.path.join(registry_dir, "plugins", plugin, "skills", name)
        for rel in info["files"]:
            data = IG.show(repo, sha, f"{prefix}skill/{name}/{rel}")
            path = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        layout["plugins"][plugin]["skills"] = sorted(set(layout["plugins"][plugin]["skills"]) | {name})
        with open(layout_path, "w", encoding="utf-8") as f:
            f.write(canonical_layout(layout))
        old, new = R.bump_plugin(registry_dir, plugin, "minor")
        R.append_impact(registry_dir, (
            f"## {today}: add `{name}` ({plugin} {old} -> {new})\n\n"
            f"- Kind: creation (draft mode, eval run {info['run_id']}).\n"
            "- Eval: both arms on the routine's four fixtures; see the draft pull request.\n"))
        R.git(registry_dir, "add", "-A")
        R.git(registry_dir, "commit", "--quiet", "-m",
              f"Add {name}: drafted from a mined lesson\n\nAgent: claude-code")
        body = (f"New skill `{name}` in `{plugin}`, drafted by eval run {info['run_id']} from a mined "
                "lesson (guide 10.4). Always a draft: review the SKILL.md, then graduate its fixtures "
                f"from `eval-draft/{info['run_id']}/fixtures/` on the routine's branch into "
                "skills-evals.\n\nOpened by routine-draft-gate.yml.\n")
        with open(os.path.join(out_dir, "pr-body.md"), "w", encoding="utf-8") as f:
            f.write(body)
        result = {"kind": "skill", "branch": branch, "title": f"Add {name} (drafted skill)",
                  "skill": name, "plugin": plugin, "draft": True}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main(argv=None, privacy_hook=IG.default_privacy_hook):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["validate", "prepare"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--head-branch", required=True)
    ap.add_argument("--registry-dir", required=True)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--out")
    ap.add_argument("--today")
    try:
        a = ap.parse_args(argv)
        if a.command == "prepare" and not a.out:
            raise ValueError("prepare needs --out")
        if a.today and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", a.today):
            raise ValueError("--today is YYYY-MM-DD")
    except (SystemExit, ValueError):
        print("draft_gate: bad usage", file=sys.stderr)
        return 2
    info, reasons = validate(a.repo, a.sha, a.head_branch, a.registry_dir, a.base, privacy_hook)
    if reasons:
        print(json.dumps({"ok": False, "reasons": reasons}))
        return 1
    if a.command == "validate":
        print(json.dumps({"ok": True, "reasons": [], **info}))
        return 0
    import datetime as dt
    today = a.today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    result = prepare(info, a.repo, a.sha, a.registry_dir, a.out, today)
    print(json.dumps({"ok": True, "reasons": [], **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
