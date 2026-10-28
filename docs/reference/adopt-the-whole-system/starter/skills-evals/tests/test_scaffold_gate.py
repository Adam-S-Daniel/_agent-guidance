import json
import shutil
import subprocess

import pytest
import yaml

import improve_gate as IG
import scaffold_gate as S
from conftest import FIXTURE_SRC, ROOT

RID = "20261028T120000Z-1a2b3c"
BRANCH = f"claude/eval-scaffold-{RID}"
FX = "evals/workflow-path-audit/from-pr-12"
SOURCE = {"repo": "acme-example/app", "number": 12, "fixture": FX}
CLEAN = lambda added: []          # noqa: E731  the privacy hook, with nothing on the denylist


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], capture_output=True, check=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "evals-repo"
    (r / "evals").mkdir(parents=True)
    shutil.copy(ROOT / "evals" / "scheduled.yml", r / "evals" / "scheduled.yml")
    (r / "evals" / "scaffold-sources.json").write_text('["acme-example/app#9"]\n')
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r


def branch(repo, mutate=None, source=SOURCE):
    git(repo, "checkout", "-q", "-B", "routine", "main")
    shutil.copytree(FIXTURE_SRC, repo / FX)
    d = repo / "eval-scaffold" / RID
    d.mkdir(parents=True)
    (d / "source.json").write_text(json.dumps(source))
    if mutate:
        mutate(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "scaffold")
    sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    return sha


def check(repo, sha, head=BRANCH, hook=CLEAN):
    return S.validate(str(repo), sha, head, "main", hook)


def test_a_good_scaffold_branch_is_accepted(repo):
    info, reasons = check(repo, branch(repo))
    assert reasons == [] and info == {"run_id": RID, "fixture": FX, "skill": "workflow-path-audit",
                                      "source": "acme-example/app#12"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: write(r / "scripts" / "x.py", "print(1)\n"), "path-not-allowed"),
    (lambda r: write(r / "evals" / "other" / "f" / "fixture.yaml", "skill: other\n"), "more than one fixture"),
    (lambda r: (r / FX / "fixture.yaml").write_text("skill: workflow-path-audit\n"), "fixture:"),
    (lambda r: (r / FX / "fixture.yaml").write_text(
        (r / FX / "fixture.yaml").read_text().replace("skill: workflow-path-audit", "skill: other-skill")),
     "skill is not the directory"),
    (lambda r: (r / FX / "seed" / "big.bin").write_bytes(b"0" * (S.MAX_FILE + 1)), "too large"),
    (lambda r: (r / FX / "seed" / "link").symlink_to("/etc/passwd"), "not-a-plain-added-file"),
])
def test_anything_else_on_the_branch_is_refused(repo, mutate, reason):
    _, reasons = check(repo, branch(repo, mutate))
    assert reasons and reason in reasons[0], reasons


def test_a_git_segment_or_dot_dot_is_never_a_fixture_path():
    for bad in (f"{FX}/seed/.git/config", f"{FX}/../x", f"{FX}/./x"):
        m = S.FIXTURE_FILE.match(bad)
        assert not m or S.BAD_SEGMENTS & set(bad.split("/"))
    assert S.FIXTURE_FILE.match(f"{FX}/seed/.github/workflows/ci.yml")


def test_a_changed_existing_file_is_refused(repo):
    _, reasons = check(repo, branch(repo, lambda r: (r / "evals" / "scheduled.yml").write_text("fixtures: []\n")))
    assert "not-a-plain-added-file" in reasons[0]


@pytest.mark.parametrize("source,reason", [
    ({**SOURCE, "fixture": "evals/workflow-path-audit/basic"}, "not the added fixture"),
    ({**SOURCE, "number": "12"}, "number"),
    ({**SOURCE, "repo": "acme/app; rm -rf"}, "owner/repo"),
    ({**SOURCE, "extra": 1}, "exactly"),
    ({**SOURCE, "number": 9}, "already became a fixture"),
])
def test_the_source_file_is_strict_and_each_pr_scaffolds_once(repo, source, reason):
    _, reasons = check(repo, branch(repo, source=source))
    assert reason in reasons[0], reasons


def test_branch_name_and_existing_fixture_and_privacy(repo):
    sha = branch(repo)
    assert check(repo, sha, head="claude/eval-improve-" + RID)[1] == ["branch-name"]
    assert check(repo, sha, hook=lambda added: ["seed/README.md:1: pattern 1"])[1][0].startswith("privacy")
    assert "privacy: no denylist" in check(repo, sha, hook=IG.default_privacy_hook)[1][0]


def test_prepare_builds_the_fixture_commit_with_a_draft_row_and_the_used_source(repo, tmp_path):
    sha = branch(repo)
    info, _ = check(repo, sha)
    out = tmp_path / "out"
    res = S.prepare(info, str(repo), sha, "main", str(out), {**IG.DEFAULT_POLICY, "improve_merge_mode": "draft"})
    assert res == {"branch": f"eval-scaffold/{RID}", "fixture": FX, "title": f"Add draft fixture {FX}", "draft": True}
    changed = set(git(repo, "diff", "--name-only", "main", res["branch"]).split())
    assert "evals/scheduled.yml" in changed and "evals/scaffold-sources.json" in changed
    assert {p for p in changed if not p.startswith(FX + "/")} == {"evals/scheduled.yml", "evals/scaffold-sources.json"}
    rows = yaml.safe_load(git(repo, "show", f"{res['branch']}:evals/scheduled.yml"))["fixtures"]
    assert rows[-1] == {"fixture": FX, "status": "draft"}
    assert json.loads(git(repo, "show", f"{res['branch']}:evals/scaffold-sources.json")) == \
        ["acme-example/app#9", "acme-example/app#12"]
    assert "#12" not in (out / "pr-body.md").read_text()     # no autolink to an issue here
    assert json.loads((out / "result.json").read_text())["draft"] is True


def test_auto_merge_mode_makes_a_ready_pr(repo, tmp_path):
    sha = branch(repo)
    info, _ = check(repo, sha)
    assert S.prepare(info, str(repo), sha, "main", str(tmp_path / "o"), dict(IG.DEFAULT_POLICY))["draft"] is False


def test_cli_exit_codes(repo, tmp_path, capsys):
    sha = branch(repo)
    pol = tmp_path / "p.yml"
    pol.write_text("improve_merge_mode: draft\n")
    base = ["--repo", str(repo), "--sha", sha, "--base", "main", "--policy", str(pol)]
    assert S.main(["validate", *base, "--head-branch", BRANCH], privacy_hook=CLEAN) == 0
    assert S.main(["validate", *base, "--head-branch", "x"], privacy_hook=CLEAN) == 1
    assert S.main(["prepare", *base, "--head-branch", BRANCH], privacy_hook=CLEAN) == 2   # no --out
