import json
import os

import pytest

from conftest import RUN_ID
import ingest_validate as iv

METER = {"seven_day": 0.1, "resets_at": "2026-11-02T17:00:00Z"}


def trial(k=1):
    return {"trial": k, "status": "ok", "errored": False, "error_kind": None, "num_turns": 3,
            "cost_usd": 0.01, "duration_s": 1.0, "tokens": {"input_tokens": 10}, "pass_rate": 1.0,
            "checks": [{"id": "c", "passed": True, "error": False, "detail": "exit 0"}]}


def summary(arm="with_skill", run_id=RUN_ID):
    return {"schema_version": 1, "run_id": run_id, "fixture": "evals/s/f", "skill": "s", "arm": arm,
            "model": "claude-sonnet-5-5", "trials_requested": 1, "trials_run": 1, "trials_skipped": 0,
            "errored_trials": 0, "check_errors": 0, "pass_rate": 1.0, "pass_rate_trials": [1.0],
            "tokens_total": 10, "cost_usd_total": 0.01, "turns_total": 3, "trials": [trial()],
            "judge": None, "budget_stop": None}


def run_doc(run_id=RUN_ID):
    return {"schema_version": 1, "run_id": run_id, "mode": "eval", "fixture": "evals/s/f", "skill": "s",
            "fixture_digest": "a" * 64, "cli_version": "2.1.999 (Claude Code)",
            "models": {"arms": "claude-sonnet-5-5", "judge": None, "reported": ["claude-sonnet-5-5"]},
            "arms": ["with_skill"], "trials": 1, "started_at": "2026-10-28T12:00:00Z",
            "finished_at": "2026-10-28T12:05:00Z", "meter_before": METER, "meter_after": METER,
            "meter_delta": 0.0, "allowance_cost": 0.005, "budget_stop": None, "judged": False,
            "guards": {"config_dir_clean": True, "sandbox_available": True, "fixture_unchanged": True}}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "branch"
    base = root / "eval-results" / RUN_ID
    (base / "with_skill").mkdir(parents=True)
    (base / "run.json").write_text(json.dumps(run_doc()))
    (base / "report.md").write_text("# report\n")
    (base / "with_skill" / "summary.json").write_text(json.dumps(summary()))
    (base / "with_skill" / "trace.json").write_text(json.dumps(
        {"arm": "with_skill", "trials": [{"trial": 1, "tools": ["Edit"], "tool_errors": 0}]}))
    return root, base


def test_a_valid_tree_passes(tree):
    root, _ = tree
    files = iv.check_tree(root, RUN_ID)
    assert {f.as_posix() for f in files} == {"run.json", "report.md", "with_skill/summary.json",
                                             "with_skill/trace.json"}


@pytest.mark.parametrize("name", [
    f"claude/eval-results-{RUN_ID}",
])
def test_good_branch_name(name):
    assert iv.check_branch(name) == RUN_ID


@pytest.mark.parametrize("name", [
    "main", "claude/eval-" + RUN_ID, "claude/eval-improve-" + RUN_ID,        # improve branches never ingest here
    f"claude/eval-results-{RUN_ID}/x", f"claude/eval-results-{RUN_ID}\n", f"x/claude/eval-results-{RUN_ID}",
    "claude/eval-results-20261028T120000Z-1A2B3C", "claude/eval-results-../../etc",
    "claude/eval-results-20261028T120000Z-1a2b3c; rm -rf /", "", None,
])
def test_bad_branch_names_are_refused(name):
    with pytest.raises(iv.Refused):
        iv.check_branch(name)


def test_changed_paths_must_stay_in_the_allowlist():
    ok = [f"eval-results/{RUN_ID}/run.json", f"eval-results/{RUN_ID}/bare/summary.json"]
    iv.check_changed(ok, RUN_ID)
    for bad in ([f"eval-results/{RUN_ID}/transcript.jsonl"], [".github/workflows/x.yml"],
                [f"eval-results/20261028T120000Z-ffffff/run.json"], [f"eval-results/{RUN_ID}/../run.json"],
                [f"eval-results/{RUN_ID}/with_skill/raw.json"], [f"eval-results/{RUN_ID}/other/summary.json"],
                ok + ["README.md"], []):
        with pytest.raises(iv.Refused):
            iv.check_changed(bad, RUN_ID)


def test_extra_and_missing_files_are_refused(tree):
    root, base = tree
    (base / "transcript.jsonl").write_text("{}")
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)
    (base / "transcript.jsonl").unlink()
    (base / "report.md").unlink()
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


def test_symlinks_and_other_run_dirs_are_refused(tree):
    root, base = tree
    (base / "notes.md").symlink_to("/etc/passwd")
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)
    (base / "notes.md").unlink()
    (root / "eval-results" / "20261028T120000Z-ffffff").mkdir()
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


def test_size_limits(tree):
    root, base = tree
    (base / "report.md").write_text("x" * (iv.MAX_FILE + 1))
    with pytest.raises(iv.Refused, match="size"):
        iv.check_tree(root, RUN_ID)


@pytest.mark.parametrize("edit", [
    lambda d: d.update(transcript="secret text"),                    # unknown key
    lambda d: d.pop("fixture_digest"),
    lambda d: d.update(allowance_cost="0.1"),
    lambda d: d.update(mode="improve"),
    lambda d: d.update(guards={"config_dir_clean": False, "sandbox_available": True, "fixture_unchanged": True}),
    lambda d: d.update(run_id="20261028T120000Z-ffffff"),            # differs from the branch
    lambda d: d.update(arms=["with_skill", "bare"]),                 # arm without a summary
    lambda d: d.update(trials=9),
    lambda d: d["meter_before"].update(seven_day=7),
])
def test_run_json_shape_is_enforced(tree, edit):
    root, base = tree
    doc = json.loads((base / "run.json").read_text())
    edit(doc)
    (base / "run.json").write_text(json.dumps(doc))
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


@pytest.mark.parametrize("edit", [
    lambda d: d.update(extra=1),
    lambda d: d["trials"][0].update(raw_transcript="x"),
    lambda d: d["trials"][0]["checks"][0].update(detail="x" * 301),
    lambda d: d.update(pass_rate=1.5),
    lambda d: d.update(arm="bare"),                                  # not the directory it sits in
    lambda d: d.update(run_id="20261028T120000Z-ffffff"),
])
def test_summary_shape_is_enforced(tree, edit):
    root, base = tree
    p = base / "with_skill" / "summary.json"
    doc = json.loads(p.read_text())
    edit(doc)
    p.write_text(json.dumps(doc))
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


def test_trace_tool_names_are_restricted(tree):
    root, base = tree
    p = base / "with_skill" / "trace.json"
    p.write_text(json.dumps({"arm": "with_skill", "trials": [{"trial": 1, "tools": ["rm -rf /"], "tool_errors": 0}]}))
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


def test_invalid_json_is_refused(tree):
    root, base = tree
    (base / "run.json").write_text("{nope")
    with pytest.raises(iv.Refused):
        iv.check_tree(root, RUN_ID)


def test_copy_writes_routine_results_once(tree, tmp_path):
    root, _ = tree
    files = iv.check_tree(root, RUN_ID)
    dest = tmp_path / "persistent"
    target = iv.copy_to(root, RUN_ID, dest, files)
    assert target == dest / "routine-results" / RUN_ID
    assert (target / "with_skill" / "summary.json").is_file()
    with pytest.raises(iv.Refused):
        iv.copy_to(root, RUN_ID, dest, files)


def test_main_exit_codes_and_no_file_text_in_refusals(tree, tmp_path, capsys):
    root, base = tree
    changed = tmp_path / "changed.z"
    paths = [f"eval-results/{RUN_ID}/{f.as_posix()}" for f in iv.check_tree(root, RUN_ID)]
    changed.write_bytes("\0".join(paths).encode() + b"\0")
    dest = tmp_path / "dest"
    args = ["--branch", f"claude/eval-results-{RUN_ID}", "--root", str(root), "--changed", str(changed)]
    assert iv.main(args + ["--dest", str(dest)]) == 0
    assert (dest / "routine-results" / RUN_ID / "run.json").exists()
    (base / "report.md").write_text("PRIVATE-TEXT")
    (base / "extra.txt").write_text("PRIVATE-TEXT")
    assert iv.main(args) == 1
    assert "PRIVATE-TEXT" not in capsys.readouterr().err
    assert iv.main(["--branch", "main", "--root", str(root), "--changed", str(changed)]) == 1
    assert iv.main(args[:-1] + [str(tmp_path / "missing")]) == 1
