"""Every fixture shipped under evals/ loads with the harness's own loader, and
one with a solution/ is a real test: its checks all pass on seed + solution
and at least one fails on the seed alone. Workspaces and scoring use the
harness's own functions, so git and each check get a scratch HOME and config."""
import os
import shutil
import stat

import pytest
import yaml

from conftest import ROOT
from harness.fixture import load_fixture
from harness.run_eval import copy_checks, make_workspace
from harness.scorers import run_check

FIXTURES = sorted(p.parent for p in (ROOT / "evals").glob("*/*/fixture.yaml"))


def ids(dirs):
    return [d.relative_to(ROOT).as_posix() for d in dirs]


def scored(fixture_dir, tmp_path, with_solution):
    fx = load_fixture(fixture_dir)
    ws = make_workspace(fixture_dir / "seed", tmp_path / "ws")
    if with_solution:
        shutil.copytree(fixture_dir / "solution", ws, dirs_exist_ok=True, symlinks=True)
    copy_checks(fixture_dir, ws)
    return [run_check(c, ws) for c in fx["objective_checks"]]


def test_the_shipped_fixtures_are_found():
    assert {"evals/example-skill/basic", "evals/workflow-path-audit/basic"} <= set(ids(FIXTURES))


@pytest.mark.parametrize("fixture_dir", FIXTURES, ids=ids(FIXTURES))
def test_fixture_loads_and_its_skill_matches_its_directory(fixture_dir):
    fx = load_fixture(fixture_dir)
    assert fx["skill"] == fixture_dir.parent.name


@pytest.mark.parametrize("fixture_dir", [d for d in FIXTURES if (d / "solution").is_dir()],
                         ids=ids([d for d in FIXTURES if (d / "solution").is_dir()]))
def test_checks_pass_on_the_solution_and_fail_on_the_seed(fixture_dir, tmp_path):
    solved = scored(fixture_dir, tmp_path / "solved", True)
    assert all(c["passed"] and not c["error"] for c in solved), solved
    seed = scored(fixture_dir, tmp_path / "seed", False)
    assert not all(c["passed"] for c in seed), seed
    assert not any(c["error"] for c in seed), seed
    assert stat.S_ISCHR(os.stat(os.devnull).st_mode)


def test_every_scheduled_row_names_a_shipped_fixture():
    rows = yaml.safe_load((ROOT / "evals" / "scheduled.yml").read_text())["fixtures"]
    assert {r["fixture"] for r in rows} <= set(ids(FIXTURES))
