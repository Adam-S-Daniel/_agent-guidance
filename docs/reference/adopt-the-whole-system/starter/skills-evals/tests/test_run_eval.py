import json
import re

import pytest
import yaml

from conftest import RUN_ID
from harness import run_eval
from harness.run_eval import ENV_ALLOW_EXACT, ENV_ALLOW_PREFIX, safe_killpg
import ingest_validate


def test_both_arms_run_and_the_skill_arm_wins(rig):
    assert rig.run() == 0
    w, wo = rig.summary("with_skill"), rig.summary("without_skill")
    assert (w["pass_rate"], wo["pass_rate"]) == (1.0, 0.75)  # solution passes 4/4; the untouched seed passes 3/4
    assert w["trials_run"] == 2 and w["pass_rate_trials"] == [1.0, 1.0]
    assert wo["errored_trials"] == 0 and wo["check_errors"] == 0
    assert w["tokens_total"] == 2 * 126 and w["turns_total"] == 6
    assert {t["status"] for t in w["trials"]} == {"ok"}
    assert (rig.out / "report.md").is_file()
    assert not list(rig.out.rglob("*.jsonl"))              # no raw transcripts


def test_run_json_carries_models_cli_digest_and_meter(rig):
    from harness.fixture import fixture_digest
    rig.set_fake(step=0.005)
    assert rig.run() == 0
    run = rig.run_doc()
    assert run["cli_version"].startswith("2.1.999")
    assert run["fixture_digest"] == fixture_digest(rig.fixture)
    assert run["models"]["reported"] == ["claude-sonnet-5-5"]
    assert run["models"]["arms"] == "claude-sonnet-5-5"
    assert run["meter_before"]["seven_day"] == pytest.approx(0.105)       # after the probe call
    assert run["meter_after"]["seven_day"] > run["meter_before"]["seven_day"]
    assert run["meter_delta"] == pytest.approx(0.02, abs=1e-6)           # 4 trials, 0.005 each
    assert run["allowance_cost"] == pytest.approx(run["meter_delta"] + 0.005)
    assert run["guards"] == {"config_dir_clean": True, "sandbox_available": True, "fixture_unchanged": True}
    report = (rig.out / "report.md").read_text()
    for needle in (run["fixture_digest"][:16], "claude-sonnet-5-5", "2.1.999", "Meter before", "Meter after"):
        assert needle in report


def test_the_output_passes_the_ingest_validator(rig):
    rig.set_fake(mode="no_edit")                            # arms tie, so the judge runs too
    assert rig.run(arms="both") == 0
    assert (rig.out / "judge.json").exists()
    staged = rig.tmp / "staged" / "eval-results"
    staged.mkdir(parents=True)
    import shutil
    shutil.copytree(rig.out, staged / RUN_ID)
    files = ingest_validate.check_tree(rig.tmp / "staged", RUN_ID)
    assert Path_names(files) >= {"run.json", "report.md", "judge.json", "with_skill/summary.json"}


def Path_names(files):
    return {f.as_posix() for f in files}


def test_trial_environment_holds_only_allowlisted_variables(rig):
    assert rig.run() == 0
    calls = rig.calls("trial")
    assert len(calls) == 4
    allowed = ENV_ALLOW_EXACT | {"HOME", "CLAUDE_CONFIG_DIR", "TMPDIR", "LC_CTYPE"}
    for c in calls:
        names = set(c["env"])
        assert names <= allowed | {k for k in names if k.startswith(ENV_ALLOW_PREFIX)}, sorted(names)
        for leaked in ("GITHUB_TOKEN", "APP_PRIVATE_KEY", "SECRET_THING"):
            assert leaked not in names
        assert "ANTHROPIC_API_KEY" in names                  # the model login is kept
        assert c["home_is_config"] is True                   # HOME points at the temp config


def test_each_arm_gets_its_own_config_built_from_nothing(rig):
    assert rig.run() == 0
    calls = rig.calls("trial")
    with_skill = [c for c in calls if c["has_skill"]]
    without = [c for c in calls if not c["has_skill"]]
    assert len(with_skill) == 2 and len(without) == 2
    assert len({c["config"] for c in calls}) == 4           # fresh dir per trial
    for c in calls:
        sb = c["settings"]["sandbox"]
        assert sb["enabled"] is True and sb["failIfUnavailable"] is True
        assert sb["allowUnsandboxedCommands"] is False and sb["network"]["allowedDomains"] == []
        assert "/real/home" not in c["config"]
    # the skill's text arrived intact, and the run left the source skill alone
    assert (rig.skill / "SKILL.md").read_text().startswith("---\nname: workflow-path-audit")


def test_cli_flags_and_workspace_state(rig):
    assert rig.run() == 0
    for c in rig.calls("trial"):
        a = c["args"]
        assert a[a.index("--output-format") + 1] == "json" and "--verbose" in a
        assert a[a.index("--disallowedTools") + 1] == "WebFetch,WebSearch"
        assert a[a.index("--max-turns") + 1] == "30"
        assert a[a.index("--model") + 1] == "claude-sonnet-5-5"
        assert c["git_commits"] == 1                         # the seed commit only
        assert c["checks_visible"] is False                  # checks are copied in only for scoring


def test_bare_and_single_arm_selection(rig):
    assert rig.run(arms="bare", trials="1") == 0
    assert (rig.out / "bare" / "summary.json").exists() and not (rig.out / "with_skill").exists()
    assert not any(c["has_skill"] for c in rig.calls("trial"))


# --- the judge ----------------------------------------------------------------

def test_judge_is_blind_and_only_runs_when_objective_scores_do_not_separate(rig):
    assert rig.run() == 0                                    # 1.0 vs 0.5 in every trial: separated
    assert rig.calls("judge") == [] and not (rig.out / "judge.json").exists()


def test_judge_runs_blind_on_a_tie_and_maps_labels_back(rig):
    rig.set_fake(mode="no_edit")
    assert rig.run() == 0
    j = rig.calls("judge")
    assert len(j) == 1 and j[0]["prompt_has_arm_names"] is False
    doc = json.loads((rig.out / "judge.json").read_text())
    assert doc["error"] is False and set(doc["mapping"]) == {"with_skill", "without_skill"}
    assert set(doc["mapping"].values()) == {"A", "B"}
    assert rig.summary("with_skill")["judge"]["score"] is not None
    assert "--tools" in j[0]["args"]                          # the judge gets no tools
    assert rig.run_doc()["judged"] is True


def test_judge_shuffle_is_deterministic_per_run_id(rig, tmp_path):
    rig.set_fake(mode="no_edit")
    rig.run()
    first = json.loads((rig.out / "judge.json").read_text())["mapping"]
    from conftest import Rig
    (tmp_path / "again").mkdir()
    rig2 = Rig(tmp_path / "again")
    rig2.set_fake(mode="no_edit")
    rig2.run()
    assert json.loads((rig2.out / "judge.json").read_text())["mapping"] == first


def test_no_judge_flag_skips_it(rig):
    rig.set_fake(mode="no_edit")
    assert rig.run("--no-judge") == 0
    assert rig.calls("judge") == [] and rig.run_doc()["judged"] is False


# --- budget stops -------------------------------------------------------------

def test_per_run_cap_stops_new_trials_and_records_budget_stop(rig):
    rig.set_fake(step=0.02)         # probe 0.12 (before); trials end 0.14, 0.16, ...
    assert rig.run(trials="3") == 0
    w, wo = rig.summary("with_skill"), rig.summary("without_skill")
    assert [t["status"] for t in w["trials"]] == ["ok", "budget_stop", "budget_stop"]
    assert [t["status"] for t in wo["trials"]] == ["ok", "budget_stop", "budget_stop"]
    assert w["trials_run"] == 1 and w["trials_skipped"] == 2
    assert wo["budget_stop"] == {"reason": "per_run_cap", "before_trial": 2}
    run = rig.run_doc()
    assert run["budget_stop"]["reason"] == "per_run_cap"
    assert len(rig.calls("trial")) == 2


def test_a_stop_in_the_middle_of_a_round_skips_the_rest(rig):
    rig.set_fake(step=0.016)        # before 0.116; with_skill ends 0.132 (+0.016 < cap), without 0.148 (+0.032 >= cap)
    assert rig.run(trials="2") == 0
    assert len(rig.calls("trial")) == 2
    assert rig.summary("with_skill")["trials"][1]["status"] == "budget_stop"


def test_account_meter_above_the_limit_starts_nothing(rig):
    rig.set_fake(meter=0.80, step=0.0)
    assert rig.run() == 0
    assert rig.calls("trial") == []
    assert rig.summary("with_skill")["trials_run"] == 0
    assert rig.run_doc()["budget_stop"]["reason"] == "account_above"


def test_judge_call_also_respects_the_budget(rig):
    rig.set_fake(mode="no_edit", step=0.0155)   # two trials move the meter 0.031, past per_run_cap 0.03
    assert rig.run(trials="1") == 0
    assert rig.calls("judge") == [] and not (rig.out / "judge.json").exists()
    assert rig.run_doc()["budget_stop"] == {"reason": "per_run_cap", "before_trial": 2}


def test_an_unreadable_meter_refuses_the_run(rig, capsys):
    rig.set_fake(mode="no_meter")
    assert rig.run() == 3
    assert "meter_unreadable" in capsys.readouterr().err


def test_no_meter_flag_runs_without_budget_stops(rig):
    rig.set_fake(mode="no_meter")
    assert rig.run("--no-meter") == 0
    run = rig.run_doc()
    assert run["meter_before"] is None and run["allowance_cost"] is None and run["budget_stop"] is None
    assert rig.calls("probe") == []


# --- guards -------------------------------------------------------------------

def test_missing_sandbox_refuses_the_run(rig, capsys):
    assert rig.run(sandbox=False) == 3
    assert "sandbox_unavailable" in capsys.readouterr().err
    assert rig.calls("trial") == [] and not rig.out.exists()


def test_cli_reporting_an_unavailable_sandbox_refuses_the_run(rig, capsys):
    rig.set_fake(mode="sandbox_fail")
    assert rig.run() == 3
    assert "sandbox_unavailable" in capsys.readouterr().err
    assert not rig.out.exists()


def test_an_arm_that_wrote_to_its_config_dir_refuses_the_run(rig, capsys):
    rig.set_fake(mode="write_config")
    assert rig.run() == 3
    assert "config_dir_written" in capsys.readouterr().err
    assert not rig.out.exists()


def test_the_clis_own_runtime_state_is_not_a_config_write(rig):
    rig.set_fake(mode="write_runtime_state")
    assert rig.run() == 0


def test_a_fixture_that_changed_mid_run_refuses_the_run(rig, capsys):
    rig.set_fake(mode="touch_fixture", touch=str(rig.fixture / "seed" / "README.md"))
    assert rig.run() == 3
    assert "fixture_changed" in capsys.readouterr().err
    assert not rig.out.exists()


def test_refuses_a_reused_results_directory_and_bad_inputs(rig, capsys):
    rig.out.mkdir()
    (rig.out / "x").write_text("x")
    assert rig.run() == 2
    import shutil
    shutil.rmtree(rig.out)
    assert rig.run("--model", "claude-not-in-roster") == 2
    (rig.fixture / "fixture.yaml").write_text("skill: x\nsurprise: 1\n")
    assert rig.run() == 2


# --- error handling -------------------------------------------------------------

def test_a_check_that_errors_scores_as_failure_and_sets_the_error_flag(rig):
    fx = rig.fixture / "fixture.yaml"
    doc = yaml.safe_load(fx.read_text())
    doc["objective_checks"].append({"id": "broken", "type": "command_succeeds",
                                    "command": "no-such-binary-for-test"})
    fx.write_text(yaml.safe_dump(doc))
    assert rig.run() == 0
    w = rig.summary("with_skill")
    assert w["check_errors"] == 2                            # once per trial
    broken = [c for t in w["trials"] for c in t["checks"] if c["id"] == "broken"]
    assert all(c["passed"] is False and c["error"] is True for c in broken)
    assert w["pass_rate"] == pytest.approx(4 / 5)            # the error counts against the arm


@pytest.mark.parametrize("mode,kind", [("no_result", "no_result"), ("error_result", "agent_error")])
def test_errored_trials_count_as_zero_and_are_flagged(rig, mode, kind):
    rig.set_fake(mode=mode)
    assert rig.run(trials="1") == 0
    w = rig.summary("with_skill")
    assert w["errored_trials"] == 1 and w["pass_rate"] == 0.0
    assert w["trials"][0]["error_kind"] == kind


def test_trace_keeps_tool_names_only(rig):
    assert rig.run(trials="1") == 0
    trace = (rig.out / "with_skill" / "trace.json").read_text()
    assert "Edit" in trace and "secret-path" not in trace


def test_safe_killpg_refuses_dangerous_groups():
    for bad in (0, 1, -1, True, "5", None, 1.0):
        with pytest.raises(ValueError):
            safe_killpg(bad)
