"""open_loop_pr.sh against a local bare repo: github.com is rewritten to a
file:// path in a scratch GIT_CONFIG_GLOBAL, and gh is a stub that logs."""
import os
import subprocess

import pytest

from conftest import ROOT

SCRIPT = ROOT / "scripts" / "open_loop_pr.sh"
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr list") printf '%s\\n' "${GH_OPEN:-0}" ;;
  "pr create") echo "https://github.com/acme-example/agent-skills/pull/9" ;;
esac
"""


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "gitconfig"
    remotes = tmp_path / "remotes"
    bare = remotes / "acme-example" / "agent-skills.git"
    bare.parent.mkdir(parents=True)
    env = {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "HOME": str(home),
           "GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1",
           "GH_LOG": str(tmp_path / "gh.log"), "GH_TOKEN": "ghs_" + "FAKE000000000000000000000000000000"}
    def git(*a, cwd=None):
        subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)
    git("config", "--global", "user.name", "t")
    git("config", "--global", "user.email", "t@example.com")
    git("config", "--global", f"url.file://{remotes}/.insteadOf", "https://github.com/")
    git("init", "-q", "--bare", str(bare))
    work = tmp_path / "work"
    git("init", "-q", "-b", "main", str(work))
    (work / "a.txt").write_text("a\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "a", cwd=work)
    git("checkout", "-q", "-b", "retire-skill/demo", cwd=work)
    (work / "a.txt").write_text("b\n")
    git("commit", "-qam", "b", cwd=work)
    (tmp_path / "bin").mkdir()
    gh = tmp_path / "bin" / "gh"
    gh.write_text(STUB)
    gh.chmod(0o755)
    body = tmp_path / "body.md"
    body.write_text("Remove demo.\n")
    return {"env": env, "work": work, "bare": bare, "body": body, "log": tmp_path / "gh.log"}


def run(w, draft="false", **extra):
    args = ["bash", str(SCRIPT), "--repo", "acme-example/agent-skills", "--dir", str(w["work"]),
            "--branch", extra.pop("branch", "retire-skill/demo"), "--title", "Remove demo",
            "--body-file", str(w["body"]), "--draft", draft]
    return subprocess.run(args, env={**w["env"], **extra}, capture_output=True, text=True)


def head(w):
    return subprocess.run(["git", "-C", str(w["work"]), "rev-parse", "retire-skill/demo"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_pushes_the_branch_opens_a_labeled_pr_and_pins_auto_merge_to_the_commit(world):
    r = run(world)
    assert r.returncode == 0, r.stderr
    remote = subprocess.run(["git", "--git-dir", str(world["bare"]), "rev-parse", "retire-skill/demo"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert remote == head(world)
    log = world["log"].read_text()
    assert "label create agent-authored --repo acme-example/agent-skills --force" in log
    assert "pr create --repo acme-example/agent-skills --head retire-skill/demo --label agent-authored" in log
    assert "--draft" not in log
    assert f"pr merge --auto --merge --match-head-commit {head(world)}" in log
    assert world["env"]["GH_TOKEN"] not in r.stdout + r.stderr + log


def test_a_draft_never_turns_on_auto_merge(world):
    assert run(world, draft="true").returncode == 0
    log = world["log"].read_text()
    assert "--draft" in log and "pr merge" not in log


def test_an_open_loop_pr_means_nothing_is_pushed_or_opened(world):
    r = run(world, GH_OPEN="1")
    assert r.returncode == 0 and "already open" in r.stdout
    log = world["log"].read_text()
    assert "pr create" not in log
    refs = subprocess.run(["git", "--git-dir", str(world["bare"]), "for-each-ref"],
                          capture_output=True, text=True).stdout
    assert refs == ""


@pytest.mark.parametrize("extra", [{"branch": "main"}, {"branch": "retire-skill/x;y"}, {"GH_TOKEN": ""}])
def test_bad_usage_is_exit_2_before_any_call(world, extra):
    assert run(world, **extra).returncode == 2
    assert not world["log"].exists()
