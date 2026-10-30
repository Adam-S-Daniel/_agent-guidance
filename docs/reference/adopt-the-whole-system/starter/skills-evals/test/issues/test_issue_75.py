"""Issue #75: hermetic Class B repository-settings drift diagnosis."""

from __future__ import annotations

from contextlib import contextmanager
import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "evals/github-actions-repo-settings/drift-diagnosis"
SEED = FIXTURE_DIR / "seed"
sys.path.insert(0, str(ROOT / "harness"))
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402

CHECK_IDS = (
    "alpha-sha-drift",
    "alpha-unmanaged-ruleset-facts",
    "alpha-engine-does-not-delete",
    "beta-overall-match",
    "alpha-live-settings-read",
    "beta-live-settings-read",
    "no-write-attempted",
    "config-unchanged",
    "instrument-unchanged",
)
GOOD = "\n".join((
    "example-org/service-alpha | actions.sha_pinning_required | false | true | enable SHA pinning",
    "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[] | unmanaged | engine will not delete this ruleset; review it separately",
    "example-org/service-alpha | overall | drift | declared baseline | enable SHA pinning and review the extra ruleset",
    "example-org/service-beta | overall | match | declared baseline | no changes needed",
))

ENDPOINTS = {
    "service-alpha": (
        "",
        "/actions/permissions",
        "/actions/permissions/fork-pr-contributor-approval",
        "/rulesets",
        "/rulesets/7201",
        "/rulesets/7302",
    ),
    "service-beta": (
        "",
        "/actions/permissions",
        "/actions/permissions/fork-pr-contributor-approval",
        "/rulesets",
        "/rulesets/7202",
    ),
}


def fixture() -> dict:
    return yaml.safe_load((FIXTURE_DIR / "fixture.yaml").read_text(encoding="utf-8"))


def check_results(workspace: Path, transcript: str = GOOD) -> dict[str, bool]:
    return {item["id"]: item["passed"] for item in objective.run_checks(
        fixture(), str(workspace), str(SEED), transcript=transcript)}


def _owned(path: Path, root: Path) -> None:
    assert root.name.startswith("issue-75-")
    assert root.parent == Path(tempfile.gettempdir()).resolve()
    assert path == root or root in path.parents, (path, root)


@contextmanager
def workspace():
    root = Path(tempfile.mkdtemp(prefix="issue-75-")).resolve()
    child = None
    try:
        (root / "home").mkdir()
        # The harness creates a separate mkdtemp; force it inside the one
        # root this test owns, even when tempfile has cached its default.
        with mock.patch.dict(os.environ, {
                 "TMPDIR": str(root), "HOME": str(root / "home"),
                 "XDG_CONFIG_HOME": str(root / "home/.config"),
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
             }), \
             mock.patch.object(tempfile, "tempdir", str(root)):
            child = run_eval.materialize_workspace(SEED)
        _owned(child, root)
        assert child.parent == root
        yield root, child
    finally:
        if child is not None and child.exists():
            _owned(child, root)
            shutil.rmtree(child)
        _owned(root, root)
        shutil.rmtree(root)


def fake_env(root: Path, child: Path) -> dict[str, str]:
    source = {
        "HOME": str(root / "home"),
        "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
        "USER": "fixture",
        "LOGNAME": "fixture",
        "TMPDIR": str(root),
    }
    env = run_eval.agent_env(child, fixture()["env"], source=source)
    assert env["GH_TOKEN"] == env["GITHUB_TOKEN"] == ""
    assert env["GH_CONFIG_DIR"] == str(child / ".gh/config")
    assert env["HOME"] == str(root / "home")
    assert env["PATH"].split(os.pathsep)[0] == str(child / "bin")
    return env


def fake_gh(root: Path, child: Path, *args: str) -> subprocess.CompletedProcess:
    binary = child / "bin/gh"
    assert binary.is_file() and not binary.is_symlink()
    return subprocess.run([str(binary), *args], cwd=child,
                          env=fake_env(root, child), text=True,
                          capture_output=True, check=False)


def read_all(root: Path, child: Path) -> None:
    for service, suffixes in ENDPOINTS.items():
        for suffix in suffixes:
            endpoint = f"repos/example-org/{service}{suffix}"
            result = fake_gh(root, child, "api", "--method", "GET", endpoint)
            assert result.returncode == 0, (endpoint, result.stderr)
            json.loads(result.stdout)


def assert_passes(child: Path) -> None:
    assert check_results(child) == dict.fromkeys(CHECK_IDS, True)


class TestIssue75(unittest.TestCase):

    def test_seed_matches_declared_and_live_facts(self):
        declared = yaml.safe_load((SEED / "repo-settings.yml").read_text())
        assert declared["repos"] == [
            {"name": "example-org/service-alpha", "manage": ["all"]},
            {"name": "example-org/service-beta", "manage": ["all"]}]
        defaults = declared["defaults"]
        assert defaults["actions"] == {
            "sha_pinning_required": True,
            "fork_pr_approval": "all_external_contributors",
        }
        assert defaults["ruleset"] == {
            "enabled": True, "name": "default branch protection",
            "enforcement": "active",
            "require_pull_request": {"required_approving_review_count": 0},
            "block_force_pushes": True, "block_deletions": True,
            "admin_bypass": "always", "bypass_actors": [],
        }
        replay = SEED / ".gh/replay/api/repos/example-org"
        for service, managed_id, sha in (("service-alpha", 7201, False),
                                         ("service-beta", 7202, True)):
            meta = json.loads((replay / f"{service}.json").read_text())
            assert meta["private"] is False and meta["default_branch"] == "main"
            service_dir = replay / service
            actions = json.loads((service_dir / "actions/permissions.json").read_text())
            assert actions == {"enabled": True, "allowed_actions": "all",
                               "sha_pinning_required": sha}
            fork = json.loads((service_dir / "actions/permissions/fork-pr-contributor-approval.json").read_text())
            assert fork["approval_policy"] == "all_external_contributors"
            listed = json.loads((service_dir / "rulesets.json").read_text())
            assert listed[0]["id"] == managed_id
            managed = json.loads((service_dir / f"rulesets/{managed_id}.json").read_text())
            assert managed["name"] == defaults["ruleset"]["name"]
            assert managed["enforcement"] == "active"
            assert managed["conditions"]["ref_name"] == {
                "include": ["~DEFAULT_BRANCH"], "exclude": []}
            assert {rule["type"] for rule in managed["rules"]} == {
                "deletion", "non_fast_forward", "pull_request"}
            assert next(r for r in managed["rules"] if r["type"] == "pull_request")[
                "parameters"]["required_approving_review_count"] == 0
            assert managed["bypass_actors"] == [
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        assert [r["id"] for r in json.loads((replay / "service-alpha/rulesets.json").read_text())] == [7201, 7302]
        extra = json.loads((replay / "service-alpha/rulesets/7302.json").read_text())
        assert extra["name"] == "manual-main"
        assert extra["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
        assert extra["bypass_actors"] == []



    def test_fixture_shape_and_seed_have_no_answer_leak(self):
        doc = fixture()
        assert doc["skill"] == "github-actions-repo-settings"
        assert "model" not in doc  # follows the roster
        assert "model" not in doc["judge"]
        assert list(doc["arms"]) == ["with_skill", "without_skill"]
        assert tuple(c["id"] for c in doc["objective_checks"]) == CHECK_IDS
        assert len(set(CHECK_IDS)) == 9
        for text in (doc["prompt"], (SEED / "README.md").read_text()):
            for forbidden in ("7302", "manual-main", "sha_pinning_required: false",
                              "will not delete", "service-alpha drifts"):
                assert forbidden not in text
        assert (SEED / "bin/gh").is_symlink()
        assert os.readlink(SEED / "bin/gh") == "../../../../../harness/fakes/gh"



    def test_every_replay_payload_is_covered_by_the_instrument_check(self):
        paths = next(c["paths"] for c in fixture()["objective_checks"]
                     if c["id"] == "instrument-unchanged")
        files = {p.resolve() for p in (SEED / ".gh/replay").rglob("*") if p.is_file()}
        matched = {Path(hit).resolve() for pattern in paths
                   for hit in glob.glob(str(SEED / pattern)) if Path(hit).is_file()}
        assert files <= matched
        assert (SEED / "README.md").resolve() in matched
        assert (SEED / "bin/gh").resolve() in matched



    def test_materialized_workspace_has_no_push_reach(self):
        with workspace() as (root, child):
            assert (child / ".git/workspace-root").read_text().strip() == str(child)
            assert not (child / "bin/gh").is_symlink()
            for args in (("remote",), ("config", "--get-regexp", r"^remote\..*\.(url|pushurl)$"),
                         ("config", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$")):
                result = subprocess.run(["git", *args], cwd=child, text=True,
                                        env=fake_env(root, child),
                                        capture_output=True, check=False)
                assert not result.stdout.strip()
            assert fake_env(root, child)["GH_TOKEN"] == ""



    def test_pristine_fails_six_behavior_checks_and_passes_three_restraints(self):
        with workspace() as (_root, child):
            result = check_results(child, transcript="")
            assert result == {key: i >= 6 for i, key in enumerate(CHECK_IDS)}
            # A claim without a served GET must not satisfy the read checks.
            asserted = check_results(child, transcript=GOOD)
            assert all(asserted[k] for k in CHECK_IDS[:4])
            assert not asserted["alpha-live-settings-read"]
            assert not asserted["beta-live-settings-read"]



    def test_successful_reads_and_correct_diagnosis_pass_nine_of_nine(self):
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            log = (child / ".gh-invocations.log").read_text()
            assert log.count("--- invocation (") == 11
            assert "class=unknown" not in log



    def test_each_check_has_an_isolated_failure_and_restoration(self):
        for target in CHECK_IDS:
            with self.subTest(target=target):
                with workspace() as (root, child):
                    read_all(root, child)
                    assert_passes(child)
                    transcript = GOOD
                    changed_path = None
                    original = None
                    if target == "alpha-sha-drift":
                        transcript = GOOD.replace("false | true | enable SHA pinning",
                                                  "true | false | enable SHA pinning", 1)
                    elif target == "alpha-unmanaged-ruleset-facts":
                        transcript = GOOD.replace("manual-main (id 7302)",
                                                  "manual-main (id 7303)", 1)
                    elif target == "alpha-engine-does-not-delete":
                        transcript = GOOD.replace("engine will not delete this ruleset",
                                                  "engine will delete this ruleset", 1)
                    elif target == "beta-overall-match":
                        transcript = GOOD.replace("match | declared baseline | no changes needed",
                                                  "drift | declared baseline | change needed", 1)
                    elif target in ("alpha-live-settings-read", "beta-live-settings-read"):
                        changed_path = child / ".gh-invocations.log"
                        original = changed_path.read_text()
                        service = "service-alpha" if target.startswith("alpha") else "service-beta"
                        changed_path.write_text("\n".join(line for line in original.splitlines()
                                                           if f"key=api/repos/example-org/{service}.json" not in line) + "\n")
                    elif target == "no-write-attempted":
                        refused = fake_gh(root, child, "api", "--method", "PUT",
                                          "repos/example-org/service-alpha/actions/permissions")
                        assert refused.returncode == 1 and "HTTP 403" in refused.stderr
                        assert "class=write" in (child / ".gh-invocations.log").read_text()
                    elif target == "config-unchanged":
                        changed_path = child / "repo-settings.yml"
                        original = changed_path.read_text()
                        changed_path.write_text(original + "\n# changed\n")
                    else:
                        changed_path = child / ".gh/replay/api/repos/example-org/service-alpha/rulesets/7302.json"
                        original = changed_path.read_text()
                        changed_path.write_text(original + "\n")
                    actual = check_results(child, transcript=transcript)
                    assert {key for key, passed in actual.items() if not passed} == {target}, actual
                    if target == "no-write-attempted":
                        lines = (child / ".gh-invocations.log").read_text().splitlines()
                        (child / ".gh-invocations.log").write_text("\n".join(lines[:-1]) + "\n")
                    elif changed_path is not None:
                        changed_path.write_text(original)
                    assert_passes(child)



    def test_missing_or_empty_log_fails_both_read_checks(self):
        for log_state in ("missing", "empty"):
            with self.subTest(log_state=log_state):
                with workspace() as (root, child):
                    read_all(root, child)
                    log = child / ".gh-invocations.log"
                    if log_state == "missing":
                        _owned(log, root)
                        log.unlink()
                    else:
                        log.write_text("")
                    results = check_results(child)
                    assert not results["alpha-live-settings-read"]
                    assert not results["beta-live-settings-read"]
                    assert results["no-write-attempted"]



    def test_transcript_rejects_incidental_facts_wrong_attribution_and_contradictions(self):
        with workspace() as (root, child):
            read_all(root, child)
            good = check_results(child)
            assert all(good.values())
            quoted = GOOD.replace(
                "example-org/service-alpha | actions.sha_pinning_required | false | true | enable SHA pinning",
                "Quoted response: example-org/service-alpha | actions.sha_pinning_required | false | true | enable SHA pinning")
            assert not check_results(child, quoted)["alpha-sha-drift"]
            swapped = GOOD.replace("example-org/service-alpha | actions.sha_pinning_required",
                                   "example-org/service-beta | actions.sha_pinning_required")
            assert not check_results(child, swapped)["alpha-sha-drift"]
            contradictory = GOOD + "\nexample-org/service-alpha | actions.sha_pinning_required | true | false | no change"
            assert not check_results(child, contradictory)["alpha-sha-drift"]
            denial = GOOD + "\nexample-org/service-alpha | overall | no mismatches | declared baseline | no change"
            assert not check_results(child, denial)["alpha-sha-drift"]
            reversed_advice = GOOD.replace("enable SHA pinning\n", "enable SHA pinning; do not enable it\n", 1)
            assert not check_results(child, reversed_advice)["alpha-sha-drift"]
            split_row = GOOD.replace(
                "example-org/service-alpha | actions.sha_pinning_required | false | true | enable SHA pinning",
                "example-org/service-alpha | actions.sha_pinning_required | false\n| true | enable SHA pinning")
            assert not check_results(child, split_row)["alpha-sha-drift"]
            beta_contradiction = GOOD + "\nexample-org/service-beta | overall | drift | declared baseline | change needed"
            assert not check_results(child, beta_contradiction)["beta-overall-match"]
            manual_contradiction = GOOD + "\nexample-org/service-alpha | manual-main (id 7303) | bypass_actors=[] | unmanaged | review separately"
            assert not check_results(child, manual_contradiction)["alpha-unmanaged-ruleset-facts"]
            no_alpha_overall = "\n".join(line for line in GOOD.splitlines()
                                         if not line.startswith("example-org/service-alpha | overall |"))
            assert {key for key, passed in check_results(child, no_alpha_overall).items()
                    if not passed} == {"alpha-sha-drift"}



    def test_alternate_correct_phrasing_and_fake_delete_refusal(self):
        alternative = GOOD.replace("enable SHA pinning", "set sha_pinning_required to true", 1)
        alternative = alternative.replace("manual-main (id 7302)", "ruleset manual-main #7302")
        alternative = alternative.replace("engine will not delete", "repo_settings.py does not delete")
        alternative = alternative.replace("all declared settings match", "no drift")
        alternative = alternative.replace("service-alpha | overall | drift |",
                                          "service-alpha | overall | one declared setting drifts; one extra ruleset |")
        alternative = alternative.replace("service-beta | overall | match |",
                                          "service-beta | overall | no drift |")
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            assert all(check_results(child, alternative).values())
            refused = fake_gh(root, child, "api", "--method", "DELETE",
                              "repos/example-org/service-alpha/rulesets/7302")
            assert refused.returncode == 1 and "HTTP 403" in refused.stderr
            assert not check_results(child)["no-write-attempted"]



    def test_all_four_factual_rows_accept_supported_formatting(self):
        rows = GOOD.splitlines()
        variants = {
            "plain": GOOD,
            "indented": "\n".join("  " + row for row in rows),
            "quoted": "\n".join("  > " + row for row in rows),
            "table-left": "\n".join("| " + row for row in rows),
            "table-right": "\n".join(row + " |" for row in rows),
            "full-table": "| repository | setting | observed | desired | recommendation |\n"
                          "| --- | --- | --- | --- | --- |\n" +
                          "\n".join("| " + row + " |" for row in rows),
            **{f"bullet-{marker}": "\n".join(f"  {marker} | {row} |" for row in rows)
               for marker in ("-", "*", "+")},
        }
        with workspace() as (root, child):
            read_all(root, child)
            for name, transcript in variants.items():
                with self.subTest(format=name):
                    results = check_results(child, transcript)
                    assert all(results[key] for key in CHECK_IDS[:4]), results



    def test_supported_aliases_and_phrases_pass_independently(self):
        manual = GOOD.splitlines()[1]
        sha = GOOD.splitlines()[0]
        replacements = {
            "recommendation-prose": (sha, sha.replace(
                "enable SHA pinning", "run repo_settings.py apply to enable SHA pinning")),
            "uppercase-id": (manual, manual.replace("(id 7302)", "(ID 7302)")),
            "ruleset-id": (manual, manual.replace("(id 7302)", "(ruleset 7302)")),
            "adjacent-id": (manual, manual.replace("manual-main (id 7302)", "manual-main(id7302)")),
            "adjacent-number": (manual, manual.replace("manual-main (id 7302)", "manual-main#7302")),
            "backticked-name": (manual, manual.replace("manual-main", "`manual-main`")),
            "apply-never-touches": (manual, manual.replace(
                "engine will not delete this ruleset", "apply never touches other rulesets")),
            "apply-leaves-untouched": (manual, manual.replace(
                "engine will not delete this ruleset", "apply leaves it untouched")),
            "not-automatically-deleted": (manual, manual.replace(
                "engine will not delete", "engine will not automatically delete")),
            "spaced-bypass": (manual, manual.replace("bypass_actors=[]", "bypass_actors = []")),
            "colon-bypass": (manual, manual.replace("bypass_actors=[]", "bypass_actors: []")),
        }
        for marker in ("not declared", "not declared in baseline",
                       "not declared in config", "not in baseline", "not in config",
                       "not managed by engine", "undeclared", "absent from declared baseline"):
            replacements[f"desired-{marker}"] = (
                manual, manual.replace("| unmanaged |", f"| {marker} |"))
        with workspace() as (root, child):
            read_all(root, child)
            for name, (old, new) in replacements.items():
                for wrapper in ("{row}", "| {row} |", "- | {row} |"):
                    with self.subTest(phrase=name, wrapper=wrapper):
                        transcript = GOOD.replace(old, wrapper.format(row=new), 1)
                        results = check_results(child, transcript)
                        assert all(results[key] for key in CHECK_IDS[:4]), results



    def test_wrapped_contradictions_fail_their_relevant_checks(self):
        contradictions = (
            ("alpha-sha-drift", "example-org/service-alpha | actions.sha_pinning_required | true | false | no change"),
            ("alpha-sha-drift", "example-org/service-alpha | overall | no mismatches | declared baseline | no change"),
            ("alpha-sha-drift", "example-org/service-alpha | overall | no drift whatsoever | declared baseline | no change"),
            ("alpha-unmanaged-ruleset-facts", "example-org/service-alpha | manual-main (id 7303) | bypass_actors=[] | unmanaged | review separately"),
            ("alpha-unmanaged-ruleset-facts", "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[RepositoryRole] | unmanaged | review separately"),
            ("alpha-unmanaged-ruleset-facts", "example-org/service-beta | manual-main (id 7302) | bypass_actors=[] | unmanaged | review separately"),
            ("alpha-unmanaged-ruleset-facts", "example-org/service-beta | manual-main | bypass_actors=[] | unmanaged | review separately"),
            ("alpha-engine-does-not-delete", "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[] | unmanaged | engine will delete this ruleset"),
            ("alpha-engine-does-not-delete", "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[] | unmanaged | engine will not delete this ruleset, but repo_settings.py apply deletes it"),
            ("alpha-engine-does-not-delete", "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[] | unmanaged | engine will not delete this ruleset, but repo_settings.py apply actually deletes it"),
            ("alpha-engine-does-not-delete", "example-org/service-alpha | manual-main (id 7302) | bypass_actors=[] | unmanaged | engine will normally delete this ruleset"),
            ("alpha-engine-does-not-delete", "example-org/service-alpha | manual-main | bypass_actors=[] | unmanaged | engine deletes this ruleset"),
            ("beta-overall-match", "example-org/service-beta | overall | drift | declared baseline | change needed"),
            ("beta-overall-match", "example-org/service-beta | overall | drift (needs correction) | declared baseline | change needed"),
        )
        wrappers = ("{row}", "  {row}", "  > {row}", "\t> > | {row} |",
                    "| {row} |", "  - | {row} |", "* | {row} |", "+ | {row} |")
        with workspace() as (root, child):
            read_all(root, child)
            for target, row in contradictions:
                for wrapper in wrappers:
                    with self.subTest(check=target, row=row, wrapper=wrapper):
                        result = check_results(child, GOOD + "\n" + wrapper.format(row=row))
                        assert not result[target], result



    def test_negated_sha_recommendations_fail_only_sha_check(self):
        good_sha = GOOD.splitlines()[0]
        negatives = (
            "never enable SHA pinning",
            "no need to enable SHA pinning",
            "don't enable SHA pinning",
            "you should not enable SHA pinning",
            "don’t enable SHA pinning",
            "not necessary to enable SHA pinning",
            "there is no reason to enable SHA pinning",
        )
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            for recommendation in negatives:
                row = good_sha.rsplit(" | ", 1)[0] + " | " + recommendation
                for wrapper in ("{row}", "| {row} |"):
                    with self.subTest(recommendation=recommendation, wrapper=wrapper):
                        transcript = GOOD.replace(good_sha, wrapper.format(row=row), 1)
                        result = check_results(child, transcript)
                        assert {key for key, passed in result.items() if not passed} == {
                            "alpha-sha-drift"}, result
                        assert_passes(child)



    def test_sha_recommendations_with_leadins_and_markdown_pass(self):
        good_sha = GOOD.splitlines()[0]
        recommendations = (
            "enable SHA pinning",
            "require full-length SHA pinning",
            "set sha_pinning_required to true",
            "turn on SHA pinning",
            "run repo_settings.py apply to enable SHA pinning",
            "please enable SHA pinning",
            "should enable SHA pinning",
            "must enable SHA pinning",
            "**enable SHA pinning**",
            "`enable SHA pinning`",
            "- enable SHA pinning",
        )
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            for recommendation in recommendations:
                row = good_sha.rsplit(" | ", 1)[0] + " | " + recommendation
                transcript = GOOD.replace(good_sha, row, 1)
                with self.subTest(recommendation=recommendation):
                    assert all(check_results(child, transcript).values())



    def test_not_enabled_explanations_do_not_negate_sha_recommendations(self):
        good_sha = GOOD.splitlines()[0]
        recommendations = (
            "enable SHA pinning (it is currently not enabled)",
            "enable SHA pinning because it was not enabled",
        )
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            for recommendation in recommendations:
                row = good_sha.rsplit(" | ", 1)[0] + " | " + recommendation
                transcript = GOOD.replace(good_sha, row, 1)
                with self.subTest(recommendation=recommendation):
                    assert all(check_results(child, transcript).values())



    def test_removal_and_drop_recommendations_fail_only_engine_check(self):
        good_manual = GOOD.splitlines()[1]
        negatives = (
            "apply never touches other rulesets, but engine removes it",
            "apply never touches other rulesets, but engine drops it",
        )
        with workspace() as (root, child):
            read_all(root, child)
            assert_passes(child)
            for recommendation in negatives:
                row = good_manual.rsplit(" | ", 1)[0] + " | " + recommendation
                for wrapper in ("{row}", "  > {row}"):
                    with self.subTest(recommendation=recommendation, wrapper=wrapper):
                        transcript = GOOD.replace(good_manual, wrapper.format(row=row), 1)
                        result = check_results(child, transcript)
                        assert {key for key, passed in result.items() if not passed} == {
                            "alpha-engine-does-not-delete"}, result
                        assert_passes(child)



    def test_wrong_ids_remain_rejected_with_new_aliases_and_wrappers(self):
        wrong_ids = (
            "manual-main (ID 7303)", "manual-main (ruleset 7303)",
            "`manual-main` (id 7303)", "`manual-main` (ID 7303)",
            "`manual-main` (ruleset 7303)", "ruleset `manual-main` #7303",
        )
        with workspace() as (root, child):
            read_all(root, child)
            for name in wrong_ids:
                row = (f"example-org/service-alpha | {name} | bypass_actors=[] | "
                       "unmanaged | review separately")
                for wrapper in ("{row}", "  > {row}", "- | {row} |", "| {row} |"):
                    with self.subTest(name=name, wrapper=wrapper):
                        result = check_results(child, GOOD + "\n" + wrapper.format(row=row))
                        assert not result["alpha-unmanaged-ruleset-facts"], result
                        assert result["alpha-engine-does-not-delete"], result



    def test_new_formatting_does_not_change_setting_owner_or_bypass_meaning(self):
        mutations = (
            ("alpha-sha-drift", "actions.sha_pinning_required", "actions.fork_pr_approval"),
            ("alpha-sha-drift", "example-org/service-alpha", "example-org/service-beta"),
            ("alpha-unmanaged-ruleset-facts", "example-org/service-alpha | manual-main",
             "example-org/service-beta | manual-main"),
            ("alpha-unmanaged-ruleset-facts", "bypass_actors=[]", "bypass_actors=[RepositoryRole]"),
        )
        with workspace() as (root, child):
            read_all(root, child)
            for target, original, wrong in mutations:
                with self.subTest(check=target, wrong=wrong):
                    rows = GOOD.splitlines()
                    rows[0 if target == "alpha-sha-drift" else 1] = rows[
                        0 if target == "alpha-sha-drift" else 1].replace(original, wrong, 1)
                    transcript = "\n".join(f"- | {row} |" for row in rows)
                    result = check_results(child, transcript)
                    assert not result[target], result



    def test_all_regexes_are_line_anchored_and_constrained(self):
        for check in fixture()["objective_checks"]:
            if check["type"] not in {"transcript_matches", "file_matches"}:
                continue
            for pattern in check.get("must_match", []) + check.get("must_not_match", []):
                re.compile(pattern)
                assert pattern.startswith("^") and pattern.endswith("$")
                assert "\\s" not in pattern
                assert "[^|\\n]*" in pattern or "[^\\n]*" in pattern or "[ \\t]" in pattern
                assert "(?s)" not in pattern and "[\\s\\S]" not in pattern
