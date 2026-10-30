#!/usr/bin/env python3
"""ADR 0008: reviewed schedules, isolated legs, and per-fixture badges.

Parse workflow structure with PyYAML and execute selection scripts offline.
The caller must use the PID namespace and sentinel required by AGENTS.md.
"""
import ast
import importlib.util
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lockfile_pins import lock_problem  # noqa: E402
import example_evals  # noqa: E402

# The kit ships no fixtures and an empty schedule (its DECISIONS X9): these
# tests read the example checkout instead, every tracked file of this
# repository plus the invented fixtures and the schedule naming them
# (test/example_evals.py).
ROOT = example_evals.checkout()
WORKFLOW = ROOT / ".github/workflows/eval.yml"
REVIEWED = list(example_evals.SCHEDULED)
#: Set by scripts/scaffold_real_work.py PROMOTED_NOTE; repeated here so this
#: module imports no harness code.
PROMOTED_NOTE = "Promoted automatically"


def bare_trials():
    """scripts/plan_week.py's literal `BARE_TRIALS`, read from its AST so
    this module imports no harness code."""
    tree = ast.parse((ROOT / "scripts" / "plan_week.py").read_text(encoding="utf-8"))
    (value,) = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and [getattr(t, "id", None) for t in node.targets] == ["BARE_TRIALS"]]
    return value


def promoted_entries():
    """Entries `scaffold_real_work.py promote` appended after the reviewed
    ones: each carries its readiness note and names a `draft: false`
    fixture. Anything else after the reviewed list is not one."""
    rows = yaml.safe_load((ROOT / "evals/scheduled.yml").read_text(encoding="utf-8"))["fixtures"]
    paths = []
    for row in rows[len(REVIEWED):]:
        fixture = yaml.safe_load((ROOT / row["path"] / "fixture.yaml").read_text(encoding="utf-8"))
        if not row["readiness"].startswith(PROMOTED_NOTE) or fixture.get("draft") is not False:
            raise AssertionError(f"{row['path']}: scheduled after the reviewed fixtures, "
                                 "but not promoted by scaffold_real_work.py promote")
        paths.append(row["path"])
    return paths


#: The schedule: the reviewed fixtures, then any promoted ones (the owner,
#: 2026-10-26: fixtures are promoted with no human in the loop).
EXPECTED = REVIEWED + promoted_entries()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


planner = module("scheduled_plan", ROOT / "scripts/plan_scheduled_evals.py")
sys.path.insert(0, str(ROOT / "scripts"))
import allowance_guard  # noqa: E402
badge = module("scheduled_badge", ROOT / "scripts/make_badge.py")


def doc():
    return yaml.safe_load(WORKFLOW.read_text())


def step(job, name):
    return next(s for s in doc()["jobs"][job]["steps"] if s["name"] == name)


class TestScheduledFixtures(unittest.TestCase):
    def test_routine_retirement_amendment_scopes_api_workflow_guarantees(self):
        adr = (
            ROOT / "docs/decisions/0008-run-ready-fixtures-on-the-weekly-schedule.md"
        ).read_text()
        design = (ROOT / "DESIGN.md").read_text()
        routine_adr = (
            ROOT / "docs/decisions/0010-run-ai-eval-steps-in-a-routine-fired-by-actions.md"
        ).read_text()

        self.assertIn(
            "These guarantees describe `eval.yml`'s manual API path, not the\nweekly routine.",
            adr,
        )
        self.assertIn(
            "## Amendment (2026-10-06): weekly routine replaces the Tuesday schedule",
            adr,
        )
        self.assertIn("Routine runs use subscription usage", adr)
        self.assertIn("results are not badge\ninput", adr)
        self.assertIn(
            "manual API workflow's OIDC exchanges, roster jobs, badge publishing, or API\nworkspace spending ceiling",
            design,
        )
        self.assertIn("retired the paid [`eval.yml`]", routine_adr)
        self.assertIn("Routine evals use\nsubscription usage", routine_adr)
        self.assertIn("routine results are not badge\ninput", routine_adr)

    def test_schedule_plan_matches_exact_reviewed_list(self):
        committed = planner.committed_fixtures(ROOT)
        paths = planner.scheduled_fixtures(ROOT / "evals/scheduled.yml", committed)
        self.assertEqual(paths, EXPECTED)
        actual = planner.plan(ROOT, "schedule", {"inputs": {"fixture": "evals/nope"}})
        self.assertEqual(actual, {"include": [
            {"fixture": p, "eval_key": p[len("evals/"):], "slot": i}
            for i, p in enumerate(EXPECTED)]})
        plan_step = step("plan", "Plan reviewed fixtures")
        self.assertEqual(plan_step["run"], "python3 scripts/plan_scheduled_evals.py")
        self.assertEqual(doc()["jobs"]["plan"]["outputs"],
                         {"matrix": "${{ steps.plan.outputs.matrix }}"})

    def test_dispatch_defaults_and_explicit_fixture_are_unchanged(self):
        trigger = doc().get("on", doc().get(True))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(trigger["workflow_dispatch"]["inputs"]["fixture"]["default"], EXPECTED[0])
        events = [{}, {"inputs": {}}, {"inputs": {"fixture": ""}},
                  {"inputs": {"fixture": None}}]
        for event in events:
            self.assertEqual(planner.plan(ROOT, "workflow_dispatch", event),
                             {"include": [{"fixture": EXPECTED[0],
                                           "eval_key": EXPECTED[0][6:], "slot": 0}]})
        for path in EXPECTED:
            self.assertEqual(planner.plan(ROOT, "workflow_dispatch", {"inputs": {"fixture": path}})["include"],
                             [{"fixture": path, "eval_key": path[6:], "slot": 0}])

    def test_dispatch_rejects_invalid_types_nul_and_paths(self):
        for value in [False, True, 0, 123, [], {}, "\0" + EXPECTED[0],
                      EXPECTED[0] + "\n", "evals/../" + EXPECTED[0], "evals/workflow-path-audit", "evals/nope"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                planner.plan(ROOT, "workflow_dispatch", {"inputs": {"fixture": value}})
        for inputs in [False, 0, [], "fixture"]:
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                planner.plan(ROOT, "workflow_dispatch", {"inputs": inputs})
        with self.assertRaises(ValueError):
            planner.plan(ROOT, "pull_request", {})

    def test_schema_rejects_unreviewed_shapes_duplicate_keys_and_notes(self):
        good = {"path": EXPECTED[0], "readiness": "Local smoke: with 8 vs without 7."}
        bad = [None, [], {}, {"fixtures": [good], "extra": 1},
               {"fixtures": {}}, {"fixtures": [None]}, {"fixtures": [{"path": EXPECTED[0]}]},
               {"fixtures": [dict(good, extra=1)]}, {"fixtures": [good, good]},
               {"fixtures": [good] * 257}]
        bad += [{"fixtures": [dict(good, readiness=n)]}
                for n in [None, 3, "", "  ", "line\nline", "line\rline", "\0", "\u2028"]]
        bad += [{"fixtures": [dict(good, path=p)]}
                for p in [None, "evals/nope", EXPECTED[0] + "\n"]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.yml"
            for document in bad:
                path.write_text(yaml.safe_dump(document))
                with self.subTest(document=document), self.assertRaises(ValueError):
                    planner.scheduled_fixtures(path, EXPECTED)
            for text in ["fixtures: []\nfixtures: []\n", "fixtures:\n  - path: evals/workflow-path-audit\n    path: evals/workflow-path-audit\n    readiness: smoke\n", "fixtures: [unclosed-private-marker"]:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    planner.scheduled_fixtures(path, EXPECTED)
            path.write_text(yaml.safe_dump({"fixtures": [good]}))
            self.assertEqual(planner.scheduled_fixtures(path, EXPECTED), [EXPECTED[0]])
            # The kit's starting state: an empty schedule plans nothing.
            path.write_text("fixtures: []\n")
            self.assertEqual(planner.scheduled_fixtures(path, EXPECTED), [])

    def test_schema_entrypoint_sanitizes_malformed_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            subprocess.run(["git", "init", "-q", str(base)], check=True)
            (base / "evals").mkdir()
            (base / "evals/scheduled.yml").write_text("fixtures: [private-source-marker")
            (base / "scripts").mkdir()
            shutil.copyfile(ROOT / "scripts/plan_scheduled_evals.py",
                            base / "scripts/plan_scheduled_evals.py")
            result = subprocess.run(["python3", "scripts/plan_scheduled_evals.py"],
                                    cwd=base, env=dict(os.environ, GITHUB_EVENT_NAME="schedule", GITHUB_EVENT_PATH="", GITHUB_OUTPUT=str(base / "out")),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "::error::Invalid eval plan or committed fixture selection\n")
            self.assertNotIn("private-source-marker", result.stdout + result.stderr)

    def test_committed_set_refuses_untracked_fixture_directories(self):
        tracked = planner.committed_fixtures(ROOT)
        self.assertTrue(set(EXPECTED) <= set(tracked))
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            subprocess.run(["git", "init", "-q", str(base)], check=True)
            tracked_path = base / "evals/tracked/fixture.yaml"
            tracked_path.parent.mkdir(parents=True)
            tracked_path.write_text("skill: tracked\n")
            subprocess.run(["git", "-C", str(base), "add", "evals/tracked/fixture.yaml"], check=True)
            untracked = base / "evals/untracked/fixture.yaml"
            untracked.parent.mkdir(parents=True)
            untracked.write_text("skill: untracked\n")
            self.assertEqual(planner.committed_fixtures(base), ["evals/tracked"])
            with self.assertRaises(ValueError):
                planner.validate_fixture("evals/untracked", planner.committed_fixtures(base))
            with self.assertRaises(ValueError):
                planner.validate_fixture("evals", ["evals"])

    def test_each_matrix_leg_selects_before_its_own_exchange(self):
        jobs = doc()["jobs"]
        for name, parallel in [("eval", 2), ("publish", 1)]:
            self.assertEqual(jobs[name]["strategy"], {"fail-fast": False, "max-parallel": parallel,
                             "matrix": "${{ fromJSON(needs.plan.outputs.matrix) }}"})
        steps = jobs["eval"]["steps"]
        select = next(i for i, s in enumerate(steps) if s.get("id") == "select-fixture")
        exchange = next(i for i, s in enumerate(steps) if s["name"].startswith("Mint OIDC"))
        self.assertLess(select, exchange)
        self.assertEqual(sum(s["name"].startswith("Mint OIDC") for s in steps), 1)
        selection = steps[select]
        self.assertEqual(selection["env"]["SCHEDULE_FIXTURE"], "${{ github.event_name == 'schedule' && matrix.fixture || '' }}")
        for path in EXPECTED:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                event = base / "event.json"
                event.write_text(json.dumps({"inputs": {"fixture": "evals/nope"}}))
                env = dict(os.environ, SCHEDULE_FIXTURE=path, MATRIX_KEY=path[6:], MATRIX_SLOT="0", GITHUB_EVENT_PATH=str(event), GITHUB_OUTPUT=str(base / "out"), RUNNER_TEMP=tmp)
                result = subprocess.run(["bash", "-c", selection["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((base / "eval-fixture").read_text(), path)
                env["MATRIX_KEY"] = "wrong-fixture"
                result = subprocess.run(["bash", "-c", selection["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)

    def test_permissions_credentials_and_roster_jobs_do_not_widen(self):
        workflow = doc()
        self.assertEqual(workflow["permissions"], {})
        expected = {"plan": {"contents": "read"},
                    "eval": {"contents": "read", "id-token": "write"},
                    "publish": {"contents": "write"},
                    "roster": {"contents": "read", "id-token": "write", "issues": "write"},
                    "disarm": {"contents": "read", "pull-requests": "write"},
                    "roster-pr": {"contents": "read", "pull-requests": "write", "issues": "write"},
                    "roster-wait": {"contents": "read", "pull-requests": "write"}}
        self.assertEqual({name: job["permissions"] for name, job in workflow["jobs"].items()}, expected)
        secrets = []
        for name, job in workflow["jobs"].items():
            self.assertNotIn("env", job)
            if name in ("roster", "disarm", "roster-pr", "roster-wait"):
                self.assertNotIn("strategy", job)
            for s in job["steps"]:
                self.assertNotIn("${{", s.get("run", ""))
                secrets += [(name, key, value) for key, value in {**s.get("env", {}), **s.get("with", {})}.items() if "secrets." in str(value)]
                if "uses" in s:
                    self.assertIsNone(lock_problem(s["uses"], WORKFLOW))
        self.assertEqual(len(secrets), 1)
        self.assertEqual(secrets[0][0], "roster-pr")

    def test_artifacts_are_isolated_success_only_with_separate_diagnostics(self):
        payload = step("eval", "Upload eval results")
        download = step("publish", "Download eval results")
        diagnostic = step("eval", "Upload eval diagnostics")
        expected = "eval-results-payload-${{ matrix.slot }}-${{ github.run_attempt }}"
        self.assertEqual(payload["with"]["name"], expected)
        self.assertEqual(download["with"]["name"], expected)
        self.assertEqual(payload["if"], "${{ success() }}")
        self.assertEqual(payload["with"]["if-no-files-found"], "error")
        self.assertEqual(diagnostic["if"], "${{ failure() && !cancelled() }}")
        self.assertEqual(diagnostic["with"]["name"], "eval-results-diagnostics-${{ matrix.slot }}-${{ github.run_attempt }}")
        keys = [entry["eval_key"] for entry in planner.plan(ROOT, "schedule", {})["include"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("outputs", doc()["jobs"]["eval"])

    def test_artifacts_upload_an_allowlist_without_raw_transcripts(self):
        # #289: this repository is public, so a workflow artifact is public.
        # Each arm's transcripts/raw.json is unredacted, so neither upload may
        # name the whole results/ tree or raw.json; the only transcript file
        # allowed out is the redacted tool_trace.json.
        sys.path.insert(0, str(ROOT / "harness"))
        import run_eval
        allowed = {"summary.json", "report.md", run_eval.TOOL_TRACE_NAME}
        self.assertEqual(set(run_eval.ARM_DIR_FILES + run_eval.RUN_DIR_FILES) - allowed,
                         {"raw.json"},
                         "a new results file needs a decision: upload it or not")
        prefix = "skills-evals/results/"
        for name in ("Upload eval results", "Upload eval diagnostics"):
            with self.subTest(step=name):
                upload = step("eval", name)["with"]
                patterns = [line.strip() for line in upload["path"].splitlines() if line.strip()]
                self.assertEqual(upload["include-hidden-files"], False)
                for pattern in patterns:
                    self.assertTrue(pattern.startswith(prefix), pattern)
                    self.assertFalse(pattern.endswith("/"), "a whole directory: " + pattern)
                    self.assertNotIn("!", pattern)
                    self.assertNotIn("raw", pattern)
                    # The last segment must be a literal allowed file name: the
                    # pinned upload-artifact (@actions/glob, implicitDescendants)
                    # uploads everything under a matched directory, so a final
                    # `**`, `*` or directory name would publish raw.json too.
                    self.assertIn(pattern.rsplit("/", 1)[1], allowed, pattern)
                    if "transcripts" in pattern:
                        self.assertTrue(pattern.endswith("/transcripts/" + run_eval.TOOL_TRACE_NAME), pattern)
                with tempfile.TemporaryDirectory() as tmp:
                    results = Path(tmp)
                    run_eval._write_summary(results, "fixture", "with_skill", "20261005T000000Z",
                                            None, None, None, None, {"result": "x"},
                                            tool_trace={"events": []})
                    run_eval._write_summary(results, "fixture", "without_skill", "20261005T000000Z",
                                            None, None, None, None, {"result": "x"},
                                            arm_dir=results / "fixture/20261005T000000Z/nested/without_skill/trial-1")
                    (results / "fixture/20261005T000000Z" / run_eval.REPORT_NAME).write_text("r")
                    written = {str(f.relative_to(results)) for f in results.rglob("*") if f.is_file()}
                    uploaded = set()
                    for pattern in patterns:
                        for match in results.glob(pattern[len(prefix):]):
                            # implicitDescendants: a matched directory uploads
                            # every file beneath it.
                            files = match.rglob("*") if match.is_dir() else [match]
                            uploaded |= {str(f.relative_to(results)) for f in files if f.is_file()}
                    self.assertEqual(written - uploaded, {
                        "fixture/20261005T000000Z/with_skill/transcripts/raw.json",
                        "fixture/20261005T000000Z/nested/without_skill/trial-1/transcripts/raw.json"})
                    self.assertTrue(uploaded <= written)

    def test_publisher_revalidates_fixture_key_before_matching_download(self):
        steps = doc()["jobs"]["publish"]["steps"]
        validation = step("publish", "Validate the publishing fixture and key")
        self.assertLess(steps.index(validation), steps.index(step("publish", "Download eval results")))
        self.assertEqual(validation["env"], {"FIXTURE": "${{ matrix.fixture }}", "EVAL_KEY": "${{ matrix.eval_key }}", "SLOT": "${{ matrix.slot }}"})
        for path in EXPECTED:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                env = dict(os.environ, FIXTURE=path, EVAL_KEY=path[6:], SLOT="0", GITHUB_OUTPUT=str(output))
                result = subprocess.run(["bash", "-c", validation["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(output.read_text(), "eval_key=" + path[6:] + "\n")
                for key, slot in [("wrong", "0"), (path[6:], "256"), (path[6:], "-1"), (path[6:], "x")]:
                    env.update(EVAL_KEY=key, SLOT=slot)
                    result = subprocess.run(["bash", "-c", validation["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)

    def test_successful_siblings_publish_and_missing_payload_is_reported(self):
        gate = doc()["jobs"]["publish"]["if"]
        self.assertIn("needs.eval.result == 'success' || needs.eval.result == 'failure' || needs.eval.result == 'cancelled'", gate)
        self.assertIn("!cancelled()", gate)
        self.assertIn("needs.plan.result == 'success'", gate)
        self.assertIn("needs.roster-wait.outputs.cleared == 'true'", gate)
        download = step("publish", "Download eval results")
        self.assertTrue(download["continue-on-error"])
        report = step("publish", "Report a missing success payload")
        self.assertEqual(report["if"], "${{ steps.download.outcome == 'failure' }}")
        commit = step("publish", "Build the badge over the run window, commit, and push")
        self.assertEqual(commit["if"], "${{ steps.download.outcome == 'success' }}")
        for aggregate, rc in [("failure", 0), ("cancelled", 0), ("success", 1)]:
            with tempfile.TemporaryDirectory() as tmp:
                summary = Path(tmp) / "summary"
                result = subprocess.run(["bash", "-c", report["run"]], env=dict(os.environ, EVAL_RESULT=aggregate, GITHUB_STEP_SUMMARY=str(summary)), capture_output=True, text=True)
                self.assertEqual(result.returncode, rc)
                self.assertIn("publishing is skipped", summary.read_text())

    def test_fixture_badge_uses_accumulated_history_and_validated_key(self):
        commit = step("publish", "Build the badge over the run window, commit, and push")
        self.assertEqual(commit["env"]["EVAL_KEY"], "${{ steps.fixture.outputs.eval_key }}")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            scripts = workspace / "scripts"
            scripts.mkdir(parents=True)
            shutil.copyfile(ROOT / "scripts/make_badge.py", scripts / "make_badge.py")
            history = base / "history"
            history.mkdir()
            runner = base / "runner"
            runner.mkdir()
            key = "skills-doctor/bucketed-account-store"
            for tree, stamp, scores in [(history, "20260927T070000Z", (3, 3)),
                                        (workspace / "results", "20261004T070000Z", (5, 3))]:
                for arm, passed in zip(("with_skill", "without_skill"), scores):
                    directory = tree / "skills-doctor" / stamp / "bucketed-account-store" / arm
                    directory.mkdir(parents=True)
                    (directory / "summary.json").write_text(json.dumps({
                        "objective_checks": [{"passed": i < passed} for i in range(5)]}))
            fake_bin = base / "bin"
            fake_bin.mkdir()
            claude_calls = base / "claude-calls"
            refusal = fake_bin / "claude"
            refusal.write_text('#!/bin/sh\nprintf "called\\n" >> "$CLAUDE_CALLS"\nexit 97\n')
            refusal.chmod(0o755)
            git = fake_bin / "git"
            git.write_text("""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
with open(os.environ["GIT_CALLS"], "a") as output:
    output.write(json.dumps(args) + "\\n")
if args[:2] == ["checkout", "-B"]:
    shutil.copytree(os.environ["HISTORY_TREE"], "results", dirs_exist_ok=True)
if args == ["diff", "--cached", "--quiet"]:
    raise SystemExit(1)
""")
            git.chmod(0o755)
            calls_path = base / "git-calls"
            env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                       HISTORY_TREE=str(history), GIT_CALLS=str(calls_path),
                       CLAUDE_CALLS=str(claude_calls),
                       EVAL_KEY=key, GITHUB_TOKEN="offline-fixture", ROSTER_LATEST_JSON="",
                       RUNNER_TEMP=str(runner), GITHUB_STEP_SUMMARY=str(base / "summary"))
            result = subprocess.run(["bash", "-c", commit["run"]], cwd=workspace,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads((workspace / "badges" / (key + ".json")).read_text())
            self.assertEqual(actual["message"], "with 4/5 vs without 3/5 · n=2 · 2026-10-04")
            self.assertEqual(actual["color"], "green")
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
            self.assertIn(["commit", "-m", "eval: " + key + " run + badge + roster [skip ci]"], calls)
            pushes = [args for args in calls if "push" in args]
            self.assertEqual(len(pushes), 1)
            self.assertEqual(pushes[0][-3:], ["push", "origin", "persistent/eval-results"])
            self.assertFalse((workspace / ".git").exists())
            self.assertFalse(claude_calls.exists() and claude_calls.read_text())

    def test_timeout_covers_every_scheduled_fixture_with_default_budgets(self):
        budget = doc()["jobs"]["eval"]["timeout-minutes"] * 60
        fixtures = planner.scheduled_fixtures(ROOT / "evals/scheduled.yml", planner.committed_fixtures(ROOT))
        self.assertEqual(len(fixtures), len(EXPECTED))
        for path in fixtures:
            fixture = yaml.safe_load((ROOT / path / "fixture.yaml").read_text())
            arms = fixture.get("arms") or {"with_skill": {}, "without_skill": {}}
            per_arm = fixture.get("setup_timeout_s", 60) + fixture.get("timeout_s", 600) + (fixture.get("judge") or {}).get("timeout_s", 120)
            # WIF preflight can retry once, with two 120 s attempts. Leave
            # room for checkout, dependency install, and summary overhead.
            self.assertLess(len(arms) * per_arm + 2 * 120, budget * .75, path)

    def test_guidance_section_fixture_badge_reads_only_its_own_runs(self):
        # Its eval key is its path without evals/, three segments; its runs
        # share results/guidance/<section>/ with the section's other fixtures.
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            key = "guidance/anything-you-name-gets-its-link/handoff"
            for stamp, name, scores in [("20261004T070000Z", "handoff", (5, 3)),
                                        ("20261005T070000Z", "no-url", (0, 5))]:
                for arm, passed in zip(("with_guidance_full",
                                        "with_guidance_full_minus_section"), scores):
                    directory = results / "guidance/anything-you-name-gets-its-link" / stamp / arm
                    directory.mkdir(parents=True)
                    (directory / "summary.json").write_text(json.dumps({
                        "fixture_name": name,
                        "objective_checks": [{"passed": i < passed} for i in range(5)]}))
            result = badge.build_badge(results, key)
            self.assertEqual(result["message"], "with 5/5 vs without 3/5 · 2026-10-04")
            self.assertEqual(result["label"],
                             "guidance eval: anything-you-name-gets-its-link/handoff")
            for bad in ("guidance/a/b/c", "skill/a/b"):
                with self.assertRaises(ValueError):
                    badge.build_badge(results, bad)

    def test_nested_fixture_badge_reads_only_its_own_runs_and_preserves_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            key = "skills-doctor/bucketed-account-store"
            for stamp, fixture, scores in [("20261004T070000Z", "bucketed-account-store", (5, 3)),
                                            ("20260927T070000Z", "bucketed-account-store", (3, 3)),
                                            ("20261005T070000Z", "other-fixture", (0, 5))]:
                for arm, passed in zip(("with_skill", "without_skill"), scores):
                    directory = results / "skills-doctor" / stamp / fixture / arm
                    directory.mkdir(parents=True)
                    (directory / "summary.json").write_text(json.dumps({"objective_checks": [{"passed": i < passed} for i in range(5)]}))
            result = badge.build_badge(results, key, window=2)
            self.assertEqual(result["color"], "green")
            self.assertEqual(result["message"], "with 4/5 vs without 3/5 · n=2 · 2026-10-04")
            self.assertEqual(result["label"], "skill eval: " + key)
            latest = badge.build_badge(results, key, window=1)
            self.assertEqual(latest["message"], "with 5/5 vs without 3/5 · 2026-10-04")
            flat = results / "workflow-path-audit/20261004T070000Z"
            for arm, passed in [("with_skill", True), ("without_skill", False)]:
                (flat / arm).mkdir(parents=True)
                (flat / arm / "summary.json").write_text(json.dumps({"objective_checks": [{"passed": passed}]}))
            self.assertEqual(badge.build_badge(results, "workflow-path-audit")["color"], "green")
            summary = results / "skills-doctor/20261004T070000Z/bucketed-account-store/with_skill/summary.json"
            summary.write_text(json.dumps({"local_exhibit": True}))
            with self.assertRaises(SystemExit):
                badge.build_badge(results, key)


class WeeklyRoutineCallerTests(unittest.TestCase):
    """The scheduled caller dispatches the banded plan (scripts/plan_week.py);
    test_issue_closed_loop_planner.py covers the planner itself."""

    def workflow(self):
        return yaml.safe_load((ROOT / ".github/workflows/routine-eval-weekly.yml").read_text())

    def test_weekly_caller_plans_by_band_and_leaves_fire_credentials_separate(self):
        workflow = self.workflow()
        trigger = workflow.get("on", workflow.get(True))
        # A manual dispatch runs the sweep when GitHub skips the schedule
        # (2026-10-27); it takes no inputs.
        self.assertEqual(trigger, {"schedule": [{"cron": "0 7 * * 2"}],
                                   "workflow_dispatch": None})
        self.assertEqual(workflow["permissions"], {})
        self.assertNotIn("concurrency", workflow)
        jobs = workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "dispatch"})
        self.assertEqual(jobs["plan"]["permissions"],
                         {"contents": "read", "actions": "read", "issues": "read",
                          "pull-requests": "read"})
        self.assertEqual(jobs["dispatch"]["permissions"],
                         {"contents": "read", "actions": "write"})
        self.assertEqual(jobs["dispatch"]["needs"], "plan")
        self.assertEqual(jobs["dispatch"]["if"],
                         jobs["plan"]["if"] + " && needs.plan.outputs.count != '0'")
        self.assertEqual(jobs["plan"]["outputs"],
                         {"matrix": "${{ steps.plan.outputs.matrix }}",
                          "count": "${{ steps.plan.outputs.count }}"})
        self.assertEqual(jobs["dispatch"]["strategy"],
                         {"fail-fast": False, "max-parallel": 1,
                          "matrix": "${{ fromJSON(needs.plan.outputs.matrix) }}"})
        plan_step = next(s for s in jobs["plan"]["steps"] if s.get("id") == "plan")
        self.assertIn("python3 scripts/plan_week.py plan --results-tree eval-results-branch",
                      plan_step["run"])
        self.assertIn("--max-fires 30", plan_step["run"])
        self.assertEqual(plan_step["env"], {"GH_TOKEN": "${{ github.token }}"})
        steps = jobs["dispatch"]["steps"]
        names = [s["name"] for s in steps]
        order = [names.index("Revalidate the planned fire"),
                 names.index("Wait until fewer than three fires are in flight"),
                 names.index("Dispatch the validated fire"),
                 names.index("Space the next fire")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(steps[order[3]]["run"], "sleep 60")
        fire = steps[order[2]]
        wait = steps[order[1]]
        for job in jobs.values():
            self.assertNotIn("concurrency", job)
            for item in job["steps"]:
                self.assertNotIn("${{", item.get("run", ""))
                if "uses" in item:
                    self.assertIsNone(lock_problem(item["uses"], "routine-eval-weekly.yml"))
                    if "checkout@" in item["uses"]:
                        self.assertEqual(item["with"]["persist-credentials"], False)
                if item not in (fire, plan_step, wait):
                    self.assertNotIn("GH_TOKEN", item.get("env", {}))
                self.assertNotIn("EVAL_ROUTINE_FIRE_BEARER", str(item))

    def dispatch(self, env_extra, fail=False):
        steps = self.workflow()["jobs"]["dispatch"]["steps"]
        script = next(s for s in steps if s["name"] == "Dispatch the validated fire")["run"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bin_dir = base / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text("#!/usr/bin/env python3\n"
                          "import json, os, sys\n"
                          "open(os.environ['CAPTURE'], 'w').write(json.dumps(sys.argv[1:]))\n"
                          "if os.environ.get('FAIL_DISPATCH') == '1':\n"
                          "    print('private-source-marker', file=sys.stderr)\n"
                          "    sys.exit(1)\n")
            gh.chmod(0o755)
            command = base / "dispatch.sh"
            command.write_text(script)
            capture = base / "capture"
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                       CAPTURE=str(capture), GITHUB_REPOSITORY="example.com/sample",
                       DEFAULT_BRANCH="main", FIXTURE="", MODEL="", SKILL="", HOLDOUT="",
                       FAIL_DISPATCH="1" if fail else "0")
            env.update(env_extra)
            result = subprocess.run(["bash", str(command)], cwd=ROOT, env=env,
                                    text=True, capture_output=True)
            sent = json.loads(capture.read_text()) if capture.exists() else None
            return result, sent

    def test_dispatch_sends_each_mode_and_sanitizes_failure(self):
        result, sent = self.dispatch({"MODE": "eval", "ARMS": "both", "FIXTURE": EXPECTED[0],
                                      "MODEL": "claude-opus-5-5"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sent, ["workflow", "run", "routine-eval-fire.yml", "--repo",
                                "example.com/sample", "--ref", "main", "--raw-field",
                                "mode=eval", "--raw-field", "fixture=" + EXPECTED[0],
                                "--raw-field", "model=claude-opus-5-5",
                                "--raw-field", "arms=both", "--raw-field", "trials=1"])
        result, sent = self.dispatch({"MODE": "improve", "SKILL": "writing-adrs",
                                      "HOLDOUT": "bootstrap"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sent[-8:], ["--raw-field", "skill=writing-adrs",
                                     "--raw-field", "holdout=bootstrap",
                                     "--raw-field", "arms=both", "--raw-field", "trials=3"])
        self.assertIn("mode=improve", sent)
        result, sent = self.dispatch({"MODE": "scaffold"})
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(sent)
        # A bare baseline (ADR 0012's 2026-10-27 addendum): one arm at the
        # planner's trials; an eval leg with any other arms sends nothing.
        result, sent = self.dispatch({"MODE": "eval", "ARMS": "bare", "FIXTURE": EXPECTED[0],
                                      "MODEL": "claude-opus-5-5"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sent[-8:], ["--raw-field", "fixture=" + EXPECTED[0],
                                      "--raw-field", "model=claude-opus-5-5",
                                      "--raw-field", "arms=bare",
                                      "--raw-field", f"trials={bare_trials()}"])
        for arms in ("", "with", "without", "both bare"):
            with self.subTest(arms=arms):
                result, sent = self.dispatch({"MODE": "eval", "ARMS": arms,
                                              "FIXTURE": EXPECTED[0], "MODEL": "m"})
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(sent)
        result, _ = self.dispatch({"MODE": "eval", "ARMS": "both", "FIXTURE": EXPECTED[0],
                                   "MODEL": "claude-opus-5-5"}, fail=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Routine eval dispatch failed", result.stdout)
        self.assertNotIn("private-source-marker", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
