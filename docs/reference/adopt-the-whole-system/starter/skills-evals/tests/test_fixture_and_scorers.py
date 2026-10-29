import copy
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import FIXTURE_SRC
from harness.fixture import FixtureError, fixture_digest, load_fixture, validate_fixture
from harness.scorers import run_check
from harness.scorers.common import ScorerError, scratch_git_env
import harness.scorers.command_succeeds as command_succeeds
import harness.scorers.file_unchanged as file_unchanged
import harness.scorers.yaml_parses as yaml_parses

GOOD = load_fixture(FIXTURE_SRC)


def variant(mutate):
    doc = copy.deepcopy(GOOD)
    mutate(doc)
    return doc


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(surprise=1),                                   # unknown top-level key
    lambda d: d["objective_checks"][0].update(extra=1),               # unknown key in a check
    lambda d: d["arms"]["with_skill"].update(flavor="x"),
    lambda d: d["judge"].update(model="x"),
    lambda d: d["objective_checks"][1].pop("command"),                # command_succeeds needs a command
    lambda d: d["objective_checks"][0].update(command="rm -rf /"),    # yaml_parses takes no command
    lambda d: d["objective_checks"][1].update(paths=["a"]),
    lambda d: d["objective_checks"][0].update(type="shell"),          # unknown scorer
    lambda d: d.update(model="claude-sonnet-5-5"),                    # a pin needs its reason
    lambda d: d.update(model_reason="because"),
    lambda d: d.update(model="claude-sonnet-5-5\n", model_reason="trailing newline"),
    lambda d: d["judge"]["weights"].update(completeness=0.9),         # weights must sum to 1
    lambda d: d["objective_checks"].append(dict(d["objective_checks"][0])),  # duplicate id
    lambda d: d.update(prompt="--dangerously-skip-permissions"),      # reads as a flag
    lambda d: d.pop("judge"),
    lambda d: d["arms"].pop("without_skill"),
])
def test_schema_refuses(mutate):
    with pytest.raises(FixtureError):
        validate_fixture(variant(mutate))


def test_a_pinned_model_with_its_reason_is_accepted():
    doc = variant(lambda d: d.update(model="claude-sonnet-5-5", model_reason="the skill targets it"))
    validate_fixture(doc)


def test_load_refuses_missing_seed_and_unparsable_yaml(tmp_path):
    (tmp_path / "fixture.yaml").write_text("skill: [unclosed")
    with pytest.raises(FixtureError):
        load_fixture(tmp_path)
    shutil.copy(FIXTURE_SRC / "fixture.yaml", tmp_path / "fixture.yaml")
    with pytest.raises(FixtureError):
        load_fixture(tmp_path)


def test_digest_moves_with_seed_checks_and_fixture_yaml(tmp_path):
    d = tmp_path / "f"
    shutil.copytree(FIXTURE_SRC, d)
    base = fixture_digest(d)
    assert fixture_digest(d) == base
    for rel in ("seed/README.md", "checks/replay.py", "fixture.yaml"):
        p = d / rel
        old = p.read_bytes()
        p.write_bytes(old + b"\n# x\n")
        assert fixture_digest(d) != base
        p.write_bytes(old)
    assert fixture_digest(d) == base


# --- the example fixture's own checks: fail on the seed, pass on a solution --

def workspace(tmp_path, overlay=False):
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_SRC / "seed", ws)
    base = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin"}
    with scratch_git_env(base) as env:
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-C", str(ws), *cmd], check=True, env=env, capture_output=True)
    if overlay:
        shutil.copytree(FIXTURE_SRC / "solution", ws, dirs_exist_ok=True)
    shutil.copytree(FIXTURE_SRC / "checks", ws / "checks")
    return ws


def results(ws):
    return {c["id"]: c for c in (run_check(c, ws) for c in GOOD["objective_checks"])}


def test_example_fixture_checks_fail_on_the_seed(tmp_path):
    r = results(workspace(tmp_path))
    assert r["docs-change-skips-tests"]["passed"] is False
    assert r["code-change-runs-tests"]["passed"] is True
    assert r["workflows-parse"]["passed"] and r["ruleset-untouched"]["passed"]


def test_example_fixture_checks_pass_on_a_hand_made_solution(tmp_path):
    r = results(workspace(tmp_path, overlay=True))
    assert all(c["passed"] and not c["error"] for c in r.values()), r


def test_deleting_the_workflow_does_not_pass_the_run_check(tmp_path):
    ws = workspace(tmp_path)
    (ws / ".github/workflows/tests.yml").unlink()
    r = results(ws)
    assert not r["docs-change-skips-tests"]["passed"] and not r["code-change-runs-tests"]["passed"]


# --- scorers ----------------------------------------------------------------

def test_yaml_parses(tmp_path):
    (tmp_path / "a.yml").write_text("a: 1\n")
    assert yaml_parses.score({"paths": ["*.yml"]}, tmp_path)[0] is True
    (tmp_path / "b.yml").write_text("a: [unclosed\n")
    assert yaml_parses.score({"paths": ["*.yml"]}, tmp_path)[0] is False
    with pytest.raises(ScorerError):
        yaml_parses.score({"paths": ["*.nothing"]}, tmp_path)
    with pytest.raises(ScorerError):
        yaml_parses.score({"paths": ["../*.yml"]}, tmp_path)


def test_command_succeeds(tmp_path):
    assert command_succeeds.score({"command": "python3 -c 'import sys; sys.exit(0)'"}, tmp_path)[0] is True
    assert command_succeeds.score({"command": "python3 -c 'import sys; sys.exit(3)'"}, tmp_path) == (False, "exit 3")
    with pytest.raises(ScorerError):
        command_succeeds.score({"command": "definitely-not-a-command-xyz"}, tmp_path)
    with pytest.raises(ScorerError):
        command_succeeds.score({"command": "  "}, tmp_path)


def test_command_succeeds_does_not_see_runner_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "leak-me")
    ok, _ = command_succeeds.score(
        {"command": "python3 -c 'import os,sys; sys.exit(1 if \"GITHUB_TOKEN\" in os.environ else 0)'"}, tmp_path)
    assert ok


def test_file_unchanged(tmp_path):
    ws = workspace(tmp_path)
    chk = {"paths": [".github/rulesets/main.json"]}
    assert file_unchanged.score(chk, ws)[0] is True
    p = ws / ".github/rulesets/main.json"
    p.write_text(p.read_text() + " ")
    assert file_unchanged.score(chk, ws)[0] is False
    p.unlink()
    assert file_unchanged.score(chk, ws)[0] is False
    with pytest.raises(ScorerError):
        file_unchanged.score({"paths": ["nope/*.json"]}, ws)


def test_an_errored_check_is_a_failure_with_an_error_flag(tmp_path):
    ws = workspace(tmp_path)
    r = run_check({"id": "x", "type": "yaml_parses", "paths": ["*.nothing"]}, ws)
    assert (r["passed"], r["error"]) == (False, True)
    r = run_check({"id": "y", "type": "command_succeeds", "command": "no-such-binary-qq"}, ws)
    assert (r["passed"], r["error"]) == (False, True)
    r = run_check({"id": "z", "type": "unknown_type"}, ws)  # a scorer bug must not skip the check
    assert (r["passed"], r["error"]) == (False, True)
    assert run_check({"id": "w", "type": "command_succeeds", "command": "python3 -c 'exit(1)'"}, ws)["error"] is False


# --- git config isolation (regression: GIT_CONFIG_GLOBAL=/dev/null) ---------
# A check or git call that writes global config as root renamed a lock file
# over /dev/null when the harness pointed GIT_CONFIG_GLOBAL there. Each call
# now gets its own scratch HOME and empty config file.

def test_a_global_git_write_in_a_check_lands_in_a_scratch_file(tmp_path):
    cmd = ("sh -c 'git config --global user.name x"
           " && cp \"$GIT_CONFIG_GLOBAL\" observed && printf %s \"$GIT_CONFIG_GLOBAL\" > where"
           " && printf %s \"$HOME\" > home'")
    result = run_check({"id": "writes-global", "type": "command_succeeds", "command": cmd}, tmp_path)
    assert result["passed"] and not result["error"], result
    assert "name = x" in (tmp_path / "observed").read_text()
    where = Path((tmp_path / "where").read_text())
    assert where != Path(os.devnull) and where.name == "gitconfig"
    assert Path((tmp_path / "home").read_text()).parent == where.parent
    assert not where.exists()  # removed after the call
    assert stat.S_ISCHR(os.stat(os.devnull).st_mode)


def test_harness_git_calls_never_name_dev_null(tmp_path, monkeypatch):
    from harness import run_eval
    seen = []
    real = subprocess.run

    def spy(argv, **kw):
        env = kw.get("env") or {}
        seen.append(dict(env))
        assert os.devnull not in env.values(), env
        return real(argv, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "a.txt").write_text("a\n")
    ws = run_eval.make_workspace(tmp_path / "seed", tmp_path / "ws")
    assert file_unchanged.score({"paths": ["a.txt"]}, ws)[0] is True
    assert len(seen) >= 4
    for env in seen:
        assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"].endswith("gitconfig")
        assert not Path(env["GIT_CONFIG_GLOBAL"]).exists()
    assert stat.S_ISCHR(os.stat(os.devnull).st_mode)
