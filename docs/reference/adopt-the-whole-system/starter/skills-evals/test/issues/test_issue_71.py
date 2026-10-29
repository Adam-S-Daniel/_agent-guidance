#!/usr/bin/env python3
"""Issue #71 (smallest slice): scripts/propose_skill_edit.py.

Every model-spending step goes through the script's `Runner`, so these tests
drive the whole pipeline with a fake one: no real CLI, no network, no clock
(the timestamp is passed in). Subprocess contracts exercise `local_eval.py`
with stand-in CLIs and both guarded launch paths with an always-available
fake `scripts/run_loop.py`. An additional contract uses the installed
skill-creator's own loop with a stand-in CLI; it is skipped when the plugin
is absent (CI does not install it). Set SKILL_CREATOR_DIR to run that contract.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_71.py`.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
#: The invented test-only fixtures (the kit ships none, its DECISIONS X9),
#: laid out as a checkout's evals/ (test/example_evals.py).
FIXTURE_EVALS = TEST_DIR / "fixtures" / "evals"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import propose_skill_edit as pse  # noqa: E402
import test_issue_local_eval as local_eval_tests  # noqa: E402

SKILL = "writing-adrs"
FIXTURES = ["bootstrap", "existing-convention", "supersede"]
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
TS = "20261004T120000Z"

ORIGINAL_SKILL_MD = textwrap.dedent("""\
    ---
    name: writing-adrs
    description: Write an Architecture Decision Record when a decision needs context.
    ---

    # Writing ADRs

    Step one: read the existing convention.
    Step two: write the record.
    """)

BODY_DIFF = textwrap.dedent("""\
    --- a/SKILL.md
    +++ b/SKILL.md
    @@ -6,4 +6,5 @@
     # Writing ADRs

     Step one: read the existing convention.
     Step two: write the record.
    +Step three: add the record to the index in the same change.
    """)

NEW_DESCRIPTION = "Use this skill to record a non-obvious decision as an ADR."


def make_registry(root: Path, text: str = ORIGINAL_SKILL_MD) -> Path:
    """A one-commit git repo laid out like agentskills."""
    skill_dir = root / "plugins" / "demo" / "skills" / SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    (root / "README.md").write_text("fictional registry\n", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "test-market", "plugins": [{"name": "demo", "source": "./plugins/demo"}]}))
    (root / "plugins" / "demo" / ".claude-plugin").mkdir()
    (root / "plugins" / "demo" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo"}))
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True, env=env)
    return root


def make_skill_creator(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run_loop.py").write_text("# stand-in\n", encoding="utf-8")
    return root


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def usage_block(tokens: int) -> dict:
    """A CLI `usage` block whose four token counts sum to `tokens`."""
    tenth = tokens // 10
    return {"input_tokens": tenth, "output_tokens": tenth,
            "cache_creation_input_tokens": tenth,
            "cache_read_input_tokens": tokens - 3 * tenth}


def model_usage_block(tokens: int, model: str = "model-a") -> dict:
    """A summary's `model_tokens` and `cross_model` for one complete model
    whose four counts sum to `tokens`."""
    counts = usage_block(tokens)
    return {"model_tokens": {model: {
                "input_tokens": counts["input_tokens"],
                "output_tokens": counts["output_tokens"],
                "cache_read_input_tokens": counts["cache_read_input_tokens"],
                "cache_creation_input_tokens": counts["cache_creation_input_tokens"],
                "canonical_model": None}},
            "cross_model": {"model": model, "canonical_model": None,
                            "complete": True, "dropped": 0, "other_share": None,
                            "threshold": 0.5, "flagged": None}}


def write_arm(run_dir: Path, ts: str, fixture: str,
              passed: int, total: int, judge: float | None,
              trials: int = 3, tokens: int | None = 1000,
              main_loop: int | None = None) -> None:
    """The per-trial layout written by local_eval's run_eval children. Every
    trial reports `tokens` in total across every model (None: an agent block
    with no usage and no `model_tokens`), and `main_loop` (default: `tokens`)
    in its main-loop `usage`."""
    main_loop = tokens if main_loop is None else main_loop
    for k in range(1, trials + 1):
        trial = run_dir / f"t{k}" / SKILL / ts / fixture / "with_skill"
        (trial / "transcripts").mkdir(parents=True)
        checks = [{"id": f"check-{i}", "passed": i < passed, "detail": f"detail {i}"}
                  for i in range(total)]
        (trial / "summary.json").write_text(json.dumps(
            {"error": None, "objective_checks": checks, "trial": k,
             "agent": {"cost_usd": 0.1,
                       **({"usage": usage_block(main_loop)} if tokens is not None else {})},
             "judge": {"overall": judge} if judge is not None else None,
             **(model_usage_block(tokens) if tokens is not None else {})}),
            encoding="utf-8")
        (trial / "transcripts" / "raw.json").write_text(
            json.dumps({"result": f"reply of trial {k}"}), encoding="utf-8")


class FakeRunner:
    """Records every call; answers from canned numbers."""

    def __init__(self, baseline: dict, candidate: dict, proposal: str | None,
                 best_description: str | None = None):
        self.numbers = {"baseline": baseline, "candidate": candidate}
        #: Tokens every trial reports, per phase; a test overrides these.
        self.tokens = {"baseline": 1000, "candidate": 1000}
        #: Main-loop `usage` tokens per phase; None: the same as `tokens`.
        self.main_loop = {"baseline": None, "candidate": None}
        self.proposal = proposal
        self.best_description = best_description
        self.calls: list[tuple] = []
        self.seen_skill_md: dict[str, str] = {}

    def run_eval(self, argv):
        run_dir = Path(flag(argv, "--results-dir"))
        label = run_dir.parent.parent.name
        registry = Path(flag(argv, "--registry").split("=", 1)[1])
        self.calls.append(("run_eval", label, registry, list(argv)))
        assert not run_dir.exists(), "local_eval destination must start empty"
        assert not (registry / ".git").exists(), "scratch registry carries .git"
        self.seen_skill_md[label] = (registry / "plugins" / "demo" / "skills"
                                     / SKILL / "SKILL.md").read_text(encoding="utf-8")
        for fixture, (passed, total, judge) in self.numbers[label].items():
            write_arm(run_dir, flag(argv, "--timestamp"), fixture,
                      passed, total, judge,
                      int(flag(argv, "--trials")), self.tokens[label],
                      self.main_loop[label])
        return 1

    def run_description_loop(self, argv, *, cwd, skill_creator):
        self.calls.append(("loop", list(argv), cwd, skill_creator))
        self.eval_set = json.loads(Path(flag(argv, "--eval-set")).read_text())
        self.loop_cwd_had_claude_dir = (Path(cwd) / ".claude").is_dir()
        self.loop_settings = json.loads((Path(cwd) / ".claude" / "settings.json").read_text())
        original = pse.frontmatter_data(
            (Path(flag(argv, "--skill-path")) / "SKILL.md").read_text())["description"]
        best = self.best_description or original
        # skill-creator's own shape: per-iteration results with run counts,
        # and one improve log per proposer call (one of them a rewrite).
        logs = Path(flag(argv, "--results-dir")) / "2026-10-04_120000" / "logs"
        logs.mkdir(parents=True)
        (logs / "improve_iter_1.json").write_text(json.dumps(
            {"iteration": 1, "rewrite_prompt": "shorter, please"}))
        runs = int(flag(argv, "--runs-per-query"))
        history = [{"iteration": i,
                    "train_results": [{"query": "q", "runs": runs}] * 4,
                    "test_results": [{"query": "t", "runs": runs}] * 2}
                   for i in (1, 2)]
        return {"best_description": best, "original_description": original,
                "best_test_score": "2/2", "best_train_score": "3/4",
                "iterations_run": 2, "history": history}

    def propose(self, prompt, model):
        self.calls.append(("propose", prompt, model))
        if self.proposal is None:
            raise AssertionError("no proposal call expected")
        return self.proposal


class NoCallRunner:
    def __getattr__(self, name):
        raise AssertionError(f"dry run made a {name} call")


class RefusingRunner(FakeRunner):
    def __init__(self, phase):
        super().__init__(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
        self.phase = phase

    def run_eval(self, argv):
        if Path(flag(argv, "--results-dir")).parent.parent.name == self.phase:
            self.calls.append(("refused", self.phase))
            return 2
        return super().run_eval(argv)


#: A reviewed --trigger-eval-set: three positives, three negatives, none of
#: them a fixture prompt.
REVIEWED_QUERIES = (
    [{"query": f"record the {topic} decision", "should_trigger": True}
     for topic in ("queue", "cache", "schema")]
    + [{"query": f"fix the flaky {thing} test", "should_trigger": False}
       for thing in ("login", "upload", "search")])

GOOD = {"bootstrap": (2, 4, 6.0), "existing-convention": (2, 4, 6.0),
        "supersede": (3, 4, 7.0)}


def proposal(diff: str = BODY_DIFF, rationale: str = "index step was missing") -> str:
    return "```json\n" + json.dumps({"rationale": rationale, "unified_diff": diff}) + "\n```"


def _skill_creator_dir_early() -> Path | None:
    try:
        return pse.find_skill_creator(None)[0]
    except pse.Refusal:
        return None


def frozen_evals(dest: Path) -> Path:
    """Every example fixture.yaml under test/fixtures/evals/, minus writing-adrs fixtures
    added after FIXTURES. The pipeline tests' canned numbers and the trigger
    guard's 2026-10-05 trial are both about exactly FIXTURES, so they read
    this snapshot; test_issue_80_why.py checks the live fixture set."""
    evals = FIXTURE_EVALS
    for path in evals.rglob("fixture.yaml"):
        rel = path.relative_to(evals)
        if rel.parts[0] == SKILL and rel.parts[1] not in FIXTURES:
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest / rel)
    return dest


class PipelineCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(
            prefix="test-issue-71-", dir=local_eval_tests.TestLocalEval._temp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = self.tmp / "home"
        home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(home),
                    "TMPDIR": str(self.tmp), "LANG": "C.UTF-8"}
        self.registry = make_registry(self.tmp / "registry")
        self.skill_creator = make_skill_creator(self.tmp / "skill-creator")
        self.results = self.tmp / "results"
        self.reviewed_set = self.tmp / "reviewed-queries.json"
        self.reviewed_set.write_text(json.dumps(REVIEWED_QUERIES))
        patcher = mock.patch.object(pse, "EVALS_DIR", frozen_evals(self.tmp / "frozen-evals"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def argv(self, *extra):
        reviewed = (["--trigger-eval-set", str(self.reviewed_set)]
                    if self.reviewed_set else [])
        return [SKILL, "--registry", f"agentskills={self.registry}",
                "--skill-creator", str(self.skill_creator),
                "--results-dir", str(self.results), *reviewed, *extra]

    def run_main(self, runner, *extra, env_extra=None):
        env = dict(self.env)
        env.update(env_extra or {})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pse.main(self.argv(*extra), runner=runner, now=NOW)
        return rc, out.getvalue(), err.getvalue()

    def record(self):
        return json.loads((self.results / "improvements" / SKILL / f"{TS}.json").read_text())


class SplitTests(unittest.TestCase):

    def test_split_is_deterministic_and_order_independent(self):
        self.assertEqual(pse.split_fixtures(["c", "a", "b"], 0), (["b", "c"], "a"))
        self.assertEqual(pse.split_fixtures(["a", "b", "c"], 0), (["b", "c"], "a"))

    def test_split_rotates(self):
        held = [pse.split_fixtures(FIXTURES, r)[1] for r in range(4)]
        self.assertEqual(held, FIXTURES + [FIXTURES[0]])

    def test_default_rotation_counts_existing_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = Path(tmp)
            self.assertEqual(pse.default_rotation(records / "absent"), 0)
            for name in ("a.json", "b.json", "a.patch"):
                (records / name).write_text("{}")
            self.assertEqual(pse.default_rotation(records), 2)

    def test_fewer_than_three_fixtures_is_refused(self):
        with self.assertRaisesRegex(pse.Refusal, "needs more fixtures"):
            pse.split_fixtures(["a", "b"], 0)


class RefusalTests(PipelineCase):

    def test_refused_environment_is_recorded_before_even_a_fake_baseline(self):
        for env in ({"ANTHROPIC_API_KEY": ""},
                    {"HTTPS_PROXY": "https://user:pass@example.com"}):
            with self.subTest(names=sorted(env)):
                runner = FakeRunner(GOOD, GOOD, proposal())
                rc, _, err = self.run_main(runner, env_extra=env)
                self.assertEqual(rc, 2)
                self.assertEqual(runner.calls, [])
                record = self.record()
                self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                                 ("refused", "preflight", 2))
                self.assertEqual(record["decision"], "refused")
                self.assertIn(next(iter(env)), err)
                shutil.rmtree(self.results)

    def test_parent_cloud_environment_does_not_reach_pipeline(self):
        parent_env = {"AZURE_EXTENSION_DIR": "/x", "GOOGLE_FOO": "y"}
        runner = FakeRunner(
            GOOD, {"bootstrap": (4, 4, 7.0), "existing-convention": (3, 4, 6.5),
                   "supersede": (3, 4, 7.0)}, proposal(), NEW_DESCRIPTION)
        with mock.patch.dict(os.environ, parent_env):
            rc, _, err = self.run_main(runner, "--rotation", "2")
            self.assertEqual(rc, 0, err)
            self.assertEqual(self.record()["status"], "accepted")
            self.assertEqual({name: os.environ[name] for name in parent_env}, parent_env)

    def test_explicit_cloud_environment_is_refused_before_runner_calls(self):
        for name, value in (("AZURE_EXTENSION_DIR", "/x"), ("GOOGLE_FOO", "y")):
            with self.subTest(name=name):
                shutil.rmtree(self.results, ignore_errors=True)
                runner = FakeRunner(GOOD, GOOD, proposal())
                rc, _, err = self.run_main(runner, env_extra={name: value})
                self.assertEqual(rc, 2)
                self.assertEqual(runner.calls, [])
                record = self.record()
                self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                                 ("refused", "preflight", 2))
                self.assertIn(name, err)
                self.assertIn(name, " ".join(record["reasons"]))

    def test_trigger_and_proposal_refusals_are_recorded_without_retry(self):
        for method, phase in (("run_description_loop", "trigger"),
                              ("propose", "proposal")):
            with self.subTest(phase=phase):
                runner = FakeRunner(GOOD, GOOD, proposal())
                with mock.patch.object(runner, method,
                                       side_effect=pse.Refusal("apiKeyHelper refused")) as launch:
                    rc, _, err = self.run_main(runner)
                self.assertEqual(rc, 2)
                self.assertEqual(launch.call_count, 1)
                self.assertIn("apiKeyHelper", err)
                record = self.record()
                self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                                 ("refused", phase, 2))
                self.assertEqual(record["decision"], "refused")
                self.assertIn("apiKeyHelper", record["reasons"][0])
                self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"],
                                 ["baseline"])
                shutil.rmtree(self.results)

    def test_baseline_local_eval_refusal_is_recorded_without_retry(self):
        runner = RefusingRunner("baseline")
        rc, _, err = self.run_main(runner)
        self.assertEqual(rc, 2)
        self.assertIn("local_eval baseline exited 2", err)
        record = self.record()
        self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                         ("refused", "baseline", 2))
        self.assertEqual(record["decision"], "refused")
        self.assertEqual(runner.calls, [("refused", "baseline")])

    def test_candidate_local_eval_refusal_is_recorded_without_retry(self):
        runner = RefusingRunner("candidate")
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 2)
        self.assertIn("local_eval candidate exited 2", err)
        record = self.record()
        self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                         ("refused", "candidate", 2))
        self.assertEqual(record["decision"], "refused")
        self.assertIn("candidate", record["runs"])
        self.assertEqual([c[0] for c in runner.calls].count("refused"), 1)

    def test_results_root_inside_repository_refuses_before_writes_or_calls(self):
        self.results = REPO_ROOT / "results" / "c42"
        runner = RefusingRunner("baseline")
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 2)
        self.assertIn("--results-dir", err)
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.results.exists())

    def test_persistent_write_subdirectory_symlink_into_repo_refuses(self):
        self.results.mkdir()
        (self.results / "improvements").symlink_to(REPO_ROOT,
                                                     target_is_directory=True)
        runner = RefusingRunner("baseline")
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 2)
        self.assertIn("--results-dir", err)
        self.assertEqual(runner.calls, [])

    def test_trigger_timestamp_symlink_into_repo_refuses(self):
        trigger = self.results / "trigger" / SKILL
        trigger.mkdir(parents=True)
        (trigger / TS).symlink_to(REPO_ROOT, target_is_directory=True)
        runner = RefusingRunner("baseline")
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 2)
        self.assertIn("--results-dir", err)
        self.assertEqual(runner.calls, [])
        self.assertFalse((self.results / "improvements").exists())

    def test_a_flat_single_fixture_skill_exits_2(self):
        rc, _, err = self.run_main(NoCallRunner(), "--dry-run")
        self.assertEqual(rc, 0)  # sanity: writing-adrs itself plans fine
        # A synthetic flat skill: workflow-path-audit, the example this used
        # to name, has nested fixtures enough to plan since 2026-10-26.
        evals = self.tmp / "evals"
        shutil.copytree(FIXTURE_EVALS / "workflow-path-audit"
                        / "required-check-routing", evals / "workflow-path-audit")
        argv = self.argv()
        argv[0] = "workflow-path-audit"
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(pse, "EVALS_DIR", evals), \
                contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
            rc = pse.main(argv, runner=NoCallRunner(), now=NOW)
        self.assertEqual(rc, 2)
        self.assertIn("needs more fixtures", buf.getvalue())

    def test_two_nested_fixtures_exit_2(self):
        evals = self.tmp / "evals"
        for name in FIXTURES[:2]:
            shutil.copytree(FIXTURE_EVALS / SKILL / name, evals / SKILL / name)
        with mock.patch.object(pse, "EVALS_DIR", evals):
            rc, _, err = self.run_main(NoCallRunner())
        self.assertEqual(rc, 2)
        self.assertIn("needs more fixtures", err)
        self.assertFalse(self.results.exists())

    def test_missing_skill_creator_is_named(self):
        argv = self.argv()
        argv[argv.index("--skill-creator") + 1] = str(self.tmp / "nowhere")
        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(err):
            rc = pse.main(argv, runner=NoCallRunner(), now=NOW)
        self.assertEqual(rc, 2)
        self.assertIn("--skill-creator", err.getvalue())


class DryRunTests(PipelineCase):

    def test_dry_run_makes_no_call_and_writes_nothing(self):
        rc, out, _ = self.run_main(NoCallRunner(), "--dry-run", "--rotation", "1")
        self.assertEqual(rc, 0)
        plan = json.loads(out)
        self.assertEqual(plan["validation"], "existing-convention")
        self.assertEqual(plan["train"], ["bootstrap", "supersede"])
        # The committed roster's first arm, read rather than named: the roster
        # changes whenever the vendor default does.
        import yaml
        roster = yaml.safe_load((REPO_ROOT / "evals" / "roster.yml").read_text(encoding="utf-8"))
        self.assertEqual(plan["models"]["arm"], roster["arms"][0]["id"])
        self.assertFalse(self.results.exists())


class AcceptTests(PipelineCase):

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner(
            GOOD, {"bootstrap": (4, 4, 7.0), "existing-convention": (3, 4, 6.5),
                   "supersede": (3, 4, 7.0)}, proposal(), NEW_DESCRIPTION)
        self.rc, self.out, _ = self.run_main(self.runner, "--rotation", "2")

    def test_accepts_and_writes_record_patch_and_pr_body(self):
        self.assertEqual(self.rc, 0, self.out)
        record = self.record()
        self.assertEqual(record["status"], "accepted")
        self.assertEqual(record["split"], {"rotation": 2, "validation": "supersede",
                                           "train": ["bootstrap", "existing-convention"]})
        stem = self.results / "improvements" / SKILL / TS
        self.assertTrue(stem.with_suffix(".patch").is_file())
        body = (stem.parent / f"{TS}.pr-body.md").read_text()
        self.assertIn("| bootstrap | train | 6/12 | 12/12 | 6.00 | 7.00 |", body)
        self.assertIn("| supersede | validation | 9/12 | 9/12 | 7.00 | 7.00 |", body)
        self.assertIn(NEW_DESCRIPTION, body)
        self.assertIn("index step was missing", body)
        self.assertIn("ADR 0002", body)

    def test_both_halves_reach_the_measured_candidate(self):
        measured = self.runner.seen_skill_md["candidate"]
        self.assertIn("Step three: add the record to the index", measured)
        self.assertEqual(pse.frontmatter_data(measured)["description"], NEW_DESCRIPTION)
        self.assertEqual(pse.frontmatter_data(measured)["name"], SKILL)
        self.assertEqual(self.runner.seen_skill_md["baseline"], ORIGINAL_SKILL_MD)

    def test_patch_applies_to_the_registry_and_reproduces_the_candidate(self):
        patch = (self.results / "improvements" / SKILL / f"{TS}.patch").read_text()
        subprocess.run(["git", "-C", str(self.registry), "apply", "-"],
                       input=patch, text=True, check=True)
        applied = (self.registry / "plugins" / "demo" / "skills" / SKILL
                   / "SKILL.md").read_text()
        self.assertEqual(applied, self.runner.seen_skill_md["candidate"])

    def test_measurement_uses_local_eval_with_n_trials_on_with_skill(self):
        evals = [c for c in self.runner.calls if c[0] == "run_eval"]
        self.assertEqual([c[1] for c in evals], ["baseline", "candidate"])
        for _, _, _, argv in evals:
            self.assertEqual(flag(argv, "--arm"), "with_skill")
            self.assertEqual(flag(argv, "--trials"), "3")
            self.assertEqual(flag(argv, "--timestamp"), TS)
            self.assertEqual(Path(argv[0]), pse.EVALS_DIR / SKILL)
            self.assertFalse(Path(flag(argv, "--results-dir")).is_relative_to(REPO_ROOT))

    def test_both_measurements_deny_their_arms_the_whole_results_root(self):
        # The candidate's arms must not read the baseline run or the proposed
        # patch, both under the results root beside the candidate's own run.
        evals = [c for c in self.runner.calls if c[0] == "run_eval"]
        self.assertEqual(len(evals), 2)
        for _, _, _, argv in evals:
            self.assertEqual(Path(flag(argv, "--read-deny")), self.results.resolve())

    def test_trigger_half_never_sees_the_validation_prompt(self):
        validation_prompt = " ".join(pse.run_eval.load_fixture(
            FIXTURE_EVALS / SKILL / "supersede")["prompt"].split())
        queries = [i["query"] for i in self.runner.eval_set]
        self.assertNotIn(validation_prompt, queries)
        self.assertEqual(self.runner.eval_set, REVIEWED_QUERIES)

    def test_skill_creator_loop_runs_in_a_scratch_project(self):
        _, argv, cwd, skill_creator = next(c for c in self.runner.calls if c[0] == "loop")
        self.assertTrue(self.runner.loop_cwd_had_claude_dir)
        self.assertFalse(Path(cwd).resolve().is_relative_to(REPO_ROOT))
        self.assertEqual(skill_creator, self.skill_creator.resolve())
        self.assertEqual(flag(argv, "--holdout"), "0.4")
        self.assertEqual(flag(argv, "--report"), "none")

    def test_proposal_prompt_carries_train_failures_only(self):
        _, prompt, model = next(c for c in self.runner.calls if c[0] == "propose")
        self.assertEqual(model, pse.proposal_model())
        self.assertIn('<fixture name="bootstrap">', prompt)
        self.assertNotIn('<fixture name="supersede">', prompt)
        self.assertIn("reply of trial 1", prompt)


class TriggerEvalSetOverrideTests(PipelineCase):

    def test_validation_prompt_is_dropped_from_a_supplied_set(self):
        validation_prompt = pse.run_eval.load_fixture(
            FIXTURE_EVALS / SKILL / "supersede")["prompt"]
        supplied = self.tmp / "queries.json"
        supplied.write_text(json.dumps([
            {"query": "  " + validation_prompt.replace(" ", "\n  "), "should_trigger": True},
            {"query": "record why we picked this queue", "should_trigger": True},
            {"query": "fix this flaky test", "should_trigger": False}]))
        runner = FakeRunner(GOOD, {}, proposal(""))
        self.run_main(runner, "--rotation", "2", "--trigger-eval-set", str(supplied))
        self.assertEqual([i["query"] for i in runner.eval_set],
                         ["record why we picked this queue", "fix this flaky test"])
        self.assertEqual(self.record()["description_half"]["eval_set_source"],
                         "file:queries.json")


class NoHeadroomTests(PipelineCase):
    """A baseline that leaves less than the minimum gain, with a judge that
    cannot make it up, stops before any trigger, proposal or candidate call."""

    @staticmethod
    def numbers(passed: int, total: int, judge: float | None) -> dict:
        return {name: (passed, total, judge) for name in GOOD}

    def test_baseline_above_ninety_percent_stops_before_any_model_call(self):
        numbers = self.numbers(19, 20, None)
        runner = FakeRunner(numbers, numbers, proposal(), NEW_DESCRIPTION)
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual((record["status"], record["decision"]), ("rejected", "rejected"))
        self.assertIn("no train headroom", " ".join(record["reasons"]))
        self.assertIn("0.950", " ".join(record["reasons"]))
        self.assertEqual([c[0] for c in runner.calls], ["run_eval"])

    def test_a_judge_with_room_keeps_the_run_going(self):
        numbers = self.numbers(19, 20, 8.0)
        runner = FakeRunner(numbers, numbers, proposal(), NEW_DESCRIPTION)
        self.run_main(runner)
        self.assertNotIn("no train headroom", " ".join(self.record()["reasons"]))
        self.assertIn("propose", [c[0] for c in runner.calls])

    def test_baseline_below_ninety_percent_keeps_the_run_going(self):
        numbers = self.numbers(17, 20, None)
        runner = FakeRunner(numbers, numbers, proposal(), NEW_DESCRIPTION)
        self.run_main(runner)
        self.assertNotIn("no train headroom", " ".join(self.record()["reasons"]))
        self.assertIn("propose", [c[0] for c in runner.calls])

    def test_exactly_ninety_percent_is_not_below_the_minimum_gain(self):
        numbers = self.numbers(9, 10, None)
        runner = FakeRunner(numbers, numbers, proposal(), NEW_DESCRIPTION)
        self.run_main(runner)
        self.assertNotIn("no train headroom", " ".join(self.record()["reasons"]))
        self.assertIn("propose", [c[0] for c in runner.calls])

    def test_pure_check(self):
        def metrics(rate, judge=None, errors=()):
            return {"errors": list(errors), "pass_rate": rate, "judge_mean": judge}
        self.assertIn("0.950", pse.no_headroom(metrics(0.95)))
        self.assertIn("0.989", pse.no_headroom(metrics(0.989, 9.5)))
        self.assertIsNone(pse.no_headroom(metrics(0.95, 9.0)))
        self.assertIsNone(pse.no_headroom(metrics(0.95, 8.0)))
        self.assertIsNone(pse.no_headroom(metrics(0.9)))
        self.assertIsNone(pse.no_headroom(metrics(0.85)))
        self.assertIsNone(pse.no_headroom(metrics(0.95, errors=["x"])))
        self.assertIsNone(pse.no_headroom(metrics(None)))
        self.assertIsNotNone(pse.no_headroom(metrics(0.95), min_gain=0.05 + 1e-3))


class RejectTests(PipelineCase):

    def test_missing_one_trial_is_inconclusive_instead_of_using_survivors(self):
        class MissingTrialRunner(FakeRunner):
            def run_eval(self, argv):
                rc = super().run_eval(argv)
                run_dir = Path(flag(argv, "--results-dir"))
                if run_dir.parent.parent.name == "baseline":
                    (run_dir / "t2" / SKILL / TS / "bootstrap" / "with_skill"
                     / "summary.json").unlink()
                return rc

        runner = MissingTrialRunner(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["baseline"]["bootstrap"]["error"], "missing_summary")
        self.assertIn("inconclusive", " ".join(record["reasons"]))

    def test_one_errored_trial_is_inconclusive(self):
        class ErroredTrialRunner(FakeRunner):
            def run_eval(self, argv):
                rc = super().run_eval(argv)
                run_dir = Path(flag(argv, "--results-dir"))
                if run_dir.parent.parent.name == "baseline":
                    path = (run_dir / "t2" / SKILL / TS / "bootstrap"
                            / "with_skill" / "summary.json")
                    summary = json.loads(path.read_text())
                    summary["error"] = {"type": "nonzero_exit"}
                    path.write_text(json.dumps(summary))
                return rc

        runner = ErroredTrialRunner(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["baseline"]["bootstrap"]["error"], "nonzero_exit")
        self.assertIn("inconclusive", " ".join(record["reasons"]))

    def test_partial_judge_manifest_and_malformed_errors_are_inconclusive(self):
        class PartialRunner(FakeRunner):
            def __init__(self, fault):
                super().__init__(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
                self.fault = fault

            def run_eval(self, argv):
                rc = super().run_eval(argv)
                run_dir = Path(flag(argv, "--results-dir"))
                if run_dir.parent.parent.name == "baseline":
                    if self.fault == "manifest":
                        (run_dir / "manifest.json").write_text(json.dumps({
                            "trials": [{"trial": 1, "exit_code": 0},
                                       {"trial": 2, "exit_code": 1},
                                       {"trial": 3, "exit_code": 0}]}))
                    else:
                        path = (run_dir / "t2" / SKILL / TS / "bootstrap"
                                / "with_skill" / "summary.json")
                        summary = json.loads(path.read_text())
                        if self.fault == "judge":
                            summary["judge"] = {"error": "unavailable"}
                        elif self.fault == "empty_type_none":
                            summary["error"] = {"type": None}
                        elif self.fault == "empty_type_string":
                            summary["error"] = {"type": ""}
                        else:
                            summary["error"] = "malformed"
                        path.write_text(json.dumps(summary))
                return rc

        for fault, expected in (("judge", "judge_error"),
                                ("manifest", "trial_exit"),
                                ("malformed", "trial_error"),
                                ("empty_type_none", "trial_error"),
                                ("empty_type_string", "trial_error")):
            with self.subTest(fault=fault):
                shutil.rmtree(self.results, ignore_errors=True)
                rc, _, _ = self.run_main(PartialRunner(fault), "--rotation", "2")
                self.assertEqual(rc, 1)
                record = self.record()
                self.assertEqual(record["status"], "rejected")
                self.assertEqual(record["baseline"]["bootstrap"]["error"], expected)
                self.assertIn("inconclusive", " ".join(record["reasons"]))

    def test_repeated_measurements_use_distinct_empty_directories(self):
        for offset in (0, 1):
            runner = FakeRunner(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
            now = NOW + timedelta(seconds=offset)
            with mock.patch.dict(os.environ, self.env, clear=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = pse.main(self.argv("--rotation", "2"), runner=runner, now=now)
            self.assertEqual(rc, 1)
            stamp = now.strftime(pse.run_eval.TIMESTAMP_FORMAT)
            record = json.loads((self.results / "improvements" / SKILL
                                 / f"{stamp}.json").read_text())
            self.assertEqual(record["status"], "rejected")
            for phase in ("baseline", "candidate"):
                self.assertTrue(all(m["error"] is None
                                    for m in record[phase].values()))
            for phase in ("baseline", "candidate"):
                path = pse.run_paths(self.results, phase, SKILL, stamp)
                self.assertTrue((path / "t1").is_dir())
        self.assertNotEqual(pse.run_paths(self.results, "baseline", SKILL, TS),
                            pse.run_paths(self.results, "baseline", "another-skill", TS))

    def test_validation_drop_is_rejected_and_recorded_with_numbers(self):
        runner = FakeRunner(GOOD, {"bootstrap": (4, 4, 7.0),
                                   "existing-convention": (4, 4, 7.0),
                                   "supersede": (2, 4, 7.0)}, proposal())
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["candidate"]["supersede"]["passed"], 6)
        self.assertEqual(record["baseline"]["supersede"]["passed"], 9)
        self.assertIn("validation objective fell", " ".join(record["reasons"]))
        self.assertFalse((self.results / "improvements" / SKILL / f"{TS}.pr-body.md").exists())
        self.assertTrue((self.results / "improvements" / SKILL / f"{TS}.patch").exists())

    def test_diff_touching_another_file_is_rejected_before_measurement(self):
        bad = BODY_DIFF.replace("a/SKILL.md", "a/README.md").replace("b/SKILL.md", "b/README.md")
        runner = FakeRunner(GOOD, {}, proposal(bad))
        rc, out, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"], ["baseline"])
        self.assertEqual(self.record()["status"], "invalid-proposal")
        self.assertEqual(self.record()["decision"], "rejected")
        self.assertIn("README.md", self.record()["reasons"][0])

    def test_diff_touching_the_frontmatter_is_rejected_before_measurement(self):
        bad = textwrap.dedent("""\
            --- a/SKILL.md
            +++ b/SKILL.md
            @@ -1,3 +1,3 @@
             ---
            -name: writing-adrs
            +name: writing-adrs-2
             description: Write an Architecture Decision Record when a decision needs context.
            """)
        runner = FakeRunner(GOOD, {}, proposal(bad))
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"], ["baseline"])
        self.assertIn("frontmatter", self.record()["reasons"][0])

    def test_no_change_from_either_half_measures_nothing(self):
        runner = FakeRunner(GOOD, {}, proposal(""))
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        self.assertEqual(self.record()["status"], "no-candidate")
        self.assertEqual(self.record()["decision"], "no-candidate")
        self.assertEqual(len([c for c in runner.calls if c[0] == "run_eval"]), 1)

    def test_no_candidate_records_tokens_for_train_validation_and_fixed_holdout(self):
        passes = {name: (4, 4, 7.0) for name in FIXTURES}
        runner = FakeRunner(passes, {}, None)
        rc, _, _ = self.run_main(runner, "--rotation", "0", "--holdout", "supersede")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["status"], "no-candidate")
        self.assertEqual(record["decision"], "no-candidate")
        self.assertEqual(record["split"], {"rotation": 0,
                                           "train": ["existing-convention"],
                                           "validation": "bootstrap",
                                           "holdout": "supersede"})
        self.assertEqual(set(record["baseline"]), set(FIXTURES))
        self.assertTrue(all(metric["tokens"] == 1000
                            for metric in record["baseline"].values()))
        self.assertFalse(any(call[0] == "propose" for call in runner.calls))

    def test_no_candidate_records_unknown_tokens_as_null(self):
        runner = FakeRunner(GOOD, {}, proposal(""))
        runner.tokens["baseline"] = None
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["decision"], "no-candidate")
        self.assertEqual(set(record["baseline"]), set(FIXTURES))
        self.assertTrue(all(metric["tokens"] is None
                            for metric in record["baseline"].values()))

    def test_refused_baseline_keeps_any_fixture_metrics_already_written(self):
        class PartialBaselineRunner(FakeRunner):
            def run_eval(self, argv):
                super().run_eval(argv)
                run_dir = Path(flag(argv, "--results-dir"))
                missing = run_dir / "t1" / SKILL / TS / "existing-convention" / "with_skill"
                shutil.rmtree(missing)
                return 2

        runner = PartialBaselineRunner(GOOD, {}, proposal(""))
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 2)
        record = self.record()
        self.assertEqual(record["status"], "refused")
        self.assertEqual(record["decision"], "refused")
        self.assertEqual(record["phase"], "baseline")
        self.assertEqual(record["baseline"]["bootstrap"]["tokens"], 1000)
        self.assertIsNone(record["baseline"]["existing-convention"]["tokens"])
        self.assertEqual(record["baseline"]["supersede"]["tokens"], 1000)


def trial_split(n: int, holdout: float = 0.4) -> tuple[int, int]:
    """skill-creator's split_eval_set arithmetic for one class: (train, held out)."""
    held = min(n, max(1, int(n * holdout)))
    return n - held, held


class TriggerSetGuardTests(PipelineCase):
    """The 2026-10-05 watched trial: writing-adrs' fixture-derived query set had
    one distinct positive, skill-creator held it out, and the trigger loop
    "passed" a train split of negatives only."""

    def setUp(self):
        super().setUp()
        self.reviewed_set = None  # The fixture-derived set, as the trial ran.

    def test_trial_split_refuses_with_a_record_and_no_call(self):
        rc, _, err = self.run_main(NoCallRunner(), "--rotation", "0")
        self.assertEqual(rc, 2)
        self.assertIn("trigger-set-unusable", err)
        record = self.record()
        self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                         ("trigger-set-unusable", "trigger-set", 2))
        self.assertEqual(record["decision"], "refused")
        self.assertIn("train split has no should-trigger query", record["reasons"][0])
        self.assertIn("--trigger-eval-set", record["reasons"][0])
        trigger_set = record["trigger_set"]
        self.assertEqual(trigger_set["source"], "fixtures")
        self.assertEqual(trigger_set["split"]["train"]["should_trigger"], 0)
        self.assertEqual(trigger_set["split"]["held_out"]["should_trigger"], 1)
        self.assertIsNone(record["baseline"])
        self.assertFalse((self.results / "trigger").exists())

    def test_every_writing_adrs_rotation_is_refused_by_dry_run(self):
        for rotation in range(3):
            with self.subTest(rotation=rotation):
                rc, out, err = self.run_main(NoCallRunner(), "--dry-run",
                                             "--rotation", str(rotation))
                self.assertEqual(rc, 2)
                planned = json.loads(out)["trigger_eval_set"]
                self.assertEqual(planned["split"]["train"]["should_trigger"], 0)
                self.assertTrue(planned["problems"])
                self.assertIn("trigger-set-unusable", err)
        self.assertFalse(self.results.exists())

    def test_duplicate_train_prompts_count_once(self):
        # Rotation 2 trains bootstrap and existing-convention, which share a
        # prompt: two identical positives would let the held-out copy leak.
        fixtures = pse.load_fixtures(SKILL, FIXTURES)
        items, source = pse.trigger_eval_set(
            SKILL, fixtures, ["bootstrap", "existing-convention"], "supersede", None)
        self.assertEqual(source, "fixtures")
        queries = [i["query"] for i in items]
        self.assertEqual(len(queries), len(set(queries)))
        self.assertEqual(sum(i["should_trigger"] for i in items), 1)
        validation = " ".join(fixtures["supersede"]["prompt"].split())
        self.assertNotIn(validation, queries)

    def test_reviewed_set_runs_and_records_its_split(self):
        thin = self.tmp / "thin.json"
        thin.write_text(json.dumps(
            [{"query": "record the queue decision", "should_trigger": True}]
            + REVIEWED_QUERIES[3:]))
        runner = FakeRunner(GOOD, {}, proposal(""))
        rc, _, err = self.run_main(runner, "--rotation", "2",
                                   "--trigger-eval-set", str(thin))
        self.assertEqual(rc, 1, err)
        self.assertIn("warning", err)
        record = self.record()
        self.assertEqual(record["status"], "no-candidate")
        self.assertEqual(record["trigger_set"]["source"], "file:thin.json")
        self.assertEqual(record["trigger_set"]["split"]["train"]["should_trigger"], 0)
        self.assertTrue(record["trigger_set"]["problems"])
        self.assertTrue(any(c[0] == "loop" for c in runner.calls))


class TriggerSplitCountTests(unittest.TestCase):

    def test_counts_follow_skill_creator_arithmetic(self):
        for positives in range(5):
            for negatives in range(5):
                items = ([{"query": f"p{i}", "should_trigger": True} for i in range(positives)]
                         + [{"query": f"n{i}", "should_trigger": False}
                            for i in range(negatives)])
                counts = pse.trigger_split_counts(items)
                for label, n in (("should_trigger", positives),
                                 ("should_not_trigger", negatives)):
                    train, held = trial_split(n)
                    self.assertEqual(counts["train"][label], train)
                    self.assertEqual(counts["held_out"][label], held)

    def test_problems_name_each_empty_class_in_each_split(self):
        one_each = [{"query": "p", "should_trigger": True},
                    {"query": "n", "should_trigger": False}]
        problems = pse.trigger_set_problems(pse.trigger_split_counts(one_each))
        self.assertEqual(problems, ["train split has no should-trigger query",
                                    "train split has no should-not-trigger query"])
        two_each = one_each + [{"query": "p2", "should_trigger": True},
                               {"query": "n2", "should_trigger": False}]
        self.assertEqual(pse.trigger_set_problems(pse.trigger_split_counts(two_each)), [])
        no_held_positive = {"train": {"should_trigger": 2, "should_not_trigger": 2},
                            "held_out": {"should_trigger": 0, "should_not_trigger": 1}}
        self.assertEqual(pse.trigger_set_problems(no_held_positive),
                         ["held-out split has no should-trigger query"])

    @unittest.skipUnless(_skill_creator_dir_early(), "skill-creator not installed")
    def test_counts_match_the_installed_split_eval_set(self):
        source = (_skill_creator_dir_early() / "scripts" / "run_loop.py").read_text()
        tree = ast.parse(source)
        split = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "split_eval_set")
        namespace: dict = {"random": __import__("random")}
        exec(compile(ast.Module(body=[split], type_ignores=[]), "run_loop.py", "exec"),
             namespace)
        for positives in range(5):
            for negatives in range(1, 5):
                items = ([{"query": f"p{i}", "should_trigger": True} for i in range(positives)]
                         + [{"query": f"n{i}", "should_trigger": False}
                            for i in range(negatives)])
                train, test = namespace["split_eval_set"](items, 0.4)
                counts = pse.trigger_split_counts(items)
                self.assertEqual(counts["train"]["should_trigger"],
                                 sum(i["should_trigger"] for i in train))
                self.assertEqual(counts["held_out"]["should_trigger"],
                                 sum(i["should_trigger"] for i in test))


class TriggerLoopUsageTests(PipelineCase):

    def test_record_counts_trigger_loop_calls_and_says_cost_is_unreported(self):
        runner = FakeRunner(GOOD, {}, proposal(""), None)
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1, err)
        usage = self.record()["description_half"]["usage"]
        # 2 iterations x 6 queries x 3 runs; 1 proposer call (between the two
        # iterations) plus the 1 rewrite its log records.
        self.assertEqual(usage["eval_calls"], 36)
        self.assertEqual(usage["description_calls"], 1)
        self.assertEqual(usage["rewrite_calls"], 1)
        self.assertEqual(usage["total_calls"], 38)
        self.assertIsNone(usage["tokens"])
        self.assertIsNone(usage["cost_usd"])
        self.assertIn("skill-creator", usage["cost_note"])

    def test_usage_tolerates_a_loop_without_history(self):
        usage = pse.trigger_loop_usage({"iterations_run": 1}, self.tmp / "absent")
        self.assertEqual((usage["eval_calls"], usage["description_calls"],
                          usage["rewrite_calls"], usage["total_calls"]),
                         (None, 0, 0, None))


class NumWorkersTests(PipelineCase):
    """skill-creator's parallel trigger workers share one project directory
    and undercount triggers (ADR 0005, serial trigger eval addendum), so the
    loop runs serially and refuses anything else before any call."""

    def test_default_is_one_and_is_recorded(self):
        runner = FakeRunner(GOOD, {}, proposal(""))
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1, err)
        argv = next(c[1] for c in runner.calls if c[0] == "loop")
        self.assertEqual(flag(argv, "--num-workers"), "1")
        self.assertEqual(self.record()["description_half"]["num_workers"], 1)

    def test_explicit_one_passes_through_to_skill_creator_and_plan(self):
        runner = FakeRunner(GOOD, {}, proposal(""))
        rc, _, err = self.run_main(runner, "--rotation", "2", "--num-workers", "1")
        self.assertEqual(rc, 1, err)
        argv = next(c[1] for c in runner.calls if c[0] == "loop")
        self.assertEqual(flag(argv, "--num-workers"), "1")
        shutil.rmtree(self.results)
        rc, out, _ = self.run_main(NoCallRunner(), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["num_workers"], 1)

    def test_more_than_one_worker_is_refused_naming_the_undercount(self):
        for value in ("2", "4", "10"):
            err = io.StringIO()
            with self.subTest(value=value), self.assertRaises(SystemExit) as exc, \
                    contextlib.redirect_stderr(err):
                pse.parse_args([SKILL, "--num-workers", value])
            self.assertEqual(exc.exception.code, 2)
            self.assertIn("share one project directory", err.getvalue())
            self.assertIn("undercounts triggers", err.getvalue())

    def test_parallel_workers_make_no_call(self):
        with self.assertRaises(SystemExit) as exc:
            self.run_main(NoCallRunner(), "--rotation", "2", "--num-workers", "4")
        self.assertEqual(exc.exception.code, 2)
        self.assertFalse((self.results / "improvements").exists())

    def test_num_workers_must_be_positive(self):
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as exc, \
                    contextlib.redirect_stderr(io.StringIO()):
                pse.parse_args([SKILL, "--num-workers", value])
            self.assertEqual(exc.exception.code, 2)


class DiffTargetTests(unittest.TestCase):

    def test_other_targets_are_refused(self):
        for header in ("--- a/SKILL.md\n+++ b/references/x.md\n",
                       "--- /dev/null\n+++ b/SKILL.md\n",
                       "diff --git a/SKILL.md b/NEW.md\n--- a/SKILL.md\n+++ b/SKILL.md\n",
                       "--- a/SKILL.md\n+++ b/SKILL.md\nnew file mode 100644\n",
                       "@@ -1 +1 @@\n-a\n+b\n"):
            with self.subTest(header=header), self.assertRaises(pse.InvalidProposal):
                pse.check_diff_targets(header)
        pse.check_diff_targets(BODY_DIFF)


class ApplyBodyDiffTests(unittest.TestCase):
    TEXT = "---\nname: x\ndescription: d\n---\n" + "".join(f"line {i}\n" for i in range(1, 9))

    def apply(self, diff):
        with tempfile.TemporaryDirectory() as tmp:
            return pse.apply_body_diff(self.TEXT, diff, Path(tmp))

    def test_wrong_hunk_header_counts_still_apply(self):
        # The header claims 7 old and 9 new lines; the body has 5 and 6.
        diff = ("--- a/SKILL.md\n+++ b/SKILL.md\n@@ -3,7 +3,9 @@\n"
                " line 1\n line 2\n-line 3\n+line three\n+line 3b\n line 4\n line 5\n")
        new = self.apply(diff)
        self.assertEqual(new, self.TEXT.replace("line 3\n", "line three\nline 3b\n"))

    def test_mismatched_context_names_the_reason(self):
        diff = ("--- a/SKILL.md\n+++ b/SKILL.md\n@@ -5,3 +5,3 @@\n"
                " no such line\n-line 2\n+line two\n another missing\n")
        with self.assertRaises(pse.InvalidProposal) as ctx:
            self.apply(diff)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("diff does not apply to SKILL.md:"), message)
        self.assertIn("patch failed", message)
        self.assertLessEqual(len(message), len("diff does not apply to SKILL.md: ") + 200)


class DescriptionRewriteTests(unittest.TestCase):

    def test_only_the_description_changes(self):
        text = ("---\nname: x\ndescription: >\n  old words\n  more old\n"
                "license: MIT\n---\nbody\n")
        new = pse.set_description(text, 'Use it: when "quoted" things # matter')
        data = pse.frontmatter_data(new)
        self.assertEqual(data, {"name": "x", "license": "MIT",
                                "description": 'Use it: when "quoted" things # matter'})
        self.assertTrue(new.endswith("---\nbody\n"))

    def test_over_long_description_is_refused(self):
        with self.assertRaises(pse.InvalidProposal):
            pse.set_description(ORIGINAL_SKILL_MD, "x" * 1025)


def m(passed, total, judge=None, error=None, tokens=1000):
    return {"passed": passed, "total": total, "judge_mean": judge, "error": error,
            "tokens": tokens}


class DecisionTableTests(unittest.TestCase):
    """Accept only if validation held AND train improved (#71 step 3)."""

    TRAIN, VAL = ["t1", "t2"], "v"
    BASE = {"t1": m(2, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 7.0)}

    def check(self, candidate, expected):
        accepted, reasons = pse.decide(self.BASE, candidate, self.TRAIN, self.VAL)
        self.assertEqual(accepted, expected, reasons)

    def test_table(self):
        cases = [
            ("train up, validation equal", {"t1": m(3, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 7.0)}, True),
            ("train up, validation up", {"t1": m(3, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(4, 4, 7.0)}, True),
            ("train up, validation objective down", {"t1": m(4, 4, 6.0), "t2": m(4, 4, 6.0), "v": m(2, 4, 7.0)}, False),
            ("train up, validation judge down 0.6", {"t1": m(3, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 6.4)}, False),
            ("train up, validation judge down 0.5", {"t1": m(3, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 6.5)}, True),
            ("train flat", {"t1": m(2, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(4, 4, 9.0)}, False),
            ("train objective flat, train judge up", {"t1": m(2, 4, 7.0), "t2": m(2, 4, 7.0), "v": m(3, 4, 7.0)}, True),
            ("train down", {"t1": m(1, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 7.0)}, False),
            ("errored fixture", {"t1": m(4, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(3, 4, 7.0, "trial_errors")}, False),
            ("unscored fixture", {"t1": m(4, 4, 6.0), "t2": m(2, 4, 6.0), "v": m(0, 0)}, False),
        ]
        for name, candidate, expected in cases:
            with self.subTest(name):
                self.check(candidate, expected)

    def test_no_judge_compares_objective_only(self):
        base = {k: m(v["passed"], v["total"]) for k, v in self.BASE.items()}
        cand = {"t1": m(3, 4), "t2": m(2, 4), "v": m(3, 4)}
        self.assertTrue(pse.decide(base, cand, self.TRAIN, self.VAL)[0])


class HardeningDecisionTests(unittest.TestCase):

    def test_minimum_objective_gain_includes_boundary_and_rejects_neutral(self):
        base = {"t": m(50, 100), "v": m(80, 100)}
        for passed, expected in ((50, False), (59, False), (60, True), (61, True)):
            with self.subTest(passed=passed):
                cand = {"t": m(passed, 100), "v": m(80, 100)}
                self.assertEqual(pse.decide(base, cand, ["t"], "v")[0], expected)

    def test_minimum_judge_gain_uses_normalized_scale(self):
        base = {"t": m(1, 2, 6.0), "v": m(1, 2, 7.0)}
        for judge, expected in ((6.0, False), (6.99, False), (7.0, True), (7.01, True)):
            with self.subTest(judge=judge):
                cand = {"t": m(1, 2, judge), "v": m(1, 2, 7.0)}
                self.assertEqual(pse.decide(base, cand, ["t"], "v")[0], expected)

    def test_small_objective_gain_cannot_use_judge_gain_to_override(self):
        base = {"t": m(50, 100, 6), "v": m(80, 100, 7)}
        cand = {"t": m(51, 100, 9), "v": m(80, 100, 7)}
        self.assertFalse(pse.decide(base, cand, ["t"], "v")[0])

    def test_fixed_holdout_rejects_regressions_and_inconclusive_scores(self):
        base = {"t": m(1, 2, 6), "v": m(1, 2, 7), "h": m(3, 4, 7)}
        for fixed, expected in ((m(3, 4, 7), True), (m(2, 4, 9), False),
                                (m(3, 4, 6.99), False), (m(0, 0, 7), False),
                                (m(3, 4, 7, "trial_errors"), False),
                                (m(3, 4, None), False)):
            with self.subTest(fixed=fixed):
                cand = {"t": m(2, 2, 7), "v": m(1, 2, 6.5), "h": fixed}
                self.assertEqual(pse.decide(base, cand, ["t"], "v", holdout="h",
                                           judge_required=True)[0], expected)
        base["h"] = m(3, 4)
        cand["h"] = m(3, 4)
        self.assertFalse(pse.decide(base, cand, ["t"], "v", holdout="h",
                                   judge_required=True)[0])
        self.assertTrue(pse.decide(base, cand, ["t"], "v", holdout="h")[0])
        base["h"] = m(3, 4, 7)
        self.assertFalse(pse.decide(base, cand, ["t"], "v", holdout="h")[0])
        del cand["h"]
        self.assertFalse(pse.decide(base, cand, ["t"], "v", holdout="h")[0])

    def test_expected_judges_do_not_require_scores_from_objective_only_fixtures(self):
        base = {"t": m(1, 2), "v": m(1, 2, 7), "h": m(3, 4)}
        cand = {"t": m(2, 2), "v": m(1, 2, 7), "h": m(3, 4)}
        self.assertTrue(pse.decide(base, cand, ["t"], "v", holdout="h",
                                  judge_required={"v"})[0])

    def test_missing_expected_train_or_validation_judge_is_inconclusive(self):
        base = {"t": m(1, 2, 6), "v": m(1, 2, 7)}
        for missing in ("t", "v"):
            with self.subTest(missing=missing):
                cand = {"t": m(2, 2, 7), "v": m(1, 2, 7)}
                cand[missing]["judge_mean"] = None
                accepted, reasons = pse.decide(base, cand, ["t"], "v", judge_required=True)
                self.assertFalse(accepted)
                self.assertIn("inconclusive", reasons[0])

    def test_min_gain_cli_requires_finite_positive_fraction(self):
        for value in ("0", "-0.1", "1.01", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as exc:
                pse.parse_args([SKILL, "--min-gain", value])
            self.assertEqual(exc.exception.code, 2)
        self.assertEqual(pse.parse_args([SKILL, "--min-gain", "1"]).min_gain, 1)


class TokenDecisionTests(unittest.TestCase):
    """ADR 0005 amendment 2: tokens are the efficiency metric beside quality.
    A candidate that costs more tokens without a quality gain is not accepted;
    token data that is missing or incomparable never passes silently."""

    BASE = {"t": m(50, 100, tokens=1000), "v": m(80, 100, tokens=1000)}

    def gain(self, **tokens):
        """Train gains .1 (the default minimum) and validation holds."""
        return {"t": m(60, 100, tokens=tokens.get("t", 1000)),
                "v": m(80, 100, tokens=tokens.get("v", 1000))}

    def flat(self, **tokens):
        return {"t": m(50, 100, tokens=tokens.get("t", 1000)),
                "v": m(80, 100, tokens=tokens.get("v", 1000))}

    def test_gain_at_equal_tokens_is_accepted(self):
        accepted, reasons = pse.decide(self.BASE, self.gain(), ["t"], "v")
        self.assertTrue(accepted, reasons)
        self.assertIn("tokens 2000 -> 2000", " ".join(reasons))

    def test_gain_with_fewer_tokens_is_accepted(self):
        accepted, reasons = pse.decide(self.BASE, self.gain(t=800), ["t"], "v")
        self.assertTrue(accepted, reasons)
        self.assertIn("tokens 2000 -> 1800", " ".join(reasons))

    def test_gain_that_costs_more_tokens_is_accepted_and_says_so(self):
        accepted, reasons = pse.decide(self.BASE, self.gain(t=1500), ["t"], "v")
        self.assertTrue(accepted, reasons)
        text = " ".join(reasons)
        self.assertIn("tokens 2000 -> 2500", text)
        self.assertIn("more tokens", text)

    def test_more_tokens_without_a_quality_gain_is_rejected_with_that_reason(self):
        accepted, reasons = pse.decide(self.BASE, self.flat(t=1001), ["t"], "v")
        self.assertFalse(accepted)
        self.assertIn("costs more tokens without a quality gain", " ".join(reasons))

    def test_fewer_tokens_without_a_quality_gain_is_still_rejected(self):
        # An efficiency-only win is an owner question, not accepted silently.
        accepted, reasons = pse.decide(self.BASE, self.flat(t=500, v=500), ["t"], "v")
        self.assertFalse(accepted)
        self.assertNotIn("without a quality gain", " ".join(reasons))
        self.assertIn("did not meet minimum gain", " ".join(reasons))

    def test_a_gain_below_the_minimum_is_not_a_quality_gain(self):
        cand = {"t": m(55, 100, tokens=1200), "v": m(80, 100, tokens=1000)}
        accepted, reasons = pse.decide(self.BASE, cand, ["t"], "v")
        self.assertFalse(accepted)
        self.assertIn("costs more tokens without a quality gain", " ".join(reasons))

    def test_equal_tokens_do_not_count_as_costing_more(self):
        accepted, reasons = pse.decide(self.BASE, self.flat(), ["t"], "v")
        self.assertFalse(accepted)
        self.assertNotIn("more tokens", " ".join(reasons))

    def test_tokens_are_summed_over_every_measured_fixture(self):
        base = {"t": m(50, 100, tokens=1000), "v": m(80, 100, tokens=1000),
                "h": m(80, 100, tokens=1000)}
        cand = {"t": m(60, 100, tokens=500), "v": m(80, 100, tokens=1000),
                "h": m(80, 100, tokens=1000)}
        accepted, reasons = pse.decide(base, cand, ["t"], "v", holdout="h")
        self.assertTrue(accepted, reasons)
        self.assertIn("tokens 3000 -> 2500", " ".join(reasons))

    def test_missing_token_data_is_inconclusive_for_every_fixture_and_side(self):
        for side in ("baseline", "candidate"):
            for fixture in ("t", "v", "h"):
                with self.subTest(side=side, fixture=fixture):
                    base = {"t": m(50, 100), "v": m(80, 100), "h": m(80, 100)}
                    cand = {"t": m(60, 100), "v": m(80, 100), "h": m(80, 100)}
                    {"baseline": base, "candidate": cand}[side][fixture]["tokens"] = None
                    accepted, reasons = pse.decide(base, cand, ["t"], "v", holdout="h")
                    self.assertFalse(accepted)
                    self.assertEqual(len(reasons), 1, reasons)
                    self.assertIn("inconclusive: missing token data", reasons[0])
                    self.assertIn(fixture, reasons[0])
                    self.assertIn(side, reasons[0])

    def test_a_fixture_with_no_tokens_key_is_missing_not_zero(self):
        cand = self.gain()
        del cand["t"]["tokens"]
        accepted, reasons = pse.decide(self.BASE, cand, ["t"], "v")
        self.assertFalse(accepted)
        self.assertIn("inconclusive: missing token data", reasons[0])

    def test_a_non_numeric_token_figure_is_missing(self):
        for bad in ("1000", float("nan"), float("inf"), True):
            with self.subTest(bad=bad):
                accepted, reasons = pse.decide(self.BASE, self.gain(t=bad), ["t"], "v")
                self.assertFalse(accepted)
                self.assertIn("inconclusive: missing token data", reasons[0])

    def test_a_negative_token_figure_is_invalid_and_inconclusive(self):
        for side in ("baseline", "candidate"):
            with self.subTest(side=side):
                base, cand = dict(self.BASE), self.gain()
                {"baseline": base, "candidate": cand}[side]["t"] = m(
                    50 if side == "baseline" else 60, 100, tokens=-1)
                accepted, reasons = pse.decide(base, cand, ["t"], "v")
                self.assertFalse(accepted)
                self.assertIn("inconclusive: missing token data", reasons[0])

    def test_extra_tokens_are_not_called_accepted_when_validation_failed(self):
        # Regression: the "accepted only because" note appeared on a reject.
        base = {"t": m(5, 10, tokens=1000), "v": m(8, 10, tokens=1000)}
        cand = {"t": m(8, 10, tokens=2000), "v": m(5, 10, tokens=2000)}
        accepted, reasons = pse.decide(base, cand, ["t"], "v")
        self.assertFalse(accepted)
        self.assertNotIn("accepted only because", " ".join(reasons))
        self.assertIn("validation objective fell", " ".join(reasons))

    def test_extra_tokens_are_not_called_accepted_when_the_holdout_regressed(self):
        base = {"t": m(5, 10, tokens=1000), "v": m(8, 10, tokens=1000),
                "h": m(8, 10, tokens=1000)}
        cand = {"t": m(8, 10, tokens=2000), "v": m(8, 10, tokens=1000),
                "h": m(5, 10, tokens=1000)}
        accepted, reasons = pse.decide(base, cand, ["t"], "v", holdout="h")
        self.assertFalse(accepted)
        self.assertNotIn("accepted only because", " ".join(reasons))

    def test_an_errored_fixture_is_reported_before_token_data(self):
        cand = {"t": m(60, 100, error="trial_errors", tokens=None), "v": m(80, 100)}
        accepted, reasons = pse.decide(self.BASE, cand, ["t"], "v")
        self.assertFalse(accepted)
        self.assertIn("errored or unscored", reasons[0])


class FixtureTokenMetricTests(unittest.TestCase):
    """`fixture_metrics` reduces the four main-loop usage counts to
    `main_loop_tokens`, recorded beside the `tokens` decide reads (those are
    pinned in test_issue_model_tokens_effort)."""

    def metrics(self, usages, trials=None):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for k, usage in enumerate(usages, start=1):
                trial = run_dir / f"t{k}" / SKILL / TS / "fx" / "with_skill"
                trial.mkdir(parents=True)
                agent = {"cost_usd": 0.1}
                if usage is not None:
                    agent["usage"] = usage
                (trial / "summary.json").write_text(json.dumps(
                    {"error": None, "trial": k, "agent": agent,
                     "objective_checks": [{"id": "c", "passed": True}]}))
            return pse.fixture_metrics(run_dir, SKILL, TS, "fx", trials or len(usages))

    def test_tokens_is_the_mean_per_trial_of_all_four_counts(self):
        out = self.metrics([usage_block(1000), usage_block(2000), usage_block(3000)])
        self.assertEqual(out["main_loop_tokens"], 2000)

    def test_one_trial_without_usage_leaves_tokens_unknown(self):
        out = self.metrics([usage_block(1000), None, usage_block(1000)])
        self.assertIsNone(out["main_loop_tokens"])

    def test_a_usage_block_missing_one_count_leaves_tokens_unknown(self):
        partial = usage_block(1000)
        del partial["cache_read_input_tokens"]
        self.assertIsNone(self.metrics([usage_block(1000), partial])["main_loop_tokens"])

    def test_a_negative_count_leaves_tokens_unknown(self):
        bad = usage_block(1000)
        bad["output_tokens"] = -5
        self.assertIsNone(self.metrics([bad, bad])["main_loop_tokens"])

    def test_a_missing_trial_leaves_tokens_unknown(self):
        self.assertIsNone(self.metrics([usage_block(1000)], trials=2)["tokens"])


class TokenPipelineTests(PipelineCase):

    ACCEPTED = {"bootstrap": (4, 4, 7.0), "existing-convention": (3, 4, 6.5),
                "supersede": (3, 4, 7.0)}

    def test_accept_record_carries_tokens_and_the_decision_line(self):
        runner = FakeRunner(GOOD, self.ACCEPTED, proposal(), NEW_DESCRIPTION)
        runner.tokens["candidate"] = 800
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 0, err)
        record = self.record()
        self.assertEqual(record["decision"], "accepted")
        self.assertEqual(record["baseline"]["bootstrap"]["tokens"], 1000)
        self.assertEqual(record["candidate"]["bootstrap"]["tokens"], 800)
        self.assertIn("tokens 3000 -> 2400", " ".join(record["reasons"]))

    def test_records_carry_per_model_tokens_without_changing_the_decision(self):
        # Every trial's summary also carries the per-model fields run_eval
        # now writes: a subagent model did most of each trial's work.
        usage = {"claude-sonnet-5": {"inputTokens": 10, "outputTokens": 20,
                                     "cacheReadInputTokens": 50,
                                     "cacheCreationInputTokens": 20},
                 "claude-opus-4-8": {"inputTokens": 100, "outputTokens": 200,
                                     "cacheReadInputTokens": 500,
                                     "cacheCreationInputTokens": 100}}

        class PerModelRunner(FakeRunner):
            def run_eval(self, argv):
                rc = super().run_eval(argv)
                for path in Path(flag(argv, "--results-dir")).rglob("summary.json"):
                    summary = json.loads(path.read_text(encoding="utf-8"))
                    tokens = pse.run_eval.model_tokens({"modelUsage": usage})
                    summary.update(model_tokens=tokens, cross_model=pse.run_eval.cross_model(
                        tokens, "claude-sonnet-5"))
                    path.write_text(json.dumps(summary), encoding="utf-8")
                return rc

        runner = PerModelRunner(GOOD, self.ACCEPTED, proposal(), NEW_DESCRIPTION)
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 0, err)
        record = self.record()
        self.assertEqual(record["decision"], "accepted")
        self.assertEqual(record["tokens_basis"], "model_usage_total")
        # 100 + 900 tokens per trial, the subagent's included.
        self.assertIn("tokens 3000 -> 3000", " ".join(record["reasons"]))
        for side in ("baseline", "candidate"):
            metrics = record[side]["bootstrap"]
            self.assertEqual(metrics["model_tokens"]["claude-opus-4-8"]["input_tokens"], 100)
            self.assertEqual(metrics["cross_model"]["flagged_trials"], 3)
            self.assertTrue(metrics["cross_model"]["flagged"])

    def test_a_subagent_heavy_candidate_is_judged_on_its_total(self):
        # Cheaper on the main loop (600 against 1000 per trial), dearer in
        # total once its subagent's tokens count (1500 against 1000), and no
        # quality gain: rejected on cost. On main-loop tokens it would have
        # looked cheaper and passed the cost veto.
        runner = FakeRunner(GOOD, GOOD, proposal(), NEW_DESCRIPTION)
        runner.tokens["candidate"] = 1500
        runner.main_loop["candidate"] = 600
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["decision"], "rejected")
        self.assertEqual(record["candidate"]["bootstrap"]["tokens"], 1500)
        self.assertEqual(record["candidate"]["bootstrap"]["main_loop_tokens"], 600)
        reasons = " ".join(record["reasons"])
        self.assertIn("tokens 3000 -> 4500", reasons)
        self.assertIn("candidate costs more tokens without a quality gain", reasons)

    def test_gain_at_higher_cost_is_accepted_and_recorded(self):
        runner = FakeRunner(GOOD, self.ACCEPTED, proposal(), NEW_DESCRIPTION)
        runner.tokens["candidate"] = 1200
        rc, _, err = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 0, err)
        self.assertIn("more tokens", " ".join(self.record()["reasons"]))

    def test_missing_candidate_usage_is_rejected_as_inconclusive(self):
        runner = FakeRunner(GOOD, self.ACCEPTED, proposal(), NEW_DESCRIPTION)
        runner.tokens["candidate"] = None
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(record["decision"], "rejected")
        self.assertIsNone(record["candidate"]["bootstrap"]["tokens"])
        self.assertIn("inconclusive: missing token data", " ".join(record["reasons"]))

    def test_missing_baseline_usage_is_rejected_as_inconclusive(self):
        runner = FakeRunner(GOOD, self.ACCEPTED, proposal(), NEW_DESCRIPTION)
        runner.tokens["baseline"] = None
        rc, _, _ = self.run_main(runner, "--rotation", "2")
        self.assertEqual(rc, 1)
        self.assertIn("inconclusive: missing token data",
                      " ".join(self.record()["reasons"]))


class HardeningPipelineTests(PipelineCase):

    def queries(self):
        path = self.tmp / "queries.json"
        path.write_text(json.dumps([
            {"query": "record the queue decision", "should_trigger": True},
            {"query": "fix the flaky test", "should_trigger": False}]))
        return str(path)

    def test_fixed_holdout_never_rotates_into_train(self):
        for rotation in range(6):
            with self.subTest(rotation=rotation):
                rc, out, _ = self.run_main(NoCallRunner(), "--dry-run", "--holdout",
                                            "supersede", "--rotation", str(rotation),
                                            "--trigger-eval-set", self.queries())
                self.assertEqual(rc, 0)
                planned = json.loads(out)
                self.assertNotIn("supersede", planned["train"])
                self.assertEqual(planned["validation"], FIXTURES[rotation % 2])
                self.assertEqual(planned["holdout"], "supersede")
                self.assertEqual(planned["min_gain"], .1)
                self.assertEqual(planned["model_calls"]["run_eval_baseline"], 18)
        self.assertFalse(self.results.exists())

    def test_unknown_holdout_refuses_without_calls_or_writes(self):
        rc, _, err = self.run_main(NoCallRunner(), "--holdout", "unknown")
        self.assertEqual(rc, 2)
        self.assertIn("--holdout", err)
        self.assertFalse(self.results.exists())

    def test_holdout_prompt_cannot_reach_proposer_via_another_train_fixture(self):
        # Existing-convention shares bootstrap's prompt; rotation 1 trains it.
        rc, _, err = self.run_main(NoCallRunner(), "--holdout", "bootstrap",
                                    "--rotation", "1", "--trigger-eval-set", self.queries())
        self.assertEqual(rc, 2)
        self.assertIn("prompt distinct", err)
        self.assertFalse(self.results.exists())

    def test_holdout_is_measured_recorded_and_excluded_from_both_proposers(self):
        fixed_prompt = pse.run_eval.load_fixture(FIXTURE_EVALS / SKILL / "supersede")["prompt"]
        supplied = self.tmp / "queries.json"
        supplied.write_text(json.dumps([
            {"query": "  " + fixed_prompt.replace(" ", "\n  "), "should_trigger": True},
            {"query": "record the queue decision", "should_trigger": True},
            {"query": "fix the flaky test", "should_trigger": False}]))
        candidate = dict(GOOD, **{"existing-convention": (3, 4, 7)})
        runner = FakeRunner(GOOD, candidate, proposal())
        rc, _, _ = self.run_main(runner, "--holdout", "supersede", "--rotation", "0",
                                  "--trigger-eval-set", str(supplied), "--min-gain", ".25")
        self.assertEqual(rc, 0)
        self.assertNotIn(" ".join(fixed_prompt.split()), [i["query"] for i in runner.eval_set])
        prompt = next(c[1] for c in runner.calls if c[0] == "propose")
        self.assertNotIn(fixed_prompt.strip(), prompt)
        self.assertNotIn('<fixture name="supersede">', prompt)
        record = self.record()
        self.assertEqual(record["min_gain"], .25)
        self.assertEqual(record["split"]["holdout"], "supersede")
        self.assertIn("supersede", record["baseline"])
        self.assertIn("supersede", record["candidate"])
        self.assertIn("| supersede | holdout |", record["table"])
        body = Path(record["files"]["pr_body"]).read_text()
        self.assertIn("Fixed holdout: supersede", body)
        self.assertIn("minimum train gain: 0.25", body)

    def test_custom_minimum_gain_changes_pipeline_decision(self):
        candidate = dict(GOOD, **{"existing-convention": (3, 4, 7)})
        runner = FakeRunner(GOOD, candidate, proposal())
        rc, _, _ = self.run_main(runner, "--holdout", "supersede", "--min-gain", ".3",
                                  "--trigger-eval-set", self.queries())
        self.assertEqual(rc, 1)
        self.assertIn("minimum gain 0.3", " ".join(self.record()["reasons"]))

    def test_trigger_settings_disable_manifest_providers(self):
        for name, skill, directory in (("custom", SKILL, "extra"),
                                        ("unrelated", "another-skill", "skills")):
            root = self.registry / "plugins" / name
            target = root / directory / skill
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text(ORIGINAL_SKILL_MD.replace(SKILL, skill))
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({
                "name": name, "skills": "./" + directory}))
        market_path = self.registry / ".claude-plugin" / "marketplace.json"
        market = json.loads(market_path.read_text())
        market["plugins"] += [{"name": name, "source": "./plugins/" + name}
                              for name in ("custom", "unrelated")]
        market_path.write_text(json.dumps(market))
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "-C", str(self.registry), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.registry), "-c", "user.name=t", "-c",
                        "user.email=t@example.com", "commit", "-q", "-m", "add providers"],
                       check=True, env=env)
        runner = FakeRunner(GOOD, GOOD, proposal())
        self.run_main(runner)
        self.assertEqual(runner.loop_settings, {"enabledPlugins": {
            "custom@test-market": False, "demo@test-market": False}})
        self.assertEqual(self.record()["description_half"]["disabled_plugins"],
                         ["custom@test-market", "demo@test-market"])

    def test_control_description_is_an_invalid_proposal_record(self):
        runner = FakeRunner(GOOD, {}, proposal(), "new\x7f description")
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertEqual(record["status"], "invalid-proposal")
        self.assertIn("control character", record["reasons"][0])
        self.assertIsNone(record["candidate"])
        self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"], ["baseline"])

    def test_whitespace_descriptions_are_flattened_and_measured(self):
        candidate = dict(GOOD, **{"bootstrap": (3, 4, 7.0)})
        descriptions = ("Use\nthis skill", "Use\tthis skill", "Use\rthis skill",
                        "Use\r\nthis skill")
        for description in descriptions:
            with self.subTest(description=repr(description)):
                shutil.rmtree(self.results, ignore_errors=True)
                runner = FakeRunner(GOOD, candidate, proposal(), description)
                rc, _, _ = self.run_main(runner, "--rotation", "2")
                self.assertEqual(rc, 0)
                record = self.record()
                self.assertEqual(record["status"], "accepted")
                self.assertIn("bootstrap", record["baseline"])
                self.assertIn("bootstrap", record["candidate"])
                self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"],
                                 ["baseline", "candidate"])
                self.assertEqual(record["body_half"]["unified_diff"], BODY_DIFF)
                measured = runner.seen_skill_md["candidate"]
                self.assertIn("Step three: add the record to the index", measured)
                self.assertEqual(pse.frontmatter_data(measured)["description"],
                                 " ".join(description.split()))
                block, _ = pse.split_frontmatter(measured)
                description_lines = [line for line in block.splitlines()
                                     if line.startswith("description:")]
                self.assertEqual(description_lines,
                                 [f"description: {' '.join(description.split())}"])

    def test_dry_run_pairwise_skill_names_no_judge_correction(self):
        argv = self.argv("--dry-run")
        argv[0] = "my-writing-style"
        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = pse.main(argv, runner=NoCallRunner(), now=NOW)
        self.assertEqual(rc, 2)
        self.assertIn("--no-judge", err.getvalue())
        self.assertIn("pairwise", err.getvalue())
        self.assertFalse(self.results.exists())

    def test_preflight_checks_every_fixture_and_respects_no_judge(self):
        fixtures = pse.load_fixtures(SKILL, FIXTURES)
        fixtures["supersede"]["judge"]["mode"] = " PairWise "
        with mock.patch.object(pse, "load_fixtures", return_value=fixtures):
            rc, _, err = self.run_main(NoCallRunner(), "--dry-run")
            self.assertEqual(rc, 2)
            self.assertIn("--no-judge", err)
            rc, out, _ = self.run_main(NoCallRunner(), "--dry-run", "--no-judge")
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["model_calls"]["run_eval_baseline"], 9)
        self.assertFalse(self.results.exists())

    def test_missing_judge_model_fails_preflight_with_corrective_flag(self):
        fixtures = pse.load_fixtures(SKILL, FIXTURES)
        fixtures["supersede"]["judge"].pop("model", None)
        original = pse.run_eval.select_models
        def select(fixture, args):
            if fixture is fixtures["supersede"] and not args.no_judge:
                return None, None, "roster names no usable judge"
            return original(fixture, args)
        with mock.patch.object(pse, "load_fixtures", return_value=fixtures), \
                mock.patch.object(pse.run_eval, "select_models", side_effect=select):
            rc, _, err = self.run_main(NoCallRunner(), "--dry-run")
            self.assertEqual(rc, 2)
            self.assertIn("--no-judge", err)
            self.assertEqual(self.run_main(NoCallRunner(), "--dry-run", "--no-judge")[0], 0)
        self.assertFalse(self.results.exists())


class HardeningDescriptionTests(unittest.TestCase):

    def test_whitespace_controls_are_flattened_in_description(self):
        text = ORIGINAL_SKILL_MD.replace(
            "description: Write an Architecture Decision Record when a decision needs context.",
            "description: >\n  original description\n  continuation")
        descriptions = ("Use\nthis skill", "Use\tthis skill", "Use\rthis skill",
                        "Use\r\nthis skill")
        original = pse.frontmatter_data(text)
        for description in descriptions:
            with self.subTest(description=repr(description)):
                new = pse.set_description(text, description)
                data = pse.frontmatter_data(new)
                self.assertEqual(data["description"], " ".join(description.split()))
                self.assertEqual({k: v for k, v in data.items() if k != "description"},
                                 {k: v for k, v in original.items() if k != "description"})
                block, body = pse.split_frontmatter(new)
                description_lines = [line for line in block.splitlines()
                                     if line.startswith("description:")]
                self.assertEqual(description_lines,
                                 [f"description: {' '.join(description.split())}"])
                self.assertEqual(body, (
                    "\n# Writing ADRs\n\nStep one: read the existing convention.\n"
                    "Step two: write the record.\n"))

    def test_control_characters_are_invalid_proposals(self):
        for char in ("\x00", "\x1f", "\x7f", "\x85", "\x9f"):
            with self.subTest(char=repr(char)), self.assertRaises(pse.InvalidProposal):
                pse.set_description(ORIGINAL_SKILL_MD, "new" + char + " description")

    def test_unrenderable_yaml_is_an_invalid_proposal(self):
        original = pse.yaml.safe_load
        def reject_description(text):
            return None if text.startswith("description:") else original(text)
        with mock.patch.object(pse.yaml, "safe_load", side_effect=reject_description), \
                self.assertRaises(pse.InvalidProposal):
            pse.set_description(ORIGINAL_SKILL_MD, "valid words")


class HardeningPluginDiscoveryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-issue-71-plugins-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".claude-plugin").mkdir()

    def marketplace(self, plugins):
        (self.tmp / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "renamed-market", "plugins": plugins}))

    def plugin(self, directory, skill=SKILL, paths=("skills",), manifest=None):
        root = self.tmp / directory
        for path in paths:
            target = root / path / skill
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text(ORIGINAL_SKILL_MD.replace(SKILL, skill))
        if manifest is not None:
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))

    def test_all_default_and_custom_providers_use_marketplace_entry_keys(self):
        self.plugin("one", manifest={"name": "different-name"})
        self.plugin("two", paths=("additional",), manifest={"skills": "./additional"})
        self.plugin("unrelated", skill="another-skill")
        self.marketplace([{"name": name, "source": "./" + directory}
                          for name, directory in (("first", "one"), ("second", "two"),
                                                   ("unrelated", "unrelated"))])
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL),
                         ["first@renamed-market", "second@renamed-market"])
        target = self.tmp / "one" / "skills" / SKILL / "SKILL.md"
        target.write_text(ORIGINAL_SKILL_MD.replace("name: writing-adrs\n", ""))
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL),
                         ["first@renamed-market", "second@renamed-market"])

    def test_additional_skills_are_additive_and_support_direct_directories(self):
        self.plugin("one", paths=("skills", "additional"),
                    manifest={"skills": ["./additional/" + SKILL]})
        self.marketplace([{"name": "first", "source": "./one"}])
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), ["first@renamed-market"])
        self.assertEqual(pse.skill_provider_plugins(self.tmp, "absent"), [])
        # Default skills/ still supplies the target after custom skill changes.
        (self.tmp / "one" / "additional" / SKILL / "SKILL.md").write_text(
            ORIGINAL_SKILL_MD.replace(SKILL, "another-skill"))
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), ["first@renamed-market"])

    def test_root_source_limits_scan_to_explicit_entry_skills(self):
        self.plugin(".", paths=("skills", "selected"))
        for source in (".", "./"):
            with self.subTest(source=source):
                self.marketplace([{"name": "root-plugin", "source": source,
                                   "skills": "./selected/" + SKILL}])
                selected = self.tmp / "selected" / SKILL / "SKILL.md"
                selected.write_text(ORIGINAL_SKILL_MD)
                self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), ["root-plugin@renamed-market"])
                selected.write_text(ORIGINAL_SKILL_MD.replace(SKILL, "another-skill"))
                self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), [])

    def test_remote_sources_are_skipped_and_missing_marketplace_has_no_key(self):
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), [])
        self.marketplace([{"name": "remote", "source": {"source": "url", "url": "https://example.com/plugin"}}])
        self.assertEqual(pse.skill_provider_plugins(self.tmp, SKILL), [])

    def test_invalid_manifests_and_escaping_paths_refuse(self):
        for content in ("[]", "{", '{"name": "market", "plugins": null}'):
            with self.subTest(content=content):
                (self.tmp / ".claude-plugin" / "marketplace.json").write_text(content)
                with self.assertRaises(pse.Refusal):
                    pse.skill_provider_plugins(self.tmp, SKILL)
        self.plugin("one", manifest={"skills": "./../other"})
        self.marketplace([{"name": "first", "source": "./one"}])
        with self.assertRaisesRegex(pse.Refusal, "escapes its plugin root"):
            pse.skill_provider_plugins(self.tmp, SKILL)
        self.marketplace([{"name": "first", "source": "./../../outside"}])
        with self.assertRaisesRegex(pse.Refusal, "escapes the archived registry"):
            pse.skill_provider_plugins(self.tmp, SKILL)

    def test_symlinked_skill_cannot_cross_plugin_root(self):
        self.plugin("one", manifest={})
        self.plugin("other")
        skill_md = self.tmp / "one" / "skills" / SKILL / "SKILL.md"
        skill_md.unlink()
        skill_md.symlink_to(self.tmp / "other" / "skills" / SKILL / "SKILL.md")
        self.marketplace([{"name": "first", "source": "./one"}])
        with self.assertRaisesRegex(pse.Refusal, "escapes its plugin root"):
            pse.skill_provider_plugins(self.tmp, SKILL)


class ProposalParsingTests(unittest.TestCase):

    def test_fenced_and_bare_json_parse(self):
        self.assertEqual(pse.parse_proposal(proposal())["rationale"], "index step was missing")
        self.assertEqual(pse.parse_proposal('noise {"a": 1} {"rationale": "r", '
                                            '"unified_diff": ""}')["rationale"], "r")

    def test_wrong_shape_is_invalid(self):
        for reply in ("", "no json", '{"rationale": "r"}', '{"rationale": 1, "unified_diff": ""}'):
            with self.subTest(reply=reply), self.assertRaises(pse.InvalidProposal):
                pse.parse_proposal(reply)


class SubprocessRunnerContractTests(unittest.TestCase):
    """The real Runner's subprocess paths, with stand-in CLIs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(
            prefix="test-issue-71-runner-",
            dir=local_eval_tests.TestLocalEval._temp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The skill's fixtures are the invented test-only ones, seeds and all:
        # these tests run local_eval for real.
        patcher = mock.patch.object(pse, "EVALS_DIR", FIXTURE_EVALS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_propose_sends_the_prompt_on_stdin_with_no_tools(self):
        fake = self.tmp / "claude"
        log = self.tmp / "log.json"
        fake.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, sys
            json.dump({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}},
                      open({str(log)!r}, "w"))
            print(json.dumps({{"result": "the reply"}}))
            """))
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        home = self.tmp / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(fake), "HOME": str(home),
                                         "PATH": os.environ["PATH"]}, clear=True):
            reply = pse.Runner().propose("the prompt", "model-x")
        self.assertEqual(reply, "the reply")
        seen = json.loads(log.read_text())
        self.assertEqual(seen["stdin"], "the prompt")
        argv = seen["argv"]
        self.assertEqual(flag(argv, "--tools"), "")
        self.assertEqual(flag(argv, "--permission-mode"), "default")
        self.assertEqual(flag(argv, "--model"), "model-x")
        self.assertNotIn("the prompt", argv)

    def test_run_eval_argv_is_accepted_and_its_tree_is_read(self):
        registry = make_registry(self.tmp / "registry")
        scratch = pse.archive_registry(registry, pse.resolve_ref(registry, "HEAD"),
                                       self.tmp / "scratch")
        self.assertFalse((scratch / ".git").exists())
        results = pse.run_paths(self.tmp / "results", "baseline", SKILL, TS)
        home = self.tmp / "home"
        home.mkdir()
        state = self.tmp / "state"
        state.mkdir()
        fake = self.tmp / "claude-dispatch"
        log = self.tmp / "calls.jsonl"
        fake.write_text(local_eval_tests.DISPATCHER.format(
            python=sys.executable, log=str(log), state=str(state),
            fail=None, plant=None, fake=str(TEST_DIR / "fake-claude"),
            fake_init=str(TEST_DIR / "fake-claude-init")), encoding="utf-8")
        fake.chmod(0o755)
        # PATH and the mount table explicit: the arm's read fence is built
        # from both, so the result does not depend on the host's.
        mountinfo = self.tmp / "mountinfo"
        mountinfo.write_text("", encoding="utf-8")
        env = {"CLAUDE_BIN": str(fake), "HOME": str(home),
               "TMPDIR": str(self.tmp), "SKILLS_EVALS_MOUNTINFO": str(mountinfo),
               "PATH": os.pathsep.join(dict.fromkeys(
                   (str(Path(sys.executable).parent), "/usr/local/bin",
                    "/usr/bin", "/bin"))),
               "LANG": "C.UTF-8", "UNRELATED_INHERITED_VALUE": "test"}
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = pse.Runner().run_eval(pse.run_eval_argv(
                SKILL, "agentskills", scratch, results, TS, 2, True))
        self.assertIn(rc, (0, 1))
        self.assertTrue(log.is_file())
        self.assertTrue((results / "LOCAL_EXHIBIT").is_file())
        self.assertTrue((results / "manifest.json").is_file())
        invocation = json.loads((results / "manifest.json").read_text())["invocation"]
        self.assertEqual(invocation["trials"], 2)
        self.assertEqual(invocation["arm"], "with_skill")
        self.assertIs(invocation["no_judge"], True)
        self.assertEqual(invocation["registries"], ["agentskills"])
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(calls)
        for call in calls:
            self.assertNotIn("UNRELATED_INHERITED_VALUE", call["env_names"])
            self.assertNotEqual(call["claude_bin"], str(fake))
            self.assertEqual(Path(call["claude_bin"]).parent,
                             Path(call["path_head"]))
        for name in FIXTURES:
            arm = pse.run_paths(self.tmp / "results", "baseline", SKILL, TS)
            metrics = pse.fixture_metrics(arm, SKILL, TS, name, 2)
            self.assertIsNone(metrics["error"], name)
            self.assertEqual(metrics["n"], 2)
            self.assertGreater(metrics["total"], 0)
            self.assertTrue(pse.failure_evidence(arm, SKILL, TS, name, 2), name)
            for k in (1, 2):
                summary = json.loads((pse.trial_arm_dir(arm, k, SKILL, TS, name)
                                      / "summary.json").read_text())
                self.assertIs(summary["local_exhibit"], True)

    def test_real_local_eval_refuses_credential_and_settings_before_cli(self):
        registry = make_registry(self.tmp / "registry")
        skill_creator = make_skill_creator(self.tmp / "skill-creator")
        home = self.tmp / "home"
        home.mkdir()
        log = self.tmp / "calls.jsonl"
        fake = self.tmp / "claude"
        fake.write_text(f"#!{sys.executable}\n"
                        f"from pathlib import Path\nPath({str(log)!r}).write_text('called')\n"
                        "raise SystemExit(97)\n", encoding="utf-8")
        fake.chmod(0o755)
        env = {"CLAUDE_BIN": str(fake), "HOME": str(home),
               "TMPDIR": str(self.tmp), "PATH": os.environ["PATH"],
               "LANG": "C.UTF-8"}
        results = pse.run_paths(self.tmp / "results", "baseline", SKILL, TS)
        reviewed = self.tmp / "reviewed-queries.json"
        reviewed.write_text(json.dumps(REVIEWED_QUERIES))
        for cause in ("credential", "settings"):
            with self.subTest(cause=cause):
                if cause == "credential":
                    env["ANTHROPIC_API_KEY"] = ""
                else:
                    env.pop("ANTHROPIC_API_KEY")
                    (home / ".claude").mkdir()
                    (home / ".claude" / "settings.json").write_text(
                        json.dumps({"apiKeyHelper": "unused"}), encoding="utf-8")
                with mock.patch.dict(os.environ, env, clear=True), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = pse.main([SKILL, "--registry", f"agentskills={registry}",
                                   "--skill-creator", str(skill_creator),
                                   "--results-dir", str(self.tmp / "results"),
                                   "--rotation", "2", "--no-judge",
                                   "--trigger-eval-set", str(reviewed)],
                                  runner=pse.Runner(), now=NOW)
                self.assertEqual(rc, 2)
                record = json.loads((self.tmp / "results" / "improvements" / SKILL
                                     / f"{TS}.json").read_text())
                self.assertEqual((record["status"], record["phase"],
                                  record["exit_code"]),
                                 ("refused", "preflight" if cause == "credential" else "baseline", 2))
                self.assertFalse(log.exists())
                self.assertFalse(results.exists())
                shutil.rmtree(self.tmp / "results")


class GuardedRunnerTests(unittest.TestCase):
    """Always-available stand-ins exercise both launch paths, never a login."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(
            prefix="test-issue-71-guards-",
            dir=local_eval_tests.TestLocalEval._temp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The skill's fixtures are the invented test-only ones.
        patcher = mock.patch.object(pse, "EVALS_DIR", frozen_evals(self.tmp / "frozen-evals"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project = self.tmp / "parent" / "project"
        self.project.mkdir(parents=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.plugin = make_skill_creator(self.tmp / "plugin")
        self.loop_log = self.tmp / "loop.json"
        self.cli_log = self.tmp / "cli.json"
        self.fake = self.tmp / "fake-cli"
        self.fake.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""\
            import json, os, shutil
            from pathlib import Path
            Path({str(self.cli_log)!r}).write_text(json.dumps({{
                "env_names": sorted(os.environ), "claude_bin": os.environ["CLAUDE_BIN"],
                "resolved": shutil.which("claude"),
                "path_head": os.environ["PATH"].split(os.pathsep)[0],
                "pythonpath": os.environ.get("PYTHONPATH"),
                "guard_source": Path(os.environ["CLAUDE_BIN"]).read_text()}}))
            print(json.dumps({{"result": "reply"}}))
            """))
        self.fake.chmod(0o700)
        # A guard-PATH mutation must still reach only a test-owned stand-in.
        fallback_bin = self.tmp / "bin"
        fallback_bin.mkdir()
        self.fallback = fallback_bin / "claude"
        shutil.copy2(self.fake, self.fallback)
        self.env = {"PATH": str(fallback_bin) + os.pathsep + os.environ["PATH"],
                    "HOME": str(self.home),
                    "TMPDIR": str(self.tmp), "CLAUDE_BIN": str(self.fake),
                    "GITHUB_TOKEN": "fixture", "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
                    "XDG_STATE_HOME": str(self.tmp / "state"),
                    "UNLISTED_VALUE": "fixture", "PYTHONPATH": str(self.tmp / "untrusted")}
        self.write_loop()

    def write_loop(self, extra=""):
        (self.plugin / "scripts" / "run_loop.py").write_text(textwrap.dedent(f"""\
            import json, os, shutil, subprocess
            from pathlib import Path
            Path({str(self.loop_log)!r}).write_text(json.dumps({{
                "env_names": sorted(os.environ), "claude_bin": os.environ["CLAUDE_BIN"],
                "resolved": shutil.which("claude"),
                "path_head": os.environ["PATH"].split(os.pathsep)[0],
                "pythonpath": os.environ.get("PYTHONPATH"),
                "guard_source": Path(os.environ["CLAUDE_BIN"]).read_text()}}))
            subprocess.run(["claude", "-p", "fixture"], capture_output=True, check=False)
            {extra}
            print(json.dumps({{"best_description": "stand-in winner"}}))
            """))

    def write_sentinels(self):
        source = (f"#!{sys.executable}\nfrom pathlib import Path\n"
                  f"Path({str(self.cli_log)!r}).write_text('sentinel called')\n"
                  "raise SystemExit(97)\n")
        for fake in (self.fake, self.fallback):
            fake.write_text(source)

    @contextlib.contextmanager
    def child_context(self, env=None):
        previous = Path.cwd()
        try:
            os.chdir(self.project)
            with mock.patch.dict(os.environ, self.env if env is None else env, clear=True):
                yield
        finally:
            os.chdir(previous)

    def launch(self, path):
        if path == "trigger":
            return pse.Runner().run_description_loop([], cwd=self.project,
                                                      skill_creator=self.plugin)
        return pse.Runner().propose("fixture prompt", "model-x")

    def assert_guard_environment(self, seen, path):
        for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "XDG_CONFIG_HOME",
                     "XDG_STATE_HOME", "UNLISTED_VALUE", "CLAUDECODE"):
            self.assertNotIn(name, seen["env_names"])
        self.assertEqual(seen["resolved"], seen["claude_bin"])
        self.assertEqual(str(Path(seen["claude_bin"]).parent), seen["path_head"])
        self.assertNotEqual(seen["claude_bin"], str(self.fake))
        tree = ast.parse(seen["guard_source"])
        imports = {(alias.name, alias.asname) for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names}
        self.assertIn(("local_eval_guard", "guard"), imports)
        self.assertTrue(any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "guard"
            and node.func.attr == "check_all_settings" for node in ast.walk(tree)))
        self.assertEqual(seen["pythonpath"], str(self.plugin) if path == "trigger" else None)
        self.assertFalse(Path(seen["claude_bin"]).parent.exists(), "guard directory leaked")

    def test_each_path_allow_lists_env_installs_guard_and_preserves_parent(self):
        for path in ("trigger", "proposal"):
            with self.subTest(path=path), self.child_context():
                before = dict(os.environ)
                self.launch(path)
                self.assertEqual(dict(os.environ), before)
                seen = json.loads((self.loop_log if path == "trigger" else self.cli_log).read_text())
                self.assert_guard_environment(seen, path)

    def test_each_path_refuses_even_empty_credential_before_subprocess(self):
        for path in ("trigger", "proposal"):
            for value in ("", "fixture"):
                with self.subTest(path=path, value=value), \
                        self.child_context(dict(self.env, ANTHROPIC_API_KEY=value)), \
                        mock.patch.object(pse.subprocess, "run") as run:
                    with self.assertRaisesRegex(pse.Refusal, "ANTHROPIC_API_KEY"):
                        self.launch(path)
                    run.assert_not_called()
        self.assertFalse(self.loop_log.exists())
        self.assertFalse(self.cli_log.exists())

    def test_each_path_guard_refuses_settings_in_actual_cwd_and_ancestor(self):
        # The stand-in loop deliberately swallows the guarded launch's error
        # and prints winner JSON; the refusal log must still stop the runner.
        self.write_sentinels()
        for path in ("trigger", "proposal"):
            for directory in (self.project, self.project.parent):
                with self.subTest(path=path, directory=directory.name):
                    settings = directory / ".claude" / "settings.json"
                    settings.parent.mkdir(exist_ok=True)
                    settings.write_text(json.dumps({"apiKeyHelper": "unused"}))
                    guards = []
                    installer = pse.local_eval.install_guard_launcher
                    def record_install(guard_dir, **kwargs):
                        guards.append(guard_dir)
                        return installer(guard_dir, **kwargs)
                    with self.child_context(), \
                            mock.patch.object(pse.local_eval, "install_guard_launcher",
                                              side_effect=record_install), \
                            self.assertRaisesRegex(pse.Refusal, "apiKeyHelper"):
                        self.launch(path)
                    self.assertFalse(self.cli_log.exists(), "CLI sentinel reached")
                    self.assertEqual(len(guards), 1)
                    self.assertFalse(guards[0].exists(), "refused guard directory leaked")
                    settings.unlink()

    def test_actual_guard_refusals_are_persisted_by_pipeline_for_both_paths(self):
        self.write_sentinels()
        registry = make_registry(self.tmp / "registry")
        results = self.tmp / "results"
        reviewed = self.tmp / "reviewed-queries.json"
        reviewed.write_text(json.dumps(REVIEWED_QUERIES))
        for phase in ("trigger", "proposal"):
            with self.subTest(phase=phase):
                runner = FakeRunner(GOOD, GOOD, proposal())
                def guarded_loop(argv, *, cwd, skill_creator):
                    (cwd / ".claude" / "settings.json").write_text(
                        json.dumps({"apiKeyHelper": "unused"}))
                    return pse.Runner().run_description_loop(
                        argv, cwd=cwd, skill_creator=skill_creator)
                method = "run_description_loop" if phase == "trigger" else "propose"
                launch = guarded_loop if phase == "trigger" else pse.Runner().propose
                settings = self.project / ".claude" / "settings.json"
                if phase == "proposal":
                    settings.parent.mkdir(exist_ok=True)
                    settings.write_text(json.dumps({"apiKeyHelper": "unused"}))
                with self.child_context(), mock.patch.object(runner, method, side_effect=launch), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = pse.main([SKILL, "--registry", f"agentskills={registry}",
                                   "--skill-creator", str(self.plugin), "--results-dir",
                                   str(results), "--rotation", "2", "--no-judge",
                                   "--trigger-eval-set", str(reviewed)],
                                  runner=runner, now=NOW)
                self.assertEqual(rc, 2)
                record = json.loads((results / "improvements" / SKILL / f"{TS}.json").read_text())
                self.assertEqual((record["status"], record["phase"], record["exit_code"]),
                                 ("refused", phase, 2))
                self.assertIn("apiKeyHelper", record["reasons"][0])
                self.assertFalse(self.cli_log.exists(), "CLI sentinel reached")
                self.assertEqual([c[1] for c in runner.calls if c[0] == "run_eval"],
                                 ["baseline"])
                shutil.rmtree(results)
                if settings.exists():
                    settings.unlink()

    def test_description_guard_rechecks_settings_on_each_cli_launch(self):
        self.write_loop('Path(".claude").mkdir(exist_ok=True); '
                        'Path(".claude/settings.json").write_text(json.dumps({"apiKeyHelper": "unused"})); '
                        'subprocess.run(["claude", "-p", "fixture"], capture_output=True, check=False)')
        with self.child_context(), self.assertRaisesRegex(pse.Refusal, "apiKeyHelper"):
            self.launch("trigger")
        self.assertTrue(self.cli_log.exists(), "first safe launch never ran")
        self.assert_guard_environment(json.loads(self.cli_log.read_text()), "trigger")

    def test_guard_directory_is_removed_after_subprocess_failure(self):
        for path in ("trigger", "proposal"):
            with self.subTest(path=path), self.child_context():
                guards = []
                installer = pse.local_eval.install_guard_launcher
                def record_install(guard_dir, **kwargs):
                    guards.append(guard_dir)
                    return installer(guard_dir, **kwargs)
                with mock.patch.object(pse.local_eval, "install_guard_launcher",
                                       side_effect=record_install), \
                        mock.patch.object(pse.subprocess, "run", side_effect=OSError("fixture")):
                    with self.assertRaises(pse.Refusal):
                        self.launch(path)
                self.assertEqual(len(guards), 1)
                self.assertFalse(guards[0].exists())

    def test_installer_with_child_mapping_resolves_only_child_path(self):
        guard_dir = self.tmp / "guard"
        guard_dir.mkdir(mode=0o700)
        child = {"PATH": str(self.tmp), "HOME": str(self.home),
                 "CLAUDE_BIN": "fake-cli"}
        before = dict(os.environ)
        real = pse.local_eval.install_guard_launcher(guard_dir, environ=child)
        self.assertEqual(real, str(self.fake))
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(child["CLAUDE_BIN"], str(guard_dir / "claude"))
        self.assertEqual(child["PATH"].split(os.pathsep)[0], str(guard_dir))

    def test_installer_refuses_when_child_has_no_cli(self):
        guard_dir = self.tmp / "guard"
        guard_dir.mkdir(mode=0o700)
        empty_bin = self.tmp / "empty-bin"
        empty_bin.mkdir()
        child = {"PATH": str(empty_bin), "HOME": str(self.home)}
        before = dict(child)
        with self.assertRaisesRegex(pse.local_eval.Refused, "cannot find the claude CLI"):
            pse.local_eval.install_guard_launcher(guard_dir, environ=child)
        self.assertEqual(child, before)
        self.assertEqual(list(guard_dir.iterdir()), [])


def _skill_creator_dir() -> Path | None:
    try:
        return pse.find_skill_creator(None)[0]
    except pse.Refusal:
        return None


@unittest.skipUnless(_skill_creator_dir(), "skill-creator not installed; set SKILL_CREATOR_DIR")
class SkillCreatorContractTests(unittest.TestCase):
    """Anthropic's own run_loop.py, unmodified, driven by our argv, with a
    stand-in `claude` on PATH: it triggers (names the command file skill-creator
    planted) only for a should-trigger query once the description carries
    IMPROVED, and answers the improvement call with such a description."""

    FAKE = textwrap.dedent("""\
        #!{python}
        import json, pathlib, signal, sys
        argv = sys.argv[1:]
        if "text" in argv:
            sys.stdin.read()
            print("<new_description>IMPROVED: use this for ADRs</new_description>")
            sys.exit(0)
        query = argv[argv.index("-p") + 1]
        def text(path):
            # Another worker's query can unlink its command file mid-glob.
            try:
                return path.read_text()
            except FileNotFoundError:
                return ""
        commands = sorted(pathlib.Path(".claude/commands").glob("*.md"))
        names = [p.stem for p in commands if "IMPROVED" in text(p)]
        content = []
        if names and "POSITIVE" in query:
            content = [{{"type": "tool_use", "name": "Skill",
                         "input": {{"skill": " ".join(names)}}}}]
        print(json.dumps({{"type": "assistant", "message": {{"content": content}}}}))
        print(json.dumps({{"type": "result"}}), flush=True)
        # Stay alive until skill-creator kills us. Its reader drops whatever
        # is still buffered once the process has exited, so a stand-in that
        # exits first races it and reads as "not triggered" (measured: one
        # failure in a handful of runs).
        signal.pause()
        """)

    def test_run_loop_picks_the_held_out_winner(self):
        tmp = Path(tempfile.mkdtemp(prefix="test-issue-71-sc-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(self.FAKE.format(python=sys.executable))
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        skill_dir = tmp / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(ORIGINAL_SKILL_MD)
        eval_set = [{"query": f"POSITIVE query {i}", "should_trigger": True} for i in range(3)]
        eval_set += [{"query": f"negative query {i}", "should_trigger": False} for i in range(3)]
        eval_path = tmp / "eval-set.json"
        eval_path.write_text(json.dumps(eval_set))
        project = tmp / "project"
        (project / ".claude").mkdir(parents=True)
        argv = pse.description_loop_argv(eval_path, skill_dir, "model-x", tmp / "out")
        argv[argv.index("--max-iterations") + 1] = "2"
        argv[argv.index("--runs-per-query") + 1] = "1"
        home = tmp / "home"
        home.mkdir()
        skill_creator = _skill_creator_dir()
        env = {"PATH": os.environ['PATH'], "HOME": str(home), "CLAUDE_BIN": str(fake)}
        with mock.patch.dict(os.environ, env, clear=True):
            out = pse.Runner().run_description_loop(
                argv, cwd=project, skill_creator=skill_creator)
        self.assertEqual(out["iterations_run"], 2)
        self.assertTrue(out["best_description"].startswith("IMPROVED"), json.dumps([(h["description"], h["train_results"], h["test_results"]) for h in out["history"]]))
        self.assertEqual(out["best_test_score"], "2/2")
        self.assertEqual(list((project / ".claude" / "commands").glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
