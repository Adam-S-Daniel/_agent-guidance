import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# A small repo root of the tests' own: the guide's example fixture lives here,
# so the suite never depends on which fixtures evals/ holds.
FIXTURE_REPO = ROOT / "tests" / "fixtures" / "repo"
FIXTURE_SRC = FIXTURE_REPO / "evals" / "workflow-path-audit" / "basic"
NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)  # a Wednesday
RUN_ID = "20261028T120000Z-1a2b3c"


class Rig:
    """A temp world: copied fixture, budget, roster, skill, and a fake claude on PATH."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        shutil.copy(ROOT / "tests" / "fake_claude.py", fake)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.fixture = tmp / "evals" / "workflow-path-audit" / "basic"
        shutil.copytree(FIXTURE_SRC, self.fixture)
        shutil.copy(ROOT / "evals" / "budget.yml", tmp / "budget.yml")
        shutil.copy(ROOT / "evals" / "roster.yml", tmp / "roster.yml")
        self.skill = tmp / "skill" / "workflow-path-audit"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("---\nname: workflow-path-audit\n---\nAudit paths.\n")
        self.out = tmp / "out"
        self.work = tmp / "work"
        self.set_fake(meter=0.10, step=0.001, resets=1791660000, mode="normal",
                      solution=str(self.fixture / "solution"))
        (self.bin / "calls.jsonl").write_text("")
        self.environ = {"PATH": f"{self.bin}:{os.environ['PATH']}", "LANG": "C.UTF-8",
                        "ANTHROPIC_API_KEY": "test-not-a-key", "GITHUB_TOKEN": "leak-me",
                        "APP_PRIVATE_KEY": "leak-me", "SECRET_THING": "leak-me",
                        "HOME": "/real/home"}

    def set_fake(self, **kw):
        p = self.bin / "fake.json"
        cur = json.loads(p.read_text()) if p.exists() else {}
        cur.update(kw)
        p.write_text(json.dumps(cur))

    def calls(self, kind=None):
        rows = [json.loads(l) for l in (self.bin / "calls.jsonl").read_text().splitlines() if l]
        return [r for r in rows if kind is None or r["kind"] == kind]

    def run(self, *extra, sandbox=True, arms="both", trials="2"):
        from harness import run_eval
        ticks = iter(range(10_000))
        argv = ["--fixture", str(self.fixture), "--arms", arms, "--trials", trials,
                "--run-id", RUN_ID, "--out", str(self.out), "--skill-dir", str(self.skill),
                "--roster", str(self.tmp / "roster.yml"), "--budget", str(self.tmp / "budget.yml"),
                "--work-dir", str(self.work), *extra]
        return run_eval.main(argv, environ=self.environ, now=lambda: NOW,
                             clock=lambda: float(next(ticks)), sandbox_check=lambda: sandbox)

    def summary(self, arm):
        return json.loads((self.out / arm / "summary.json").read_text())

    def run_doc(self):
        return json.loads((self.out / "run.json").read_text())


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)
