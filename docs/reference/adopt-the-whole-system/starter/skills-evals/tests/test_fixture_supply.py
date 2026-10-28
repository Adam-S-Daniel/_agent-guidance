import json

import pytest
import yaml

import dispatch_fires as D
import fire_inputs as fi
import fixture_supply as S
import pick_fixture_candidates as P
from conftest import ROOT

GOOD = {"repos": ["acme-example/app"], "skills": {"workflow-path-audit": [r"^\.github/workflows/"]}, "per_day": 2}


def prepare(tmp_path, sources=GOOD, used=("acme-example/app#3",)):
    src, u = tmp_path / "s.yml", tmp_path / "u.json"
    src.write_text(yaml.safe_dump(sources))
    u.write_text(json.dumps(list(used)))
    return S.main(["prepare", "--sources", str(src), "--used", str(u), "--out", str(tmp_path / "o")])


def test_prepare_feeds_the_picker_and_plan_feeds_the_fire_workflow(tmp_path, capsys):
    assert prepare(tmp_path) == 0
    out = tmp_path / "o"
    assert (out / "repos.txt").read_text() == "acme-example/app\n"
    assert (out / "limit.txt").read_text() == "2\n"
    ev = json.loads((out / "evidence.json").read_text())
    prs = [{"repo": "acme-example/app", "number": n, "merged_at": "2026-10-27T10:00:00Z",
            "files": [".github/workflows/ci.yml", "src/a.py"]} for n in (3, 4)]
    picks = P.rank(prs, ev["skills"], set(ev["used"]), limit=2)
    assert [p["number"] for p in picks] == [4]               # #3 is already a fixture
    (tmp_path / "picks.json").write_text(json.dumps(picks))
    assert S.main(["plan", "--picks", str(tmp_path / "picks.json")]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan == {"fires": [{"mode": "scaffold", "repo": "acme-example/app", "number": 4}]}
    sent = D.inputs_for(plan["fires"][0], "20261028T120000Z-1a2b3c")
    event = {"inputs": {k: "" for k in fi.KNOWN} | {"arms": "both", "trials": "3"} | sent}
    assert fi.build_payload(event, None, None) == {"mode": "scaffold", "repo": "acme-example/app",
                                                    "number": 4, "run_id": "20261028T120000Z-1a2b3c"}


@pytest.mark.parametrize("change", [
    {"repos": ["<" "you>/agent-guidance"]}, {"repos": []},     # split so copy-starter.sh leaves it {"per_day": 4}, {"per_day": "1"},
    {"skills": {"Bad": ["x"]}}, {"skills": {"ok": ["("]}}, {"skills": {"ok": []}}, {"extra": True},
])
def test_a_bad_sources_file_is_exit_2_and_writes_nothing(tmp_path, change, capsys):
    assert prepare(tmp_path, GOOD | change) == 2
    assert not (tmp_path / "o").exists()


def test_the_shipped_sources_file_is_refused_until_the_placeholder_is_filled(tmp_path):
    shipped = ROOT / "evals" / "fixture-sources.yml"
    text = shipped.read_text()
    assert set(yaml.safe_load(text)) == {"repos", "skills", "per_day"}
    placeholder = "<" "you>"            # split so copy-starter.sh leaves it
    if placeholder in text:
        with pytest.raises(S.BadInput, match="replace "):
            S.load_sources(shipped)
    filled = tmp_path / "f.yml"
    filled.write_text(text.replace(placeholder, "acme-example"))
    assert len(S.load_sources(filled)[0]) == 1


def test_plan_refuses_a_bad_pick(tmp_path):
    (tmp_path / "p.json").write_text(json.dumps([{"repo": "x; y", "number": 1}]))
    assert S.main(["plan", "--picks", str(tmp_path / "p.json")]) == 2
