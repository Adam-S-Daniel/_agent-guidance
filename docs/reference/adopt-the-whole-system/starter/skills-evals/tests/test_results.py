import json
import os

import pytest

from harness import results as R
import run_dirs as D

RID = D.run_id_for(1)


def two_arms(root, rid=RID, with_trials=None, judge=(None, None), **kw):
    with_trials = with_trials or [D.ok(1, 0.8), D.ok(2, 1.0)]
    return D.write_run(root, rid, {"with_skill": (with_trials, judge[0]),
                                   "without_skill": ([D.ok(1, 0.4), D.ok(2, 0.6)], judge[1])}, **kw)


def load(root):
    return R.load_runs(str(root))


def test_reads_run_json_and_each_arm_summary(tmp_path):
    two_arms(tmp_path, cli="2.1.300", skill="s", fixture="evals/s/f", model="claude-m")
    (r,) = load(tmp_path)
    assert (r.run_id, r.skill, r.fixture, r.model) == (RID, "s", "evals/s/f", "claude-m")
    assert r.cli_version == "2.1.300" and r.with_arm.cli_version == "2.1.300"   # lives in run.json
    assert r.with_arm.pass_rate == pytest.approx(0.9) and r.without_arm.pass_rate == pytest.approx(0.5)
    assert r.with_arm.tokens == 40 and r.errors == 0     # four token counters of 10, summed


def test_errored_trial_counts_as_zero(tmp_path):
    two_arms(tmp_path, with_trials=[D.ok(1, 1.0), D.errored(2), D.errored(3, "timeout", "timeout")])
    (r,) = load(tmp_path)
    assert r.with_arm.pass_rate == pytest.approx(1 / 3) and r.with_arm.errors == 2 and r.errors == 2
    assert r.with_arm.trials[1].tokens is None           # an errored trial has tokens {}


def test_budget_stop_trial_counts_as_nothing(tmp_path):
    two_arms(tmp_path, with_trials=[D.ok(1, 1.0), D.stopped(2), D.stopped(3)])
    (r,) = load(tmp_path)
    assert len(r.with_arm.trials) == 1 and r.with_arm.pass_rate == 1.0 and r.with_arm.errors == 0


def test_arm_with_every_trial_budget_stopped_is_dropped(tmp_path):
    two_arms(tmp_path, with_trials=[D.stopped(1), D.stopped(2)])
    assert load(tmp_path) == []


def test_judge_score_belongs_to_the_arm(tmp_path):
    two_arms(tmp_path, judge=(7.5, 5.0))
    (r,) = load(tmp_path)
    assert (r.with_arm.judge, r.without_arm.judge) == (7.5, 5.0)
    other = tmp_path / "other"
    two_arms(other)
    assert load(other)[0].with_arm.judge is None          # "judge": null


def test_unpaired_arm_is_dropped(tmp_path):
    D.write_run(tmp_path, RID, {"with_skill": ([D.ok(1, 0.5)], None)})
    D.write_run(tmp_path, D.run_id_for(2), {"bare": ([D.ok(1, 0.5)], None)})
    assert len(R.load_arms(str(tmp_path))) == 2 and load(tmp_path) == []


def test_missing_results_directory_is_empty(tmp_path):
    assert load(tmp_path) == []


def edit(path, fn):
    doc = json.loads(path.read_text())
    fn(doc)
    path.write_text(json.dumps(doc))


@pytest.mark.parametrize("where,change", [
    ("run.json", lambda d: d.update(schema_version=2)),
    ("run.json", lambda d: d.pop("cli_version")),
    ("run.json", lambda d: d.update(run_id=D.run_id_for(9))),          # not its directory
    ("run.json", lambda d: d.update(arms=["with_skill", "x"])),
    ("run.json", lambda d: d.pop("models")),
    ("with_skill/summary.json", lambda d: d.pop("schema_version")),
    ("with_skill/summary.json", lambda d: d.update(schema=1, schema_version=None)),
    ("with_skill/summary.json", lambda d: d.update(skill="other")),   # disagrees with run.json
    ("with_skill/summary.json", lambda d: d.update(arm="without_skill")),
    ("with_skill/summary.json", lambda d: d["trials"][0].update(pass_rate=1.5)),
    ("with_skill/summary.json", lambda d: d["trials"][0].update(status="weird")),
    ("with_skill/summary.json", lambda d: d["trials"][0].pop("errored")),
    ("with_skill/summary.json", lambda d: d["trials"][0].update(tokens=12)),   # an int, not a dict
    ("with_skill/summary.json", lambda d: d.update(judge=7.5)),               # arm-level object, not a number
    ("with_skill/summary.json", lambda d: d.update(judge={"score": 11})),
])
def test_reader_rejects_malformed_files(tmp_path, where, change):
    d = two_arms(tmp_path)
    edit(type(tmp_path)(d) / where, change)
    with pytest.raises(R.ResultsError):
        R.load_arms(str(tmp_path))


def test_missing_arm_summary_or_unparseable_json_is_an_error(tmp_path):
    d = type(tmp_path)(two_arms(tmp_path))
    (d / "with_skill" / "summary.json").write_text("{not json")
    with pytest.raises(R.ResultsError):
        R.load_arms(str(tmp_path))
    os.remove(d / "with_skill" / "summary.json")
    with pytest.raises(R.ResultsError):
        R.load_arms(str(tmp_path))


def test_written_files_match_the_ingest_schemas(tmp_path):
    """The run directories these tests build must pass the validator the real
    ones pass, so the fixtures cannot drift from the producer's shape."""
    import ingest_validate as iv   # scripts/, on sys.path from conftest.py
    import jsonschema
    d = type(tmp_path)(D.write_run(tmp_path, RID, {
        "with_skill": ([D.ok(1, 1.0), D.errored(2), D.stopped(3)], 7.5),
        "without_skill": ([D.ok(1, 0.5, status="max_turns")], None)}))
    jsonschema.validate(json.loads((d / "run.json").read_text()), iv.SCHEMAS["run"])
    for arm in ("with_skill", "without_skill"):
        jsonschema.validate(json.loads((d / arm / "summary.json").read_text()), iv.SCHEMAS["summary"])
