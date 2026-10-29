"""list_fires.sh with a gh stub that answers the two API calls from files."""
import json
import os
import subprocess

import allowance_guard as g
from conftest import ROOT

STUB = r"""#!/usr/bin/env bash
case "$*" in
  *"/workflows/routine-eval-fire.yml/runs"*) printf '11\tfire eval 20261027T090000Z-0000aa\t2026-10-27T09:00:05Z\n12\tfire draft 20261027T100000Z-0000bb\t2026-10-27T10:00:05Z\n' ;;
  *"/runs/11/jobs"*) echo true ;;
  *"/runs/12/jobs"*) echo false ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


def run(tmp_path, start, repo="acme-example/skills-evals"):
    (tmp_path / "gh").write_text(STUB)
    (tmp_path / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GITHUB_REPOSITORY": repo}
    return subprocess.run(["bash", str(ROOT / "scripts" / "list_fires.sh"), start],
                          env=env, capture_output=True, text=True)


def test_lists_each_dispatch_and_whether_it_fired_in_the_guards_format(tmp_path):
    r = run(tmp_path, "2026-10-26T17:00:00Z")
    assert r.returncode == 0, r.stderr
    fires = json.loads(r.stdout)
    assert fires == [
        {"name": "fire eval 20261027T090000Z-0000aa", "created_at": "2026-10-27T09:00:05Z", "fired": True},
        {"name": "fire draft 20261027T100000Z-0000bb", "created_at": "2026-10-27T10:00:05Z", "fired": False}]
    path = tmp_path / "fires.json"
    path.write_text(r.stdout)
    assert len(g.read_fires(path)) == 2          # the guard reads it as-is


def test_refuses_a_bad_window_or_repo(tmp_path):
    assert run(tmp_path, "last monday").returncode == 2
    assert run(tmp_path, "2026-10-26T17:00:00Z", repo="x; rm").returncode == 2
