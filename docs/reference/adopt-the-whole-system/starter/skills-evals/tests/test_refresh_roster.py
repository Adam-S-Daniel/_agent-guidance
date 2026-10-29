import json
import shutil

import pytest
import yaml

import fire_inputs as fi
import refresh_roster as R
from conftest import ROOT


def copies(tmp_path):
    src, roster = tmp_path / "src.yml", tmp_path / "roster.yml"
    shutil.copy(ROOT / "evals" / "roster-source.yml", src)
    shutil.copy(ROOT / "evals" / "roster.yml", roster)
    return src, roster


def test_the_shipped_source_reproduces_the_shipped_roster(tmp_path, capsys):
    src, roster = copies(tmp_path)
    before = roster.read_bytes()
    assert R.main(["--source", str(src), "--roster", str(roster), "--write"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False
    assert roster.read_bytes() == before


def test_a_new_top_model_rewrites_the_arms_and_keeps_the_header(tmp_path, capsys):
    src, roster = copies(tmp_path)
    doc = yaml.safe_load(src.read_text())
    doc["seats"] = 2
    doc["models"] += [{"id": "claude-haiku-5-0", "tier": "haiku", "share": 0.15},
                      {"id": "claude-opus-5-0", "tier": "opus", "share": 0.85}]
    src.write_text(yaml.safe_dump(doc))
    header = R.header_of(roster.read_text())
    assert R.main(["--source", str(src), "--roster", str(roster), "--write"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"changed": True, "arms": ["claude-opus-5-0", "claude-sonnet-5-5"], "judge": "claude-opus-5-0"}
    text = roster.read_text()
    assert text.startswith(header) and header
    parsed = yaml.safe_load(text)
    assert [a["id"] for a in parsed["arms"]] == ["claude-opus-5-0", "claude-sonnet-5-5"]
    assert parsed["arms"][0]["why"] == "85% of my recent turns"
    assert fi.roster_arm_ids(roster) == ["claude-opus-5-0", "claude-sonnet-5-5"]   # the fire workflow reads it


def test_without_write_nothing_changes(tmp_path, capsys):
    src, roster = copies(tmp_path)
    doc = yaml.safe_load(src.read_text())
    doc["judge"] = "claude-opus-6-0"
    src.write_text(yaml.safe_dump(doc))
    before = roster.read_bytes()
    assert R.main(["--source", str(src), "--roster", str(roster)]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is True and roster.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"seats": 0}, {"seats": 5}, {"judge": "gpt-x"}, {"extra": 1},
    {"models": [{"id": "claude-a", "tier": "t", "share": 2}]},
    {"models": [{"id": "claude-a", "tier": "t", "share": 0.5, "why": "line\nbreak"}]},
    {"models": [{"id": "claude-a", "tier": "t", "share": 0.5}, {"id": "claude-a", "tier": "t", "share": 0.1}]},
    {"models": [{"id": "claude-a", "tier": "t"}]},
])
def test_a_bad_source_is_exit_2_and_leaves_the_roster(tmp_path, change, capsys):
    src, roster = copies(tmp_path)
    doc = yaml.safe_load(src.read_text()) | change
    src.write_text(yaml.safe_dump(doc))
    before = roster.read_bytes()
    assert R.main(["--source", str(src), "--roster", str(roster), "--write"]) == 2
    assert roster.read_bytes() == before
