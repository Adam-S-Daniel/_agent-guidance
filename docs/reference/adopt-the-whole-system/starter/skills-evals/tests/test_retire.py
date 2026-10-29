import json
import os
import subprocess

import pytest

import retire_skill as RS


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], capture_output=True, check=True, text=True).stdout


@pytest.fixture
def reg(tmp_path):
    r = tmp_path / "reg"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    files = {
        "plugins/p1/.claude-plugin/plugin.json": json.dumps({"name": "p1", "version": "1.4.2"}, indent=2) + "\n",
        "plugins/p1/skills/old-skill/SKILL.md": "---\nname: old-skill\ndescription: d\n---\nBody\n",
        "plugins/p1/skills/old-skill/references/a.md": "ref\n",
        "plugins/p1/skills/keep-skill/SKILL.md": "---\nname: keep-skill\ndescription: d\n---\nBody\n",
        "docs/skill-impact.md": "# Skill impact\n\n## 2026-01-01: created\n",
        "README.md": "# Registry\n\n- `old-skill`: does a thing\n- `keep-skill`: does another\n- old-skill-extra: unrelated\n",
        "scripts/plugin_layout.json": json.dumps({"plugins": {"p1": ["keep-skill", "old-skill"]}}, indent=2) + "\n",
    }
    for rel, text in files.items():
        f = r / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def verdict(tmp_path, v="retire", skill="old-skill"):
    p = tmp_path / "verdict.json"
    p.write_text(json.dumps({"cells": [], "skills": {skill: {"verdict": v, "classes": ["no-benefit"] * 3}}}))
    return str(p)


def args(reg, tmp_path, *extra, skill="old-skill", v="retire"):
    return ["--registry", str(reg), "--skill", skill, "--verdict", verdict(tmp_path, v, skill),
            "--today", "2026-10-28", *extra]


def test_dry_run_prints_the_removal_and_changes_nothing(reg, tmp_path, capsys):
    before = git(reg, "rev-parse", "HEAD")
    assert RS.main(args(reg, tmp_path, "--dry-run")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["plugin"] == "p1" and out["draft"] is False
    assert out["version"] == {"from": "1.4.2", "to": "2.0.0"}
    assert out["delete"] == ["plugins/p1/skills/old-skill/SKILL.md", "plugins/p1/skills/old-skill/references/a.md"]
    assert out["branch"] == "retire-skill/old-skill"
    assert "## 2026-10-28: removed `old-skill`" in out["impact_entry"]
    assert {(x["file"], x["removed"]) for x in out["listings"]} == {("README.md", 1), ("scripts/plugin_layout.json", 1)}
    assert git(reg, "status", "--porcelain") == "" and git(reg, "rev-parse", "HEAD") == before
    assert (reg / "plugins/p1/skills/old-skill").exists()


def test_real_run_makes_one_removal_commit(reg, tmp_path, capsys):
    assert RS.main(args(reg, tmp_path)) == 0
    assert git(reg, "rev-parse", "--abbrev-ref", "HEAD").strip() == "retire-skill/old-skill"
    assert not (reg / "plugins/p1/skills/old-skill").exists()
    assert (reg / "plugins/p1/skills/keep-skill/SKILL.md").exists()
    assert json.loads((reg / "plugins/p1/.claude-plugin/plugin.json").read_text())["version"] == "2.0.0"
    assert "removed `old-skill`" in (reg / "docs/skill-impact.md").read_text()
    readme = (reg / "README.md").read_text()
    assert "`old-skill`" not in readme and "keep-skill" in readme and "old-skill-extra" in readme
    assert json.loads((reg / "scripts/plugin_layout.json").read_text()) == {"plugins": {"p1": ["keep-skill"]}}
    assert git(reg, "status", "--porcelain") == ""
    assert git(reg, "log", "--oneline").count("\n") == 2


def test_refuses_a_verdict_that_is_not_retire(reg, tmp_path):
    assert RS.main(args(reg, tmp_path, v="keep")) == 3
    assert (reg / "plugins/p1/skills/old-skill").exists()


def test_exit_codes_for_missing_skill_and_bad_input(reg, tmp_path):
    assert RS.main(args(reg, tmp_path, skill="ghost")) == 4
    assert RS.main(["--registry", str(reg), "--skill", "../x", "--verdict", "v"]) == 2
    assert RS.main(["--registry", str(reg), "--skill", "old-skill", "--verdict", str(tmp_path / "none")]) == 2
    assert RS.main([]) == 2


def test_plugin_that_runs_code_is_flagged_draft(reg, tmp_path, capsys):
    (reg / "plugins/p1/bin").mkdir()
    (reg / "plugins/p1/bin/x").write_text("x\n")
    RS.main(args(reg, tmp_path, "--dry-run"))
    assert json.loads(capsys.readouterr().out)["draft"] is True
