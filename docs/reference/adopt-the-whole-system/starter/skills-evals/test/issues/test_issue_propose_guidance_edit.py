#!/usr/bin/env python3
"""scripts/propose_guidance_edit.py: the guidance-section improve mode.

The guidance analog of propose_skill_edit (test_issue_71.py). Every
model-spending step goes through the script's `Runner`, so these tests drive
the whole pipeline with a fake one on a tiny invented _agent-guidance
checkout built in a temp git repository, with four invented fixtures under a
temp `evals/` (the module's EVALS_DIR is patched). No real CLI, no network,
no clock: the timestamp is passed in. The patch validator, which runs
_agent-guidance's own node checks, is injected as a fake everywhere but the
one test that applies the REAL patch with `git apply --check`.

Discovered and run by test/run_tests.py; also runnable with
`python3 -m pytest test/issues/test_issue_propose_guidance_edit.py -q`.
"""

from __future__ import annotations

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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import propose_guidance_edit as pge  # noqa: E402
import test_issue_local_eval as local_eval_tests  # noqa: E402

SECTION = "beta-rules"
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
TS = "20261004T120000Z"
FIXTURES = ["guidance/beta-rules/one", "guidance/beta-rules/three",
            "guidance/beta-rules/two", "guidance/beta-zeta"]

BASE_MD = textwrap.dedent("""\
    # Fleet guidance

    Intro text for every session.

    ## Alpha rules

    - Keep alpha tidy — always.

    ## Beta rules

    - Do beta things carefully.
    - Never skip the beta check.

    ```
    ## Not a heading inside a fence
    ```

    ## Gamma rules

    - Gamma closes the file.
    """)

MANIFEST = textwrap.dedent("""\
    # Section manifest (invented).
    # `bytes` is generated.

    - id: alpha-rules
      heading: Alpha rules
      file: agents-md/base.md
      status: gap
      bytes: {alpha}

    - id: beta-rules   # the one under test
      heading: Beta rules
      file: agents-md/base.md
      status: gap
      bytes: {beta}

    - id: gamma-rules
      heading: Gamma rules
      file: agents-md/base.md
      status: skipped
      reason: invented
      since: 2026-09-04
      bytes: {gamma}
    """)

IMPACT_MD = textwrap.dedent("""\
    # guidance-impact.md

    Every change gets an entry.

    ## Entry format

    ```
    ## 2026-01-01 — <section-id> — edit
    - Motivation: one line
    ```

    ## 2026-09-10 — alpha-rules — create
    - Motivation: invented
    - Change: invented
    - Eval: none — no fixture yet
    - Outcome: pending
    """)

BUILD_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    echo "<!-- BEGIN MANAGED SECTION -->"
    cat "$ROOT/agents-md/stub.md"
    echo "<!-- END MANAGED SECTION -->"
    """)

NEW_BETA = textwrap.dedent("""\
    ## Beta rules

    - Do beta things carefully, and run the beta check before every commit.
    """)


def git_env() -> dict:
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def make_guidance(root: Path, agent_checks: bool = True) -> Path:
    """A one-commit invented _agent-guidance checkout."""
    (root / "agents-md").mkdir(parents=True)
    (root / "agents-md" / "base.md").write_text(BASE_MD, encoding="utf-8")
    sizes = {name.split()[0].lower(): pge.extent_bytes(BASE_MD, name)
             for name in ("Alpha rules", "Beta rules", "Gamma rules")}
    (root / "agents-md" / "eval-coverage.yml").write_text(
        MANIFEST.format(**sizes), encoding="utf-8")
    (root / "agents-md" / "stub.md").write_text("Read the guidance.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guidance-impact.md").write_text(IMPACT_MD, encoding="utf-8")
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "hooks" / "fleet-memory.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "scripts").mkdir()
    build = root / "scripts" / "build-agents-md.sh"
    build.write_text(BUILD_SCRIPT, encoding="utf-8")
    build.chmod(build.stat().st_mode | stat.S_IXUSR)
    if agent_checks:
        (root / ".githooks").mkdir()
        (root / ".githooks" / "agent-checks").write_text(
            "#!/usr/bin/env bash\n# refuses .env files (invented)\n", encoding="utf-8")
    managed = subprocess.run(["bash", str(build)], capture_output=True, text=True,
                             check=True).stdout
    (root / "AGENTS.md").write_text(
        managed + "\n## Repo-specific additions\n\nNothing.\n", encoding="utf-8")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True, env=git_env())
    # Untracked, as `npm ci` leaves it: never archived.
    (root / "node_modules" / "markdown-it").mkdir(parents=True)
    return root


def fixture_yaml(section: str, name: str, ablation=("full", "full-minus-section"),
                 subject: str = "guidance") -> str:
    return textwrap.dedent(f"""\
        subject: {subject}
        section: {section}
        model: claude-haiku-4-5
        prompt: Invented task {name} for example.com.
        ablation: {list(ablation)}
        objective_checks:
          - id: did-{name}
            description: Secret check text of {name}.
            type: transcript_matches
            must_match: ["{name}"]
        judge_rubric: Rubric text of {name}.
        """)


def make_evals(root: Path) -> Path:
    """Four fixtures of SECTION (one flat, three nested), plus decoys the
    discovery must skip."""
    files = {
        "guidance/beta-rules/one": fixture_yaml(SECTION, "one"),
        "guidance/beta-rules/two": fixture_yaml(SECTION, "two"),
        "guidance/beta-rules/three": fixture_yaml(SECTION, "three"),
        "guidance/beta-zeta": fixture_yaml(SECTION, "zeta"),
        "guidance/_delivery": fixture_yaml(SECTION, "delivery"),
        "guidance/beta-rules/_draft": fixture_yaml(SECTION, "draft"),
        "guidance/other": fixture_yaml("alpha-rules", "other"),
        "guidance/no-full": fixture_yaml(SECTION, "nofull",
                                         ablation=("section", "none")),
    }
    for rel, text in files.items():
        (root / rel).mkdir(parents=True)
        (root / rel / "fixture.yaml").write_text(text, encoding="utf-8")
    return root


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def summary(passed: int, total: int, judge: float | None = None,
            tokens: int = 1000) -> dict:
    tenth = tokens // 10
    counts = {"input_tokens": tenth, "output_tokens": tenth,
              "cache_creation_input_tokens": tenth,
              "cache_read_input_tokens": tokens - 3 * tenth}
    return {"error": None,
            "objective_checks": [{"id": f"c{i}", "passed": i < passed}
                                 for i in range(total)],
            "judge": {"overall": judge} if judge is not None else None,
            "agent": {"cost_usd": 0.1, "usage": counts},
            "model_tokens": {"model-a": dict(counts, canonical_model=None)},
            "cross_model": {"model": "model-a", "canonical_model": None,
                            "complete": True, "dropped": 0, "other_share": None,
                            "threshold": 0.5, "flagged": None}}


class FakeRunner:
    """Answers from canned numbers: numbers[side][fixture] is
    (passed, total) or a callable of the trial number."""

    def __init__(self, baseline: dict, candidate: dict, reply: str | None,
                 rc: int = 0):
        self.numbers = {"baseline": baseline, "candidate": candidate}
        self.reply = reply
        self.rc = rc
        self.calls: list[tuple] = []

    def run_eval(self, argv):
        out = Path(flag(argv, "--results-dir"))
        guidance_dir = Path(flag(argv, "--guidance"))
        side = guidance_dir.name
        fixture = Path(argv[0]).relative_to(pge.EVALS_DIR).as_posix()
        trial = int(out.name.split("-")[1])
        self.calls.append(("run_eval", side, fixture, trial, list(argv)))
        assert not out.exists(), "every trial gets an empty results dir"
        assert not (guidance_dir / ".git").exists(), "scratch guidance carries .git"
        self.seen = getattr(self, "seen", {})
        self.seen[side] = (guidance_dir / "agents-md" / "base.md").read_text(encoding="utf-8")
        value = self.numbers[side][fixture]
        value = value(trial) if callable(value) else value
        # run_eval refuses --timestamp for a guidance fixture outside in-place
        # pairing (2026-10-28: every live trial ended missing_summary), and
        # picks its own timestamp directory.
        assert "--timestamp" not in argv, "run_eval refuses --timestamp here"
        path = out / "guidance" / SECTION / f"20261028T{trial:06d}Z" / pge.ARM / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(summary(*value)), encoding="utf-8")
        return self.rc

    def propose(self, prompt, model):
        self.calls.append(("propose", prompt, model))
        if self.reply is None:
            raise AssertionError("no proposal call expected")
        return self.reply


class NoCallRunner:
    def __getattr__(self, name):
        raise AssertionError(f"dry run made a {name} call")


def reply(section: str = NEW_BETA, rationale: str = "the check was implicit",
          hooks=None) -> str:
    return "```json\n" + json.dumps({
        "rationale": rationale, "section": section,
        "hook_candidates": hooks if hooks is not None else [
            {"rule": "Never skip the beta check.",
             "check": "pre-commit refuses a commit without a beta stamp"}]}) + "\n```"


def ok_validator(calls=None):
    def validate(base, cand, checkout, now):
        if calls is not None:
            calls.append((base, cand, checkout, now))
        return {"ok": True, "base": "b" * 40, "head": "c" * 40,
                "checks": {"guidance-touch": {"exit_code": 0, "tail": ""}}}
    return validate


def failing_validator(base, cand, checkout, now):
    return {"ok": False, "base": "b" * 40, "head": "c" * 40,
            "checks": {"guidance-touch": {"exit_code": 1, "tail": "missing entry"}}}


#: At rotation 0 train is (three, two), validation one, holdout beta-zeta.
#: Train gains 2/10 -> 9/10 per fixture; validation and holdout hold.
GAIN_B = {f: (2, 10) for f in FIXTURES}
GAIN_C = {**GAIN_B, "guidance/beta-rules/three": (9, 10),
          "guidance/beta-rules/two": (9, 10)}


class PipelineCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(
            prefix="test-guidance-edit-", dir=local_eval_tests.TestLocalEval._temp_base()))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = self.tmp / "home"
        home.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(home),
                    "TMPDIR": str(self.tmp), "LANG": "C.UTF-8"}
        self.guidance = make_guidance(self.tmp / "guidance")
        self.results = self.tmp / "results"
        for name, value in (("EVALS_DIR", make_evals(self.tmp / "evals")),
                            ("check_validation_tools", lambda _: None)):
            patcher = mock.patch.object(pge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def argv(self, *extra):
        return [SECTION, "--guidance", str(self.guidance), "--results-dir",
                str(self.results), "--no-judge", *extra]

    def run_main(self, runner, *extra, validator=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pge.main(self.argv(*extra), runner=runner, now=NOW,
                          validator=validator or ok_validator())
        return rc, out.getvalue(), err.getvalue()

    def stem(self) -> Path:
        return self.results / "guidance-improvements" / SECTION / TS

    def record(self) -> dict:
        return json.loads(self.stem().with_suffix(".json").read_text())


class DiscoveryAndSplitTests(PipelineCase):

    def test_discovery_skips_underscore_dirs_other_sections_and_missing_full(self):
        self.assertEqual(pge.fixture_layout_discover(pge.EVALS_DIR, SECTION), FIXTURES)

    def test_default_holdout_is_the_last_and_validation_rotates(self):
        held = [pge.split_fixtures(FIXTURES, r) for r in range(4)]
        self.assertTrue(all(h[2] == "guidance/beta-zeta" for h in held))
        self.assertEqual([h[1] for h in held], FIXTURES[:3] + [FIXTURES[0]])
        self.assertEqual(held[0][0], FIXTURES[1:3])

    def test_explicit_holdout_leaves_the_rotation_population(self):
        train, validation, holdout = pge.split_fixtures(FIXTURES, 0, FIXTURES[0])
        self.assertEqual((train, validation, holdout),
                         (FIXTURES[2:], FIXTURES[1], FIXTURES[0]))
        with self.assertRaisesRegex(pge.Refusal, "not a fixture"):
            pge.split_fixtures(FIXTURES, 0, "guidance/absent")

    def test_fewer_than_four_fixtures_is_refused_with_exit_2(self):
        with self.assertRaisesRegex(pge.Refusal, "needs more fixtures: 3 found, 4 required"):
            pge.split_fixtures(FIXTURES[:3], 0)
        shutil.rmtree(pge.EVALS_DIR / "guidance" / "beta-zeta")
        rc, _, err = self.run_main(NoCallRunner())
        self.assertEqual(rc, 2)
        self.assertIn("needs more fixtures: 3 found, 4 required", err)

    def test_default_rotation_counts_records(self):
        records = self.stem().parent
        records.mkdir(parents=True)
        (records / "earlier.json").write_text("{}")
        rc, out, _ = self.run_main(NoCallRunner(), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["validation"], FIXTURES[1])


class DryRunTests(PipelineCase):

    def test_dry_run_makes_no_call_and_writes_nothing(self):
        rc, out, _ = self.run_main(NoCallRunner(), "--dry-run")
        self.assertEqual(rc, 0)
        plan = json.loads(out)
        self.assertEqual(plan["holdout"], "guidance/beta-zeta")
        self.assertEqual(plan["row"]["heading"], "Beta rules")
        argv = plan["first_run_argv"]
        self.assertEqual(argv[1:4], ["--ablation", "--arm", "ablation_full"])
        self.assertFalse(self.results.exists())

    def test_dry_run_refusal_is_exit_2(self):
        rc, _, err = self.run_main(NoCallRunner(), "--dry-run", "--holdout", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("not a fixture", err)

    def test_a_skipped_row_is_refused(self):
        for rel in FIXTURES:
            path = pge.EVALS_DIR / rel / "fixture.yaml"
            path.write_text(path.read_text().replace(SECTION, "gamma-rules"))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pge.main(["gamma-rules", "--guidance", str(self.guidance),
                           "--results-dir", str(self.results), "--dry-run"],
                          runner=NoCallRunner(), now=NOW)
        self.assertEqual(rc, 2)
        self.assertIn("skipped row", err.getvalue())

    def test_missing_node_modules_is_refused_before_any_call(self):
        with mock.patch.object(pge, "check_validation_tools", REAL_CHECK_TOOLS):
            shutil.rmtree(self.guidance / "node_modules")
            rc, _, err = self.run_main(NoCallRunner())
        self.assertEqual(rc, 2)
        self.assertIn("npm ci", err)
        self.assertFalse(self.results.exists())


REAL_CHECK_TOOLS = pge.check_validation_tools


class AcceptTests(PipelineCase):

    def test_accepted_run_writes_record_patch_and_pr_body(self):
        calls = []
        runner = FakeRunner(GAIN_B, GAIN_C, reply())
        rc, out, _ = self.run_main(runner, validator=ok_validator(calls))
        self.assertEqual(rc, 0, out)
        record = self.record()
        self.assertEqual(record["status"], "accepted")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["trials"], {"initial": 3, "total": 3})
        self.assertEqual(record["split"]["holdout"], "guidance/beta-zeta")
        self.assertEqual(len(record["proposal"]["hook_candidates"]), 1)
        self.assertTrue(record["patch_validation"]["ok"])
        self.assertEqual(len(calls), 1)
        self.assertIn("run the beta check before every commit", runner.seen["candidate"])
        self.assertNotIn("before every commit", runner.seen["baseline"])
        patch = self.stem().with_suffix(".patch").read_text()
        for rel in ("agents-md/base.md", "agents-md/eval-coverage.yml",
                    "docs/guidance-impact.md"):
            self.assertIn(f"+++ b/{rel}", patch)
        body = self.stem().with_name(TS + ".pr-body.md").read_text()
        self.assertIn("### Could move to agent-checks", body)
        self.assertIn("Never skip the beta check.", body)
        self.assertIn("Part of skills-evals ADR 0005", body)
        self.assertNotIn("/issues/", body)

    def test_proposal_sees_train_only_and_agent_checks(self):
        runner = FakeRunner(GAIN_B, GAIN_C, reply())
        self.run_main(runner)
        prompt = next(c[1] for c in runner.calls if c[0] == "propose")
        for name in ("three", "two"):
            self.assertIn(f"Secret check text of {name}.", prompt)
            self.assertIn(f"Rubric text of {name}.", prompt)
        for name in ("one", "zeta"):
            self.assertNotIn(f"of {name}", prompt)
            self.assertNotIn(f"task {name}", prompt)
        self.assertIn("refuses .env files", prompt)
        self.assertIn("- Never skip the beta check.", prompt)

    def test_proposal_says_when_agent_checks_is_absent(self):
        shutil.rmtree(self.guidance)
        self.guidance = make_guidance(self.tmp / "guidance", agent_checks=False)
        runner = FakeRunner(GAIN_B, GAIN_C, reply())
        self.run_main(runner)
        prompt = next(c[1] for c in runner.calls if c[0] == "propose")
        self.assertIn("absent: this checkout has no .githooks/agent-checks", prompt)

    def test_runs_interleave_baseline_then_candidate_per_trial(self):
        runner = FakeRunner(GAIN_B, GAIN_C, reply())
        self.run_main(runner)
        order = [(c[3], c[2], c[1]) for c in runner.calls if c[0] == "run_eval"]
        names = FIXTURES[1:3] + [FIXTURES[0], FIXTURES[3]]
        expected = [(k, n, side) for k in (1, 2, 3) for n in names
                    for side in ("baseline", "candidate")]
        self.assertEqual(order, expected)
        self.assertEqual(runner.calls[0][0], "propose")

    def test_a_varying_arm_extends_to_six_trials(self):
        varying = dict(GAIN_C, **{FIXTURES[0]: lambda k: (9 if k % 2 else 8, 10)})
        runner = FakeRunner(GAIN_B, varying, reply())
        self.run_main(runner)
        trials = [c[3] for c in runner.calls if c[0] == "run_eval"]
        self.assertEqual(max(trials), 6)
        self.assertEqual(len(trials), 6 * 4 * 2)
        self.assertEqual(self.record()["trials"]["total"], 6)

    def test_bytes_and_impact_entry_land_in_the_patch(self):
        runner = FakeRunner(GAIN_B, GAIN_C, reply())
        captured = {}

        def validate(base, cand, checkout, now):
            captured["manifest"] = (cand / "agents-md/eval-coverage.yml").read_text()
            captured["impact"] = (cand / "docs/guidance-impact.md").read_text()
            captured["old"] = (base / "agents-md/eval-coverage.yml").read_text()
            return ok_validator()(base, cand, checkout, now)
        self.run_main(runner, validator=validate)
        new_bytes = len((NEW_BETA + "\n").encode("utf-8"))
        self.assertIn(f"bytes: {new_bytes}\n", captured["manifest"])
        changed = [(a, b) for a, b in zip(captured["old"].splitlines(),
                                          captured["manifest"].splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn("2026-10-04 — beta-rules — edit", captured["impact"])
        self.assertIn("Eval: train objective 12/60 -> 54/60, validation 6/30 -> 6/30, "
                      "holdout 6/30 -> 6/30, n=3 per side; fixtures "
                      + ", ".join(FIXTURES[1:3] + [FIXTURES[0], FIXTURES[3]]),
                      captured["impact"])
        self.assertIn("; 1 rule could move to agent-checks", captured["impact"])


class RejectAndRefuseTests(PipelineCase):

    def test_small_gain_is_rejected_with_exit_1_and_no_pr_body(self):
        small = dict(GAIN_B, **{FIXTURES[1]: (3, 10)})
        rc, _, _ = self.run_main(FakeRunner(GAIN_B, small, reply()))
        self.assertEqual(rc, 1)
        self.assertEqual(self.record()["status"], "rejected")
        self.assertTrue(self.stem().with_suffix(".patch").exists())
        self.assertFalse(self.stem().with_name(TS + ".pr-body.md").exists())

    def test_failed_patch_validation_is_exit_2(self):
        rc, _, err = self.run_main(FakeRunner(GAIN_B, GAIN_C, reply()),
                                   validator=failing_validator)
        self.assertEqual(rc, 2)
        self.assertEqual(self.record()["phase"], "patch-validation")
        self.assertFalse(self.stem().with_name(TS + ".pr-body.md").exists())
        self.assertIn("guidance-touch", err)

    def test_errored_trials_are_inconclusive_never_failed(self):
        rc, _, _ = self.run_main(FakeRunner(GAIN_B, GAIN_C, reply(), rc=2))
        self.assertEqual(rc, 1)
        record = self.record()
        self.assertTrue(record["reasons"][0].startswith("inconclusive"))
        self.assertEqual(record["baseline"][FIXTURES[0]]["error"], "trial_exit")

    def test_invalid_proposal_is_recorded_and_never_measured(self):
        runner = FakeRunner(GAIN_B, GAIN_C, "no json here")
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 1)
        self.assertEqual(self.record()["status"], "invalid-proposal")
        self.assertFalse([c for c in runner.calls if c[0] == "run_eval"])
        self.assertFalse(self.stem().with_suffix(".patch").exists())

    def test_refused_environment_is_exit_2_before_the_proposal(self):
        runner = FakeRunner(GAIN_B, GAIN_C, None)
        self.env["ANTHROPIC_API_KEY"] = "x"
        rc, _, _ = self.run_main(runner)
        self.assertEqual(rc, 2)
        self.assertEqual(self.record()["phase"], "preflight")
        self.assertEqual(runner.calls, [])


class ProposalValidationTests(unittest.TestCase):
    ORIGINAL = BASE_MD[BASE_MD.index("## Beta"):BASE_MD.index("## Gamma")]

    def norm(self, text):
        return pge.normalize_section(text, self.ORIGINAL, "Beta rules")

    def test_valid_proposal_keeps_the_original_trailing_blank_line(self):
        self.assertEqual(self.norm(NEW_BETA + "\n\n  \n"), NEW_BETA + "\n")
        self.assertTrue(self.ORIGINAL.endswith("```\n\n"))

    def test_changed_heading_is_invalid(self):
        with self.assertRaisesRegex(pge.InvalidProposal, "identical heading"):
            self.norm(NEW_BETA.replace("Beta rules", "Beta Rules"))

    def test_extra_h2_is_invalid_but_h3_is_allowed(self):
        with self.assertRaisesRegex(pge.InvalidProposal, "2 `##`"):
            self.norm(NEW_BETA + "\n## Sneaky\n\ntext\n")
        self.assertIn("### Sub", self.norm(NEW_BETA + "\n### Sub\n\ntext\n"))

    def test_unchanged_text_is_invalid(self):
        with self.assertRaisesRegex(pge.InvalidProposal, "unchanged"):
            self.norm(self.ORIGINAL.rstrip() + "\n")

    def test_not_json_is_invalid(self):
        for text in ("plain words", '{"rationale": "r", "section": "s"}',
                     '{"rationale": 1, "section": "s", "hook_candidates": []}'):
            with self.subTest(text=text), self.assertRaises(pge.InvalidProposal):
                pge.parse_proposal(text)

    def test_hook_candidates_are_validated(self):
        good = {"rule": "r", "check": "c"}
        self.assertEqual(pge.check_hook_candidates([]), [])
        self.assertEqual(pge.check_hook_candidates([good] * 10), [good] * 10)
        for bad in ({}, [good] * 11, ["x"], [{"rule": "r"}],
                    [{"rule": " ", "check": "c"}], [{"rule": "r", "check": 3}],
                    [{"rule": "r" * 301, "check": "c"}],
                    [dict(good, extra="x")]):
            with self.subTest(bad=bad), self.assertRaises(pge.InvalidProposal):
                pge.check_hook_candidates(bad)

    def test_fenced_reply_parses(self):
        self.assertEqual(pge.parse_proposal(reply(hooks=[]))["section"], NEW_BETA)


def m(passed, total, judge=None, error=None, tokens=1000):
    return {"error": error, "passed": passed, "total": total, "judge_mean": judge,
            "tokens": tokens, "tokens_basis": pge.TOKENS_BASIS,
            "per_trial_passed": []}


TRAIN, VAL, HOLD = ["a", "b"], "v", "h"


def sides(train_b, train_c, val=(10, 10), val_c=None, hold=(10, 10), hold_c=None,
          **kw):
    b = {"a": m(*train_b), "b": m(*train_b), "v": m(*val), "h": m(*hold)}
    c = {"a": m(*train_c), "b": m(*train_c), "v": m(*(val_c or val)),
         "h": m(*(hold_c or hold))}
    return b, c


class DecideTests(unittest.TestCase):

    def decide(self, b, c):
        return pge.decide(b, c, TRAIN, VAL, HOLD)

    def test_accept(self):
        ok, reasons = self.decide(*sides((10, 30), (25, 30)))
        self.assertTrue(ok, reasons)

    def test_reject_small_gain(self):
        # 0.30 -> 0.35 pooled: under the required 0.10.
        ok, reasons = self.decide(*sides((9, 30), (10, 30)))
        self.assertFalse(ok)
        self.assertIn("did not gain enough", reasons[0])

    def test_reject_when_newcombe_lower_bound_is_not_above_zero(self):
        # 1/2 -> 2/2 per fixture: +0.5 >= required 0.10, but n is tiny.
        ok, reasons = self.decide(*sides((1, 2), (2, 2)))
        self.assertFalse(ok)
        self.assertIn("lower bound", reasons[0])

    def test_required_gain_is_capped_by_headroom(self):
        self.assertAlmostEqual(pge.required_gain(0.5), 0.10)
        self.assertAlmostEqual(pge.required_gain(0.9), 0.05)
        self.assertAlmostEqual(pge.required_gain(0.96), 0.02)
        ok, reasons = self.decide(*sides((10, 10), (10, 10)))
        self.assertFalse(ok)
        self.assertIn("no headroom", reasons[0])

    def test_validation_confident_regression_rejects(self):
        ok, reasons = self.decide(*sides((10, 30), (25, 30), val=(20, 20),
                                         val_c=(5, 20)))
        self.assertFalse(ok)
        self.assertTrue(any("validation objective regressed confidently" in r
                            for r in reasons))

    def test_validation_small_point_drop_is_allowed(self):
        ok, reasons = self.decide(*sides((10, 30), (25, 30), val=(20, 20),
                                         val_c=(19, 20)))
        self.assertTrue(ok, reasons)

    def test_holdout_regression_rejects(self):
        ok, reasons = self.decide(*sides((10, 30), (25, 30), hold=(20, 20),
                                         hold_c=(16, 20)))
        self.assertFalse(ok)
        self.assertTrue(any(r.startswith("holdout") for r in reasons), reasons)

    def test_holdout_judge_has_zero_tolerance(self):
        b, c = sides((10, 30), (25, 30))
        b["h"]["judge_mean"], c["h"]["judge_mean"] = 7.0, 6.9
        for side in (b, c):
            for n in ("a", "b", "v"):
                side[n]["judge_mean"] = 5.0
        ok, reasons = self.decide(b, c)
        self.assertFalse(ok)
        self.assertTrue(any("holdout judge" in r for r in reasons))

    def test_judge_only_gain_is_accepted(self):
        b, c = sides((10, 30), (10, 30))
        for side, score in ((b, 5.0), (c, 6.0)):
            for n in ("a", "b", "v", "h"):
                side[n]["judge_mean"] = score
        ok, reasons = self.decide(b, c)
        self.assertTrue(ok, reasons)

    def test_tokens_rise_without_gain_rejects(self):
        b, c = sides((9, 30), (10, 30))
        for n in c:
            c[n]["tokens"] = 2000
        ok, reasons = self.decide(b, c)
        self.assertFalse(ok)
        self.assertIn("candidate costs more tokens without a quality gain", reasons)

    def test_tokens_rise_with_gain_is_noted(self):
        b, c = sides((10, 30), (25, 30))
        for n in c:
            c[n]["tokens"] = 2000
        ok, reasons = self.decide(b, c)
        self.assertTrue(ok)
        self.assertTrue(any("accepted only because" in r for r in reasons))

    def test_inconclusive_on_missing_tokens_and_errors(self):
        b, c = sides((10, 30), (25, 30))
        c["a"]["tokens"] = None
        self.assertIn("missing token data", self.decide(b, c)[1][0])
        b, c = sides((10, 30), (25, 30))
        b["v"]["error"] = "trial_exit"
        self.assertIn("inconclusive: errored", self.decide(b, c)[1][0])
        b, c = sides((10, 30), (25, 30))
        c["h"]["tokens_basis"] = "usage_main_loop"
        self.assertIn("token bases differ", self.decide(b, c)[1][0])

    def test_near_threshold_is_within_one_check(self):
        b, c = sides((10, 30), (13, 30))   # gain 0.10, required 0.10
        self.assertTrue(pge.near_threshold(b, c, TRAIN))
        b, c = sides((10, 30), (25, 30))
        self.assertFalse(pge.near_threshold(b, c, TRAIN))


class IntervalTests(unittest.TestCase):

    def test_newcombe_1998_table_ii_example(self):
        lower, upper = pge.newcombe_interval(56, 70, 48, 80)
        self.assertAlmostEqual(lower, 0.0524, places=4)
        self.assertAlmostEqual(upper, 0.3339, places=4)

    def test_wilson_published_example(self):
        # Newcombe 1998 Table I's single-proportion example, 81/263.
        lower, upper = pge.wilson_interval(81, 263)
        self.assertAlmostEqual(lower, 0.2553, places=4)
        self.assertAlmostEqual(upper, 0.3662, places=4)


class PatchEditTests(unittest.TestCase):

    def test_bytes_edit_changes_only_that_value(self):
        text = MANIFEST.format(alpha=1, beta=2, gamma=3)
        new = pge.set_manifest_bytes(text, SECTION, 4321)
        diff = [(a, b) for a, b in zip(text.splitlines(), new.splitlines()) if a != b]
        self.assertEqual(diff, [("  bytes: 2", "  bytes: 4321")])
        self.assertIn("# the one under test", new)
        self.assertEqual(len(text.splitlines()), len(new.splitlines()))
        with self.assertRaises(pge.Refusal):
            pge.set_manifest_bytes(text, "absent", 1)

    def test_extent_bytes_count_utf8(self):
        alpha = BASE_MD[BASE_MD.index("## Alpha"):BASE_MD.index("## Beta")]
        self.assertEqual(pge.extent_bytes(BASE_MD, "Alpha rules"),
                         len(alpha.encode("utf-8")))
        self.assertGreater(pge.extent_bytes(BASE_MD, "Alpha rules"), len(alpha))

    def test_impact_entry_goes_before_the_first_dated_entry_not_the_fence(self):
        entry = "## 2026-10-04 — beta-rules — edit\n- Outcome: pending\n\n"
        new = pge.insert_impact_entry(IMPACT_MD, entry)
        fence_at = new.index("## 2026-01-01")
        self.assertLess(fence_at, new.index(entry))
        self.assertEqual(new.index(entry) + len(entry),
                         new.index("## 2026-09-10"))
        self.assertEqual(new.replace(entry, ""), IMPACT_MD)

    def test_impact_entry_format(self):
        b = {f: m(2, 10) for f in ("a", "b", "v", "h")}
        c = dict(b, a=m(9, 10))
        entry = pge.impact_entry(
            SECTION, TS, NOW, "line one\nline two " + "x" * 400, 3, 100, 90, b, c,
            {"train": ["a", "b"], "validation": "v", "holdout": "h"}, 3,
            ["a", "b", "v", "h"])
        lines = entry.split("\n")
        self.assertEqual(lines[0], "## 2026-10-04 — beta-rules — edit")
        self.assertTrue(lines[1].startswith(
            f"- Motivation: automatic improve run {TS}: line one line two x"))
        self.assertTrue(lines[1].endswith("...; 3 rules could move to agent-checks"))
        self.assertEqual(lines[2], "- Change: rewrote the section (100 -> 90 bytes); "
                         "proposed by skills-evals scripts/propose_guidance_edit.py")
        self.assertEqual(lines[3], "- Eval: train objective 4/20 -> 11/20, validation "
                         "2/10 -> 2/10, holdout 2/10 -> 2/10, n=3 per side; "
                         "fixtures a, b, v, h")
        self.assertEqual(lines[4:], ["- Outcome: pending", "", ""])

    def test_pr_body_has_no_closing_keyword_or_mention(self):
        record = {"section": SECTION, "guidance": {"sha": "a" * 40},
                  "row": {"file": "agents-md/base.md", "heading": "Beta rules"},
                  "split": {"train": ["a"], "validation": "v", "holdout": "h",
                            "rotation": 0},
                  "trials": {"total": 3}, "reasons": ["fine"],
                  "files": {"patch": "/x.patch"},
                  "proposal": {"rationale": "Fixes #12 and closes #3, thanks @someone",
                               "hook_candidates": [{"rule": "resolves #4",
                                                    "check": "ping @other"}]}}
        body = pge.pr_body(record, "| table |")
        self.assertNotRegex(body, r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\b\s*:?\s*\S*#\d")
        self.assertNotIn("@", body)
        self.assertIn("### Could move to agent-checks", body)
        record["proposal"]["hook_candidates"] = []
        self.assertNotIn("Could move to agent-checks", pge.pr_body(record, ""))


class RealPatchTests(PipelineCase):
    """The real patch builder and AGENTS.md regeneration, applied with
    `git apply --check` to the archived commit of the invented checkout."""

    def test_patch_applies_to_the_checkout_at_its_sha(self):
        rc, _, _ = self.run_main(FakeRunner(GAIN_B, GAIN_C, reply()))
        self.assertEqual(rc, 0)
        patch = self.stem().with_suffix(".patch")
        self.assertIn("diff --git a/agents-md/base.md b/agents-md/base.md",
                      patch.read_text())
        proc = subprocess.run(["git", "-C", str(self.guidance), "apply", "--check",
                               str(patch)], capture_output=True, text=True,
                              env=git_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        subprocess.run(["git", "-C", str(self.guidance), "apply", str(patch)],
                       check=True, env=git_env())
        text = (self.guidance / "agents-md" / "base.md").read_text()
        self.assertIn(NEW_BETA + "\n## Gamma rules", text)


if __name__ == "__main__":
    unittest.main()
