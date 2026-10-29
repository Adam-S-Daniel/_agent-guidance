import json
import subprocess

import pytest

import draft_gate as G
import improve_gate as IG

RID = "20261028T120000Z-1a2b3c"
BRANCH = f"claude/eval-draft-{RID}"
P = f"eval-draft/{RID}"
SKILL_MD = "---\nname: push-check\ndescription: Check CI before you push. Use before git push.\n---\n\n# Push check\n\nRun the checks first.\n"
CLEAN = lambda added: []  # noqa: E731


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], capture_output=True, check=True, text=True).stdout


def commit_files(path, files):
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "x")


@pytest.fixture
def registry(tmp_path):
    r = tmp_path / "registry"
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    commit_files(r, {
        "plugins/b-plugin/.claude-plugin/plugin.json": json.dumps({"name": "b", "version": "1.0.0"}) + "\n",
        "plugins/b-plugin/skills/existing/SKILL.md": "---\nname: existing\ndescription: x\n---\n",
        "plugins/a-plugin/.claude-plugin/plugin.json": json.dumps({"name": "a", "version": "1.4.2"}) + "\n",
        "plugins/a-plugin/skills/zeta/SKILL.md": "---\nname: zeta\ndescription: z\n---\n",
        "scripts/plugin_layout.json": json.dumps({"plugins": {
            "b-plugin": {"defaultEnabled": False, "skills": ["existing"]},
            "a-plugin": {"defaultEnabled": True, "skills": ["zeta"]}}}, indent=2) + "\n",
        "docs/skill-impact.md": "# Skill impact\n",
    })
    return r


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "evals"
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    commit_files(r, {"README.md": "evals\n"})
    return r


def branch(repo, files):
    git(repo, "checkout", "-q", "-B", "routine", "main")
    commit_files(repo, {f"{P}/{k}": v for k, v in files.items()})
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    return sha


SKILL = {"skill/push-check/SKILL.md": SKILL_MD, "skill/push-check/references/notes.md": "More.\n",
         "fixtures/one/fixture.yaml": "skill: push-check\n"}


def check(repo, registry, sha, head=BRANCH, hook=CLEAN):
    return G.validate(str(repo), sha, head, str(registry), "main", hook)


def test_a_drafted_skill_is_accepted(repo, registry):
    info, reasons = check(repo, registry, branch(repo, SKILL))
    assert reasons == [] and info == {"kind": "skill", "run_id": RID, "skill": "push-check",
                                      "files": ["SKILL.md", "references/notes.md"]}


def test_a_hook_proposal_alone_is_accepted(repo, registry):
    info, reasons = check(repo, registry, branch(repo, {"hook-proposal.md": "Refuse *.orig files.\n"}))
    assert reasons == [] and info["kind"] == "hook"


@pytest.mark.parametrize("files,reason", [
    ({**SKILL, "hook-proposal.md": "x"}, "comes alone"),
    ({**SKILL, "skill/push-check/run.sh": "echo hi\n"}, "path-not-allowed"),
    ({**SKILL, "skill/other/SKILL.md": SKILL_MD.replace("push-check", "other")}, "exactly one skill"),
    ({"skill/push-check/references/a.md": "x\n"}, "no SKILL.md"),
    ({"skill/push-check/SKILL.md": "# no frontmatter\n"}, "no frontmatter"),
    ({"skill/push-check/SKILL.md": SKILL_MD.replace("name: push-check", "name: push")}, "name is not"),
    ({"skill/push-check/SKILL.md": SKILL_MD.replace("description: Check CI before you push. Use before git push.", "description: ''")},
     "description"),
    ({"skill/push-check/SKILL.md": SKILL_MD + "!`git push --force`\n"}, "shell command"),
    ({"skill/push-check/SKILL.md": SKILL_MD, "skill/push-check/references/r.md": "  !rm -rf /\n"}, "shell command"),
    ({"skill/existing/SKILL.md": SKILL_MD.replace("push-check", "existing")}, "already has a skill"),
])
def test_anything_else_is_refused(repo, registry, files, reason):
    _, reasons = check(repo, registry, branch(repo, files))
    assert reasons and reason in reasons[0], reasons


def test_a_file_outside_the_run_directory_or_a_wrong_branch_is_refused(repo, registry):
    git(repo, "checkout", "-q", "-B", "routine", "main")
    commit_files(repo, {f"{P}/hook-proposal.md": "x\n", "scripts/evil.py": "print(1)\n"})
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    assert "path-not-allowed" in check(repo, registry, sha)[1][0]
    sha = branch(repo, SKILL)
    assert check(repo, registry, sha, head=f"claude/eval-improve-{RID}")[1] == ["branch-name"]
    assert check(repo, registry, sha, hook=lambda a: ["SKILL.md:3: pattern 1"])[1][0].startswith("privacy")
    assert "no denylist" in check(repo, registry, sha, hook=IG.default_privacy_hook)[1][0]


def test_prepare_adds_the_skill_to_the_first_plugin_and_its_layout(repo, registry, tmp_path):
    sha = branch(repo, SKILL)
    info, _ = check(repo, registry, sha)
    res = G.prepare(info, str(repo), sha, str(registry), str(tmp_path / "out"), "2026-10-28")
    assert res == {"kind": "skill", "branch": f"eval-draft/{RID}", "title": "Add push-check (drafted skill)",
                   "skill": "push-check", "plugin": "a-plugin", "draft": True}
    changed = set(git(registry, "diff", "--name-only", "main", res["branch"]).split())
    assert changed == {"plugins/a-plugin/skills/push-check/SKILL.md",
                       "plugins/a-plugin/skills/push-check/references/notes.md",
                       "plugins/a-plugin/.claude-plugin/plugin.json",
                       "scripts/plugin_layout.json", "docs/skill-impact.md"}
    layout_text = git(registry, "show", f"{res['branch']}:scripts/plugin_layout.json")
    layout = json.loads(layout_text)
    assert layout["plugins"]["a-plugin"]["skills"] == ["push-check", "zeta"]
    assert list(layout["plugins"]) == ["a-plugin", "b-plugin"]
    assert layout_text == json.dumps(layout, indent=2) + "\n"
    assert json.loads(git(registry, "show", f"{res['branch']}:plugins/a-plugin/.claude-plugin/plugin.json"))["version"] == "1.5.0"
    assert git(registry, "show", f"{res['branch']}:plugins/a-plugin/skills/push-check/SKILL.md") == SKILL_MD


def test_prepare_for_a_hook_writes_a_sanitized_issue_body(repo, registry, tmp_path):
    sha = branch(repo, {"hook-proposal.md": "Refuse *.orig. Fixes #12, cc @someone\n"})
    info, _ = check(repo, registry, sha)
    res = G.prepare(info, str(repo), sha, str(registry), str(tmp_path / "out"), "2026-10-28")
    assert res["kind"] == "hook"
    body = (tmp_path / "out" / "issue.md").read_text()
    assert "#12" not in body and "@someone" not in body and "Refuse *.orig" in body
    assert git(registry, "branch", "--show-current").strip() == "main"     # the registry is untouched


def test_cli_exit_codes(repo, registry, tmp_path):
    sha = branch(repo, SKILL)
    base = ["--repo", str(repo), "--sha", sha, "--registry-dir", str(registry), "--base", "main"]
    assert G.main(["validate", *base, "--head-branch", BRANCH], privacy_hook=CLEAN) == 0
    assert G.main(["validate", *base, "--head-branch", "nope"], privacy_hook=CLEAN) == 1
    assert G.main(["prepare", *base, "--head-branch", BRANCH], privacy_hook=CLEAN) == 2
