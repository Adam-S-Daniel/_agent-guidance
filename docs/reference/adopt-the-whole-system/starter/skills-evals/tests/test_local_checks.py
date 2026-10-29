"""The repo's .agent-checks/local.sh template: it lists the paths and, as
shipped, refuses nothing. Its full red/green test (the example line enabled)
is in agent-guidance's tests/test-kit.sh, where the template comes from."""
import subprocess

from conftest import ROOT
from harness.scorers.common import scratch_git_env

LOCAL = ROOT / ".agent-checks" / "local.sh"


def _run(env, cwd, *args):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True)


def test_local_checks_template_passes_an_empty_list_and_refuses_nothing(tmp_path):
    with scratch_git_env({"PATH": "/usr/local/bin:/usr/bin:/bin"}) as env:
        assert _run(env, tmp_path, "git", "init", "-q").returncode == 0
        assert _run(env, tmp_path, "bash", str(LOCAL), "pre-commit").returncode == 0
        (tmp_path / "a.py.orig").write_text("x\n")
        assert _run(env, tmp_path, "git", "add", "-A").returncode == 0
        done = _run(env, tmp_path, "bash", str(LOCAL), "pre-commit")
        assert done.returncode == 0, done.stderr
        assert _run(env, tmp_path, "bash", str(LOCAL), "pre-push").returncode == 0
